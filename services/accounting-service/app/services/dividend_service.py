"""國泰 dividend receipts on the bank statement (portfolio integration spec §4.6; dividends plan rev 2.3, R1–R11): a
dividend-labelled deposit line (`matching.is_dividend_line`) of a settlement-source bank statement is matched against
the portfolio's confirmed receipts, and `book_dividend` (`reconciliation_actions.settlement_actions`) books it as an
income entry under one `settlement_booking` row of kind `dividend`.

- Match (R1): a receipt is a candidate of a line when it is unreserved (no `kind='dividend'` booking holds its
  fingerprint or item), of exactly the line's flow (the feed's `amount` is already net of 匯費 / 補充保費: never
  deducted again) and within `WINDOW_DAYS` of the line's posted date. Both sides unique within the statement → a
  `dividend` case; a line with several candidates, or whose receipt another line could also take → `ambiguous` +
  `dividend_ambiguous` (the owner may pick one, the policy never does); a line without candidates beside exactly one
  near receipt of another amount → its plain `line_unmatched` case gains `dividend_mismatch` with the nearest receipt
  and its signed delta (never a candidate, never a booking); anything else keeps its ordinary case.
- Feed (R3): `reconcile` makes the one window read (`fetch_for`, statement period ± `WINDOW_DAYS`) next to the
  settlement read, through the same cached client, only when `applies`; `book_dividend.prepare` re-reads the line's
  window (`read_window`). An unavailable, incomplete or other-currency answer leaves the lines in their ordinary case.
- Re-check (R11): `recheck_statement_ids` names every statement holding an open dividend-family or plain
  dividend-line case; `trade_settlement_service.recheck_targets` unions it in.
- Owner wins (R6, spec §4.7 single leg, Phase 1 parity under owner ruling 2026-10-31): `housekeep` tombstones a
  booking whose income entry the owner deleted (`owner_deleted`, items cleared: the receipt is free and the policy
  proposes it, never books it) or edited structurally (`owner_edited`, items kept: the receipt stays reserved);
  `retire_twins_for_statement` retires a booking whose owner `income` twin arrived (`retire_twin`). Both run in
  `reconcile` after the feed reads and before the population is read, under the statement row; the locks are the
  Phase 1 protocol (`tss.lock_pair`: booking row → entry NOWAIT, `settlement_busy`, never the account row).

Nothing here commits; the reconciler and the engine own the transaction."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, Category, LedgerEntry, ReconciliationCase, ReconciliationSettings, SettlementBooking,
    StatementLine,
)
from app.schemas.writes import SETTLEMENT_SOURCES
from app.services import coverage_service, entry_write_service, portfolio_client, settings_service
from app.services import trade_settlement_service as tss
from app.services.entry_write_service import STATEMENT_SOURCE
from app.services.errors import CodedConflictError
from app.services.portfolio_client import Dividend, DividendFeed, PortfolioClient, PortfolioUnavailable
from app.services.reconciliation_actions import audit
from app.services.statements import matching, retired

KIND = "dividend"
AMBIGUOUS_HINT = "dividend_ambiguous"
MISMATCH_HINT = "dividend_mismatch"
FAMILY_HINTS = (AMBIGUOUS_HINT, MISMATCH_HINT)
# a dividend line's plain case: no hint, or the owner-deleted marker `_desired_cases` gives a line whose booked
# receipt the owner deleted while the feed cannot say more (final review M1)
STAND_IN_DELETED_HINT = "stand_in_deleted"  # reconciliation_actions.STAND_IN_DELETED_HINT (a circular import)
PLAIN_HINTS = (None, STAND_IN_DELETED_HINT)
WINDOW_DAYS = 3
ITEM_SOURCE = "dividend"
UNAVAILABLE_KEY = "dividend_unavailable_code"
COUNT_KEYS = ("dividend", "dividend_ambiguous", "dividend_mismatch")
_Q = Decimal("0.0001")

logger = logging.getLogger(__name__)


# --- facts ------------------------------------------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    return f"{Decimal(value).quantize(_Q)}"


def facts_of(d: Dividend) -> dict:
    """The one JSON shape of a dividend candidate and of `settlement_booking.facts` (dates ISO, amount 4-dp)."""
    return {"fingerprint": d.fingerprint, "id": d.id, "symbol": d.symbol, "receipt_date": d.receipt_date.isoformat(),
            "amount": _money(d.amount), "currency": d.currency}


def same_facts(booked: dict, now: dict | None) -> bool:
    """The receipt as listed equals the receipt as re-read: fingerprint, amount, receipt date and currency."""
    return now is not None and (booked["fingerprint"], Decimal(booked["amount"]), booked["receipt_date"],
                                booked["currency"]) == (now["fingerprint"], Decimal(now["amount"]),
                                                        now["receipt_date"], now["currency"])


def nearest_of(line: matching.Line, d: Dividend) -> dict:
    """The `dividend_mismatch` hint's receipt: `delta = line.flow − amount`, signed, 4 dp."""
    return {"id": d.id, "symbol": d.symbol, "receipt_date": d.receipt_date.isoformat(), "amount": _money(d.amount),
            "delta": f"{(line.flow - d.amount).quantize(_Q):+.4f}"}


