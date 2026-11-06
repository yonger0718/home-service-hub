"""E-invoice import, link bookkeeping and owner actions (spec §5-§8). Never log invoice payloads (privacy)."""
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Einvoice, EinvoiceItem, EinvoiceLink, EinvoiceSellerMute, LedgerEntry
from ..schemas.einvoices import EinvoiceImportIn, EinvoiceIn, RunbookRefIn
from .einvoice_rules import period_of
from .errors import RETRY, CodedConflictError, NotFoundError, ValidationError

EINVOICE_LOCK_KEY = 0x45494E56
KNOWN_STATUSES = ("開立", "作廢", "折讓")
VOID_STATUS = "作廢"


def lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": EINVOICE_LOCK_KEY})


def clear_stamp(db: Session, entry_id: int, number: str) -> None:
    """Clear the entry's invoice_number only when it still equals `number`."""
    db.execute(update(LedgerEntry).where(LedgerEntry.id == entry_id, LedgerEntry.invoice_number == number)
               .values(invoice_number=None))


def stamp(db: Session, entry_id: int, number: str) -> None:
    """Set invoice_number only when NULL. Ignores is_locked (R8)."""
    db.execute(update(LedgerEntry).where(LedgerEntry.id == entry_id, LedgerEntry.invoice_number.is_(None))
               .values(invoice_number=number))


def remove_link(db: Session, invoice: Einvoice) -> None:
    """Delete the link row. The caller sets the new state first: the AFTER DELETE trigger only resets `linked`."""
    link = db.get(EinvoiceLink, invoice.id)
    if link is None:
        return
    if link.entry_id is not None:
        clear_stamp(db, link.entry_id, invoice.number)
    db.delete(link)
    db.flush()
    db.refresh(invoice)  # the trigger may have rewritten the row


def void_invoice(db: Session, invoice: Einvoice) -> None:
    invoice.is_void = True
    invoice.state = "void"
    invoice.dismiss_reason = None
    db.flush()  # before the link delete, so the trigger sees a non-linked state
    remove_link(db, invoice)


def _items_of(rows) -> list[tuple[int, str, Decimal]]:
    return sorted((r.seq, r.name, Decimal(r.subtotal)) for r in rows)


def _upsert(db: Session, inv: EinvoiceIn, counts: dict) -> None:
    period = period_of(inv.issue_date)
    row = db.execute(select(Einvoice).where(Einvoice.number == inv.number, Einvoice.period == period)
                     .with_for_update()).scalar_one_or_none()
    new_items = sorted((i.seq, i.name, Decimal(i.subtotal)) for i in inv.items)
    if inv.status not in KNOWN_STATUSES:
        counts["unknown_status"] += 1
    if row is None:
        row = Einvoice(number=inv.number, issue_date=inv.issue_date, period=period, seller_tax_id=inv.seller_tax_id,
                       seller_name=inv.seller_name, total_amount=inv.total_amount, status_raw=inv.status,
                       state="unmatched", source_drive_id=inv.source_drive_id)
        db.add(row)
        db.flush()
        db.add_all(EinvoiceItem(invoice_id=row.id, seq=s, name=n, subtotal=t) for s, n, t in new_items)
        db.flush()
        counts["inserted"] += 1
        if inv.status == VOID_STATUS:
            void_invoice(db, row)
            counts["voided"] += 1
        return
    if row.is_void:  # sticky (§6)
        counts["unchanged"] += 1
        return
    old_items = _items_of(db.execute(select(EinvoiceItem).where(EinvoiceItem.invoice_id == row.id)).scalars())
    same = (row.issue_date == inv.issue_date and row.seller_tax_id == inv.seller_tax_id
            and row.seller_name == inv.seller_name and Decimal(row.total_amount) == inv.total_amount
            and row.status_raw == inv.status and old_items == new_items)
    if same and inv.status != VOID_STATUS:
        counts["unchanged"] += 1  # R3: touch nothing, not even updated_at or source_drive_id
        return
    row.issue_date = inv.issue_date
    row.seller_tax_id, row.seller_name = inv.seller_tax_id, inv.seller_name
    row.total_amount, row.status_raw, row.source_drive_id = inv.total_amount, inv.status, inv.source_drive_id
    if old_items != new_items:
        db.query(EinvoiceItem).filter(EinvoiceItem.invoice_id == row.id).delete()
        db.add_all(EinvoiceItem(invoice_id=row.id, seq=s, name=n, subtotal=t) for s, n, t in new_items)
    db.flush()
    if inv.status == VOID_STATUS:
        void_invoice(db, row)
        counts["voided"] += 1
    else:
        counts["updated"] += 1


