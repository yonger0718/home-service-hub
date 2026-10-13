"""Case actions that write no ledger rows (R1c; spec §4.9, rulings 23, 28, 29, 30, 31): `dismiss` accepts a case as an
exception, `defer_next_period` explains an unmatched entry as belonging to the next statement (owner deferrals are
authoritative for the reconcile), `link_installment_plan` confirms a card installment plan and re-reconciles so the
line is claimed in the same transaction, `resolve_conflict` settles a `statement_conflict` (promote the stored
revision or keep the current one). None sets an effect slot: each is one effect per case version."""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, InstallmentPlanMap, ReconciliationAction, ReconciliationCase, ScheduleDefinition,
    ScheduleInstance, StatementCoverage, StatementRevision,
)
from app.schemas.writes import Int32
from app.services import coverage_service
from app.services import reconciliation_service as rec
from app.services import statement_revision_service as revisions
from app.services.errors import CodedConflictError, CodedValidationError, ValidationError

from . import audit, register, snapshots
from .base import ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, lockset_for
from .engine_locks import ACTION_BUSY

OPEN_AGAIN = "open"


def _reopen(case: ReconciliationCase) -> None:
    case.status, case.explanation = OPEN_AGAIN, None
    case.resolved_by = case.resolved_action_id = case.resolved_at = None


# --- dismiss ------------------------------------------------------------------------------------------------------

class DismissParams(ActionParams):
    note: str = Field(min_length=1, max_length=1000)


class DismissAction(ActionBase):
    name = "dismiss"
    Params = DismissParams
    case_kinds = frozenset({"line_unmatched", "ambiguous", "amount_delta", "entry_unmatched", "balance_gap",
                            "duplicate_claim", "recheck", "parse_review"})  # not statement_conflict (ruling 34)

    def apply(self, ctx: Context, params: DismissParams, prepared: Prepared, effect: Effect) -> None:
        ctx.case.note = params.note
        effect.case_status, effect.explanation = "dismissed", "accepted_exception"

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        case = ctx.case
        context = dict(case.context or {})
        if case.note is not None:
            context["dismiss_note"] = case.note
        case.context, case.note = context, None
        _reopen(case)
        return Effect()


# --- defer_next_period --------------------------------------------------------------------------------------------

class DeferParams(ActionParams):
    entry_id: Int32


class DeferNextPeriodAction(ActionBase):
    name = "defer_next_period"
    Params = DeferParams
    case_kinds = frozenset({"entry_unmatched"})

    def validate_before(self, ctx: Context, params: DeferParams, prepared: Prepared, before: dict) -> None:
        if ctx.case.kind != "entry_unmatched" or params.entry_id != ctx.case.entry_id:
            raise ValidationError("entry_id", "must be the case's entry")

    def apply(self, ctx: Context, params: DeferParams, prepared: Prepared, effect: Effect) -> None:
        ctx.case.deferred_to_period_end = rec.deferral_period_end(ctx.statement, ctx.account)
        # never the statement link (ruling 29): the target's reconcile (or the batch) fills it
        ctx.case.deferred_to_statement_id = None
        effect.case_status, effect.explanation = "resolved", rec.DEFERRED

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        ctx.case.deferred_to_period_end = ctx.case.deferred_to_statement_id = None
        _reopen(ctx.case)
        return Effect()


# --- link_installment_plan ----------------------------------------------------------------------------------------

class LinkParams(ActionParams):
    definition_id: Int32