# --- the assignment ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    unique: dict[int, Dividend]  # line id → its one receipt (both sides unique within the statement)
    ambiguous: dict[int, tuple[Dividend, ...]]  # line id → its candidates, (receipt_date, id) order
    near: dict[int, Dividend] = field(default_factory=dict)  # line id → the one near receipt of another amount


def _within(line: matching.Line, d: Dividend) -> bool:
    return abs((d.receipt_date - line.posted_date).days) <= WINDOW_DAYS


def _same(d: Dividend, line: matching.Line) -> bool:
    return d.amount.quantize(_Q) == line.flow.quantize(_Q)


def assign(lines: Sequence[matching.Line], rows: Sequence[Dividend], reserved: set[str],
           rules: matching.Rules) -> Assignment:
    """R1 over the dividend lines of `lines` (pure): a receipt is a candidate of a line when unreserved, of the line's
    exact flow and within `WINDOW_DAYS`; a line with one candidate that no other line could take is `unique`, any
    other line with candidates `ambiguous`; a line without candidates whose only near receipt (unreserved, within the
    window, of another amount, no line's candidate) is one gets `near`."""
    lines = [ln for ln in lines if matching.is_dividend_line(ln, rules)]
    free = [r for r in rows if r.fingerprint not in reserved]
    candidates = {ln.id: tuple(sorted((r for r in free if _within(ln, r) and _same(r, ln)),
                                      key=lambda r: (r.receipt_date, r.id))) for ln in lines}
    # both sides unique within the statement: a receipt that two lines could take decides neither
    takers = Counter(row.fingerprint for found in candidates.values() for row in found)
    unique = {lid: found[0] for lid, found in candidates.items() if len(found) == 1 and takers[found[0].fingerprint] == 1}
    ambiguous = {lid: found for lid, found in candidates.items() if found and lid not in unique}  # (receipt_date, id) order
    near = {}
    for ln in lines:
        if candidates[ln.id]:
            continue
        close = [r for r in free if _within(ln, r) and not _same(r, ln) and r.fingerprint not in takers]
        if len(close) == 1:
            near[ln.id] = close[0]
    return Assignment(unique=unique, ambiguous=ambiguous, near=near)


