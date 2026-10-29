"""國泰證券日對帳單: category mapping, symbol resolution, fingerprint, date ownership."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import portfolio as models
from ..models.cathay_statement import CathayStatementDay
from ..schemas.cathay_statement import Holding, StatementPayload
from . import broker_cathay_service, cash_account_service
from .broker_cathay_service import CATHAY_SIDE_MAP
from .import_service import ParsedRow, _broker_transaction_fingerprint

STATEMENT_BROKER = "TW_CATHAY_STATEMENT"

CATEGORY_TO_CSV_SIDE: dict[str, str] = {
    "集買": "現買",
    "集賣": "現賣",
    "沖買": "沖買",
    "沖賣": "沖賣",
}


@dataclass
class Mapped:
    rows: list[ParsedRow]
    canonical: list[dict]


@dataclass
class HeldReason:
    code: str
    name: str | None


def resolve_line_symbol(db: Session, name: str, holdings: list[Holding]) -> str | None:
    try:
        return broker_cathay_service.resolve_symbol(name)
    except ValueError:
        pass
    stored = {
        symbol
        for (symbol,) in db.query(models.Transaction.symbol)
        .filter(models.Transaction.name == name, models.Transaction.market == "TW")
        .distinct()
    }
    if len(stored) == 1:
        return stored.pop()
    codes = {h.code for h in holdings if h.name == name}
    if len(codes) == 1:
        return codes.pop()
    return None


def statement_fingerprint(row: ParsedRow, order_no: str) -> str:
    p = row.payload
    return _broker_transaction_fingerprint(
        broker=STATEMENT_BROKER,
        symbol=p["symbol"],
        market="TW",
        type_=p["type"],
        quantity=Decimal(p["quantity"]),
        price=p["price"],
        trade_date=p["trade_date"],
        fee=p["fee"],
        tax=p["tax"],
        currency="TWD",
        note=order_no,
    )


def map_statement(db: Session, payload: StatementPayload) -> Mapped | HeldReason:
    if payload.unsupported:
        return HeldReason("unsupported_category", None)
    trade_date = datetime(
        payload.trade_date.year, payload.trade_date.month, payload.trade_date.day, tzinfo=timezone.utc
    )
    rows: list[ParsedRow] = []
    canonical: list[dict] = []
    for index, line in enumerate(payload.lines, start=1):
        symbol = resolve_line_symbol(db, line.name, payload.holdings)
        if symbol is None:
            return HeldReason("unresolved_symbol", line.name)
        side = CATEGORY_TO_CSV_SIDE[line.category]
        type_, position_side = CATHAY_SIDE_MAP[side]
        marker = side if side in ("沖買", "沖賣") else None
        row = ParsedRow(
            row_index=index,
            fingerprint="",
            payload={
                "symbol": symbol,
                "type": type_,
                "position_side": position_side,
                "quantity": line.quantity,
                "price": line.price,
                "trade_date": trade_date,
                "fee": line.fee,
                "tax": line.tax,
                "name": line.name,
                "order_id": line.order_no[:-2],
                "broker_subtype": side[0],
                "broker_day_trade_marker": marker,
            },
        )
        row.fingerprint = statement_fingerprint(row, line.order_no)
        rows.append(row)
        canonical.append(
            {
                "symbol": symbol,
                "type": type_,
                "position_side": position_side,
                "marker": marker,
                "quantity": line.quantity,
                "price": str(line.price),
                "fee": str(line.fee),
                "tax": str(line.tax),
                "order_no": line.order_no,
            }
        )
    return Mapped(rows=rows, canonical=canonical)


# --- Date ownership and import (spec 5.3-5.6) ------------------------------

_LOCK_TAG = 0x43415448  # "CATH"

TW_CATHAY_FILTER = and_(
    models.Transaction.market == "TW",
    or_(models.Transaction.broker.is_(None), models.Transaction.broker == "TW_CATHAY"),
)


def lock_trade_dates(db: Session, dates: Iterable[date]) -> None:
    """Take the per-date advisory lock shared with the CSV commit, ascending.

    Transaction-scoped on Postgres; a no-op elsewhere (SQLite tests are single-writer).
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    for day in sorted(set(dates)):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_TAG << 32 | day.toordinal()}
        )


@dataclass
class StatementOutcome:
    """Result of one statement post.

    ``outcome`` is the §5.4 status (``imported`` / ``verified`` / ``duplicate`` /
    ``conflict`` / ``held`` / ``mismatch``). ``counts``: ``lines`` = statement lines in
    the payload, ``created`` = transactions inserted (0 on a dry run). ``errors`` is
    non-empty only when the commit failed and was rolled back; nothing was written then.
    """

    outcome: str
    trade_date: date
    reason: str | None
    counts: dict[str, int]
    holdings_diff: list[dict] = field(default_factory=list)
    created_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class _Decision:
    outcome: str
    reason: str | None = None
    detail: dict | None = None
    mapped: Mapped | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(value) -> str:
    return format(Decimal(value).normalize(), "f")


