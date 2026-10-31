"""國泰 settlement bookings (portfolio integration spec §4.3–§4.5, §4.7, §9): the reconcile half — the feed read, the
global assignment's case outcomes, the stand-in claim link, drift over claimed lines, the owner-delete / owner-edit
housekeeping of both origins and the twin retirement; and the daily stand-in job half (§4.7, owner decision Q7).

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
- Stand-in job (`run_stand_ins`, §4.7): on each settlement day a `bookable`, statement-dated feed row on or after
  go-live is booked as the same transfer under a `stand_in` row (`stand_in_candidates` → `gate` → `book_stand_in`,
  else `record_skip`); the bank line later claims it on the exact date (reconcile). The job's own UNCLAIMED rows are
  retired (owner twin, feed row gone or no longer bookable) or replaced (the trade day's facts changed: `replace`,
  one transaction) by `check_row`; a claimed stand-in's later change is reconcile's `source_drift`. Departures from
  auto-pay (C4: the lifecycles differ, only pure helpers and the lock step are shared): no statement header; a pair
  is replaceable while unclaimed; retirement for a source reason refuses a claimed pair (the claim is released for
  twins only); `housekeep` also runs inside reconcile; and, like auto-pay, nothing takes the account row.

Locks (§4.7 Locks, rev 4.3 — the one protocol): the booking row FOR UPDATE NOWAIT → its two legs FOR UPDATE NOWAIT →
coverage re-read, in one savepoint (`lock_pair` over `pair_locks.lock_row_and_legs`); a held row or leg is
`settlement_busy`, never a wait. Nothing here takes an account row: the engine's account-row takers hold no entry or
group lock (engine_locks.py:1-9), and a reconcile or job holding entry locks or a statement row while waiting for the
account row would close the two cycles §4.7 Locks (a)/(b) describes. `reconcile` claims a stand-in leg only while it
holds that leg (`_lock_candidates`, NOWAIT), so a job holding the legs makes the reconcile `reconcile_busy` and a
reconcile holding them makes the job skip the row. The C4 ruling keeps this lifecycle separate from auto-pay's on
purpose (claimed pairs refused except for twins, replacement, two origins, feed facts instead of a header); only the
lock step is shared. The one account-row lock that remains is implicit: a leg insert (`book_stand_in`, the job's
replacement too) takes the FK's `FOR KEY SHARE` on the account row while the job holds the shared DIRTY barrier, so it
can deadlock with an engine action holding the account row FOR UPDATE and waiting for the barrier; Postgres detects
it — the job's decision counts `failed` and retries next run, the engine answers 409 `sweep_barrier_busy` — the same
shape as the auto-pay job. Nothing here commits — callers own the transaction (flush only) — except
`run_stand_ins`, which runs every decision in a transaction of its own under `SETTLEMENT_JOB_LOCK_KEY`."""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
import dataclasses
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, exists, func, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationCase, SettlementBooking, SettlementBookingItem,
    StatementCoverage, StatementLine,
)
from app.schemas.writes import SETTLEMENT_SOURCES, SOURCE_CURRENCIES, TransferIn
from app.services import (
    auto_pay_service, coverage_service, entry_write_service, ledger_service, pair_locks, portfolio_client,
    settings_service, stand_ins, transfer_service,
)
from app.services.entry_write_service import STATEMENT_SOURCE
from app.services.errors import CodedConflictError
from app.services.portfolio_client import Feed, PortfolioClient, PortfolioUnavailable, Settlement
from app.services.reconciliation_actions import audit
from app.services.schedule_locks import SETTLEMENT_JOB_LOCK_KEY, ImportRunningError, take_import_key_shared
from app.services.statements import matching, retired, settlement_match

ACTOR = "settlement"
SETTLEMENT_BUSY = "settlement_busy"
CURRENCY_MISMATCH = "currency_mismatch"  # PR #234 review finding 1 (`currency_mismatch`)
KIND = "settlement"
TWIN_WINDOW_DAYS = 3  # spec §8 `settlement.twin_window_days` default (`settings_service.SETTLEMENT_DEFAULTS`)
SETTLE_MARGIN_DAYS = 5  # spec §4.3: the settlements whose date can fall in period_start − 5 … period_end + 5
NEIGHBOUR_RECHECK_DAYS = 7  # spec §4.5 item 3: the revision hook flags statements within period ± 7
FACTS_KEYS = ("fingerprint", "trade_date", "settlement_date", "net_amount", "date_source", "bookable", "items")
# window-read failures after which the drift re-reads are still tried: the calendar fault made no call, and an
# `incomplete` answer came from a reachable server; after a transport / body failure they are skipped (one timeout)
DRIFT_READ_AFTER = frozenset({"calendar", "incomplete"})
_Q = Decimal("0.0001")
TRANSFER_NAME = "證券交割"  # the pair's name, both origins (spec §4.4 item 2, §4.7 Booking)
# a booking's unique violations → the code both origins report (Task 7: 409 codes; Task 9: skip reasons)
UNIQUE_CODES = (("ux_settlement_booking_item_source", "constituent_reserved"),
                ("ux_settlement_booking_fingerprint_booked", "already_booked"),
                ("ux_settlement_booking_day_booked", "day_booked"))

logger = logging.getLogger(__name__)


# --- feed -----------------------------------------------------------------------------------------------------------


def broker_of(account: Account) -> str:
    """`portfolio:cathay` → `cathay` (spec §4.2: the feed's `broker` parameter)."""
    return (account.settlement_source or "").removeprefix("portfolio:")


def currency_mismatch(account: Account, rows: Iterable[Settlement] = ()) -> bool:
    """PR #234 review finding 1, fail-closed: the account is outside the currency its known source answers in
    (`SOURCE_CURRENCIES`), or a feed row is in a currency other than the account's. Nothing is booked, retired or
    replaced from such a feed (`outcomes`, `facts_now`, the job). Pure."""
    expected = SOURCE_CURRENCIES.get(account.settlement_source)
    return (expected is not None and account.currency != expected) or any(
        r.currency != account.currency for r in rows)


