"""Structural reconciliation actions (R1c; spec §4.9 action table, C4; rulings 7, 12, 14, 15, 19, 26):
`move_account` re-homes an unmatched entry on the account whose statement printed it (slot `move`),
`split_entry` converts a near entry into a split and claims the line on the member that equals it (slot `split`),
`balance_adjustment_asof` books a bank statement's balance gap as of the period end (slot `balance`).

Validation runs under the engine's locks (`validate_before`) and again in `apply`, which re-derives everything from
the locked rows; a refusal writes nothing. Each revert runs only while the action's rows equal its `after` (the
engine checks) and is exact: the moved entry goes back and both statements are reconciled again; the split's
non-anchor members are deleted, the group dissolved and the anchor's columns restored from `before`; the adjustment
is deleted."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import Field
from sqlalchemy import Date, DateTime, Numeric, Time, Uuid, exists, inspect, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, EntryGroup, EntryRewardRule, LedgerEntry, ReconciliationAction, ReconciliationCase,
    StatementCoverage,
)
from app.schemas.writes import MAX_SPLIT_MEMBERS, Int32, Money, ShortText, SplitIn, SplitMemberIn
from app.services import coverage_service, ledger_service, settings_service, split_service
from app.services import entry_write_service as ews
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError, ValidationError
from app.services.split_protection import protected_reason

from . import audit, register
from .base import (
    ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, lockset_for, schedule_rows,
)
from .coverage_actions import _line_claimed as line_claimed
from .base import OWNER, POLICY
from .ledger_actions import STATEMENT_SOURCE, _claim, _gate, _release

MOVE, SPLIT, BALANCE = "move", "split", "balance"
SNAPSHOT_CONFLICT = "snapshot_conflict"


def _refuse(field: str, code: str, detail: str | None = None) -> CodedValidationError:
    return CodedValidationError(field, code, detail)


def _case_entry_ids(case: ReconciliationCase) -> set[int]:
    """The entries a case names: its candidates and `context.entry_id` (ruling 19 scope)."""
    named = {c.get("entry_id") for c in (case.candidates or [])} | {(case.context or {}).get("entry_id")}
    return {i for i in named if i is not None}


def _active_coverage_on(db: Session, entry_ids) -> set[int]:
    ids = sorted(set(entry_ids))
    if not ids:
        return set()
    return set(db.execute(select(StatementCoverage.id).where(StatementCoverage.entry_id.in_(ids),
                                                             StatementCoverage.status == "active")).scalars())


def _reconcile_keeping_case(db: Session, case: ReconciliationCase, statement_ids) -> None:
    """Reconcile each statement (all locked by the engine) in ascending id order. The action already holds entry
    and coverage row locks, so the sweep never waits at the writer barrier (`barrier_wait=False`, as the revision
    hook): a busy barrier is 409 `sweep_barrier_busy`. A reconcile that retires the case the action is answering
    (its entry left the population) gives it back its status and version: the engine resolves (or reopens) it."""
    status, version = case.status, case.version
    for sid in sorted(set(statement_ids)):
        rec.reconcile(db, sid, locked=True, barrier_wait=False)
    if case.status == "superseded":
        case.status, case.version = status, version
        db.flush()


# --- move_account (ruling 12) -------------------------------------------------------------------------------------

class MoveAccountParams(ActionParams):
    entry_id: Int32
    account_id: Int32


class MoveAccountAction(ActionBase):
    """The entry belongs to another account: its `account_id` changes (the dirty trigger records the old and the new
    account), then the case statement and the target account's statement covering the entry's posted date (same
    currency) are reconciled in this transaction, so the target can claim it at once. The case resolves: the entry
    is no longer this account's. Only standalone, editable, unprotected entries without children move."""
    name = "move_account"
    Params = MoveAccountParams
    case_kinds = frozenset({"entry_unmatched"})
    slot = MOVE

    @staticmethod
    def _scoped(ctx: Context, params: MoveAccountParams) -> bool:
        return ctx.case.entry_id is not None and params.entry_id == ctx.case.entry_id

    @staticmethod
    def target_statements(db: Session, entry: LedgerEntry | None, account_id: int) -> tuple[int, ...]:
        """Ruling 12: the target account's statements (the entry's currency) whose period contains the entry's
        posted date; read without a lock (`lock_targets`) and again under the locks (`validate_before`)."""
        if entry is None:
            return ()
        return tuple(db.execute(select(AccountStatement.id).where(
            AccountStatement.account_id == account_id, AccountStatement.currency == entry.currency,
            AccountStatement.period_start <= entry.posted_date, AccountStatement.period_end >= entry.posted_date)
            .order_by(AccountStatement.id)).scalars())

    def lock_targets(self, ctx: Context, params: MoveAccountParams) -> LockSet:
        if not self._scoped(ctx, params):
            return LockSet()
        entry = ctx.db.get(LedgerEntry, params.entry_id)
        if entry is None:
            return LockSet()
        return lockset_for(ctx.db, entry_ids=(entry.id,),
                           statement_ids=self.target_statements(ctx.db, entry, params.account_id))

    def validate_before(self, ctx: Context, params: MoveAccountParams, prepared: Prepared, before: dict) -> None:
        entry, _ = self._plan(ctx, params)
        found = tuple(sorted(self.target_statements(ctx.db, entry, params.account_id)))
        if found != prepared.lock_set.statement_ids:  # a target statement appeared, went or moved its period
            raise CodedConflictError(SNAPSHOT_CONFLICT, f"target statements {list(found)}")

    def _plan(self, ctx: Context, params: MoveAccountParams) -> tuple[LedgerEntry, Account]:
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        entry = db.get(LedgerEntry, params.entry_id)
        if entry is None:
            raise _refuse("entry_id", "not_found", f"entry {params.entry_id}")
        if entry.account_id not in coverage_service.participating_accounts(db, ctx.statement.account_id):
            raise _refuse("entry_id", "wrong_account", f"entry {entry.id}")
        target = db.get(Account, params.account_id)
        if target is None:
            raise _refuse("account_id", "not_found", f"account {params.account_id}")
        if target.is_archived:
            raise _refuse("account_id", "archived", f"account {target.id}")
        if target.currency != entry.currency:
            raise _refuse("account_id", "currency", f"{target.currency} vs {entry.currency}")
        if target.id == entry.account_id:
            raise _refuse("account_id", "same_account", f"account {target.id}")
        if target.id in coverage_service.participating_accounts(db, ctx.statement.account_id):
            # still in this statement's population: the next reconcile would reopen the case
            raise _refuse("account_id", "not_movable", f"account {target.id} is part of statement {ctx.statement.id}")
        has_children = db.execute(select(exists().where(LedgerEntry.parent_entry_id == entry.id))).scalar_one()
        if (entry.group_id is not None or entry.parent_entry_id is not None or entry.transfer_group_id is not None
                or has_children or any(schedule_rows(db, [entry.id]))):
            raise _refuse("entry_id", "not_movable", f"entry {entry.id}")
        if entry.id in coverage_service.claimed_entry_ids(db):
            raise _refuse("entry_id", "entry_claimed", f"entry {entry.id}")
        ews.assert_entry_editable(db, entry)
        reason = protected_reason(db, entry)
        if reason is not None:
            raise CodedConflictError("protected", f"entry {entry.id}: {reason}")
        return entry, target

    def apply(self, ctx: Context, params: MoveAccountParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        entry, target = self._plan(ctx, params)
        targets = self.target_statements(db, entry, target.id)
        old = entry.account_id
        entry.account_id = target.id
        db.flush()
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *targets])
        effect.coverage_ids = sorted(_active_coverage_on(db, [entry.id]))
        effect.touched_entry_ids = [entry.id]
        effect.inverse = {"account_id": old, "statement_ids": list(targets)}

    # --- revert -------------------------------------------------------------------------------------------------

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        targets = super().revert_lock_targets(ctx, action)
        stored = [sid for sid in (action.inverse or {}).get("statement_ids", [])
                  if ctx.db.get(AccountStatement, sid) is not None]
        return replace(targets, statement_ids=tuple(sorted({*targets.statement_ids, *stored})))

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        db = ctx.db
        entry = db.get(LedgerEntry, action.params["entry_id"])
        if entry is None:
            raise NotRevertable(f"entry {action.params['entry_id']} is gone")
        old = db.get(Account, action.inverse["account_id"])
        if old is None or old.is_archived:
            raise NotRevertable(f"account {action.inverse['account_id']} is gone or archived")
        _release(ctx, action)  # the target's claim goes first: the move back must not reach it as a dirty edit
        entry.account_id = old.id
        db.flush()
        _reopen(ctx.case)  # so the case statement's reconcile keeps this case instead of opening a twin
        stored = [sid for sid in action.inverse.get("statement_ids", [])
                  if db.get(AccountStatement, sid) is not None]
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *stored])
        return Effect(touched_entry_ids=[entry.id])


