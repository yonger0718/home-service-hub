"""The action engine's lock steps (spec §7.4, D32, R1b errata; rulings 15, 22, 26): statements ascending with their
sweeps, then the action row (revert) and the case row, then — after the case checks — the account row and the
writer barrier when asked, then schedule rows, groups and entries NOWAIT. Ledger writers lock an entry first and
then need coverage/case rows the action already holds (ON DELETE SET NULL), so the schedule, group and entry locks
are NOWAIT: a held row raises 409 `action_busy` instead of closing a cycle. The case, action and account locks
wait by design: no ledger writer locks a case or action row, and the account lockers (`balance_adjustment_asof`,
`anchor_opening`, `backfill_entry`, `create_system_entry`, `pair_transfer`, `book_settlement` / `book_dividend` and
their reverts) take no entry or group lock before the barrier, so those waits cannot close a cycle. P12 barrier
pre-lock rule (PR #290 re-check at 9e1bc745): they lock EVERY account they will write or lock FOR UPDATE, in id
order, before the barrier (`lock_account_and_barrier`), so they never wait on an account row while holding it. R7 `book_settlement` takes the account row and the barrier too; the settlement booking rows and legs it then
needs (and its revert's booking row, taken after the legs) are try-locked NOWAIT (409 `settlement_busy`), never
waited for. The barrier waits at most BARRIER_LOCK_TIMEOUT (409 `sweep_barrier_busy`)."""
from __future__ import annotations

from typing import Iterable

from psycopg2.errors import LockNotAvailable
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, RewardRule
from app.services import coverage_service, reward_engine
from app.services import reconciliation_service as rec
from app.services.entry_write_service import lock_group
from app.services.errors import CodedConflictError, NotFoundError
from app.services.schedule_locks import lock_definition, lock_instance

from .base import LockSet

ACTION_BUSY = "action_busy"


def lock_statements(db: Session, statement_ids: Iterable[int], *, run_id: int | None) -> dict[int, AccountStatement]:
    """Every statement FOR UPDATE in ascending id order, then `coverage_service.sweep` on each (the sweep needs the
    statement lock and must run before any group/entry lock). Only the FIRST sweep may wait at the writer barrier:
    it holds no row a ledger writer could need yet. A later sweep may hold coverage/case rows the earlier ones wrote
    (a writer's ON DELETE SET NULL waits on them while it holds the shared barrier), so it try-locks
    (`barrier_wait=False`): a busy barrier is 409 `sweep_barrier_busy` instead of a deadlock."""
    locked = {sid: rec._lock_statement(db, sid) for sid in sorted(set(statement_ids))}
    db.flush()  # the sweep runs on a clean session
    for index, statement in enumerate(locked.values()):
        coverage_service.sweep(db, statement, run_id=run_id, barrier_wait=index == 0)
    return locked