def _calendar(db: Session) -> tuple[frozenset[date], frozenset[date]]:
    """The bank calendar overrides (`auto_pay.extra_holidays` / `extra_workdays`, owner ruling Q1 (b))."""
    return auto_pay_service.calendar_sets(settings_service.read_auto_pay_settings(db))


def _dated(db: Session, feed: Feed) -> Feed:
    holidays, workdays = _calendar(db)
    rows = settlement_match.with_dates(list(feed.rows), extra_holidays=holidays, extra_workdays=workdays)
    return dataclasses.replace(feed, rows=tuple(rows))


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
           excluded_entries: set[int], coverage: Mapping[int, Iterable[int | None]] | None = None,
           ) -> tuple[list[matching.Line], set[int]]:
    """Spec §4.3 "Assignment is global": EVERY settlement line of this statement and of the account's LIVE,
    non-retired neighbour statements (the `_guard_neighbours` shape over the account itself, bounded by this window
    rather than the population window, which the rules could set narrower) whose posted date lies in the settlement
    window ± `settlement_window_days`, covered or not, MINUS the lines whose active coverage entry is a `booked` row's
    leg (`excluded_entries`; rev 4.5 B7). Historical (shadow) statements stay as they are (§11). Returns `(lines,
    contestants)`: contestants are the remaining lines with active coverage anywhere (this run's claims included) —
    they compete and are never booked. `coverage` (`outcomes`): this statement's active coverage as the run leaves it,
    line id → entry ids, in place of this statement's stored rows (None: read them). Read only."""
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
    statement_ids = sorted({statement.id, *neighbours} if coverage is None else neighbours)
    lines = [ln for ln in (_as_line(r) for r in rows) if matching.is_settlement_line(ln, rules)]
    ids = [ln.id for ln in lines]
    covered: dict[int, set[int | None]] = {}
    if coverage is not None:
        own = {r.id for r in all_lines}
        covered.update({lid: set(coverage[lid]) for lid in ids if lid in own and lid in coverage})
    for start in range(0, len(ids) if statement_ids else 0, 200):
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
                         rules: matching.Rules, claimed: set[int] = frozenset()) -> SettlementBooking | None:
    """Spec §4.3 bullet 3: an unclaimed booked stand-in of the account (`unclaimed_settlement_legs`) settling within
    `settlement_window_days` of the line — the nearest date first, then the nearest net, then id. `claimed`: entry
    ids the run claims without stored coverage yet (`outcomes` with a `coverage` override); their rows are claimed."""
    found = [db.get(SettlementBooking, row_id) for row_id in sorted(set(
        row_id for entry_id, row_id in stand_ins.unclaimed_settlement_legs(db, account_id).items()
        if entry_id not in claimed))]
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
    its parts again. None: gone (or a pair without a settlement date). Raises `PortfolioUnavailable` — the feed's,
    or `currency_mismatch` for a row in another currency than the account's (`currency_mismatch`)."""
    parts = fingerprint.split("+")
    hi = trade_date if len(parts) == 1 else settlement_date
    if hi is None:
        return None
    feed = fetch_by_trade_date(db, account, trade_date, hi, client=client)
    found = sorted((r for r in feed.rows if r.fingerprint in parts and trade_date <= r.trade_date <= hi
                    and (len(parts) > 1 or r.trade_date == trade_date)), key=lambda r: r.fingerprint)
    if len(found) != len(parts) or len({r.fingerprint for r in found}) != len(parts):
        return None
    if currency_mismatch(account, found):
        raise PortfolioUnavailable(CURRENCY_MISMATCH)
    merged = found[0] if len(found) == 1 else settlement_match.merge(tuple(found))
    return None if merged is None else facts_of(merged)


