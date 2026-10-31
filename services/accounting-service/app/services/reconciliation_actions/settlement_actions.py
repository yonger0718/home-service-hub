"""R7 Phase 1 `book_settlement` (portfolio integration spec §4.4, §4.7 Locks, §9): the owner / policy action that books
a bank settlement line (交割股款) as the bank ↔ market-value transfer the portfolio settlement describes, under ONE
`settlement_booking` owner row (`origin='bank'`) and its constituent items (slot `create`, one creation per event).

Steps (the engine's order, `engine.apply_action`):
- `prepare` (unlocked, before any lock): the fingerprint must be one of the case's candidates (422 `not_a_candidate`);
  the settlement is re-read from the feed on its trade day (`trade_settlement_service.facts_now`; the trade axis
  returns the day's row whatever its settlement date) — the one HTTP call, made before the writer barrier is taken.
  A portfolio failure is 409 `source_unavailable`.
- `check_before_locks` (statement + case locks): the statement not `needs_recheck` (409), a target account in
  `settlement.target_accounts` (409 `no_target`), the `_TwinGuarded` dirty-flag check.
- `validate_before` (account row + writer barrier, `_TwinGuarded`; the plan is proved once and stored on the context
  for `apply`): every `booked` row of this event, fingerprint or
  (account, settlement date) is locked row → legs NOWAIT (`trade_settlement_service.lock_pair`, 409
  `settlement_busy`; M11) and re-read; a row of THIS event that is still active coverage (booked, both legs present,
  its bank leg claiming a line of the event) → nothing new: the engine replays its action (`Replay`); then the line
  open (`line_claimed` …); a booked row of the fingerprint for another event → 409 `already_booked`; of the day under
  another fingerprint → 409 `day_booked`; the locked candidate equal to the re-read (net, settlement date,
  `(source_id, amount)` set, `bookable`, `date_source`: `same_facts`), bookable and of the line's flow, else 409
  `candidate_changed` (AGENT-360 major 3); the target still mapped and the source still set (409 `no_target`); the
  bank (refreshed by the engine's account lock), the re-read target and the source in one currency
  (`tss.pair_currency_ok`, else 409 `currency_mismatch`; PR #234 re-check finding 1); the owner-twin re-check for
  guarded actors (`_twin_check` plus the settlement twin `find_twins` within `settlement.twin_window_days`).
- `apply`: `transfer_service.create_transfer` bank → target for a negative net, target → bank for a positive one
  (`|line.flow|`, both legs on the line's posted date, name `證券交割`, description `證券交割 <trade date>`, source
  `statement`), the booking row (`facts` = the re-read facts) and its items in a savepoint — a unique violation is
  409 `constituent_reserved` / `already_booked` / `day_booked` — and the bank leg claims the line (`match_rule
  book_settlement`).

Revert (`invert`): the engine holds the legs NOWAIT (`lock_rows`) and its snapshot gate refuses an edited or missing
leg; the booking row is then try-locked NOWAIT — a reverse-order lock, so held → 409 `settlement_busy`, never a wait
(M11) — then release → `delete_entries_cascade` of both legs → the row `retired` (reason `revert`) with its items
deleted → the case reopens as `line_unmatched` + `settlement_pending` without candidates. The next reconcile re-derives
the line (spec §9: the bank path may book again)."""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Account, LedgerEntry, ReconciliationAction, SettlementBooking, SettlementBookingItem, StatementCoverage, StatementLine,
)
from app.schemas.writes import TransferIn
from app.services import settings_service, transfer_service
from app.services import entry_write_service as ews
from app.services import pair_locks
from app.services import trade_settlement_service as tss
from app.services.errors import CodedConflictError
from app.services.portfolio_client import PortfolioUnavailable

from . import audit, register
from .base import OWNER, POLICY, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, Replay
from .ledger_actions import STATEMENT_SOURCE, _claim, _open_line, _refuse, _release, _TwinGuarded

NAME = "book_settlement"
TRANSFER_NAME = tss.TRANSFER_NAME
PENDING_HINT = "settlement_pending"
UNIQUE_CODES = tss.UNIQUE_CODES


