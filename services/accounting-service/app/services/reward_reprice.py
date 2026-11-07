"""Reprice a purchase's posted reward accruals on its current figures (issue #306, reward engine spec 5.1 "Reprice").

A posted accrual is frozen (5.1): an edit of its purchase's amount, date, account or rule links never changes it and
sets the purchase's `reward_posted` review flag instead. This module is the owner's answer to that flag:

- `preview` lists every frozen accrual of the purchase with its booked amount and what it would be now;
- `apply(keep=False)` rewrites the repriceable ones (accrual `estimate` and `amount`, and the reward row's `amount`),
  re-allots the windows' estimated accruals, and clears the flag unless an accrual still needs the owner (`ATTENTION`:
  its rule unlinked, its purchase moved to another window, or a new allotment of 0 under a booked amount);
- `apply(keep=True)` only clears the flag (保持原樣, the owner's explicit answer for every row).

Repriceable: `posted` (not `bank_confirmed` or `forfeited`: the bank's figure wins, 7), its rule still linked, its
window unchanged (a purchase moved to another window is the owner's to sort out), its reward row present and still at
the accrual's amount (an owner who edited the row already decided), and a new allotment above 0. The new amount is
the window's allotment (5.2) with the purchase's repriceable accruals priced on their current estimates and every
other frozen accrual keeping its cap use (`reward_engine.window_amounts`).

Lock order (P12), apply only: the purchase `SELECT ... FOR UPDATE` (tier 1) → its rules shared → the accruals'
windows (`lock_windows`, tier 2) → the reward rows `SELECT ... FOR UPDATE` by ascending id. Flushes, never commits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LedgerEntry, RewardAccrual, RewardRule
from . import reward_engine, reward_rule_service
from .entry_write_service import assert_entry_editable, locked_entry
from .errors import NotFoundError

REPRICE, SAME, CONFIRMED, RULE_UNLINKED, WINDOW_MOVED, ROW_EDITED, ZERO = (
    "reprice", "same", "confirmed", "rule_unlinked", "window_moved", "row_edited", "zero")
# Left for the owner to sort out (delete the reward row, or 保持原樣): a reprice keeps the flag while one remains.
ATTENTION = (RULE_UNLINKED, WINDOW_MOVED, ZERO)


@dataclass
class Item:
    accrual_id: int
    rule_id: int
    rule_name: str
    reward_entry_id: int | None
    booked: Decimal
    new: Decimal | None
    status: str


@dataclass
class Plan:
    entry_id: int
    flagged: bool
    items: list[Item]
    applied: list[int] = field(default_factory=list)


def _frozen_rows(db: Session, entry_id: int) -> list[RewardAccrual]:
    """The purchase's posted and bank-confirmed accruals with a reward row (a forfeited one has nothing to fix)."""
    return list(db.scalars(
        select(RewardAccrual)
        .where(RewardAccrual.source_entry_id == entry_id, RewardAccrual.state.in_(("posted", "bank_confirmed")),
               RewardAccrual.ledger_entry_id.is_not(None))
        .order_by(RewardAccrual.id)))