def _drift(db: Session, statement: AccountStatement, account: Account, all_lines: list[StatementLine],
           legs: dict[int, SettlementBooking], client: PortfolioClient | None, skip_reads: bool,
           coverage: Mapping[int, Iterable[int | None]] | None = None) -> list[dict]:
    """Spec §4.4 item 4 (AGENT-360 major 6): every current line of the statement whose active coverage entry is a
    `booked` row's leg (either origin, owned and matcher coverage alike) has the row's facts re-read
    (`_now_for`); a difference or a vanished row → a `settlement` case `{"hint": "source_drift", "booking_id",
    "booked", "now"}` without candidates on that line (taken from `all_lines`). The pair stays; nothing is rebooked.
    When the feed cannot be read (`skip_reads`, or a re-read raises) an open `source_drift` case of the line is
    carried as it stands, never superseded by silence; the first re-read that raises sets `skip_reads` for the
    remaining lines, so a portfolio that stops answering costs one client timeout, not one per line, while this
    transaction holds the statement row and its locks. `coverage` as in `_graph`."""
    from app.services.reconciliation_service import _case  # local: the reconciler calls this function

    by_line = {ln.id: ln for ln in all_lines}
    held: dict[int, SettlementBooking] = {}
    entry_ids = coverage if coverage is not None else {
        line_id: [r.entry_id for r in rows] for line_id, rows in coverage_service.active_rows(db, statement.id).items()}
    for line_id, entries in entry_ids.items():
        row = next((legs[e] for e in entries if e in legs), None)
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
    coverage is written: `link_claims` (the one write; `counts["claimed"]`), then `outcomes` over the coverage just
    written."""
    claimed = len(link_claims(db, statement))
    settled = outcomes(db, statement, account, result, all_lines, rules, feed, unavailable_code, client=client)
    settled.counts["claimed"] = claimed
    return settled


def outcomes(db: Session, statement: AccountStatement, account: Account, result: matching.MatchResult,
             all_lines: list[StatementLine], rules: matching.Rules, feed: Feed | None, unavailable_code: str | None, *,
             client: PortfolioClient | None, coverage: Mapping[int, Iterable[int | None]] | None = None) -> Settled:
    """`settle` without its write (`link_claims`), read only — the dry-run dump (`scripts/r6_case_dump.py`, under
    `SET TRANSACTION READ ONLY`) calls it directly: the global assignment over `_graph` (reservations as stored: the
    reconcile reads them after `housekeep`); each `settlement_line` placeholder replaced — booked → `settlement`
    (candidates `[facts]`, context `{fingerprint, source, owner_deleted}`); ambiguous → `ambiguous` +
    `settlement_ambiguous` (candidates the settlements); pending → `line_unmatched` + `settlement_pending` (`reason`,
    `nearest`), or, with no candidate (`reason is None`) beside an unclaimed stand-in within the window,
    `settlement_mismatch` naming it (a sole non-bookable candidate keeps `settlement_pending` with its reason, D8); the
    feed unavailable / incomplete / past the calendar, or answering in another currency than the account's
    (`currency_mismatch`, code `currency_mismatch`, logged by code) → every placeholder `settlement_pending` and
    `unavailable`. Then
    the drift cases (live statements only; the drift re-reads are portfolio GETs). Counts per `COUNT_KEYS` +
    `unavailable_code` (`claimed` 0: `settle` sets it). `coverage`: this statement's active coverage as the run would
    leave it (line id → entry ids) when it is not written (the dump: the run's claims plus the owner's coverage);
    None reads the stored rows (the reconcile, after `_write_coverage`)."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    if feed is not None and currency_mismatch(account, feed.rows):  # treated as an unavailable source (finding 1)
        logger.info("settlement.currency_mismatch", extra={"statement_id": statement.id, "account_id": account.id,
                                                           "code": CURRENCY_MISMATCH})
        feed, unavailable_code = None, CURRENCY_MISMATCH
    counts: dict = dict.fromkeys(COUNT_KEYS, 0)
    counts["unavailable_code"] = unavailable_code
    claimed = set() if coverage is None else {e for entries in coverage.values() for e in entries if e is not None}
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
        graph, contestants = _graph(db, statement, all_lines, rules, set(legs), coverage)
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
                stand_in = None if reason is not None else _mismatched_stand_in(db, account.id, line, rules, claimed)
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
    drift = (_drift(db, statement, account, all_lines, legs, client, skip_reads, coverage)
             if statement.mode == "live" else [])
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


def recheck_statement_ids(db: Session) -> list[int]:
    """Spec §4.5: the live, non-retired statements of accounts with a known `settlement_source` (`SETTLEMENT_SOURCES`)
    holding an open case whose hint is
    `settlement_pending`, `settlement_ambiguous` or `settlement_mismatch`, ascending. Read only."""
    return sorted(db.execute(
        select(AccountStatement.id).distinct()
        .join(ReconciliationCase, ReconciliationCase.statement_id == AccountStatement.id)
        .join(Account, Account.id == AccountStatement.account_id)
        .where(ReconciliationCase.status == "open",
               ReconciliationCase.context["hint"].astext.in_(SETTLEMENT_CASE_HINTS),
               Account.settlement_source.in_(SETTLEMENT_SOURCES),  # "" or an unknown stored value: not a source
               AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
               ~retired.clause(AccountStatement.id))).scalars())


def flag_for_recheck(db: Session, report: dict) -> int:
    """Spec §4.5 item 1: `needs_recheck` on `recheck_targets`; returns the number flagged. Flushes; the caller
    commits."""
    return flag_statements(db, recheck_targets(db, report))


def recheck_targets(db: Session, report: dict) -> set[int]:
    """The live, non-retired statements whose period holds a `touched_dates` day of their account (a stand-in job
    report), plus `recheck_statement_ids`. Read only."""
    wanted = set(recheck_statement_ids(db))
    for account_id, days in (report.get("touched_dates") or {}).items():
        spans = [and_(AccountStatement.period_start <= day, AccountStatement.period_end >= day)
                 for day in (date.fromisoformat(d) if isinstance(d, str) else d for d in days)]
        if not spans:
            continue
        wanted |= set(db.execute(
            select(AccountStatement.id)
            .where(AccountStatement.account_id == int(account_id), AccountStatement.mode == "live",
                   AccountStatement.current_revision_id.is_not(None), or_(*spans),
                   ~retired.clause(AccountStatement.id))).scalars())
    return wanted


def flag_statements(db: Session, statement_ids: Iterable[int]) -> int:
    """`needs_recheck` on these statements (those not flagged yet); returns the number flagged. Flushes."""
    wanted = set(statement_ids)
    if not wanted:
        return 0
    count = db.execute(
        update(AccountStatement).where(AccountStatement.id.in_(wanted), AccountStatement.needs_recheck.is_(False))
        .values(needs_recheck=True).execution_options(synchronize_session=False)).rowcount
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
    except Exception as exc:  # noqa: BLE001  (the revision is stored; the daily re-check is the backstop)
        # the class name only, never the message or traceback (a driver message can carry amounts; final review M5)
        logger.error("settlement.neighbour_flag_failed: %s", type(exc).__name__, extra={"statement_id": statement_id})
        db.rollback()
        return 0


# --- the daily stand-in job (spec §4.7, owner decision Q7) -----------------------------------------------------------

HOUSEKEEPING_KEYS = ("checked", "owner_deleted", "owner_edited", "retired", "replaced", "replaced_waiting",
                     "settlement_busy", "failed", CURRENCY_MISMATCH)
FINAL_STATUSES = ("booked", "owner_deleted", "owner_edited")  # with `retired` of FINAL_RETIRED_REASONS (spec §4.7)
FINAL_RETIRED_REASONS = ("twin", "revert")
SETTLEMENT_CASE_HINTS = ("settlement_pending", "settlement_ambiguous", "settlement_mismatch")
FINAL = "final"


