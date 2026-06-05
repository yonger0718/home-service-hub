from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models import portfolio as models
from app.models.corporate_action import CorporateAction
from app.services import scheduler as sched
from app.services.quotes.fx_rate_service import RefreshResult


def _job_ids(monkeypatch, enabled: str) -> set[str]:
    monkeypatch.setenv("SCHEDULER_ENABLED", enabled)
    scheduler = sched.build_scheduler(MagicMock())
    return {job.id for job in scheduler.get_jobs()}


def test_foreign_jobs_registered_when_scheduler_enabled(monkeypatch) -> None:
    assert {"fx_rate_refresh", "foreign_price_refresh"} <= _job_ids(monkeypatch, "true")


def test_foreign_jobs_absent_when_scheduler_disabled(monkeypatch) -> None:
    assert "fx_rate_refresh" not in _job_ids(monkeypatch, "false")
    assert "foreign_price_refresh" not in _job_ids(monkeypatch, "false")


def test_foreign_price_refresh_selects_only_open_non_tw_positions(db_session) -> None:
    db_session.add_all(
        [
            models.Transaction(
                symbol="AAPL",
                market="US",
                type=models.TransactionType.BUY,
                quantity=Decimal("1.5"),
                price=Decimal("100"),
                currency="USD",
                fx_rate_to_twd=Decimal("32"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            ),
            models.Transaction(
                symbol="MSFT",
                market="US",
                type=models.TransactionType.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                currency="USD",
                fx_rate_to_twd=Decimal("32"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            ),
            models.Transaction(
                symbol="MSFT",
                market="US",
                type=models.TransactionType.SELL,
                quantity=Decimal("1"),
                price=Decimal("110"),
                currency="USD",
                fx_rate_to_twd=Decimal("32"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 2),
            ),
            models.Transaction(
                symbol="2330",
                market="TW",
                type=models.TransactionType.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            ),
        ]
    )
    db_session.commit()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db_session
    factory.return_value.__exit__.return_value = False

    with patch.object(
        sched.quote_dispatcher,
        "refresh_daily_ohlc",
        return_value=RefreshResult(ok_count=1, skipped_count=0, errors=[]),
    ) as refresh:
        result = sched.run_foreign_price_refresh(factory)

    refresh.assert_called_once_with(db_session, [("AAPL", "US")])
    assert result["requested"] == 1


def test_open_foreign_positions_respects_corporate_action_adjusted_quantity(db_session) -> None:
    db_session.add_all(
        [
            models.Transaction(
                symbol="AAPL",
                market="US",
                type=models.TransactionType.BUY,
                quantity=Decimal("100"),
                price=Decimal("100"),
                currency="USD",
                fx_rate_to_twd=Decimal("32"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 1, 1),
            ),
            models.Transaction(
                symbol="AAPL",
                market="US",
                type=models.TransactionType.SELL,
                quantity=Decimal("150"),
                price=Decimal("60"),
                currency="USD",
                fx_rate_to_twd=Decimal("32"),
                fee=Decimal("0"),
                tax=Decimal("0"),
                trade_date=datetime(2026, 2, 1),
            ),
            CorporateAction(
                symbol="AAPL",
                market="US",
                effective_date=date(2026, 1, 15),
                action_type="SPLIT",
                ratio=Decimal("2"),
                source="test",
                source_event_key="AAPL-US-2026-01-15",
            ),
        ]
    )
    db_session.commit()

    assert sched._open_foreign_positions(db_session) == [("AAPL", "US")]


def test_foreign_price_refresh_empty_ledger_short_circuits(db_session) -> None:
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db_session
    factory.return_value.__exit__.return_value = False

    with patch.object(sched.quote_dispatcher, "refresh_daily_ohlc") as refresh:
        result = sched.run_foreign_price_refresh(factory)

    refresh.assert_not_called()
    assert result["ok_count"] == 0
    assert result["skipped_count"] == 0


def test_fx_rate_refresh_failure_logs_and_does_not_raise() -> None:
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    factory.return_value.__exit__.return_value = False

    with patch.object(sched.quote_fx_rate_service, "refresh_today", side_effect=RuntimeError("down")):
        result = sched.run_fx_rate_refresh(factory)

    assert result["status"] == "failed"
    assert "down" in result["error"]
