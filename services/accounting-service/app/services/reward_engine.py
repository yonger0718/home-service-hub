"""Reward engine: keeps `reward_accrual` rows in step with entries and rules, and allots each window's cap
(spec 4.1, 4.4, 5.1-5.3; plan P2, P3, P12).

Lock contract (P12): the caller locks its ledger rows first; the engine then takes every accrual row it needs in
**one** `SELECT ... ORDER BY id FOR UPDATE` (`lock_windows`) before it writes, and recomputes those windows with
`locked=True`. Nothing here commits; the caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, exists, func, select, tuple_
from sqlalchemy.orm import Session

from ..models import Account, EntryRewardRule, LedgerEntry, RewardAccrual, RewardRule
from . import reward_rule_service
from .reward_math import Slot, allot, estimate
from .reward_windows import post_on_for, window_for

FROZEN = ("posted", "bank_confirmed", "forfeited")
_Q = Decimal("0.0001")
_ZERO = Decimal("0")

Pair = tuple[int, date]  # (rule_id, window_start)


def card_closing_day(db: Session, account: Account) -> int | None:
    """The card's own closing_day, else its combined parent's, else None (calendar month)."""
    if account.closing_day is not None:
        return account.closing_day
    if account.combined_account_id is not None:
        parent = db.get(Account, account.combined_account_id)
        if parent is not None:
            return parent.closing_day
    return None


def _is_placeholder(rule: RewardRule) -> bool:
    """The MOZE placeholder shape (method fixed, fixed_amount 0) accrues nothing until migrated (5.3)."""
    return rule.method == "fixed" and not rule.fixed_amount


def _has_moze_reward(db: Session, purchase_id: int, rule_id: int) -> bool:
    """A reward row for (purchase, rule) with no accrual (MOZE era): no double booking (5.3)."""
    return bool(db.scalar(select(exists().where(
        LedgerEntry.reward_source_entry_id == purchase_id,
        LedgerEntry.reward_rule_id == rule_id,
        ~exists().where(RewardAccrual.ledger_entry_id == LedgerEntry.id),
    ))))


def _is_refund(entry: LedgerEntry) -> bool:
    return entry.kind == "refund"


def _purchase_of(db: Session, entry: LedgerEntry) -> LedgerEntry | None:
    """The purchase whose rules and window an entry accrues under: itself, or the refunded row for a refund
    (None for a refund of nothing)."""
    if not _is_refund(entry):
        return entry
    if entry.refunds_entry_id is None:
        return None
    return db.get(LedgerEntry, entry.refunds_entry_id)


def _target(db: Session, rule: RewardRule, entry: LedgerEntry, purchase: LedgerEntry):
    """(window, post_on, estimate) of `entry`'s accrual under `rule`; `purchase` is `entry` or the refunded row."""
    card = db.get(Account, rule.account_id)
    window = window_for(rule.window, card_closing_day(db, card), purchase.entry_date)
    post_on = post_on_for(rule.posting, rule.delay_days, rule.post_month_offset, rule.post_day, window,
                          entry.entry_date)
    refund_of = Decimal(purchase.amount) if entry is not purchase else None
    value = estimate(method=rule.method, rate=rule.rate, fixed_amount=rule.fixed_amount, unit_spend=rule.unit_spend,
                     txn_rounding=rule.txn_rounding, amount=Decimal(entry.amount), refund_of=refund_of)
    return window, post_on, value


def _desired(db: Session, entry: LedgerEntry) -> dict[int, tuple]:
    """rule_id -> (window, post_on, estimate) for the accruals `entry` should have."""
    purchase = _purchase_of(db, entry)
    if purchase is None:
        return {}
    rules = db.scalars(
        select(RewardRule)
        .join(EntryRewardRule, EntryRewardRule.rule_id == RewardRule.id)
        .where(EntryRewardRule.entry_id == purchase.id)
        .order_by(RewardRule.id)
    ).all()
    out = {}
    for rule in rules:
        if _is_placeholder(rule) or _has_moze_reward(db, purchase.id, rule.id):
            continue
        out[rule.id] = _target(db, rule, entry, purchase)
    return out


def _group_ids(db: Session, rule_id: int) -> list[int]:
    rule = db.get(RewardRule, rule_id)
    return reward_rule_service.group_rule_ids(db, rule) if rule is not None else [rule_id]


