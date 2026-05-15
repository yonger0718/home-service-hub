"""Unit tests for TWT49U historical dividend fetcher."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services import dividend_history_service as svc


@pytest.fixture(autouse=True)
def _clear_dividend_history_caches():
    svc._fetch_twt49u_year_raw.cache_clear()
    svc._fetch_detail_cached.cache_clear()
    svc._fetch_tpex_year_cached.cache_clear()
    yield
    svc._fetch_twt49u_year_raw.cache_clear()
    svc._fetch_detail_cached.cache_clear()
    svc._fetch_tpex_year_cached.cache_clear()


_FIELDS = [
    "資料日期",
    "股票代號",
    "股票名稱",
    "除權息前收盤價",
    "除權息參考價",
    "權值+息值",
    "權/息",
    "漲停價格",
    "跌停價格",
    "開盤競價基準",
    "減除股利參考價",
    "詳細資料",
]


def _envelope(rows, *, stat="OK", fields=None):
    return {"stat": stat, "fields": fields or _FIELDS, "data": rows}


def _row(symbol, ex_roc, *, prev="100.00", ref="95.00", div="5.00", div_type="息"):
    return [
        ex_roc, symbol, f"{symbol}-name", prev, ref, div, div_type,
        "110.00", "85.50", "100.00", "95.00", f"{symbol},{_iso_to_twse(ex_roc)}",
    ]


def _iso_to_twse(roc: str) -> str:
    # "115/06/15" → "20260615"
    parts = roc.replace("年", "/").replace("月", "/").replace("日", "").split("/")
    year = int(parts[0]) + 1911 if int(parts[0]) < 1911 else int(parts[0])
    return f"{year:04d}{int(parts[1]):02d}{int(parts[2]):02d}"


def test_parse_twt49u_response_filters_by_symbol():
    payload = _envelope([
        _row("2330", "115/06/15"),
        _row("0050", "115/06/16"),
    ])
    rows = svc.parse_twt49u_response("2330", payload)
    assert len(rows) == 1
    ex_date, prev_close, ref_price, detail_param, div_value, div_type = rows[0]
    assert ex_date == date(2026, 6, 15)
    assert prev_close == Decimal("100.00")
    assert ref_price == Decimal("95.00")
    assert detail_param == "20260615"
    assert div_value == Decimal("5.00")
    assert div_type == "息"


def test_parse_returns_empty_when_stat_not_ok():
    payload = _envelope([_row("2330", "115/06/15")], stat="ERR")
    assert svc.parse_twt49u_response("2330", payload) == []


def test_fetch_symbol_year_logs_and_returns_empty_on_http_failure():
    with patch.object(svc, "_http_get", return_value=None):
        assert svc.fetch_symbol_year("2330", 2026) == []


def test_fetch_symbol_year_uses_detail_for_cash():
    main_payload = _envelope([_row("2330", "115/06/15")])
    detail_payload = {
        "stat": "ok",
        "data": [["2330", "台積電", "4.00 元／股", "", "0 股"]],
    }
    with patch.object(svc, "_http_get", side_effect=[main_payload, detail_payload]):
        events = svc.fetch_symbol_year("2330", 2026)
    assert len(events) == 1
    ev = events[0]
    assert ev.cash_dividend_per_share == Decimal("4.00")
    assert ev.stock_dividend_per_thousand is None


def test_fetch_symbol_year_falls_back_to_row_when_detail_empty():
    main_payload = _envelope([_row("2330", "115/06/15", div="2.00", div_type="息")])
    detail_payload = {"stat": "no data"}
    with patch.object(svc, "_http_get", side_effect=[main_payload, detail_payload]):
        events = svc.fetch_symbol_year("2330", 2026)
    assert len(events) == 1
    assert events[0].cash_dividend_per_share == Decimal("2.00")
    assert events[0].stock_dividend_per_thousand is None


def test_fetch_symbol_year_skips_when_both_cash_and_stock_missing():
    main_payload = _envelope([_row("2330", "115/06/15", div="0", div_type="-")])
    detail_payload = {"stat": "ok", "data": [["2330", "台積電", "0 元／股", "", "0 股"]]}
    with patch.object(svc, "_http_get", side_effect=[main_payload, detail_payload]):
        events = svc.fetch_symbol_year("2330", 2026)
    assert events == []


def test_fetch_for_symbol_all_years_walks_year_range():
    with patch.object(svc, "fetch_symbol_year", return_value=[]) as mock:
        with patch.object(svc, "_current_tw_year", return_value=2026):
            svc.fetch_for_symbol_all_years("2330", date(2024, 3, 1))
    years_called = [args[0][1] for args in mock.call_args_list]
    assert years_called == [2024, 2025, 2026]


def test_fetch_for_symbol_all_years_isolates_per_year_exception():
    def _flaky(symbol, year):
        if year == 2025:
            raise RuntimeError("boom")
        return [
            svc.HistoricalDividendEvent(
                symbol=symbol,
                ex_date=date(year, 6, 1),
                cash_dividend_per_share=Decimal("1"),
                stock_dividend_per_thousand=None,
                previous_close=None,
                reference_price=None,
                source="TWT49U",
            )
        ]

    with patch.object(svc, "fetch_symbol_year", side_effect=_flaky), \
            patch.object(svc, "_current_tw_year", return_value=2026):
        events = svc.fetch_for_symbol_all_years("2330", date(2024, 1, 1))
    assert [e.ex_date.year for e in events] == [2024, 2026]