def _day_bounds(trade_date: date) -> tuple[datetime, datetime]:
    start = datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _cathay_rows_on(db: Session, trade_date: date) -> list[models.Transaction]:
    start, end = _day_bounds(trade_date)
    return (
        db.query(models.Transaction)
        .filter(TW_CATHAY_FILTER)
        .filter(models.Transaction.trade_date >= start, models.Transaction.trade_date < end)
        .all()
    )


def _sums(items: Iterable[tuple]) -> dict[tuple, list[Decimal]]:
    """Sum (quantity, fee, tax) per (symbol, type, position_side, marker, price, *rest); price normalised."""
    sums: dict[tuple, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 3)
    for symbol, type_, side, marker, price, *rest, quantity, fee, tax in items:
        acc = sums[(symbol, type_, side, marker, _num(price), *rest)]
        acc[0] += Decimal(quantity)
        acc[1] += Decimal(fee or 0)
        acc[2] += Decimal(tax or 0)
    return sums


def _group(items: Iterable[tuple]) -> list[dict]:
    """Sum (quantity, fee, tax) per (symbol, type, position_side, marker, price)."""
    sums = _sums(items)
    return [
        {"symbol": k[0], "type": k[1], "position_side": k[2], "marker": k[3], "price": k[4],
         "quantity": _num(v[0]), "fee": _num(v[1]), "tax": _num(v[2])}
        for k, v in sorted(sums.items(), key=lambda kv: tuple(str(x) for x in kv[0]))
    ]


def _row_groups(rows: list[models.Transaction]) -> list[dict]:
    return _group(
        (r.symbol, r.type.value, r.position_side.value, r.broker_day_trade_marker, r.price,
         r.quantity, r.fee, r.tax)
        for r in rows
    )


def _line_groups(mapped: Mapped) -> list[dict]:
    return _group(
        (p["symbol"], p["type"], p["position_side"], p["broker_day_trade_marker"], p["price"],
         p["quantity"], p["fee"], p["tax"])
        for p in (row.payload for row in mapped.rows)
    )


def _held_reason(held: HeldReason) -> str:
    return f"{held.code}:{held.name}" if held.name else held.code


def _decide(db: Session, payload: StatementPayload, day: CathayStatementDay | None) -> _Decision:
    """§5.4 cases 1-5, read-only."""
    sha = payload.source.sha256
    if day is not None and day.accepted_kind is not None:
        if day.accepted_sha256 == sha:
            return _Decision("duplicate")
        result = map_statement(db, payload)
        detail = {"accepted_lines": day.canonical_lines, "statement_lines": None}
        if isinstance(result, Mapped):
            detail["statement_lines"] = result.canonical
        else:
            detail["statement_held"] = _held_reason(result)
        return _Decision("conflict", detail=detail)
    result = map_statement(db, payload)
    if isinstance(result, HeldReason):
        return _Decision("held", reason=_held_reason(result))
    rows = _cathay_rows_on(db, payload.trade_date)
    if not rows:
        return _Decision("imported", mapped=result)
    portfolio_groups, statement_groups = _row_groups(rows), _line_groups(result)
    if portfolio_groups == statement_groups:
        return _Decision("verified", mapped=result)
    return _Decision(
        "mismatch",
        detail={"portfolio_groups": portfolio_groups, "statement_groups": statement_groups},
    )


def _holdings_diff(
    db: Session, payload: StatementPayload, pending: Iterable[ParsedRow] = ()
) -> list[dict]:
    """§5.6: statement 集保股數 vs Σ BUY − Σ SELL LONG of TW Cathay rows up to D.

    ``pending`` adds rows a dry run would insert.
    """
    _, end = _day_bounds(payload.trade_date)
    portfolio: dict[str, Decimal] = defaultdict(Decimal)
    totals = (
        db.query(models.Transaction.symbol, models.Transaction.type, func.sum(models.Transaction.quantity))
        .filter(TW_CATHAY_FILTER)
        .filter(models.Transaction.position_side == models.PositionSide.LONG)
        .filter(models.Transaction.trade_date < end)
        .group_by(models.Transaction.symbol, models.Transaction.type)
    )
    for symbol, type_, quantity in totals:
        sign = 1 if type_ == models.TransactionType.BUY else -1
        portfolio[symbol] += sign * Decimal(quantity)
    for row in pending:
        if row.payload["position_side"] == models.PositionSide.LONG.value:
            sign = 1 if row.payload["type"] == models.TransactionType.BUY.value else -1
            portfolio[row.payload["symbol"]] += sign * Decimal(row.payload["quantity"])
    statement: dict[str, int] = defaultdict(int)
    for holding in payload.holdings:
        statement[holding.code] += holding.shares
    symbols = set(statement) | {symbol for symbol, shares in portfolio.items() if shares != 0}
    return [
        {"symbol": symbol, "statement_shares": statement[symbol],
         "portfolio_shares": int(portfolio[symbol])}
        for symbol in sorted(symbols)
        if statement[symbol] != portfolio[symbol]
    ]


