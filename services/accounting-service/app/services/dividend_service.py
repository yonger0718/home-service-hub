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

Nothing here commits; the reconciler and the engine own the transaction."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models import Account, AccountStatement, Category, ReconciliationCase, SettlementBooking, StatementLine
from app.schemas.writes import SETTLEMENT_SOURCES
from app.services import portfolio_client, settings_service
from app.services import trade_settlement_service as tss
from app.services.portfolio_client import Dividend, DividendFeed, PortfolioClient, PortfolioUnavailable
from app.services.statements import matching, retired

KIND = "dividend"
AMBIGUOUS_HINT = "dividend_ambiguous"
MISMATCH_HINT = "dividend_mismatch"
FAMILY_HINTS = (AMBIGUOUS_HINT, MISMATCH_HINT)
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


def recheck_statement_ids(db: Session) -> list[int]:
    """R11: the live, non-retired statements, ascending, of accounts with a known `settlement_source`
    (`SETTLEMENT_SOURCES`) holding an open case of kind `dividend`, with a hint in `FAMILY_HINTS`, or a plain
    `line_unmatched` one (no hint) on a deposit line naming one of the rules' `dividend_labels`. It compares nothing
    (Phase 1's shape): while such a case is open, each run's reconcile re-reads the feed. Read only."""
    from app.services.reconciliation_service import rules_for  # local: the reconciler imports this module

    labels = rules_for(settings_service.reconciliation_rules(db), date.today()).dividend_labels
    hint = ReconciliationCase.context["hint"].astext
    plain_line = (ReconciliationCase.kind == "line_unmatched") & hint.is_(None) & exists().where(
        StatementLine.id == ReconciliationCase.line_id, StatementLine.line_kind == "deposit",
        or_(*(StatementLine.merchant_norm.contains(label, autoescape=True) for label in labels)))
    return sorted(db.execute(
        select(AccountStatement.id).distinct()
        .join(ReconciliationCase, ReconciliationCase.statement_id == AccountStatement.id)
        .join(Account, Account.id == AccountStatement.account_id)
        .where(ReconciliationCase.status == "open",
               or_(ReconciliationCase.kind == KIND, hint.in_(FAMILY_HINTS), plain_line) if labels
               else or_(ReconciliationCase.kind == KIND, hint.in_(FAMILY_HINTS)),
               Account.settlement_source.in_(SETTLEMENT_SOURCES),
               AccountStatement.mode == "live", AccountStatement.current_revision_id.is_not(None),
               ~retired.clause(AccountStatement.id))).scalars())
