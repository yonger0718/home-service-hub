"""Parser tests for the three dividend-event sources."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.services.dividend_sources import twse_twt48u, twse_twt49u, tpex_otc


# ---------- TWT48U ----------

def test_twt48u_parses_payload():
    payload = [
        {
            "股票代號": "2330",
            "名稱": "台積電",
            "除息交易日": "115/06/15",
            "除權交易日": "",
            "最近一次配息": "13",
            "最近一次配股": "",
        }
    ]
    rows = twse_twt48u.parse_twt48u(payload)
    assert len(rows) == 1
    assert rows[0].symbol == "2330"
    assert rows[0].ex_dividend_date == date(2026, 6, 15)
    assert rows[0].cash_dividend == Decimal("13")
    assert rows[0].stock_dividend is None
    assert rows[0].source == "TWSE_TWT48U"


def test_twt48u_skips_when_no_date():
    payload = [{"股票代號": "2330", "除息交易日": "", "除權交易日": ""}]
    assert twse_twt48u.parse_twt48u(payload) == []


def test_twt48u_skips_when_no_symbol():
    payload = [{"股票代號": "", "除息交易日": "115/06/15"}]
    assert twse_twt48u.parse_twt48u(payload) == []


def test_twt48u_falls_back_to_ex_rights_date():
    payload = [{
        "股票代號": "2330", "除息交易日": "",
        "除權交易日": "115/06/16", "最近一次配股": "0.5",
    }]
    rows = twse_twt48u.parse_twt48u(payload)
    assert rows[0].ex_dividend_date == date(2026, 6, 16)
    assert rows[0].stock_dividend == Decimal("0.5")


# ---------- TWT49U ----------

def test_twt49u_parses_payload():
    payload = [
        {
            "公司代號": "2330", "除權息日期": "115/06/15",
            "現金股利": "13.0", "股票股利": "",
        },
        {
            "公司代號": "0050", "除權息日期": "115/07/01",
            "現金股利": "3.5", "股票股利": "0",
        },
    ]
    rows = twse_twt49u.parse_twt49u(payload)
    assert [r.symbol for r in rows] == ["2330", "0050"]
    assert rows[0].cash_dividend == Decimal("13.0")
    assert rows[1].cash_dividend == Decimal("3.5")
    assert rows[1].stock_dividend is None
    assert rows[0].source == "TWSE_TWT49U"


def test_twt49u_skips_missing_symbol():
    payload = [{"公司代號": "", "除權息日期": "115/06/15", "現金股利": "1"}]
    assert twse_twt49u.parse_twt49u(payload) == []


def test_twt49u_accepts_bytes():
    payload = json.dumps([
        {"公司代號": "2330", "除權息日期": "115/06/15", "現金股利": "13"}
    ]).encode("utf-8")
    rows = twse_twt49u.parse_twt49u(payload)
    assert len(rows) == 1


def test_twt49u_western_year_format():
    payload = [{"公司代號": "2330", "除權息日期": "2026/06/15", "現金股利": "13"}]
    rows = twse_twt49u.parse_twt49u(payload)
    assert rows[0].ex_dividend_date == date(2026, 6, 15)


# ---------- TPEx OTC ----------

def _tpex_payload(*rows):
    return {
        "tables": [
            {
                "fields": ["除權息日期", "股票代號", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "現金股利", "股票股利"],
                "data": list(rows),
            }
        ]
    }


def test_tpex_otc_parses_payload():
    payload = _tpex_payload(
        ["115/06/15", "5483", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "8.0", "0"],
    )
    rows = tpex_otc.parse_tpex_otc(payload)
    assert len(rows) == 1
    assert rows[0].symbol == "5483"
    assert rows[0].ex_dividend_date == date(2026, 6, 15)
    assert rows[0].cash_dividend == Decimal("8.0")
    assert rows[0].stock_dividend is None
    assert rows[0].source == "TPEX_OTC"


def test_tpex_otc_stock_per_thousand_to_per_share():
    """cells[14] is shares per 1000 (TPEx convention) — divide by 1000."""
    payload = _tpex_payload(
        ["115/06/15", "5483", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "0", "500"],
    )
    rows = tpex_otc.parse_tpex_otc(payload)
    assert rows[0].stock_dividend == Decimal("0.5")
    assert rows[0].cash_dividend is None


def test_tpex_otc_skips_short_rows():
    payload = _tpex_payload(["115/06/15", "5483"])  # only 2 cells
    assert tpex_otc.parse_tpex_otc(payload) == []


def test_tpex_otc_skips_missing_date():
    payload = _tpex_payload(
        ["", "5483", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "1", "0"],
    )
    assert tpex_otc.parse_tpex_otc(payload) == []


def test_tpex_otc_accepts_bytes():
    raw = json.dumps(_tpex_payload(
        ["115/06/15", "5483", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "8", "0"],
    )).encode("utf-8")
    rows = tpex_otc.parse_tpex_otc(raw)
    assert len(rows) == 1