def assignment_for(db: Session, account_id: int, all_lines: list[StatementLine], rows: Sequence[Dividend],
                   rules: matching.Rules) -> Assignment:
    """The one rule both callers apply (`outcomes` at reconcile, `book_dividend` under the action locks): `assign` over
    the statement's dividend lines, minus those whose event a `booked` dividend row of the account holds (R5), against
    every receipt `tss.reservations(kind=KIND)` reserves. A line the owner claimed still takes part, so a receipt near
    it is never handed to its neighbour. Read only."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler imports this module

    held = set(db.scalars(select(SettlementBooking.event_id).where(
        SettlementBooking.account_id == account_id, SettlementBooking.kind == KIND,
        SettlementBooking.status == "booked", SettlementBooking.event_id.is_not(None))))
    fps, ids = tss.reservations(db, kind=KIND)
    return assign([_as_line(row) for row in all_lines if row.event_id not in held], rows, fps | ids, rules)


def owner_deleted(db: Session, fingerprint: str) -> bool:
    """A `kind='dividend'` booking of the fingerprint the owner deleted (its income entry gone): proposed, never
    auto-booked. An `owner_edited` row keeps its item, so its receipt is never offered at all."""
    return db.scalar(select(exists().where(
        SettlementBooking.kind == KIND, SettlementBooking.status == "owner_deleted",
        SettlementBooking.fingerprint == fingerprint))) is True


# --- the feed ---------------------------------------------------------------------------------------------------------


def applies(account: Account, statement: AccountStatement, lines: Iterable[StatementLine],
            rules: matching.Rules) -> bool:
    """R3: the account has a `settlement_source` and the bank statement prints a dividend line."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    return bool(account.settlement_source) and statement.kind == "bank" and any(
        matching.is_dividend_line(_as_line(line), rules) for line in lines)


def window(statement: AccountStatement) -> tuple[date, date]:
    span = timedelta(days=WINDOW_DAYS)
    return statement.period_start - span, statement.period_end + span


def fetch_for(db: Session, account: Account, statement: AccountStatement, *,
              client: PortfolioClient) -> tuple[DividendFeed | None, str | None]:
    """The reconcile's one dividend read (R3, statement period ± `WINDOW_DAYS`): `(feed, None)`, or `(None, code)` on
    any `PortfolioUnavailable` (`incomplete` included: nothing is assigned then)."""
    lo, hi = window(statement)
    try:
        return client.dividends(tss.broker_of(account), date_from=lo, date_to=hi), None
    except PortfolioUnavailable as exc:
        logger.info("dividend.source_unavailable", extra={"statement_id": statement.id, "code": exc.code})
        return None, exc.code


def read_window(account: Account, day: date, *, client: PortfolioClient | None = None) -> tuple[Dividend, ...]:
    """`book_dividend`'s one re-read: every receipt of `day ± WINDOW_DAYS`. Every candidate of a line, and every line
    the line's candidates could also fit, lies within that window of a receipt in it, so the window decides the line's
    assignment. Raises the feed's `PortfolioUnavailable`, or `PortfolioUnavailable("currency_mismatch")` when the
    account or a receipt is outside the source's currency."""
    client = client or portfolio_client.client_for()
    span = timedelta(days=WINDOW_DAYS)
    rows = client.dividends(tss.broker_of(account), date_from=day - span, date_to=day + span).rows
    if tss.currency_mismatch(account, rows):
        raise PortfolioUnavailable(tss.CURRENCY_MISMATCH)
    return rows


# --- the reconcile's outcomes -----------------------------------------------------------------------------------------


@dataclass
class Divided:
    cases: list[matching.Case]  # the matcher's cases, plain dividend-line cases replaced by their outcome
    counts: dict
    unavailable: bool


def _plain(case: matching.Case) -> bool:
    return case.kind == "line_unmatched" and case.line_id is not None and not case.context and not case.candidates


