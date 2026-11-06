"""E-invoice reads (spec §8, §9): the inbox section, the manual-link target list and the dismissed list.
Never log invoice payloads (privacy)."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Category, Einvoice, EinvoiceItem, EinvoiceSellerMute
from .einvoice_matcher import Target, _candidates, _span, eligible_targets, order_by_closeness
from .errors import CodedConflictError, NotFoundError
from .ledger_service import main_currency
from .settings_service import read_einvoice_settings

STATE_ORDER = {"ambiguous": 0, "near": 1, "unmatched": 2}


def _category_names(db: Session, category_ids) -> dict[int, str]:
    """`parent/child` for a sub-category, the bare name for a root (how `ledger_service._entry_rows` shows it)."""
    ids = {c for c in category_ids if c is not None}
    if not ids:
        return {}
    rows = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(ids)))}
    parents = {c.parent_id for c in rows.values() if c.parent_id is not None} - set(rows)
    if parents:
        rows.update({c.id: c for c in db.scalars(select(Category).where(Category.id.in_(parents)))})
    return {cid: (f"{rows[c.parent_id].name}/{c.name}" if c.parent_id in rows else c.name)
            for cid, c in rows.items() if cid in ids}


def _target_dict(t: Target, names: dict[int, str]) -> dict:
    return {"kind": t.key[0], "id": t.key[1], "account_name": t.account_name, "date": t.day, "amount": t.net,
            "currency": t.currency, "category": names.get(t.category_id), "name": t.name}


def _targets_out(db: Session, targets: list[Target]) -> list[dict]:
    names = _category_names(db, (t.category_id for t in targets))
    return [_target_dict(t, names) for t in targets]


def target_out(db: Session, target: Target) -> dict:
    return _targets_out(db, [target])[0]


def _items_by_invoice(db: Session, invoice_ids) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    if not invoice_ids:
        return out
    for row in db.scalars(select(EinvoiceItem).where(EinvoiceItem.invoice_id.in_(list(invoice_ids)))
                          .order_by(EinvoiceItem.invoice_id, EinvoiceItem.seq)):
        out[row.invoice_id].append({"name": row.name, "subtotal": row.subtotal})
    return out


def _summary(inv: Einvoice, items: dict[int, list[dict]]) -> dict:
    return {"id": inv.id, "number": inv.number, "issue_date": inv.issue_date, "seller_name": inv.seller_name,
            "seller_tax_id": inv.seller_tax_id, "total_amount": inv.total_amount, "status_raw": inv.status_raw,
            "items": items.get(inv.id, [])}


def inbox_section(db: Session, *, limit: int) -> tuple[list[dict], int]:
    settings = read_einvoice_settings(db)
    if not settings["enabled"]:
        return [], 0
    states = ["ambiguous", "near"] + (["unmatched"] if settings["remind_unmatched"] else [])
    invoices = list(db.scalars(select(Einvoice).where(Einvoice.state.in_(states))))
    invoices.sort(key=lambda i: (STATE_ORDER[i.state], -i.issue_date.toordinal(), i.id))
    # the bell counts questions only, over the whole set (reminders never light it)
    ask = sum(1 for i in invoices if i.state != "unmatched")
    page = invoices[:limit]
    if not page:
        return [], ask

    window_days, main = settings["date_window_days"], main_currency(db)
    asking = [i for i in page if i.state != "unmatched"]
    candidates: dict[int, list[Target]] = {}
    if asking:  # one eligible-targets query over the union window, then the pure rules per invoice
        spans = [_span(i, window_days) for i in asking]
        targets = eligible_targets(db, min(s for s, _ in spans), max(e for _, e in spans))
        for inv in asking:
            found, _ = _candidates(inv, targets, window_days, main)
            candidates[inv.id] = order_by_closeness(found, abs(Decimal(inv.total_amount)), inv.issue_date)

    tax_ids = {i.seller_tax_id for i in page if i.state == "unmatched" and i.seller_tax_id}
    muted: set[str] = set()
    dismissed_count: dict[str, int] = {}
    if tax_ids:
        muted = set(db.scalars(select(EinvoiceSellerMute.seller_tax_id)
                               .where(EinvoiceSellerMute.seller_tax_id.in_(tax_ids))))
        dismissed_count = dict(db.execute(
            select(Einvoice.seller_tax_id, func.count()).where(
                Einvoice.seller_tax_id.in_(tax_ids), Einvoice.state == "dismissed",
                Einvoice.dismiss_reason == "owner").group_by(Einvoice.seller_tax_id)).all())

    flat = [t for found in candidates.values() for t in found]
    names = _category_names(db, (t.category_id for t in flat))
    items = _items_by_invoice(db, [i.id for i in page])
    out = []
    for inv in page:
        cards = [_target_dict(t, names) for t in candidates.get(inv.id, [])]
        offer = (inv.state == "unmatched" and bool(inv.seller_tax_id) and inv.seller_tax_id not in muted
                 and dismissed_count.get(inv.seller_tax_id, 0) >= settings["mute_prompt_after"])
        out.append({**_summary(inv, items), "state": inv.state, "candidates": cards, "offer_mute": offer})
    return out, ask


def invoice_targets(db: Session, invoice_id: int, *, days: int) -> list[dict]:
    invoice = db.get(Einvoice, invoice_id)
    if invoice is None:
        raise NotFoundError(f"einvoice {invoice_id} not found")
    if invoice.state == "void":
        raise CodedConflictError("einvoice_void")
    window = timedelta(days=days)
    targets = eligible_targets(db, invoice.issue_date - window, invoice.issue_date + window)
    ordered = order_by_closeness(targets, abs(Decimal(invoice.total_amount)), invoice.issue_date)
    return _targets_out(db, ordered)


def dismissed_invoices(db: Session, *, limit: int = 100) -> list[dict]:
    rows = list(db.scalars(
        select(Einvoice).where(Einvoice.state == "dismissed", Einvoice.dismiss_reason.in_(("owner", "runbook")))
        .order_by(Einvoice.updated_at.desc(), Einvoice.id.desc()).limit(limit)))
    items = _items_by_invoice(db, [i.id for i in rows])
    return [{**_summary(i, items), "dismiss_reason": i.dismiss_reason} for i in rows]
