"""Retire a duplicate statement (fix 2026-10-17; `POST /accounts/{a}/statements/{s}/retire`, admin scope).

Before the file guard (`statement_revision_service._file_statement_id`) one PDF re-parsed with another period made a
second live statement holding the same lines. Retiring such a duplicate, in one transaction (never committed here):
the shared import key → the statement FOR UPDATE and swept (`engine_locks.lock_statements`: the sweep first retires
applied actions a ledger delete made unrevertable) → refused with 409 `actions_applied` while any action on its cases
is `applied` (its effects belong to the owner: revert them first) → its active coverage released (`retired`) → every
non-retired event `retired` → every open/proposed case and every resolved `deferred_next_period` deferral of it
superseded (version bumped) and their pending proposals superseded → the deferrals of other statements linked to it
unlinked (`deferred_to_statement_id` NULL; the linkers never pick a retired statement as source or target, so none is
linked or reopened through it again) → `mode = historical`, the note recorded, `needs_recheck` and `conflict_open`
cleared, counts recomputed → audit `statement_retired`, which is the retired marker
(`app.services.statements.retired`). A retired statement leaves the pending selection, the reconcile pass, the policy
(not live) and the dry-run dump (live only), and no revision lands on it again. Retiring it again is a 200 no-op
(`already_retired`)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementEvent,
)
from app.services import coverage_service
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, NotFoundError
from app.services.reconciliation_actions import audit
from app.services.reconciliation_actions import engine_locks as locks
from app.services.schedule_locks import take_import_key_shared
from app.services.statements import retired

OPEN = ("open", "proposed")


@dataclass
class RetireResult:
    statement: AccountStatement
    already_retired: bool
    released_coverage: int = 0
    retired_events: int = 0
    superseded_cases: int = 0


def retire_statement(db: Session, *, account_id: int, statement_id: int, note: str, label: str,
                     request_id: str | None) -> RetireResult:
    """Retire the account's statement (module docstring). Raises NotFoundError (not the account's statement),
    ImportRunningError, CodedConflictError `actions_applied`. Never commits."""
    statement = db.get(AccountStatement, statement_id)
    if statement is None or statement.account_id != account_id:
        raise NotFoundError(f"statement {statement_id} not found")
    if retired.is_retired(db, statement.id):  # final once written: no lock, no sweep, no write
        return RetireResult(statement, already_retired=True)
    take_import_key_shared(db)
    # locked and swept: the sweep retires applied actions a ledger delete made unrevertable (e.g. a MOZE re-import),
    # which would otherwise refuse the retire as `actions_applied` for good
    statement = locks.lock_statements(db, [statement_id], run_id=None)[statement_id]
    if retired.is_retired(db, statement.id):
        return RetireResult(statement, already_retired=True)
    applied = db.execute(
        select(ReconciliationAction.id).join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied")
        .order_by(ReconciliationAction.id)).scalars().all()
    if applied:
        raise CodedConflictError("actions_applied", f"actions {applied} are applied: revert them first")
    previous_mode = statement.mode
    released = coverage_service.release_statement(db, statement, reason="retired")
    events = db.execute(
        update(StatementEvent).where(StatementEvent.statement_id == statement.id, StatementEvent.status != "retired")
        .values(status="retired").returning(StatementEvent.id).execution_options(synchronize_session="fetch")
    ).scalars().all()
    deferral = and_(ReconciliationCase.status == "resolved", ReconciliationCase.explanation == rec.DEFERRED)
    cases = db.execute(
        select(ReconciliationCase).where(ReconciliationCase.statement_id == statement.id,
                                         or_(ReconciliationCase.status.in_(OPEN), deferral))
        .order_by(ReconciliationCase.id).with_for_update().execution_options(populate_existing=True)).scalars().all()
    for case in cases:
        case.status, case.version = "superseded", case.version + 1
    db.flush()  # the proposal update and the recount below read these rows
    proposals = db.execute(
        update(ReconciliationProposal)
        .where(ReconciliationProposal.case_id.in_([c.id for c in cases]), ReconciliationProposal.status == "pending")
        .values(status="superseded", decided_at=func.now())
        .returning(ReconciliationProposal.id, ReconciliationProposal.case_id)
        .execution_options(synchronize_session="fetch")).all() if cases else []
    for proposal_id, case_id in sorted(proposals):
        audit.record(db, event="proposal_superseded", actor="system", statement_id=statement.id, case_id=case_id,
                     request_id=request_id, detail={"proposal_id": proposal_id, "by": "retire"})
    unlinked = db.execute(
        update(ReconciliationCase)
        .where(ReconciliationCase.deferred_to_statement_id == statement.id,
               ReconciliationCase.statement_id != statement.id)
        .values(deferred_to_statement_id=None).returning(ReconciliationCase.id)
        .execution_options(synchronize_session="fetch")).scalars().all()
    statement.mode = "historical"
    statement.note = note if not statement.note else f"{statement.note}\n{note}"
    statement.needs_recheck = False
    statement.conflict_open = False
    rec._recount(db, statement)
    audit.record(db, event=retired.EVENT, actor=label, statement_id=statement.id, request_id=request_id, detail={
        "note": note, "previous_mode": previous_mode, "released_coverage": released, "retired_events": sorted(events),
        "superseded_cases": [c.id for c in cases], "superseded_proposals": sorted(p for p, _ in proposals),
        "unlinked_deferrals": sorted(unlinked)})
    db.flush()
    return RetireResult(statement, already_retired=False, released_coverage=released, retired_events=len(events),
                        superseded_cases=len(cases))