def _window_rows(db: Session, pairs: Iterable[Pair], *, lock: bool) -> list[RewardAccrual]:
    db.flush()  # populate_existing below must not discard pending changes
    keys = set()
    for rule_id, start in pairs:
        for member in _group_ids(db, rule_id):
            keys.add((member, start))
    if not keys:
        return []
    query = (
        select(RewardAccrual)
        .where(tuple_(RewardAccrual.rule_id, RewardAccrual.window_start).in_(sorted(keys)))
        .order_by(RewardAccrual.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    return list(db.scalars(query))


def lock_windows(db: Session, pairs: Iterable[Pair]) -> list[RewardAccrual]:
    """Lock every accrual of these windows (each rule's whole shared-cap group) in one
    `SELECT ... ORDER BY id FOR UPDATE` and return them, freshly loaded, in id order (P12 step 2)."""
    return _window_rows(db, pairs, lock=True)


def _frozen_amounts(db: Session, rows: list[RewardAccrual]) -> dict[int, Decimal]:
    """Cap use of frozen accruals (5.2.2): posted -> its amount; bank_confirmed / forfeited -> the ledger row's
    amount, 0 without a row."""
    ledger_ids = [r.ledger_entry_id for r in rows
                  if r.state in ("bank_confirmed", "forfeited") and r.ledger_entry_id is not None]
    ledger = dict(db.execute(
        select(LedgerEntry.id, LedgerEntry.amount).where(LedgerEntry.id.in_(ledger_ids))
    ).all()) if ledger_ids else {}
    out = {}
    for r in rows:
        if r.state == "posted":
            out[r.id] = Decimal(r.amount)
        elif r.state in ("bank_confirmed", "forfeited"):
            out[r.id] = Decimal(ledger.get(r.ledger_entry_id, _ZERO)) if r.ledger_entry_id else _ZERO
    return out


def _external_used(db: Session, rule_ids: list[int], start: date, end: date) -> Decimal:
    """No-accrual (MOZE era) reward rows of the group whose source purchase is in the window (5.3)."""
    source = LedgerEntry.__table__.alias("source")
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(source, source.c.id == LedgerEntry.reward_source_entry_id)
        .where(
            LedgerEntry.reward_rule_id.in_(rule_ids),
            source.c.entry_date.between(start, end),
            ~exists().where(RewardAccrual.ledger_entry_id == LedgerEntry.id),
        )
    )
    return Decimal(total)


def recompute_window(db: Session, rule_id: int, window_start: date, *, locked: bool = False) -> None:
    """Allot the cap of one window of a rule's shared-cap group to its estimated accruals (5.2).

    `locked=False` locks the window's accruals itself (`lock_windows`); `locked=True` takes no locks, the caller
    already holds them (P12)."""
    if locked:
        assert db.in_transaction(), "locked=True needs the caller's transaction holding the window locks"
    rule = db.get(RewardRule, rule_id)
    if rule is None:
        return
    group = reward_rule_service.group_rule_ids(db, rule)
    rows = _window_rows(db, [(rule_id, window_start)], lock=not locked)
    estimated = [r for r in rows if r.state == "estimated"]
    if not estimated:
        return  # frozen amounts are never rewritten; no estimated row, no adjustment (5.2.4)
    window_end = max(r.window_end for r in estimated)
    dates = dict(db.execute(
        select(LedgerEntry.id, LedgerEntry.entry_date)
        .where(LedgerEntry.id.in_(sorted({r.source_entry_id for r in rows if r.source_entry_id is not None})))
    ).all())
    frozen = _frozen_amounts(db, rows)
    ordered = sorted(rows, key=lambda r: (dates.get(r.source_entry_id, date.max), r.id))
    slots = [Slot(key=r.id, estimate=Decimal(r.estimate), frozen=frozen.get(r.id)) for r in ordered]
    # The group's lowest-id rule carries the window's total_rounding, so every member computes the same split.
    lead = db.get(RewardRule, group[0])
    amounts = allot(slots, cap=reward_rule_service.group_cap(db, rule),
                    external_used=_external_used(db, group, window_start, window_end),
                    total_rounding=lead.total_rounding)
    for r in estimated:
        value = amounts[r.id].quantize(_Q)
        if Decimal(r.amount) != value:
            r.amount = value
    db.flush()


def _recompute_all(db: Session, pairs: Iterable[Pair]) -> None:
    for rule_id, start in sorted(set(pairs)):
        recompute_window(db, rule_id, start, locked=True)


def _set(row: RewardAccrual, window, post_on, value: Decimal) -> None:
    row.window_start, row.window_end = window
    row.post_on = post_on
    row.estimate = value


def sync_entry(db: Session, entry_id: int) -> None:
    """Create, re-bucket or delete `entry_id`'s estimated accruals to match its rule links (a refund: its
    purchase's links, in the purchase's window), then recompute every window touched, old and new. Frozen
    accruals are never changed. A purchase then re-syncs its refunds."""
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return
    desired = _desired(db, entry)
    current = _accruals_of(db, [entry_id])
    pairs = {(a.rule_id, a.window_start) for a in current}
    pairs |= {(rule_id, target[0][0]) for rule_id, target in desired.items()}
    locked = {a.id: a for a in lock_windows(db, pairs) if a.source_entry_id == entry_id}
    current = [locked.get(a.id, a) for a in current]
    have = set()
    for accrual in current:
        have.add(accrual.rule_id)
        if accrual.state in FROZEN:
            continue
        if accrual.rule_id not in desired:
            db.delete(accrual)
        else:
            _set(accrual, *desired[accrual.rule_id])
    for rule_id, (window, post_on, value) in desired.items():
        if rule_id in have:
            continue
        db.add(RewardAccrual(source_entry_id=entry_id, rule_id=rule_id, window_start=window[0],
                             window_end=window[1], post_on=post_on, estimate=value, amount=value))
    db.flush()
    _recompute_all(db, pairs)
    if not _is_refund(entry):
        refunds = db.scalars(
            select(LedgerEntry.id).where(LedgerEntry.refunds_entry_id == entry_id).order_by(LedgerEntry.id))
        for refund_id in list(refunds):
            sync_entry(db, refund_id)


def _accruals_of(db: Session, entry_ids: Iterable[int]) -> list[RewardAccrual]:
    ids = list(entry_ids)
    if not ids:
        return []
    return list(db.scalars(
        select(RewardAccrual).where(RewardAccrual.source_entry_id.in_(ids)).order_by(RewardAccrual.id)
        .execution_options(populate_existing=True)
    ))


def recompute_rules(db: Session, rule_ids: Iterable[int]) -> None:
    """After a rule or card write: re-bucket and re-estimate every estimated accrual of these rules, then
    recompute their old and new windows. Frozen accruals keep their window and amount (5.1)."""
    ids = sorted(set(rule_ids))
    if not ids:
        return
    estimated = list(db.scalars(
        select(RewardAccrual)
        .where(RewardAccrual.rule_id.in_(ids), RewardAccrual.state == "estimated")
        .order_by(RewardAccrual.id)
    ))
    plans: dict[int, tuple] = {}
    for accrual in estimated:
        if accrual.source_entry_id is None:
            continue
        entry = db.get(LedgerEntry, accrual.source_entry_id)
        purchase = _purchase_of(db, entry) if entry is not None else None
        if purchase is None:
            continue
        plans[accrual.id] = _target(db, db.get(RewardRule, accrual.rule_id), entry, purchase)
    pairs = {(a.rule_id, a.window_start) for a in estimated}
    pairs |= {(a.rule_id, plans[a.id][0][0]) for a in estimated if a.id in plans}
    rows = {a.id: a for a in lock_windows(db, pairs)}
    for accrual_id, plan in plans.items():
        row = rows.get(accrual_id)
        if row is not None and row.state == "estimated":
            _set(row, *plan)
    db.flush()
    _recompute_all(db, pairs)


def recompute_account(db: Session, account_id: int) -> None:
    """`recompute_rules` over the card's own rules (a closing_day change). Children whose closing day resolves
    through this card are the caller's to name."""
    recompute_rules(db, db.scalars(select(RewardRule.id).where(RewardRule.account_id == account_id)))


def delete_estimated_for(db: Session, entry_ids: Iterable[int]) -> set[Pair]:
    """Delete the estimated accruals of these source entries (their windows locked first, P12) and return the
    (rule_id, window_start) pairs to recompute once the entries are gone. Frozen accruals stay."""
    current = _accruals_of(db, entry_ids)
    pairs = {(a.rule_id, a.window_start) for a in current if a.state == "estimated"}
    if not pairs:
        return set()
    ids = {a.id for a in current}
    doomed = [a.id for a in lock_windows(db, pairs) if a.id in ids and a.state == "estimated"]
    if doomed:
        db.execute(delete(RewardAccrual).where(RewardAccrual.id.in_(doomed)).execution_options(
            synchronize_session="fetch"))
    db.flush()
    return pairs
