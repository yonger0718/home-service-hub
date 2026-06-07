"""One-shot historical backfill for ``portfolio_snapshot``.

Two phases:

* :func:`backfill_prices_range` — per-trading-day driver around
  :func:`market_data_service.backfill_date` with weekend skip,
  empty-payload holiday probe, throttle gap, retry-with-backoff, and
  per-date error isolation.
* :func:`replay_snapshots_range` — pure-DB recomputation of
  ``portfolio_snapshot`` rows from existing transactions, dividends,
  and ``price_history`` already in the database.

Idempotent: both phases upsert via ``Session.merge``.

CLI:
    python -m app.services.networth_backfill_service --rebuild-all
    python -m app.services.networth_backfill_service --rebuild-all --dry-run
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as dt_date, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from sqlalchemy import case, delete, func, literal, select, union_all
from sqlalchemy.orm import Session

from ..models import portfolio as portfolio_models
from ..models.broker_account import BrokerAccount
from ..models.cash_transaction import CashTransaction
from ..models.portfolio import PositionSide
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..models.price_history import PriceHistory
from . import cash_account_service
from . import market_data_service
from .portfolio_service import _load_adjusted_transactions
from .quotes import fx_rate_service
from .realized_pnl_service import iter_realized_events

logger = logging.getLogger(__name__)

DEFAULT_THROTTLE_SEC = 1.5
RETRY_DELAYS_SEC = (2.0, 5.0)
PARTIAL_FETCH_RATIO = 0.8
PARTIAL_FETCH_MIN_BASELINE_DAYS = 10
PARTIAL_FETCH_BASELINE_WINDOW_DAYS = 30
PARTIAL_FETCH_LOOKBACK_DAYS = 45
SNAPSHOT_QUANT = Decimal("0.0001")


@dataclass
class BackfillError:
    date: dt_date
    reason: str


@dataclass
class PriceBackfillResult:
    dates_processed: int = 0
    dates_skipped: int = 0
    dates_inactive: int = 0
    rows_written: int = 0
    errors: List[BackfillError] = field(default_factory=list)


@dataclass
class SnapshotReplayResult:
    dates_processed: int = 0
    dates_inactive: int = 0
    snapshots_written: int = 0
    stale_rows_deleted: int = 0
    errors: List[BackfillError] = field(default_factory=list)


def _snapshot_amount(value: Decimal) -> Decimal:
    return Decimal(value).quantize(SNAPSHOT_QUANT)


def _display_snapshot_amount(value: Decimal) -> str:
    amount = Decimal(value)
    if amount == 0:
        return "0"
    cents = amount.quantize(Decimal("0.01"))
    if amount == cents:
        return str(cents)
    return str(amount)


# ---------- Helpers ----------


def _iter_trading_days(from_d: dt_date, to_d: dt_date):
    """Yield weekdays (Mon-Fri) inclusive. Holiday probe happens per-fetch."""
    cur = from_d
    while cur <= to_d:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)


def count_trading_days(from_d: dt_date, to_d: dt_date) -> int:
    """Count weekdays (Mon-Fri) in ``[from_d, to_d]`` inclusive."""
    return sum(1 for _ in _iter_trading_days(from_d, to_d))


def _fetch_with_retry(
    fetcher: Callable[[dt_date], list],
    date: dt_date,
    *,
    delays: tuple[float, ...] = RETRY_DELAYS_SEC,
    sleep: Callable[[float], None] = time.sleep,
) -> list:
    """Call ``fetcher(date)``. Retry on empty result with backoff delays.

    The underlying ``market_data_service`` HTTP helpers already log and
    return ``[]`` on transport failures, so an empty list is the
    failure signal we retry on. A genuinely-closed market (holiday)
    will return ``[]`` on every attempt — the caller then treats the
    final empty as a holiday-skip.
    """
    result = fetcher(date)
    if result:
        return result
    for delay in delays:
        sleep(delay)
        result = fetcher(date)
        if result:
            return result
    return []


def _existing_price_dates(
    db: Session, from_d: dt_date, to_d: dt_date
) -> dict[str, set[dt_date]]:
    """Return per-source sets of dates already present in ``price_history``.

    Keyed by ``source`` (``"TWSE"`` / ``"TPEx"``). Used by the price-range
    driver to skip dates we've already pulled for that source, so
    re-running a backfill costs zero requests per already-done (source,
    date) pair.
    """
    rows = (
        db.query(PriceHistory.source, PriceHistory.date)
        .filter(PriceHistory.date >= from_d, PriceHistory.date <= to_d)
        .distinct()
        .all()
    )
    out: dict[str, set[dt_date]] = {}
    for source, d in rows:
        out.setdefault(source, set()).add(d)
    return out


def _recent_row_counts(session: Session, *, source: str, today: dt_date) -> list[int]:
    """Return recent per-date ``price_history`` counts for one source."""
    cutoff = today - timedelta(days=PARTIAL_FETCH_LOOKBACK_DAYS)
    rows = (
        session.query(PriceHistory.date, func.count())
        .filter(
            PriceHistory.source == source,
            PriceHistory.date >= cutoff,
            PriceHistory.date < today,
        )
        .group_by(PriceHistory.date)
        .order_by(PriceHistory.date.desc())
        .limit(PARTIAL_FETCH_BASELINE_WINDOW_DAYS)
        .all()
    )
    return [int(count) for _date, count in rows]


def _is_partial_response(
    session: Session,
    *,
    source: str,
    date: dt_date,
    fetched_rows: int,
) -> bool:
    """Return whether a non-empty whole-market fetch is under baseline."""
    if fetched_rows == 0:
        return False

    baseline = _recent_row_counts(session, source=source, today=date)
    if len(baseline) < PARTIAL_FETCH_MIN_BASELINE_DAYS:
        logger.info(
            "phase1.partial_check_skipped_cold_start",
            extra={
                "source": source,
                "date": date.isoformat(),
                "baseline_days": len(baseline),
            },
        )
        return False

    baseline_median = statistics.median(baseline)
    ratio = fetched_rows / baseline_median
    if ratio < PARTIAL_FETCH_RATIO:
        logger.warning(
            "phase1.partial_fetch_skipped",
            extra={
                "source": source,
                "date": date.isoformat(),
                "fetched_rows": fetched_rows,
                "baseline_median": baseline_median,
                "ratio": ratio,
            },
        )
        return True
    return False


def compute_active_dates(
    db: Session,
    from_d: dt_date,
    to_d: dt_date,
    *,
    include_non_trading: bool = False,
) -> set[dt_date]:
    """Return held dates in ``[from_d, to_d]`` from all tx/dividend events."""
    tx_events = select(
        portfolio_models.Transaction.symbol.label("symbol"),
        portfolio_models.Transaction.trade_date.label("event_at"),
        case(
            (
                portfolio_models.Transaction.type
                == portfolio_models.TransactionType.BUY,
                portfolio_models.Transaction.quantity,
            ),
            else_=-portfolio_models.Transaction.quantity,
        ).label("delta"),
        case(
            (
                portfolio_models.Transaction.type
                == portfolio_models.TransactionType.BUY,
                literal(0),
            ),
            else_=literal(2),
        ).label("event_rank"),
    ).where(portfolio_models.Transaction.trade_date <= to_d)
    dividend_events = (
        select(
            portfolio_models.Dividend.symbol.label("symbol"),
            portfolio_models.Dividend.ex_dividend_date.label("event_at"),
            portfolio_models.Dividend.stock_dividend_shares.label("delta"),
            literal(1).label("event_rank"),
        )
        .where(
            portfolio_models.Dividend.stock_dividend_shares > 0,
            portfolio_models.Dividend.ex_dividend_date <= to_d,
        )
    )
    events = union_all(tx_events, dividend_events).subquery()
    rows = db.execute(
        select(
            events.c.symbol,
            events.c.event_at,
            events.c.delta,
            events.c.event_rank,
        ).order_by(
            events.c.symbol,
            events.c.event_at,
            events.c.event_rank,
        )
    ).all()

    active_dates: set[dt_date] = set()
    current_symbol: Optional[str] = None
    running_qty: Decimal = Decimal("0")
    open_date: Optional[dt_date] = None

    def add_interval(start: dt_date, end: dt_date) -> None:
        clipped_start = max(start, from_d)
        clipped_end = min(end, to_d)
        if clipped_start <= clipped_end:
            if include_non_trading:
                cur = clipped_start
                while cur <= clipped_end:
                    active_dates.add(cur)
                    cur += timedelta(days=1)
            else:
                active_dates.update(_iter_trading_days(clipped_start, clipped_end))

    for symbol, event_at, delta, _event_rank in rows:
        if symbol != current_symbol:
            if current_symbol is not None and open_date is not None:
                add_interval(open_date, to_d)
            current_symbol = symbol
            running_qty = Decimal("0")
            open_date = None

        event_date = event_at.date() if hasattr(event_at, "date") else event_at
        previous_qty = running_qty
        running_qty += Decimal(delta) if delta is not None else Decimal("0")
        if previous_qty == 0 and running_qty != 0:
            open_date = event_date
        elif previous_qty != 0 and running_qty == 0 and open_date is not None:
            add_interval(open_date, event_date)
            open_date = None

    if current_symbol is not None and open_date is not None:
        add_interval(open_date, to_d)

    return active_dates


# ---------- Phase 1: prices ----------


def backfill_prices_range(
    db: Session,
    from_d: dt_date,
    to_d: dt_date,
    *,
    throttle_sec: float = DEFAULT_THROTTLE_SEC,
    sleep: Callable[[float], None] = time.sleep,
    twse_fetcher: Callable[[dt_date], list] = market_data_service.fetch_twse_date,
    tpex_fetcher: Callable[[dt_date], list] = market_data_service.fetch_tpex_date,
    active_dates: Optional[set[dt_date]] = None,
) -> PriceBackfillResult:
    """Walk ``[from_d, to_d]`` weekdays, persist TWSE+TPEx rows per date.

    Empty payload from BOTH markets ⇒ treat as holiday, log + skip, no
    throttle sleep. Any other failure ⇒ rollback, log, continue to next
    date.
    """
    result = PriceBackfillResult()
    first = True
    already = _existing_price_dates(db, from_d, to_d)
    twse_done = already.get("TWSE", set())
    tpex_done = already.get("TPEx", set())
    # Fire TWSE + TPEx fetches concurrently per date — the two endpoints
    # are independent and IO-bound, so a 2-thread pool roughly halves
    # per-date wall time without raising rate-limit pressure (still one
    # request per second to each host).
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        for date in _iter_trading_days(from_d, to_d):
            if active_dates is not None and date not in active_dates:
                result.dates_inactive += 1
                continue
            need_twse = date not in twse_done
            need_tpex = date not in tpex_done
            if not need_twse and not need_tpex:
                result.dates_skipped += 1
                continue
            if not first:
                sleep(throttle_sec)
            first = False
            try:
                twse_future = (
                    pool.submit(_fetch_with_retry, twse_fetcher, date, sleep=sleep)
                    if need_twse
                    else None
                )
                tpex_future = (
                    pool.submit(_fetch_with_retry, tpex_fetcher, date, sleep=sleep)
                    if need_tpex
                    else None
                )
                twse_rows = twse_future.result() if twse_future else []
                tpex_rows = tpex_future.result() if tpex_future else []
            except Exception as exc:  # noqa: BLE001 — per-date isolation
                db.rollback()
                logger.exception(
                    "networth_backfill.prices.fetch_failed",
                    extra={"date": date.isoformat(), "error": str(exc)},
                )
                result.errors.append(BackfillError(date=date, reason=f"fetch: {exc}"))
                continue

            if not twse_rows and not tpex_rows:
                if need_twse and need_tpex:
                    # Both sides actually fetched and both empty → genuine full-market holiday.
                    logger.info(
                        "networth_backfill.prices.holiday_skip",
                        extra={"date": date.isoformat()},
                    )
                    result.dates_skipped += 1
                    continue
                # Cached side proves the market was open; the fetched side
                # returning empty is a fetch failure, not a holiday.
                missing = "TWSE" if need_twse else "TPEx"
                logger.warning(
                    "networth_backfill.prices.empty_fetch",
                    extra={"date": date.isoformat(), "source": missing},
                )
                result.errors.append(
                    BackfillError(date=date, reason=f"{missing} returned no rows")
                )
                continue

            if twse_rows and _is_partial_response(
                db,
                source="TWSE",
                date=date,
                fetched_rows=len(twse_rows),
            ):
                twse_rows = []
                result.errors.append(
                    BackfillError(date=date, reason="TWSE partial response, skipped")
                )
            if tpex_rows and _is_partial_response(
                db,
                source="TPEx",
                date=date,
                fetched_rows=len(tpex_rows),
            ):
                tpex_rows = []
                result.errors.append(
                    BackfillError(date=date, reason="TPEx partial response, skipped")
                )

            if not twse_rows and not tpex_rows:
                continue

            try:
                written = market_data_service.upsert_rows(db, [*twse_rows, *tpex_rows])
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.exception(
                    "networth_backfill.prices.upsert_failed",
                    extra={"date": date.isoformat(), "error": str(exc)},
                )
                result.errors.append(BackfillError(date=date, reason=f"upsert: {exc}"))
                continue

            result.dates_processed += 1
            result.rows_written += written
            logger.info(
                "networth_backfill.prices.date_done",
                extra={
                    "date": date.isoformat(),
                    "twse_rows": len(twse_rows),
                    "tpex_rows": len(tpex_rows),
                    "written": written,
                },
            )
    finally:
        pool.shutdown(wait=True)
    return result


# ---------- Phase 2: snapshot replay ----------


def _trade_date_of(t: portfolio_models.Transaction) -> dt_date:
    td = t.trade_date
    return td.date() if hasattr(td, "date") else td


def _ex_date_of(d: portfolio_models.Dividend) -> dt_date:
    ex = d.ex_dividend_date
    return ex.date() if hasattr(ex, "date") else ex


def _load_price_map(
    db: Session, from_d: dt_date, to_d: dt_date
) -> Dict[tuple[str, str, dt_date], tuple[Decimal, str | None]]:
    """Pull close prices in range as ``{(symbol, market, date): (close, currency)}``."""
    rows = (
        db.query(
            PriceHistory.symbol,
            PriceHistory.market,
            PriceHistory.date,
            PriceHistory.close,
            PriceHistory.currency,
        )
        .filter(
            PriceHistory.date >= from_d,
            PriceHistory.date <= to_d,
        )
        .all()
    )
    return {
        (sym, (market or "TW").upper(), d): (Decimal(close), currency)
        for sym, market, d, close, currency in rows
    }


def replay_snapshots_range(
    db: Session,
    from_d: dt_date,
    to_d: dt_date,
    *,
    active_dates: Optional[set[dt_date]] = None,
    dry_run: bool = False,
) -> SnapshotReplayResult:
    """Recompute one ``portfolio_snapshot`` row per date in range.

    Walks transactions + dividends in chronological order, maintains
    per-symbol holdings and cost basis, and at each target date emits
    a snapshot computed against the same-date ``price_history.close``.

    Stock dividends are already represented as zero-cost BUY transactions
    by the auto-record service, so they are picked up via the
    transactions walk. Corporate-action split factors are NOT applied
    here (out of scope for v1; live daily-cron path still applies them
    going forward).
    """
    result = SnapshotReplayResult()
    held_calendar = compute_active_dates(
        db, from_d, to_d, include_non_trading=True
    )
    # Cash-activity dates: only these days are allowed to emit a cash-only
    # snapshot row when no stock activity exists. Without this gate, every
    # calendar day where the running cash balance is positive would write a
    # row, inflating the snapshot table by 365 rows/year per cash-only
    # period. Sources: explicit cash transactions + account opening dates
    # for accounts that started with a non-zero opening_balance.
    cash_txn_dates = {
        row[0]
        for row in db.query(CashTransaction.txn_date).distinct().all()
        if row[0] is not None
    }
    opening_dates = {
        row[0]
        for row in db.query(BrokerAccount.opening_date)
        .filter(BrokerAccount.opening_balance != 0)
        .distinct()
        .all()
        if row[0] is not None
    }
    cash_activity_dates = cash_txn_dates | opening_dates

    transactions = list(_load_adjusted_transactions(db))
    events = list(iter_realized_events(transactions))
    realized_by_date: dict[dt_date, Decimal] = defaultdict(lambda: Decimal("0"))
    for event in events:
        realized_by_date[event.trade_date] += event.realized_pnl

    cumulative_realized = sum(
        (
            amount
            for event_date, amount in realized_by_date.items()
            if event_date < from_d
        ),
        Decimal("0"),
    )
    for event_date in [event_date for event_date in realized_by_date if event_date < from_d]:
        realized_by_date.pop(event_date, None)

    dividends = (
        db.query(portfolio_models.Dividend)
        .order_by(portfolio_models.Dividend.ex_dividend_date)
        .all()
    )
    price_map = _load_price_map(db, from_d, to_d)
    trading_dates = {d for (_s, _m, d) in price_map.keys()}
    trading_dates_by_market: Dict[str, set[dt_date]] = defaultdict(set)
    for (_sym, _market, _d) in price_map.keys():
        trading_dates_by_market[_market].add(_d)
    # Stock activity dates — transaction trade dates plus dividend
    # ex-dividend dates. Used by the trading-day cash-only gate so a
    # close-out SELL or dividend date still writes a row even when
    # holdings are zero and cash did not change that day.
    stock_activity_dates = {
        _trade_date_of(t) for t in transactions
    } | {_ex_date_of(d) for d in dividends}
    prior_snapshot = (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.date < from_d,
            PortfolioSnapshot.total_market_value > 0,
        )
        .order_by(PortfolioSnapshot.date.desc())
        .first()
    )
    last_trading_mv: Decimal | None = (
        Decimal(prior_snapshot.total_market_value)
        if prior_snapshot is not None
        else None
    )
    last_trading_cost: Decimal | None = (
        Decimal(prior_snapshot.total_cost) if prior_snapshot is not None else None
    )

    # Pre-seed forward-fill cache from the most recent price_history row
    # before from_d for each (symbol, market). Lets a foreign-market
    # holiday on the first date of the range still revalue against the
    # last known close instead of dropping the symbol's contribution.
    seed_rows = (
        db.query(
            PriceHistory.symbol,
            PriceHistory.market,
            PriceHistory.close,
            PriceHistory.currency,
            PriceHistory.date,
        )
        .filter(PriceHistory.date < from_d)
        .order_by(
            PriceHistory.symbol.asc(),
            PriceHistory.market.asc(),
            PriceHistory.date.desc(),
        )
        .all()
    )
    last_close_by_key: Dict[tuple[str, str], tuple[Decimal, str | None]] = {}
    for sym, market, close, currency, _d in seed_rows:
        key = (sym, (market or "TW").upper())
        if key not in last_close_by_key:
            last_close_by_key[key] = (Decimal(close), currency)

    qty: Dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    cost: Dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    # Signed running BUY-SELL per symbol (no clamp). Matches the
    # portfolio_service active-holdings convention: if net <= 0 at a
    # given date, treat the symbol as fully exited so a dropped SELL
    # (qty=0 at the time) doesn't leave phantom holdings behind once
    # later BUY+SELL pairs cancel out the deficit.
    signed_net: Dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    cumulative_dividends = Decimal("0")
    warned_missing: set[tuple[str, str, dt_date]] = set()
    warned_missing_fx: set[tuple[str, str, dt_date]] = set()
    stale_candidates: list[dt_date] = []
    # Distinct list of dates the NEW cash-only gate suppressed. These
    # dates may carry a phantom all-zero row written by a prior
    # backfill, which must be cleaned up so `--rebuild-all` actually
    # repairs history. Kept separate from `stale_candidates` so the
    # explicit `active_dates` opt-out path (caller said "leave my
    # inactive-date rows alone") is unaffected.
    gated_phantom_candidates: list[dt_date] = []

    def write_snapshot(snapshot_date: dt_date, row: PortfolioSnapshot) -> bool:
        """Merge one snapshot inside its own SAVEPOINT."""
        row.total_market_value = _snapshot_amount(row.total_market_value)
        row.total_cost = _snapshot_amount(row.total_cost)
        row.total_unrealized_pnl = _snapshot_amount(row.total_unrealized_pnl)
        row.total_dividends = _snapshot_amount(row.total_dividends)
        row.total_realized_pnl = _snapshot_amount(row.total_realized_pnl)
        row.total_cash_twd = _snapshot_amount(row.total_cash_twd)
        if dry_run:
            existing = db.get(PortfolioSnapshot, snapshot_date)
            old = (
                Decimal(existing.total_realized_pnl)
                if existing is not None
                else Decimal("0")
            )
            new = Decimal(row.total_realized_pnl)
            old_display = str(old) if existing is not None else "0"
            print(
                f"{snapshot_date} old={old_display} "
                f"new={_display_snapshot_amount(new)} "
                f"delta={str(new - old) if new != old else '0'}"
            )
            return True

        sp = db.begin_nested()
        try:
            db.merge(row)
            sp.commit()
            result.snapshots_written += 1
            return True
        except Exception as exc:  # noqa: BLE001 — per-date isolation
            sp.rollback()
            logger.exception(
                "networth_backfill.replay.date_failed",
                extra={"date": snapshot_date.isoformat(), "error": str(exc)},
            )
            result.errors.append(BackfillError(date=snapshot_date, reason=str(exc)))
            return False

    def total_cash_twd(snapshot_date: dt_date) -> Decimal:
        cash_total, skipped = cash_account_service.get_total_balance_in(
            db, "TWD", asof=snapshot_date
        )
        if skipped:
            logger.warning(
                "networth_backfill.replay.cash_skipped_currencies",
                extra={"date": snapshot_date.isoformat(), "skipped": skipped},
            )
        return cash_total

    tx_i = 0
    div_i = 0
    cur = from_d
    while cur <= to_d:
        cumulative_realized += realized_by_date.pop(cur, Decimal("0"))

        # Advance transactions up to and including ``cur``.
        while tx_i < len(transactions) and _trade_date_of(transactions[tx_i]) <= cur:
            t = transactions[tx_i]
            sym = t.symbol
            market = (getattr(t, "market", "TW") or "TW").upper()
            state_key = (sym, market)
            tx_qty = Decimal(t.quantity)
            tx_price = Decimal(t.price)
            tx_fee = Decimal(t.fee or 0)
            tx_fx = Decimal(getattr(t, "fx_rate_to_twd", None) or 1)
            side = getattr(t, "position_side", None) or PositionSide.LONG
            if not isinstance(side, PositionSide):
                side = PositionSide(side)
            if side is PositionSide.SHORT:
                # SHORT excluded from long-side aggregates — see design.md decision 2.
                tx_i += 1
                continue
            if t.type == portfolio_models.TransactionType.BUY:
                qty[state_key] += tx_qty
                cost[state_key] += tx_qty * tx_price * tx_fx + tx_fee * tx_fx
                signed_net[state_key] += tx_qty
            else:  # SELL
                signed_net[state_key] -= tx_qty
                if qty[state_key] > 0:
                    avg = cost[state_key] / qty[state_key]
                    sold = min(tx_qty, qty[state_key])
                    qty[state_key] -= sold
                    cost[state_key] -= sold * avg
                    if qty[state_key] <= 0:
                        qty[state_key] = 0
                        cost[state_key] = Decimal("0")
            tx_i += 1

        # Advance dividends up to and including ``cur``.
        while div_i < len(dividends) and _ex_date_of(dividends[div_i]) <= cur:
            dividend = dividends[div_i]
            cumulative_dividends += Decimal(dividend.amount) * Decimal(
                dividend.fx_rate_to_twd or 1
            )
            div_i += 1

        is_weekend = cur.weekday() >= 5
        is_inactive = active_dates is not None and cur not in active_dates
        is_holiday = cur not in trading_dates
        would_skip = is_weekend or is_inactive or is_holiday
        if is_inactive and not is_weekend:
            result.dates_inactive += 1

        if would_skip:
            wrote_forward_fill = False
            if (
                cur in held_calendar
                and last_trading_mv is not None
                and last_trading_cost is not None
                and sum(qty.values()) > 0
            ):
                wrote_forward_fill = write_snapshot(
                    cur,
                    PortfolioSnapshot(
                        date=cur,
                        total_market_value=last_trading_mv,
                        total_cost=last_trading_cost,
                        total_unrealized_pnl=last_trading_mv - last_trading_cost,
                        total_dividends=cumulative_dividends,
                        total_realized_pnl=cumulative_realized,
                        total_cash_twd=total_cash_twd(cur),
                        portfolio_xirr=None,
                    ),
                )
            if not wrote_forward_fill and cur in cash_activity_dates:
                cash_total = total_cash_twd(cur)
                if cash_total != 0:
                    wrote_forward_fill = write_snapshot(
                        cur,
                        PortfolioSnapshot(
                            date=cur,
                            total_market_value=Decimal("0"),
                            total_cost=Decimal("0"),
                            total_unrealized_pnl=Decimal("0"),
                            total_dividends=cumulative_dividends,
                            total_realized_pnl=cumulative_realized,
                            total_cash_twd=cash_total,
                            portfolio_xirr=None,
                        ),
                    )
            if not wrote_forward_fill:
                stale_candidates.append(cur)
            cur += timedelta(days=1)
            continue

        mv = Decimal("0")
        for (sym, market), q in qty.items():
            state_key = (sym, market)
            if q <= 0 or signed_net.get(state_key, 0) <= 0:
                continue
            price_row = price_map.get((sym, market, cur))
            if price_row is None:
                # Foreign market closed on a TW-open day (e.g. US Memorial
                # Day, UK bank holidays): the symbol's own market has no
                # rows for ``cur`` at all. Fall back to the last known
                # close so the holding still revalues. A TW symbol with
                # no row on an otherwise-active TW day is a true data
                # gap (e.g. partial fetch) and contributes 0 as before.
                market_closed = cur not in trading_dates_by_market.get(market, set())
                cached = last_close_by_key.get((sym, market)) if market_closed else None
                if cached is None:
                    key = (sym, market, cur)
                    if key not in warned_missing:
                        warned_missing.add(key)
                        logger.warning(
                            "networth_backfill.replay.missing_price",
                            extra={
                                "symbol": sym,
                                "market": market,
                                "date": cur.isoformat(),
                            },
                        )
                    continue
                close, currency = cached
            else:
                close, currency = price_row
                last_close_by_key[(sym, market)] = (close, currency)
            if currency in (None, "TWD"):
                price_fx = Decimal("1")
            else:
                rate = fx_rate_service.get_rate(db, currency, as_of=cur)
                if rate is None:
                    key = (sym, market, cur)
                    if key not in warned_missing_fx:
                        warned_missing_fx.add(key)
                        logger.warning(
                            "networth_backfill.replay.missing_fx_rate",
                            extra={
                                "symbol": sym,
                                "market": market,
                                "currency": currency,
                                "date": cur.isoformat(),
                            },
                        )
                    continue
                price_fx = Decimal(rate)
            mv += Decimal(q) * close * price_fx

        total_cost = sum(
            (
                c
                for s, c in cost.items()
                if qty[s] > 0 and signed_net.get(s, 0) > 0
            ),
            Decimal("0"),
        )

        # Data-gap guard: every held symbol's price lookup missed on a
        # date that price_history nominally covers (trading_dates check
        # passed because *some* row exists, but none for our holdings).
        # Writing mv=0,cost>0 would pollute `last_trading_mv` and
        # propagate the zero through every subsequent forward-fill.
        # Treat as a stale candidate so the bulk DELETE removes any
        # pre-existing bad row and the chart bridges the gap via
        # interpolation against the prior good snapshot.
        if mv == 0 and total_cost > 0:
            stale_candidates.append(cur)
            cur += timedelta(days=1)
            continue

        # Cash-only on a trading day (no held stock at all) — only emit
        # when the date has actual activity (stock txn / dividend /
        # cash). Otherwise we'd bloat the snapshot table by one row per
        # trading day where price_history happens to cover the symbol.
        # Mirrors the gate in the would_skip branch above. Close-out
        # SELL and dividend dates remain captured because they're in
        # stock_activity_dates.
        if (
            mv == 0
            and total_cost == 0
            and cur not in cash_activity_dates
            and cur not in stock_activity_dates
        ):
            # Pre-existing all-zero rows on a now-gated date must be
            # cleaned up by the end-of-run DELETE, otherwise a prior
            # backfill's phantom rows survive --rebuild-all.
            gated_phantom_candidates.append(cur)
            cur += timedelta(days=1)
            continue

        wrote_trading_day = write_snapshot(
            cur,
            PortfolioSnapshot(
                date=cur,
                total_market_value=mv,
                total_cost=total_cost,
                total_unrealized_pnl=mv - total_cost,
                total_dividends=cumulative_dividends,
                total_realized_pnl=cumulative_realized,
                total_cash_twd=total_cash_twd(cur),
                portfolio_xirr=None,
            ),
        )
        if wrote_trading_day:
            result.dates_processed += 1
            last_trading_mv = mv
            last_trading_cost = total_cost

        cur += timedelta(days=1)

    if stale_candidates and not dry_run:
        delete_result = db.execute(
            delete(PortfolioSnapshot).where(
                PortfolioSnapshot.date.in_(stale_candidates),
                PortfolioSnapshot.total_market_value == 0,
                PortfolioSnapshot.total_cost > 0,
            )
        )
        result.stale_rows_deleted += max(delete_result.rowcount or 0, 0)

    if gated_phantom_candidates and not dry_run:
        phantom_result = db.execute(
            delete(PortfolioSnapshot).where(
                PortfolioSnapshot.date.in_(gated_phantom_candidates),
                PortfolioSnapshot.total_market_value == 0,
                PortfolioSnapshot.total_cost == 0,
                PortfolioSnapshot.total_cash_twd == 0,
                PortfolioSnapshot.total_dividends == 0,
                PortfolioSnapshot.total_realized_pnl == 0,
                PortfolioSnapshot.portfolio_xirr.is_(None),
            )
        )
        result.stale_rows_deleted += max(phantom_result.rowcount or 0, 0)

    if dry_run:
        return result

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "networth_backfill.replay.commit_failed",
            extra={"error": str(exc)},
        )
        result.errors.append(BackfillError(date=to_d, reason=f"commit: {exc}"))

    return result


# ---------- Combined dispatcher ----------


@dataclass
class NetworthBackfillResult:
    dates_processed: int = 0
    dates_skipped: int = 0
    dates_inactive: int = 0
    snapshots_written: int = 0
    stale_rows_deleted: int = 0
    rows_written: int = 0
    errors: List[BackfillError] = field(default_factory=list)


def run_backfill(
    db: Session,
    from_d: dt_date,
    to_d: dt_date,
    *,
    phase: str = "both",
    throttle_sec: float = DEFAULT_THROTTLE_SEC,
    active_dates: Optional[set[dt_date]] = None,
) -> NetworthBackfillResult:
    """Dispatch on ``phase`` ∈ {prices, snapshots, both}."""
    combined = NetworthBackfillResult()
    phase = phase.lower()
    if phase not in {"prices", "snapshots", "both"}:
        raise ValueError(f"unsupported phase: {phase}")

    if phase in {"prices", "both"}:
        pres = backfill_prices_range(
            db,
            from_d,
            to_d,
            throttle_sec=throttle_sec,
            active_dates=active_dates,
        )
        combined.dates_processed += pres.dates_processed
        combined.dates_skipped += pres.dates_skipped
        combined.dates_inactive = max(combined.dates_inactive, pres.dates_inactive)
        combined.rows_written += pres.rows_written
        combined.errors.extend(pres.errors)

    if phase in {"snapshots", "both"}:
        sres = replay_snapshots_range(
            db,
            from_d,
            to_d,
            active_dates=active_dates,
        )
        combined.snapshots_written += sres.snapshots_written
        combined.stale_rows_deleted += sres.stale_rows_deleted
        combined.dates_inactive = max(combined.dates_inactive, sres.dates_inactive)
        if phase == "snapshots":
            combined.dates_processed += sres.dates_processed
        combined.errors.extend(sres.errors)

    return combined


def _date_of(value: object) -> dt_date:
    return value.date() if hasattr(value, "date") else value  # type: ignore[return-value]


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild portfolio_snapshot rows from transaction history."
    )
    parser.add_argument(
        "--rebuild-all",
        action="store_true",
        help="replay snapshots from earliest transaction date through today",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print per-date realized-PnL diffs without writing rows",
    )
    args = parser.parse_args(argv)

    if not args.rebuild_all:
        parser.print_help(sys.stderr)
        return 2

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        transactions = db.query(portfolio_models.Transaction).all()
        earliest_stock_date = (
            min(_date_of(t.trade_date) for t in transactions)
            if transactions
            else None
        )
        earliest_cash_date = db.query(func.min(CashTransaction.txn_date)).scalar()
        # Include accounts initialized with a non-zero opening_balance even
        # when they have no cash_transaction rows yet — their cash history
        # starts at opening_date, not at the first explicit deposit.
        earliest_opening_date = (
            db.query(func.min(BrokerAccount.opening_date))
            .filter(BrokerAccount.opening_balance != 0)
            .scalar()
        )
        candidates = [
            d
            for d in (earliest_stock_date, earliest_cash_date, earliest_opening_date)
            if d is not None
        ]
        if not candidates:
            print("No transactions found; nothing to rebuild.")
            return 0

        from_d = min(candidates)
        to_d = dt_date.today()
        result = replay_snapshots_range(db, from_d, to_d, dry_run=args.dry_run)
        if result.errors:
            for error in result.errors:
                print(error, file=sys.stderr)
            return 1
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main())
