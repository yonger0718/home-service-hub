"""Reward posting: one estimated accrual becomes a reward ledger row (spec 4.4, 5.1, 6; plan P11, P12).

The per-accrual step, shared by the daily job (`post_due_accrual`), the manual endpoint (`post_manual`) and bank
wins (Task 8, `post_accrual(..., locked=True)`):
1. read the accrual's (rule, window, source purchase) unlocked; lock the source purchase `SELECT ... FOR UPDATE`
   (P12 tier 1: the reward row's FK check share-locks it, and an owner PUT holds it before it waits on the window),
   then re-sync that source (`reward_engine.sync_entry`), whose ONE `SELECT ... ORDER BY id FOR UPDATE` takes the
   accrual's windows (each rule's whole shared-cap group, P12 tier 2) and re-derives its window, post_on and
   estimate from the committed rule and purchase; or, with `locked=True`, assert the caller already holds the window
   (and the purchase: bank wins locks it first), take no lock and re-derive in place (`reward_engine.rederive`);
2. re-read the accrual under the lock and recompute the window (`recompute_window(locked=True)`), so the amount
   that freezes is the current allotment of a current estimate (`_lock` says when a moved accrual is skipped);
3. skip unless the accrual is still `estimated` with no reward row;
4. an allotment of 0 (past the cap) is `forfeited` without a row; otherwise insert the reward row (P11) and set
   `posted` and `ledger_entry_id`. A post `on` a given date records it as the accrual's `post_on` (a manual post,
   an accepted early credit), so bank wins finds the row on the statement whose period holds that date.
The purchase row is locked (unlocked path) and read (its project), never updated. Nothing here commits.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ..models import Account, LedgerEntry, RewardAccrual, RewardRule
from . import ledger_service, reward_engine
from .entry_write_service import system_category_id
from .errors import CodedConflictError, NotFoundError

POSTED, FORFEITED, SKIPPED = "posted", "forfeited", "skipped"
RESULTS = (POSTED, FORFEITED, SKIPPED)
ACCRUAL_NOT_ESTIMATED = "accrual_not_estimated"
RULE_NOT_MANUAL = "rule_not_manual"
SOURCE = "rule"  # P11: an existing ENTRY_SOURCES value; "has an accrual" is what marks an engine row
_Q = Decimal("0.0001")
_NAME_LENGTH = 128  # ledger_entry.name


def _window_of(db: Session, accrual_id: int) -> tuple[int, date, int | None]:
    found = db.execute(
        select(RewardAccrual.rule_id, RewardAccrual.window_start, RewardAccrual.source_entry_id)
        .where(RewardAccrual.id == accrual_id)
    ).first()
    if found is None:
        raise NotFoundError(f"reward accrual {accrual_id} not found")
    return found.rule_id, found.window_start, found.source_entry_id


def _place(accrual: RewardAccrual) -> tuple:
    return accrual.rule_id, accrual.window_start, accrual.window_end, accrual.post_on


def _lock(db: Session, accrual_id: int, *, locked: bool, moved_ok: bool = False) -> RewardAccrual | None:
    """Steps 1-2: the accrual, reloaded under its window lock, re-derived from its committed rule and source, with
    the window recomputed. None when it is gone or not postable as it stands.

    Re-derivation (PR #290 re-check P1-A: an estimate priced on a rule a concurrent rule PUT was rewriting, when the
    window held no row for the PUT to lock, must not freeze):
    - unlocked (the job, the manual endpoint): the source row FOR UPDATE (tier 1), then `sync_entry` of that source,
      whose ONE `lock_windows` takes the accrual's current and re-derived windows (tier 2) and rewrites its window,
      post_on and estimate. When that moved the accrual (another window or post_on) it is not posted now
      (`moved_ok=False`, the job: the re-sync is kept, the next run posts it on its re-derived date); the manual
      post (`moved_ok=True`) books it, its new window being held too.
    - locked (bank wins, which holds the source and the window): `reward_engine.rederive`, no lock; an accrual
      whose re-derived window or post_on differs is not posted (its new window is not held); the next job run or
      write re-syncs it."""
    db.flush()
    rule_id, window_start, source_id = _window_of(db, accrual_id)
    if locked:
        reward_engine.require_window_locked(db, rule_id, window_start)
        accrual = db.get(RewardAccrual, accrual_id, populate_existing=True)
        if accrual is None or (accrual.rule_id, accrual.window_start) != (rule_id, window_start):
            return None
        if not reward_engine.rederive(db, accrual):
            return None
    elif source_id is not None:  # P12 tier 1 before the window (tier 2)
        db.execute(select(LedgerEntry.id).where(LedgerEntry.id == source_id).with_for_update())
        before = db.get(RewardAccrual, accrual_id, populate_existing=True)
        if before is None or before.source_entry_id != source_id:
            return None
        place = _place(before)
        reward_engine.sync_entry(db, source_id)
        accrual = db.get(RewardAccrual, accrual_id, populate_existing=True)
        if accrual is None or (_place(accrual) != place and not moved_ok):
            return None
    else:
        rows = reward_engine.lock_windows(db, [(rule_id, window_start)])
        accrual = next((row for row in rows if row.id == accrual_id), None)
        if accrual is None or (accrual.rule_id, accrual.window_start) != (rule_id, window_start):
            return None
    reward_engine.recompute_window(db, accrual.rule_id, accrual.window_start, locked=True)
    return accrual


def _postable(accrual: RewardAccrual | None) -> bool:
    return accrual is not None and accrual.state == "estimated" and accrual.ledger_entry_id is None


def _has_reward_row(db: Session, source_entry_id: int | None, rule_id: int) -> bool:
    """A reward row already booked for (purchase, rule): never a second one (5.3; the partial unique index
    `ux_ledger_entry_reward_source_rule` would refuse it anyway)."""
    if source_entry_id is None:
        return False
    return bool(db.scalar(select(exists().where(
        LedgerEntry.reward_source_entry_id == source_entry_id, LedgerEntry.reward_rule_id == rule_id))))


def _book(db: Session, accrual: RewardAccrual, on: date | None) -> str:
    """Steps 3-4 on a locked, recomputed accrual."""
    if not _postable(accrual) or _has_reward_row(db, accrual.source_entry_id, accrual.rule_id):
        return SKIPPED
    if on is not None:
        accrual.post_on = on  # the date it posted on: bank wins pairs it with that statement (spec 7.1 (a))
    amount = Decimal(accrual.amount).quantize(_Q)
    if amount == 0:
        accrual.state = FORFEITED  # no 0 rows for purchases past a cap (5.1)
        db.flush()
        return FORFEITED
    rule = db.get(RewardRule, accrual.rule_id)
    account = db.get(Account, rule.reward_account_id or rule.account_id)
    source = db.get(LedgerEntry, accrual.source_entry_id) if accrual.source_entry_id is not None else None
    project_id = rule.reward_project_id or (source.project_id if source is not None else None)  # #262
    day = on or accrual.post_on or ledger_service._today()
    row = LedgerEntry(
        account_id=account.id, currency=account.currency, kind="reward", amount=amount,
        entry_date=day, posted_date=day, category_id=system_category_id(db, "reward"), project_id=project_id,
        name=rule.name[:_NAME_LENGTH], reward_source_entry_id=accrual.source_entry_id, reward_rule_id=rule.id,
        source=SOURCE,
    )
    db.add(row)
    db.flush()
    accrual.state, accrual.ledger_entry_id = POSTED, row.id
    db.flush()
    return POSTED


def post_accrual(db: Session, accrual_id: int, *, on: date | None = None, locked: bool = False) -> str:
    """Post one accrual (5.1): `"posted"`, `"forfeited"` (allotment 0, no row) or `"skipped"` (not `estimated`,
    already has a row, or left its window under a concurrent write). The row is dated `on`, else the accrual's
    `post_on`, else today (a `manual` rule). `locked=True`: the caller holds the window (`lock_windows`, bank
    wins), so no lock is taken and RuntimeError is raised when it does not. NotFoundError for a missing id."""
    return _book(db, _lock(db, accrual_id, locked=locked, moved_ok=not locked), on)


def post_due_accrual(db: Session, accrual_id: int, today: date) -> str:
    """The daily job's step: `post_accrual` on the accrual's `post_on`, re-checking under the lock that it is still
    due (`post_on <= today`); one that a concurrent rule write moved later is skipped."""
    accrual = _lock(db, accrual_id, locked=False)
    if accrual is None or accrual.post_on is None or accrual.post_on > today:
        return SKIPPED
    return _book(db, accrual, None)


def post_manual(db: Session, accrual_id: int, on: date | None) -> dict:
    """POST /reward-accruals/{id}/post (6): a `manual` rule's accrual, on `on` or today. 404 missing; 409
    `rule_not_manual` for any other posting; 409 `accrual_not_estimated` when it is no longer postable."""
    accrual = db.get(RewardAccrual, accrual_id)
    if accrual is None:
        raise NotFoundError(f"reward accrual {accrual_id} not found")
    if accrual.state != "estimated" or accrual.ledger_entry_id is not None:
        raise CodedConflictError(ACCRUAL_NOT_ESTIMATED)
    rule = db.get(RewardRule, accrual.rule_id)
    if rule.posting != "manual":
        raise CodedConflictError(RULE_NOT_MANUAL, f"rule {rule.id} posts {rule.posting}")
    result = post_accrual(db, accrual_id, on=on or ledger_service._today())
    if result == SKIPPED:
        raise CodedConflictError(ACCRUAL_NOT_ESTIMATED)
    return {"result": result, "ledger_entry_id": accrual.ledger_entry_id}