@dataclass(frozen=True)
class StandInCandidate:
    account_id: int
    target_account_id: int
    settlement: Settlement


@dataclass
class _Notes:
    """What one decision did, merged into the run's report only after its transaction committed (or, in a dry run,
    rolled back as planned): ids, dates and codes, never amounts."""
    booked: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    touched: set[date] = field(default_factory=set)


def _note_booked(notes: _Notes | None, row: SettlementBooking) -> None:
    if notes is not None:
        notes.booked.append({"account_id": row.account_id, "settlement_date": row.settlement_date.isoformat(),
                             "booking_id": row.id})
        notes.touched.add(row.settlement_date)


def _note_skipped(notes: _Notes | None, account_id: int, day: date | None, reason: str) -> None:
    if notes is not None:
        notes.skipped.append({"account_id": account_id, "settlement_date": None if day is None else day.isoformat(),
                              "reason": reason})


def ineligibility(settlement: Settlement, settings: dict, today: date) -> str | None:
    """Why `stand_in_candidates` drops a feed row (M9), or None: `not_statement_dated` (a CSV-only day: no persisted
    國泰 statement date), `not_bookable` (margin day, legs disagree), `zero`, `pre_go_live` (before
    `settlement.enabled_from`, or no go-live date at all), `future` (settles after `today`)."""
    if settlement.date_source != "statement" or settlement.settlement_date is None:
        return "not_statement_dated"
    if not settlement.bookable:
        return "not_bookable"
    if Decimal(settlement.net_amount) == 0:
        return "zero"
    enabled_from = settings.get("enabled_from")
    if enabled_from is None or settlement.settlement_date < date.fromisoformat(enabled_from):
        return "pre_go_live"
    if settlement.settlement_date > today:
        return "future"
    return None


def _target_of(settings: dict, account_id: int) -> int | None:
    return (settings.get("target_accounts") or {}).get(str(account_id))


def _booked_days(db: Session, account_id: int) -> set[date]:
    return set(db.scalars(select(SettlementBooking.settlement_date).where(
        SettlementBooking.account_id == account_id, SettlementBooking.kind == KIND,
        SettlementBooking.status == "booked")))


def stand_in_candidates(db: Session, rows: list[Settlement], account: Account, settings: dict,
                        today: date) -> list[StandInCandidate]:
    """Spec §4.7 Candidate: the feed rows with no `ineligibility` on a `settlement_source` account with a target in
    `settlement.target_accounts`, minus the days that already hold a `booked` row of either origin (the gate would
    report `final`; the job re-run books nothing new). Read only."""
    target = _target_of(settings, account.id)
    if target is None or not account.settlement_source:
        return []
    booked = _booked_days(db, account.id)
    return [StandInCandidate(account.id, target, s) for s in rows
            if ineligibility(s, settings, today) is None and s.settlement_date not in booked]


def _final_on(db: Session, account_id: int, day: date) -> bool:
    return db.scalar(select(exists().where(
        SettlementBooking.account_id == account_id, SettlementBooking.kind == KIND,
        SettlementBooking.settlement_date == day,
        or_(SettlementBooking.status.in_(FINAL_STATUSES),
            and_(SettlementBooking.status == "retired", SettlementBooking.reason.in_(FINAL_RETIRED_REASONS)))))) is True


def _dismissed(db: Session, account_id: int, settlement: Settlement) -> bool:
    """A `dismissed` settlement case of the account (kind `settlement`, or a line case with a settlement hint) whose
    context fingerprint is this one or whose line posted on the settlement date (controller ruling 2026-10-30)."""
    settlement_case = or_(ReconciliationCase.kind == KIND,
                          ReconciliationCase.context["hint"].astext.in_(SETTLEMENT_CASE_HINTS))
    return db.scalar(select(exists().where(
        ReconciliationCase.status == "dismissed", settlement_case,
        ReconciliationCase.statement_id == AccountStatement.id, AccountStatement.account_id == account_id,
        or_(ReconciliationCase.context["fingerprint"].astext == settlement.fingerprint,
            exists().where(StatementLine.id == ReconciliationCase.line_id,
                           StatementLine.posted_date == settlement.settlement_date))))) is True


def gate(db: Session, cand: StandInCandidate, settings: dict) -> str | None:
    """The first gate the candidate fails (spec §4.7 Gates, in order), or None to book: `disabled`;
    `currency_mismatch` (the feed row, the bank or the target outside the bank's currency / its source's, finding 1 —
    the settings refuse such a pair, this asserts it); `zero`; `final`
    — a row for (account, settlement date) `booked`, `owner_deleted`, `owner_edited`, or `retired` by a twin or a
    revert (a `skipped` row is never final: it is re-evaluated every run, M7; `final` is reported, never recorded);
    `dismissed`; `constituent_reserved` (a constituent or the fingerprint held, `reservations`); `day_booked` (a
    `booked` row of the day under another fingerprint, M10); `twin` (an owner record, `find_twins`). Read only."""
    s = cand.settlement
    if settings.get("stand_in_enabled") is not True:
        return "disabled"
    account, target = db.get(Account, cand.account_id), db.get(Account, cand.target_account_id)
    if target is None or currency_mismatch(account, [s]) or target.currency != account.currency:
        return CURRENCY_MISMATCH
    if Decimal(s.net_amount) == 0:
        return "zero"
    if _final_on(db, cand.account_id, s.settlement_date):
        return FINAL
    if _dismissed(db, cand.account_id, s):
        return "dismissed"
    fps, ids = reservations(db)
    if s.fingerprint in fps or ids.intersection(s.source_ids):
        return "constituent_reserved"
    if db.scalar(select(exists().where(
            SettlementBooking.account_id == cand.account_id, SettlementBooking.kind == KIND,
            SettlementBooking.status == "booked", SettlementBooking.settlement_date == s.settlement_date,
            SettlementBooking.fingerprint != s.fingerprint))):
        return "day_booked"
    if find_twins(db, account_id=cand.account_id, target_account_id=cand.target_account_id,
                  amount=Decimal(s.net_amount), settlement_date=s.settlement_date,
                  window_days=int(settings.get("twin_window_days", TWIN_WINDOW_DAYS))):
        return "twin"
    return None


