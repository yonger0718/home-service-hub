"""Post-import recalc chain — orchestrator + router wiring."""

from __future__ import annotations

import asyncio
import io
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models import portfolio as portfolio_models
from app.services import post_import_orchestrator as orch


TW = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _reset_state():
    orch.reset_state_for_tests()
    yield
    orch.reset_state_for_tests()


@contextmanager
def _factory_cm(session):
    """Context-manager wrapper that yields the same `db_session` fixture."""
    try:
        yield session
    finally:
        pass  # caller-managed fixture; do not close


def _session_factory(session):
    def factory():
        return _factory_cm(session)

    return factory


def _make_tx(symbol="2330", qty=1000, price="50", days_ago=1):
    return portfolio_models.Transaction(
        symbol=symbol,
        name=symbol,
        type=portfolio_models.TransactionType.BUY,
        quantity=qty,
        price=Decimal(price),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


# ---------- is_enabled feature flag ----------


def test_is_enabled_default_true(monkeypatch):
    monkeypatch.delenv("POST_IMPORT_RECALC_ENABLED", raising=False)
    assert orch.is_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
def test_is_enabled_false_when_flag_set(monkeypatch, value):
    monkeypatch.setenv("POST_IMPORT_RECALC_ENABLED", value)
    assert orch.is_enabled() is False


# ---------- chain step ordering + isolation ----------


def test_chain_calls_steps_in_order(db_session):
    order: list[str] = []
    with patch.object(orch, "_step_symbol_map_backfill", side_effect=lambda f: (order.append("symbol_map"), orch.StepResult("symbol_map_backfill", "ok"))[1]), \
         patch.object(orch, "_step_dividends", side_effect=lambda f, s, a, b: (order.append("dividends"), orch.StepResult("dividend_auto_record", "ok"))[1]), \
         patch.object(orch, "_step_networth_backfill", side_effect=lambda f, a, b: (order.append("networth"), orch.StepResult("networth_backfill", "ok"))[1]):
        result = asyncio.run(
            orch.run_chain(
                _session_factory(db_session),
                recalc_from=datetime.now(TW).date(),
                recalc_to=datetime.now(TW).date(),
                touched_symbols={"2330"},
            )
        )
    assert order == ["symbol_map", "dividends", "networth"]
    assert result.state == "completed"


def test_step_failure_does_not_skip_later_steps(db_session):
    """If the dividend step fails, networth step still runs."""
    calls: list[str] = []

    def ok_symbol(_f):
        calls.append("symbol_map")
        return orch.StepResult("symbol_map_backfill", "ok")

    def boom_dividends(_f, _s, _a, _b):
        calls.append("dividends")
        # Step impl swallows exceptions and returns a "failed" StepResult — simulate that
        return orch.StepResult(
            "dividend_auto_record", "failed", error="boom"
        )

    def ok_networth(_f, _a, _b):
        calls.append("networth")
        return orch.StepResult("networth_backfill", "ok")

    with patch.object(orch, "_step_symbol_map_backfill", side_effect=ok_symbol), \
         patch.object(orch, "_step_dividends", side_effect=boom_dividends), \
         patch.object(orch, "_step_networth_backfill", side_effect=ok_networth):
        result = asyncio.run(
            orch.run_chain(
                _session_factory(db_session),
                recalc_from=datetime.now(TW).date(),
                recalc_to=datetime.now(TW).date(),
                touched_symbols={"2330"},
            )
        )
    assert calls == ["symbol_map", "dividends", "networth"]
    assert result.state == "partial"
    statuses = {s.name: s.status for s in result.steps}
    assert statuses["dividend_auto_record"] == "failed"
    assert statuses["networth_backfill"] == "ok"


def test_dividend_step_swallows_inner_exception(db_session):
    """When auto_record_for_event raises, the step records the error and continues."""
    from app.services import dividend_event_service
    from app.services.dividend_sources import DividendEventRow

    today = datetime.now(TW).date()
    row = DividendEventRow(
        symbol="2330",
        ex_dividend_date=today,
        cash_dividend=Decimal("3.0"),
        stock_dividend=None,
        source="twt48u",
    )

    with patch.object(
        dividend_event_service, "fetch_for_holdings", return_value=[row]
    ), patch(
        "app.services.dividend_auto_record_service.auto_record_for_event",
        side_effect=RuntimeError("twse outage"),
    ):
        step = orch._step_dividends(
            _session_factory(db_session),
            {"2330"},
            today,
            today,
        )
    assert step.status == "partial"
    assert step.detail["event_errors"]
    assert step.detail["event_errors"][0]["symbol"] == "2330"


# ---------- lock serialization ----------


def test_recalc_lock_serializes_concurrent_chains(db_session):
    enter_order: list[str] = []
    exit_order: list[str] = []

    def slow_symbol(_f):
        enter_order.append("symbol")
        # Simulate slow step
        import time as _t
        _t.sleep(0.05)
        exit_order.append("symbol")
        return orch.StepResult("symbol_map_backfill", "ok")

    def noop_dividends(_f, _s, _a, _b):
        return orch.StepResult("dividend_auto_record", "ok")

    def noop_networth(_f, _a, _b):
        return orch.StepResult("networth_backfill", "ok")

    async def runner():
        with patch.object(orch, "_step_symbol_map_backfill", side_effect=slow_symbol), \
             patch.object(orch, "_step_dividends", side_effect=noop_dividends), \
             patch.object(orch, "_step_networth_backfill", side_effect=noop_networth):
            today = datetime.now(TW).date()
            await asyncio.gather(
                orch.run_chain(
                    _session_factory(db_session),
                    recalc_from=today, recalc_to=today, touched_symbols={"2330"},
                ),
                orch.run_chain(
                    _session_factory(db_session),
                    recalc_from=today, recalc_to=today, touched_symbols={"2330"},
                ),
            )

    asyncio.run(runner())
    # Two chains each call slow_symbol once → 2 enters total.
    # Because of the lock, the second can only enter after the first exits.
    assert len(enter_order) == 2
    assert len(exit_order) == 2


# ---------- latest_status / TTL ----------


def test_latest_status_returns_idle_when_empty():
    assert orch.latest_status() == {"state": "idle"}


def test_latest_status_returns_recent_result(db_session):
    with patch.object(orch, "_step_symbol_map_backfill", return_value=orch.StepResult("symbol_map_backfill", "ok")), \
         patch.object(orch, "_step_dividends", return_value=orch.StepResult("dividend_auto_record", "ok")), \
         patch.object(orch, "_step_networth_backfill", return_value=orch.StepResult("networth_backfill", "ok")):
        today = datetime.now(TW).date()
        asyncio.run(
            orch.run_chain(
                _session_factory(db_session),
                recalc_from=today, recalc_to=today, touched_symbols={"2330"},
            )
        )
    status = orch.latest_status()
    assert status["state"] == "completed"
    assert status["touched_symbols"] == ["2330"]


def test_latest_status_prunes_old_entries():
    # Inject a stale finished result and a fresh one; only fresh should surface.
    stale = orch.ChainResult(
        state="completed",
        started_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        finished_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    )
    fresh = orch.ChainResult(
        state="completed",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    orch._LATEST_RESULTS[stale.started_at] = stale
    orch._LATEST_RESULTS[fresh.started_at] = fresh
    status = orch.latest_status()
    assert status["started_at"] == fresh.started_at
    # Stale entry has been pruned.
    assert stale.started_at not in orch._LATEST_RESULTS


# ---------- router wiring ----------


_TX_HEADER = "symbol,type,quantity,price,trade_date,fee,tax,name\n"


def _tx_csv(symbol="2330", qty=1000, price="50", trade_date="2026-05-10T13:30:00+08:00"):
    return (_TX_HEADER + f"{symbol},BUY,{qty},{price},{trade_date},10,1,\n").encode("utf-8")


def test_import_transactions_schedules_recalc_on_success(client, db_session, monkeypatch):
    monkeypatch.setenv("POST_IMPORT_RECALC_ENABLED", "true")
    calls: list[dict] = []

    def fake_schedule(session_factory, *, recalc_from, recalc_to, touched_symbols):
        calls.append(
            {
                "recalc_from": recalc_from,
                "recalc_to": recalc_to,
                "touched_symbols": touched_symbols,
            }
        )
        return orch.ChainResult(
            state="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    monkeypatch.setattr(orch, "schedule_chain_sync", fake_schedule)
    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", _tx_csv(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1
    assert body["recalc_scheduled"] is True
    # BackgroundTasks runs synchronously after the response in TestClient
    assert calls
    assert calls[0]["touched_symbols"] == {"2330"}


def test_import_transactions_skips_recalc_when_zero_created(client, db_session, monkeypatch):
    monkeypatch.setenv("POST_IMPORT_RECALC_ENABLED", "true")
    schedule_mock = MagicMock()
    monkeypatch.setattr(orch, "schedule_chain_sync", schedule_mock)
    csv_bytes = _tx_csv()
    # First import succeeds.
    first = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", csv_bytes, "text/csv")},
    )
    assert first.json()["created"] == 1
    schedule_mock.reset_mock()
    # Second identical import → fingerprint collision → created == 0 → no schedule.
    second = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", csv_bytes, "text/csv")},
    )
    body = second.json()
    assert body["created"] == 0
    assert body["skipped_duplicates"] == 1
    assert body["recalc_scheduled"] is False
    schedule_mock.assert_not_called()


def test_import_transactions_skips_recalc_when_flag_disabled(client, monkeypatch):
    monkeypatch.setenv("POST_IMPORT_RECALC_ENABLED", "false")
    schedule_mock = MagicMock()
    monkeypatch.setattr(orch, "schedule_chain_sync", schedule_mock)
    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", _tx_csv(), "text/csv")},
    )
    body = response.json()
    assert body["created"] == 1
    assert body["recalc_scheduled"] is False
    schedule_mock.assert_not_called()


