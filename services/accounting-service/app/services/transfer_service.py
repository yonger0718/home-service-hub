"""Transfers: a transfer_out and a transfer_in leg sharing a transfer_group_id (design D5, spec "Transfer endpoint").

For cross-currency transfers each leg stores the other leg's amount, carrying this leg's sign, as
original_amount / original_currency, and fx_rate = |amount| / |original_amount| rounded half-up to 10 decimals.
The two amounts are the source of truth; `fx_consistent` is the only allowed consistency check.
"""

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Account, Category, LedgerEntry
from ..schemas.writes import TransferIn
from .entry_write_service import (
    RATE_QUANTUM,
    assert_entry_editable,
    check_project,
    check_rules,
    display_quantum,
    locked_with_legs,
    remember_defaults,
    write_children,
    write_rule_links,
)
from .errors import NotFoundError, ValidationError

NO_FX = {"original_amount": None, "original_currency": None, "fx_rate": None, "fx_source": None}


def fx_consistent(amount: Decimal, original_amount: Decimal, fx_rate: Decimal, currency: str) -> bool:
    """original_amount × fx_rate ≈ amount within half of the currency's smallest displayed unit."""
    return abs(Decimal(original_amount) * Decimal(fx_rate) - Decimal(amount)) <= display_quantum(currency) / 2


