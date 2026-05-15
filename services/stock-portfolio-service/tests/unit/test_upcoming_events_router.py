"""Router tests for GET /api/portfolio/upcoming-events."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.corporate_action import CorporateAction
from app.models.portfolio import Transaction, TransactionType
from app.services.dividend_sources import DividendEventRow

TW = timezone(timedelta(hours=8))


def _seed_buy(db, symbol):
    db.add(
        Transaction(
            symbol=symbol,
            type=TransactionType.BUY,
            quantity=1000,
            price=Decimal("100"),
            trade_date=datetime(2024, 1, 1, tzinfo=TW),
            fee=Decimal("0"),
            tax=Decimal("0"),
            is_day_trade=False,
        )
    )
    db.commit()


def _seed_face_value(db, symbol, effective):
    db.add(
        CorporateAction(
            symbol=symbol,
            effective_date=effective,
            action_type="FACE_VALUE_CHANGE",
            ratio=Decimal("0.5"),
            source="TWSE",
            source_event_key=f"{symbol}_{effective.isoformat()}",
        )
    )
    db.commit()


def test_upcoming_events_merges_dividends_and_face_value(client, db_session):
    _seed_buy(db_session, "2330")
    _seed_buy(db_session, "0050")
    _seed_face_value(db_session, "2330", date(2027, 1, 1))

    future_div = DividendEventRow(
        symbol="0050",
        ex_dividend_date=date(2026, 12, 1),
        cash_dividend=Decimal("3.5"),
        stock_dividend=None,
        source="TWSE_TWT48U",
    )
    with patch(
        "app.routers.upcoming_events.dividend_event_service.fetch_upcoming_for_holdings",
        return_value=[future_div],
    ):
        resp = client.get("/api/portfolio/upcoming-events?from=2026-05-15")
    assert resp.status_code == 200
    body = resp.json()
    # Ascending by date
    dates = [r["date"] for r in body]
    assert dates == sorted(dates)
    types = {r["type"] for r in body}
    assert "CASH_DIV" in types
    assert "FACE_VALUE" in types


def test_past_events_excluded(client, db_session):
    _seed_buy(db_session, "2330")
    _seed_face_value(db_session, "2330", date(2020, 1, 1))  # before from
    with patch(
        "app.routers.upcoming_events.dividend_event_service.fetch_upcoming_for_holdings",
        return_value=[],
    ):
        resp = client.get("/api/portfolio/upcoming-events?from=2026-05-15")
    assert resp.status_code == 200
    assert resp.json() == []


def test_type_both_when_cash_and_stock_present(client, db_session):
    _seed_buy(db_session, "0050")
    row = DividendEventRow(
        symbol="0050",
        ex_dividend_date=date(2026, 12, 1),
        cash_dividend=Decimal("3.5"),
        stock_dividend=Decimal("0.1"),
        source="TWSE_TWT48U",
    )
    with patch(
        "app.routers.upcoming_events.dividend_event_service.fetch_upcoming_for_holdings",
        return_value=[row],
    ):
        resp = client.get("/api/portfolio/upcoming-events?from=2026-05-15")
    body = resp.json()
    assert len(body) == 1
    assert body[0]["type"] == "BOTH"
