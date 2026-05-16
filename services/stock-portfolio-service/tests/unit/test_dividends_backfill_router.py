"""Router tests for POST /api/portfolio/dividends/backfill."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio import Dividend, Transaction, TransactionType
from app.services.dividend_history_service import HistoricalDividendEvent

TW = timezone(timedelta(hours=8))


def _seed_buy(db, symbol="2330", qty=1000, trade_date=date(2024, 1, 5)):
    db.add(
        Transaction(
            symbol=symbol,
            type=TransactionType.BUY,
            quantity=qty,
            price=Decimal("500.00"),
            trade_date=datetime.combine(trade_date, datetime.min.time(), tzinfo=TW),
            fee=Decimal("0"),
            tax=Decimal("0"),
            is_day_trade=False,
        )
    )
    db.commit()


def test_backfill_aggregates_counts_and_inserts(client, db_session):
    _seed_buy(db_session, symbol="2330")
    events = [
        HistoricalDividendEvent(
            symbol="2330",
            ex_date=date(2024, 6, 15),
            cash_dividend_per_share=Decimal("3.0"),
            stock_dividend_per_thousand=None,
            previous_close=None,
            reference_price=None,
            source="TWT49U",
        )
    ]
    with patch("app.routers.dividends_backfill.dividend_history_service.fetch_for_symbol_all_years", return_value=events):
        resp = client.post("/api/portfolio/dividends/backfill")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols_scanned"] == 1
    assert body["events_seen"] == 1
    assert body["cash_inserted"] == 1
    assert body["stock_inserted"] == 0


def test_backfill_second_call_is_idempotent(client, db_session):
    _seed_buy(db_session, symbol="2330")
    events = [
        HistoricalDividendEvent(
            symbol="2330",
            ex_date=date(2024, 6, 15),
            cash_dividend_per_share=Decimal("3.0"),
            stock_dividend_per_thousand=None,
            previous_close=None,
            reference_price=None,
            source="TWT49U",
        )
    ]
    with patch("app.routers.dividends_backfill.dividend_history_service.fetch_for_symbol_all_years", return_value=events):
        client.post("/api/portfolio/dividends/backfill")
        resp2 = client.post("/api/portfolio/dividends/backfill")
    assert resp2.json()["cash_inserted"] == 0


def test_backfill_scans_fully_sold_symbols(client, db_session):
    """Symbols user has since sold off completely must still be scanned —
    they received dividends while held in the past."""
    _seed_buy(db_session, symbol="00713", qty=1000, trade_date=date(2023, 1, 5))
    db_session.add(
        Transaction(
            symbol="00713",
            type=TransactionType.SELL,
            quantity=1000,
            price=Decimal("50.00"),
            trade_date=datetime.combine(date(2024, 12, 1), datetime.min.time(), tzinfo=TW),
            fee=Decimal("0"),
            tax=Decimal("0"),
            is_day_trade=False,
        )
    )
    db_session.commit()
    events = [
        HistoricalDividendEvent(
            symbol="00713",
            ex_date=date(2023, 9, 18),
            cash_dividend_per_share=Decimal("0.84"),
            stock_dividend_per_thousand=None,
            previous_close=None,
            reference_price=None,
            source="TWT49U",
        )
    ]
    with patch("app.routers.dividends_backfill.dividend_history_service.fetch_for_symbol_all_years", return_value=events):
        resp = client.post("/api/portfolio/dividends/backfill")
    body = resp.json()
    assert body["symbols_scanned"] == 1
    assert body["cash_inserted"] == 1


def test_backfill_per_symbol_exception_isolated(client, db_session):
    _seed_buy(db_session, symbol="2330", qty=1000, trade_date=date(2024, 1, 5))
    _seed_buy(db_session, symbol="0050", qty=500, trade_date=date(2024, 1, 5))

    def _flaky(symbol, since):
        if symbol == "2330":
            raise RuntimeError("upstream blew up")
        return [
            HistoricalDividendEvent(
                symbol=symbol,
                ex_date=date(2024, 7, 1),
                cash_dividend_per_share=Decimal("2.0"),
                stock_dividend_per_thousand=None,
                previous_close=None,
                reference_price=None,
                source="TWT49U",
            )
        ]

    with patch("app.routers.dividends_backfill.dividend_history_service.fetch_for_symbol_all_years", side_effect=_flaky):
        resp = client.post("/api/portfolio/dividends/backfill")
    assert resp.status_code == 200
    assert resp.json()["cash_inserted"] == 1
