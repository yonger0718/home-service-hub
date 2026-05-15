"""Scheduler boot, gate logic, and job callable wiring."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services import scheduler as sched

TW = timezone(timedelta(hours=8))


# ---------------- market-hours gate ----------------

@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 5, 14, 10, 0, tzinfo=TW), True),    # Thu 10:00 in
        (datetime(2026, 5, 14, 9, 0, tzinfo=TW), True),     # open boundary inclusive
        (datetime(2026, 5, 14, 13, 29, tzinfo=TW), True),   # last minute in
        (datetime(2026, 5, 14, 13, 30, tzinfo=TW), False),  # close boundary exclusive
        (datetime(2026, 5, 14, 8, 59, tzinfo=TW), False),   # pre-open
        (datetime(2026, 5, 14, 14, 0, tzinfo=TW), False),   # post-close
        (datetime(2026, 5, 16, 10, 0, tzinfo=TW), False),   # Saturday
        (datetime(2026, 5, 17, 10, 0, tzinfo=TW), False),   # Sunday
    ],
)
def test_is_tw_market_session(now, expected):
    assert sched.is_tw_market_session(now) is expected


# ---------------- scheduler construction ----------------

def test_build_scheduler_registers_three_jobs():
    factory = MagicMock()
    scheduler = sched.build_scheduler(factory)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "tw_daily_prices",
        "quote_refresh",
        "portfolio_snapshot",
        "symbol_map_refresh",
        "dividend_auto_record",
    }


def test_dividend_auto_record_uses_18_00_weekdays():
    factory = MagicMock()
    scheduler = sched.build_scheduler(factory)
    job = scheduler.get_job("dividend_auto_record")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "18"
    assert fields["minute"] == "0"
    assert fields["day_of_week"] == "mon-fri"


def test_scheduler_jobs_use_tw_timezone():
    factory = MagicMock()
    scheduler = sched.build_scheduler(factory)
    for job in scheduler.get_jobs():
        assert str(job.trigger.timezone) == "Asia/Taipei"


def test_scheduler_cron_fields_match_spec():
    factory = MagicMock()
    scheduler = sched.build_scheduler(factory)
    by_id = {job.id: job for job in scheduler.get_jobs()}
    daily = {f.name: str(f) for f in by_id["tw_daily_prices"].trigger.fields}
    assert daily["hour"] == "17" and daily["minute"] == "0"
    assert daily["day_of_week"] == "mon-fri"
    quote = {f.name: str(f) for f in by_id["quote_refresh"].trigger.fields}
    assert quote["minute"] == "*/15"
    assert quote["hour"] == "9-13"
    assert quote["day_of_week"] == "mon-fri"
    snap = {f.name: str(f) for f in by_id["portfolio_snapshot"].trigger.fields}
    assert snap["hour"] == "15" and snap["minute"] == "30"


# ---------------- job callables ----------------

def test_run_tw_daily_prices_calls_backfill_with_both():
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False
    with patch.object(sched.market_data_service, "backfill_date") as backfill:
        backfill.return_value = {"date": "2026-05-14", "twse_rows": 1, "tpex_rows": 2, "written": 3, "market": "BOTH"}
        result = sched.run_tw_daily_prices(factory)
    backfill.assert_called_once()
    args, kwargs = backfill.call_args
    assert args[0] is db
    assert kwargs["market"] == "BOTH"
    assert result["written"] == 3


def test_run_quote_refresh_skips_outside_session():
    factory = MagicMock()
    with patch.object(sched, "_now_tw", return_value=datetime(2026, 5, 14, 14, 0, tzinfo=TW)):
        with patch.object(sched.twse_service, "get_stock_quotes") as quotes:
            result = sched.run_quote_refresh(factory)
    quotes.assert_not_called()
    assert result == {"skipped": True, "reason": "outside_session"}


def test_run_quote_refresh_skips_when_no_active_symbols():
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False
    with patch.object(sched, "_now_tw", return_value=datetime(2026, 5, 14, 10, 0, tzinfo=TW)), \
         patch.object(sched.portfolio_service, "get_active_holdings", return_value={}), \
         patch.object(sched.twse_service, "get_stock_quotes") as quotes:
        result = sched.run_quote_refresh(factory)
    quotes.assert_not_called()
    assert result == {"skipped": True, "reason": "no_active_symbols"}


def test_run_quote_refresh_fetches_when_in_session():
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False
    holdings = {"2330": {}, "0050": {}}
    with patch.object(sched, "_now_tw", return_value=datetime(2026, 5, 14, 10, 0, tzinfo=TW)), \
         patch.object(sched.portfolio_service, "get_active_holdings", return_value=holdings), \
         patch.object(sched.twse_service, "get_stock_quotes", return_value={"2330": {}, "0050": {}}) as quotes:
        result = sched.run_quote_refresh(factory)
    quotes.assert_called_once_with(["0050", "2330"])
    assert result == {"requested": 2, "received": 2}


def test_run_portfolio_snapshot_persists_and_returns_ok():
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False
    snapshot = MagicMock(date=type("D", (), {"isoformat": lambda self: "2026-05-15"})(),
                         total_market_value="100", total_cost="80")
    with patch.object(sched.portfolio_snapshot_service, "write_today_snapshot",
                      return_value=snapshot) as writer:
        result = sched.run_portfolio_snapshot(factory)
    writer.assert_called_once_with(db)
    assert result == {"status": "ok", "date": "2026-05-15"}


def test_run_portfolio_snapshot_swallows_exceptions():
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False
    with patch.object(sched.portfolio_snapshot_service, "write_today_snapshot",
                      side_effect=RuntimeError("twse down")):
        result = sched.run_portfolio_snapshot(factory)
    assert result["status"] == "failed"
    assert "twse down" in result["error"]


# ---------------- env gate ----------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        (None, True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
    ],
)
def test_is_enabled_env_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCHEDULER_ENABLED", value)
    assert sched.is_enabled() is expected