def _record_attempt(
    db: Session, payload: StatementPayload, day: CathayStatementDay | None, decision: _Decision,
    holdings_diff: list[dict],
) -> None:
    now = _now()
    if day is None:
        day = CathayStatementDay(trade_date=payload.trade_date)
        db.add(day)
    if decision.outcome in ("imported", "verified"):
        day.accepted_kind = decision.outcome
        day.accepted_sha256 = payload.source.sha256
        day.accepted_at = now
        day.canonical_lines = decision.mapped.canonical
        detail = {"holdings_diff": holdings_diff}
    else:
        detail = decision.detail
    if day.last_status != decision.outcome:
        day.since = now
    day.last_sha256 = payload.source.sha256
    day.last_status = decision.outcome
    day.last_reason = decision.reason[:80] if decision.reason else None
    day.last_detail = detail
    day.last_at = now
    day.drive_file_id = payload.source.drive_file_id


def import_statement(db: Session, payload: StatementPayload, *, dry_run: bool) -> StatementOutcome:
    """Apply one statement for its trade date (spec 5.4) under the date lock (5.3)."""
    counts = {"lines": len(payload.lines), "created": 0}

    def outcome(decision: _Decision, holdings_diff=(), created_ids=(), errors=()) -> StatementOutcome:
        return StatementOutcome(
            outcome=decision.outcome, trade_date=payload.trade_date, reason=decision.reason,
            counts=counts, holdings_diff=list(holdings_diff), created_ids=list(created_ids),
            errors=list(errors),
        )

    if dry_run:
        decision = _decide(db, payload, db.get(CathayStatementDay, payload.trade_date))
        holdings_diff: list[dict] = []
        if decision.outcome in ("imported", "verified"):
            pending = decision.mapped.rows if decision.outcome == "imported" else []
            holdings_diff = _holdings_diff(db, payload, pending)
        return outcome(decision, holdings_diff)

    if db.in_transaction():
        db.rollback()
    created_ids: list[int] = []
    decision = _Decision("held")
    try:
        with db.begin():
            lock_trade_dates(db, [payload.trade_date])
            day = db.get(CathayStatementDay, payload.trade_date, populate_existing=True)
            decision = _decide(db, payload, day)
            if decision.outcome == "imported":
                cash_account_id: int | None = None
                if cash_account_service.cash_leg_enabled():
                    cash_account_id = cash_account_service.resolve_default_cathay_twd_account(db).id
                for row in decision.mapped.rows:
                    tx = broker_cathay_service._insert_transaction(db, row, cash_account_id)
                    created_ids.append(tx.id)
            holdings_diff = []
            if decision.outcome in ("imported", "verified"):
                holdings_diff = _holdings_diff(db, payload)
            _record_attempt(db, payload, day, decision, holdings_diff)
    except (
        cash_account_service.CashAccountNotFound,
        cash_account_service.CashAccountAmbiguous,
    ):
        db.rollback()
        raise
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return outcome(decision, errors=[str(exc)])
    counts["created"] = len(created_ids)
    return outcome(decision, holdings_diff, created_ids)


# --- CSV import on an owned date (spec 5.5) --------------------------------


def owned_days(db: Session, dates: Iterable[date]) -> dict[date, CathayStatementDay]:
    """Ownership rows (``accepted_kind`` set) for ``dates``, re-read from the database."""
    dates = set(dates)
    if not dates:
        return {}
    days = (
        db.query(CathayStatementDay)
        .filter(CathayStatementDay.trade_date.in_(dates), CathayStatementDay.accepted_kind.is_not(None))
        .populate_existing()
        .all()
    )
    return {day.trade_date: day for day in days}


def split_owned(
    rows: list[ParsedRow], owned: dict[date, CathayStatementDay]
) -> tuple[dict[date, list[ParsedRow]], list[ParsedRow]]:
    """CSV rows by owned trade date, and the rows on dates no statement owns (in upload order)."""
    by_date: dict[date, list[ParsedRow]] = defaultdict(list)
    free: list[ParsedRow] = []
    for row in rows:
        trade_date = row.payload["trade_date"].date()
        if trade_date in owned:
            by_date[trade_date].append(row)
        else:
            free.append(row)
    return dict(by_date), free


def covered_check(rows: list[ParsedRow], canonical: list[dict]) -> bool:
    """True when the CSV rows of an owned date sum to its accepted statement lines.

    Grouped per (symbol, type, position_side, marker, price, 委託書號); a statement order_no is
    the CSV 委託書號 plus a two-character suffix.
    """
    csv_sums = _sums(
        (p["symbol"], p["type"], p["position_side"], p["broker_day_trade_marker"], p["price"],
         p["order_id"], p["quantity"], p["fee"], p["tax"])
        for p in (row.payload for row in rows)
    )
    line_sums = _sums(
        (line["symbol"], line["type"], line["position_side"], line["marker"], line["price"],
         line["order_no"][:-2], line["quantity"], line["fee"], line["tax"])
        for line in canonical
    )
    return csv_sums == line_sums