def lock_case(db: Session, case_id: int) -> ReconciliationCase:
    case = db.execute(select(ReconciliationCase).where(ReconciliationCase.id == case_id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one_or_none()
    if case is None:
        raise NotFoundError(f"case {case_id} not found")
    return case


def lock_action(db: Session, action_id: int) -> ReconciliationAction:
    row = db.execute(select(ReconciliationAction).where(ReconciliationAction.id == action_id).with_for_update()
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"action {action_id} not found")
    return row


def barrier_accounts(db: Session, targets: LockSet, statement: AccountStatement) -> set[int]:
    """Every account row a barrier holder may write or lock (P12 barrier pre-lock rule), read unlocked: the
    statements' accounts (the action's and `targets.statement_ids`: a pair_transfer peer, anchor_opening's live
    statements) with the accounts combined into them (a reconcile covers them: `participating_accounts`), the
    accounts of the target entries (a revert's legs), `targets.account_ids` (book_settlement's target), then the
    combined parents of all of them (a card share prices under the parent's closing_day) and the reward accounts of
    their rules (an early post books its reward row there)."""
    statements = {statement.id, *targets.statement_ids}
    owners = set(db.scalars(select(AccountStatement.account_id).where(AccountStatement.id.in_(sorted(statements)))))
    ids = {statement.account_id, *targets.account_ids}
    for owner in owners | {statement.account_id}:
        ids |= set(coverage_service.participating_accounts(db, owner))
    if targets.entry_ids:
        ids |= set(db.scalars(select(LedgerEntry.account_id).where(LedgerEntry.id.in_(sorted(targets.entry_ids)))))
    ids |= set(db.scalars(select(Account.combined_account_id).where(
        Account.id.in_(sorted(ids)), Account.combined_account_id.is_not(None))))
    ids |= set(db.scalars(select(RewardRule.reward_account_id).where(
        RewardRule.account_id.in_(sorted(ids)), RewardRule.reward_account_id.is_not(None))))
    return ids


def lock_account_and_barrier(db: Session, targets: LockSet, statement: AccountStatement) -> None:
    """The account lockers (they lock no entry or group before the barrier; engine docstring), then the exclusive
    writer barrier until the transaction ends (409 `sweep_barrier_busy` on timeout).

    P12 barrier pre-lock rule (PR #290 re-check at 9e1bc745): a barrier holder must never wait on an account row
    while it holds the barrier, because every ledger / account writer (an accrual path holding a card FOR KEY SHARE,
    a card writer holding it FOR UPDATE, a balance adjustment) may itself be waiting on the barrier in the dirty
    trigger. So EVERY account it will write or lock (`barrier_accounts`) is taken FOR UPDATE, in id order, BEFORE the
    barrier: these waits happen while it does not hold the barrier yet; the set is re-derived under the locks until
    stable (a child combined in meanwhile). The FK checks of its later inserts (KEY SHARE) and the engine's card
    shares on these rows are then no-ops, and no accrual path, rule writer or card writer of these cards is in
    flight. An account found only later (book_settlement's re-read target) is locked NOWAIT (`lock_late_accounts`),
    and after the barrier every engine card / rule / window lock is NOWAIT (`reward_engine.note_barrier`)."""
    if targets.account_lock or targets.barrier:
        held: set[int] = set()
        while True:
            need = barrier_accounts(db, targets, statement) - held
            if not need:
                break
            db.execute(select(Account).where(Account.id.in_(sorted(need))).order_by(Account.id).with_for_update()
                       .execution_options(populate_existing=True)).all()
            held |= need
        reward_engine.note_cards_locked(db, held)
    if targets.barrier:
        coverage_service.take_barrier_xact(db)
        reward_engine.note_barrier(db)


def lock_late_accounts(db: Session, account_ids: Iterable[int]) -> None:
    """An account a barrier holder discovers only after the barrier (book_settlement re-reads its target): FOR
    UPDATE NOWAIT unless pre-locked, so it never waits while holding the barrier (409 `retry`)."""
    reward_engine.lock_cards_for_write(db, account_ids)


def lock_rows(db: Session, targets: LockSet, expected: dict) -> dict[int, LedgerEntry]:
    """D32 after the statements: schedule definitions (FOR SHARE: the action acts on posted rows) and instances
    (FOR UPDATE), ascending; groups ascending; then the entries — the targets, every row of the expected snapshot,
    their children and every top-level leg of their transfers — in ONE ordered SELECT … FOR UPDATE. All NOWAIT,
    inside a savepoint: `LockNotAvailable` → 409 `action_busy` (the caller's attempt savepoint rolls back the
    rest). A definition in `exclusive_definition_ids` is taken FOR UPDATE (an ordinary posting takes it FOR SHARE,
    so the posted set cannot change while it is held)."""
    entry_ids = {*targets.entry_ids, *(int(i) for i in (expected or {}).get("entries", {}))}
    group_ids = sorted({*targets.group_ids, *(int(i) for i in (expected or {}).get("groups", {}))})
    if entry_ids:
        entry_ids |= set(db.execute(select(LedgerEntry.id)
                                    .where(LedgerEntry.parent_entry_id.in_(sorted(entry_ids)))).scalars())
    transfers = set(db.execute(select(LedgerEntry.transfer_group_id).where(
        LedgerEntry.id.in_(sorted(entry_ids)), LedgerEntry.transfer_group_id.is_not(None))).scalars()) \
        if entry_ids else set()
    try:
        with db.begin_nested():
            exclusive = set(targets.exclusive_definition_ids)
            for definition_id in sorted({*targets.definition_ids, *exclusive}):
                lock_definition(db, definition_id, share=definition_id not in exclusive, nowait=True)
            for instance_id in sorted(set(targets.instance_ids)):
                lock_instance(db, instance_id, nowait=True)
            for group_id in group_ids:
                lock_group(db, group_id, nowait=True)
            return rec._lock_entries(db, entry_ids, transfers)
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(ACTION_BUSY, "a row the action needs is locked by another write") from exc
        raise
