"""The reconciliation action engine (R1c; spec §4.9, §7.4, C6; rulings 6, 7, 8, 14, 15, 18, 22, 24, 25, 27, 29): one
transaction per action, never committed here (the router commits; policy commits per candidate).

`apply_action`: registry lookup and params → idempotency key from the case version → replay of a stored row →
`prepare` without locks (the C6 expected snapshot; FX on its own session) → inside one attempt savepoint: the shared
import key → [policy budget row] → statements ascending, each swept → idempotency again under the lock → the case
FOR UPDATE → [the proposal FOR UPDATE: pending, written at this case version] (version, status, allowed, historical, precheck, the action's `check_before_locks`) → [account row,
writer barrier] → schedule rows, groups, entries NOWAIT → `before` = the expected ids re-read (any difference: 409
`snapshot_conflict`) → `validate_before` → creation-family check → the action row (status `applied`, flushed first:
the partial unique index refuses a duplicate effect before any ledger write) → the `app.reconciliation_action_id`
GUC → `apply` → conservation → `after`/`inverse`/ids → case resolution or context → other pending proposals
superseded → audit → counts. Any failure rolls the attempt savepoint back (row locks included) and nothing is
written.

`revert_action` takes the same locks for the action's recorded rows, refuses when a later action touched them,
when they differ from the action's `after` (409) or when its coverage is gone (422), then inverts, releases the
action's coverage, marks it `reverted` and reopens its case at a new version (so a re-apply computes a new key)."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Callable

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementCoverage,
)
from app.services import coverage_service, settings_service
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError, NotFoundError, ValidationError
from app.services.schedule_locks import take_import_key_shared

from . import ACTIONS, COVERAGE_ACTIONS, OPEN_STATUSES, allowed, audit, snapshots
from . import engine_locks as locks
from .base import Action, ActionParams, Context, Effect, LockSet, NotRevertable, current_line

__all__ = ["NotRevertable", "apply_action", "canonical_params", "idempotency_key", "revert_action"]

EFFECT_INDEX = "ux_reconciliation_action_effect"
KEY_INDEX = "uq_reconciliation_action_idempotency"
CREATE_SLOT = "create"
SNAPSHOT_CONFLICT = "snapshot_conflict"


# --- keys ----------------------------------------------------------------------------------------------------------

def canonical_params(params: ActionParams) -> str:
    """The validated params as canonical JSON: sorted keys, no spaces, Decimals as strings, defaults included."""
    return json.dumps(params.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def idempotency_key(case_id: int, case_version: int, action: str, canonical: str) -> str:
    return hashlib.sha256(f"{case_id}|{case_version}|{action}|{canonical}".encode()).hexdigest()


def revert_key(action_id: int) -> str:
    return hashlib.sha256(f"revert|{action_id}".encode()).hexdigest()


# --- shared steps --------------------------------------------------------------------------------------------------

def _lookup(action: str) -> Action:
    impl = ACTIONS.get(action)
    if impl is None:
        raise ValidationError("action", "unknown action")
    return impl


def _parse(impl: Action, params: dict | None) -> ActionParams:
    try:
        return impl.Params(**(params or {}))
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "params"
        raise ValidationError("params", f"{where}: {first.get('msg')}") from exc
    except TypeError as exc:  # params not a mapping of names
        raise ValidationError("params", str(exc)) from exc


def _context(db: Session, case: ReconciliationCase, statement: AccountStatement, *, actor: str, label: str,
             run_id: int | None, request_id: str | None, proposal_id: int | None = None) -> Context:
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    return Context(db=db, case=case, statement=statement, account=db.get(Account, statement.account_id),
                   line=current_line(db, case, statement), actor=actor, label=label, run_id=run_id,
                   request_id=request_id, rules=rules, proposal_id=proposal_id)


def _by_key(db: Session, key: str) -> ReconciliationAction | None:
    return db.execute(select(ReconciliationAction).where(ReconciliationAction.idempotency_key == key)
                      .execution_options(populate_existing=True)).scalar_one_or_none()


def _replay(db: Session, row: ReconciliationAction, statement_id: int, *, label: str, run_id, request_id):
    """Ruling 6: an applied row replays (no new effect, audited); a reverted one is 409 `action_reverted`, a failed
    one (never written in R1c) 409 `action_failed`."""
    if row.status == "reverted":
        raise CodedConflictError("action_reverted", f"action {row.id}")
    if row.status != "applied":
        raise CodedConflictError("action_failed", f"action {row.id}")
    audit.record(db, event="action_replayed", actor=label, statement_id=statement_id, case_id=row.case_id,
                 action_id=row.id, run_id=run_id, request_id=request_id, detail={"action": row.action})
    return row, True


def _reserve_budget(db: Session, month: date, currency: str, amount: Decimal) -> None:
    """Policy budget reservation (ruling 10/22): the hook Task 10 implements (INSERT … ON CONFLICT DO NOTHING, then
    the row FOR UPDATE, before the statement lock)."""
    raise NotImplementedError("policy budget reservation lands with the policy engine (Task 10)")


def _check_snapshots(expected: dict, current: dict) -> None:
    paths = snapshots.diff(expected, current)
    if paths:
        raise CodedConflictError(SNAPSHOT_CONFLICT, ", ".join(paths[:5]))


def _insert(db: Session, row: ReconciliationAction, *, indexes: tuple[str, ...],
            duplicate: Callable[[], Exception]) -> ReconciliationAction:
    """Ruling 6: the row goes in first (status `applied`), so a unique index named in `indexes` refuses a duplicate
    before any ledger write; the savepoint keeps the session usable for the caller's rollback."""
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        if any(index in str(exc.orig) for index in indexes):
            raise duplicate() from exc
        raise
    return row


