"""Statement confirm (R1c; spec §7.1, rulings 11, 29): one transaction, never committed here (the route commits).

`confirm_statement` lock order: the shared import key → DISCOVER, without locks, the earlier statements holding a
pending deferral linked to this one → those statements and this one FOR UPDATE in ascending id order, each swept
(`engine_locks.lock_statements`: only the first sweep may wait at the writer barrier, as no row lock is held yet;
a later statement's sweep may already hold rows an earlier sweep wrote, so it try-locks, 409 `sweep_barrier_busy`
when busy) → the link set re-read under the locks, its case rows FOR UPDATE (a link that appeared meanwhile on a
statement not locked here → 409 `confirm_retry`; linkers serialize with confirm through the target's statement row,
ruling 29, so no link can land here once this statement is locked) → ruling 11 per linked case → counts-only
recounts → stale proposals superseded → the checks → `reconciled` → audit.

Ruling 11: a pending incoming deferral (an earlier statement's resolved `deferred_next_period` case linked here and
not yet processed) whose entry this statement claims stays resolved and gets `context.resolved_in_statement_id`;
one still unclaimed reopens on the earlier statement (`open`, version+1, explanation cleared,
`context.reopened_by_confirm_of`), audited `deferral_reopened`. Incoming deferrals are never a refusal reason, and
their processing never sets `needs_recheck`. A refusal (409 `confirm_refused`, the checks as JSON in the detail)
rolls the deferral processing back with everything else."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, ReconciliationCase, ReconciliationProposal, StatementCoverage, StatementRevision,
)
from app.services import coverage_service, proposal_service
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError, ConflictError, NotFoundError
from app.services.reconciliation_actions import audit
from app.services.reconciliation_actions import engine_locks as locks
from app.services.schedule_locks import take_import_key_shared

RESOLVED_IN = "resolved_in_statement_id"
REOPENED_BY = "reopened_by_confirm_of"


@dataclass
class ConfirmResult:
    status: str
    checks: dict[str, bool]
    reopened_cases: list[int] = field(default_factory=list)
    forced: bool = False


def _incoming_conditions(statement_id: int) -> list:
    """Pending incoming deferrals (ruling 29's `incoming_deferrals_pending`): linked here, still the resolved
    `deferred_next_period` explanation, not processed by a confirm yet."""
    case = ReconciliationCase
    return [case.deferred_to_statement_id == statement_id, case.kind == "entry_unmatched",
            case.status == "resolved", case.explanation == rec.DEFERRED,
            case.context.op("?")(RESOLVED_IN).is_(False)]


def _discover(db: Session, statement_id: int) -> list[int]:
    """The earlier statements holding pending deferrals linked to `statement_id` (read without locks)."""
    return sorted(set(db.execute(select(ReconciliationCase.statement_id)
                                 .where(*_incoming_conditions(statement_id))).scalars()))


def _incoming(db: Session, statement_id: int) -> list[ReconciliationCase]:
    """The pending incoming deferral cases, re-read FOR UPDATE (the caller holds every statement lock first)."""
    return list(db.execute(select(ReconciliationCase).where(*_incoming_conditions(statement_id))
                           .order_by(ReconciliationCase.id).with_for_update()
                           .execution_options(populate_existing=True)).scalars())


def _resolve_incoming(db: Session, statement: AccountStatement, cases: list[ReconciliationCase], *, label: str,
                      request_id: str | None) -> list[int]:
    """Ruling 11 for each linked case (module docstring); returns the ids of the reopened cases."""
    entry_ids = sorted({c.entry_id for c in cases if c.entry_id is not None})
    claimed = set(db.execute(select(StatementCoverage.entry_id).where(
        StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active",
        StatementCoverage.entry_id.in_(entry_ids))).scalars()) if entry_ids else set()
    reopened: list[int] = []
    for case in cases:
        if case.entry_id is not None and case.entry_id in claimed:
            case.context = {**(case.context or {}), RESOLVED_IN: statement.id}
            continue
        case.status, case.version, case.explanation = "open", case.version + 1, None
        case.resolved_by = case.resolved_action_id = case.resolved_at = None
        case.context = {**(case.context or {}), REOPENED_BY: statement.id}
        reopened.append(case.id)
        audit.record(db, event="deferral_reopened", actor=label, statement_id=case.statement_id, case_id=case.id,
                     request_id=request_id, detail={"confirmed_statement_id": statement.id,
                                                    "entry_id": case.entry_id})
    db.flush()
    return reopened


def _supersede_stale_proposals(db: Session, statement: AccountStatement) -> None:
    case_ids = db.execute(select(ReconciliationProposal.case_id.distinct())
                          .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id)
                          .where(ReconciliationCase.statement_id == statement.id,
                                 ReconciliationProposal.status == "pending")).scalars().all()
    for case_id in sorted(case_ids):
        proposal_service.supersede_stale(db, case_id=case_id)


def _conserved(db: Session, statement: AccountStatement) -> bool:
    try:
        coverage_service.assert_conserved(db, statement)
    except ConflictError:
        return False
    return True


def _checks(db: Session, statement: AccountStatement) -> dict[str, bool]:
    current = db.get(StatementRevision, statement.current_revision_id) \
        if statement.current_revision_id is not None else None
    open_cases = db.execute(select(func.count()).select_from(ReconciliationCase).where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.status.in_(rec.OPEN))).scalar_one()
    pending = db.execute(select(func.count()).select_from(ReconciliationProposal)
                         .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id)
                         .where(ReconciliationCase.statement_id == statement.id,
                                ReconciliationProposal.status == "pending")).scalar_one()
    return {
        "current_revision": current is not None,
        "guardrail_ok": current is not None and bool(current.guardrail_ok),
        "conflict_open": not statement.conflict_open,
        "needs_recheck": not statement.needs_recheck,
        "open_cases": open_cases == 0,
        "pending_proposals": pending == 0,
        "conserved": _conserved(db, statement),
    }


def confirm_statement(db: Session, *, account_id: int, statement_id: int, force: bool, note: str | None,
                      label: str, request_id: str | None) -> ConfirmResult:
    """Confirm (reconcile) one statement (module docstring for the steps). Each check is True when it passes:
    `current_revision`, `guardrail_ok` (of the current revision), `conflict_open` (no open conflict),
    `needs_recheck` (the flag is clear), `open_cases` (none open or proposed), `pending_proposals` (none after the
    stale ones are superseded), `conserved`. Any False without `force` → 409 `confirm_refused`; `force` needs a
    non-blank `note` (422 `note_required`) and confirms anyway (`forced` when a check failed). Confirming a
    reconciled statement again is allowed (it processes links that arrived since). Raises NotFoundError (the
    statement is not the account's), ImportRunningError, CodedConflictError (`confirm_retry`, `confirm_refused`,
    `sweep_barrier_busy`). Never commits."""
    if force and not (note and note.strip()):
        raise CodedValidationError("note", "note_required")
    statement = db.get(AccountStatement, statement_id)
    if statement is None or statement.account_id != account_id:
        raise NotFoundError(f"statement {statement_id} not found")
    take_import_key_shared(db)
    earlier = _discover(db, statement_id)
    locked = locks.lock_statements(db, [statement_id, *earlier], run_id=None)
    statement = locked[statement_id]
    if statement.account_id != account_id:  # re-read under the lock
        raise NotFoundError(f"statement {statement_id} not found")
    cases = _incoming(db, statement_id)
    unlocked = sorted({c.statement_id for c in cases} - set(locked))
    if unlocked:
        raise CodedConflictError("confirm_retry", f"a deferral of statement {unlocked[0]} was linked meanwhile")
    reopened = _resolve_incoming(db, statement, cases, label=label, request_id=request_id)
    for other in locked.values():
        rec._recount(db, other)  # counts only (ruling 29)
    db.flush()  # supersede_stale may expire the session: nothing pending may be lost
    _supersede_stale_proposals(db, statement)
    rec._recount(db, statement)
    checks = _checks(db, statement)
    failed = not all(checks.values())
    if failed and not force:
        raise CodedConflictError("confirm_refused", json.dumps(checks, sort_keys=True))
    statement.status = "reconciled"
    rec._recount(db, statement)
    forced = force and failed
    detail = {"checks": checks, "reopened_cases": reopened}
    if note:
        detail["note"] = note
    audit.record(db, event="statement_confirmed_forced" if forced else "statement_confirmed", actor=label,
                 statement_id=statement.id, request_id=request_id, detail=detail)
    db.flush()
    return ConfirmResult(status=statement.status, checks=checks, reopened_cases=reopened, forced=forced)