def _reopen(case: ReconciliationCase) -> None:
    case.status, case.explanation = "open", None
    case.resolved_by = case.resolved_action_id = case.resolved_at = None


# --- split_entry (ruling 19) --------------------------------------------------------------------------------------

class SplitPart(ActionParams):
    amount: Money
    category_id: Int32
    name: ShortText | None = None


class SplitEntryParams(ActionParams):
    entry_id: Int32
    parts: list[SplitPart] = Field(min_length=2, max_length=MAX_SPLIT_MEMBERS)


_NOT_RESTORED = ("id", "seq", "created_at")


def _from_json(column_type, value):
    """A `snapshots` column value (JSON-ready) back to its Python type."""
    if value is None:
        return None
    if isinstance(column_type, Numeric):
        return Decimal(value)
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column_type, Date):
        return date.fromisoformat(value)
    if isinstance(column_type, Time):
        return time.fromisoformat(value)
    if isinstance(column_type, Uuid):
        return UUID(value)
    return list(value) if isinstance(value, list) else value


def _restore_columns(entry: LedgerEntry, snap: dict) -> None:
    """Every column of the entry as `snap` (an `entries` item of a snapshot set) recorded it, except the identity
    columns, which never change (updated_at included, so the restored row equals its snapshot)."""
    for attr in inspect(LedgerEntry).column_attrs:
        if attr.key not in _NOT_RESTORED:
            setattr(entry, attr.key, _from_json(attr.columns[0].type, snap[attr.key]))


