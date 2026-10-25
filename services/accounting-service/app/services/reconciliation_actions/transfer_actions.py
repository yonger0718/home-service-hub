"""Transfer reconciliation actions (R6c; spec §5.1, §5.2): `adjust_transfer` rewrites an owner's transfer to the
statement figures across both statements (slot `amount`). Task 4 adds `pair_transfer` here; both share the
peer-statement discovery and the both-statement lifecycle helpers below.

Lifecycle (§5.1): discovery runs unlocked in `lock_targets` (the peer leg, the peer statement(s), the complementary
peer line) and is frozen on the context for `prepare`; `validate_before` re-derives it under the engine's locks and
refuses 409 `candidate_changed` on any difference. `apply` writes the legs directly (never
`transfer_service.update_transfer`, which would copy one date to both legs, drop children and force `source`), claims
this line and the peer line, resolves the peer line's case with this action row, then reconciles both statements.
`invert` restores the legs, releases the claims, reopens the peer case and this case, and reconciles again.

The peer-busy predicate is a local copy of `policy_service.statement_excluded` (that module imports the engine)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal,
    StatementCoverage, StatementLine,
)
from app.schemas.writes import Int32
from app.services import coverage_service
from app.services import entry_write_service as ews
from app.services.errors import CodedConflictError
from app.services.statements import matching, retired

from . import OPEN_STATUSES, audit, register, snapshots
from .base import OWNER, ActionBase, ActionParams, Context, Effect, LockSet, Prepared, lockset_for, schedule_rows
from .coverage_actions import _line_claimed as line_claimed
from .coverage_actions import case_line
from .ledger_actions import AMOUNT, _gate, _match_kind, _open_line, _refuse, _release
from .structural_actions import _reconcile_keeping_case, _reopen

ADJUST, PAIR = "adjust_transfer", "pair_transfer"
PEER_LINE_KINDS_FOR_ADJUST = frozenset({"payment", "transfer_in", "transfer_out", "withdrawal", "deposit"})
BANK_ONLY_PEER_KINDS = PEER_LINE_KINDS_FOR_ADJUST - matching.PEER_LINE_KINDS  # «BC 7»: 提款 / 轉帳支出 on a bank
BUSY_CASES = ("parse_review", "statement_conflict")  # = policy_service.EXCLUDING_CASES
LINE_CASE_KINDS = ("line_unmatched", "ambiguous", "amount_delta")  # the matcher's cases on a line
CANDIDATE_CHANGED = "candidate_changed"


# --- shared helpers -------------------------------------------------------------------------------------------------

def peer_leg(db: Session, leg: LedgerEntry) -> LedgerEntry | None:
    """The other top-level entry of the leg's transfer group (None unless there is exactly one)."""
    if leg.transfer_group_id is None:
        return None
    others = db.execute(select(LedgerEntry).where(
        LedgerEntry.transfer_group_id == leg.transfer_group_id, LedgerEntry.id != leg.id,
        LedgerEntry.parent_entry_id.is_(None)).order_by(LedgerEntry.id)).scalars().all()
    return others[0] if len(others) == 1 else None


def family_root(db: Session, account_id: int) -> int:
    """The account whose statements print this account's rows: its combined parent, else itself."""
    account = db.get(Account, account_id)
    return account.combined_account_id if account is not None and account.combined_account_id else account_id


def peer_statements(db: Session, account_id: int, currency: str, start: date, end: date) -> list[AccountStatement]:
    """Live, non-retired statements of `participating_accounts(account_id)` in `currency` whose period intersects
    [start, end], ascending id."""
    accounts = coverage_service.participating_accounts(db, account_id)
    return list(db.execute(select(AccountStatement).where(
        AccountStatement.account_id.in_(accounts), AccountStatement.currency == currency,
        AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
        AccountStatement.period_start <= end, AccountStatement.period_end >= start,
        ~retired.clause(AccountStatement.id)).order_by(AccountStatement.id)).scalars())