def _items(facts: dict) -> list[SettlementBookingItem]:
    return [SettlementBookingItem(source=i["source_id"].split(":")[0], source_id=i["source_id"],
                                  amount=Decimal(i["amount"])) for i in facts["items"]]


def book_stand_in(db: Session, cand: StandInCandidate, settings: dict) -> SettlementBooking:
    """Spec §4.7 Booking: the §4.4 item 2 transfer (bank → target for a negative net, target → bank for a positive
    one, `|net|`, both legs on the settlement date, name `證券交割`, description `證券交割 <trade date>`, source
    `statement`) and its `settlement_booking` row (`origin='stand_in'`, `status='booked'`, `event_id` NULL, `facts`
    = `facts_of`) with its items, all inside one savepoint. No account row lock: the partial unique on (account,
    settlement_date) over `booked` rows serialises it against a concurrent `book_settlement`; a unique violation
    rolls the savepoint back and is recorded as the skip it means (`UNIQUE_CODES`: `day_booked`,
    `constituent_reserved`, `already_booked`) — the returned row is then that `skipped` row. Audit
    `settlement_booked`. The caller has run `gate`. Flushes, never commits."""
    s = cand.settlement
    account = db.get(Account, cand.account_id)
    net = Decimal(s.net_amount)
    source, sink = (cand.account_id, cand.target_account_id) if net < 0 else (cand.target_account_id, cand.account_id)
    facts = facts_of(s)
    try:
        with db.begin_nested():
            group_id = transfer_service.create_transfer(db, TransferIn(
                from_account_id=source, to_account_id=sink, out_amount=abs(net), entry_date=s.settlement_date,
                posted_date=s.settlement_date, name=TRANSFER_NAME,
                description=f"{TRANSFER_NAME} {facts['trade_date']}"),
                source=STATEMENT_SOURCE, remember=False)
            out_leg, in_leg = transfer_service.transfer_legs(db, group_id)
            row = SettlementBooking(
                kind=KIND, origin="stand_in", account_id=cand.account_id, target_account_id=cand.target_account_id,
                event_id=None, source=account.settlement_source, fingerprint=s.fingerprint, trade_date=s.trade_date,
                settlement_date=s.settlement_date, net_amount=net, facts=facts, transfer_group_id=group_id,
                out_entry_id=out_leg.id, in_entry_id=in_leg.id, status="booked", items=_items(facts))
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        code = next((code for index, code in UNIQUE_CODES if index in str(exc.orig)), None)
        if code is None:
            raise
        logger.info("settlement.stand_in_lost_race", extra={"account_id": cand.account_id, "code": code,
                                                            "settlement_date": s.settlement_date.isoformat()})
        return record_skip(db, account, s, code, settings=settings)
    audit.record(db, event="settlement_booked", actor=ACTOR,
                 detail={**_figures(row), "trade_date": facts["trade_date"], "out_entry_id": out_leg.id,
                         "in_entry_id": in_leg.id})
    return row


def record_skip(db: Session, account: Account, settlement: Settlement, reason: str, *,
                settings: dict) -> SettlementBooking:
    """The day's `skipped` row for `reason` (one per (account, settlement date, reason)): the existing one rewritten in
    place with the current facts, or a new one; audit `settlement_skipped` only when the reason differs from the
    day's latest skip (a repeat is no event). Takes the feed row, so an ineligible row is recorded too; an undated one
    (a CSV-only day past the calendar) is recorded on its trade date — the column is NOT NULL. No items: a skipped row
    reserves nothing. Never `final` (M7: callers report it instead). `settings` is the caller's (the run's)
    settlement block: the target is read from it, not re-read from the store. Flushes, never commits."""
    day = settlement.settlement_date or settlement.trade_date
    rows = list(db.scalars(select(SettlementBooking).where(
        SettlementBooking.account_id == account.id, SettlementBooking.kind == KIND,
        SettlementBooking.settlement_date == day, SettlementBooking.status == "skipped")
        .order_by(SettlementBooking.updated_at.desc(), SettlementBooking.id.desc())))
    latest = rows[0] if rows else None
    row = next((r for r in rows if r.reason == reason), None)
    if row is None:
        row = SettlementBooking(kind=KIND, origin="stand_in", account_id=account.id, status="skipped", reason=reason)
        db.add(row)
    row.target_account_id = _target_of(settings, account.id)
    row.source = account.settlement_source
    row.fingerprint, row.trade_date, row.settlement_date = settlement.fingerprint, settlement.trade_date, day
    row.net_amount, row.facts = Decimal(settlement.net_amount), facts_of(settlement)
    row.updated_at = func.now()  # the day's latest verdict is the most recently written row
    db.flush()
    if latest is None or latest.reason != reason:
        audit.record(db, event="settlement_skipped", actor=ACTOR,
                     detail={**_figures(row), "reason": reason, "trade_date": settlement.trade_date.isoformat()})
    return row


def unclaimed_stand_ins(db: Session, account_id: int) -> set[int]:
    """The booking ids of the account's `booked` stand-ins with no active coverage on either leg
    (`stand_ins.unclaimed_settlement_legs`). Read without locks: every retirement re-reads the claim under the row and
    leg locks (`retire_pair`)."""
    return set(stand_ins.unclaimed_settlement_legs(db, account_id).values())