def _rate(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _account(db: Session, account_id: int, field: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise ValidationError(field, f"account {account_id} not found")
    return account


def matching_in_category_id(db: Session, out_category: Category) -> int | None:
    """The transfer_in category with the same name (and same parent name), or None."""
    in_parent_id = None
    if out_category.parent_id is not None:
        parent_name = db.get(Category, out_category.parent_id).name
        in_parent_id = db.scalar(
            select(Category.id).where(
                Category.kind == "transfer_in", Category.parent_id.is_(None), Category.name == parent_name
            )
        )
        if in_parent_id is None:
            return None
    parent_filter = Category.parent_id.is_(None) if in_parent_id is None else Category.parent_id == in_parent_id
    return db.scalar(
        select(Category.id).where(Category.kind == "transfer_in", parent_filter, Category.name == out_category.name)
    )


def _leg_values(db: Session, payload: TransferIn) -> tuple[dict, dict]:
    """Validate the payload and return the column values of the out leg and the in leg."""
    if payload.from_account_id == payload.to_account_id:
        raise ValidationError("to_account_id", "must differ from from_account_id")
    source = _account(db, payload.from_account_id, "from_account_id")
    target = _account(db, payload.to_account_id, "to_account_id")
    in_category_id = None
    if payload.category_id is not None:
        category = db.get(Category, payload.category_id)
        if category is None or category.kind != "transfer_out":
            raise ValidationError("category_id", "must be a transfer_out category")
        in_category_id = matching_in_category_id(db, category)
    check_project(db, payload.project_id)
    check_rules(db, payload.reward_rule_ids, source.id, payload.entry_date)

    out_amount = payload.out_amount
    if source.currency == target.currency:
        if payload.in_amount is not None and payload.in_amount != out_amount:
            raise ValidationError("in_amount", "must equal out_amount when both accounts use the same currency")
        in_amount, out_fx, in_fx = out_amount, NO_FX, NO_FX
    else:
        if payload.in_amount is None:
            raise ValidationError("in_amount", "required when the two accounts use different currencies")
        in_amount = payload.in_amount
        out_fx = {
            "original_amount": -in_amount, "original_currency": target.currency,
            "fx_rate": _rate(out_amount, in_amount), "fx_source": "manual",
        }
        in_fx = {
            "original_amount": out_amount, "original_currency": source.currency,
            "fx_rate": _rate(in_amount, out_amount), "fx_source": "manual",
        }

    common = {
        "entry_date": payload.entry_date,
        "entry_time": payload.entry_time,
        "posted_date": payload.posted_date or payload.entry_date,
        "project_id": payload.project_id,
        "name": payload.name,
        "merchant": payload.merchant,
        "description": payload.description,
        "tags": list(payload.tags),
        "counterparty_id": None,
        "needs_review": False,
        "source": "manual",
    }
    out_values = {
        **common, **out_fx, "account_id": source.id, "currency": source.currency, "kind": "transfer_out",
        "amount": -out_amount, "category_id": payload.category_id,
    }
    in_values = {
        **common, **in_fx, "account_id": target.id, "currency": target.currency, "kind": "transfer_in",
        "amount": in_amount, "category_id": in_category_id,
    }
    return out_values, in_values


def _write_leg_extras(db: Session, out_leg: LedgerEntry, in_leg: LedgerEntry, payload: TransferIn) -> None:
    write_children(db, out_leg, payload.out_fee, payload.out_discount)
    write_children(db, in_leg, payload.in_fee, payload.in_discount)
    write_rule_links(db, out_leg.id, payload.reward_rule_ids)
    write_rule_links(db, in_leg.id, [])
    remember_defaults(db, payload.category_id, payload.from_account_id, payload.project_id)


def create_transfer(db: Session, payload: TransferIn) -> UUID:
    out_values, in_values = _leg_values(db, payload)
    group_id = uuid4()
    out_leg = LedgerEntry(transfer_group_id=group_id, **out_values)
    in_leg = LedgerEntry(transfer_group_id=group_id, **in_values)
    db.add_all([out_leg, in_leg])
    db.flush()
    _write_leg_extras(db, out_leg, in_leg, payload)
    return group_id


def _top_level_legs(group_id: UUID):
    return select(LedgerEntry).where(LedgerEntry.transfer_group_id == group_id, LedgerEntry.parent_entry_id.is_(None))


def _out_and_in(legs: list[LedgerEntry], group_id: UUID) -> tuple[LedgerEntry, LedgerEntry]:
    if not legs:
        raise NotFoundError(f"transfer {group_id} not found")
    outs = [leg for leg in legs if leg.kind == "transfer_out"]
    ins = [leg for leg in legs if leg.kind == "transfer_in"]
    if len(outs) != 1 or len(ins) != 1:
        raise ValidationError("transfer_group_id", "a transfer needs exactly one transfer_out and one transfer_in leg")
    return outs[0], ins[0]


def transfer_legs(db: Session, group_id: UUID) -> tuple[LedgerEntry, LedgerEntry]:
    return _out_and_in(list(db.scalars(_top_level_legs(group_id))), group_id)


def _locked_legs(db: Session, group_id: UUID) -> tuple[LedgerEntry, LedgerEntry]:
    """Both legs locked in ONE SELECT … FOR UPDATE by ascending id (entry_write_service.locked_with_legs), the
    same lock order as DELETE /entries on either leg, so a concurrent delete and update cannot deadlock."""
    first_id = db.scalar(_top_level_legs(group_id).with_only_columns(LedgerEntry.id).order_by(LedgerEntry.id).limit(1))
    if first_id is None:
        raise NotFoundError(f"transfer {group_id} not found")
    legs = [leg for leg in locked_with_legs(db, first_id, group_id) if leg.transfer_group_id == group_id]
    return _out_and_in(legs, group_id)


def update_transfer(db: Session, group_id: UUID, payload: TransferIn) -> None:
    """Replace both legs' fields, children and rule links in place (the leg ids stay).

    Lock order: no FX lookup happens here (the rates derive from the two amounts, nothing commits the session),
    so both legs are locked first, together, then validated and written; remember_defaults runs after the locks.
    """
    out_leg, in_leg = _locked_legs(db, group_id)
    assert_entry_editable(db, out_leg)
    assert_entry_editable(db, in_leg)
    out_values, in_values = _leg_values(db, payload)
    for leg, values in ((out_leg, out_values), (in_leg, in_values)):
        for column, value in values.items():
            setattr(leg, column, value)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id.in_([out_leg.id, in_leg.id])))
    db.flush()
    _write_leg_extras(db, out_leg, in_leg, payload)