def complementary_lines(db: Session, statements: list[AccountStatement], line: StatementLine, *,
                        kinds: frozenset[str], window_days: int) -> list[tuple[AccountStatement, StatementLine]]:
    """Current-revision lines of `statements` of a kind in `kinds`, flow == −line.flow, posted within `window_days`
    of `line`, with no active coverage; ordered by (day distance, line id)."""
    by_revision = {s.current_revision_id: s for s in statements if s.current_revision_id is not None}
    if not by_revision:
        return []
    covered = exists().where(StatementCoverage.line_id == StatementLine.id, StatementCoverage.status == "active")
    rows = db.execute(select(StatementLine).where(
        StatementLine.revision_id.in_(sorted(by_revision)), StatementLine.line_kind.in_(sorted(kinds)),
        StatementLine.flow_amount == -Decimal(line.flow_amount), ~covered)).scalars().all()
    distance = lambda other: abs((other.posted_date - line.posted_date).days)  # noqa: E731
    found = [(by_revision[other.revision_id], other) for other in rows if distance(other) <= window_days]
    return sorted(found, key=lambda pair: (distance(pair[1]), pair[1].id))


def peer_statement_refusal(statement: AccountStatement, db: Session) -> str | None:
    """`peer_not_live` (historical: never written on), `peer_statement_busy` (conflict, recheck pending, or an open
    parse_review / statement_conflict case — `policy_service.statement_excluded`'s predicate), else None."""
    if statement.mode != "live":
        return "peer_not_live"
    if statement.conflict_open or statement.needs_recheck:
        return "peer_statement_busy"
    busy = db.execute(select(exists().where(
        ReconciliationCase.statement_id == statement.id, ReconciliationCase.kind.in_(BUSY_CASES),
        ReconciliationCase.status.in_(OPEN_STATUSES)))).scalar_one()
    return "peer_statement_busy" if busy else None


def resolve_peer_case(db: Session, ctx: Context, peer_statement: AccountStatement,
                      peer_line: StatementLine) -> int | None:
    """§5.1 step 5: the peer line's open/proposed matcher case is resolved by this action row (`explanation` is an
    enum, so the provenance goes in `note`); its pending proposals are superseded (`by: peer_resolved`)."""
    if ctx.action_id is None:
        raise RuntimeError("resolve_peer_case needs the action row (Context.action_id)")
    case = db.execute(select(ReconciliationCase).where(
        ReconciliationCase.statement_id == peer_statement.id, ReconciliationCase.event_id == peer_line.event_id,
        ReconciliationCase.kind.in_(LINE_CASE_KINDS), ReconciliationCase.status.in_(OPEN_STATUSES))
        .order_by(ReconciliationCase.id).limit(1)).scalar_one_or_none()
    if case is None:
        return None
    action = db.get(ReconciliationAction, ctx.action_id).action
    case.status, case.explanation, case.note = "resolved", None, f"{action} from case {ctx.case.id}"
    case.resolved_by, case.resolved_action_id, case.resolved_at = ctx.actor, ctx.action_id, func.now()
    pending = db.execute(select(ReconciliationProposal).where(
        ReconciliationProposal.case_id == case.id, ReconciliationProposal.status == "pending")
        .order_by(ReconciliationProposal.id)).scalars().all()
    for proposal in pending:
        proposal.status, proposal.decided_at = "superseded", func.now()
        audit.record(db, event="proposal_superseded", actor=ctx.label, statement_id=peer_statement.id, case_id=case.id,
                     action_id=ctx.action_id, run_id=ctx.run_id, request_id=ctx.request_id,
                     detail={"proposal_id": proposal.id, "by": "peer_resolved"})
    db.flush()
    return case.id


def reopen_peer_case(db: Session, action: ReconciliationAction) -> None:
    """The stored peer case row, reopened while it is still resolved by this action."""
    case_id = (action.inverse or {}).get("peer_case_id")
    case = None if case_id is None else db.get(ReconciliationCase, case_id)
    if case is not None and case.status == "resolved" and case.resolved_action_id == action.id:
        _reopen(case)
        db.flush()


