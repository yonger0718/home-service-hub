from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import broker_ib_flex_service, broker_ib_service

FIXTURE = Path(__file__).parent / "fixtures/ib_flex_sample.xml"
CSV_FIXTURE = Path(__file__).parent / "fixtures/ib_sample.csv"


def _transactions(parsed):
    return [r for r in parsed.rows if r.payload["_kind"] == "transaction"]


def test_parse_trades_mirror_the_csv_parser_payload():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    first = _transactions(parsed)[0].payload
    assert first == {
        "_kind": "transaction", "broker": "IB", "symbol": "ACWD", "market": "LSE",
        "name": "SS SPD MS AL CO WO UC ET-USD", "type": "BUY", "position_side": "LONG",
        "quantity": Decimal("1"), "price": Decimal("325.05"), "currency": "USD",
        "trade_date": datetime(2026, 6, 2, tzinfo=timezone.utc), "fee": Decimal("1.78"), "tax": Decimal("0"),
        "fingerprint_note": "SS SPD MS AL CO WO UC ET-USD ib:900001",
    }


def test_parse_trade_fingerprint_is_the_broker_fingerprint_with_the_trade_id_in_the_note():
    from app.services.import_service import _broker_transaction_fingerprint
    first = _transactions(broker_ib_flex_service.parse(FIXTURE.read_bytes()))[0]
    assert first.fingerprint == _broker_transaction_fingerprint(
        broker="IB", symbol="ACWD", market="LSE", type_="BUY", quantity=Decimal("1"), price=Decimal("325.05"),
        trade_date=datetime(2026, 6, 2, tzinfo=timezone.utc), fee=Decimal("1.78"), tax=Decimal("0"), currency="USD",
        note="SS SPD MS AL CO WO UC ET-USD ib:900001")


def test_parse_two_identical_same_day_fills_are_two_rows():
    raw = FIXTURE.read_bytes().replace(b'tradeID="900001"', b'tradeID="900009"', 1)  # same trade, another execution id
    raw = FIXTURE.read_bytes().replace(b"</Trades>", raw[raw.index(b"<Trade "):raw.index(b"/>") + 2] + b"</Trades>", 1)
    rows = _transactions(broker_ib_flex_service.parse(raw))
    assert len(rows) == 5 and len({r.fingerprint for r in rows}) == 5


def test_parse_trades_take_the_market_from_the_listing_exchange():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert {r.payload["market"] for r in _transactions(parsed)} == {"LSE"}
    us = broker_ib_flex_service.parse(FIXTURE.read_bytes().replace(b'listingExchange="LSEETF"', b'listingExchange="NASDAQ"'))
    assert {r.payload["market"] for r in _transactions(us)} == {"US"}


def test_parse_sell_uses_absolute_quantity_and_taxes():
    sell = [r.payload for r in _transactions(broker_ib_flex_service.parse(FIXTURE.read_bytes())) if r.payload["type"] == "SELL"]
    assert len(sell) == 1
    assert (sell[0]["quantity"], sell[0]["price"], sell[0]["fee"], sell[0]["tax"]) == (Decimal("10"), Decimal("28.1"), Decimal("1.78"), Decimal("0.5"))


def test_parse_trades_imports_execution_rows_only():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert len(_transactions(parsed)) == 4  # three buys, one sell; the ORDER-level twin and the OPT row are not rows


def test_parse_rejects_non_stock_trades_with_a_row_error_naming_the_category():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert any(e.message == "unsupported assetCategory OPT" for e in parsed.errors)


def test_parse_rejects_a_commission_in_another_currency():
    raw = FIXTURE.read_bytes().replace(b'ibCommissionCurrency="USD" netCash="-326.83"', b'ibCommissionCurrency="GBP" netCash="-326.83"', 1)
    parsed = broker_ib_flex_service.parse(raw)
    assert any(e.message == "ibCommissionCurrency GBP differs from currency USD" for e in parsed.errors)
    assert len(_transactions(parsed)) == 3


def test_parse_gbp_trade_maps_to_lse_like_the_csv_parser():
    raw = FIXTURE.read_bytes().replace(b'currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="ACWD"', b'currency="GBP" fxRateToBase="1.27" assetCategory="STK" subCategory="ETF" symbol="ACWD"', 1)
    raw = raw.replace(b'ibCommissionCurrency="USD" netCash="-326.83"', b'ibCommissionCurrency="GBP" netCash="-326.83"', 1)
    first = _transactions(broker_ib_flex_service.parse(raw))[0].payload
    assert (first["market"], first["currency"]) == ("LSE", "GBP")
