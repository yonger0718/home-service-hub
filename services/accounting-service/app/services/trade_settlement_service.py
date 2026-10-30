"""國泰 settlement bookings (portfolio integration spec §4.3–§4.5, §4.7, §9): the reconcile half — the feed read, the
global assignment's case outcomes, the stand-in claim link, drift over claimed lines, the owner-delete / owner-edit
housekeeping of both origins and the twin retirement. (Task 9 adds the stand-in job half.)

- Feed (§4.1, §4.3): `fetch_by_settlement_window` asks the portfolio for every settlement whose (printed or derived)
  date can fall in a settlement window, the trade bound proven from the bank calendar (`settlement_match.trade_bounds`
  with the auto-pay `extra_holidays` / `extra_workdays`); a calendar that ends too early is `PortfolioUnavailable
  ("calendar")` before any HTTP call, and the client raises `incomplete` for narrower bounds or `complete: false` —
  no automatic assignment on an answer whose completeness is not asserted. `fetch_by_trade_date` reads trade days on
  the trade axis (every row of those trade dates, whatever its settlement date; rows of OTHER trade dates settling on
  `trade_from` come back too, so callers select by trade date and fingerprint). Undated rows stay in `Feed.rows`.
- Facts (M6): `facts_of` is the one JSON shape of `settlement_booking.facts` (both origins) and of every case
  candidate (`FACTS_KEYS`); drift compares net, settlement date, the `(source_id, amount)` set, `bookable` and
  `date_source` (`same_facts`).
- Reconcile (`settle`, after the statement's coverage is written): every settlement line of the statement and of the
  account's live neighbours in the window enters the graph, minus the lines a `booked` row's leg covers (their
  settlement is identified and reserved, rev 4.5 B7); covered lines compete as contestants and are never booked
  (AGENT-360 major 4); each `settlement_line` placeholder of the matcher becomes `settlement` (unique), `ambiguous` +
  `settlement_ambiguous`, `line_unmatched` + `settlement_pending` (with `reason`, `nearest`) or `settlement_mismatch`
  (no candidate, an unclaimed stand-in within the window). A claimed booking's facts re-read from the feed differing
  → a `settlement` case `source_drift` on its line; nothing is rebooked.
- Housekeeping (§4.7, both origins; the job runs it too, reconcile before it reads reservations): `housekeep` moves
  a `booked` row whose legs are gone to `owner_deleted` (items released in the same transaction) and one whose leg is
  gone or no longer `source='statement'` to `owner_edited` (items kept). `retire_twins_for_statement` retires a pair
  whose owner twin appeared (`retire_pair(reason="twin")`, the auto-pay semantics: coverage released).

Locks (§4.7 Locks, rev 4.3 — the one protocol): the booking row FOR UPDATE NOWAIT → its two legs FOR UPDATE NOWAIT →
coverage re-read, in one savepoint (`lock_pair` over `pair_locks.lock_row_and_legs`); a held row or leg is
`settlement_busy`, never a wait. Nothing here takes an account row: the engine's account-row takers hold no entry or
group lock (engine_locks.py:1-9), and a reconcile or job holding entry locks or a statement row while waiting for the
account row would close the two cycles §4.7 Locks (a)/(b) describes. `reconcile` claims a stand-in leg only while it
holds that leg (`_lock_candidates`, NOWAIT), so a job holding the legs makes the reconcile `reconcile_busy` and a
reconcile holding them makes the job skip the row. The C4 ruling keeps this lifecycle separate from auto-pay's on
purpose (claimed pairs refused except for twins, replacement, two origins, feed facts instead of a header); only the
lock step is shared. Nothing here commits; callers own the transaction (flush only)."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationCase, SettlementBooking, SettlementBookingItem,
    StatementCoverage, StatementLine,
)
from app.services import (
    auto_pay_service, coverage_service, entry_write_service, pair_locks, portfolio_client, settings_service, stand_ins,
)
from app.services.entry_write_service import STATEMENT_SOURCE
from app.services.errors import CodedConflictError
from app.services.portfolio_client import Feed, PortfolioClient, PortfolioUnavailable, Settlement
from app.services.reconciliation_actions import audit
from app.services.statements import matching, retired, settlement_match

ACTOR = "settlement"
SETTLEMENT_BUSY = "settlement_busy"
KIND = "settlement"
TWIN_WINDOW_DAYS = 3  # spec §8 `settlement.twin_window_days` default (`settings_service.SETTLEMENT_DEFAULTS`)
SETTLE_MARGIN_DAYS = 5  # spec §4.3: the settlements whose date can fall in period_start − 5 … period_end + 5
NEIGHBOUR_RECHECK_DAYS = 7  # spec §4.5 item 3: the revision hook flags statements within period ± 7
FACTS_KEYS = ("fingerprint", "trade_date", "settlement_date", "net_amount", "date_source", "bookable", "items")
# window-read failures after which the drift re-reads are still tried: the calendar fault made no call, and an
# `incomplete` answer came from a reachable server; after a transport / body failure they are skipped (one timeout)
DRIFT_READ_AFTER = frozenset({"calendar", "incomplete"})
_Q = Decimal("0.0001")

logger = logging.getLogger(__name__)


# --- feed -----------------------------------------------------------------------------------------------------------


def broker_of(account: Account) -> str:
    """`portfolio:cathay` → `cathay` (spec §4.2: the feed's `broker` parameter)."""
    return (account.settlement_source or "").removeprefix("portfolio:")


def _calendar(db: Session) -> tuple[frozenset[date], frozenset[date]]:
    """The bank calendar overrides (`auto_pay.extra_holidays` / `extra_workdays`, owner ruling Q1 (b))."""
    return auto_pay_service.calendar_sets(settings_service.read_auto_pay_settings(db))


def _dated(db: Session, feed: Feed) -> Feed:
    holidays, workdays = _calendar(db)
    return replace(feed, rows=tuple(settlement_match.with_dates(list(feed.rows), extra_holidays=holidays,
                                                                 extra_workdays=workdays)))


def fetch_by_settlement_window(db: Session, account: Account, settle_from: date, settle_to: date, *,
                               client: PortfolioClient | None = None) -> Feed:
    """Every settlement whose printed or derived settlement date can fall in `settle_from … settle_to` (spec §4.1 two
    axes): the trade range is `settlement_match.trade_bounds` over the bank calendar — `CalendarExpired` is
    `PortfolioUnavailable("calendar")` before any client call — then ONE client call; a `legs` row gets its T+2 date
    (`with_dates`, None past the calendar). Raises `PortfolioUnavailable` (`connect`, `timeout`, `http_<status>`,
    `bad_body`, `incomplete`, `calendar`)."""
    holidays, workdays = _calendar(db)
    try:
        trade_from, trade_to = settlement_match.trade_bounds(settle_from, settle_to, extra_holidays=holidays,
                                                             extra_workdays=workdays)
    except settlement_match.CalendarExpired:
        raise PortfolioUnavailable("calendar") from None
    client = client if client is not None else portfolio_client.client_for()
    return _dated(db, client.settlements(broker_of(account), settle_from=settle_from, settle_to=settle_to,
                                         trade_from=trade_from, trade_to=trade_to))


def fetch_by_trade_date(db: Session, account: Account, trade_from: date, trade_to: date, *,
                        client: PortfolioClient | None = None) -> Feed:
    """The trade days `trade_from … trade_to` on the trade axis (`settle_from = settle_to = trade_from`): every row of
    those trade dates whatever its settlement date (Task 2 contract), plus rows of OTHER trade dates that settle on
    `trade_from` — callers select `r.trade_date` (and, for a booking, `r.fingerprint`) (M1). Raises
    `PortfolioUnavailable`."""
    client = client if client is not None else portfolio_client.client_for()
    return _dated(db, client.settlements(broker_of(account), settle_from=trade_from, settle_to=trade_from,
                                         trade_from=trade_from, trade_to=trade_to))


def facts_of(settlement: Settlement) -> dict:
    """The one facts schema (`FACTS_KEYS`; M6): dates ISO, amounts 4-dp strings, items sorted by `source_id`."""
    return {
        "fingerprint": settlement.fingerprint,
        "trade_date": settlement.trade_date.isoformat(),
        "settlement_date": None if settlement.settlement_date is None else settlement.settlement_date.isoformat(),
        "net_amount": f"{Decimal(settlement.net_amount).quantize(_Q)}",
        "date_source": settlement.date_source,
        "bookable": settlement.bookable,
        "items": [{"source_id": i.source_id, "type": i.type, "amount": f"{Decimal(i.amount).quantize(_Q)}"}
                  for i in sorted(settlement.items, key=lambda i: i.source_id)],
    }


def _comparable(facts: dict) -> tuple:
    return (Decimal(facts["net_amount"]).quantize(_Q), facts["settlement_date"],
            frozenset((i["source_id"], Decimal(i["amount"]).quantize(_Q)) for i in facts["items"]),
            facts["bookable"], facts["date_source"])


def same_facts(booked: dict, now: dict | None) -> bool:
    """Spec §4.4 item 4: net, settlement date, the `(source_id, amount)` set, `bookable` and `date_source` agree."""
    return now is not None and _comparable(booked) == _comparable(now)


# --- rows, twins, locks ---------------------------------------------------------------------------------------------


def twin_window_days(db: Session) -> int:
    """`settlement.twin_window_days` (the settings block's default is the spec's 3)."""
    block = settings_service.read_reconciliation_settings(db).get("settlement") or {}
    return int(block.get("twin_window_days", TWIN_WINDOW_DAYS))


def booked_rows(db: Session, account_id: int) -> list[SettlementBooking]:
    """The account's `booked` settlement rows, both origins, in id order, refreshed from the database (an owner
    delete in another transaction set a leg column NULL)."""
    return list(db.scalars(select(SettlementBooking).where(
        SettlementBooking.account_id == account_id, SettlementBooking.kind == KIND,
        SettlementBooking.status == "booked").order_by(SettlementBooking.id)
        .execution_options(populate_existing=True)))


def bookings_by_event(db: Session, account_id: int) -> dict[int, SettlementBooking]:
    """Bank event id → the `booked` row holding it: a bank-origin booking (`book_settlement`) or a claimed stand-in
    (`link_claims`)."""
    return {row.event_id: row for row in booked_rows(db, account_id) if row.event_id is not None}


def booked_legs(db: Session, account_id: int) -> dict[int, SettlementBooking]:
    """Entry id → its `booked` row for the legs of either origin — the stand-ins (`stand_ins.settlement_legs`) and
    the bank bookings (`bookings_by_event`) alike. A line one of these covers has its settlement identified and
    reserved (spec §4.3 rev 4.5, B7)."""
    return {entry_id: row for row in booked_rows(db, account_id)
            for entry_id in (row.out_entry_id, row.in_entry_id) if entry_id is not None}


def reservations(db: Session) -> tuple[set[str], set[str]]:
    """What the constraints reserve (spec §4.4 item 1): the fingerprints of every `booked` settlement row (the partial
    unique) and every constituent id still held by an item row (`ux_settlement_booking_item_source` is not partial: a
    `booked` row's items, and an `owner_edited` row's, which §4.7 keeps because the edited pair still records the
    event — a booking of them would fail the constraint, so the assignment never offers them). Read after
    `housekeep`, so an owner-deleted booking (items deleted) no longer reserves."""
    fps = set(db.scalars(select(SettlementBooking.fingerprint).where(
        SettlementBooking.kind == KIND, SettlementBooking.status == "booked")))
    ids = set(db.scalars(select(SettlementBookingItem.source_id)
                         .join(SettlementBooking, SettlementBooking.id == SettlementBookingItem.booking_id)
                         .where(SettlementBooking.kind == KIND)))
    return fps, ids


def find_twins(db: Session, *, account_id: int, target_account_id: int | None, amount: Decimal,
               settlement_date: date, window_days: int, exclude: Iterable[int] = ()) -> list[int]:
    """Owner records of this settlement (spec §4.7 Gates, owner ruling Q5), the `auto_pay_service.find_twins` shape:
    top-level entries NOT of statement source with `entry_date` within `window_days` of `settlement_date` — on
    `account_id` an entry of `amount` (the net: negative = money out of the bank), or on `target_account_id` a
    transfer leg of `−amount` whose peer leg is on `account_id`."""
    window = timedelta(days=window_days)
    peer = aliased(LedgerEntry)
    mirror = and_(LedgerEntry.account_id == target_account_id, LedgerEntry.amount == -amount,
                  LedgerEntry.transfer_group_id.is_not(None),
                  exists().where(peer.transfer_group_id == LedgerEntry.transfer_group_id, peer.id != LedgerEntry.id,
                                 peer.account_id == account_id))
    own = and_(LedgerEntry.account_id == account_id, LedgerEntry.amount == amount)
    query = select(LedgerEntry.id).where(
        LedgerEntry.parent_entry_id.is_(None), LedgerEntry.source != STATEMENT_SOURCE,
        LedgerEntry.entry_date.between(settlement_date - window, settlement_date + window),
        own if target_account_id is None else or_(own, mirror))
    excluded = list(exclude)
    if excluded:
        query = query.where(LedgerEntry.id.not_in(excluded))
    return list(db.scalars(query.order_by(LedgerEntry.id)))


def lock_pair(db: Session, row: SettlementBooking) -> tuple[LedgerEntry | None, LedgerEntry | None]:
    """The booking row (refreshed in place) then its (out, in) legs, all `FOR UPDATE NOWAIT` in one savepoint
    (`pair_locks.lock_row_and_legs`); a gone leg comes back None. A held row or leg raises `settlement_busy`, never a
    wait: a blocking row lock would close a cycle against a reconcile that holds its statement row and waits for the
    booking row in `retire_twins_for_statement` (module docstring)."""
    return pair_locks.lock_row_and_legs(db, row, leg_ids=(row.out_entry_id, row.in_entry_id),
                                        busy_code=SETTLEMENT_BUSY)


def _leg(db: Session, entry_id: int | None) -> LedgerEntry | None:
    return None if entry_id is None else db.get(LedgerEntry, entry_id, populate_existing=True)


def _system_pair(out_leg: LedgerEntry | None, in_leg: LedgerEntry | None) -> bool:
    return (out_leg is not None and in_leg is not None and out_leg.source == STATEMENT_SOURCE
            and in_leg.source == STATEMENT_SOURCE)


def _figures(row: SettlementBooking) -> dict:
    return {"booking_id": row.id, "origin": row.origin, "account_id": row.account_id,
            "target_account_id": row.target_account_id, "fingerprint": row.fingerprint,
            "settlement_date": row.settlement_date.isoformat(), "net_amount": str(row.net_amount)}


def _active_on(db: Session, entry_ids: list[int]) -> bool:
    return bool(entry_ids) and db.scalar(select(exists().where(
        StatementCoverage.entry_id.in_(entry_ids), StatementCoverage.status == "active")))


def housekeep(db: Session, account_id: int) -> dict[str, int]:
    """Spec §4.7 Housekeeping over every `booked` row of the account, both origins (AGENT-360 major 1, M8): both legs
    gone → `owner_deleted` and its items deleted (the reservation is released in the same transaction), audit
    `settlement_owner_deleted`; a leg gone or no longer `source='statement'` → `owner_edited`, items kept (the edited
    pair still records the event), audit `settlement_owner_edited`. Read without locks first; a row to move is then
    locked row → legs NOWAIT (`lock_pair`) and re-checked; a held one (`settlement_busy`) is left for the next run and
    keeps reserving (fail closed). Departure from auto-pay: its owner-delete / owner-edit step runs in its job only
    (`check_row`); the bank origin has no job, so `reconcile` runs this before it reads the reservations. Flushes,
    never commits."""
    counts = {"owner_deleted": 0, "owner_edited": 0, "settlement_busy": 0}
    for row in booked_rows(db, account_id):
        if _system_pair(_leg(db, row.out_entry_id), _leg(db, row.in_entry_id)):
            continue
        try:
            out_leg, in_leg = lock_pair(db, row)
        except CodedConflictError as exc:
            if exc.code != SETTLEMENT_BUSY:
                raise
            logger.info("settlement.housekeep_busy", extra={"settlement_booking_id": row.id})
            counts["settlement_busy"] += 1
            continue
        if row.status != "booked" or _system_pair(out_leg, in_leg):  # re-checked under the locks
            continue
        if out_leg is None and in_leg is None:
            row.status = "owner_deleted"
            row.items.clear()  # delete-orphan: the reservation goes with the row's `booked` status
            event = "settlement_owner_deleted"
        else:
            row.status = "owner_edited"
            event = "settlement_owner_edited"
        db.flush()
        audit.record(db, event=event, actor=ACTOR,
                     detail={**_figures(row), "out_entry_id": row.out_entry_id, "in_entry_id": row.in_entry_id})
        counts[row.status] += 1
    return counts


def retire_pair(db: Session, row: SettlementBooking, *, reason: str) -> set[int] | None:
    """Delete the row's pair and mark it `retired` (`replaced` for `reason="replaced"`): lock the row, then both
    legs (`lock_pair`), and re-validate under the locks — the row still `booked`, both legs present and
    `source='statement'`; for `reason="twin"` the owner twin must still exist (`find_twins`, the legs excluded) and
    the pair's coverage is released (the auto-pay sequence: the line re-meets the twin); for EVERY other reason
    (`gone`, a feed `reason`, `replaced`) the legs' active coverage is re-read under the locks and any active row →
    None: a claimed pair is never retired by the job (AGENT-360 major 2; its later change is reconcile's
    `source_drift`). A failed re-check returns None and leaves no lock behind (one savepoint). Otherwise release,
    `delete_entries_cascade`, items deleted, status / reason set, the released statements `needs_recheck`, audit
    `settlement_retired`; returns the released statement ids (possibly empty). A held row or leg raises
    `settlement_busy`. Flushes, never commits."""
    locks = db.begin_nested()
    try:
        out_leg, in_leg = lock_pair(db, row)
        if row.status != "booked" or not _system_pair(out_leg, in_leg):
            locks.rollback()
            return None
        legs = [out_leg.id, in_leg.id]
        if reason == "twin":
            if not find_twins(db, account_id=row.account_id, target_account_id=row.target_account_id,
                              amount=row.net_amount, settlement_date=row.settlement_date,
                              window_days=twin_window_days(db), exclude=legs):
                locks.rollback()
                return None
        elif _active_on(db, legs):  # claimed: never retired for a source reason (coverage re-read under the locks)
            locks.rollback()
            return None
    except BaseException:
        if locks.is_active:
            locks.rollback()
        raise
    locks.commit()  # release the savepoint; the row and leg locks stay until the caller commits
    # delete_entries_cascade ends with expire_all and the leg FKs are ON DELETE SET NULL: copy everything first
    row_id, figures = row.id, _figures(row)
    released = coverage_service.release_entries(db, legs, reason=f"settlement:{reason}") if reason == "twin" else set()
    entry_write_service.delete_entries_cascade(db, legs)
    row = db.get(SettlementBooking, row_id)
    row.status, row.reason = ("replaced" if reason == "replaced" else "retired"), reason
    row.items.clear()
    for sid in released:
        db.get(AccountStatement, sid).needs_recheck = True
    db.flush()
    audit.record(db, event="settlement_retired", actor=ACTOR,
                 detail={**figures, "reason": reason, "out_entry_id": legs[0], "in_entry_id": legs[1],
                         "released_statement_ids": sorted(released)})
    return released


def retire_twins_for_statement(db: Session, statement: AccountStatement, accounts: list[int]) -> list[int]:
    """The reconcile's half of owner-wins (spec §4.7 "Bank line", the `auto_pay_service.retire_twins_for_statement`
    sequence), run before the population is read: every `booked` settlement row of either origin on `accounts` whose
    settlement date lies in the period ± `matching.population_window_days`, both legs still the system's, and an
    owner twin within `twin_window_days` → `retire_pair(reason="twin")`. A pair another transaction holds
    (`settlement_busy`) is logged and left for the next run; a failed locked re-check is skipped. Returns the retired
    row ids. Flushes, never commits."""
    from app.services.reconciliation_service import rules_for  # local: the reconciler calls this function

    rules = rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    span = timedelta(days=matching.population_window_days(rules))
    lo, hi = statement.period_start - span, statement.period_end + span
    rows = list(db.scalars(select(SettlementBooking).where(
        SettlementBooking.account_id.in_(accounts), SettlementBooking.kind == KIND,
        SettlementBooking.status == "booked", SettlementBooking.settlement_date.between(lo, hi))
        .order_by(SettlementBooking.id)))
    if not rows:
        return []
    window = twin_window_days(db)
    out = []
    for row in rows:
        legs = [_leg(db, i) for i in (row.out_entry_id, row.in_entry_id)]
        if not _system_pair(*legs):
            continue  # housekeeping's owner_edited / owner_deleted
        if not find_twins(db, account_id=row.account_id, target_account_id=row.target_account_id,
                          amount=row.net_amount, settlement_date=row.settlement_date, window_days=window,
                          exclude=[leg.id for leg in legs]):
            continue
        try:
            released = retire_pair(db, row, reason="twin")
        except CodedConflictError as exc:
            if exc.code != SETTLEMENT_BUSY:
                raise
            logger.info("settlement.sweep_retire_busy",
                        extra={"settlement_booking_id": row.id, "statement_id": statement.id})
            continue
        if released is not None:
            out.append(row.id)
    return out


def link_claims(db: Session, statement: AccountStatement) -> list[int]:
    """Spec §4.7 "Bank line": a booked stand-in whose bank leg holds active coverage on a current line of this
    statement is the bank-attested booking — `event_id` = that line's event (its row stays `booked`), audit
    `settlement_claimed` when it changes; idempotent. The booking row is try-locked NOWAIT (the reconcile already
    holds the leg it claimed, so a blocking row lock would invert the row → legs order); a held row is linked by the
    next reconcile. Returns the ids of every stand-in claimed on this statement (linked now or before)."""
    legs = stand_ins.settlement_legs(db, [statement.account_id])
    if not legs:
        return []
    rows = db.execute(
        select(StatementCoverage.entry_id, StatementLine.id, StatementLine.event_id)
        .join(StatementLine, StatementLine.id == StatementCoverage.line_id)
        .where(StatementCoverage.statement_id == statement.id, StatementCoverage.status == "active",
               StatementCoverage.entry_id.in_(legs), StatementLine.revision_id == statement.current_revision_id)
        .order_by(StatementCoverage.id)).all()
    out = []
    for entry_id, line_id, event_id in rows:
        booking_id = legs[entry_id]
        row = db.get(SettlementBooking, booking_id)
        out.append(booking_id)
        if row.event_id == event_id:
            continue
        try:
            row = pair_locks.lock_row(db, SettlementBooking, booking_id, busy_code=SETTLEMENT_BUSY,
                                      label="settlement_booking row")
        except CodedConflictError as exc:
            if exc.code != SETTLEMENT_BUSY:
                raise
            logger.info("settlement.link_busy", extra={"settlement_booking_id": booking_id})
            continue
        if row is None or row.status != "booked":
            continue
        row.event_id = event_id
        db.flush()
        audit.record(db, event="settlement_claimed", actor=ACTOR, statement_id=statement.id,
                     detail={**_figures(row), "event_id": event_id, "line_id": line_id, "entry_id": entry_id})
    return sorted(set(out))


# --- reconcile -------------------------------------------------------------------------------------------------------


def applies(account: Account, statement: AccountStatement, lines: Iterable[StatementLine],
            rules: matching.Rules) -> bool:
    """Spec §4.3: the statement's account has a `settlement_source` and it is a bank statement printing a settlement
    line (`matching.is_settlement_line`)."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    return bool(account.settlement_source) and statement.kind == "bank" and any(
        matching.is_settlement_line(_as_line(line), rules) for line in lines)


def settle_window(statement: AccountStatement) -> tuple[date, date]:
    span = timedelta(days=SETTLE_MARGIN_DAYS)
    return statement.period_start - span, statement.period_end + span


def fetch_for(db: Session, account: Account, statement: AccountStatement, *,
              client: PortfolioClient) -> tuple[Feed | None, str | None]:
    """The reconcile's one settlement-window read (spec §4.3): `(feed, None)`, or `(None, code)` on any
    `PortfolioUnavailable` — `incomplete` and `calendar` included: nothing is assigned automatically then."""
    lo, hi = settle_window(statement)
    try:
        return fetch_by_settlement_window(db, account, lo, hi, client=client), None
    except PortfolioUnavailable as exc:
        logger.info("settlement.source_unavailable", extra={"statement_id": statement.id, "code": exc.code})
        return None, exc.code


@dataclass
class Settled:
    cases: list[matching.Case]  # the matcher's cases with every `settlement_line` placeholder replaced
    drift: list[dict] = field(default_factory=list)  # desired-case dicts (`source_drift`) for claimed lines
    counts: dict = field(default_factory=dict)
    unavailable: bool = False


COUNT_KEYS = ("booked", "ambiguous", "pending", "mismatch", "drift", "claimed", "unknown_dates")


def _graph(db: Session, statement: AccountStatement, all_lines: list[StatementLine], rules: matching.Rules,
           excluded_entries: set[int]) -> tuple[list[matching.Line], set[int]]:
    """Spec §4.3 "Assignment is global": EVERY settlement line of this statement and of the account's LIVE,
    non-retired neighbour statements (the `_guard_neighbours` shape over the account itself, bounded by this window
    rather than the population window, which the rules could set narrower) whose posted date lies in the settlement
    window ± `settlement_window_days`, covered or not, MINUS the lines whose active coverage entry is a `booked` row's
    leg (`excluded_entries`; rev 4.5 B7). Historical (shadow) statements stay as they are (§11). Returns `(lines,
    contestants)`: contestants are the remaining lines with active coverage anywhere (this run's claims included) —
    they compete and are never booked. Read only."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    lo, hi = settle_window(statement)
    edge = timedelta(days=rules.settlement_window_days)
    neighbours = dict(db.execute(select(AccountStatement.id, AccountStatement.current_revision_id).where(
        AccountStatement.account_id == statement.account_id, AccountStatement.id != statement.id,
        AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
        AccountStatement.period_start <= hi + edge, AccountStatement.period_end >= lo - edge,
        ~retired.clause(AccountStatement.id))).all())
    rows = list(all_lines)
    if neighbours:
        rows += db.scalars(select(StatementLine).where(StatementLine.revision_id.in_(list(neighbours.values())),
                                                       StatementLine.posted_date.between(lo - edge, hi + edge))
                           .order_by(StatementLine.id))
    statement_ids = sorted({statement.id, *neighbours})
    lines = [ln for ln in (_as_line(r) for r in rows) if matching.is_settlement_line(ln, rules)]
    ids = [ln.id for ln in lines]
    covered: dict[int, set[int | None]] = {}
    for start in range(0, len(ids), 200):
        for line_id, entry_id in db.execute(
                select(StatementCoverage.line_id, StatementCoverage.entry_id)
                .where(StatementCoverage.statement_id.in_(statement_ids),
                       StatementCoverage.line_id.in_(ids[start:start + 200]), StatementCoverage.status == "active")):
            covered.setdefault(line_id, set()).add(entry_id)
    gone = {lid for lid, entry_ids in covered.items() if entry_ids & excluded_entries}
    lines = [ln for ln in lines if ln.id not in gone]
    return lines, {ln.id for ln in lines if ln.id in covered}


def _free(rows: Iterable[Settlement], fps: set[str], ids: set[str]) -> list[Settlement]:
    return [s for s in rows if s.fingerprint not in fps and not ids.intersection(s.source_ids)]


def _nearest(line: matching.Line, rows: list[Settlement]) -> dict | None:
    """Spec §9 (D6): the dated, unreserved settlement of the window nearest the line — smallest |settlement date −
    posted date|, ties by the smallest |net − flow| — as `{date, delta}` with `delta = net − flow` (4-dp string)."""
    dated = [s for s in rows if s.settlement_date is not None]
    if not dated:
        return None
    s = min(dated, key=lambda s: (abs((s.settlement_date - line.posted_date).days), abs(s.net_amount - line.flow),
                                  s.fingerprint))
    return {"date": s.settlement_date.isoformat(), "delta": f"{(s.net_amount - line.flow).quantize(_Q)}"}


def _mismatched_stand_in(db: Session, account_id: int, line: matching.Line,
                         rules: matching.Rules) -> SettlementBooking | None:
    """Spec §4.3 bullet 3: an unclaimed booked stand-in of the account (`unclaimed_settlement_legs`) settling within
    `settlement_window_days` of the line — the nearest date first, then the nearest net, then id."""
    found = [db.get(SettlementBooking, row_id) for row_id in sorted(set(
        stand_ins.unclaimed_settlement_legs(db, account_id).values()))]
    found = [r for r in found if abs((r.settlement_date - line.posted_date).days) <= rules.settlement_window_days]
    if not found:
        return None
    return min(found, key=lambda r: (abs((r.settlement_date - line.posted_date).days),
                                     abs(Decimal(r.net_amount) - line.flow), r.id))


def _owner_deleted(db: Session, account_id: int, s: Settlement) -> bool:
    """An `owner_deleted` booking with this fingerprint or on this (account, settlement date) exists: the event is
    owner-deleted — proposed, never auto-booked (spec §4.7 Housekeeping, §9)."""
    return db.scalar(select(exists().where(
        SettlementBooking.kind == KIND, SettlementBooking.status == "owner_deleted",
        or_(SettlementBooking.fingerprint == s.fingerprint,
            and_(SettlementBooking.account_id == account_id,
                 SettlementBooking.settlement_date == s.settlement_date))))) is True


def _pending(line: matching.Line, reason: str | None, rows: list[Settlement] | None) -> matching.Case:
    return matching.Case(line.id, None, "line_unmatched", (), {
        "hint": "settlement_pending", "reason": reason, "nearest": None if rows is None else _nearest(line, rows)})


def _now_for(db: Session, account: Account, row: SettlementBooking, client: PortfolioClient) -> dict | None:
    """The booking's settlement as the feed shows it now (`facts_now` over its booked fingerprint, trade date and
    settlement date). None: gone."""
    return facts_now(db, account, fingerprint=row.facts["fingerprint"],
                     trade_date=row.trade_date or date.fromisoformat(row.facts["trade_date"]),
                     settlement_date=row.settlement_date, client=client)


def facts_now(db: Session, account: Account, *, fingerprint: str, trade_date: date, settlement_date: date | None,
              client: PortfolioClient | None = None) -> dict | None:
    """A settlement's facts as the feed shows them now (M1; `book_settlement` and the drift re-read): its trade day
    re-read on the trade axis and the row of that trade date with the fingerprint; a pair (fingerprint `a+b`,
    `trade_date` the earlier part, never re-read by one trade date) re-reads `trade_date … settlement_date` and merges
    its parts again. None: gone (or a pair without a settlement date). Raises `PortfolioUnavailable`."""
    parts = fingerprint.split("+")
    hi = trade_date if len(parts) == 1 else settlement_date
    if hi is None:
        return None
    feed = fetch_by_trade_date(db, account, trade_date, hi, client=client)
    found = sorted((r for r in feed.rows if r.fingerprint in parts and trade_date <= r.trade_date <= hi
                    and (len(parts) > 1 or r.trade_date == trade_date)), key=lambda r: r.fingerprint)
    if len(found) != len(parts) or len({r.fingerprint for r in found}) != len(parts):
        return None
    merged = found[0] if len(found) == 1 else settlement_match.merge(tuple(found))
    return None if merged is None else facts_of(merged)


def _drift(db: Session, statement: AccountStatement, account: Account, all_lines: list[StatementLine],
           legs: dict[int, SettlementBooking], client: PortfolioClient | None, skip_reads: bool) -> list[dict]:
    """Spec §4.4 item 4 (AGENT-360 major 6): every current line of the statement whose active coverage entry is a
    `booked` row's leg (either origin, owned and matcher coverage alike) has the row's facts re-read
    (`_now_for`); a difference or a vanished row → a `settlement` case `{"hint": "source_drift", "booking_id",
    "booked", "now"}` without candidates on that line (taken from `all_lines`). The pair stays; nothing is rebooked.
    When the feed cannot be read (`skip_reads`, or a re-read raises) an open `source_drift` case of the line is
    carried as it stands, never superseded by silence; the first re-read that raises sets `skip_reads` for the
    remaining lines, so a portfolio that stops answering costs one client timeout, not one per line, while this
    transaction holds the statement row and its locks."""
    from app.services.reconciliation_service import _case  # local: the reconciler calls this function

    by_line = {ln.id: ln for ln in all_lines}
    held: dict[int, SettlementBooking] = {}
    for line_id, rows in coverage_service.active_rows(db, statement.id).items():
        row = next((legs[r.entry_id] for r in rows if r.entry_id in legs), None)
        if row is not None and line_id in by_line:
            held[line_id] = row
    if not held:
        return []
    carried = {(c.line_id, (c.context or {}).get("booking_id")): c for c in db.scalars(
        select(ReconciliationCase).where(ReconciliationCase.statement_id == statement.id,
                                         ReconciliationCase.kind == KIND,
                                         ReconciliationCase.status.in_(coverage_service.OPEN_CASE_STATUSES))
        .order_by(ReconciliationCase.id)) if (c.context or {}).get("hint") == "source_drift"}
    out = []
    for line_id, row in sorted(held.items()):
        line = by_line[line_id]
        now, readable = None, not skip_reads
        if readable:
            try:
                now = _now_for(db, account, row, client)
            except PortfolioUnavailable:
                readable, skip_reads = False, True
        if not readable:
            case = carried.get((line_id, row.id))
            if case is not None:  # `_write_cases` keeps it: the same key (its sweep note is not part of it)
                out.append(_case(KIND, event_id=line.event_id, line_id=line.id, candidates=case.candidates or (),
                                 context=case.context))
            continue
        if same_facts(row.facts, now):
            continue
        out.append(_case(KIND, event_id=line.event_id, line_id=line.id, candidates=(),
                         context={"hint": "source_drift", "booking_id": row.id, "booked": row.facts, "now": now}))
    return out


def settle(db: Session, statement: AccountStatement, account: Account, result: matching.MatchResult,
           all_lines: list[StatementLine], rules: matching.Rules, feed: Feed | None, unavailable_code: str | None, *,
           client: PortfolioClient | None) -> Settled:
    """The reconcile's settlement outcomes (spec §4.3, §4.4 item 4, §4.7 "Bank line", §9), after the statement's
    coverage is written: `link_claims`; the global assignment over `_graph` (reservations read after `housekeep`);
    each `settlement_line` placeholder replaced — booked → `settlement` (candidates `[facts]`, context `{fingerprint,
    source, owner_deleted}`); ambiguous → `ambiguous` + `settlement_ambiguous` (candidates the settlements); pending
    → `line_unmatched` + `settlement_pending` (`reason`, `nearest`), or, with no candidate (`reason is None`) beside an
    unclaimed stand-in within the window, `settlement_mismatch` naming it (a sole non-bookable candidate keeps
    `settlement_pending` with its reason, D8); the feed unavailable / incomplete / past the calendar → every
    placeholder `settlement_pending` and `unavailable`. Then the drift cases (live statements only). Counts per
    `COUNT_KEYS` + `unavailable_code`."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    counts: dict = dict.fromkeys(COUNT_KEYS, 0)
    counts["unavailable_code"] = unavailable_code
    counts["claimed"] = len(link_claims(db, statement))
    placeholders = {c.line_id for c in result.cases
                    if c.kind == "line_unmatched" and (c.context or {}).get("hint") == matching.SETTLEMENT_LINE_HINT}
    lines = {row.id: _as_line(row) for row in all_lines}
    legs = booked_legs(db, account.id)
    outcome: dict[int, matching.Case] = {}
    if feed is None:
        for line_id in placeholders:
            outcome[line_id] = _pending(lines[line_id], None, None)
        counts["pending"] = len(placeholders)
    else:
        fps, ids = reservations(db)
        graph, contestants = _graph(db, statement, all_lines, rules, set(legs))
        assignment = settlement_match.assign(graph, list(feed.rows), reserved_fps=fps, reserved_ids=ids,
                                             contestants=contestants, rules=rules)
        counts["unknown_dates"] = assignment.unknown_dates
        free = _free(feed.rows, fps, ids)
        for line_id in sorted(placeholders):
            line = lines[line_id]
            if line_id in assignment.booked:
                s = assignment.booked[line_id]
                outcome[line_id] = matching.Case(line_id, None, KIND, (facts_of(s),), {
                    "fingerprint": s.fingerprint, "source": account.settlement_source,
                    "owner_deleted": _owner_deleted(db, account.id, s)})
                counts["booked"] += 1
            elif line_id in assignment.ambiguous:
                outcome[line_id] = matching.Case(line_id, None, "ambiguous",
                                                 tuple(facts_of(s) for s in assignment.ambiguous[line_id]),
                                                 {"hint": "settlement_ambiguous"})
                counts["ambiguous"] += 1
            else:
                reason = assignment.pending.get(line_id)
                stand_in = None if reason is not None else _mismatched_stand_in(db, account.id, line, rules)
                if stand_in is not None:
                    outcome[line_id] = matching.Case(line_id, None, "line_unmatched", (), {
                        "hint": "settlement_mismatch", "settlement_booking_id": stand_in.id,
                        "stand_in_date": stand_in.settlement_date.isoformat(),
                        "stand_in_amount": f"{Decimal(stand_in.net_amount).quantize(_Q)}"})
                    counts["mismatch"] += 1
                else:
                    outcome[line_id] = _pending(line, reason, free)
                    counts["pending"] += 1
    cases = [outcome.get(c.line_id, c) if c.line_id in placeholders else c for c in result.cases]
    skip_reads = unavailable_code is not None and unavailable_code not in DRIFT_READ_AFTER
    # drift cases are live-only (the reconciler writes no historical cases): a historical reconcile re-reads nothing
    drift = _drift(db, statement, account, all_lines, legs, client, skip_reads) if statement.mode == "live" else []
    counts["drift"] = sum(1 for d in drift if d["context"].get("hint") == "source_drift")
    return Settled(cases=cases, drift=drift, counts=counts, unavailable=unavailable_code is not None)


def flag_neighbours(db: Session, statement: AccountStatement) -> int:
    """Spec §4.5 item 3: once a revision of the statement became current, the account's other live, non-retired
    statements whose period overlaps `period_start − 7 … period_end + 7` get `needs_recheck`, so no case derived
    before the new lines existed stays actionable. One UPDATE of statement rows (the `needs_recheck` flag `retire_pair`
    writes the same way); returns the number flagged."""
    span = timedelta(days=NEIGHBOUR_RECHECK_DAYS)
    count = db.execute(
        update(AccountStatement)
        .where(AccountStatement.account_id == statement.account_id, AccountStatement.id != statement.id,
               AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
               AccountStatement.period_start <= statement.period_end + span,
               AccountStatement.period_end >= statement.period_start - span,
               AccountStatement.needs_recheck.is_(False), ~retired.clause(AccountStatement.id))
        .values(needs_recheck=True)
        .execution_options(synchronize_session=False)).rowcount
    db.flush()
    return count


def flag_neighbours_safe(db: Session, statement_id: int) -> int:
    """For the ingest routes, after their commit (`SubmitResult.neighbours_recheck`, the `run_rules_safe` pattern):
    `flag_neighbours` in a short transaction of its own, so neither a yielded hook pass (`reconcile_busy`,
    `sweep_barrier_busy`, the import key) nor a lost deadlock drops the flag, and the UPDATE runs while no statement
    row of the ingest is held. Any error rolls back and is only logged (ids only); returns the number flagged."""
    try:
        statement = db.get(AccountStatement, statement_id)
        count = 0 if statement is None else flag_neighbours(db, statement)
        db.commit()
        return count
    except Exception:  # noqa: BLE001  (the revision is stored; the daily re-check is the backstop)
        logger.exception("settlement.neighbour_flag_failed", extra={"statement_id": statement_id})
        db.rollback()
        return 0
