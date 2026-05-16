"""APScheduler scaffold for stock-portfolio-service.

Runs three cron jobs in a single in-process ``BackgroundScheduler`` tied
to ``Asia/Taipei``:

- ``tw_daily_prices`` — 17:00 Mon-Fri — backfills TWSE+TPEx EoD into
  ``price_history``.
- ``quote_refresh`` — every 15 min between 09:00 and 13:30 Mon-Fri —
  pre-warms TWSE quote cache for active-holding symbols. Gated by
  :func:`is_tw_market_session` so the 13:00-13:30 leg only fires inside
  the session.
- ``portfolio_snapshot`` — 15:30 daily — stub placeholder until the next
  change wires the snapshot table.

The scheduler is started from the FastAPI ``startup`` event when
``SCHEDULER_ENABLED`` is truthy (default ``true``) and stopped on
``shutdown``. Tests set ``SCHEDULER_ENABLED=false`` so the scheduler
never boots inside the test process.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timezone, timedelta
from typing import Callable, ContextManager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from datetime import timedelta as _timedelta

from . import (
    dividend_auto_record_service,
    dividend_event_service,
    market_data_service,
    portfolio_service,
    portfolio_snapshot_service,
    symbol_map_service,
    twse_service,
)
from .dividend_history_service import HistoricalDividendEvent

logger = logging.getLogger(__name__)

TW_TIMEZONE = "Asia/Taipei"
_TW_OFFSET = timezone(timedelta(hours=8))
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)


def is_tw_market_session(now: datetime) -> bool:
    """Return true on a TW weekday inside ``09:00 <= time < 13:30``.

    No holiday calendar — Mon-Fri + time-of-day window only.
    """
    if now.weekday() >= 5:
        return False
    current = now.time()
    return SESSION_OPEN <= current < SESSION_CLOSE


def _now_tw() -> datetime:
    return datetime.now(_TW_OFFSET)


def _today_tw() -> date:
    return _now_tw().date()


def run_tw_daily_prices(session_factory: Callable[[], ContextManager]) -> dict:
    """Backfill TWSE+TPEx EoD for today (TW)."""
    today = _today_tw()
    with session_factory() as db:
        result = market_data_service.backfill_date(db, today, market="BOTH")
    logger.info(
        "scheduler.tw_daily_prices.done",
        extra={
            "date": result["date"],
            "twse_rows": result["twse_rows"],
            "tpex_rows": result["tpex_rows"],
            "written": result["written"],
        },
    )
    return result


def run_quote_refresh(session_factory: Callable[[], ContextManager]) -> dict:
    """Pre-warm TWSE quote cache for active-holding symbols."""
    if not is_tw_market_session(_now_tw()):
        logger.debug("scheduler.quote_refresh.skipped_outside_session")
        return {"skipped": True, "reason": "outside_session"}
    with session_factory() as db:
        symbols = sorted(portfolio_service.get_active_holdings(db).keys())
    if not symbols:
        logger.debug("scheduler.quote_refresh.no_active_symbols")
        return {"skipped": True, "reason": "no_active_symbols"}
    quotes = twse_service.get_stock_quotes(symbols)
    logger.info(
        "scheduler.quote_refresh.done",
        extra={"requested": len(symbols), "received": len(quotes)},
    )
    return {"requested": len(symbols), "received": len(quotes)}


def run_portfolio_snapshot(session_factory: Callable[[], ContextManager]) -> dict:
    """Persist today's PortfolioSummary into ``portfolio_snapshot``.

    Swallows any exception raised by the underlying service so the
    scheduler thread keeps running; a missed day is preferable to a
    dead cron.
    """
    try:
        with session_factory() as db:
            snapshot = portfolio_snapshot_service.write_today_snapshot(db)
    except Exception as exc:  # noqa: BLE001 — scheduler must not die
        logger.exception("scheduler.portfolio_snapshot.failed", extra={"error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    logger.info(
        "scheduler.portfolio_snapshot.done",
        extra={
            "date": snapshot.date.isoformat(),
            "total_market_value": str(snapshot.total_market_value),
            "total_cost": str(snapshot.total_cost),
        },
    )
    return {"status": "ok", "date": snapshot.date.isoformat()}


def _event_row_to_historical(row, source: str) -> HistoricalDividendEvent:
    """Bridge DividendEventRow → HistoricalDividendEvent for the recorder."""
    return HistoricalDividendEvent(
        symbol=row.symbol,
        ex_date=row.ex_dividend_date,
        cash_dividend_per_share=row.cash_dividend,
        stock_dividend_per_thousand=(
            (row.stock_dividend * 1000) if row.stock_dividend is not None else None
        ),
        previous_close=None,
        reference_price=None,
        source=source,
    )


def run_dividend_auto_record(session_factory: Callable[[], ContextManager]) -> dict:
    """Record dividend events whose ex-date falls in the last 7 days.

    Pulls the merged upcoming-events feed for currently-held symbols,
    filters to events with `ex_date in [today-7, today]`, and feeds
    each to ``auto_record_for_event``. Exceptions are swallowed so the
    cron thread keeps running.
    """
    try:
        today = _today_tw()
        window_start = today - _timedelta(days=7)
        with session_factory() as db:
            holdings = portfolio_service.get_active_holdings(db)
            held_symbols = set(holdings.keys())
            name_for = {
                sym: (info.get("name") if isinstance(info, dict) else None)
                for sym, info in holdings.items()
            }
            events = dividend_event_service.fetch_upcoming_for_holdings(
                held_symbols, from_date=window_start
            )
            cash_inserted = 0
            stock_inserted = 0
            events_processed = 0
            for row in events:
                if row.ex_dividend_date > today:
                    continue
                historical = _event_row_to_historical(row, row.source)
                events_processed += 1
                result = dividend_auto_record_service.auto_record_for_event(
                    db, historical, name=name_for.get(row.symbol)
                )
                if result.cash_inserted:
                    cash_inserted += 1
                if result.stock_inserted:
                    stock_inserted += 1
            db.commit()
    except Exception as exc:  # noqa: BLE001 — scheduler must not die
        logger.exception(
            "scheduler.dividend_auto_record.failed", extra={"error": str(exc)}
        )
        return {"status": "failed", "error": str(exc)}
    logger.info(
        "scheduler.dividend_auto_record.done",
        extra={
            "events_processed": events_processed,
            "cash_inserted": cash_inserted,
            "stock_inserted": stock_inserted,
        },
    )
    return {
        "status": "ok",
        "events_processed": events_processed,
        "cash_inserted": cash_inserted,
        "stock_inserted": stock_inserted,
    }


def run_symbol_map_refresh(session_factory: Callable[[], ContextManager]) -> dict:
    """Refresh symbol_map from twstock; swallow upstream errors so the cron keeps running."""
    try:
        with session_factory() as db:
            result = symbol_map_service.refresh_all_from_twstock(db)
    except Exception as exc:  # noqa: BLE001 — scheduler must not die
        logger.exception("scheduler.symbol_map_refresh.failed", extra={"error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    logger.info("scheduler.symbol_map_refresh.done", extra={"count": result["refreshed_count"]})
    return {"status": "ok", **result}


def build_scheduler(session_factory: Callable[[], ContextManager]) -> BackgroundScheduler:
    """Construct a configured ``BackgroundScheduler``; caller starts it."""
    scheduler = BackgroundScheduler(timezone=TW_TIMEZONE)
    scheduler.add_job(
        run_tw_daily_prices,
        CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone=TW_TIMEZONE),
        id="tw_daily_prices",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        run_quote_refresh,
        CronTrigger(minute="*/15", hour="9-13", day_of_week="mon-fri", timezone=TW_TIMEZONE),
        id="quote_refresh",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        run_portfolio_snapshot,
        CronTrigger(hour=15, minute=30, timezone=TW_TIMEZONE),
        id="portfolio_snapshot",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        run_symbol_map_refresh,
        CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=TW_TIMEZONE),
        id="symbol_map_refresh",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    scheduler.add_job(
        run_dividend_auto_record,
        CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone=TW_TIMEZONE),
        id="dividend_auto_record",
        kwargs={"session_factory": session_factory},
        replace_existing=True,
    )
    return scheduler


def is_enabled() -> bool:
    return os.getenv("SCHEDULER_ENABLED", "true").lower() not in {"false", "0", "no"}