class LinkInstallmentPlanAction(ActionBase):
    name = "link_installment_plan"
    Params = LinkParams
    case_kinds = frozenset({"line_unmatched"})

    @staticmethod
    def _candidates(case: ReconciliationCase) -> set[int]:
        return {c.get("definition_id") for c in (case.candidates or [])}

    def lock_targets(self, ctx: Context, params: LinkParams) -> LockSet:
        # the schedule definition is read FOR SHARE (D32); an out-of-scope id is refused before any lock
        if params.definition_id in self._candidates(ctx.case):
            return LockSet(definition_ids=(params.definition_id,))
        return LockSet()

    def validate_before(self, ctx: Context, params: LinkParams, prepared: Prepared, before: dict) -> None:
        case, db = ctx.case, ctx.db
        if params.definition_id not in self._candidates(case):
            raise CodedValidationError("definition_id", "id_out_of_scope", f"definition {params.definition_id}")
        definition = db.get(ScheduleDefinition, params.definition_id)
        if definition is None or definition.kind != "installment" or definition.status != "active":
            raise ValidationError("definition_id", "an active installment definition is required")
        if self._mapped(ctx) is not None:
            raise CodedConflictError("plan_already_mapped", f"plan {case.context.get('plan_key')}")

    @staticmethod
    def _mapped(ctx: Context) -> InstallmentPlanMap | None:
        return ctx.db.execute(select(InstallmentPlanMap).where(
            InstallmentPlanMap.account_id == ctx.statement.account_id,
            InstallmentPlanMap.plan_key == ctx.case.context["plan_key"])).scalar_one_or_none()

    @staticmethod
    def _active_coverage(ctx: Context) -> set[int]:
        """The active coverage on the case's own event: the in-action reconcile may also claim other lines (an entry
        booked since the last reconcile); those claims are not this action's and its revert must not release them."""
        return set(ctx.db.execute(select(StatementCoverage.id).where(
            StatementCoverage.statement_id == ctx.statement.id, StatementCoverage.event_id == ctx.case.event_id,
            StatementCoverage.status == "active")).scalars())

    def apply(self, ctx: Context, params: LinkParams, prepared: Prepared, effect: Effect) -> None:
        db, case = ctx.db, ctx.case
        status, version = case.status, case.version
        had = self._active_coverage(ctx)
        plan = InstallmentPlanMap(account_id=ctx.statement.account_id, plan_key=case.context["plan_key"],
                                  definition_id=params.definition_id, confirmed_by="owner")
        try:
            with db.begin_nested():
                db.add(plan)
                db.flush()
        except IntegrityError as exc:
            raise CodedConflictError("plan_already_mapped", f"plan {plan.plan_key}") from exc
        # claims the line (rule installment) in this transaction; the action holds row locks, so the sweep never
        # waits at the writer barrier (a busy barrier is 409 sweep_barrier_busy)
        rec.reconcile(db, ctx.statement.id, locked=True, barrier_wait=False)
        if case.status == "superseded":
            # the reconcile retired the case it just answered; the engine resolves it (with the action as
            # resolved_action_id), so give it back its status and version
            case.status, case.version = status, version
            db.flush()
        effect.coverage_ids = sorted(self._active_coverage(ctx) - had)
        effect.inverse = {"plan_map_id": plan.id}

    # --- revert -------------------------------------------------------------------------------------------------

    PLAN_RULE = "installment"  # matching.py: Representation("installment", …, rule="installment") for a plan-map claim

    @staticmethod
    def _plan_entries(db: Session, definition_id: int) -> set[int]:
        posted = db.execute(select(ScheduleInstance.posted_entry_ids)
                            .where(ScheduleInstance.definition_id == definition_id)).scalars()
        return {i for ids in posted for i in ids or [] if isinstance(i, int)}

    @classmethod
    def _other_claims(cls, db: Session, definition_id: int, statement_id: int) -> list[StatementCoverage]:
        """R1d item 3: active plan-rule coverage of the plan's posted entries on OTHER statements of the account."""
        entries = sorted(cls._plan_entries(db, definition_id))
        if not entries:
            return []
        return list(db.execute(select(StatementCoverage).where(
            StatementCoverage.status == "active", StatementCoverage.match_rule == cls.PLAN_RULE,
            StatementCoverage.entry_id.in_(entries), StatementCoverage.statement_id != statement_id)
            .order_by(StatementCoverage.id)).scalars())

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        plan = ctx.db.get(InstallmentPlanMap, (action.inverse or {}).get("plan_map_id"))
        if plan is None:
            return super().revert_lock_targets(ctx, action)
        # R1d item 3: the definition FOR UPDATE (freezes the posted set: a posting takes it FOR SHARE), the plan's
        # posted entries (NOWAIT: a reconcile claiming one holds it first → action_busy) and the other statements
        # already claiming them (locked ascending with the case's, each swept)
        entries, groups = snapshots.ids_of(action.after)
        others = {c.statement_id for c in self._other_claims(ctx.db, plan.definition_id, ctx.statement.id)}
        targets = lockset_for(ctx.db,
                              entry_ids={*entries, *(action.touched_entry_ids or ()), *(action.created_entry_ids or ()),
                                         *self._plan_entries(ctx.db, plan.definition_id)},
                              group_ids={*groups, *(action.touched_group_ids or ())}, statement_ids=others)
        return replace(targets, definition_ids=tuple(i for i in targets.definition_ids if i != plan.definition_id),
                       exclusive_definition_ids=(plan.definition_id,))

    def _check_lock_set(self, ctx: Context, definition_id: int) -> list[StatementCoverage]:
        """Under the locks, before any mutation: the posted-entry set and the other-statement set re-derived; an
        entry or statement the engine did not lock (posted or claimed between the unlocked discovery and the
        locks) is 409 action_busy — the retry discovers and locks the full set. Returns the other claims."""
        held = ctx.lock_set or LockSet()
        new_entries = sorted(self._plan_entries(ctx.db, definition_id) - set(held.entry_ids))
        if new_entries:
            raise CodedConflictError(ACTION_BUSY, f"entry {new_entries[0]} was posted after discovery; retry")
        others = self._other_claims(ctx.db, definition_id, ctx.statement.id)
        late = sorted({c.statement_id for c in others} - set(held.statement_ids))
        if late:
            raise CodedConflictError(ACTION_BUSY, f"statement {late[0]} gained a plan claim after discovery; retry")
        return others

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        db = ctx.db
        plan = db.get(InstallmentPlanMap, action.inverse["plan_map_id"])
        if plan is None:
            raise NotRevertable("the plan mapping is gone")
        definition_id = plan.definition_id
        entries = sorted(self._plan_entries(db, definition_id) | set(action.touched_entry_ids or ()))
        if entries:
            later = db.execute(select(ReconciliationAction.id).where(
                ReconciliationAction.id > action.id, ReconciliationAction.status == "applied",
                ReconciliationAction.action != "revert",
                ReconciliationAction.touched_entry_ids.overlap(entries)).order_by(ReconciliationAction.id).limit(1)
            ).scalar_one_or_none()
            if later is not None:
                raise NotRevertable(f"later action {later} touched the plan's entries")
        others = self._check_lock_set(ctx, definition_id)  # R1d item 3: the last check before any write
        # --- mutations ---
        db.delete(plan)
        db.flush()
        _reopen(ctx.case)  # so the reconcile keeps this case instead of opening a twin
        db.flush()
        # the installment claim is no longer derived: released (barrier_wait=False as in apply)
        rec.reconcile(db, ctx.statement.id, locked=True, barrier_wait=False)
        if others:  # R1d item 3: the plan's claims on other statements are no longer derived either
            coverage_service.release_rows(db, [c.id for c in others], reason="plan_unmapped")
            statement_ids = sorted({c.statement_id for c in others})
            for statement in db.execute(select(AccountStatement).where(AccountStatement.id.in_(statement_ids))
                                        .execution_options(populate_existing=True)).scalars():
                statement.needs_recheck = True  # ruling 29: a matching reason; its next reconcile raises the case
            db.flush()
        return Effect(touched_entry_ids=list(action.touched_entry_ids or ()))