def outcomes(db: Session, statement: AccountStatement, account: Account, cases: list[matching.Case],
             all_lines: list[StatementLine], rules: matching.Rules, feed: DividendFeed | None,
             unavailable_code: str | None) -> Divided:
    """R1 over the plain cases (`line_unmatched`, no context, no candidates) of the statement's dividend lines, after
    the coverage is written: unique → `dividend` (candidates `[facts]`, context `{fingerprint, source,
    owner_deleted}`); ambiguous → `ambiguous` + `dividend_ambiguous`; one near receipt → `line_unmatched` +
    `dividend_mismatch` with `nearest`; every other case unchanged. The feed unavailable, or answering in another
    currency than the account's (`currency_mismatch`), leaves every case unchanged and sets `unavailable`. Counts per
    `COUNT_KEYS` + `UNAVAILABLE_KEY`. Read only."""
    from app.services.reconciliation_service import _as_line  # local: the reconciler calls this function

    if feed is not None and tss.currency_mismatch(account, feed.rows):  # treated as an unavailable source
        logger.info("dividend.currency_mismatch", extra={"statement_id": statement.id, "account_id": account.id,
                                                         "code": tss.CURRENCY_MISMATCH})
        feed, unavailable_code = None, tss.CURRENCY_MISMATCH
    counts: dict = {**dict.fromkeys(COUNT_KEYS, 0), UNAVAILABLE_KEY: unavailable_code}
    if feed is None:
        return Divided(cases=list(cases), counts=counts, unavailable=True)
    found = assignment_for(db, account.id, all_lines, feed.rows, rules)
    lines = {row.id: _as_line(row) for row in all_lines}
    out = []
    for case in cases:
        lid = case.line_id
        if not _plain(case) or lid not in lines:
            out.append(case)
        elif lid in found.unique:
            row = found.unique[lid]
            out.append(matching.Case(lid, None, KIND, (facts_of(row),), {
                "fingerprint": row.fingerprint, "source": account.settlement_source,
                "owner_deleted": owner_deleted(db, row.fingerprint)}))
            counts["dividend"] += 1
        elif lid in found.ambiguous:
            out.append(matching.Case(lid, None, "ambiguous", tuple(facts_of(r) for r in found.ambiguous[lid]),
                                     {"hint": AMBIGUOUS_HINT}))
            counts["dividend_ambiguous"] += 1
        elif lid in found.near:
            out.append(matching.Case(lid, None, "line_unmatched", (), {
                "hint": MISMATCH_HINT, "nearest": nearest_of(lines[lid], found.near[lid])}))
            counts["dividend_mismatch"] += 1
        else:
            out.append(case)
    return Divided(cases=out, counts=counts, unavailable=False)


# --- booking and re-check helpers -------------------------------------------------------------------------------------


def category_for(db: Session) -> tuple[int | None, bool]:
    """`(category id, needs_review)` of a dividend income entry: `(id, False)` only when
    `settlement.dividend_category_id` names an existing income category, else `(None, True)`."""
    category_id = settings_service.read_settlement_settings(db).get("dividend_category_id")
    category = None if category_id is None else db.get(Category, category_id)
    return (category.id, False) if category is not None and category.kind == "income" else (None, True)


def _stored_labels(db: Session) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(dividend labels, settlement labels) as the rules document stores them, else the `matching.Rules` defaults.
    Read straight from the settings row: neither key is touched by `settings_service.normalise_stale_rules` (only
    the R1b and score-scale keys are), so this equals what `rules_for` yields, without re-running that
    normalisation (and its stale-rule warnings) on every re-check."""
    row = db.get(ReconciliationSettings, 1)
    stored = ((row.data or {}).get("rules") or {}) if row is not None else {}
    return (tuple(stored.get("dividend_labels", matching.DIVIDEND_LABELS)),
            tuple(stored.get("settlement_labels", matching.SETTLEMENT_LABELS)))


def recheck_statement_ids(db: Session) -> list[int]:
    """R11: the live, non-retired statements, ascending, of accounts with a known `settlement_source`
    (`SETTLEMENT_SOURCES`) holding an open case of kind `dividend`, with a hint in `FAMILY_HINTS`, or a plain one —
    `line_unmatched`, a hint in `PLAIN_HINTS` (none, or `stand_in_deleted`: the owner deleted the booked receipt while
    the feed was down, final review M1), no candidates — on a dividend line (`matching.is_dividend_line`: a deposit
    naming one of the rules' `dividend_labels` and none of their `settlement_labels`). It compares nothing (Phase 1's
    shape): while such a case is open, each run's reconcile re-reads the feed. Read only."""
    labels, settlement_labels = _stored_labels(db)
    hint = ReconciliationCase.context["hint"].astext
    family = [ReconciliationCase.kind == KIND, hint.in_(FAMILY_HINTS)]
    if labels:
        dividend_line = [StatementLine.id == ReconciliationCase.line_id, StatementLine.line_kind == "deposit",
                         or_(*(StatementLine.merchant_norm.contains(label, autoescape=True) for label in labels))]
        if settlement_labels:
            dividend_line.append(~or_(*(StatementLine.merchant_norm.contains(label, autoescape=True)
                                        for label in settlement_labels)))
        family.append((ReconciliationCase.kind == "line_unmatched")
                      & or_(hint.is_(None), hint == STAND_IN_DELETED_HINT)
                      & (func.jsonb_array_length(ReconciliationCase.candidates) == 0)
                      & exists().where(*dividend_line))
    return sorted(db.execute(
        select(AccountStatement.id).distinct()
        .join(ReconciliationCase, ReconciliationCase.statement_id == AccountStatement.id)
        .join(Account, Account.id == AccountStatement.account_id)
        .where(ReconciliationCase.status == "open", or_(*family),
               Account.settlement_source.in_(SETTLEMENT_SOURCES),
               AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
               ~retired.clause(AccountStatement.id))).scalars())