def _set_guc(db: Session, action_id: int) -> None:
    """Every ledger write after this, in this transaction, is tagged with the action (the sweep skips its events)."""
    db.execute(text("SELECT set_config('app.reconciliation_action_id', :id, true)"), {"id": str(action_id)})


def _coverage_entry_ids(db: Session, coverage_ids: list[int]) -> set[int]:
    if not coverage_ids:
        return set()
    return set(db.execute(select(StatementCoverage.entry_id).where(StatementCoverage.id.in_(coverage_ids),
                                                                   StatementCoverage.entry_id.is_not(None))).scalars())


def _after(db: Session, entry_ids, group_ids, coverage_ids: list[int], extra: dict | None = None) -> dict:
    return {**snapshots.snapshot_set(db, entry_ids, group_ids), **(extra or {}), "coverage_ids": sorted(coverage_ids)}


def _finish(db: Session, statements: dict[int, AccountStatement]) -> None:
    for statement in statements.values():
        coverage_service.assert_conserved(db, statement)


def _recount(db: Session, statements: dict[int, AccountStatement]) -> None:
    for statement in statements.values():
        rec._recount(db, statement)  # counts only (ruling 29): needs_recheck stays as the action left it


# --- apply ---------------------------------------------------------------------------------------------------------

def apply_action(db: Session, *, case_id: int, action: str, params: dict, actor: str, label: str,
                 proposal_id: int | None = None, run_id: int | None = None, request_id: str | None = None,
                 budget: tuple[date, str, Decimal] | None = None,
                 precheck: Callable[[Context], None] | None = None) -> tuple[ReconciliationAction, bool]:
    """Apply `action` with `params` to case `case_id` (module docstring for the steps); returns (row, replayed).
    Raises ValidationError 422 (`unknown action`, params, actor, not allowed, `historical_statement`,
    `duplicate_effect`, the action's own codes), CodedConflictError 409 (`action_reverted`, `action_failed`,
    `case_moved`, `case_closed`, `action_busy`, `snapshot_conflict`, `sweep_barrier_busy`, `reconcile_busy`),
    NotFoundError. Never commits; on any error nothing is written."""
    impl = _lookup(action)
    parsed = _parse(impl, params)
    if actor not in impl.actors:
        raise ValidationError("actor", f"{action} cannot be applied by {actor}")
    case = db.get(ReconciliationCase, case_id)
    if case is None:
        raise NotFoundError(f"case {case_id} not found")
    canonical = canonical_params(parsed)
    key_version = case.version
    key = idempotency_key(case.id, key_version, action, canonical)
    found = _by_key(db, key)
    if found is not None:
        return _replay(db, found, case.statement_id, label=label, run_id=run_id, request_id=request_id)
    statement = db.get(AccountStatement, case.statement_id)
    ctx0 = _context(db, case, statement, actor=actor, label=label, run_id=run_id, request_id=request_id,
                    proposal_id=proposal_id)
    targets = impl.lock_targets(ctx0, parsed)  # read without locks; C6 covers what changes before the locks
    prepared = impl.prepare(ctx0, parsed)
    prepared.lock_set = targets
    with db.begin_nested():  # the attempt savepoint: any failure rolls back every write and row lock below
        take_import_key_shared(db)
        if budget is not None:
            _reserve_budget(db, *budget)
        statements = locks.lock_statements(db, [case.statement_id, *targets.statement_ids], run_id=run_id)
        found = _by_key(db, key)  # ruling 24: a concurrent identical request replays
        if found is not None:
            return _replay(db, found, case.statement_id, label=label, run_id=run_id, request_id=request_id)
        case = locks.lock_case(db, case_id)
        proposal = _lock_proposal(db, proposal_id, case) if proposal_id is not None else None
        if case.version != key_version:
            raise CodedConflictError("case_moved", f"case {case.id} is at version {case.version}")
        statement = statements[case.statement_id]
        ctx = _context(db, case, statement, actor=actor, label=label, run_id=run_id, request_id=request_id,
                       proposal_id=proposal_id)
        if case.status not in OPEN_STATUSES:
            raise CodedConflictError("case_closed", f"case {case.id} is {case.status}")
        if action not in allowed(case):
            raise ValidationError("action", "not allowed for this case")
        if statement.mode == "historical" and action not in COVERAGE_ACTIONS:
            raise CodedValidationError("action", "historical_statement")
        if precheck is not None:  # policy re-validation under the locks, after the case checks (ruling 22)
            precheck(ctx)
        impl.check_before_locks(ctx, parsed)
        locks.lock_account_and_barrier(db, targets, statement)
        locks.lock_rows(db, targets, prepared.expected)
        before = snapshots.snapshot_set(db, *snapshots.ids_of(prepared.expected))
        _check_snapshots(prepared.expected, before)
        impl.validate_before(ctx, parsed, prepared, before)
        slot = impl.effect_slot(ctx, parsed)
        _check_creation_family(db, case, slot)
        row = _insert(db, ReconciliationAction(
            idempotency_key=key, event_id=case.event_id, effect_slot=slot, case_id=case.id, proposal_id=proposal_id,
            action=action, params=json.loads(canonical), actor=actor, run_id=run_id, status="applied", before=before),
            indexes=(EFFECT_INDEX,),
            duplicate=lambda: CodedValidationError("action", "duplicate_effect", f"{action} on event {case.event_id}"))
        _set_guc(db, row.id)
        effect = Effect(before=before)
        impl.apply(ctx, parsed, prepared, effect)
        _finish(db, statements)
        _record_effect(db, row, effect)
        _resolve(case, effect, actor, row)
        _supersede_proposals(db, case, proposal_id, row, label=label, run_id=run_id, request_id=request_id)
        if proposal is not None:
            proposal.status, proposal.applied_action_id, proposal.decided_at = "applied", row.id, func.now()
            audit.record(db, event="proposal_applied", actor=label, statement_id=case.statement_id, case_id=case.id,
                         action_id=row.id, run_id=run_id, request_id=request_id, detail={"proposal_id": proposal.id})
        audit.record(db, event="action_applied", actor=label, statement_id=case.statement_id, case_id=case.id,
                     action_id=row.id, run_id=run_id, request_id=request_id,
                     detail={"action": action, "actor": actor, "params": row.params, "effect_slot": slot,
                             "created_entry_ids": row.created_entry_ids, "deleted_entry_ids": row.deleted_entry_ids,
                             "touched_entry_ids": row.touched_entry_ids,
                             "touched_group_ids": row.touched_group_ids, "coverage_ids": row.after["coverage_ids"]})
        _recount(db, statements)
        db.flush()
        return row, False


