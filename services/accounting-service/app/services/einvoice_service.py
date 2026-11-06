"""E-invoice import and link bookkeeping (spec §5-§6). Never log invoice payloads (privacy)."""
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from ..models import Einvoice, EinvoiceItem, EinvoiceLink, LedgerEntry
from ..schemas.einvoices import EinvoiceImportIn, EinvoiceIn
from .einvoice_rules import period_of

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