# --- owner wins (R6, spec §4.7 single leg) ----------------------------------------------------------------------------


def _income(db: Session, entry_id: int | None) -> LedgerEntry | None:
    return None if entry_id is None else db.get(LedgerEntry, entry_id, populate_existing=True)


def _system(entry: LedgerEntry | None) -> bool:
    return entry is not None and entry.source == STATEMENT_SOURCE


def _figures(row: SettlementBooking) -> dict:
    return {"booking_id": row.id, "account_id": row.account_id, "fingerprint": row.fingerprint,
            "settlement_date": row.settlement_date.isoformat(), "in_entry_id": row.in_entry_id}


def _booked(db: Session, *where) -> list[SettlementBooking]:
    """The `booked` dividend rows matching `where`, id order, refreshed from the database (an owner delete in another
    transaction set `in_entry_id` NULL)."""
    return list(db.scalars(select(SettlementBooking).where(
        SettlementBooking.kind == KIND, SettlementBooking.status == "booked", *where).order_by(SettlementBooking.id)
        .execution_options(populate_existing=True)))


def housekeep(db: Session, account_id: int) -> dict[str, int]:
    """R6 Housekeeping over the account's `booked` dividend rows, Phase 1 parity (`tss.housekeep`, owner ruling
    2026-10-31): a row whose income entry is still present with `source='statement'` is skipped; any other is locked
    row → entry NOWAIT (`tss.lock_pair`) and re-checked under the lock. Entry gone → `owner_deleted`, items cleared
    (the receipt is free again: the next reconcile offers it and the policy proposes it, never books it), audit
    `dividend_owner_deleted`; present but no longer the statement's (a structural owner edit flips it to `manual`) →
    `owner_edited`, items kept (the edited entry still records the receipt, so it stays reserved), audit
    `dividend_owner_edited`. A held row or entry (`settlement_busy`) is logged, counted and left for the next run; it
    keeps reserving (fail closed). Flushes, never commits."""
    counts = {"owner_deleted": 0, "owner_edited": 0, "settlement_busy": 0}
    for row in _booked(db, SettlementBooking.account_id == account_id):
        if _system(_income(db, row.in_entry_id)):
            continue
        try:
            _, entry = tss.lock_pair(db, row)
        except CodedConflictError as exc:
            if exc.code != tss.SETTLEMENT_BUSY:
                raise
            logger.info("dividend.housekeep_busy", extra={"settlement_booking_id": row.id})
            counts["settlement_busy"] += 1
            continue
        if row.status != "booked" or _system(entry):  # re-checked under the locks
            continue
        if entry is None:
            row.status = "owner_deleted"
            row.items.clear()  # delete-orphan: the reservation goes with the row's `booked` status
            event = "dividend_owner_deleted"
        else:
            row.status = "owner_edited"  # the item stays: the edited entry still records the receipt
            event = "dividend_owner_edited"
        db.flush()
        audit.record(db, event=event, actor=tss.ACTOR, detail=_figures(row))
        counts[row.status] += 1
    return counts