def claim_on(db: Session, ctx: Context, statement: AccountStatement, line: StatementLine, entry: LedgerEntry,
             rule: str) -> list[int]:
    """One principal claim of `entry` on `line` of `statement` (the case's or the peer's); the coverage ids."""
    written = coverage_service.write_claim(db, statement, line, [matching.Row(entry.id, "principal", Decimal(entry.amount))],
                                           entries_by_id={entry.id: entry}, match_kind=_match_kind(ctx),
                                           match_rule=rule, run_id=ctx.run_id)
    return [c.id for c in written]


def _has_children(db: Session, entry_id: int) -> bool:
    return db.execute(select(exists().where(LedgerEntry.parent_entry_id == entry_id))).scalar_one()


# --- adjust_transfer (§5.2) -----------------------------------------------------------------------------------------

class AdjustTransferParams(ActionParams):
    entry_id: Int32
    line_id: Int32


@dataclass
class _Plan:
    line: StatementLine
    leg: LedgerEntry
    peer: LedgerEntry
    peer_statement: AccountStatement | None
    peer_line: StatementLine | None
    statement_ids: list[int]


_FROZEN = ("peer_leg_id", "peer_statement_ids", "peer_line_id")


class AdjustTransferAction(ActionBase):
    """The owner's transfer differs from the printed amount (or date): both legs take `±|line.flow|`, this leg the
    line's dates, the peer leg the peer line's dates when the peer statement prints one (else its own), both a
    `對帳調整` note; `source`, `needs_review`, group, category and rule links stay. Claims: this line and the peer line.
    The peer line's case resolves with this row; both legs' `entry_unmatched` cases go in the reconcile."""
    name = ADJUST
    Params = AdjustTransferParams
    case_kinds = frozenset({"amount_delta", "ambiguous"})
    actors = frozenset({OWNER})
    slot = AMOUNT

    @staticmethod
    def _scoped(ctx: Context, params: AdjustTransferParams) -> bool:
        return params.entry_id in {c.get("entry_id") for c in ctx.case.candidates or []}

    def _discover(self, ctx: Context, params: AdjustTransferParams) -> dict:
        """The peer leg, the statements to lock (the live one holding the peer leg's posted date ∪ the peer line's)
        and the complementary peer line(s) — read as they are (unlocked in `lock_targets`, locked in
        `validate_before`). «MR 4»: the line may sit up to `drift_window_days` away, on the next monthly statement."""
        db = ctx.db
        found = {"leg_id": None, "peer_leg_id": None, "peer_statement_ids": [], "peer_line_id": None, "peer_line_ids": []}
        leg = db.get(LedgerEntry, params.entry_id) if self._scoped(ctx, params) else None
        peer = None if leg is None else peer_leg(db, leg)
        found["leg_id"] = None if leg is None else leg.id
        if peer is None:
            return found
        found["peer_leg_id"] = peer.id
        line = db.get(StatementLine, params.line_id)  # `case_line` proves it belongs to the case, under the locks
        window = ctx.rules.drift_window_days
        start = peer.posted_date if line is None else min(peer.posted_date, line.posted_date - timedelta(days=window))
        end = peer.posted_date if line is None else max(peer.posted_date, line.posted_date + timedelta(days=window))
        statements = peer_statements(db, family_root(db, peer.account_id), peer.currency, start, end)
        lines = [] if line is None else [
            (s, other) for s, other in complementary_lines(db, statements, line, kinds=PEER_LINE_KINDS_FOR_ADJUST,
                                                           window_days=window)
            if other.line_kind not in BANK_ONLY_PEER_KINDS or s.kind == "bank"]
        ids = {s.id for s in statements if s.period_start <= peer.posted_date <= s.period_end}
        if len(lines) == 1:
            ids.add(lines[0][0].id)
            found["peer_line_id"] = lines[0][1].id
        found["peer_statement_ids"] = sorted(ids)
        found["peer_line_ids"] = [other.id for _, other in lines]
        return found

    @staticmethod
    def _leg_ids(found: dict) -> tuple[int, ...]:
        return tuple(i for i in (found["leg_id"], found["peer_leg_id"]) if i is not None)

    def lock_targets(self, ctx: Context, params: AdjustTransferParams) -> LockSet:
        found = self._discover(ctx, params)
        ctx._transfer_discovery = found  # the one unlocked discovery; prepare freezes it
        legs = self._leg_ids(found)
        return lockset_for(ctx.db, entry_ids=legs, statement_ids=found["peer_statement_ids"]) if legs else LockSet()

    def prepare(self, ctx: Context, params: AdjustTransferParams) -> Prepared:
        found = ctx._transfer_discovery if ctx._transfer_discovery is not None else self._discover(ctx, params)
        targets = lockset_for(ctx.db, entry_ids=self._leg_ids(found))
        return Prepared(expected=snapshots.expected_set(ctx.db, targets.entry_ids, targets.group_ids),
                        data={key: found[key] for key in _FROZEN})

    def validate_before(self, ctx: Context, params: AdjustTransferParams, prepared: Prepared, before: dict) -> None:
        found = self._discover(ctx, params)
        if ({key: found[key] for key in _FROZEN} != prepared.data
                or tuple(found["peer_statement_ids"]) != tuple(prepared.lock_set.statement_ids)):
            raise CodedConflictError(CANDIDATE_CHANGED, f"discovered {prepared.data}, now {({k: found[k] for k in _FROZEN})}")
        self._plan(ctx, params, found)
        ctx._transfer_discovery = found  # the locked, verified discovery: `apply` consumes exactly this one

    @staticmethod
    def _verified(ctx: Context) -> dict:
        """The discovery `validate_before` proved under the locks; its statements must be the ones the engine
        locked (a rediscovery could reach an unlocked window statement)."""
        found = ctx._transfer_discovery
        if found is None:  # never: the engine runs validate_before first on this context
            raise RuntimeError("adjust_transfer apply without a verified discovery")
        locked = tuple(ctx.lock_set.statement_ids) if ctx.lock_set is not None else ()
        if tuple(found["peer_statement_ids"]) != locked:
            raise CodedConflictError(CANDIDATE_CHANGED, f"statements {found['peer_statement_ids']}, locked {list(locked)}")
        return found

    def _plan(self, ctx: Context, params: AdjustTransferParams, found: dict) -> _Plan:
        db = ctx.db
        if not self._scoped(ctx, params):
            raise _refuse("entry_id", "entry_out_of_scope", f"entry {params.entry_id}")
        line = _open_line(ctx, case_line(ctx, params.line_id))
        leg = _gate(ctx, db.get(LedgerEntry, params.entry_id), "entry_id", params.entry_id)
        peer = peer_leg(db, leg)
        if peer is None:
            raise _refuse("entry_id", "not_standalone", f"entry {leg.id} is not a leg of a two-leg transfer")
        legs = (leg, peer)
        for entry in legs:
            if (entry.parent_entry_id is not None or entry.group_id is not None or _has_children(db, entry.id)
                    or any(schedule_rows(db, [entry.id]))):
                raise _refuse("entry_id", "not_standalone", f"entry {entry.id}")
        for entry in legs:
            ews.assert_entry_editable(db, entry)
        for entry in legs:
            if ews.has_settlements_or_refunds(db, entry.id):
                raise CodedConflictError("has_settlements", f"entry {entry.id}")
        if peer.currency != leg.currency or leg.currency != ctx.statement.currency:
            raise _refuse("entry_id", "cross_currency", f"legs {leg.id}, {peer.id}")
        if line.foreign_amount is not None or line.foreign_currency is not None \
                or any(entry.original_currency is not None for entry in legs):
            raise _refuse("line_id", "fx_pair_required", f"line {line.id}")
        incoming = Decimal(line.flow_amount) > 0
        if (leg.kind, peer.kind) != (("transfer_in", "transfer_out") if incoming else ("transfer_out", "transfer_in")):
            raise _refuse("entry_id", "kind_sign", f"{leg.kind} on a flow of {'+' if incoming else '-'}")
        if ctx.statement.kind == "card" and line.line_kind == "payment" and db.get(Account, peer.account_id).is_credit:
            raise _refuse("entry_id", "peer_is_card", f"entry {peer.id}")
        statements = [db.get(AccountStatement, sid) for sid in found["peer_statement_ids"]]
        for statement in statements:
            code = peer_statement_refusal(statement, db)
            if code is not None:
                raise _refuse("entry_id", code, f"statement {statement.id}")
        if peer.id in coverage_service.claimed_entry_ids(db):
            raise _refuse("entry_id", "peer_leg_claimed", f"entry {peer.id}")
        if len(found["peer_line_ids"]) > 1:
            raise _refuse("line_id", "peer_ambiguous", f"lines {found['peer_line_ids']}")
        peer_line = None if found["peer_line_id"] is None else db.get(StatementLine, found["peer_line_id"])
        if peer_line is not None and line_claimed(ctx, peer_line.id):
            raise CodedConflictError(CANDIDATE_CHANGED, f"peer line {peer_line.id} is claimed")
        peer_statement = None if peer_line is None else next(
            s for s in statements if s.current_revision_id == peer_line.revision_id)
        return _Plan(line, leg, peer, peer_statement, peer_line, list(found["peer_statement_ids"]))

    def apply(self, ctx: Context, params: AdjustTransferParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        plan = self._plan(ctx, params, self._verified(ctx))
        magnitude = abs(Decimal(plan.line.flow_amount))
        restore = {}
        for entry, source in ((plan.leg, plan.line), (plan.peer, plan.peer_line)):
            restore[str(entry.id)] = {"amount": str(entry.amount), "entry_date": entry.entry_date.isoformat(),
                                      "posted_date": entry.posted_date.isoformat(), "description": entry.description}
            note = f"對帳調整 原金額 {ews._plain(entry.amount)}"
            entry.amount = -magnitude if entry.kind == "transfer_out" else magnitude
            if source is not None:
                entry.posted_date, entry.entry_date = source.posted_date, source.txn_date or source.posted_date
            entry.description = note if not entry.description else f"{entry.description}\n{note}"
        db.flush()
        coverage = claim_on(db, ctx, ctx.statement, plan.line, plan.leg, ADJUST)
        peer_case_id = None
        if plan.peer_line is not None:
            coverage += claim_on(db, ctx, plan.peer_statement, plan.peer_line, plan.peer, ADJUST)
            peer_case_id = resolve_peer_case(db, ctx, plan.peer_statement, plan.peer_line)
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *plan.statement_ids])
        effect.coverage_ids = coverage
        effect.touched_entry_ids = [plan.leg.id, plan.peer.id]
        effect.inverse = {"restore": restore, "statement_ids": plan.statement_ids, "peer_case_id": peer_case_id,
                          "peer_event_id": None if plan.peer_line is None else plan.peer_line.event_id,
                          "peer_line_id": None if plan.peer_line is None else plan.peer_line.id}

    # --- revert (§5.1 step 7) -----------------------------------------------------------------------------------

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        targets = super().revert_lock_targets(ctx, action)
        stored = [sid for sid in (action.inverse or {}).get("statement_ids", [])
                  if ctx.db.get(AccountStatement, sid) is not None]
        return replace(targets, statement_ids=tuple(sorted({*targets.statement_ids, *stored})))

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        """The engine's snapshot gate has already refused a changed or missing leg (409 `snapshot_conflict`). The
        order is fixed here: the engine releases and reopens only the primary case after `invert` returns."""
        db = ctx.db
        touched = []
        for entry_id, fields in action.inverse["restore"].items():
            entry = db.get(LedgerEntry, int(entry_id))
            entry.amount, entry.description = Decimal(fields["amount"]), fields["description"]
            entry.entry_date = date.fromisoformat(fields["entry_date"])
            entry.posted_date = date.fromisoformat(fields["posted_date"])
            touched.append(entry.id)
        db.flush()
        _release(ctx, action)
        reopen_peer_case(db, action)
        _reopen(ctx.case)  # so the case statement's reconcile keeps this case instead of opening a twin
        stored = [sid for sid in action.inverse.get("statement_ids", []) if db.get(AccountStatement, sid) is not None]
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *stored])
        return Effect(touched_entry_ids=sorted(touched))


register(AdjustTransferAction())