def _plan(db: Session, entry: LedgerEntry, *, lock: bool) -> tuple[Plan, dict[int, Decimal], set]:
    """The items, the new estimate of each repriceable accrual, and their windows. `lock`: take the window locks
    (apply); the preview reads without them."""
    rows = _frozen_rows(db, entry.id)
    desired = reward_engine.desired_accruals(db, entry)
    names = dict(db.execute(select(RewardRule.id, RewardRule.name)
                            .where(RewardRule.id.in_({r.rule_id for r in rows}))).all()) if rows else {}
    ledger = {e.id: e for e in db.scalars(select(LedgerEntry).where(
        LedgerEntry.id.in_([r.ledger_entry_id for r in rows])))} if rows else {}
    estimates: dict[int, Decimal] = {}
    status: dict[int, str] = {}
    for row in rows:
        reward = ledger.get(row.ledger_entry_id)
        if row.state != "posted":
            status[row.id] = CONFIRMED
        elif row.rule_id not in desired:
            status[row.id] = RULE_UNLINKED
        elif desired[row.rule_id][0][0] != row.window_start:
            status[row.id] = WINDOW_MOVED
        elif reward is None or Decimal(reward.amount) != Decimal(row.amount):
            status[row.id] = ROW_EDITED
        else:
            estimates[row.id] = Decimal(desired[row.rule_id][2])
    pairs = {(r.rule_id, r.window_start) for r in rows if r.id in estimates}
    if lock and pairs:
        reward_engine.lock_windows(db, pairs)
    new: dict[int, Decimal] = {}
    for rule_id, start in sorted(pairs):
        rule = db.get(RewardRule, rule_id)
        group = reward_rule_service.group_rule_ids(db, rule)
        window = reward_engine.window_rows(db, rule_id, start)
        unfreeze = {r.id: estimates[r.id] for r in window if r.id in estimates}
        new.update({k: v for k, v in reward_engine.window_amounts(db, rule, group, start, window, unfreeze).items()
                    if k in unfreeze})
    items = []
    for row in rows:
        value = new.get(row.id)
        if row.id in estimates:
            status[row.id] = ZERO if value == 0 else SAME if value == Decimal(row.amount) else REPRICE
        # A confirmed row's booked figure is the bank's, on its reward row (7); its accrual keeps the engine's.
        reward = ledger.get(row.ledger_entry_id)
        booked = Decimal(reward.amount) if row.state != "posted" and reward is not None else Decimal(row.amount)
        items.append(Item(accrual_id=row.id, rule_id=row.rule_id, rule_name=names.get(row.rule_id, ""),
                          reward_entry_id=row.ledger_entry_id, booked=booked, new=value,
                          status=status[row.id]))
    flagged = entry.needs_review and entry.review_reason == reward_engine.REVIEW_REASON
    return Plan(entry.id, flagged, items), estimates, pairs


def preview(db: Session, entry_id: int) -> Plan:
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"entry {entry_id} not found")
    plan, _, _ = _plan(db, entry, lock=False)
    return plan


def apply(db: Session, entry_id: int, *, keep: bool) -> Plan:
    """Reprice (keep=False) or keep (keep=True) the purchase's posted accruals, then clear its `reward_posted`
    flag (a reprice keeps it while an `ATTENTION` accrual remains). Returns the plan as it stands after the write."""
    entry = locked_entry(db, entry_id)
    assert_entry_editable(db, entry)
    if not keep:
        reward_engine.share_entry_rules(db, [entry.id])
        plan, estimates, pairs = _plan(db, entry, lock=True)
        todo = {i.accrual_id: i for i in plan.items if i.status == REPRICE}
        if todo:
            rewards = {e.id: e for e in db.scalars(
                select(LedgerEntry).where(LedgerEntry.id.in_(sorted(i.reward_entry_id for i in todo.values())))
                .order_by(LedgerEntry.id).with_for_update())}
            for reward in rewards.values():
                assert_entry_editable(db, reward)
            for accrual in db.scalars(select(RewardAccrual).where(RewardAccrual.id.in_(sorted(todo)))):
                item = todo[accrual.id]
                accrual.estimate, accrual.amount = estimates[accrual.id], item.new
                rewards[item.reward_entry_id].amount = item.new
            db.flush()
            reward_engine.recompute_locked(db, pairs)  # the other estimated accruals get the cap that is left
        applied = sorted(todo)
    else:
        applied = []
    plan, _, _ = _plan(db, entry, lock=False)
    if keep or not any(item.status in ATTENTION for item in plan.items):
        reward_engine.clear_reward_flag(db, [entry.id])
        db.flush()
        db.refresh(entry)
        plan.flagged = entry.needs_review and entry.review_reason == reward_engine.REVIEW_REASON
    plan.applied = applied
    return plan
