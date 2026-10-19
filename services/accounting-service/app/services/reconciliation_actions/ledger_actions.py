"""Ledger-writing actions (R1c; spec §4.9 action table, C6, C8; rulings 7, 18, 20, 25, 31): `create_entry`,
`create_system_entry` and `create_refund` book the case's statement line as a new row (effect slot `create`, one
creation per event whichever action made it), `attach_fee` books the foreign-transaction fee a bundled card line
carries as a child of the purchase (slot `fee`), `adjust_amount` rewrites a near entry to the printed amount (slot
`amount`). Each claims the line on what it wrote (`match_kind` manual, or `agent` for every policy apply — R1d;
`match_rule` = the action name) and reverts only while its rows equal the action's `after` (the engine checks).

Created rows carry `source='statement'`, the statement's account and currency, `entry_date` = the line's
transaction date (else its posting date), `posted_date` = the line's posting date, a description starting
`對帳建立 <period end>`, and `needs_review` unless the owner chose the category (`create_entry`). Validation runs
under the engine's locks (`validate_before`) and again in `apply`, which re-derives everything from the locked
rows; a refusal writes nothing."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from sqlalchemy import select

from app.models import (
    Account, LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementLine,
)
from app.schemas.writes import ChildIn, Int32, RefundIn, ShortText
from app.services import coverage_service, settings_service, settlement_service, stand_ins
from app.services import entry_write_service as ews
from app.services.errors import CodedConflictError, CodedValidationError
from app.services.split_protection import protected_reason
from app.services.statements import matching

from . import audit, register, snapshots
from .base import (
    OWNER, POLICY, ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, line_for_event,
    lockset_for, schedule_rows,
)
from .coverage_actions import _line_claimed as line_claimed
from .coverage_actions import case_line

CREATE, FEE, AMOUNT = "create", "fee", "amount"
STATEMENT_SOURCE = "statement"
FEE_CAP = Decimal("0.05")  # an attached fee is at most 5 % of the purchase
# `fx_source` has no 'statement' value and R1c adds no migration: a rate derived from the printed pair is recorded
# as an owner-supplied rate. Flagged for a ruling (Task 6 report).
ADJUST_FX_SOURCE = "manual"


def _refuse(field: str, code: str, detail: str | None = None) -> CodedValidationError:
    return CodedValidationError(field, code, detail)


def _match_kind(ctx: Context) -> str:
    if ctx.actor == POLICY:
        # R1d item 1: never `auto` — a reconcile keeps only `manual`/`agent` rows (ruling 16), so an `auto` claim
        # written by the policy was rewritten by the next reconcile and its action could not be reverted
        return "agent"
    return "manual"  # an owner-applied proposal is the owner's decision


def _note(ctx: Context, line: StatementLine) -> str:
    return f"對帳建立 {ctx.statement.period_end.isoformat()} {line.merchant_raw}"


def _open_line(ctx: Context, line: StatementLine | None = None) -> StatementLine:
    """The case's line in the current revision (or `line`), not claimed by anything yet."""
    line = ctx.line if line is None else line
    if line is None:
        raise _refuse("case", "line_not_printed", f"case {ctx.case.id}")
    if line_claimed(ctx, line.id):
        raise _refuse("line_id", "line_claimed", f"line {line.id}")
    return line


def _gate(ctx: Context, entry: LedgerEntry | None, field: str, entry_id: int) -> LedgerEntry:
    """An existing entry on a participating account, in the statement's currency, not claimed by any line."""
    if entry is None:
        raise _refuse(field, "not_found", f"entry {entry_id}")
    if entry.account_id not in coverage_service.participating_accounts(ctx.db, ctx.statement.account_id):
        raise _refuse(field, "wrong_account", f"entry {entry.id}")
    if entry.currency != ctx.statement.currency:
        raise _refuse(field, "wrong_currency", f"entry {entry.id}")
    if entry.id in coverage_service.claimed_entry_ids(ctx.db):
        raise _refuse(field, "entry_claimed", f"entry {entry.id}")
    return entry


def _claim(ctx: Context, line: StatementLine, picked: list[tuple[LedgerEntry, str]], rule: str,
           group_id: int | None = None) -> list[int]:
    rows = [matching.Row(entry.id, role, Decimal(entry.amount), group_id) for entry, role in picked]
    written = coverage_service.write_claim(ctx.db, ctx.statement, line, rows,
                                           entries_by_id={entry.id: entry for entry, _ in picked},
                                           match_kind=_match_kind(ctx), match_rule=rule, run_id=ctx.run_id)
    return [c.id for c in written]