def _retire(db: Session, row: SettlementBooking, reason: str, notes: _Notes | None) -> str | None:
    day = row.settlement_date
    if retire_pair(db, row, reason=reason) is None:
        return None  # the locked re-check failed (a claim landed, an owner write got there first): the pair stays
    if notes is not None:
        notes.touched.add(day)
    return "retired"


def check_row(db: Session, row: SettlementBooking, feed: dict[date, Settlement], settings: dict,
              today: date, *, notes: _Notes | None = None) -> str | None:
    """One job pass over a `booked` stand-in (spec §4.7 Housekeeping, for an UNCLAIMED stand-in only — claimed rows
    and bank-origin rows are `housekeep`'s and reconcile's): the row must be in `unclaimed_stand_ins` (a module
    global, read unlocked); then, in order, an owner twin → `retire_pair(reason="twin")` → `retired`; no feed row for
    the row's trade date (`feed` is keyed by trade date: the account's one `settlement_source` and the booking's trade
    day, never the stored fingerprint), or the row no longer `bookable` → `retire_pair(reason=<feed reason or
    "gone">)` → `retired`; the trade day's row present with other facts (`same_facts`) → `replace` → `replaced` /
    `replaced_waiting`. Every retirement re-reads the claim under `lock_pair`: a claim that landed since the
    unlocked read → None, the pair stays. A held row or leg → `settlement_busy` (logged by ids; the row is left for
    the next run). Returns the transition or None. Flushes, never commits."""
    if row.status != "booked" or row.origin != "stand_in" or row.kind != KIND:
        return None
    if row.id not in unclaimed_stand_ins(db, row.account_id):
        return None
    try:
        if find_twins(db, account_id=row.account_id, target_account_id=row.target_account_id,
                      amount=Decimal(row.net_amount), settlement_date=row.settlement_date,
                      window_days=int(settings.get("twin_window_days", TWIN_WINDOW_DAYS)),
                      exclude=[i for i in (row.out_entry_id, row.in_entry_id) if i is not None]):
            return _retire(db, row, "twin", notes)
        trade_date = row.trade_date or date.fromisoformat(row.facts["trade_date"])
        new = feed.get(trade_date)
        if new is None or not new.bookable:
            return _retire(db, row, "gone" if new is None else (new.reason or "not_bookable"), notes)
        if not same_facts(row.facts, facts_of(new)):
            return replace(db, row, new, settings, today, notes=notes)
        return None
    except CodedConflictError as exc:
        if exc.code != SETTLEMENT_BUSY:
            raise
        logger.info("settlement.job_busy", extra={"settlement_booking_id": row.id, "account_id": row.account_id})
        return SETTLEMENT_BUSY


def replace(db: Session, row: SettlementBooking, new: Settlement, settings: dict, today: date, *,
            notes: _Notes | None = None) -> str | None:
    """Spec §4.7 (AGENT-360 major 5): a changed feed row on the same trade day replaces the unclaimed stand-in inside
    the caller's ONE transaction — `retire_pair(reason="replaced")` (None → None, nothing else happens), then the new
    facts go through `stand_in_candidates` and `gate` again: eligible → `book_stand_in` → `replaced`; otherwise the
    first `ineligibility` or gate reason (`no_target` when the account lost its target) is recorded as the new day's
    skip (`record_skip`; `final` is reported, never recorded, M7) → `replaced_waiting`: the old pair is gone and the
    day waits for a legal date or a bank line. Audit `settlement_replaced` with both fingerprints. Flushes, never
    commits."""
    old_id, old_fp, old_day, account_id = row.id, row.fingerprint, row.settlement_date, row.account_id
    if retire_pair(db, row, reason="replaced") is None:
        return None
    account = db.get(Account, account_id)
    target = _target_of(settings, account_id)
    found = stand_in_candidates(db, [new], account, settings, today)
    if found:
        cand, reason = found[0], gate(db, found[0], settings)
    else:  # dropped: ineligible, no target, or the new day already holds a `booked` row (the gate's `final`)
        cand = None if target is None else StandInCandidate(account_id, target, new)
        reason = ineligibility(new, settings, today) or ("no_target" if cand is None else gate(db, cand, settings)) \
            or FINAL
    booked = None
    if reason is None:
        result = book_stand_in(db, cand, settings)
        if result.status == "booked":
            booked = result
        else:
            reason = result.reason
    elif reason != FINAL:
        record_skip(db, account, new, reason, settings=settings)
    outcome = "replaced" if booked is not None else "replaced_waiting"
    audit.record(db, event="settlement_replaced", actor=ACTOR, detail={
        "account_id": account_id, "old_booking_id": old_id, "new_booking_id": None if booked is None else booked.id,
        "old_fingerprint": old_fp, "new_fingerprint": new.fingerprint, "old_settlement_date": old_day.isoformat(),
        "new_settlement_date": None if new.settlement_date is None else new.settlement_date.isoformat(),
        "outcome": outcome, "reason": reason})
    if notes is not None:
        notes.touched.add(old_day)
        if booked is not None:
            _note_booked(notes, booked)
        else:
            _note_skipped(notes, account_id, new.settlement_date, reason)
            if new.settlement_date is not None:
                notes.touched.add(new.settlement_date)
    return outcome


# --- the run ---------------------------------------------------------------------------------------------------------


def _log_failure(event: str, exc: BaseException, **ids) -> None:
    """ERROR with ids and the error class only — never the message (a driver's text can carry amounts); the full
    exception goes to DEBUG."""
    logger.error(event, extra={**ids, "error_class": exc.__class__.__name__})
    logger.debug(event, exc_info=exc)


def _new_report() -> dict:
    return {"status": "completed", "booked": [], "skipped": [],
            "housekeeping": dict.fromkeys(HOUSEKEEPING_KEYS, 0), "touched_dates": {}}


