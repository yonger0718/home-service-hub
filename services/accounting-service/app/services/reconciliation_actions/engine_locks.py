"""The action engine's lock steps (spec §7.4, D32, R1b errata; rulings 15, 22, 26): statements ascending with their
sweeps, then — after the case checks — the account row and the writer barrier when asked, then schedule rows,
groups and entries NOWAIT. Ledger writers lock an entry first and then need coverage/case rows the action already
holds (ON DELETE SET NULL), so every row lock after the statement is NOWAIT: a held row raises 409 `action_busy`
instead of closing a cycle."""
from __future__ import annotations

from typing import Iterable

from psycopg2.errors import LockNotAvailable
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase
from app.services import coverage_service
from app.services import reconciliation_service as rec
from app.services.entry_write_service import lock_group
from app.services.errors import CodedConflictError, NotFoundError
from app.services.schedule_locks import lock_definition, lock_instance

from .base import LockSet

ACTION_BUSY = "action_busy"


def lock_statements(db: Session, statement_ids: Iterable[int], *, run_id: int | None) -> dict[int, AccountStatement]:
    """Every statement FOR UPDATE in ascending id order, then `coverage_service.sweep` on each (the sweep needs the
    statement lock and must run before any group/entry lock)."""
    locked = {sid: rec._lock_statement(db, sid) for sid in sorted(set(statement_ids))}
    db.flush()  # the sweep runs on a clean session
    for statement in locked.values():
        coverage_service.sweep(db, statement, run_id=run_id, barrier_wait=True)
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


def lock_account_and_barrier(db: Session, targets: LockSet, statement: AccountStatement) -> None:
    """`balance_adjustment_asof` only (that action locks no entry or group): the statement's account row FOR
    UPDATE, then the exclusive writer barrier until the transaction ends (409 `sweep_barrier_busy` on timeout)."""
    if targets.account_lock:
        db.execute(select(Account).where(Account.id == statement.account_id).with_for_update()
                   .execution_options(populate_existing=True))
    if targets.barrier:
        coverage_service.take_barrier_xact(db)


def lock_rows(db: Session, targets: LockSet, expected: dict) -> dict[int, LedgerEntry]:
    """D32 after the statements: schedule definitions (FOR SHARE: the action acts on posted rows) and instances
    (FOR UPDATE), ascending; groups ascending; then the entries — the targets, every row of the expected snapshot,
    their children and every top-level leg of their transfers — in ONE ordered SELECT … FOR UPDATE. All NOWAIT,
    inside a savepoint: `LockNotAvailable` → 409 `action_busy` (the caller's attempt savepoint rolls back the
    rest)."""
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
            for definition_id in sorted(set(targets.definition_ids)):
                lock_definition(db, definition_id, share=True, nowait=True)
            for instance_id in sorted(set(targets.instance_ids)):
                lock_instance(db, instance_id, nowait=True)
            for group_id in group_ids:
                lock_group(db, group_id, nowait=True)
            return rec._lock_entries(db, entry_ids, transfers)
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(ACTION_BUSY, "a row the action needs is locked by another write") from exc
        raise