def _twins(db: Session, row: SettlementBooking, entry: LedgerEntry, window_days: int) -> list[int]:
    return tss.find_twins(db, account_id=row.account_id, target_account_id=None, amount=entry.amount,
                          settlement_date=entry.entry_date, window_days=window_days, exclude=[entry.id])


def retire_twin(db: Session, row: SettlementBooking) -> set[int] | None:
    """`tss.retire_pair(reason="twin")` for one leg: lock the row, then its income entry (`tss.lock_pair`), and
    re-check under the locks in one savepoint — the row still `booked`, the entry present and `source='statement'`,
    the owner twin still found. A failed re-check returns None and leaves no lock behind. Otherwise the entry's
    coverage is released (the line re-meets the twin), the entry deleted (`delete_entries_cascade`), the row
    `retired` / `twin` with its items cleared, the released statements `needs_recheck`, audit `dividend_retired`;
    returns the released statement ids (possibly empty). A held row or entry raises `settlement_busy`. Flushes,
    never commits."""
    locks = db.begin_nested()
    try:
        _, entry = tss.lock_pair(db, row)
        if row.status != "booked" or not _system(entry) or not _twins(db, row, entry, tss.twin_window_days(db)):
            locks.rollback()
            return None
    except BaseException:
        if locks.is_active:
            locks.rollback()
        raise
    locks.commit()  # release the savepoint; the row and entry locks stay until the caller commits
    # delete_entries_cascade ends with expire_all and `in_entry_id` is ON DELETE SET NULL: copy everything first
    row_id, figures, entry_id = row.id, {**_figures(row), "origin": row.origin}, entry.id
    released = coverage_service.release_entries(db, [entry_id], reason="dividend:twin")
    entry_write_service.delete_entries_cascade(db, [entry_id])
    row = db.get(SettlementBooking, row_id)
    row.status, row.reason = "retired", "twin"
    row.items.clear()
    for sid in released:
        db.get(AccountStatement, sid).needs_recheck = True
    db.flush()
    audit.record(db, event="dividend_retired", actor=tss.ACTOR,
                 detail={**figures, "reason": "twin", "released_statement_ids": sorted(released)})
    return released


def retire_twins_for_statement(db: Session, statement: AccountStatement, accounts: list[int]) -> list[int]:
    """The reconcile's half of owner-wins for dividends (`tss.retire_twins_for_statement`'s shape), run before the
    population is read: every `booked` dividend row on `accounts` whose settlement date lies in the period ±
    `matching.population_window_days`, its income entry still the system's, and an owner entry of the same amount
    within `tss.twin_window_days` of the entry's date (`tss.find_twins`, `target_account_id=None`) → `retire_twin`.
    A row another transaction holds (`settlement_busy`) is logged and left for the next run; a failed locked re-check
    is skipped. Returns the retired row ids. Flushes, never commits."""
    from app.services.reconciliation_service import rules_for  # local: the reconciler calls this function

    rules = rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    span = timedelta(days=matching.population_window_days(rules))
    rows = _booked(db, SettlementBooking.account_id.in_(accounts),
                   SettlementBooking.settlement_date.between(statement.period_start - span, statement.period_end + span))
    if not rows:
        return []
    window_days = tss.twin_window_days(db)
    out = []
    for row in rows:
        entry = _income(db, row.in_entry_id)
        if not _system(entry) or not _twins(db, row, entry, window_days):
            continue  # housekeeping's owner_edited / owner_deleted, or no owner twin
        try:
            released = retire_twin(db, row)
        except CodedConflictError as exc:
            if exc.code != tss.SETTLEMENT_BUSY:
                raise
            logger.info("dividend.sweep_retire_busy",
                        extra={"settlement_booking_id": row.id, "statement_id": statement.id})
            continue
        if released is not None:
            out.append(row.id)
    return out