def _release(ctx: Context, action: ReconciliationAction) -> None:
    """Coverage first, then the ledger rows (the engine's own release finds nothing left)."""
    coverage_service.release_rows(ctx.db, (action.after or {}).get("coverage_ids", []), reason="revert")


# --- the creation family ------------------------------------------------------------------------------------------

class _Creation(ActionBase):
    """Books the case's line as one new row; reverting deletes it (only while it equals the action's `after`:
    no child added, no refund or settlement pointing at it, the same amount)."""
    slot = CREATE

    def _book(self, ctx: Context, line: StatementLine, effect: Effect, **fields) -> LedgerEntry:
        entry = LedgerEntry(account_id=ctx.statement.account_id, currency=ctx.statement.currency,
                            amount=line.flow_amount, entry_date=line.txn_date or line.posted_date,
                            posted_date=line.posted_date, merchant=line.merchant_raw[:128],
                            description=_note(ctx, line), source=STATEMENT_SOURCE, **fields)
        ctx.db.add(entry)
        ctx.db.flush()
        self._claimed(ctx, line, entry, effect)
        return entry

    def _claimed(self, ctx: Context, line: StatementLine, entry: LedgerEntry, effect: Effect) -> None:
        effect.coverage_ids = _claim(ctx, line, [(entry, "principal")], self.name)
        effect.created_entry_ids = [entry.id]
        effect.inverse = {"created_entry_id": entry.id}

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        entry_id = action.inverse["created_entry_id"]
        if ctx.db.get(LedgerEntry, entry_id) is None:
            raise NotRevertable(f"entry {entry_id} is gone")
        _release(ctx, action)
        ews.delete_entries_cascade(ctx.db, [entry_id])
        others = [i for i in (action.touched_entry_ids or ()) if i != entry_id]
        return Effect(deleted_entry_ids=[entry_id], touched_entry_ids=others)


class _TwinGuarded(_Creation):
    """PR #77 review M1/S1: the automatic creations (`stand_ins.OWNER_TWIN_GUARDED`) must not book a line an owner
    entry may already record (`stand_ins.owner_may_record`: an owner twin — a split member included — or a replaced
    event). The policy's precheck and `refuse_owner_recorded` run under the statement and case locks only, which do
    not stop an ordinary ledger writer; so, like `balance_adjustment_asof` / `anchor_opening`, the action takes the
    account row and the exclusive writer barrier (after the statements and the case: the engine's order) and
    re-checks INSIDE the action, in `validate_before`: from there to the commit no writer can commit a twin. The
    re-check binds the policy (`candidate_changed`) and the owner applying a policy-authored proposal
    (`owner_may_record`), both refused `dirty_disabled` while the dirty triggers (which take the barrier) are off; a
    plain owner action from the case takes the barrier but stays unrestricted (owner override)."""

    def lock_targets(self, ctx: Context, params) -> LockSet:
        return LockSet(account_lock=True, barrier=True)

    @staticmethod
    def _guard_code(ctx: Context) -> str | None:
        """`candidate_changed` for the policy, `owner_may_record` for the owner applying a policy-authored proposal,
        None for a plain owner action."""
        if ctx.actor == POLICY:
            return "candidate_changed"
        proposal = ctx.db.get(ReconciliationProposal, ctx.proposal_id) if ctx.proposal_id is not None else None
        return "owner_may_record" if proposal is not None and proposal.author == POLICY else None

    def check_before_locks(self, ctx: Context, params) -> None:
        """Before the account row and the barrier (the triggers take the barrier only while on)."""
        if self._guard_code(ctx) is not None \
                and not settings_service.read_reconciliation_settings(ctx.db)["dirty_enabled"]:
            raise _refuse("action", "dirty_disabled", "the owner-twin re-check needs the writer barrier")

    def _twin_check(self, ctx: Context, line: StatementLine) -> None:
        """Under the writer barrier: refuse when an owner entry may record the line (guarded callers only). The dirty
        flag is read again, as `balance_adjustment_asof` / `anchor_opening` do in `apply`."""
        code = self._guard_code(ctx)
        if code is None:
            return
        self.check_before_locks(ctx, None)
        if stand_ins.owner_may_record(ctx.db, ctx.statement, ctx.case.event_id, line, ctx.rules):
            raise CodedConflictError(code, f"an owner entry may already record line {line.id}")


class CreateEntryParams(ActionParams):
    kind: Literal["expense", "income"]
    category_id: Int32
    name: ShortText | None = None
    project_id: Int32 | None = None


