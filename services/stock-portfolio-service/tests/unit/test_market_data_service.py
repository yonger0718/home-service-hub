"""Market-data parsers, upsert idempotency, and history endpoint."""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.price_history import PriceHistory
from app.services import market_data_service as svc


TRADING_DAY = date(2026, 5, 14)


def _twse_payload(*rows) -> dict:
    return {
        "tables": [
            {
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "成交股數",
                    "成交筆數",
                    "成交金額",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                ],
                "data": list(rows),
            }
        ]
    }


def _tpex_payload(*rows) -> dict:
    return {
        "tables": [
            {
                "fields": [
                    "代號",
                    "名稱",
                    "收盤",
                    "開盤",
                    "最高",
                    "最低",
                    "成交股數",
                    "成交金額(元)",
                ],
                "data": list(rows),
            }
        ]
    }


def test_parse_twse_mi_index_extracts_rows():
    payload = _twse_payload(
        ["2330", "台積電", "12,345,678", "9,000", "8,500,000,000", "600.00", "612.00", "599.00", "610.00"],
        ["0050", "元大台灣50", "5,000,000", "3,000", "700,000,000", "140.00", "141.00", "139.50", "140.50"],
    )
    rows = svc.parse_twse_mi_index(payload, TRADING_DAY)
    assert len(rows) == 2
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["2330"].close == Decimal("610.00")
    assert by_symbol["2330"].volume == 12345678
    assert by_symbol["2330"].source == "TWSE"
    assert by_symbol["0050"].open == Decimal("140.00")


def test_parse_twse_mi_index_skips_missing_close():
    payload = _twse_payload(
        ["2330", "台積電", "12,345,678", "9,000", "8,500,000,000", "600.00", "612.00", "599.00", "-"],
        ["0050", "元大台灣50", "5,000,000", "3,000", "700,000,000", "140.00", "141.00", "139.50", "140.50"],
    )
    rows = svc.parse_twse_mi_index(payload, TRADING_DAY)
    assert [row.symbol for row in rows] == ["0050"]


def test_parse_twse_mi_index_accepts_bytes_payload():
    payload = _twse_payload(
        ["2330", "台積電", "12345678", "9000", "8500000000", "600.00", "612.00", "599.00", "610.00"],
    )
    raw = json.dumps(payload).encode("utf-8-sig")
    rows = svc.parse_twse_mi_index(raw, TRADING_DAY)
    assert len(rows) == 1
    assert rows[0].symbol == "2330"


def test_parse_twse_mi_index_handles_legacy_data9_shape():
    payload = {
        "data9": [
            ["2330", "台積電", "12345678", "9000", "8500000000", "600.00", "612.00", "599.00", "610.00"],
        ]
    }
    rows = svc.parse_twse_mi_index(payload, TRADING_DAY)
    assert len(rows) == 1
    assert rows[0].close == Decimal("610.00")


def test_parse_tpex_daily_quotes_extracts_rows():
    payload = _tpex_payload(
        ["3008", "大立光", "3000.00", "2990.00", "3010.00", "2985.00", "1,234,567", "3,700,000,000"],
    )
    rows = svc.parse_tpex_daily_quotes(payload, TRADING_DAY)
    assert len(rows) == 1
    assert rows[0].symbol == "3008"
    assert rows[0].close == Decimal("3000.00")
    assert rows[0].source == "TPEx"
    assert rows[0].turnover == Decimal("3700000000")


def test_upsert_rows_inserts_new_records(db_session):
    rows = [
        svc.DailyPriceRow(
            symbol="2330",
            date=TRADING_DAY,
            open=Decimal("600"),
            high=Decimal("612"),
            low=Decimal("599"),
            close=Decimal("610"),
            volume=12345678,
            turnover=Decimal("8500000000"),
            source="TWSE",
        )
    ]
    written = svc.upsert_rows(db_session, rows)
    assert written == 1
    stored = db_session.query(PriceHistory).one()
    assert stored.close == Decimal("610")


def test_upsert_rows_is_idempotent(db_session):
    row = svc.DailyPriceRow(
        symbol="2330",
        date=TRADING_DAY,
        open=Decimal("600"),
        high=Decimal("612"),
        low=Decimal("599"),
        close=Decimal("610"),
        volume=12345678,
        turnover=Decimal("8500000000"),
        source="TWSE",
    )
    svc.upsert_rows(db_session, [row])
    svc.upsert_rows(db_session, [row])
    assert db_session.query(PriceHistory).count() == 1


def test_upsert_rows_updates_existing_close(db_session):
    initial = svc.DailyPriceRow(
        symbol="2330",
        date=TRADING_DAY,
        open=None,
        high=None,
        low=None,
        close=Decimal("605"),
        volume=None,
        turnover=None,
        source="TWSE",
    )
    svc.upsert_rows(db_session, [initial])
    revised = svc.DailyPriceRow(
        symbol="2330",
        date=TRADING_DAY,
        open=Decimal("600"),
        high=Decimal("612"),
        low=Decimal("599"),
        close=Decimal("610"),
        volume=12345678,
        turnover=Decimal("8500000000"),
        source="TWSE",
    )
    svc.upsert_rows(db_session, [revised])
    row = db_session.query(PriceHistory).one()
    assert row.close == Decimal("610")
    assert row.open == Decimal("600")
    assert row.volume == 12345678