class SplitEntryAction(ActionBase):
    """Converts the named entry into a split (`split_service.convert_to_split`; the anchor keeps its id and takes
    the first part, the other parts become new members with the anchor's kind, account, dates, project, tags,
    merchant and counterparty) and claims the case's line on the member whose amount equals it — the case then
    resolves; otherwise it stays open with `context.split_group_id` for a later `match`. Preflight refusals (422
    `not_splittable`) keep the inverse exact: children, reward links, a foreign amount, settlements or refunds,
    a group, a parent, a transfer, a schedule instance, a protected or non-editable kind."""
    name = "split_entry"
    Params = SplitEntryParams
    case_kinds = frozenset({"amount_delta", "line_unmatched"})
    slot = SPLIT

    @staticmethod
    def _scoped(ctx: Context, params: SplitEntryParams) -> bool:
        return params.entry_id in _case_entry_ids(ctx.case)

    def lock_targets(self, ctx: Context, params: SplitEntryParams) -> LockSet:
        return lockset_for(ctx.db, entry_ids=(params.entry_id,)) if self._scoped(ctx, params) else LockSet()

    def validate_before(self, ctx: Context, params: SplitEntryParams, prepared: Prepared, before: dict) -> None:
        self._plan(ctx, params)

    def _plan(self, ctx: Context, params: SplitEntryParams) -> tuple[LedgerEntry, SplitIn]:
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        entry = _gate(ctx, db.get(LedgerEntry, params.entry_id), "entry_id", params.entry_id)
        reason = self._unsplittable(db, entry)
        if reason is not None:
            raise _refuse("entry_id", "not_splittable", f"entry {entry.id}: {reason}")
        ews.assert_entry_editable(db, entry)
        total = sum((part.amount for part in params.parts), Decimal(0))
        if total != abs(Decimal(entry.amount)):
            raise _refuse("parts", "parts_sum", f"parts {total} vs entry {abs(Decimal(entry.amount))}")
        for index, part in enumerate(params.parts):
            try:
                ews.check_category(db, part.category_id, entry.kind)
            except ValidationError as exc:
                raise ValidationError(f"parts.{index}.category_id", exc.message) from exc
        return entry, self._split_in(entry, params)

    @staticmethod
    def _unsplittable(db: Session, entry: LedgerEntry) -> str | None:
        """Ruling 19's preflight, in order; None when the convert is exactly invertible."""
        if db.execute(select(exists().where(LedgerEntry.parent_entry_id == entry.id))).scalar_one():
            return "children"
        if (entry.reward_rule_id is not None or entry.reward_source_entry_id is not None
                or db.execute(select(exists().where(EntryRewardRule.entry_id == entry.id))).scalar_one()
                or db.execute(select(exists().where(LedgerEntry.reward_source_entry_id == entry.id))).scalar_one()):
            return "reward_link"
        if entry.original_currency is not None:
            return "foreign_amount"
        if ews.has_settlements_or_refunds(db, entry.id):
            return "settlements_or_refunds"
        if entry.group_id is not None:
            return "grouped"
        if entry.parent_entry_id is not None:
            return "child"
        if entry.transfer_group_id is not None:
            return "transfer"
        if entry.source == "schedule" or any(schedule_rows(db, [entry.id])):
            return "scheduled"
        if entry.kind not in ews.EDITABLE_KINDS:
            return entry.kind
        return protected_reason(db, entry)

    @staticmethod
    def _split_in(entry: LedgerEntry, params: SplitEntryParams) -> SplitIn:
        """Every member carries the anchor's dates, project and tags explicitly (`member_payload` would otherwise
        post a member on its own entry_date); the anchor keeps its own name unless the first part names one, and
        its merchant, counterparty, description and invoice fields (convert rewrites every column)."""
        shared = {"kind": entry.kind, "account_id": entry.account_id, "entry_date": entry.entry_date,
                  "entry_time": entry.entry_time, "posted_date": entry.posted_date, "project_id": entry.project_id,
                  "tags": list(entry.tags or []), "merchant": entry.merchant, "counterparty_id": entry.counterparty_id}
        first, *rest = params.parts
        anchor = SplitMemberIn(id=entry.id, amount=first.amount, category_id=first.category_id,
                               name=first.name if first.name is not None else entry.name,
                               description=entry.description, invoice_number=entry.invoice_number,
                               invoice_random=entry.invoice_random, **shared)
        members = [SplitMemberIn(amount=p.amount, category_id=p.category_id, name=p.name, **shared) for p in rest]
        return SplitIn(entry_date=entry.entry_date, entry_time=entry.entry_time, posted_date=entry.posted_date,
                       project_id=entry.project_id, tags=list(entry.tags or []), members=[anchor, *members])

    def apply(self, ctx: Context, params: SplitEntryParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        entry, split_in = self._plan(ctx, params)
        result = split_service.convert_to_split(db, entry.id, split_in, source=STATEMENT_SOURCE)
        gid = result.group_id
        member_ids = [entry_id for entry_id, _ in result.members]
        members = [db.get(LedgerEntry, mid) for mid in member_ids]
        line = ctx.line
        hit = None
        if line is not None and not line_claimed(ctx, line.id):
            hit = next((m for m in members if Decimal(m.amount) == Decimal(line.flow_amount)), None)
        if hit is not None:
            effect.coverage_ids = _claim(ctx, line, [(hit, "principal")], self.name)
        else:
            effect.resolves_case = False
            effect.case_context = {"split_group_id": gid}
        effect.created_entry_ids = [mid for mid in member_ids if mid != entry.id]
        effect.touched_entry_ids = [entry.id]
        effect.touched_group_ids = [gid]
        effect.inverse = {"group_id": gid, "anchor_id": entry.id, "member_ids": member_ids}

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        """Spec: only while every member and child equals `after` (the engine compares the snapshots, whose member
        and child id lists catch an added or removed row); the members are re-checked against the inverse here."""
        db, inverse = ctx.db, action.inverse
        gid, anchor_id = inverse["group_id"], inverse["anchor_id"]
        group = db.get(EntryGroup, gid)
        if group is None:
            raise NotRevertable(f"split {gid} is gone")
        current = split_service.member_ids(db, gid)
        if sorted(current) != sorted(inverse["member_ids"]):
            raise NotRevertable(f"the members of split {gid} changed")
        snap = (action.before or {}).get("entries", {}).get(str(anchor_id))
        if snap is None:
            raise NotRevertable(f"no before snapshot of entry {anchor_id}")
        name, merchant, description = group.name, group.merchant, group.description
        _release(ctx, action)
        others = [mid for mid in current if mid != anchor_id]
        ews.delete_entries_cascade(db, others)
        dissolve = db.get(LedgerEntry, anchor_id)
        ews.dissolve_group(db, gid, dissolve, name=name, merchant=merchant, description=description)
        anchor = db.get(LedgerEntry, anchor_id)
        _restore_columns(anchor, snap)
        db.flush()
        context = dict(ctx.case.context or {})
        if context.pop("split_group_id", None) is not None:
            ctx.case.context = context
        return Effect(deleted_entry_ids=others, touched_entry_ids=[anchor_id], touched_group_ids=[gid])


# --- balance_adjustment_asof (C4 via rulings 15, 26) --------------------------------------------------------------

class BalanceAdjustmentParams(ActionParams):
    pass


class BalanceAdjustmentAsOfAction(ActionBase):
    """Books `statement_total − balance(period end)` as a `balance_adjustment` dated the period end. The engine
    holds the import key and the statement lock, the account row FOR UPDATE and the exclusive transaction-scoped
    writer barrier (`coverage_service.take_barrier_xact`): no ledger writer is between its dirty insert and its
    commit, and none can commit until this transaction ends, so the balance read is complete. Nothing to release:
    the barrier ends with the router's commit or rollback. Refused while the dirty triggers are off (they would not
    take the barrier)."""
    name = "balance_adjustment_asof"
    Params = BalanceAdjustmentParams
    case_kinds = frozenset({"balance_gap"})
    slot = BALANCE

    def lock_targets(self, ctx: Context, params: BalanceAdjustmentParams) -> LockSet:
        return LockSet(account_lock=True, barrier=True)

    def check_before_locks(self, ctx: Context, params: BalanceAdjustmentParams) -> None:
        """Before the account row and the barrier are taken (the triggers take the barrier only while on)."""
        if not settings_service.read_reconciliation_settings(ctx.db)["dirty_enabled"]:
            raise _refuse("action", "dirty_disabled", "the writer barrier needs the dirty triggers")

    def apply(self, ctx: Context, params: BalanceAdjustmentParams, prepared: Prepared, effect: Effect) -> None:
        self.check_before_locks(ctx, params)
        db, statement = ctx.db, ctx.statement
        end = statement.period_end
        delta = Decimal(statement.statement_total) - ledger_service.account_balance(db, statement.account_id,
                                                                                    as_of=end)
        if delta == 0:
            raise _refuse("action", "zero_delta", f"statement {statement.id} balances at {end.isoformat()}")
        entry = LedgerEntry(account_id=statement.account_id, currency=statement.currency, kind="balance_adjustment",
                            amount=delta, entry_date=end, posted_date=end,
                            category_id=ews.system_category_id(db, "balance_adjustment"),
                            description=f"對帳調整 {end.isoformat()}", source=STATEMENT_SOURCE, needs_review=True)
        db.add(entry)
        db.flush()
        effect.created_entry_ids = [entry.id]
        effect.inverse = {"created_entry_id": entry.id}

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        entry_id = action.inverse["created_entry_id"]
        if ctx.db.get(LedgerEntry, entry_id) is None:
            raise NotRevertable(f"entry {entry_id} is gone")
        ews.delete_entries_cascade(ctx.db, [entry_id])
        return Effect(deleted_entry_ids=[entry_id])



# --- anchor_opening (R6b.2) ---

ANCHOR = "anchor"


def live_bank_statement_ids(db: Session, account_id: int) -> tuple[int, ...]:
    return tuple(db.execute(select(AccountStatement.id).where(
        AccountStatement.account_id == account_id, AccountStatement.kind == "bank", AccountStatement.mode == "live")
        .order_by(AccountStatement.id)).scalars())


def anchor_target(db: Session, account_id: int) -> AccountStatement | None:
    return db.execute(select(AccountStatement).where(
        AccountStatement.account_id == account_id, AccountStatement.kind == "bank", AccountStatement.mode == "live",
        AccountStatement.opening_balance.is_not(None)).order_by(AccountStatement.period_start, AccountStatement.id)).scalars().first()


def anchor_applied(db: Session, account_id: int, *, exclude_case_id: int | None = None) -> bool:
    """An applied anchor exists on the account. `apply` runs after the engine inserted its own row, so it passes its
    case id to leave that row out."""
    return db.scalar(select(exists().where(
        ReconciliationAction.action == "anchor_opening", ReconciliationAction.status == "applied",
        ReconciliationAction.case_id != exclude_case_id if exclude_case_id is not None else True,
        ReconciliationAction.case_id == ReconciliationCase.id, ReconciliationCase.statement_id == AccountStatement.id,
        AccountStatement.account_id == account_id)))


def anchor_as_of(db: Session, account_id: int) -> date | None:
    """The `as_of` of the account's applied anchor (its inverse), or None. Every missing entry dated on or before
    it is already inside the anchor's amount (final review C1): the automatic builders must not book such a line."""
    found = db.execute(
        select(ReconciliationAction.inverse)
        .join(ReconciliationCase, ReconciliationCase.id == ReconciliationAction.case_id)
        .join(AccountStatement, AccountStatement.id == ReconciliationCase.statement_id)
        .where(ReconciliationAction.action == "anchor_opening", ReconciliationAction.status == "applied",
               AccountStatement.account_id == account_id)
        .order_by(ReconciliationAction.id.desc()).limit(1)).scalar_one_or_none()
    as_of = (found or {}).get("as_of")
    return date.fromisoformat(as_of) if as_of else None


def anchor_delta(db: Session, account_id: int) -> tuple[date, Decimal] | None:
    """R6b.2: (as_of, target.opening_balance − balance(as_of)) at the account's anchor target, or None. Module
    level, defined BEFORE the action class (one arithmetic for the action, the policy builder and the dry-run)."""
    target = anchor_target(db, account_id)
    if target is None:
        return None
    as_of = target.period_start - timedelta(days=1)
    delta = (Decimal(target.opening_balance) - ledger_service.account_balance(db, account_id, as_of=as_of)).quantize(Decimal("0.0001"))
    return as_of, delta


class AnchorOpeningParams(ActionParams):
    pass


class AnchorOpeningAction(ActionBase):
    """R6b.2: books `target.opening_balance − balance(period_start − 1)` as a balance_adjustment dated the day before
    the account's earliest live statement, once per account, and flags every live bank statement of the account
    for recheck. Same barrier discipline as balance_adjustment_asof."""
    name = "anchor_opening"
    Params = AnchorOpeningParams
    case_kinds = frozenset({"balance_gap"})
    actors = frozenset({OWNER, POLICY})
    slot = ANCHOR

    def lock_targets(self, ctx: Context, params: AnchorOpeningParams) -> LockSet:
        return LockSet(account_lock=True, barrier=True, statement_ids=live_bank_statement_ids(ctx.db, ctx.statement.account_id))

    def check_before_locks(self, ctx: Context, params: AnchorOpeningParams) -> None:
        if not settings_service.read_reconciliation_settings(ctx.db)["dirty_enabled"]:
            raise _refuse("action", "dirty_disabled", "the writer barrier needs the dirty triggers")

    def _verify_targets(self, ctx: Context, locked: tuple[int, ...], recorded: tuple[int, ...] = ()) -> None:
        """Apply: the locked set must equal the current live set. Revert: it must equal recorded ∪ current (a
        statement that left the live set since the anchor is still re-flagged; one that joined is locked too)."""
        required = set(live_bank_statement_ids(ctx.db, ctx.statement.account_id)) | set(recorded)
        if required != set(locked):
            raise _refuse("action", "targets_changed", "the account's live statements changed; retry")

    def apply(self, ctx: Context, params: AnchorOpeningParams, prepared: Prepared, effect: Effect) -> None:
        self.check_before_locks(ctx, params)
        db, account_id = ctx.db, ctx.statement.account_id
        locked = tuple(i for i in ctx.lock_set.statement_ids) if ctx.lock_set else ()
        self._verify_targets(ctx, locked)
        if anchor_applied(db, account_id, exclude_case_id=ctx.case.id):  # the engine's own row is already inserted
            raise _refuse("action", "already_anchored", f"account {account_id} is anchored")
        found = anchor_delta(db, account_id)  # under the account lock and the barrier: the balance is complete
        if found is None:
            raise _refuse("action", "no_opening_balance", "no live statement carries an opening balance")
        as_of, delta = found
        if db.scalar(select(exists().where(LedgerEntry.account_id == account_id, LedgerEntry.kind == "balance_adjustment",
                                            LedgerEntry.posted_date >= as_of))):
            raise _refuse("action", "closing_adjustments_present", "revert or delete the account's closing adjustments first")
        if delta == 0:
            raise _refuse("action", "zero_delta", f"account {account_id} balances at {as_of.isoformat()}")
        if ctx.budget_amount is not None and abs(delta) != ctx.budget_amount:  # M3: a writer committed between the policy's charge and the barrier
            raise _refuse("action", "amount_changed", f"anchor delta moved since the policy reserved {ctx.budget_amount}")
        entry = LedgerEntry(account_id=account_id, currency=ctx.statement.currency, kind="balance_adjustment", amount=delta,
                            entry_date=as_of, posted_date=as_of, category_id=ews.system_category_id(db, "balance_adjustment"),
                            description=f"期初對齊 {as_of.isoformat()}", source=STATEMENT_SOURCE, needs_review=True)
        db.add(entry)
        db.flush()
        for sid in locked:
            db.get(AccountStatement, sid).needs_recheck = True
        effect.created_entry_ids = [entry.id]
        effect.inverse = {"created_entry_id": entry.id, "statement_ids": list(locked), "as_of": as_of.isoformat()}
        audit.record(db, event="anchor_applied", actor=ctx.actor, statement_id=ctx.statement.id, case_id=ctx.case.id,
                     action_id=None, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"account_id": account_id, "as_of": as_of.isoformat(), "statements": list(locked)})

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        recorded = tuple(action.inverse.get("statement_ids") or ())
        return LockSet(account_lock=True, barrier=True,
                       statement_ids=tuple(sorted(set(recorded) | set(live_bank_statement_ids(ctx.db, ctx.statement.account_id)))))

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        self.check_before_locks(ctx, None)
        locked = tuple(ctx.lock_set.statement_ids) if ctx.lock_set else ()
        self._verify_targets(ctx, locked, recorded=tuple(action.inverse.get("statement_ids") or ()))
        entry_id = action.inverse["created_entry_id"]
        if ctx.db.get(LedgerEntry, entry_id) is None:
            raise NotRevertable(f"entry {entry_id} is gone")
        as_of = date.fromisoformat(action.inverse["as_of"])
        if ctx.db.scalar(select(exists().where(
                LedgerEntry.account_id == ctx.statement.account_id, LedgerEntry.kind == "balance_adjustment",
                LedgerEntry.posted_date >= as_of, LedgerEntry.id != entry_id))):
            raise _refuse("action", "closing_adjustments_present", "revert or delete the account's closing adjustments first")
        ews.delete_entries_cascade(ctx.db, [entry_id])
        for sid in locked:
            ctx.db.get(AccountStatement, sid).needs_recheck = True
        return Effect(deleted_entry_ids=[entry_id])


register(MoveAccountAction())
register(SplitEntryAction())
register(BalanceAdjustmentAsOfAction())
register(AnchorOpeningAction())