def _merge(report: dict, account_id: int, notes: _Notes) -> None:
    report["booked"] += notes.booked
    report["skipped"] += notes.skipped
    if notes.touched:
        days = set(report["touched_dates"].get(account_id, [])) | {d.isoformat() for d in notes.touched}
        report["touched_dates"][account_id] = sorted(days)


def _settle_from(db: Session, account_id: int, settings: dict) -> date | None:
    """`min(enabled_from, the oldest booked stand-in's settlement date)` (either may be missing; None: nothing to
    read). Statement days are found by their persisted date, so a statement that arrives late for an old trade day is
    picked up as long as it settles on or after go-live."""
    oldest = db.scalar(select(func.min(SettlementBooking.settlement_date)).where(
        SettlementBooking.account_id == account_id, SettlementBooking.kind == KIND,
        SettlementBooking.origin == "stand_in", SettlementBooking.status == "booked"))
    days = [d for d in (None if settings.get("enabled_from") is None else date.fromisoformat(settings["enabled_from"]),
                        oldest) if d is not None]
    return min(days) if days else None


def _accounts(db: Session, settings: dict) -> list[tuple[int, date]]:
    """(account id, settle_from) for every account with a known `settlement_source` (`SETTLEMENT_SOURCES`, as
    `recheck_statement_ids`) with a booked stand-in, or — while the job is
    enabled — a target, in id order, skipping those with nothing to read (no go-live date and no stand-in)."""
    with_stand_ins = set(db.scalars(select(SettlementBooking.account_id).where(
        SettlementBooking.kind == KIND, SettlementBooking.origin == "stand_in", SettlementBooking.status == "booked")))
    enabled = settings.get("stand_in_enabled") is True
    out = []
    for account in db.scalars(select(Account).where(Account.settlement_source.in_(SETTLEMENT_SOURCES))
                              .order_by(Account.id)):
        if account.id not in with_stand_ins and not (enabled and _target_of(settings, account.id) is not None):
            continue
        settle_from = _settle_from(db, account.id, settings)
        if settle_from is not None:
            out.append((account.id, settle_from))
    return out


class _Stop(Exception):
    """An import holds the key: the run stops `import_running` (decisions already committed stay done)."""


def _decide(factory, report: dict, account_id: int, dry_run: bool, fn, **ids) -> object:
    """One decision in its own transaction: the shared import key (not in a dry run) → `fn(db, notes)` → commit (a dry
    run rolls back); the notes reach the report only then. `ImportRunningError` → `_Stop`; any other error rolls the
    decision back, is logged by ids and counts `failed`. Returns `fn`'s result (None after a failure)."""
    notes = _Notes()
    with factory() as db:
        try:
            if not dry_run:
                take_import_key_shared(db)
            result = fn(db, notes)
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except ImportRunningError:
            db.rollback()
            raise _Stop() from None
        except Exception as exc:  # noqa: BLE001 — one broken decision must not stop the others
            db.rollback()
            _log_failure("settlement_job.decision_failed", exc, account_id=account_id, **ids)
            report["housekeeping"]["failed"] += 1
            return None
    _merge(report, account_id, notes)
    return result


def _run_account(factory, account_id: int, feed: Feed, settings: dict, today: date, report: dict, *,
                 dry_run: bool, enabled: bool) -> None:
    counts = report["housekeeping"]
    by_trade_date = {r.trade_date: r for r in feed.rows}
    with factory() as db:
        mismatch = currency_mismatch(db.get(Account, account_id), feed.rows)
    if mismatch:  # finding 1: logged by code; `housekeep` (owner deletes / edits, no feed) still runs below
        logger.info("settlement_job.currency_mismatch", extra={"account_id": account_id, "code": CURRENCY_MISMATCH})
        counts[CURRENCY_MISMATCH] += 1

    def housekeeping(db, notes):
        return housekeep(db, account_id)

    if not dry_run:  # the auto-pay shape: a dry run touches no booked row (no NOWAIT locks, no retire / replace)
        moved = _decide(factory, report, account_id, dry_run, housekeeping, step="housekeep")
        for key, value in (moved or {}).items():
            counts[key] += value
    with factory() as db:
        rows = [(row.id, row.trade_date or date.fromisoformat(row.facts["trade_date"]))
                for row in booked_rows(db, account_id) if row.origin == "stand_in"]
    decided: set[date] = set()  # the trade days `check_row` looked at: their fate is decided there, not as candidates
    for row_id, trade_date in rows:
        decided.add(trade_date)
        if dry_run or mismatch:  # never a retirement or a replacement from a feed in another currency
            continue
        counts["checked"] += 1

        def check(db, notes, row_id=row_id):
            row = db.get(SettlementBooking, row_id)
            return None if row is None else check_row(db, row, by_trade_date, settings, today, notes=notes)

        transition = _decide(factory, report, account_id, dry_run, check, settlement_booking_id=row_id)
        if transition is not None:
            counts[transition] += 1
    if not enabled:
        return
    with factory() as db:
        found = stand_in_candidates(db, [r for r in feed.rows if r.trade_date not in decided],
                                    db.get(Account, account_id), settings, today)
    if mismatch:  # every candidate reported skipped, nothing written (no `skipped` row either)
        notes = _Notes()
        for cand in found:
            _note_skipped(notes, account_id, cand.settlement.settlement_date, CURRENCY_MISMATCH)
        _merge(report, account_id, notes)
        return
    for cand in found:
        def decide(db, notes, cand=cand):
            reason = gate(db, cand, settings)
            day = cand.settlement.settlement_date
            if dry_run:  # read only: the would-book list has no booking id yet; nothing is written, then rolled back
                if reason is None:
                    notes.booked.append({"account_id": cand.account_id, "settlement_date": day.isoformat(),
                                         "booking_id": None})
                    notes.touched.add(day)
                else:
                    _note_skipped(notes, cand.account_id, day, reason)
                return
            if reason is None:
                row = book_stand_in(db, cand, settings)
                if row.status == "booked":
                    _note_booked(notes, row)
                    return
                reason = row.reason
            elif reason != FINAL:
                record_skip(db, db.get(Account, cand.account_id), cand.settlement, reason, settings=settings)
            _note_skipped(notes, cand.account_id, day, reason)

        _decide(factory, report, account_id, dry_run, decide,
                settlement_date=cand.settlement.settlement_date.isoformat())


