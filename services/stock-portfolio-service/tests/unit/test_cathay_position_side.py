from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import broker_cathay_service, import_service


CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)


def _cathay_csv(*rows: str) -> bytes:
    return (
        "根據您篩選的結果，總計有N筆資料\n" + CATHAY_HEADER + "\n".join(rows) + "\n"
    ).encode("utf-8")


def _row(
    *,
    name: str = "晶宏",
    side: str = "現買",
    quantity: str = "1,000",
    price: str = "56.3",
    fee: str = "22",
    tax: str = "0",
    interest: str = "0",
    borrow_fee: str = "0",
    order_id: str = "aT532",
) -> str:
    return (
        f'{name},2026/05/08,"{quantity}","-56,322",{side},"{price}","56,300",'
        f"{fee},{tax},0,0,{interest},0,{borrow_fee},{order_id}"
    )


@pytest.fixture(autouse=True)
def _stable_name_map(monkeypatch):
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "晶宏", ["3141"])


@pytest.mark.parametrize(
    ("side", "type_", "position_side"),
    [
        ("現買", "BUY", "LONG"),
        ("資買", "BUY", "LONG"),
        ("沖買", "BUY", "LONG"),
        ("券買", "BUY", "SHORT"),
        ("現賣", "SELL", "LONG"),
        ("資賣", "SELL", "LONG"),
        ("沖賣", "SELL", "LONG"),
        ("券賣", "SELL", "SHORT"),
    ],
)
def test_cathay_side_maps_to_type_and_position_side(side, type_, position_side):
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row(side=side)))
    assert parsed.errors == []
    payload = parsed.rows[0].payload
    assert payload["type"] == type_
    assert payload["position_side"] == position_side


def test_long_long_pair_share_fingerprint_when_same_business_key():
    # 現買 (LONG) and 資買 (LONG) collapse to identical fingerprint:
    # both are BUY/LONG and broker_subtype is not in fingerprint.
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="現買"), _row(side="資買"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].fingerprint == parsed.rows[1].fingerprint


def test_long_vs_short_diverge_in_fingerprint():
    # 現買 (LONG) vs 券買 (SHORT) — position_side is in fingerprint, so the two
    # rows hash to distinct fingerprints even with otherwise identical fields.
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="現買"), _row(side="券買"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].fingerprint != parsed.rows[1].fingerprint


def test_zheng_mai_folds_interest_into_fee():
    # 資賣: 手續費 62 + 利息 23 → fee 85
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="資賣", fee="62", interest="23"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].payload["fee"] == Decimal("85")


def test_quan_mai_folds_borrow_fee_into_fee():
    # 券賣: 手續費 63 + 券手續費 63 → fee 126
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="券賣", fee="63", borrow_fee="63"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].payload["fee"] == Decimal("126")


def test_quan_mai_folds_interest_into_fee():
    # 券買: 手續費 22 + 利息 88 (cover interest) → fee 110
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="券買", fee="22", interest="88"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].payload["fee"] == Decimal("110")


def test_xian_mai_no_fold_unchanged():
    # 現買: zero in 利息 + 券手續費 → fee unchanged (22)
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="現買", fee="22"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].payload["fee"] == Decimal("22")


def test_fingerprint_long_omits_side_marker_for_backward_compat():
    # A 現買 (LONG) row's fingerprint must equal a hash computed WITHOUT
    # position_side argument — keeps legacy rehash chain matching pre-feature
    # rows whose fingerprint never included a side marker.
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row(side="現買")))
    legacy_with_order_id = import_service._transaction_fingerprint(
        "3141",
        "BUY",
        1000,
        Decimal("56.3"),
        parsed.rows[0].payload["trade_date"],
        Decimal("22"),
        Decimal("0"),
        order_id="aT532",
    )
    assert parsed.rows[0].fingerprint == legacy_with_order_id
