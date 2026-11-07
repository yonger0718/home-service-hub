"""Retire a duplicate statement (fix 2026-10-17; `POST /accounts/{a}/statements/{s}/retire`, admin scope).

Before the file guard (`statement_revision_service._file_statement_id`) one PDF re-parsed with another period made a
second live statement holding the same lines. Retiring such a duplicate, in one transaction (never committed here):
the shared import key → the statement FOR UPDATE and swept (`engine_locks.lock_statements`: the sweep first retires
applied actions a ledger delete left fully stranded; a busy writer barrier is 409 `sweep_barrier_busy`, retry), the
statements holding an applied `anchor_opening` that rests on it locked with it in ascending id order → refused with 409
`anchor_depends` while such an anchor stays applied, and with 409 `actions_applied` while any action on its cases is
`applied` — the `revert` rows themselves excepted: they are `applied` but carry no effect, and so is
`accept_early_reward` (#297, owner 2026-11-07), retired with the statement below (the owner reverts what the engine
can revert; a statement with an irreversible — `replace_stand_in` —, snapshot-refused or partially stranded applied
action has no supported retire path and goes to the owner / Lead; no shortcut edits the applied marker) → the reward
outcomes it decided handed back (#297 M9, `reward_bank_wins.release_for_retire`: every accrual it confirmed or
forfeited is `posted` again at its engine amount, unconfirmed, NOWAIT locks → 409 `reconcile_busy`), its applied
`accept_early_reward` actions `reverted` with `inverse.retired_with_statement` (audit `action_retired`; their
accruals keep the early posting date), the other live statements holding those posting dates flagged
`needs_recheck` (`reward_bank_wins.flag_rechecks`) → its active coverage released (`retired`) → every
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
    AccountStatement, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementAutoPay, StatementEvent,
)
from app.services import coverage_service, reward_bank_wins
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, NotFoundError
from app.services.reconciliation_actions import audit
from app.services.reconciliation_actions import engine_locks as locks
from app.services.reconciliation_actions import structural_actions as sa
from app.services.schedule_locks import take_import_key_shared
from app.services.statements import retired

OPEN = ("open", "proposed")


def _anchor_dependents(db: Session, statement: AccountStatement) -> list[tuple[int, int]]:
    """(action id, holding statement id) of the account's applied `anchor_opening` rows (on any statement's case)
    that rest on `statement`: it is in the anchor's recorded `inverse.statement_ids`, or it is the account's current
    opening target (`anchor_target`: the anchor's basis would move if it left the live set)."""
    target = sa.anchor_target(db, statement.account_id)
    is_target = target is not None and target.id == statement.id
    rows = db.execute(
        select(ReconciliationAction.id, ReconciliationCase.statement_id, ReconciliationAction.inverse)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
        .where(ReconciliationAction.action == "anchor_opening", ReconciliationAction.status == "applied",
               AccountStatement.account_id == statement.account_id)
        .order_by(ReconciliationAction.id)).all()
    return [(action_id, holder) for action_id, holder, inverse in rows
            if is_target or statement.id in ((inverse or {}).get("statement_ids") or ())]


@dataclass
class RetireResult:
    statement: AccountStatement
    already_retired: bool
    released_coverage: int = 0
    retired_events: int = 0
    superseded_cases: int = 0


RETIRED_WITH_STATEMENT = "accept_early_reward"  # applied, never revertible: retired with its statement (#297)


def _retire_accepted(db: Session, statement: AccountStatement, label: str, request_id: str | None) -> list[int]:
    """Owner ruling 2026-11-07: the statement's applied `accept_early_reward` actions are retired with it (status
    `reverted`, `inverse.retired_with_statement`, audit `action_retired`) and the cases they resolved are superseded
    (version bumped) like the statement's open ones (AGENT-420 F2); their accruals were reset by
    `reward_bank_wins.release_for_retire` (they keep the early posting date). Returns the action ids."""
    rows = db.execute(
        select(ReconciliationAction, ReconciliationCase)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied",
               ReconciliationAction.action == RETIRED_WITH_STATEMENT)
        .order_by(ReconciliationAction.id).with_for_update(of=(ReconciliationAction, ReconciliationCase))
        .execution_options(populate_existing=True)).all()
    for row, case in rows:
        if case.status == "resolved" and case.resolved_action_id == row.id:
            case.status, case.version = "superseded", case.version + 1
        row.status = "reverted"
        row.inverse = {**(row.inverse or {}), "retired_with_statement": statement.id}
        audit.record(db, event="action_retired", actor=label, statement_id=statement.id, case_id=row.case_id,
                     action_id=row.id, request_id=request_id, detail={"by": "retire", "action": row.action})
    return [row.id for row, _ in rows]