def import_invoices(db: Session, body: EinvoiceImportIn) -> dict:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "voided": 0, "unknown_status": 0}
    for inv in body.invoices:  # body order: a later copy of the same invoice wins
        _upsert(db, inv, counts)
    return counts


# --- owner actions (§7.5), mutes (§8), runbook (§13) ---------------------------------------------------------
# Every action takes the e-invoice lock first, then reads the invoice: a racing action re-reads after the winner
# commits. Every path that removes a link sets the new state and flushes before `remove_link` (the trigger).

UNDISMISSABLE = ("owner", "runbook")


def _invoice(db: Session, invoice_id: int) -> Einvoice:
    row = db.execute(select(Einvoice).where(Einvoice.id == invoice_id).with_for_update()
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"einvoice {invoice_id} not found")
    return row


def _current_link(db: Session, invoice_id: int) -> EinvoiceLink | None:
    return db.execute(select(EinvoiceLink).where(EinvoiceLink.invoice_id == invoice_id)
                      .execution_options(populate_existing=True)).scalar_one_or_none()


def _state_out(db: Session, invoice: Einvoice) -> dict:
    link = _current_link(db, invoice.id)
    return {"id": invoice.id, "state": invoice.state, "dismiss_reason": invoice.dismiss_reason,
            "linked_by": link.linked_by if link else None, "entry_id": link.entry_id if link else None,
            "group_id": link.group_id if link else None}


def _eligible(db: Session, invoice: Einvoice, entry_id: int | None, group_id: int | None) -> bool:
    from .einvoice_matcher import entry_target, group_target  # the matcher imports this module

    if entry_id is not None:
        target = entry_target(db, entry_id)
        return target is not None and target.invoice_number in (None, invoice.number)
    return group_target(db, group_id) is not None


def link(db: Session, invoice_id: int, *, entry_id: int | None, group_id: int | None) -> dict:
    """Owner link / re-link (§7.5). Eligibility is checked under the lock, so a racing link of the same target
    gets `target_ineligible`. A re-link updates the link row in place and never deletes it (§5)."""
    lock(db)
    invoice = _invoice(db, invoice_id)
    if invoice.state == "void":
        raise CodedConflictError("einvoice_void")
    current = _current_link(db, invoice.id)
    own = current is not None and (current.entry_id, current.group_id) == (entry_id, group_id)
    if not own and not _eligible(db, invoice, entry_id, group_id):
        raise CodedConflictError("target_ineligible")
    previous_entry = current.entry_id if current is not None else None
    try:
        with db.begin_nested():
            if current is None:
                db.add(EinvoiceLink(invoice_id=invoice.id, entry_id=entry_id, group_id=group_id, linked_by="owner"))
            else:
                current.linked_by = "owner"
                if not own:
                    current.entry_id, current.group_id, current.linked_at = entry_id, group_id, func.now()
            db.flush()
    except IntegrityError as exc:  # a concurrent write took the target (R6)
        raise CodedConflictError(RETRY, "the target was linked meanwhile") from exc
    if previous_entry is not None and previous_entry != entry_id:
        clear_stamp(db, previous_entry, invoice.number)
    if entry_id is not None:
        stamp(db, entry_id, invoice.number)  # groups are never stamped (§5)
    invoice.state, invoice.dismiss_reason = "linked", None
    db.flush()
    return _state_out(db, invoice)


def unlink(db: Session, invoice_id: int) -> dict:
    """Unlink = dismissed(owner), overwriting seller_muted, so the next run does not re-link it (§7.5)."""
    lock(db)
    invoice = _invoice(db, invoice_id)
    if _current_link(db, invoice.id) is None:
        raise CodedConflictError("not_linked")
    invoice.state, invoice.dismiss_reason = "dismissed", "owner"
    db.flush()  # before the link delete, so the trigger sees a non-linked state
    remove_link(db, invoice)
    return _state_out(db, invoice)