class CreateEntryAction(_Creation):
    name = "create_entry"
    Params = CreateEntryParams
    case_kinds = frozenset({"line_unmatched"})

    def validate_before(self, ctx: Context, params: CreateEntryParams, prepared: Prepared, before: dict) -> None:
        self._check(ctx, params)

    def _check(self, ctx: Context, params: CreateEntryParams) -> StatementLine:
        line = _open_line(ctx)
        flow = Decimal(line.flow_amount)
        if (params.kind == "expense" and flow >= 0) or (params.kind == "income" and flow <= 0):
            raise _refuse("kind", "kind_sign", f"{params.kind} cannot book a flow of {flow}")
        ews.check_category(ctx.db, params.category_id, params.kind)  # 422 on category_id: missing or other kind
        ews.check_project(ctx.db, params.project_id)
        return line

    def apply(self, ctx: Context, params: CreateEntryParams, prepared: Prepared, effect: Effect) -> None:
        line = self._check(ctx, params)
        self._book(ctx, line, effect, kind=params.kind, category_id=params.category_id,
                   project_id=params.project_id, name=params.name, needs_review=False)


class CreateSystemEntryParams(ActionParams):
    kind: Literal["fee", "interest", "reward"]


class CreateSystemEntryAction(_TwinGuarded):
    name = "create_system_entry"
    Params = CreateSystemEntryParams
    case_kinds = frozenset({"line_unmatched"})
    actors = frozenset({OWNER, POLICY})

    def validate_before(self, ctx: Context, params: CreateSystemEntryParams, prepared: Prepared,
                        before: dict) -> None:
        self._twin_check(ctx, _open_line(ctx))

    def apply(self, ctx: Context, params: CreateSystemEntryParams, prepared: Prepared, effect: Effect) -> None:
        line = _open_line(ctx)
        self._book(ctx, line, effect, kind=params.kind, category_id=ews.system_category_id(ctx.db, params.kind),
                   needs_review=True)


class CreateRefundParams(ActionParams):
    original_entry_id: Int32


class CreateRefundAction(_Creation):
    """Books a refund line as a refund of an owner-named expense (`settlement_service.refund`, which re-checks the
    remaining amount under its own row lock)."""
    name = "create_refund"
    Params = CreateRefundParams
    case_kinds = frozenset({"line_unmatched"})

    def lock_targets(self, ctx: Context, params: CreateRefundParams) -> LockSet:
        if ctx.db.get(LedgerEntry, params.original_entry_id) is None:
            return LockSet()
        return lockset_for(ctx.db, entry_ids=(params.original_entry_id,))  # its refund_ids change

    def validate_before(self, ctx: Context, params: CreateRefundParams, prepared: Prepared, before: dict) -> None:
        self._check(ctx, params)

    def _check(self, ctx: Context, params: CreateRefundParams) -> tuple[StatementLine, LedgerEntry]:
        line = _open_line(ctx)
        flow = Decimal(line.flow_amount)
        if flow <= 0:
            raise _refuse("case", "refund_sign", f"line flow {flow}")
        original = ctx.db.get(LedgerEntry, params.original_entry_id)
        if original is None:
            raise _refuse("original_entry_id", "not_found", f"entry {params.original_entry_id}")
        if original.account_id not in coverage_service.participating_accounts(ctx.db, ctx.statement.account_id):
            raise _refuse("original_entry_id", "wrong_account", f"entry {original.id}")
        if original.currency != ctx.statement.currency:
            raise _refuse("original_entry_id", "wrong_currency", f"entry {original.id}")
        if original.kind != "expense" or original.parent_entry_id is not None:
            raise _refuse("original_entry_id", "not_refundable", f"entry {original.id} is {original.kind}")
        refunded = settlement_service.refunded_amount(ctx.db, original)
        if refunded + abs(flow) > abs(Decimal(original.amount)):
            raise _refuse("original_entry_id", "refund_exceeds",
                          f"refunded {refunded} + {abs(flow)} > {abs(Decimal(original.amount))}")
        return line, original

    def apply(self, ctx: Context, params: CreateRefundParams, prepared: Prepared, effect: Effect) -> None:
        line, original = self._check(ctx, params)
        refund_id = settlement_service.refund(
            ctx.db, original.id,
            RefundIn(account_id=ctx.statement.account_id, amount=abs(Decimal(line.flow_amount)),
                     entry_date=line.txn_date or line.posted_date, description=_note(ctx, line)),
            source=STATEMENT_SOURCE)
        refund = ctx.db.get(LedgerEntry, refund_id)
        refund.posted_date, refund.needs_review = line.posted_date, True  # refund() posts on its entry date
        ctx.db.flush()
        self._claimed(ctx, line, refund, effect)
        effect.touched_entry_ids = [original.id]


# --- attach_fee (C8, ruling 20: the fee bundled into the purchase line only) -----------------------------------

