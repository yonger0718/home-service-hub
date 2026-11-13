"""Collections, repayments and refunds linked to the entry they settle or refund (design D16, spec).

A settlement may come from an account in another currency when it states the `original_amount` it settles in the
target's currency (design D3); refunds still need an account in the original's currency (a later phase).
"""

from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..models import Account, LedgerEntry
from ..schemas.writes import RefundIn, SettleIn
from . import reward_engine
from .fx_rate_service import RATE_QUANTUM
from .entry_write_service import assert_entry_editable, locked_entry
from .errors import ValidationError
from .settled_value import settled_value

SETTLE_NAMES = {"receivable": "收款", "payable": "還款"}
SETTLE_SIGNS = {"receivable": 1, "payable": -1}
REFUNDABLE_KINDS = ("expense",)


def open_amount(db: Session, entry: LedgerEntry) -> Decimal:
    """|amount + Σ settled values of the entries settling it| (see `settled_value`: another currency counts its
    `original_amount` only when it is stated in this entry's currency)."""
    settlement = aliased(LedgerEntry)
    settled = db.scalar(
        select(func.coalesce(func.sum(settled_value(settlement, entry.currency)), 0)).where(
            settlement.settles_entry_id == entry.id
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


def settle(
    db: Session,
    entry_id: int,
    payload: SettleIn,
    *,
    source: str = "manual",
    check_cutover_lock: bool = True,
    name: str | None = None,
) -> int:
    """收款 (+amount, kind receivable) or 還款 (−amount, kind payable) pointing at the target, is_settlement=True.

    `locked_entry` (Task 12) holds the target FOR UPDATE until the request commits, so a concurrent settle,
    refund, PUT or DELETE of it either committed first or waits; two requests never both pass the open-amount check.
    """
    target = locked_entry(db, entry_id)
    if check_cutover_lock:  # False only from schedule_posting: a schedule may repay an imported loan (D36)
        assert_entry_editable(db, target)
    if target.kind not in SETTLE_SIGNS or target.is_settlement:
        raise ValidationError("kind", "only receivable and payable entries can be settled")
    if target.is_closed:
        raise ValidationError("is_closed", "已結清: the debt is closed")
    account = db.get(Account, payload.account_id)
    if account is None:
        raise ValidationError("account_id", f"account {payload.account_id} not found")
    sign = SETTLE_SIGNS[target.kind]
    fx_fields: dict = {}
    if account.currency == target.currency:
        if payload.original_amount is not None:
            raise ValidationError(
                "original_amount", f"account {account.name} uses {account.currency} like the entry; omit original_amount"
            )
        settled = payload.amount
    else:
        if payload.original_amount is None:
            raise ValidationError(
                "original_amount",
                f"account {account.name} uses {account.currency}; the entry is in {target.currency}: "
                "state original_amount",
            )
        with localcontext() as ctx:
            ctx.prec = 60
            rate = (payload.amount / payload.original_amount).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        if rate == 0 or rate >= Decimal("1E10"):
            raise ValidationError("original_amount", "the implied exchange rate is out of range")
        settled = payload.original_amount
        fx_fields = {
            "original_amount": payload.original_amount * sign,
            "original_currency": target.currency,
            "fx_rate": rate,
            "fx_source": "manual",
        }
    remaining = open_amount(db, target)
    if settled > remaining:
        raise ValidationError("amount", f"exceeds the open amount {remaining}")
    entry = LedgerEntry(
        account_id=account.id,
        currency=account.currency,
        kind=target.kind,
        amount=payload.amount * sign,
        **fx_fields,
        entry_date=payload.entry_date,
        entry_time=payload.entry_time,
        posted_date=payload.entry_date,
        name=name or SETTLE_NAMES[target.kind],
        counterparty_id=target.counterparty_id,
        description=payload.description,
        settles_entry_id=target.id,
        is_settlement=True,
        source=source,
    )
    db.add(entry)
    db.flush()
    return entry.id


def refund(db: Session, entry_id: int, payload: RefundIn, *, source: str = "manual") -> int:
    """A refund entry of +amount with the original's category, name and merchant; `source` is the new row's
    (`statement` from the reconciliation action `create_refund`).

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
        source=source,
    )
    db.add(entry)
    db.flush()
    reward_engine.sync_entry(db, entry.id)  # spec 5.1: one accrual per rule of the purchase, in its window
    return entry.id
