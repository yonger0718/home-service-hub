"""Collections, repayments and refunds linked to the entry they settle or refund (design D16, spec).

Only accounts in the target entry's currency may receive them (cross-currency settlement is a later phase).
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, LedgerEntry
from ..schemas.writes import RefundIn, SettleIn
from .entry_write_service import assert_entry_editable, locked_entry
from .errors import ValidationError

SETTLE_NAMES = {"receivable": "收款", "payable": "還款"}
SETTLE_SIGNS = {"receivable": 1, "payable": -1}
REFUNDABLE_KINDS = ("expense",)


def open_amount(db: Session, entry: LedgerEntry) -> Decimal:
    """|amount + Σ amounts of the entries settling it in its own currency| (another currency never nets)."""
    settled = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.settles_entry_id == entry.id, LedgerEntry.currency == entry.currency
        )
    )
    return abs(Decimal(entry.amount) + Decimal(settled))


def refunded_amount(db: Session, entry: LedgerEntry) -> Decimal:
    refunded = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.refunds_entry_id == entry.id, LedgerEntry.kind == "refund"
        )
    )
    return Decimal(refunded)


def _same_currency_account(db: Session, account_id: int, currency: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise ValidationError("account_id", f"account {account_id} not found")
    if account.currency != currency:
        raise ValidationError("currency", f"account {account.name} uses {account.currency}; the entry is in {currency}")
    return account


def settle(db: Session, entry_id: int, payload: SettleIn) -> int:
    """收款 (+amount, kind receivable) or 還款 (−amount, kind payable) pointing at the target, is_settlement=True.

    `locked_entry` (Task 12) holds the target FOR UPDATE until the request commits, so a concurrent settle,
    refund, PUT or DELETE of it either committed first or waits; two requests never both pass the open-amount check.
    """
    target = locked_entry(db, entry_id)
    assert_entry_editable(db, target)
    if target.kind not in SETTLE_SIGNS or target.is_settlement:
        raise ValidationError("kind", "only receivable and payable entries can be settled")
    if target.is_closed:
        raise ValidationError("is_closed", "已結清: the debt is closed")
    account = _same_currency_account(db, payload.account_id, target.currency)
    remaining = open_amount(db, target)
    if payload.amount > remaining:
        raise ValidationError("amount", f"exceeds the open amount {remaining}")
    entry = LedgerEntry(
        account_id=account.id,
        currency=account.currency,
        kind=target.kind,
        amount=payload.amount * SETTLE_SIGNS[target.kind],
        entry_date=payload.entry_date,
        entry_time=payload.entry_time,
        posted_date=payload.entry_date,
        name=SETTLE_NAMES[target.kind],
        counterparty_id=target.counterparty_id,
        description=payload.description,
        settles_entry_id=target.id,
        is_settlement=True,
        source="manual",
    )
    db.add(entry)
    db.flush()
    return entry.id


def refund(db: Session, entry_id: int, payload: RefundIn) -> int:
    """A refund entry of +amount with the original's category, name and merchant.

    A group member is an ordinary entry and may be refunded; a group has no entry id, so it cannot be targeted.
    """
    original = locked_entry(db, entry_id)
    assert_entry_editable(db, original)
    if original.kind not in REFUNDABLE_KINDS or original.parent_entry_id is not None:
        raise ValidationError("kind", "only expense entries can be refunded")
    account = _same_currency_account(db, payload.account_id or original.account_id, original.currency)
    refundable = abs(Decimal(original.amount)) - refunded_amount(db, original)
    if payload.amount > refundable:
        raise ValidationError("amount", f"exceeds the refundable amount {refundable}")
    entry = LedgerEntry(
        account_id=account.id,
        currency=account.currency,
        kind="refund",
        amount=payload.amount,
        entry_date=payload.entry_date,
        entry_time=payload.entry_time,
        posted_date=payload.entry_date,
        category_id=original.category_id,
        name=original.name,
        merchant=original.merchant,
        description=payload.description,
        refunds_entry_id=original.id,
        source="manual",
    )
    db.add(entry)
    db.flush()
    return entry.id