def _check_creation_family(db: Session, case: ReconciliationCase, slot: str) -> None:
    """Ruling 18: one creation per statement event, whichever creation action made it."""
    if slot != CREATE_SLOT or case.event_id is None:
        return
    taken = db.execute(select(exists().where(ReconciliationAction.event_id == case.event_id,
                                             ReconciliationAction.effect_slot == CREATE_SLOT,
                                             ReconciliationAction.status == "applied"))).scalar_one()
    if taken:
        raise CodedValidationError("action", "duplicate_effect", f"a creation is applied on event {case.event_id}")


def _record_effect(db: Session, row: ReconciliationAction, effect: Effect) -> None:
    """Ruling 25: the ids behind the coverage rows are always touched, also for coverage-only actions."""
    touched = sorted(set(effect.touched_entry_ids) | _coverage_entry_ids(db, effect.coverage_ids))
    created, deleted = sorted(set(effect.created_entry_ids)), sorted(set(effect.deleted_entry_ids))
    groups = sorted(set(effect.touched_group_ids))
    extra = {k: v for k, v in effect.after.items() if k not in ("entries", "groups", "coverage_ids")}
    effect.after = _after(db, set(touched) | set(created), groups, effect.coverage_ids, extra)
    row.before, row.after, row.inverse = effect.before, effect.after, effect.inverse
    row.created_entry_ids, row.deleted_entry_ids = created, deleted
    row.touched_entry_ids, row.touched_group_ids = touched, groups
    db.flush()