def retire_statement(db: Session, *, account_id: int, statement_id: int, note: str, label: str,
                     request_id: str | None) -> RetireResult:
    """Retire the account's statement (module docstring). Raises NotFoundError (not the account's statement),
    ImportRunningError, CodedConflictError `anchor_depends`, `actions_applied` or `sweep_barrier_busy` (the sweep
    under the lock). Never commits."""
    statement = db.get(AccountStatement, statement_id)
    if statement is None or statement.account_id != account_id:
        raise NotFoundError(f"statement {statement_id} not found")
    if retired.is_retired(db, statement.id):  # final once written: no lock, no sweep, no write
        return RetireResult(statement, already_retired=True)
    take_import_key_shared(db)
    # The statements holding an anchor that rests on this one, discovered without locks, are locked with it in
    # ascending id order (engine_locks.lock_statements, as the engine does) and the dependency is re-read under the
    # locks. Locked and swept: the sweep retires applied actions a ledger delete left fully stranded (e.g. a MOZE
    # re-import), which would otherwise refuse the retire as `actions_applied` for good.
    holders = {holder for _, holder in _anchor_dependents(db, statement)}
    statement = locks.lock_statements(db, [statement_id, *holders], run_id=None)[statement_id]
    if retired.is_retired(db, statement.id):
        return RetireResult(statement, already_retired=True)
    depends = _anchor_dependents(db, statement)
    if depends:
        raise CodedConflictError("anchor_depends", "anchor_opening actions {} on statements {} rest on statement {}"
                                 .format([a for a, _ in depends], sorted({h for _, h in depends}),
                                                            statement.id))
    applied = db.execute(  # a `revert` row is itself `applied` but has no effect of its own (engine._refuse_later)
        select(ReconciliationAction.id).join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .where(ReconciliationCase.statement_id == statement.id, ReconciliationAction.status == "applied",
               ReconciliationAction.action.not_in(("revert", RETIRED_WITH_STATEMENT)))
        .order_by(ReconciliationAction.id)).scalars().all()
    if applied:
        raise CodedConflictError("actions_applied", f"actions {applied} are applied")
    # #297 M9: the reward outcomes this statement decided go back to the engine (NOWAIT locks, reconcile_busy)
    reset = reward_bank_wins.release_for_retire(db, statement)
    accepted = _retire_accepted(db, statement, label, request_id)
    rechecked = reward_bank_wins.flag_rechecks(db, statement, reset)
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
        "unlinked_deferrals": sorted(unlinked), "reset_accruals": sorted(a.id for a in reset),
        "retired_actions": accepted, "rechecked_statements": rechecked})
    booked = db.execute(select(StatementAutoPay).where(StatementAutoPay.statement_id == statement.id,
                                                       StatementAutoPay.status == "booked")).scalar_one_or_none()
    if booked is not None:  # the pair stays (it is real money moved); only the audit trail notes it
        audit.record(db, event="auto_pay_statement_retired", actor=label, statement_id=statement.id,
                     request_id=request_id, detail={"auto_pay_id": booked.id, "out_entry_id": booked.out_entry_id,
                                                    "in_entry_id": booked.in_entry_id})
    db.flush()
    return RetireResult(statement, already_retired=False, released_coverage=released, retired_events=len(events),
                        superseded_cases=len(cases))
