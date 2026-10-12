"""Case actions that write no ledger rows (R1c; spec §4.9, rulings 29, 31): `dismiss` accepts a case as an exception,
`defer_next_period` explains an unmatched entry as belonging to the next statement (owner deferrals are
authoritative for the reconcile), `link_installment_plan` confirms a card installment plan and re-reconciles so the
line is claimed in the same transaction. None sets an effect slot: each is one effect per case version."""
from __future__ import annotations

from dataclasses import replace

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    InstallmentPlanMap, ReconciliationAction, ReconciliationCase, ScheduleDefinition, ScheduleInstance,
    StatementCoverage,
)
from app.schemas.writes import Int32
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError, ValidationError

from . import register
from .base import ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared

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
                            "duplicate_claim", "recheck", "parse_review", "statement_conflict"})

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
        closing_day = ctx.account.closing_day if ctx.account.is_credit else None
        ctx.case.deferred_to_period_end = rec.next_period_end(ctx.statement.period_end, closing_day)
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
        return set(ctx.db.execute(select(StatementCoverage.id).where(
            StatementCoverage.statement_id == ctx.statement.id, StatementCoverage.status == "active")).scalars())

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
        rec.reconcile(db, ctx.statement.id, locked=True)  # claims the line (rule installment) in this transaction
        if case.status == "superseded":
            # the reconcile retired the case it just answered; the engine resolves it (with the action as
            # resolved_action_id), so give it back its status and version
            case.status, case.version = status, version
            db.flush()
        effect.coverage_ids = sorted(self._active_coverage(ctx) - had)
        effect.inverse = {"plan_map_id": plan.id}

    # --- revert -------------------------------------------------------------------------------------------------

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        targets = super().revert_lock_targets(ctx, action)
        plan = ctx.db.get(InstallmentPlanMap, (action.inverse or {}).get("plan_map_id"))
        if plan is None:
            return targets
        return replace(targets, definition_ids=tuple(sorted({*targets.definition_ids, plan.definition_id})))

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        db = ctx.db
        plan = db.get(InstallmentPlanMap, action.inverse["plan_map_id"])
        if plan is None:
            raise NotRevertable("the plan mapping is gone")
        posted = db.execute(select(ScheduleInstance.posted_entry_ids)
                            .where(ScheduleInstance.definition_id == plan.definition_id)).scalars()
        entries = sorted({i for ids in posted for i in ids or [] if isinstance(i, int)}
                         | set(action.touched_entry_ids or ()))
        if entries:
            later = db.execute(select(ReconciliationAction.id).where(
                ReconciliationAction.id > action.id, ReconciliationAction.status == "applied",
                ReconciliationAction.action != "revert",
                ReconciliationAction.touched_entry_ids.overlap(entries)).order_by(ReconciliationAction.id).limit(1)
            ).scalar_one_or_none()
            if later is not None:
                raise NotRevertable(f"later action {later} touched the plan's entries")
        db.delete(plan)
        db.flush()
        _reopen(ctx.case)  # so the reconcile keeps this case instead of opening a twin
        db.flush()
        rec.reconcile(db, ctx.statement.id, locked=True)  # the installment claim is no longer derived: released
        return Effect(touched_entry_ids=list(action.touched_entry_ids or ()))


register(DismissAction())
register(DeferNextPeriodAction())
register(LinkInstallmentPlanAction())
