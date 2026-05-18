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
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date as dt_date, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from sqlalchemy import case, delete, literal, select, union_all
from sqlalchemy.orm import Session

from ..models import portfolio as portfolio_models
from ..models.portfolio_snapshot import PortfolioSnapshot
from ..models.price_history import PriceHistory
from . import market_data_service

logger = logging.getLogger(__name__)

DEFAULT_THROTTLE_SEC = 1.5
RETRY_DELAYS_SEC = (2.0, 5.0)


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
    running_qty = 0
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
            running_qty = 0
            open_date = None

        event_date = event_at.date() if hasattr(event_at, "date") else event_at
        previous_qty = running_qty
        running_qty += int(delta or 0)
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
) -> Dict[tuple[str, dt_date], Decimal]:
    """Pull all close prices in range as ``{(symbol, date): close}``."""
    rows = (
        db.query(PriceHistory.symbol, PriceHistory.date, PriceHistory.close)
        .filter(PriceHistory.date >= from_d, PriceHistory.date <= to_d)
        .all()
    )
    return {(sym, d): close for sym, d, close in rows}


def replay_snapshots_range(
    db: Session,
    from_d: dt_date,
    to_d: dt_date,
    *,
    active_dates: Optional[set[dt_date]] = None,
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

    transactions = (
        db.query(portfolio_models.Transaction)
        .order_by(
            portfolio_models.Transaction.trade_date,
            portfolio_models.Transaction.id,
        )
        .all()
    )
    # Within the same trade_date, force BUYs to be processed before SELLs.
    # CSV imports (especially day-trades) can land with SELL having a smaller
    # id than its matching BUY; without this re-order the SELL hits qty=0 and
    # is silently dropped, leaving phantom holdings that inflate MV and cost.
    transactions.sort(
        key=lambda t: (
            _trade_date_of(t),
            0 if t.type == portfolio_models.TransactionType.BUY else 1,
            t.id,
        )
    )
    dividends = (
        db.query(portfolio_models.Dividend)
        .order_by(portfolio_models.Dividend.ex_dividend_date)
        .all()
    )
    price_map = _load_price_map(db, from_d, to_d)
    trading_dates = {d for (_s, d) in price_map.keys()}
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

    qty: Dict[str, int] = defaultdict(int)
    cost: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    # Signed running BUY-SELL per symbol (no clamp). Matches the
    # portfolio_service active-holdings convention: if net <= 0 at a
    # given date, treat the symbol as fully exited so a dropped SELL
    # (qty=0 at the time) doesn't leave phantom holdings behind once
    # later BUY+SELL pairs cancel out the deficit.
    signed_net: Dict[str, int] = defaultdict(int)
    cumulative_dividends = Decimal("0")
    cumulative_realized = Decimal("0")
    warned_missing: set[tuple[str, dt_date]] = set()
    stale_candidates: list[dt_date] = []

    def write_snapshot(snapshot_date: dt_date, row: PortfolioSnapshot) -> bool:
        """Merge one snapshot inside its own SAVEPOINT."""
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

    tx_i = 0
    div_i = 0
    cur = from_d
    while cur <= to_d:
        # Advance transactions up to and including ``cur``.
        while tx_i < len(transactions) and _trade_date_of(transactions[tx_i]) <= cur:
            t = transactions[tx_i]
            sym = t.symbol
            tx_qty = int(t.quantity)
            tx_price = Decimal(t.price)
            tx_fee = Decimal(t.fee or 0)
            tx_tax = Decimal(t.tax or 0)
            if t.type == portfolio_models.TransactionType.BUY:
                qty[sym] += tx_qty
                cost[sym] += Decimal(tx_qty) * tx_price + tx_fee
                signed_net[sym] += tx_qty
            else:  # SELL
                signed_net[sym] -= tx_qty
                if qty[sym] > 0:
                    avg = cost[sym] / Decimal(qty[sym])
                    sold = min(tx_qty, qty[sym])
                    proceeds = Decimal(sold) * tx_price - tx_fee - tx_tax
                    cost_out = Decimal(sold) * avg
                    cumulative_realized += proceeds - cost_out
                    qty[sym] -= tx_qty
                    cost[sym] -= cost_out
                    if qty[sym] <= 0:
                        qty[sym] = 0
                        cost[sym] = Decimal("0")
            tx_i += 1

        # Advance dividends up to and including ``cur``.
        while div_i < len(dividends) and _ex_date_of(dividends[div_i]) <= cur:
            cumulative_dividends += Decimal(dividends[div_i].amount)
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
                        portfolio_xirr=None,
                    ),
                )
            if not wrote_forward_fill:
                stale_candidates.append(cur)
            cur += timedelta(days=1)
            continue

        mv = Decimal("0")
        for sym, q in qty.items():
            if q <= 0 or signed_net.get(sym, 0) <= 0:
                continue
            close = price_map.get((sym, cur))
            if close is None:
                key = (sym, cur)
                if key not in warned_missing:
                    warned_missing.add(key)
                    logger.warning(
                        "networth_backfill.replay.missing_price",
                        extra={"symbol": sym, "date": cur.isoformat()},
                    )
                continue
            mv += Decimal(q) * Decimal(close)

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

        wrote_trading_day = write_snapshot(
            cur,
            PortfolioSnapshot(
                date=cur,
                total_market_value=mv,
                total_cost=total_cost,
                total_unrealized_pnl=mv - total_cost,
                total_dividends=cumulative_dividends,
                total_realized_pnl=cumulative_realized,
                portfolio_xirr=None,
            ),
        )
        if wrote_trading_day:
            result.dates_processed += 1
            last_trading_mv = mv
            last_trading_cost = total_cost

        cur += timedelta(days=1)

    if stale_candidates:
        delete_result = db.execute(
            delete(PortfolioSnapshot).where(
                PortfolioSnapshot.date.in_(stale_candidates),
                PortfolioSnapshot.total_market_value == 0,
                PortfolioSnapshot.total_cost > 0,
            )
        )
        result.stale_rows_deleted = max(delete_result.rowcount or 0, 0)

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