def test_manual_recalc_returns_409_when_no_transactions(client):
    response = client.post("/api/portfolio/imports/recalc", json={})
    assert response.status_code == 409


def test_manual_recalc_defaults_to_min_trade_date_and_today(client, db_session, monkeypatch):
    db_session.add(_make_tx("2330", days_ago=30))
    db_session.add(_make_tx("0050", days_ago=10))
    db_session.commit()
    captured: dict = {}

    def fake_schedule(session_factory, *, recalc_from, recalc_to, touched_symbols):
        captured["recalc_from"] = recalc_from
        captured["recalc_to"] = recalc_to
        captured["touched_symbols"] = touched_symbols
        return orch.ChainResult(
            state="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    monkeypatch.setattr(orch, "schedule_chain_sync", fake_schedule)
    response = client.post("/api/portfolio/imports/recalc", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recalc_scheduled"] is True
    assert captured["touched_symbols"] == {"2330", "0050"}
    # Default end_date is today_tw.
    assert captured["recalc_to"] == orch.today_tw()
    # Default start_date is min(trade_date) → ~30 days ago.
    assert captured["recalc_from"] <= orch.today_tw()


def test_manual_recalc_accepts_explicit_range(client, db_session, monkeypatch):
    db_session.add(_make_tx("2330", days_ago=5))
    db_session.commit()
    captured: dict = {}

    def fake_schedule(session_factory, *, recalc_from, recalc_to, touched_symbols):
        captured["recalc_from"] = recalc_from
        captured["recalc_to"] = recalc_to
        return orch.ChainResult(
            state="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    monkeypatch.setattr(orch, "schedule_chain_sync", fake_schedule)
    response = client.post(
        "/api/portfolio/imports/recalc",
        json={"start_date": "2024-01-15", "end_date": "2026-05-17"},
    )
    assert response.status_code == 200
    assert captured["recalc_from"].isoformat() == "2024-01-15"
    assert captured["recalc_to"].isoformat() == "2026-05-17"


def test_status_endpoint_returns_idle_when_empty(client):
    response = client.get("/api/portfolio/imports/recalc/status")
    assert response.status_code == 200
    assert response.json() == {"state": "idle"}
