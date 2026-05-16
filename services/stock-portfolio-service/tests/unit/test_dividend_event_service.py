"""Orchestrator: merge, dedupe, source-priority, fault-tolerant chain."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models import portfolio as models
from app.services import dividend_event_service as svc
from app.services.dividend_sources import DividendEventRow

TW = timezone(timedelta(hours=8))


def _row(symbol, ex_date, cash=None, stock=None, source="TWSE_TWT48U"):
    return DividendEventRow(
        symbol=symbol, ex_dividend_date=ex_date,
        cash_dividend=cash, stock_dividend=stock, source=source,
    )


def test_default_year_resolves_to_current_tw_year():
    with patch.object(svc, "datetime") as dt_mock:
        dt_mock.now.return_value = datetime(2026, 5, 15, tzinfo=TW)
        with patch.object(svc.twse_twt48u, "fetch_twt48u", return_value=[]), \
             patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[]), \
             patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]) as tpex_mock:
            svc.fetch_for_holdings({"2330"})
        # All three should receive year=2026
        tpex_mock.assert_called_with(2026)


def test_merges_across_three_sources():
    with patch.object(svc.twse_twt48u, "fetch_twt48u",
                      return_value=[_row("2330", date(2026, 6, 15), cash=Decimal("13"), source="TWSE_TWT48U")]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u",
                      return_value=[_row("0050", date(2026, 7, 1), cash=Decimal("3.5"), source="TWSE_TWT49U")]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc",
                      return_value=[_row("5483", date(2026, 8, 1), cash=Decimal("8"), source="TPEX_OTC")]):
        rows = svc.fetch_for_holdings({"2330", "0050", "5483"}, year=2026)
    assert [r.symbol for r in rows] == ["2330", "0050", "5483"]
    assert [r.source for r in rows] == ["TWSE_TWT48U", "TWSE_TWT49U", "TPEX_OTC"]


def test_dedupes_by_symbol_and_date_prioritising_twt48u():
    twt48u_row = _row("2330", date(2026, 6, 15), cash=Decimal("13"), source="TWSE_TWT48U")
    twt49u_row = _row("2330", date(2026, 6, 15), cash=Decimal("99"), source="TWSE_TWT49U")
    with patch.object(svc.twse_twt48u, "fetch_twt48u", return_value=[twt48u_row]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[twt49u_row]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]):
        rows = svc.fetch_for_holdings({"2330"}, year=2026)
    assert len(rows) == 1
    assert rows[0].source == "TWSE_TWT48U"
    assert rows[0].cash_dividend == Decimal("13")


def test_dedupe_replaces_when_existing_has_no_payload():
    """A first row with no cash/stock should be overwritten by a later source with real data."""
    empty_row = _row("2330", date(2026, 6, 15), cash=None, stock=None, source="TWSE_TWT48U")
    full_row = _row("2330", date(2026, 6, 15), cash=Decimal("13"), source="TWSE_TWT49U")
    with patch.object(svc.twse_twt48u, "fetch_twt48u", return_value=[empty_row]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[full_row]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]):
        rows = svc.fetch_for_holdings({"2330"}, year=2026)
    assert len(rows) == 1
    assert rows[0].source == "TWSE_TWT49U"
    assert rows[0].cash_dividend == Decimal("13")


def test_source_exception_does_not_abort():
    with patch.object(svc.twse_twt48u, "fetch_twt48u",
                      return_value=[_row("2330", date(2026, 6, 15), cash=Decimal("13"))]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u",
                      return_value=[_row("0050", date(2026, 7, 1), cash=Decimal("3.5"), source="TWSE_TWT49U")]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", side_effect=RuntimeError("down")):
        rows = svc.fetch_for_holdings({"2330", "0050"}, year=2026)
    assert {r.symbol for r in rows} == {"2330", "0050"}


def test_filters_to_held_symbols():
    with patch.object(svc.twse_twt48u, "fetch_twt48u",
                      return_value=[
                          _row("2330", date(2026, 6, 15), cash=Decimal("13")),
                          _row("0050", date(2026, 7, 1), cash=Decimal("3.5")),
                      ]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]):
        rows = svc.fetch_for_holdings({"2330"}, year=2026)
    assert [r.symbol for r in rows] == ["2330"]


def test_empty_holdings_short_circuits():
    with patch.object(svc.twse_twt48u, "fetch_twt48u") as mock_fetch:
        rows = svc.fetch_for_holdings(set(), year=2026)
    mock_fetch.assert_not_called()
    assert rows == []


def test_results_sorted_ascending_by_date():
    with patch.object(svc.twse_twt48u, "fetch_twt48u",
                      return_value=[
                          _row("Z", date(2026, 8, 1), cash=Decimal("1")),
                          _row("A", date(2026, 6, 1), cash=Decimal("1")),
                      ]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]):
        rows = svc.fetch_for_holdings({"A", "Z"}, year=2026)
    assert [r.ex_dividend_date for r in rows] == [date(2026, 6, 1), date(2026, 8, 1)]


def test_dividend_events_endpoint(client, db_session):
    db_session.add(models.Transaction(
        symbol="2330", name="台積電", type=models.TransactionType.BUY,
        quantity=10, price=Decimal("600"), fee=Decimal("0"), tax=Decimal("0"),
        trade_date=datetime(2026, 1, 1, 9, 0),
    ))
    db_session.commit()
    with patch.object(svc.twse_twt48u, "fetch_twt48u",
                      return_value=[_row("2330", date(2026, 6, 15), cash=Decimal("13"))]), \
         patch.object(svc.twse_twt49u, "fetch_twt49u", return_value=[]), \
         patch.object(svc.tpex_otc, "fetch_tpex_otc", return_value=[]):
        response = client.get("/api/portfolio/dividend-events", params={"year": 2026})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "2330"
    assert body[0]["ex_dividend_date"] == "2026-06-15"
    assert body[0]["cash_dividend"] == "13"
    assert body[0]["source"] == "TWSE_TWT48U"
