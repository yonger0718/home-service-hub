"""Manual entry writes: validation, FX resolution, fee/discount children and rule links (design D19–D21).

Services never commit; routers commit after a successful call. `prepare_entry` runs every check and the FX
lookup before anything is written, so a multi-entry write (split) inserts all of its rows or none.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Iterable

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, EntryGroup, EntryRewardRule, LedgerEntry, Project, RewardRule
from ..schemas.writes import BalanceAdjustmentIn, ChildIn, EntryIn, EntryUpdateIn
from . import fx_rate_service, ledger_service
from .edit_lock import assert_editable
from .errors import ConflictError, NotFoundError, ValidationError  # noqa: F401  (ValidationError re-exported)
from .moze_import_service import SYSTEM_CATEGORY_NAMES

AMOUNT_QUANTUM = Decimal("0.0001")
RATE_QUANTUM = fx_rate_service.RATE_QUANTUM
SIGN_BY_KIND = {"expense": -1, "receivable": -1, "income": 1, "payable": 1}
EDITABLE_KINDS = tuple(SIGN_BY_KIND)
COUNTERPARTY_KINDS = ("receivable", "payable")
CHILD_DEFAULT_NAMES = {"fee": "手續費", "discount": "折扣"}
FX_FEE_CHILD_NAME = "國外交易手續費"
WHOLE_UNIT_CURRENCIES = ("TWD", "JPY")
_ROUNDING = {"round": ROUND_HALF_UP, "floor": ROUND_FLOOR, "ceil": ROUND_CEILING}


@dataclass(frozen=True)
class FxResult:
    amount: Decimal
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    fx_source: str | None


@dataclass(frozen=True)
class PreparedEntry:
    payload: EntryIn
    account: Account
    fx: FxResult
    posted_date: date


def signed(kind: str, unsigned: Decimal) -> Decimal:
    """expense/receivable → negative; income/payable → positive."""
    if kind not in SIGN_BY_KIND:
        raise ValueError(f"no sign rule for kind {kind!r}")
    return unsigned if SIGN_BY_KIND[kind] > 0 else -unsigned


def display_quantum(currency: str) -> Decimal:
    """Smallest displayed unit: whole units for TWD and JPY, cents otherwise."""
    return Decimal("1") if currency in WHOLE_UNIT_CURRENCIES else Decimal("0.01")


def _round_amount(value: Decimal) -> Decimal:
    return value.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def proposed_fx_fee(account, amount: Decimal) -> Decimal | None:
    """|amount| × fx_fee_pct / 100, rounded per fx_fee_rounding to the display unit; None without a pct."""
    if account.fx_fee_pct is None:
        return None
    raw = abs(Decimal(amount)) * Decimal(account.fx_fee_pct) / Decimal(100)
    rounding = account.fx_fee_rounding or "keep"
    if rounding == "keep":
        return _round_amount(raw)
    return raw.quantize(display_quantum(account.currency), rounding=_ROUNDING[rounding])


def resolve_fx(
    db: Session,
    account: Account,
    *,
    signed_amount: Decimal | None,
    original_amount: Decimal | None,
    original_currency: str | None,
    fx_rate: Decimal | None,
    entry_date: date,
    http_get=None,
) -> FxResult:
    """Amounts arrive signed. Rate precedence: request fx_rate, then amount / original, then the daily cache."""
    if original_currency is None or original_currency == account.currency:
        if signed_amount is None:
            raise ValidationError("amount", "amount is required")
        return FxResult(signed_amount, None, None, None, None)
    if original_amount is None:
        raise ValidationError("original_amount", "original_amount is required with original_currency")
    if fx_rate is not None:
        rate = Decimal(fx_rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        result = FxResult(_round_amount(original_amount * rate), original_amount, original_currency, rate, "manual")
    elif signed_amount is not None:
        rate = (abs(signed_amount) / abs(original_amount)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        result = FxResult(signed_amount, original_amount, original_currency, rate, "manual")
    else:
        try:
            rate = fx_rate_service.get_rate(db, entry_date, original_currency, account.currency, http_get)
        except fx_rate_service.FxRateUnavailableError as exc:
            raise ValidationError("fx_rate", f"no rate for {original_currency}→{account.currency}: {exc}") from exc
        result = FxResult(_round_amount(original_amount * rate), original_amount, original_currency, rate, "fx_api")
    if result.amount == 0:
        raise ValidationError("amount", "the converted amount rounds to zero")
    return result


def _require(db: Session, model, row_id: int, field: str, label: str):
    row = db.get(model, row_id)
    if row is None:
        raise ValidationError(field, f"{label} {row_id} not found")
    return row


def check_category(db: Session, category_id: int | None, kind: str) -> Category | None:
    if category_id is None:
        return None
    category = _require(db, Category, category_id, "category_id", "category")
    if category.kind != kind:
        raise ValidationError("category_id", f"category {category_id} is a {category.kind} category, not {kind}")
    return category


def check_project(db: Session, project_id: int | None) -> None:
    if project_id is not None:
        _require(db, Project, project_id, "project_id", "project")


def check_rules(db: Session, rule_ids: Iterable[int], account_id: int, on: date) -> list[int]:
    """Every rule must belong to the account and be enabled on `on`; returns the ids without duplicates."""
    unique = list(dict.fromkeys(rule_ids))
    if not unique:
        return []
    rules = {rule.id: rule for rule in db.scalars(select(RewardRule).where(RewardRule.id.in_(unique)))}
    for rule_id in unique:
        rule = rules.get(rule_id)
        if rule is None or rule.account_id != account_id:
            raise ValidationError("reward_rule_ids", f"rule {rule_id} does not belong to account {account_id}")
        active = (
            rule.is_enabled
            and (rule.starts_on is None or rule.starts_on <= on)
            and (rule.ends_on is None or on <= rule.ends_on)
        )
        if not active:
            raise ValidationError("reward_rule_ids", f"rule {rule_id} is not enabled on {on.isoformat()}")
    return unique


def _check_counterparty(db: Session, kind: str, counterparty_id: int | None) -> None:
    if kind in COUNTERPARTY_KINDS:
        if counterparty_id is None:
            raise ValidationError("counterparty_id", "required for receivable and payable entries")
        _require(db, Counterparty, counterparty_id, "counterparty_id", "counterparty")
    elif counterparty_id is not None:
        raise ValidationError("counterparty_id", "only receivable and payable entries have a counterparty")


def prepare_entry(db: Session, payload: EntryIn, *, http_get=None) -> PreparedEntry:
    """Validate one entry payload and resolve its FX without writing anything."""
    account = _require(db, Account, payload.account_id, "account_id", "account")
    _check_counterparty(db, payload.kind, payload.counterparty_id)
    check_category(db, payload.category_id, payload.kind)
    check_project(db, payload.project_id)
    check_rules(db, payload.reward_rule_ids, account.id, payload.entry_date)
    fx = resolve_fx(
        db,
        account,
        signed_amount=None if payload.amount is None else signed(payload.kind, payload.amount),
        original_amount=None if payload.original_amount is None else signed(payload.kind, payload.original_amount),
        original_currency=payload.original_currency,
        fx_rate=payload.fx_rate,
        entry_date=payload.entry_date,
        http_get=http_get,
    )
    return PreparedEntry(payload, account, fx, payload.posted_date or payload.entry_date)


def _apply(entry: LedgerEntry, prepared: PreparedEntry) -> None:
    payload, account, fx = prepared.payload, prepared.account, prepared.fx
    entry.account_id = account.id
    entry.currency = account.currency
    entry.kind = payload.kind
    entry.amount = fx.amount
    entry.original_amount = fx.original_amount
    entry.original_currency = fx.original_currency
    entry.fx_rate = fx.fx_rate
    entry.fx_source = fx.fx_source
    entry.entry_date = payload.entry_date
    entry.entry_time = payload.entry_time
    entry.posted_date = prepared.posted_date
    entry.category_id = payload.category_id
    entry.project_id = payload.project_id
    entry.name = payload.name
    entry.merchant = payload.merchant
    entry.counterparty_id = payload.counterparty_id
    entry.is_settlement = False  # manual receivable / payable rows carry the sign of their kind; only settle() sets it
    entry.description = payload.description
    entry.tags = list(payload.tags)
    entry.invoice_number = payload.invoice_number
    entry.invoice_random = payload.invoice_random
    entry.needs_review = False
    entry.source = "manual"


def system_category_id(db: Session, kind: str) -> int:
    """The single fixed category of a system kind (fee, discount, reward, interest, balance_adjustment)."""
    name = SYSTEM_CATEGORY_NAMES[kind]
    category_id = db.scalar(
        select(Category.id).where(Category.kind == kind, Category.parent_id.is_(None), Category.name == name)
    )
    if category_id is None:
        category = Category(kind=kind, name=name)
        db.add(category)
        db.flush()
        category_id = category.id
    return category_id


def write_children(db: Session, parent: LedgerEntry, fee: ChildIn | None, discount: ChildIn | None) -> None:
    """Fee stored negative, discount positive; same account, date, time and posting date as the parent."""
    for kind, child in (("fee", fee), ("discount", discount)):
        if child is None:
            continue
        db.add(
            LedgerEntry(
                account_id=parent.account_id,
                currency=parent.currency,
                kind=kind,
                amount=-child.amount if kind == "fee" else child.amount,
                entry_date=parent.entry_date,
                entry_time=parent.entry_time,
                posted_date=parent.posted_date,
                category_id=system_category_id(db, kind),
                name=child.name or CHILD_DEFAULT_NAMES[kind],
                parent_entry_id=parent.id,
                source="manual",
            )
        )
    db.flush()


def write_rule_links(db: Session, entry_id: int, rule_ids: Iterable[int]) -> None:
    db.execute(delete(EntryRewardRule).where(EntryRewardRule.entry_id == entry_id))
    for rule_id in dict.fromkeys(rule_ids):
        db.add(EntryRewardRule(entry_id=entry_id, rule_id=rule_id))
    db.flush()


def remember_defaults(db: Session, category_id: int | None, account_id: int, project_id: int | None) -> None:
    """The category keeps the account and project last used with it (entry form defaults)."""
    if category_id is None:
        return
    category = db.get(Category, category_id)
    if category is not None:
        category.default_account_id = account_id
        category.default_project_id = project_id


def insert_prepared(db: Session, prepared: PreparedEntry, *, group_id: int | None = None) -> int:
    entry = LedgerEntry(group_id=group_id)
    _apply(entry, prepared)
    db.add(entry)
    db.flush()
    write_children(db, entry, prepared.payload.fee, prepared.payload.discount)
    write_rule_links(db, entry.id, prepared.payload.reward_rule_ids)
    remember_defaults(db, prepared.payload.category_id, prepared.account.id, prepared.payload.project_id)
    return entry.id


def create_entry(db: Session, payload: EntryIn, *, http_get=None) -> int:
    return insert_prepared(db, prepare_entry(db, payload, http_get=http_get))


def get_entry(db: Session, entry_id: int) -> LedgerEntry:
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"entry {entry_id} not found")
    return entry


def locked_entry(db: Session, entry_id: int) -> LedgerEntry:
    """Load the entry with SELECT … FOR UPDATE inside the request transaction (PUT, DELETE, settle, refund).

    A concurrent writer on the same entry blocks here until the first transaction commits, then reads the
    committed rows (READ COMMITTED takes a fresh snapshot per statement), so a settlement / refund check and the
    write it guards can never interleave with another request's. Never call anything that commits the session
    (fx_rate_service.get_rate does when it fetches) after this: the commit releases the lock.
    """
    entry = db.execute(
        select(LedgerEntry).where(LedgerEntry.id == entry_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"entry {entry_id} not found")
    return entry


# Lock order for every write that touches a group member (delete_entry here, split_service.update_split /
# delete_split in Task 14): the entry_group row(s) (lock_group, ascending id) → the target entry / the group's
# members together with their transfer legs, in ONE statement ordered by ascending id (locked_with_legs) →
# nothing else. Taking the group last (after a member) deadlocks against a split PUT that holds the group and
# waits for that member (plan review round 5); locking the target first and its other leg in a second statement
# deadlocks two concurrent deletes of a transfer's two legs (Task 12 review).


def locked_with_legs(db: Session, entry_id: int, transfer_group_id) -> list[LedgerEntry]:
    """The entry plus, when transfer_group_id is set, every top-level leg of that transfer, locked with one
    SELECT … FOR UPDATE ordered by ascending id, so two writers on different legs of one transfer take the row
    locks in the same order. Rows deleted by a transaction this one waited on are skipped (READ COMMITTED)."""
    condition = LedgerEntry.id == entry_id
    if transfer_group_id is not None:
        condition = or_(
            condition,
            and_(LedgerEntry.transfer_group_id == transfer_group_id, LedgerEntry.parent_entry_id.is_(None)),
        )
    return list(
        db.scalars(
            select(LedgerEntry)
            .where(condition)
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def lock_group(db: Session, group_id: int) -> EntryGroup | None:
    """The entry_group row, SELECT … FOR UPDATE; None when it is gone. Shared with split_service so both paths
    take the group lock through one function and in one order."""
    return db.execute(
        select(EntryGroup)
        .where(EntryGroup.id == group_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def assert_entry_editable(db: Session, entry: LedgerEntry) -> None:
    """The cutover lock on the entry itself and, for a group member, on its group and every member."""
    assert_editable(entry)
    if entry.group_id is not None:
        group = db.get(EntryGroup, entry.group_id)
        if group is not None:
            assert_editable(group)


def has_settlements_or_refunds(db: Session, entry_id: int) -> bool:
    """True while another entry settles or refunds this one (its settled_by / refunded_by is non-empty)."""
    dependant = db.scalar(
        select(LedgerEntry.id)
        .where((LedgerEntry.settles_entry_id == entry_id) | (LedgerEntry.refunds_entry_id == entry_id))
        .limit(1)
    )
    return dependant is not None


def _check_linked_original(db: Session, entry: LedgerEntry, prepared: PreparedEntry) -> None:
    """A settled or refunded original keeps kind, account, counterparty, currency and amount; other fields stay
    editable. Call it with `entry` loaded by `locked_entry`, so no settle / refund can commit in between."""
    if not has_settlements_or_refunds(db, entry.id):
        return
    changes = (
        ("kind", entry.kind, prepared.payload.kind),
        ("account_id", entry.account_id, prepared.account.id),
        ("counterparty_id", entry.counterparty_id, prepared.payload.counterparty_id),
        ("currency", entry.currency, prepared.account.currency),
        ("amount", Decimal(entry.amount), prepared.fx.amount),
    )
    for field, old, new in changes:
        if old != new:
            raise ValidationError(
                field, f"{field} cannot change while settlements or refunds point at this entry; delete them first"
            )


def update_entry(db: Session, entry_id: int, payload: EntryUpdateIn, *, http_get=None) -> None:
    """Settlement entries (`is_settlement`, with or without a settles_entry_id: the link is NULL after the
    original was deleted and imported collections may never have had one) and refund entries are never re-signed
    here: `signed(kind, amount)` would flip a +200 collection to −200. They are edited by deleting and
    re-settling / re-refunding.

    Order: `prepare_entry` (and with it resolve_fx) runs first because fx_rate_service.get_rate commits the
    session when it fetches and caches a daily rate, which would release a row lock taken before it. The target
    is then loaded with SELECT … FOR UPDATE (`locked_entry`, the same lock settle / refund take), and every guard
    runs inside that lock: a concurrent settle / refund has either committed (has_settlements_or_refunds sees
    it) or waits until this transaction commits.
    """
    prepared = prepare_entry(db, payload, http_get=http_get)
    entry = locked_entry(db, entry_id)
    assert_entry_editable(db, entry)
    if entry.transfer_group_id is not None and (payload.kind != entry.kind or payload.account_id != entry.account_id):
        field = "kind" if payload.kind != entry.kind else "account_id"
        raise ValidationError(field, "transfer legs keep kind and account; edit them with PUT /transfers/{transfer_group_id}")
    if entry.kind == "refund":
        raise ValidationError("kind", "refund entries are edited by deleting and re-refunding")
    if entry.is_settlement:
        raise ValidationError("kind", "settlement entries are edited by deleting and re-settling")
    if entry.kind not in EDITABLE_KINDS or entry.parent_entry_id is not None:
        raise ValidationError("kind", f"{entry.kind} entries are not edited through this endpoint")
    _check_linked_original(db, entry, prepared)
    _apply(entry, prepared)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id))
    db.flush()
    write_children(db, entry, payload.fee, payload.discount)
    write_rule_links(db, entry.id, payload.reward_rule_ids)
    remember_defaults(db, payload.category_id, prepared.account.id, payload.project_id)


def delete_entries_cascade(db: Session, entry_ids: Iterable[int]) -> None:
    """Delete entries and their fee/discount children; dependants keep existing with their links cleared."""
    parents = list(dict.fromkeys(entry_ids))
    if not parents:
        return
    children = list(db.scalars(select(LedgerEntry.id).where(LedgerEntry.parent_entry_id.in_(parents))))
    doomed = parents + children
    for column in (LedgerEntry.settles_entry_id, LedgerEntry.refunds_entry_id, LedgerEntry.reward_source_entry_id):
        db.execute(
            update(LedgerEntry)
            .where(column.in_(doomed), LedgerEntry.id.not_in(doomed))
            .values({column.key: None})
            .execution_options(synchronize_session=False)
        )
    if children:
        db.execute(delete(LedgerEntry).where(LedgerEntry.id.in_(children)).execution_options(synchronize_session=False))
    db.execute(delete(LedgerEntry).where(LedgerEntry.id.in_(parents)).execution_options(synchronize_session=False))
    db.expire_all()


def _group_ids_to_lock(db: Session, entry_id: int) -> tuple[list[int], object]:
    """The entry_group ids of the entry (and of its transfer legs), read without a lock so the group rows can be
    locked before the entries. group_id never changes on an existing row; a member a split PUT replaced is a
    new row, so locked_entry then raises NotFoundError."""
    entry = get_entry(db, entry_id)
    if entry.transfer_group_id is None:
        ids = {entry.group_id}
    else:
        ids = set(
            db.scalars(
                select(LedgerEntry.group_id).where(
                    LedgerEntry.transfer_group_id == entry.transfer_group_id, LedgerEntry.parent_entry_id.is_(None)
                )
            )
        )
    return sorted(group_id for group_id in ids if group_id is not None), entry.transfer_group_id


def delete_entry(db: Session, entry_id: int) -> None:
    """Deleting needs no FX, so the locks are taken straight away, in the shared lock order: the entry_group rows
    first (lock_group, ascending id; skipped when group_id is NULL), then the target and its other transfer leg in
    one statement ordered by id (locked_with_legs), then nothing else. Two concurrent deletes of a transfer's two
    legs therefore queue instead of deadlocking; the second then finds its leg gone (404). Plan review round 5:
    locking the member first and the group only when deleting the last member deadlocked against a concurrent
    split PUT, which locks group → members. A concurrent settle / refund
    of the target has either committed before (its link is cleared below) or waits for this delete and then
    finds no target (404); a concurrent split PUT / DELETE has either replaced the member (404 here) or waits on
    the group lock and then sees the member gone."""
    group_ids, transfer_group_id = _group_ids_to_lock(db, entry_id)
    for group_id in group_ids:
        lock_group(db, group_id)
    locked = locked_with_legs(db, entry_id, transfer_group_id)
    entry = next((row for row in locked if row.id == entry_id), None)
    if entry is None:
        raise NotFoundError(f"entry {entry_id} not found")
    if entry.transfer_group_id != transfer_group_id:
        raise ConflictError(f"entry {entry_id} changed concurrently; retry the delete")
    if entry.kind == "reward":
        raise ConflictError("reward entries cannot be deleted in phase 2a")
    targets = locked if entry.transfer_group_id is not None else [entry]
    for target in targets:
        assert_entry_editable(db, target)
    if not {target.group_id for target in targets if target.group_id is not None} <= set(group_ids):
        raise ConflictError(f"entry {entry_id} changed concurrently; retry the delete")
    delete_entries_cascade(db, [target.id for target in targets])
    for group_id in group_ids:
        remaining = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.group_id == group_id))
        if remaining == 0:
            db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))


def _plain(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def create_balance_adjustment(db: Session, payload: BalanceAdjustmentIn) -> int:
    """Checkpoint at save time (D20): amount = target − the balance right now; zero delta refused."""
    account = _require(db, Account, payload.account_id, "account_id", "account")
    delta = payload.target_balance - ledger_service.account_balance(db, account.id)
    if delta == 0:
        raise ValidationError("target_balance", "the account already has this balance")
    note = f"調整後餘額 {_plain(payload.target_balance)}"
    entry = LedgerEntry(
        account_id=account.id,
        currency=account.currency,
        kind="balance_adjustment",
        amount=delta,
        entry_date=payload.entry_date,
        entry_time=payload.entry_time,
        posted_date=payload.entry_date,
        category_id=system_category_id(db, "balance_adjustment"),
        description=f"{payload.description}\n{note}" if payload.description else note,
        source="manual",
    )
    db.add(entry)
    db.flush()
    return entry.id


def proposed_fee_for_entry(db: Session, entry_id: int) -> Decimal | None:
    """The fee to propose after a write: foreign entry, account with fx_fee_pct, no 國外交易手續費 child yet."""
    entry = get_entry(db, entry_id)
    if entry.original_currency is None or entry.original_currency == entry.currency:
        return None
    existing = db.scalar(
        select(LedgerEntry.id)
        .where(
            LedgerEntry.parent_entry_id == entry.id,
            LedgerEntry.kind == "fee",
            LedgerEntry.name == FX_FEE_CHILD_NAME,
        )
        .limit(1)
    )
    if existing is not None:
        return None
    return proposed_fx_fee(db.get(Account, entry.account_id), entry.amount)