def _resolve(case: ReconciliationCase, effect: Effect, actor: str, row: ReconciliationAction) -> None:
    if effect.resolves_case:
        case.status, case.explanation = effect.case_status, effect.explanation
        case.resolved_by, case.resolved_action_id, case.resolved_at = actor, row.id, func.now()
    else:
        case.context = {**(case.context or {}), **effect.case_context}


def _lock_proposal(db: Session, proposal_id: int, case: ReconciliationCase) -> ReconciliationProposal:
    """Ruling 27: the proposal row FOR UPDATE after the statement and case locks; it must still be pending (a reject
    or another apply committed meanwhile: 409 `proposal_not_pending`) and written against this case version (409
    `proposal_superseded`). The error rolls the attempt back, so the caller (the route) persists the superseded status
    through `proposal_service.supersede_stale`."""
    proposal = db.execute(select(ReconciliationProposal).where(ReconciliationProposal.id == proposal_id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if proposal is None or proposal.case_id != case.id:
        raise NotFoundError(f"proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise CodedConflictError("proposal_not_pending", f"proposal {proposal.id} is {proposal.status}")
    if proposal.case_version != case.version:
        raise CodedConflictError("proposal_superseded", f"proposal {proposal.id} was made at version "
                                                       f"{proposal.case_version}, the case is at {case.version}")
    return proposal


def _supersede_proposals(db: Session, case: ReconciliationCase, proposal_id: int | None, row: ReconciliationAction,
                         *, label: str, run_id, request_id) -> None:
    pending = db.execute(select(ReconciliationProposal).where(
        ReconciliationProposal.case_id == case.id, ReconciliationProposal.status == "pending",
        *([ReconciliationProposal.id != proposal_id] if proposal_id is not None else []))
        .order_by(ReconciliationProposal.id)).scalars().all()
    for proposal in pending:
        proposal.status, proposal.decided_at = "superseded", func.now()
        audit.record(db, event="proposal_superseded", actor=label, statement_id=case.statement_id, case_id=case.id,
                     action_id=row.id, run_id=run_id, request_id=request_id, detail={"proposal_id": proposal.id})


# --- revert --------------------------------------------------------------------------------------------------------

def _revertable(row: ReconciliationAction) -> Action:
    if row.action == "revert":
        raise NotRevertable("a revert cannot be reverted")
    if row.status != "applied":
        raise NotRevertable(f"action {row.id} is {row.status}")
    impl = ACTIONS.get(row.action)
    if impl is None:
        raise NotRevertable(f"unknown action {row.action}")
    return impl


def _refuse_later(db: Session, row: ReconciliationAction) -> None:
    """Ruling 14: a later applied action (not a revert) touching the same entries or groups blocks the revert."""
    entries = sorted(set(row.touched_entry_ids or ()) | set(row.created_entry_ids or ()))
    groups = sorted(set(row.touched_group_ids or ()))
    overlaps = []
    if entries:
        overlaps += [ReconciliationAction.touched_entry_ids.overlap(entries),
                     ReconciliationAction.created_entry_ids.overlap(entries)]
    if groups:
        overlaps.append(ReconciliationAction.touched_group_ids.overlap(groups))
    if not overlaps:
        return
    later = db.execute(select(ReconciliationAction.id).where(
        ReconciliationAction.id > row.id, ReconciliationAction.status == "applied",
        ReconciliationAction.action != "revert", or_(*overlaps)).order_by(ReconciliationAction.id).limit(1)
    ).scalar_one_or_none()
    if later is not None:
        raise NotRevertable(f"later action {later}")


def _coverage_still_active(db: Session, coverage_ids: list[int]) -> None:
    if not coverage_ids:
        return
    rows = db.execute(select(StatementCoverage.id, StatementCoverage.status)
                      .where(StatementCoverage.id.in_(coverage_ids))).all()
    active = {cid for cid, status in rows if status == "active"}
    gone = sorted(set(coverage_ids) - active)
    if gone:
        raise NotRevertable(f"coverage {gone[0]} was released")


def revert_action(db: Session, *, action_id: int, actor: str, label: str,
                  request_id: str | None = None) -> ReconciliationAction:
    """Revert an applied action (module docstring); returns the new `revert` row. Raises NotRevertable 422
    (`not_revertable`: a revert, already reverted, a later action, coverage gone, the action's own refusal),
    ValidationError 422 `historical_statement`, CodedConflictError 409 (`snapshot_conflict`, `action_busy`,
    `sweep_barrier_busy`), NotFoundError. Never commits; on any error nothing is written."""
    original = db.get(ReconciliationAction, action_id)
    if original is None:
        raise NotFoundError(f"action {action_id} not found")
    impl = _revertable(original)
    case = db.get(ReconciliationCase, original.case_id)
    statement = db.get(AccountStatement, case.statement_id)
    ctx0 = _context(db, case, statement, actor=actor, label=label, run_id=None, request_id=request_id)
    targets: LockSet = impl.revert_lock_targets(ctx0, original)
    with db.begin_nested():  # the attempt savepoint (as apply_action)
        take_import_key_shared(db)
        statements = locks.lock_statements(db, [case.statement_id, *targets.statement_ids], run_id=None)
        original = locks.lock_action(db, action_id)
        impl = _revertable(original)  # re-read under the lock (ruling 25)
        case = locks.lock_case(db, original.case_id)
        statement = statements[case.statement_id]
        ctx = _context(db, case, statement, actor=actor, label=label, run_id=None, request_id=request_id)
        if statement.mode == "historical" and original.action not in COVERAGE_ACTIONS:
            raise CodedValidationError("action", "historical_statement")
        locks.lock_account_and_barrier(db, targets, statement)
        after = original.after or {}
        expected = {"entries": after.get("entries", {}), "groups": after.get("groups", {})}
        locks.lock_rows(db, targets, expected)
        _refuse_later(db, original)
        current = snapshots.snapshot_set(db, *snapshots.ids_of(expected))
        _check_snapshots(expected, current)
        _coverage_still_active(db, after.get("coverage_ids", []))
        row = _insert(db, ReconciliationAction(
            idempotency_key=revert_key(action_id), event_id=original.event_id, effect_slot=f"r{action_id}",
            case_id=original.case_id, action="revert", params={"action_id": action_id}, actor=actor,
            status="applied", before=current),
            indexes=(EFFECT_INDEX, KEY_INDEX),
            duplicate=lambda: NotRevertable(f"action {action_id} is already reverted"))
        _set_guc(db, row.id)
        effect = impl.invert(ctx, original)
        coverage_service.release_rows(db, after.get("coverage_ids", []), reason="revert")
        _finish(db, statements)
        original.status, original.reverted_by_action_id = "reverted", row.id
        case.status, case.version, case.explanation = "open", case.version + 1, None
        case.resolved_by = case.resolved_action_id = case.resolved_at = None
        case.context = {**(case.context or {}), "reverted_action_id": action_id}
        entry_ids, group_ids = snapshots.ids_of(expected)
        touched = sorted(set(original.touched_entry_ids or ()) | set(original.created_entry_ids or ())
                         | set(effect.touched_entry_ids))
        groups = sorted(set(original.touched_group_ids or ()) | set(effect.touched_group_ids))
        row.after = _after(db, set(entry_ids) | set(touched) | set(effect.created_entry_ids),
                           set(group_ids) | set(groups), effect.coverage_ids)
        row.touched_entry_ids, row.touched_group_ids = touched, groups
        row.created_entry_ids, row.deleted_entry_ids = (sorted(set(effect.created_entry_ids)),
                                                        sorted(set(effect.deleted_entry_ids)))
        row.inverse = effect.inverse
        audit.record(db, event="action_reverted", actor=label, statement_id=case.statement_id, case_id=case.id,
                     action_id=row.id, request_id=request_id,
                     detail={"reverted_action_id": action_id, "action": original.action, "actor": actor,
                             "coverage_ids": row.after["coverage_ids"]})
        _recount(db, statements)
        db.flush()
        return row