def test_list_history_normalises_symbol_and_filters_range(db_session):
    rows = [
        svc.DailyPriceRow(
            symbol="2330",
            date=date(2026, 5, d),
            open=None, high=None, low=None,
            close=Decimal("600") + Decimal(d),
            volume=None, turnover=None,
            source="TWSE",
        )
        for d in (10, 12, 14, 16)
    ]
    svc.upsert_rows(db_session, rows)

    out = svc.list_history(db_session, symbol="2330.TW", from_date=date(2026, 5, 11), to_date=date(2026, 5, 14))
    assert [row.date for row in out] == [date(2026, 5, 12), date(2026, 5, 14)]


def test_backfill_date_uses_mocked_fetchers(db_session):
    twse = [
        svc.DailyPriceRow(
            symbol="2330",
            date=TRADING_DAY,
            open=Decimal("600"),
            high=Decimal("612"),
            low=Decimal("599"),
            close=Decimal("610"),
            volume=12345678,
            turnover=Decimal("8500000000"),
            source="TWSE",
        )
    ]
    tpex = [
        svc.DailyPriceRow(
            symbol="3008",
            date=TRADING_DAY,
            open=Decimal("2990"),
            high=Decimal("3010"),
            low=Decimal("2985"),
            close=Decimal("3000"),
            volume=1234567,
            turnover=Decimal("3700000000"),
            source="TPEx",
        )
    ]
    with (
        patch.object(svc, "fetch_twse_date", return_value=twse) as twse_mock,
        patch.object(svc, "fetch_tpex_date", return_value=tpex) as tpex_mock,
    ):
        result = svc.backfill_date(db_session, TRADING_DAY, market="BOTH")

    twse_mock.assert_called_once_with(TRADING_DAY)
    tpex_mock.assert_called_once_with(TRADING_DAY)
    assert result["written"] == 2
    assert db_session.query(PriceHistory).count() == 2


def test_backfill_date_market_filter(db_session):
    twse = [
        svc.DailyPriceRow(
            symbol="2330", date=TRADING_DAY,
            open=None, high=None, low=None,
            close=Decimal("610"), volume=None, turnover=None,
            source="TWSE",
        )
    ]
    with (
        patch.object(svc, "fetch_twse_date", return_value=twse) as twse_mock,
        patch.object(svc, "fetch_tpex_date") as tpex_mock,
    ):
        result = svc.backfill_date(db_session, TRADING_DAY, market="TWSE")

    twse_mock.assert_called_once()
    tpex_mock.assert_not_called()
    assert result["twse_rows"] == 1
    assert result["tpex_rows"] == 0


def test_history_endpoint_returns_range(client, db_session):
    svc.upsert_rows(
        db_session,
        [
            svc.DailyPriceRow(
                symbol="2330", date=date(2026, 5, 13),
                open=Decimal("600"), high=Decimal("612"), low=Decimal("599"),
                close=Decimal("610"), volume=12345678, turnover=Decimal("8500000000"),
                source="TWSE",
            ),
            svc.DailyPriceRow(
                symbol="2330", date=date(2026, 5, 14),
                open=Decimal("610"), high=Decimal("615"), low=Decimal("608"),
                close=Decimal("614"), volume=11000000, turnover=Decimal("6700000000"),
                source="TWSE",
            ),
        ],
    )
    response = client.get(
        "/api/portfolio/price-history",
        params={"symbol": "2330", "from": "2026-05-13", "to": "2026-05-14"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["close"] == "610.0000"
    assert body[1]["date"] == "2026-05-14"


def test_backfill_endpoint_triggers_service(client, db_session):
    fake = [
        svc.DailyPriceRow(
            symbol="2330", date=TRADING_DAY,
            open=None, high=None, low=None,
            close=Decimal("610"), volume=None, turnover=None,
            source="TWSE",
        )
    ]
    with patch.object(svc, "fetch_twse_date", return_value=fake) as twse_mock:
        response = client.post(
            "/api/portfolio/price-history/backfill",
            params={"date": TRADING_DAY.isoformat(), "market": "TWSE"},
        )
    twse_mock.assert_called_once()
    assert response.status_code == 200
    body = response.json()
    assert body["written"] == 1
    assert db_session.query(PriceHistory).count() == 1


@pytest.mark.parametrize("market", ["INVALID", "us", "twse "])
def test_backfill_endpoint_rejects_unknown_market(client, market):
    response = client.post(
        "/api/portfolio/price-history/backfill",
        params={"date": TRADING_DAY.isoformat(), "market": market},
    )
    assert response.status_code == 422