def _run_locked(factory, today: date, dry_run: bool, report: dict) -> None:
    with factory() as db:
        settings = settings_service.read_settlement_settings(db)
        if dry_run:  # the preview runs whatever `stand_in_enabled` says; without a go-live date, as if live from today
            effective_from = settings.get("enabled_from") or today.isoformat()
            report["previewed_settings"] = {"stand_in_enabled": settings.get("stand_in_enabled") is True,
                                            "enabled_from": settings.get("enabled_from"),
                                            "effective_enabled_from": effective_from}
            settings = {**settings, "stand_in_enabled": True, "enabled_from": effective_from}
        enabled = settings.get("stand_in_enabled") is True
        accounts = [(db.get(Account, account_id), settle_from) for account_id, settle_from in _accounts(db, settings)]
        feeds: list[tuple[int, Feed]] = []
        for account, settle_from in accounts:
            if settle_from > today:
                continue
            try:  # every feed is read before anything is written: unreachable → nothing booked, nothing retired (§9)
                feeds.append((account.id, fetch_by_settlement_window(db, account, settle_from, today)))
            except PortfolioUnavailable as exc:
                logger.info("settlement_job.source_unavailable", extra={"account_id": account.id, "code": exc.code})
                report["status"] = "source_unavailable"
                return
        db.rollback()
    try:
        for account_id, feed in feeds:
            _run_account(factory, account_id, feed, settings, today, report, dry_run=dry_run, enabled=enabled)
    except _Stop:
        report["status"] = "import_running"
        return
    if dry_run:
        report["status"] = "dry_run"
    elif not enabled:
        report["status"] = "disabled"


def run_stand_ins(engine: Engine, *, today: date | None = None, dry_run: bool = False) -> dict:
    """The daily stand-in job (spec §4.7; triggers §4.5): under `SETTLEMENT_JOB_LOCK_KEY` (session-level, on a
    dedicated connection — a second runner returns `busy` and writes nothing), per `settlement_source` account with
    a target (or a booked stand-in): ONE `fetch_by_settlement_window(settle_from, today)` with `settle_from =
    min(enabled_from, the oldest booked stand-in's settlement date)` — all feeds first, so `PortfolioUnavailable` →
    `source_unavailable` with nothing written — then `housekeep`, `check_row` over its booked stand-ins, and the
    candidates (`stand_in_candidates` → `gate` → `book_stand_in` / `record_skip`). Each decision runs in its own
    transaction: the shared import key → (row → legs NOWAIT for a retirement or replacement) → write → commit; no
    account row lock (spec §4.7 Locks) — but inserting a leg takes the FK's `FOR KEY SHARE` on the account row while
    the decision holds the shared DIRTY barrier, so a deadlock with an engine action that holds the account row FOR
    UPDATE and waits for the barrier is possible; Postgres detects it, this decision counts `failed` and is retried
    next run, the engine gets 409 `sweep_barrier_busy` — the auto-pay job's shape. `ImportRunningError` stops the run
    `import_running`. Disabled → housekeeping and the replacements / retirements of the job's own rows still run, no
    new day is booked (`disabled`). A dry run (the auto-pay shape) takes no import key, skips `housekeep` and
    `check_row` (no booked row is locked, retired or replaced; `housekeeping` stays zero), gates the candidates read
    only (`booked` entries carry `booking_id: None`), rolls every transaction back, and previews whatever
    `stand_in_enabled` says — without a go-live date, as if live from `today`; the report then carries
    `previewed_settings` {stand_in_enabled, enabled_from (as stored), effective_enabled_from (the date used)}. An
    account whose feed answers in another currency than its own (`currency_mismatch`, PR #234 review finding 1) gets
    `housekeep` only: no `check_row`, its candidates reported `skipped` `currency_mismatch` with nothing written,
    `housekeeping.currency_mismatch` +1 (dry runs too), logged by code.

    Report: `status` (completed | busy | disabled | source_unavailable | import_running | dry_run), `booked`
    [{account_id, settlement_date, booking_id}], `skipped` [{account_id, settlement_date, reason}] (gated candidates and
    `replaced_waiting` days, `final` included), `housekeeping` (every `HOUSEKEEPING_KEYS` key: `checked` = every
    booked stand-in looked at, claimed or not; the transitions; `settlement_busy`; `failed`; `currency_mismatch`
    accounts), `touched_dates`
    {account_id: [ISO dates]} — the dates booked, retired or replaced (old and new) and every `replaced_waiting`
    day (§4.5 item 1's flag set). Ids, dates and codes, never amounts; never raises for an expected condition."""
    today = today or ledger_service._today()
    report = _new_report()
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": SETTLEMENT_JOB_LOCK_KEY}).scalar_one()
        lock_conn.commit()
        if not acquired:
            report["status"] = "busy"
        else:
            try:
                _run_locked(sessionmaker(bind=engine, autoflush=False), today, dry_run, report)
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SETTLEMENT_JOB_LOCK_KEY})
                lock_conn.commit()
    logger.info("settlement_job.run", extra={
        "today": today.isoformat(), "run_status": report["status"], "housekeeping": report["housekeeping"],
        "booked": [b["booking_id"] for b in report["booked"]],
        "skipped": [(s["account_id"], s["settlement_date"], s["reason"]) for s in report["skipped"]]})
    return report
