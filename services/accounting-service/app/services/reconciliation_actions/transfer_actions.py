"""Transfer reconciliation actions (R6c; spec §5.1–§5.3): `adjust_transfer` rewrites an owner's transfer to the
statement figures across both statements (slot `amount`); `pair_transfer` books one transfer from two complementary
`needs_peer` lines when the owner recorded none (slot `create`). Both share the peer-statement discovery and the
both-statement lifecycle helpers of `transfer_support.py` (re-exported here under the same names).

Lifecycle (§5.1): discovery runs unlocked in `lock_targets` (the peer leg or line, the peer statement(s)) and is
frozen on the context for `prepare`; `validate_before` re-derives it under the engine's locks and refuses 409
`candidate_changed` on any difference, and `apply` consumes only that verified discovery. `apply` writes the legs
directly (never `transfer_service.update_transfer`, which would copy one date to both legs, drop children and force
`source`), claims this line and the peer line, resolves the peer line's case with this action row, then reconciles
both statements. `invert` undoes the ledger write, releases the claims, reopens the peer case and this case, and
reconciles again."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal,
    StatementLine,
)
from app.schemas.writes import Int32, TransferIn
from app.services import coverage_service, settings_service, stand_ins, transfer_service
from app.services import entry_write_service as ews
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError
from app.services.statements import matching, retired

from . import OPEN_STATUSES, register, snapshots
from .base import (
    OWNER, POLICY, ActionBase, ActionParams, Context, Effect, LockSet, Prepared, current_line, lockset_for,
    schedule_rows,
)
from .coverage_actions import _line_claimed as line_claimed
from .coverage_actions import case_line
from .ledger_actions import AMOUNT, CREATE, STATEMENT_SOURCE, _gate, _open_line, _refuse, _release
from .structural_actions import _reconcile_keeping_case, _reopen
from .transfer_support import (  # noqa: F401  (re-exported: the public names of Task 3)
    BUSY_CASES, CANDIDATE_CHANGED, LINE_CASE_KINDS, _has_children, claim_on, complementary_lines, family_root,
    peer_leg, peer_statement_refusal, peer_statements, reopen_peer_case, resolve_peer_case, stored_statement_ids,
    verified_discovery, with_stored_statements,
)

ADJUST, PAIR = "adjust_transfer", "pair_transfer"
PEER_LINE_KINDS_FOR_ADJUST = frozenset({"payment", "transfer_in", "transfer_out", "withdrawal", "deposit"})
BANK_ONLY_PEER_KINDS = PEER_LINE_KINDS_FOR_ADJUST - matching.PEER_LINE_KINDS  # «BC 7»: 提款 / 轉帳支出 on a bank


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
        """The discovery `validate_before` proved under the locks (`verified_discovery`)."""
        return verified_discovery(ctx)

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
        return with_stored_statements(ctx.db, super().revert_lock_targets(ctx, action), action)

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
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *stored_statement_ids(db, action)])
        return Effect(touched_entry_ids=sorted(touched))


register(AdjustTransferAction())


# --- pair_transfer (§5.3) -------------------------------------------------------------------------------------------

PAIR_NAME = "對帳配對轉帳"
PEER_HINT = "needs_peer"
_PAIR_FROZEN = ("peer_line_id", "peer_statement_ids", "in_scope")


def _guard_code(ctx: Context) -> str | None:
    """The owner-override gate (copy of `_TwinGuarded._guard_code`, owner rulings B + B): `candidate_changed` for the
    policy, `owner_may_record` for the owner applying a policy-authored proposal, None for a plain owner action (or
    an owner applying an owner- or agent-authored proposal)."""
    if ctx.actor == POLICY:
        return "candidate_changed"
    proposal = ctx.db.get(ReconciliationProposal, ctx.proposal_id) if ctx.proposal_id is not None else None
    return "owner_may_record" if proposal is not None and proposal.author == POLICY else None


def _day(entry: LedgerEntry) -> date:
    return entry.posted_date or entry.entry_date


def transfer_twin_reason(db: Session, rules: matching.Rules, a: tuple[AccountStatement, StatementLine],
                         b: tuple[AccountStatement, StatementLine]) -> str | None:
    """Why an owner entry may already record the pair (§5.3 transfer-twin guard), or None. Inside
    `transfer_pair_window_days` of either line: (a) a transfer whose two top-level legs join the two account families
    with at least one leg unclaimed, whatever its amount; (b) an unclaimed top-level entry on a line's family, of any
    kind, with that line's flow sign and `|amount|` within max(`near_tolerance_abs`, `transfer_tolerance_pct` ·
    |flow|); (c) the R6b twin rule (`stand_ins.owner_may_record`) for either line."""
    window = rules.transfer_pair_window_days
    days = [line.posted_date for _, line in (a, b)]
    near = lambda entry: any(abs((_day(entry) - d).days) <= window for d in days)  # noqa: E731
    families = [set(coverage_service.participating_accounts(db, stmt.account_id)) for stmt, _ in (a, b)]
    lo, hi = min(days) - timedelta(days=window), max(days) + timedelta(days=window)
    currency = a[0].currency
    claimed = coverage_service.claimed_entry_ids(db)
    day = func.coalesce(LedgerEntry.posted_date, LedgerEntry.entry_date)
    rows = [e for e in db.execute(select(LedgerEntry).where(
        LedgerEntry.account_id.in_(sorted(families[0] | families[1])), LedgerEntry.currency == currency,
        LedgerEntry.parent_entry_id.is_(None), day >= lo, day <= hi).order_by(LedgerEntry.id)).scalars() if near(e)]
    groups = sorted({e.transfer_group_id for e in rows if e.transfer_group_id is not None}, key=str)
    for group in groups:  # (a)
        legs = db.execute(select(LedgerEntry).where(LedgerEntry.transfer_group_id == group,
                                                    LedgerEntry.parent_entry_id.is_(None))).scalars().all()
        accounts = {leg.account_id for leg in legs}
        if accounts & families[0] and accounts & families[1] and any(leg.id not in claimed for leg in legs):
            return f"unclaimed_transfer {group}"
    for (_, line), family in zip((a, b), families):  # (b)
        flow = Decimal(line.flow_amount)
        tol = max(rules.near_tolerance_abs, (rules.transfer_tolerance_pct * abs(flow)).quantize(matching.Q))
        for entry in rows:
            amount = Decimal(entry.amount)
            if entry.account_id in family and entry.id not in claimed and amount != 0 and (amount > 0) == (flow > 0) \
                    and abs(abs(amount) - abs(flow)) <= tol:
                return f"near_entry {entry.id} for line {line.id}"
    for stmt, line in (a, b):  # (c)
        if stand_ins.owner_may_record(db, stmt, line.event_id, line, rules):
            return f"owner_may_record line {line.id}"
    return None


class PairTransferParams(ActionParams):
    peer_line_id: Int32


@dataclass
class _Pair:
    line: StatementLine
    peer_statement: AccountStatement
    peer_line: StatementLine


class PairTransferAction(ActionBase):
    """Two complementary `needs_peer` lines on statements of different account families and no owner transfer: one
    transfer of `|flow|` from the out side's statement account to the in side's, each leg dated from its own line,
    `source = "statement"`, `needs_review`; both lines claimed (rule `pair_transfer`), the peer line's case resolved
    by this row. The case statement's leg is a stand-in (`stand_ins.BACKFILL_ACTIONS`), the peer leg an owner entry
    on its statement (option A). Gate (`_guard_code`): the occupancy guard (`owner_deleted`), the transfer-twin guard
    and `dirty_disabled` bind the policy and the owner applying a policy-authored proposal only; the re-derivation,
    the peer statement, the open lines and the peer event's creation family bind every actor."""
    name = PAIR
    Params = PairTransferParams
    case_kinds = frozenset({"line_unmatched"})
    actors = frozenset({OWNER, POLICY})
    slot = CREATE

    @staticmethod
    def peer_candidates(db: Session, case: ReconciliationCase) -> list[tuple[AccountStatement, StatementLine]]:
        """Computed when the action or the inbox runs, never stored (§5.3 «BC 5»): uncovered current transfer lines
        on live statements of accounts outside the case statement's participating family, same currency,
        complementary to the case's line within `transfer_pair_window_days`, whose own case is open/proposed
        `line_unmatched` with hint `needs_peer`; ordered by (day distance, line id)."""
        statement = db.get(AccountStatement, case.statement_id)
        line = None if statement is None else current_line(db, case, statement)
        if line is None or line.line_kind not in matching.PEER_LINE_KINDS or Decimal(line.flow_amount) == 0:
            return []
        rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
        window = rules.transfer_pair_window_days
        family = coverage_service.participating_accounts(db, statement.account_id)
        start, end = line.posted_date - timedelta(days=window), line.posted_date + timedelta(days=window)
        statements = list(db.execute(select(AccountStatement).where(
            AccountStatement.account_id.not_in(family), AccountStatement.currency == statement.currency,
            AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
            AccountStatement.period_start <= end, AccountStatement.period_end >= start,
            ~retired.clause(AccountStatement.id)).order_by(AccountStatement.id)).scalars())
        if statement.kind == "card" and line.line_kind == "payment":  # mirrors `peer_is_card`: a card payment comes from a bank
            credit = set(db.scalars(select(Account.id).where(Account.is_credit.is_(True))))
            statements = [s for s in statements if s.account_id not in credit]
        found = complementary_lines(db, statements, line, kinds=matching.PEER_LINE_KINDS, window_days=window)
        return [(s, other) for s, other in found if db.execute(select(exists().where(
            ReconciliationCase.statement_id == s.id, ReconciliationCase.event_id == other.event_id,
            ReconciliationCase.kind == "line_unmatched", ReconciliationCase.status.in_(OPEN_STATUSES),
            ReconciliationCase.context["hint"].astext == PEER_HINT))).scalar_one()]

    def _discover(self, ctx: Context, params: PairTransferParams) -> dict:
        """The peer line's statement (the one whose current revision prints it) and whether the line is a peer
        candidate — read as they are (unlocked in `lock_targets`, locked in `validate_before`)."""
        db = ctx.db
        peer_line = db.get(StatementLine, params.peer_line_id)
        peer_statement = None if peer_line is None else db.execute(select(AccountStatement).where(
            AccountStatement.current_revision_id == peer_line.revision_id)).scalar_one_or_none()
        in_scope = any(other.id == params.peer_line_id for _, other in self.peer_candidates(db, ctx.case))
        return {"peer_line_id": params.peer_line_id, "in_scope": in_scope,
                "peer_statement_ids": [] if peer_statement is None else [peer_statement.id]}

    def lock_targets(self, ctx: Context, params: PairTransferParams) -> LockSet:
        found = self._discover(ctx, params)
        ctx._transfer_discovery = found  # the one unlocked discovery; prepare freezes it
        # the account row and the global writer barrier belong to the transfer-twin re-check (the peer account row
        # is never locked: the barrier is global)
        return lockset_for(ctx.db, statement_ids=found["peer_statement_ids"], account_lock=True, barrier=True)

    def prepare(self, ctx: Context, params: PairTransferParams) -> Prepared:
        found = ctx._transfer_discovery if ctx._transfer_discovery is not None else self._discover(ctx, params)
        return Prepared(expected=snapshots.expected_set(ctx.db, (), ()), data={key: found[key] for key in _PAIR_FROZEN})

    def check_before_locks(self, ctx: Context, params: PairTransferParams) -> None:
        """Before the account row and the barrier (the dirty triggers take the barrier only while on)."""
        if _guard_code(ctx) is not None and not settings_service.read_reconciliation_settings(ctx.db)["dirty_enabled"]:
            raise _refuse("action", "dirty_disabled", "the transfer-twin re-check needs the writer barrier")

    def validate_before(self, ctx: Context, params: PairTransferParams, prepared: Prepared, before: dict) -> None:
        code = _guard_code(ctx)
        if code is not None:  # first: an owner-deleted pairing refuses whatever the peer line looks like now
            self._occupancy_guard(ctx, params)
        found = self._discover(ctx, params)
        now = {key: found[key] for key in _PAIR_FROZEN}
        if now != prepared.data or tuple(found["peer_statement_ids"]) != tuple(prepared.lock_set.statement_ids):
            raise CodedConflictError(CANDIDATE_CHANGED, f"discovered {prepared.data}, now {now}")
        pair = self._plan(ctx, found)
        if code is not None:  # under the writer barrier: the dirty flag again, then the transfer-twin guard
            self.check_before_locks(ctx, params)
            reason = transfer_twin_reason(ctx.db, ctx.rules, (ctx.statement, pair.line),
                                          (pair.peer_statement, pair.peer_line))
            if reason is not None:
                raise CodedConflictError(code, f"an owner entry may already record the pair: {reason}")
        ctx._transfer_discovery = found  # the locked, verified discovery: `apply` consumes exactly this one

    @staticmethod
    def _occupancy_guard(ctx: Context, params: PairTransferParams) -> None:
        """§5.1 option A: a `pair_transfer` row naming this event or the peer line's event that is still applied,
        or was deleted by the owner (and not re-booked since), refuses 422 `owner_deleted`."""
        peer_line = ctx.db.get(StatementLine, params.peer_line_id)
        events = [ctx.case.event_id, None if peer_line is None else peer_line.event_id]
        if stand_ins.pair_transfer_block(ctx.db, events):
            raise _refuse("action", "owner_deleted", f"events {[e for e in events if e is not None]}")

    def _plan(self, ctx: Context, found: dict) -> _Pair:
        """Every actor: the peer statement live and not busy, the peer line a candidate, both lines open, both
        statement accounts in the statement's currency, no applied creation on the peer event."""
        db = ctx.db
        if not found["peer_statement_ids"]:
            raise _refuse("peer_line_id", "peer_out_of_scope", f"line {found['peer_line_id']}")
        peer_statement = db.get(AccountStatement, found["peer_statement_ids"][0])
        refusal = peer_statement_refusal(peer_statement, db)
        if refusal is not None:
            raise _refuse("peer_line_id", refusal, f"statement {peer_statement.id}")
        if not found["in_scope"]:
            raise _refuse("peer_line_id", "peer_out_of_scope", f"line {found['peer_line_id']}")
        line = _open_line(ctx)
        peer_line = _open_line(ctx, db.get(StatementLine, found["peer_line_id"]))
        for statement in (ctx.statement, peer_statement):
            if db.get(Account, statement.account_id).currency != statement.currency:
                raise _refuse("peer_line_id", "cross_currency", f"statement {statement.id}")
        taken = db.execute(select(exists().where(
            ReconciliationAction.event_id == peer_line.event_id, ReconciliationAction.effect_slot == CREATE,
            ReconciliationAction.status == "applied"))).scalar_one()
        if taken:
            raise _refuse("action", "duplicate_effect", f"a creation is applied on peer event {peer_line.event_id}")
        return _Pair(line, peer_statement, peer_line)

    def apply(self, ctx: Context, params: PairTransferParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        pair = self._plan(ctx, verified_discovery(ctx))
        outgoing = Decimal(pair.line.flow_amount) < 0  # the case line may be either side
        mine, theirs = (ctx.statement, pair.line), (pair.peer_statement, pair.peer_line)
        (out_stmt, out_line), (in_stmt, in_line) = (mine, theirs) if outgoing else (theirs, mine)
        group = transfer_service.create_transfer(db, TransferIn(
            from_account_id=out_stmt.account_id, to_account_id=in_stmt.account_id,
            out_amount=abs(Decimal(out_line.flow_amount)), entry_date=out_line.txn_date or out_line.posted_date,
            posted_date=out_line.posted_date, name=PAIR_NAME,
            description=f"對帳配對 {out_stmt.period_end.isoformat()} / {in_stmt.period_end.isoformat()}"),
            source=STATEMENT_SOURCE, remember=False)
        out_leg, in_leg = transfer_service.transfer_legs(db, group)
        in_leg.entry_date, in_leg.posted_date = in_line.txn_date or in_line.posted_date, in_line.posted_date
        out_leg.needs_review = in_leg.needs_review = True  # create_transfer writes False
        db.flush()
        case_leg, peer_leg_entry = (out_leg, in_leg) if outgoing else (in_leg, out_leg)
        coverage = claim_on(db, ctx, ctx.statement, pair.line, case_leg, PAIR)
        coverage += claim_on(db, ctx, pair.peer_statement, pair.peer_line, peer_leg_entry, PAIR)
        peer_case_id = resolve_peer_case(db, ctx, pair.peer_statement, pair.peer_line)
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, pair.peer_statement.id])
        effect.coverage_ids = coverage
        effect.created_entry_ids = [out_leg.id, in_leg.id]
        effect.inverse = {"created_entry_ids": [out_leg.id, in_leg.id], "statement_ids": [pair.peer_statement.id],
                          "peer_case_id": peer_case_id, "peer_event_id": pair.peer_line.event_id,
                          "peer_line_id": pair.peer_line.id}

    # --- revert (§5.1 step 7) -----------------------------------------------------------------------------------

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        return with_stored_statements(ctx.db, super().revert_lock_targets(ctx, action), action)

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        """The engine's snapshot gate has already refused an edited or missing leg (409 `snapshot_conflict`), so no
        check of its own. Fixed order: release the claims, delete both legs, reopen the peer case and this case,
        reconcile both statements (the engine releases and reopens only the primary case after `invert`)."""
        db = ctx.db
        legs = list(action.inverse["created_entry_ids"])
        _release(ctx, action)
        ews.delete_entries_cascade(db, legs)
        reopen_peer_case(db, action)
        _reopen(ctx.case)  # so the case statement's reconcile keeps this case instead of opening a twin
        _reconcile_keeping_case(db, ctx.case, [ctx.statement.id, *stored_statement_ids(db, action)])
        return Effect(deleted_entry_ids=sorted(legs))


register(PairTransferAction())