def dismiss(db: Session, invoice_id: int, *, reason: str = "owner", mute_seller: bool = False) -> dict:
    lock(db)
    invoice = _invoice(db, invoice_id)
    if invoice.state == "void":
        raise CodedConflictError("einvoice_void")
    if mute_seller and not invoice.seller_tax_id:
        raise ValidationError("mute_seller", "the invoice has no seller tax id")
    invoice.state, invoice.dismiss_reason = "dismissed", reason
    db.flush()  # before the link delete, so the trigger sees a non-linked state
    remove_link(db, invoice)
    if mute_seller:
        create_mute(db, invoice.seller_tax_id)
    return _state_out(db, invoice)


def undismiss(db: Session, invoice_id: int) -> dict:
    """Back to `unmatched` for the next run. A seller_muted invoice comes back by removing the mute instead."""
    lock(db)
    invoice = _invoice(db, invoice_id)
    if invoice.state != "dismissed" or invoice.dismiss_reason not in UNDISMISSABLE:
        raise CodedConflictError("not_dismissed")
    if invoice.seller_tax_id and db.get(EinvoiceSellerMute, invoice.seller_tax_id) is not None:
        raise CodedConflictError("seller_muted")
    invoice.state, invoice.dismiss_reason = "unmatched", None
    db.flush()
    return _state_out(db, invoice)


def create_mute(db: Session, tax_id: str) -> None:
    """Idempotent. The seller's `unmatched` invoices become dismissed(seller_muted) (§8)."""
    lock(db)
    db.execute(pg_insert(EinvoiceSellerMute).values(seller_tax_id=tax_id)
               .on_conflict_do_nothing(index_elements=["seller_tax_id"]))
    db.execute(update(Einvoice).where(Einvoice.seller_tax_id == tax_id, Einvoice.state == "unmatched")
               .values(state="dismissed", dismiss_reason="seller_muted"))
    db.flush()


def delete_mute(db: Session, tax_id: str) -> None:
    """The seller's dismissed(seller_muted) invoices return to `unmatched` (§8)."""
    lock(db)
    mute = db.get(EinvoiceSellerMute, tax_id, with_for_update=True)
    if mute is None:
        raise NotFoundError(f"mute {tax_id} not found")
    db.delete(mute)
    db.execute(update(Einvoice)
               .where(Einvoice.seller_tax_id == tax_id, Einvoice.state == "dismissed",
                      Einvoice.dismiss_reason == "seller_muted")
               .values(state="unmatched", dismiss_reason=None))
    db.flush()


def list_mutes(db: Session) -> list[dict]:
    """Every mute, newest first, with the seller name of that seller's latest invoice. Read-only: no lock."""
    latest_name = (select(Einvoice.seller_name).where(Einvoice.seller_tax_id == EinvoiceSellerMute.seller_tax_id)
                   .order_by(Einvoice.issue_date.desc(), Einvoice.id.desc()).limit(1).scalar_subquery())
    rows = db.execute(select(EinvoiceSellerMute.seller_tax_id, latest_name, EinvoiceSellerMute.created_at)
                      .order_by(EinvoiceSellerMute.created_at.desc(), EinvoiceSellerMute.seller_tax_id)).all()
    return [{"seller_tax_id": tax_id, "seller_name": name, "created_at": created} for tax_id, name, created in rows]


def runbook_dismiss(db: Session, refs: Sequence[RunbookRefIn]) -> dict:
    """Rollout step 3 (§13): dismiss(runbook) each (number, issue_date); a missing or void one is reported by index."""
    lock(db)
    dismissed, not_found = 0, []
    for index, ref in enumerate(refs):
        invoice_id = db.scalar(select(Einvoice.id).where(Einvoice.number == ref.number,
                                                          Einvoice.period == period_of(ref.issue_date),
                                                          Einvoice.state != "void"))
        if invoice_id is None:
            not_found.append(index)
            continue
        dismiss(db, invoice_id, reason="runbook")
        dismissed += 1
    return {"dismissed": dismissed, "not_found": not_found}
