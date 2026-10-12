"""Ledger-writing actions (R1c; spec §4.9 action table, C6, C8; rulings 7, 18, 20, 25, 31): `create_entry`,
`create_system_entry` and `create_refund` book the case's statement line as a new row (effect slot `create`, one
creation per event whichever action made it), `attach_fee` books the foreign-transaction fee a bundled card line
carries as a child of the purchase (slot `fee`), `adjust_amount` rewrites a near entry to the printed amount (slot
`amount`). Each claims the line on what it wrote (`match_kind` manual, `auto` when policy applies it;
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

from app.models import Account, LedgerEntry, ReconciliationAction, StatementLine
from app.schemas.writes import ChildIn, Int32, RefundIn, ShortText
from app.services import coverage_service, settlement_service
from app.services import entry_write_service as ews
from app.services.errors import CodedConflictError, CodedValidationError
from app.services.split_protection import protected_reason
from app.services.statements import matching

from . import register
from .base import (
    OWNER, POLICY, ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, lockset_for,
    schedule_rows,
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
        return "agent" if ctx.proposal_id is not None else "auto"  # ruling 31
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


def _claim(ctx: Context, line: StatementLine, picked: list[tuple[LedgerEntry, str]], rule: str) -> list[int]:
    rows = [matching.Row(entry.id, role, Decimal(entry.amount)) for entry, role in picked]
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


class CreateSystemEntryAction(_Creation):
    name = "create_system_entry"
    Params = CreateSystemEntryParams
    case_kinds = frozenset({"line_unmatched"})
    actors = frozenset({OWNER, POLICY})

    def validate_before(self, ctx: Context, params: CreateSystemEntryParams, prepared: Prepared,
                        before: dict) -> None:
        _open_line(ctx)

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


RESTORED = ("amount", "original_amount", "original_currency", "fx_rate", "fx_source", "description")
_DECIMALS = ("amount", "original_amount", "fx_rate")


class AdjustAmountAction(ActionBase):
    """Rewrites a standalone, unprotected candidate to the printed amount (and, when the line prints one, the
    foreign pair and the rate it implies); the old amount is noted in the description."""
    name = "adjust_amount"
    Params = AdjustAmountParams
    case_kinds = frozenset({"amount_delta"})
    slot = AMOUNT

    @staticmethod
    def _scoped(ctx: Context, params: AdjustAmountParams) -> bool:
        return params.entry_id in {c.get("entry_id") for c in (ctx.case.candidates or [])}

    def lock_targets(self, ctx: Context, params: AdjustAmountParams) -> LockSet:
        return lockset_for(ctx.db, entry_ids=(params.entry_id,)) if self._scoped(ctx, params) else LockSet()

    def validate_before(self, ctx: Context, params: AdjustAmountParams, prepared: Prepared, before: dict) -> None:
        self._plan(ctx, params)

    def _plan(self, ctx: Context, params: AdjustAmountParams) -> tuple[StatementLine, LedgerEntry, dict]:
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        line = _open_line(ctx, case_line(ctx, params.line_id))
        entry = _gate(ctx, db.get(LedgerEntry, params.entry_id), "entry_id", params.entry_id)
        ews.assert_entry_editable(db, entry)
        reason = protected_reason(db, entry)
        if reason is not None:
            raise CodedConflictError("protected", f"entry {entry.id}: {reason}")
        if entry.group_id is not None or entry.parent_entry_id is not None or any(schedule_rows(db, [entry.id])):
            raise _refuse("entry_id", "not_standalone", f"entry {entry.id}")
        if ews.has_settlements_or_refunds(db, entry.id):
            raise CodedConflictError("has_settlements", f"entry {entry.id}")
        if entry.original_currency is not None and line.foreign_amount is None:
            raise _refuse("line_id", "fx_pair_required", f"entry {entry.id} is in {entry.original_currency}")
        flow = Decimal(line.flow_amount)
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
        return line, entry, new

    def apply(self, ctx: Context, params: AdjustAmountParams, prepared: Prepared, effect: Effect) -> None:
        line, entry, new = self._plan(ctx, params)
        restore = {key: getattr(entry, key) for key in RESTORED}
        note = f"對帳調整 原金額 {ews._plain(entry.amount)}"
        for key, value in new.items():
            setattr(entry, key, value)
        entry.description = note if not entry.description else f"{entry.description}\n{note}"
        ctx.db.flush()
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


register(CreateEntryAction())
register(CreateSystemEntryAction())
register(CreateRefundAction())
register(AttachFeeAction())
register(AdjustAmountAction())