# --- resolve_conflict ---------------------------------------------------------------------------------------------

class ResolveConflictParams(ActionParams):
    keep: Literal["current", "new"]


class ResolveConflictAction(ActionBase):
    """Rulings 23/28/30/34. The case's revision (`case.revision_id`, stored as non-current by the conflicting
    submission) must still be the statement's newest non-rejected revision (409 `conflict_stale`). `keep=new`
    promotes it (`statement_revision_service.promote_revision`: persisted rows only — coverage transferred or
    quarantined by lineage, event pointers, header, current revision), reopens the statement (`status='open'`) and
    reconciles it in this transaction (`barrier_wait=False`: the action holds row locks); a guardrail-failed revision
    never replaces the current one (409 `conflict_guardrail_failed`, R1a §17). `keep=current` marks the revision
    `rejected` (the statement stays `reconciled`). Ruling 34: on either outcome every other open `statement_conflict`
    case of the statement (an older revision's) is superseded (version bumped, audited `conflict_superseded`) and,
    for `keep=current`, its revision rejected too — so clearing `conflict_open` matches the cases. Either resolves
    the case. Not revertable: the owner re-submits instead."""
    name = "resolve_conflict"
    Params = ResolveConflictParams
    case_kinds = frozenset({"statement_conflict"})

    @staticmethod
    def _revision(ctx: Context) -> StatementRevision:
        revision = ctx.db.get(StatementRevision, ctx.case.revision_id)
        newest = ctx.db.execute(
            select(StatementRevision.id).where(StatementRevision.statement_id == ctx.statement.id,
                                               StatementRevision.rejected.is_(False))
            .order_by(StatementRevision.revision.desc()).limit(1)).scalar_one_or_none()
        if (revision is None or revision.statement_id != ctx.statement.id or revision.id != newest
                or revision.id == ctx.statement.current_revision_id):
            raise CodedConflictError("conflict_stale", f"revision {ctx.case.revision_id} is not the newest pending one")
        return revision

    @classmethod
    def _checked(cls, ctx: Context, params: ResolveConflictParams) -> StatementRevision:
        revision = cls._revision(ctx)
        if params.keep == "new" and not revision.guardrail_ok:
            raise CodedConflictError("conflict_guardrail_failed",
                                     f"revision {revision.revision} failed its guardrails; keep the current one")
        return revision

    def validate_before(self, ctx: Context, params: ResolveConflictParams, prepared: Prepared, before: dict) -> None:
        self._checked(ctx, params)

    @staticmethod
    def _supersede_older(ctx: Context, revision: StatementRevision, keep: str) -> None:
        """Ruling 34: the statement's other open/proposed statement_conflict cases (older revisions) → superseded,
        their revisions rejected when the current one is kept."""
        db = ctx.db
        older = db.execute(
            select(ReconciliationCase)
            .join(StatementRevision, StatementRevision.id == ReconciliationCase.revision_id)
            .where(ReconciliationCase.statement_id == ctx.statement.id,
                   ReconciliationCase.kind == "statement_conflict", ReconciliationCase.id != ctx.case.id,
                   ReconciliationCase.status.in_(("open", "proposed")),
                   StatementRevision.revision < revision.revision)
            .order_by(ReconciliationCase.id).with_for_update(of=ReconciliationCase)).scalars().all()
        for case in older:
            case.status, case.version = "superseded", case.version + 1
            if keep == "current" and case.revision_id != ctx.statement.current_revision_id:
                db.get(StatementRevision, case.revision_id).rejected = True
            audit.record(db, event="conflict_superseded", actor=ctx.label, statement_id=ctx.statement.id,
                         case_id=case.id, run_id=ctx.run_id, request_id=ctx.request_id,
                         detail={"by_case_id": ctx.case.id, "revision_id": case.revision_id, "keep": keep})
        db.flush()

    def apply(self, ctx: Context, params: ResolveConflictParams, prepared: Prepared, effect: Effect) -> None:
        db, statement = ctx.db, ctx.statement
        revision = self._checked(ctx, params)
        effect.inverse = {"keep": params.keep, "previous_current_revision_id": statement.current_revision_id}
        self._supersede_older(ctx, revision, params.keep)
        if params.keep == "new":
            revisions.promote_revision(db, statement, revision)
            statement.conflict_open, statement.status = False, "open"
            db.flush()
            rec.reconcile(db, statement.id, locked=True, barrier_wait=False)
        else:
            revision.rejected = True
            statement.conflict_open = False
            db.flush()

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        raise NotRevertable("a conflict resolution is not revertable; submit the statement again")


register(DismissAction())
register(DeferNextPeriodAction())
register(LinkInstallmentPlanAction())
register(ResolveConflictAction())
