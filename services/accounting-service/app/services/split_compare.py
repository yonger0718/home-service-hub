"""Canonical comparison of a full split member against its stored row (split rework §1.4 step 1).

Inputs are compared unsigned (the payload's amounts) against `abs()` of the stored signed values. An online-FX
payload (amount and fx_rate both null) equals a stored row with the same original amount and currency whose
fx_source is the online source (`fx_api`; the spec's "online"). The verdict decides the write: "unchanged" →
skipped; "meta" → name / merchant / project / tags / description applied with no FX, no child or rule rebuild;
"financial" → prepared and rewritten like a single-entry update."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LedgerEntry
from ..schemas.writes import EntryIn
from .entry_write_service import AMOUNT_QUANTUM, CHILD_DEFAULT_NAMES, RATE_QUANTUM, attached_rule_ids

Change = Literal["unchanged", "meta", "financial"]
ONLINE_FX_SOURCE = "fx_api"


@dataclass(frozen=True)
class MemberSignature:
    financial: tuple  # kind, account, category, counterparty, FX inputs, fee/discount, rule ids, invoice, dates
    meta: tuple  # name, merchant, project, tags, description


def _abs(value) -> Decimal | None:
    return None if value is None else abs(Decimal(value))


def _children_key(rows) -> tuple:
    return tuple(sorted((kind, _abs(amount), name) for kind, amount, name in rows))


def _stored_fx(entry: LedgerEntry) -> tuple:
    if entry.original_currency is None:
        return ("plain", _abs(entry.amount))
    if entry.fx_source == ONLINE_FX_SOURCE:
        return ("online", _abs(entry.original_amount), entry.original_currency)
    rate = None if entry.fx_rate is None else Decimal(entry.fx_rate)
    return ("manual", _abs(entry.amount), _abs(entry.original_amount), entry.original_currency, rate)


def _payload_fx(payload: EntryIn, account_currency: str) -> tuple:
    """The FX inputs as resolve_fx would store them, without any rate lookup."""
    if payload.original_currency is None or payload.original_currency == account_currency:
        return ("plain", payload.amount)
    if payload.original_amount is None:
        return ("incomplete",)  # prepare_entry answers the 422
    if payload.fx_rate is not None:
        rate = Decimal(payload.fx_rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        amount = (payload.original_amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
        return ("manual", amount, payload.original_amount, payload.original_currency, rate)
    if payload.amount is not None:
        rate = (payload.amount / payload.original_amount).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        return ("manual", payload.amount, payload.original_amount, payload.original_currency, rate)
    return ("online", payload.original_amount, payload.original_currency)


def stored_signature(db: Session, entry: LedgerEntry) -> MemberSignature:
    children = db.execute(
        select(LedgerEntry.kind, LedgerEntry.amount, LedgerEntry.name).where(LedgerEntry.parent_entry_id == entry.id)
    ).all()
    financial = (
        entry.kind, entry.account_id, entry.category_id, entry.counterparty_id, _stored_fx(entry),
        _children_key(children), frozenset(attached_rule_ids(db, entry.id)), entry.invoice_number,
        entry.invoice_random, entry.entry_date, entry.entry_time, entry.posted_date,
    )
    meta = (entry.name, entry.merchant, entry.project_id, tuple(entry.tags or ()), entry.description)
    return MemberSignature(financial, meta)


def payload_signature(payload: EntryIn, account_currency: str) -> MemberSignature:
    """`account_currency`: the stored row's currency (when the account differs the verdict is financial anyway)."""
    children = [
        (kind, child.amount, child.name or CHILD_DEFAULT_NAMES[kind])
        for kind, child in (("fee", payload.fee), ("discount", payload.discount))
        if child is not None
    ]
    financial = (
        payload.kind, payload.account_id, payload.category_id, payload.counterparty_id,
        _payload_fx(payload, account_currency), _children_key(children), frozenset(payload.reward_rule_ids),
        payload.invoice_number, payload.invoice_random, payload.entry_date, payload.entry_time,
        payload.posted_date or payload.entry_date,
    )
    meta = (payload.name, payload.merchant, payload.project_id, tuple(payload.tags), payload.description)
    return MemberSignature(financial, meta)


def classify(stored: MemberSignature, sent: MemberSignature) -> Change:
    if stored.financial != sent.financial:
        return "financial"
    if stored.meta != sent.meta:
        return "meta"
    return "unchanged"