class BookSettlementParams(ActionParams):
    fingerprint: str


def _candidate(ctx: Context, fingerprint: str) -> dict | None:
    return next((c for c in ctx.case.candidates or () if isinstance(c, dict) and c.get("fingerprint") == fingerprint),
                None)


def _target_id(ctx: Context) -> int | None:
    return settings_service.read_settlement_settings(ctx.db)["target_accounts"].get(str(ctx.statement.account_id))


def _day(facts: dict, line: StatementLine) -> date:
    return date.fromisoformat(facts["settlement_date"]) if facts.get("settlement_date") else line.posted_date


class BookSettlementAction(_TwinGuarded):
    name = NAME
    Params = BookSettlementParams
    case_kinds = frozenset({"settlement", "ambiguous"})
    actors = frozenset({OWNER, POLICY})
    budget_exempt = True  # zero-charged and outside every policy cap (owner ruling Q4 (b))

    # --- unlocked ---------------------------------------------------------------------------------------------------

    def prepare(self, ctx: Context, params: BookSettlementParams) -> Prepared:
        prepared = super().prepare(ctx, params)
        candidate = _candidate(ctx, params.fingerprint)
        if candidate is None:
            raise _refuse("fingerprint", "not_a_candidate", f"case {ctx.case.id}")
        if not ctx.account.settlement_source or _target_id(ctx) is None:
            return dataclasses.replace(prepared, data={"now": None, "read": False})  # check_before_locks refuses
        try:
            now = tss.facts_now(ctx.db, ctx.account, fingerprint=params.fingerprint,
                                trade_date=date.fromisoformat(candidate["trade_date"]),
                                settlement_date=None if not candidate.get("settlement_date")
                                else date.fromisoformat(candidate["settlement_date"]))
        except PortfolioUnavailable as exc:
            raise CodedConflictError("source_unavailable", exc.code) from exc
        return dataclasses.replace(prepared, data={"now": now, "read": True})

    # --- under the statement and case locks -------------------------------------------------------------------------

    def check_before_locks(self, ctx: Context, params: BookSettlementParams) -> None:
        if ctx.statement.needs_recheck:
            raise CodedConflictError("needs_recheck", f"statement {ctx.statement.id} awaits its re-check")
        if not ctx.account.settlement_source or _target_id(ctx) is None:
            raise CodedConflictError("no_target", f"account {ctx.statement.account_id} has no settlement target")
        super().check_before_locks(ctx, params)

    # --- under the account row and the writer barrier ----------------------------------------------------------------

    def validate_before(self, ctx: Context, params: BookSettlementParams, prepared: Prepared, before: dict) -> None:
        ctx._settlement_plan = self._plan(ctx, params, prepared)  # proved once; `apply` consumes exactly this one

    def _plan(self, ctx: Context, params: BookSettlementParams, prepared: Prepared) -> tuple[StatementLine, dict, int]:
        """Every check under the engine's locks (module docstring); returns (line, the re-read facts, target id)."""
        db, line = ctx.db, ctx.line
        candidate = _candidate(ctx, params.fingerprint)  # the LOCKED case's listing
        if candidate is None:
            raise _refuse("fingerprint", "not_a_candidate", f"case {ctx.case.id}")
        event_id = None if line is None else line.event_id
        day = None if line is None else _day(candidate, line)
        rows = self._holders(ctx, params.fingerprint, event_id, day)
        for row in rows:
            tss.lock_pair(db, row)  # row → legs NOWAIT, refreshed in place (settlement_busy)
        holders = [row for row in rows if row.status == "booked"]
        for row in holders:
            if event_id is not None and row.event_id == event_id and self._covers_event(ctx, row, event_id):
                self._replay(ctx, row)
        line = _open_line(ctx)
        if any(row.fingerprint == params.fingerprint for row in holders):
            raise CodedConflictError("already_booked", f"fingerprint {params.fingerprint} is booked for another event")
        if any(row.account_id == ctx.statement.account_id and row.settlement_date == day for row in holders):
            raise CodedConflictError("day_booked", f"{day.isoformat()} is booked under another fingerprint")
        now = prepared.data.get("now")
        if not prepared.data.get("read") or not tss.same_facts(candidate, now) or not now["bookable"] \
                or Decimal(now["net_amount"]) != Decimal(line.flow_amount):
            raise CodedConflictError("candidate_changed", f"settlement {params.fingerprint} changed in the feed")
        target_id = _target_id(ctx)
        if not ctx.account.settlement_source or target_id is None:
            raise CodedConflictError("no_target", f"account {ctx.statement.account_id} has no settlement target")
        # PR #234 re-check finding 1: `prepare` checked the currencies unlocked; the engine's account lock refreshed
        # `ctx.account`, and the target is re-read here — a concurrent currency change is refused, never booked
        target = ctx.db.get(Account, target_id, populate_existing=True)
        if not tss.pair_currency_ok(ctx.account, target):
            raise CodedConflictError("currency_mismatch", f"account {ctx.statement.account_id} or its target "
                                                          f"{target_id} is not in its settlement source's currency")
        self._settlement_twin_check(ctx, line, target_id)
        return line, now, target_id

    @staticmethod
    def _holders(ctx: Context, fingerprint: str, event_id: int | None,
                 day: date | None) -> list[SettlementBooking]:
        """The `booked` settlement rows this booking would meet: of the event, the fingerprint or the account-day."""
        clauses = [SettlementBooking.fingerprint == fingerprint]
        if event_id is not None:
            clauses.append(SettlementBooking.event_id == event_id)
        if day is not None:
            clauses.append((SettlementBooking.account_id == ctx.statement.account_id)
                           & (SettlementBooking.settlement_date == day))
        return list(ctx.db.scalars(select(SettlementBooking).where(
            SettlementBooking.kind == tss.KIND, SettlementBooking.status == "booked", or_(*clauses))
            .order_by(SettlementBooking.id).execution_options(populate_existing=True)))

    @staticmethod
    def _covers_event(ctx: Context, row: SettlementBooking, event_id: int) -> bool:
        """Task 6 review minor 5: `event_id` alone may name a stale holder — the row must still be active coverage of
        the event (both legs present, its bank leg claiming a line of that event)."""
        db = ctx.db
        legs = [db.get(LedgerEntry, i) for i in (row.out_entry_id, row.in_entry_id) if i is not None]
        if len(legs) != 2 or any(leg is None for leg in legs):
            return False
        bank_leg = next((leg.id for leg in legs if leg.account_id == row.account_id), None)
        return bank_leg is not None and db.scalar(
            select(StatementCoverage.id).join(StatementLine, StatementLine.id == StatementCoverage.line_id)
            .where(StatementCoverage.entry_id == bank_leg, StatementCoverage.status == "active",
                   StatementLine.event_id == event_id).limit(1)) is not None

    @staticmethod
    def _replay(ctx: Context, row: SettlementBooking) -> None:
        """Spec §4.4 item 3: a booking for this event exists → nothing new. Its applied action replays; a booking
        without one (a claimed stand-in) has no row to replay and is refused `already_booked`."""
        action = None if row.action_id is None else ctx.db.get(ReconciliationAction, row.action_id)
        if action is not None and action.status == "applied":
            raise Replay(action)
        raise CodedConflictError("already_booked", f"booking {row.id} holds this event")

    def _settlement_twin_check(self, ctx: Context, line: StatementLine, target_id: int) -> None:
        """Under the writer barrier, for the guarded actors: the `_TwinGuarded` owner twin of the line, then the
        settlement twin of spec §4.7 (an owner entry of the net on the bank account, or an owner transfer leg of its
        mirror on the target, within `settlement.twin_window_days` of the line)."""
        self._twin_check(ctx, line)
        code = self._guard_code(ctx)
        if code is not None and tss.find_twins(
                ctx.db, account_id=ctx.statement.account_id, target_account_id=target_id,
                amount=Decimal(line.flow_amount), settlement_date=line.posted_date,
                window_days=tss.twin_window_days(ctx.db)):
            raise CodedConflictError(code, f"an owner entry may already record line {line.id}")

    def apply(self, ctx: Context, params: BookSettlementParams, prepared: Prepared, effect: Effect) -> None:
        db = ctx.db
        if ctx._settlement_plan is None:  # the engine always runs validate_before first, under the same locks
            raise RuntimeError("book_settlement applied without its validated plan")
        line, facts, target_id = ctx._settlement_plan
        flow = Decimal(line.flow_amount)
        bank_id = ctx.statement.account_id
        source, sink = (bank_id, target_id) if flow < 0 else (target_id, bank_id)
        group_id = transfer_service.create_transfer(db, TransferIn(
            from_account_id=source, to_account_id=sink, out_amount=abs(flow), entry_date=line.posted_date,
            posted_date=line.posted_date, name=TRANSFER_NAME, description=f"{TRANSFER_NAME} {facts['trade_date']}"),
            source=STATEMENT_SOURCE, remember=False)
        out_leg, in_leg = transfer_service.transfer_legs(db, group_id)
        booking = SettlementBooking(
            kind=tss.KIND, origin="bank", account_id=bank_id, target_account_id=target_id, event_id=line.event_id,
            source=ctx.account.settlement_source, fingerprint=params.fingerprint,
            trade_date=date.fromisoformat(facts["trade_date"]), settlement_date=_day(facts, line),
            net_amount=Decimal(facts["net_amount"]), facts=facts, transfer_group_id=group_id,
            out_entry_id=out_leg.id, in_entry_id=in_leg.id, status="booked", action_id=ctx.action_id,
            items=[SettlementBookingItem(source=i["source_id"].split(":")[0], source_id=i["source_id"],
                                         amount=Decimal(i["amount"])) for i in facts["items"]])
        try:
            with db.begin_nested():
                db.add(booking)
                db.flush()
        except IntegrityError as exc:
            code = next((code for index, code in UNIQUE_CODES if index in str(exc.orig)), None)
            if code is None:
                raise
            raise CodedConflictError(code, f"settlement {params.fingerprint}") from exc
        bank_leg = out_leg if flow < 0 else in_leg
        effect.coverage_ids = _claim(ctx, line, [(bank_leg, "principal")], self.name)
        effect.created_entry_ids = [out_leg.id, in_leg.id]
        effect.inverse = {"created_entry_ids": [out_leg.id, in_leg.id], "booking_id": booking.id}

    # --- revert -----------------------------------------------------------------------------------------------------

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        """The legs (engine default) plus the account row and the writer barrier, as the apply took them (§4.7)."""
        return dataclasses.replace(super().revert_lock_targets(ctx, action), account_lock=True, barrier=True)

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        db = ctx.db
        booking_id = action.inverse["booking_id"]
        row = pair_locks.lock_row(db, SettlementBooking, booking_id, busy_code=tss.SETTLEMENT_BUSY,
                                  label="settlement_booking row")  # reverse order: NOWAIT, never a wait (M11)
        if row is None or row.status != "booked":
            raise NotRevertable(f"settlement booking {booking_id} is {None if row is None else row.status}")
        legs = list(action.inverse["created_entry_ids"])
        detail = {"booking_id": row.id, "origin": row.origin, "account_id": row.account_id,
                  "target_account_id": row.target_account_id, "fingerprint": row.fingerprint,
                  "settlement_date": row.settlement_date.isoformat(), "reason": "revert",
                  "out_entry_id": row.out_entry_id, "in_entry_id": row.in_entry_id}
        _release(ctx, action)
        ews.delete_entries_cascade(db, legs)  # ends with expire_all; the leg FKs go NULL
        row = db.get(SettlementBooking, booking_id)
        row.status, row.reason = "retired", "revert"
        row.items.clear()  # the reservation goes with the `booked` status
        case = ctx.case
        case.kind, case.candidates = "line_unmatched", []
        case.context = {"hint": PENDING_HINT, "reason": None, "nearest": None}
        db.flush()
        audit.record(db, event="settlement_retired", actor=ctx.label, statement_id=ctx.statement.id, case_id=case.id,
                     action_id=action.id, detail=detail)
        return Effect(deleted_entry_ids=sorted(legs))


register(BookSettlementAction())