class AttachFeeParams(ActionParams):
    entry_id: Int32


class AttachFeeAction(ActionBase):
    """The purchase line prints principal + fee; the entry holds the principal. The fee is the residual of a debit
    line (`line.flow − entry.flow` < 0), positive in `ChildIn` and stored negative as the 國外交易手續費 child; it
    must be in display units, within 5 % of the principal and within one display unit of the account's proposed
    FX fee. Coverage: principal + child on the line."""
    name = "attach_fee"
    Params = AttachFeeParams
    case_kinds = frozenset({"amount_delta"})
    actors = frozenset({OWNER, POLICY})
    slot = FEE

    @staticmethod
    def _scoped(ctx: Context, params: AttachFeeParams) -> bool:
        return (ctx.case.context or {}).get("entry_id") == params.entry_id

    def lock_targets(self, ctx: Context, params: AttachFeeParams) -> LockSet:
        return lockset_for(ctx.db, entry_ids=(params.entry_id,)) if self._scoped(ctx, params) else LockSet()

    def validate_before(self, ctx: Context, params: AttachFeeParams, prepared: Prepared, before: dict) -> None:
        self._plan(ctx, params)

    def _plan(self, ctx: Context, params: AttachFeeParams) -> tuple[StatementLine, LedgerEntry, Decimal]:
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        entry = db.get(LedgerEntry, params.entry_id)
        if entry is not None and db.execute(select(LedgerEntry.id).where(
                LedgerEntry.parent_entry_id == entry.id, LedgerEntry.name == ews.FX_FEE_CHILD_NAME)).first():
            raise CodedConflictError("fee_exists", f"entry {entry.id}")
        line = _open_line(ctx)
        entry = _gate(ctx, entry, "entry_id", params.entry_id)
        if entry.parent_entry_id is not None or entry.group_id is not None:
            raise _refuse("entry_id", "not_standalone", f"entry {entry.id}")
        if entry.original_currency is None:
            raise _refuse("entry_id", "not_foreign", f"entry {entry.id}")
        ews.assert_entry_editable(db, entry)
        flow, principal = Decimal(line.flow_amount), Decimal(entry.amount)
        residual = flow - principal
        if flow >= 0 or residual > 0:  # credit-side residuals are not supported (write_children books fees < 0)
            raise _refuse("entry_id", "fee_sign", f"line {flow}, entry {principal}")
        fee = -residual
        if not (0 < fee <= FEE_CAP * abs(principal)):
            raise _refuse("entry_id", "fee_out_of_bounds", f"fee {fee}")
        quantum = ews.display_quantum(ctx.statement.currency)
        if fee.quantize(quantum) != fee:
            raise _refuse("entry_id", "fee_quantum", f"fee {fee}")
        proposed = ews.proposed_fx_fee(db.get(Account, entry.account_id), principal)
        if proposed is None or abs(fee - proposed) > quantum:
            raise _refuse("entry_id", "fee_unexpected", f"fee {fee}, proposed {proposed}")
        rows = (matching.Row(entry.id, "principal", principal), matching.Row(0, "child", -fee))
        if not matching.conserved(rows, flow):  # checked before the child exists: a refusal writes nothing
            raise _refuse("entry_id", "fee_quantum", f"principal {principal} + fee {-fee} vs line {flow}")
        return line, entry, fee

    def apply(self, ctx: Context, params: AttachFeeParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        line, entry, fee = self._plan(ctx, params)
        ews.write_children(db, entry, fee=ChildIn(amount=fee, name=ews.FX_FEE_CHILD_NAME), discount=None,
                           source=STATEMENT_SOURCE)
        child = db.execute(select(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id,
                                                     LedgerEntry.name == ews.FX_FEE_CHILD_NAME)
                           .order_by(LedgerEntry.id.desc()).limit(1)).scalar_one()
        effect.coverage_ids = _claim(ctx, line, [(entry, "principal"), (child, "child")], self.name)
        effect.created_entry_ids = [child.id]
        effect.touched_entry_ids = [entry.id]
        effect.inverse = {"child_id": child.id}

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        child_id = action.inverse["child_id"]
        if ctx.db.get(LedgerEntry, child_id) is None:
            raise NotRevertable(f"fee {child_id} is gone")
        _release(ctx, action)
        ews.delete_entries_cascade(ctx.db, [child_id])
        return Effect(deleted_entry_ids=[child_id], touched_entry_ids=list(action.touched_entry_ids or ()))


# --- adjust_amount ------------------------------------------------------------------------------------------------

class AdjustAmountParams(ActionParams):
    entry_id: Int32
    line_id: Int32
    group_id: Int32 | None = None  # a foreign group case: the split group the case names; entry_id is the member rewritten


RESTORED = ("amount", "original_amount", "original_currency", "fx_rate", "fx_source", "description")
_DECIMALS = ("amount", "original_amount", "fx_rate")


class AdjustAmountAction(ActionBase):
    """Rewrites a standalone, unprotected candidate to the printed amount (and, when the line prints one, the
    foreign pair and the rate it implies); the old amount is noted in the description. For a foreign group case
    (`group_id`, 2026-10-19) the named member absorbs the residual instead — its foreign amount stays, the rate it
    implies is rewritten — and the whole group is claimed."""
    name = "adjust_amount"
    Params = AdjustAmountParams
    case_kinds = frozenset({"amount_delta"})
    slot = AMOUNT

    @staticmethod
    def _scoped(ctx: Context, params: AdjustAmountParams) -> bool:
        candidates = ctx.case.candidates or []
        if params.group_id is not None:
            return params.group_id in {c.get("group_id") for c in candidates}
        return params.entry_id in {c.get("entry_id") for c in candidates}

    def lock_targets(self, ctx: Context, params: AdjustAmountParams) -> LockSet:
        if not self._scoped(ctx, params):
            return LockSet()
        return lockset_for(ctx.db, entry_ids=(params.entry_id,),
                           group_ids=() if params.group_id is None else (params.group_id,))

    def validate_before(self, ctx: Context, params: AdjustAmountParams, prepared: Prepared, before: dict) -> None:
        self._plan(ctx, params)

    def _plan(self, ctx: Context, params: AdjustAmountParams) -> tuple[StatementLine, LedgerEntry, dict, list[LedgerEntry]]:
        """The line, the entry rewritten, its new fields and the group members claimed beside it ([] standalone)."""
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        line = _open_line(ctx, case_line(ctx, params.line_id))
        entry = _gate(ctx, db.get(LedgerEntry, params.entry_id), "entry_id", params.entry_id)
        ews.assert_entry_editable(db, entry)
        reason = protected_reason(db, entry)
        if reason is not None:
            raise CodedConflictError("protected", f"entry {entry.id}: {reason}")
        if entry.parent_entry_id is not None or any(schedule_rows(db, [entry.id])):
            raise _refuse("entry_id", "not_standalone", f"entry {entry.id}")
        members: list[LedgerEntry] = []
        if params.group_id is None:
            if entry.group_id is not None:
                raise _refuse("entry_id", "not_standalone", f"entry {entry.id}")
        else:
            if entry.group_id != params.group_id:
                raise _refuse("entry_id", "not_member", f"entry {entry.id} is not in group {params.group_id}")
            members = list(db.execute(select(LedgerEntry).where(LedgerEntry.group_id == params.group_id,
                                                                LedgerEntry.parent_entry_id.is_(None))
                                      .order_by(LedgerEntry.id)).scalars())
            for member in members:
                _gate(ctx, member, "group_id", member.id)
        if ews.has_settlements_or_refunds(db, entry.id):
            raise CodedConflictError("has_settlements", f"entry {entry.id}")
        if entry.original_currency is not None and line.foreign_amount is None:
            raise _refuse("line_id", "fx_pair_required", f"entry {entry.id} is in {entry.original_currency}")
        if (line.foreign_amount is None) != (line.foreign_currency is None):
            raise _refuse("line_id", "fx_pair_required", f"line {line.id} prints half a foreign pair")
        flow = Decimal(line.flow_amount)
        if members:
            # the named member takes the residual of the whole group; the other members are left as the owner wrote them
            others = sum((Decimal(m.amount) for m in members if m.id != entry.id), Decimal(0))
            amount = flow - others
            expected = ews.signed(entry.kind, abs(amount)) if entry.kind in ews.SIGN_BY_KIND else amount
            if amount == 0 or expected != amount:
                raise _refuse("entry_id", "kind_sign", f"{entry.kind} cannot carry a flow of {amount}")
            new = {"amount": amount}
            if entry.original_amount is not None:
                foreign = abs(Decimal(entry.original_amount))
                if foreign == 0:
                    raise _refuse("entry_id", "foreign_zero", f"entry {entry.id}")
                new |= {"fx_rate": (abs(amount) / foreign).quantize(ews.RATE_QUANTUM), "fx_source": ADJUST_FX_SOURCE}
            return line, entry, new, members
        amount = ews.signed(entry.kind, abs(flow)) if entry.kind in ews.SIGN_BY_KIND else flow
        if amount != flow:
            raise _refuse("entry_id", "kind_sign", f"{entry.kind} cannot carry a flow of {flow}")
        new = {"amount": amount}
        if line.foreign_amount is not None:
            foreign = abs(Decimal(line.foreign_amount))
            if foreign == 0:
                raise _refuse("line_id", "foreign_zero", f"line {line.id}")
            original = foreign if amount > 0 else -foreign
            new |= {"original_amount": original, "original_currency": line.foreign_currency,
                    "fx_rate": (abs(amount) / foreign).quantize(ews.RATE_QUANTUM), "fx_source": ADJUST_FX_SOURCE}
        return line, entry, new, members

    def apply(self, ctx: Context, params: AdjustAmountParams, prepared: Prepared, effect: Effect) -> None:
        line, entry, new, members = self._plan(ctx, params)
        restore = {key: getattr(entry, key) for key in RESTORED}
        note = f"對帳調整 原金額 {ews._plain(entry.amount)}"
        for key, value in new.items():
            setattr(entry, key, value)
        entry.description = note if not entry.description else f"{entry.description}\n{note}"
        ctx.db.flush()
        if members:
            effect.coverage_ids = _claim(ctx, line, [(m, "member") for m in members], self.name, group_id=params.group_id)
            effect.touched_group_ids = [params.group_id]
        else:
            effect.coverage_ids = _claim(ctx, line, [(entry, "principal")], self.name)
        effect.touched_entry_ids = [entry.id]
        effect.inverse = {"restore": {k: (str(v) if k in _DECIMALS and v is not None else v)
                                      for k, v in restore.items()}}

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        entry = ctx.db.get(LedgerEntry, action.params["entry_id"])
        if entry is None:
            raise NotRevertable(f"entry {action.params['entry_id']} is gone")
        _release(ctx, action)
        for key, value in action.inverse["restore"].items():
            setattr(entry, key, Decimal(value) if key in _DECIMALS and value is not None else value)
        ctx.db.flush()
        return Effect(touched_entry_ids=[entry.id])


BACKFILL_KINDS = {("card", "purchase"): "expense", ("bank", "withdrawal"): "expense", ("bank", "purchase"): "expense",
                  ("bank", "deposit"): "income"}


def backfill_kind(statement_kind: str, line_kind: str) -> str | None:
    """R6b.1: the entry kind a statement line is backfilled as, or None when the line is never backfilled."""
    return BACKFILL_KINDS.get((statement_kind, line_kind))


class BackfillEntryParams(ActionParams):
    kind: Literal["expense", "income"]


class BackfillEntryAction(_TwinGuarded):
    """R6b.1: books an unmatched purchase/withdrawal/deposit line as a stand-in (category 對帳待分類, needs_review)."""
    name = "backfill_entry"
    Params = BackfillEntryParams
    case_kinds = frozenset({"line_unmatched"})
    actors = frozenset({OWNER, POLICY})

    def _check(self, ctx: Context, params: BackfillEntryParams) -> StatementLine:
        line = _open_line(ctx)
        if backfill_kind(ctx.statement.kind, line.line_kind) != params.kind:
            raise _refuse("kind", "kind_sign", f"{line.line_kind} on a {ctx.statement.kind} statement is not backfilled as {params.kind}")
        return line

    def validate_before(self, ctx: Context, params: BackfillEntryParams, prepared: Prepared, before: dict) -> None:
        self._twin_check(ctx, self._check(ctx, params))

    def apply(self, ctx: Context, params: BackfillEntryParams, prepared: Prepared, effect: Effect) -> None:
        line = self._check(ctx, params)
        self._book(ctx, line, effect, kind=params.kind, category_id=ews.backfill_category_id(ctx.db, params.kind),
                   needs_review=True)


# --- replace_stand_in (R6b.5) ----------------------------------------------------------------------------------

REPLACE = "replace"
STAND_IN_FIELDS = ("account_id", "currency", "kind", "amount", "entry_date", "posted_date")
REPLACE_HINTS = {"shadowed_by_stand_in": "shadow", "orphaned_stand_in": "orphan"}


class ReplaceStandInParams(ActionParams):
    pass


class ReplaceStandInAction(ActionBase):
    """R6b.5: the owner's entry takes the line its stand-in held (shadow) or has already taken it (orphan); the
    stand-in is deleted and its backfill action reverted. Everything is derived from case.event_id/case.entry_id
    and re-proven under the locks. Not revertable.

    Safety: the creating action row is read FOR UPDATE inside `_prove` (after the entry locks; an action row, not an
    entry or statement, so the engine's lock order is not involved) and its case's statement is proven to be
    `ctx.statement`, which the engine already holds — so a concurrent sweep retirement or engine revert of that action
    waits on this statement lock, and vice versa."""
    name = "replace_stand_in"
    Params = ReplaceStandInParams
    case_kinds = frozenset({"entry_unmatched"})
    actors = frozenset({OWNER, POLICY})
    slot = REPLACE

    def _shape(self, ctx: Context, *, discovery: bool) -> dict:
        """The evidence {shape, stand_in_id, other_id, action_id, event_id}. `discovery=True` (unlocked, once): a
        missing orphan claimant is `no_claimant`. `discovery=False` (under the locks): a missing claimant means the
        targets moved since discovery → `claimant_changed` (R1)."""
        context = ctx.case.context or {}
        hint = context.get("hint")
        if hint == "shadowed_by_stand_in":
            stand_in, other = context.get("stand_in_entry_id"), ctx.case.entry_id
        elif hint == "orphaned_stand_in":
            stand_in, other = ctx.case.entry_id, self._claimant(ctx)
            if other is None:
                raise _refuse("action", "no_claimant" if discovery else "claimant_changed",
                              "the orphan's line has no owner claim")
        else:
            raise _refuse("action", "not_a_stand_in_case")
        action = stand_ins.creation_for_entry(ctx.db, stand_in) if stand_in else None
        return {"shape": REPLACE_HINTS[hint], "stand_in_id": stand_in, "other_id": other,
                "action_id": None if action is None else action.id, "event_id": ctx.case.event_id}

    def _claimant(self, ctx: Context) -> int | None:
        """The entry holding the case event's line on this statement now (active coverage), or None."""
        for rows in coverage_service.active_rows(ctx.db, ctx.statement.id).values():
            for r in rows:
                if r.event_id == ctx.case.event_id and r.entry_id is not None:
                    return r.entry_id
        return None

    @staticmethod
    def _target_ids(found: dict) -> list[int]:
        return sorted({i for i in (found["stand_in_id"], found["other_id"]) if i})

    def lock_targets(self, ctx: Context, params: ReplaceStandInParams) -> LockSet:
        found = self._shape(ctx, discovery=True)
        ctx._replace_discovery = found  # the single unlocked discovery read; prepare freezes it (R1)
        return LockSet(entry_ids=tuple(self._target_ids(found)))

    def prepare(self, ctx: Context, params: ReplaceStandInParams) -> Prepared:
        """Engine interface unchanged (`prepare(ctx, params)`). The engine calls lock_targets first (the ONE unlocked
        discovery, stored on ctx._replace_discovery) and then prepare; prepare does NOT call the base implementation
        (which would call lock_targets again and rediscover) — it builds the Prepared from the stored discovery's ids
        directly (R1)."""
        frozen = ctx._replace_discovery
        if frozen is None:  # never: the engine always calls lock_targets first; guard against a direct caller
            raise _refuse("action", "claimant_changed", "prepare called without a discovery")
        ids = self._target_ids(frozen)
        return Prepared(expected=snapshots.expected_set(ctx.db, tuple(ids), ()),
                        data={**frozen, "locked_entry_ids": ids})

    def validate_before(self, ctx: Context, params: ReplaceStandInParams, prepared: Prepared, before: dict) -> None:
        self._prove(ctx, prepared)

    def _prove(self, ctx: Context, prepared: Prepared):
        """M2/R1: under the statement, case and entry locks — (1) the frozen discovery must name exactly the entry ids
        the engine locked (`ctx.lock_set.entry_ids`) and snapshotted (`prepared.expected["entries"]`); (2) the
        re-derived evidence must equal the frozen one; (3) then the structural allowlist, the provenance (creating
        action read FOR UPDATE, this event, this statement), the claimant's gate and the coverage are proved. Error
        precedence (S3): a stand-in that no longer exists → `stand_in_changed` first (the engine's `expected_set` omits
        a missing row, so the set-equality check would otherwise report it as `claimant_changed`); then the lock-set
        binding; then the re-derived evidence; then the structural/provenance/claimant/coverage proofs."""
        db = ctx.db
        frozen = {k: v for k, v in prepared.data.items() if k != "locked_entry_ids"}
        if frozen["stand_in_id"] is None or db.get(LedgerEntry, frozen["stand_in_id"]) is None:  # S3: first
            raise _refuse("action", "stand_in_changed", "the stand-in is gone")
        locked_ids = sorted(ctx.lock_set.entry_ids) if ctx.lock_set else []
        if prepared.data.get("locked_entry_ids") != locked_ids \
                or set(map(str, locked_ids)) != set(prepared.expected.get("entries", {})):
            raise _refuse("action", "claimant_changed", "the locked rows are not the discovered targets")
        now = self._shape(ctx, discovery=False)  # re-derived under the locks (the orphan claimant from current coverage)
        if now != frozen:
            raise _refuse("action", "claimant_changed", "the replacement targets changed since discovery")
        shape, stand_in_id, other_id, action_id = now["shape"], now["stand_in_id"], now["other_id"], now["action_id"]
        stand_in = db.get(LedgerEntry, stand_in_id)
        action = None if action_id is None else db.execute(
            select(ReconciliationAction).where(ReconciliationAction.id == action_id).with_for_update()
            .execution_options(populate_existing=True)).scalar_one_or_none()
        if stand_in is None or stand_in.source != STATEMENT_SOURCE or action is None or action.status != "applied" \
                or action.action not in stand_ins.BACKFILL_ACTIONS or action.event_id != ctx.case.event_id \
                or db.get(ReconciliationCase, action.case_id).statement_id != ctx.statement.id:
            raise _refuse("action", "stand_in_changed", "the stand-in is gone, not a backfilled entry, or not this event's")
        created = ((action.after or {}).get("entries") or {}).get(str(stand_in_id)) or {}
        current = snapshots.snapshot_set(db, [stand_in_id], [])["entries"][str(stand_in_id)]
        if any(created.get(k) != current.get(k) for k in STAND_IN_FIELDS) or current.get("child_ids") \
                or current.get("refund_ids") or current.get("settlement_ids") or current.get("reward_ids") \
                or stand_in.transfer_group_id is not None:
            raise _refuse("action", "stand_in_changed", "the stand-in was edited structurally")
        line = line_for_event(db, ctx.case, ctx.statement)  # the event's line on the current revision
        if line is None:
            raise _refuse("case", "line_not_printed", f"case {ctx.case.id}")
        held = list(coverage_service.active_rows(db, ctx.statement.id).get(line.id, ()))
        other = db.get(LedgerEntry, other_id)
        accounts = coverage_service.participating_accounts(db, ctx.statement.account_id)
        bad = "claimant_changed" if shape == "orphan" else "owner_entry_taken"
        if other is None or other.account_id not in accounts or other.currency != ctx.statement.currency \
                or other.parent_entry_id is not None or other.id in stand_ins.stand_in_entry_ids(db, accounts):
            raise _refuse("action", bad, "the claimant is not an eligible owner entry of this statement's accounts")
        if not stand_ins._conserves(ctx.statement, line, other, ctx.rules):
            raise _refuse("action", bad, "the claimant no longer conserves the line within the drift window")
        anywhere = coverage_service.claimed_entry_ids(db)
        if shape == "shadow":
            if not held or any(r.entry_id != stand_in_id for r in held):
                raise _refuse("action", "stand_in_coverage_gone")
            if other_id in anywhere:
                raise _refuse("action", "owner_entry_taken")
        elif not held or any(r.entry_id != other_id for r in held) or stand_in_id in anywhere:
            raise _refuse("action", "claimant_changed")  # the orphan: the owner holds the line, the stand-in nothing
        return shape, stand_in, other, line, held, action

    def apply(self, ctx: Context, params: ReplaceStandInParams, prepared: Prepared, effect: Effect) -> None:
        shape, stand_in, other, line, held, action = self._prove(ctx, prepared)
        db = ctx.db
        stand_in_id, other_id, action_id = stand_in.id, other.id, action.id  # ids first: the delete expires the session
        effect.before = {**effect.before, "stand_in_entry_id": stand_in_id, "backfill_action_id": action_id,
                         "claimant_entry_id": other_id, "shape": shape}
        if shape == "shadow":
            coverage_service.release_rows(db, [r.id for r in held], reason="replaced")
            effect.coverage_ids = _claim(ctx, line, [(other, "principal")], self.name)
        effect.touched_entry_ids = [other_id]
        ews.delete_entries_cascade(db, [stand_in_id])
        action.status = "reverted"  # reverted_by_action_id is set by the engine once this action's row is recorded
        effect.deleted_entry_ids = [stand_in_id]
        effect.inverse = {"stand_in": prepared.expected["entries"].get(str(stand_in_id)), "backfill_action_id": action_id}
        effect.case_context = {"replaced_stand_in": stand_in_id}
        audit.record(db, event="stand_in_replaced", actor=ctx.actor, statement_id=ctx.statement.id, case_id=ctx.case.id,
                     action_id=None, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"stand_in_entry_id": stand_in_id, "backfill_action_id": action_id,
                             "claimant_entry_id": other_id, "shape": shape})

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        raise NotRevertable("stand-in deleted")


register(CreateEntryAction())
register(CreateSystemEntryAction())
register(CreateRefundAction())
register(AttachFeeAction())
register(AdjustAmountAction())
register(BackfillEntryAction())
register(ReplaceStandInAction())
