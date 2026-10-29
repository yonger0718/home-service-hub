import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import portfolio as models
from app.schemas.cathay_statement import StatementPayload
from app.services import broker_cathay_service
from app.services.broker_cathay_statement_service import (
    CATEGORY_TO_CSV_SIDE,
    HeldReason,
    Mapped,
    map_statement,
    statement_fingerprint,
)
from app.services.import_service import _transaction_fingerprint

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cathay_statement_payload.json"


def base(category="集買", name="範例電子", code="9999"):
    amount, fee, tax = 12300, 5, 0
    if category in ("集賣", "沖賣"):
        tax = 37
        net = amount - fee - tax
    else:
        net = -(amount + fee)
    return {
        "source": {"drive_file_id": "f", "sha256": "a" * 64, "file_name": "x.pdf"},
        "trade_date": "2026-03-09",
        "settlement_date": "2026-03-11",
        "net_amount": str(net),
        "lines": [
            {"name": name, "category": category, "quantity": 1000, "price": "12.3",
             "amount": str(amount), "fee": str(fee), "tax": str(tax), "net": str(net),
             "order_no": "XY12300", "points": False}
        ],
        "totals": {"amount": str(amount), "fee": str(fee), "tax": str(tax), "net": str(net)},
        "holdings": [{"code": code, "name": name, "shares": 1000}],
        "warnings": [],
    }


@pytest.mark.parametrize(
    "category,expected",
    [
        ("集買", ("BUY", "LONG", None)),
        ("集賣", ("SELL", "LONG", None)),
        ("沖買", ("BUY", "LONG", "沖買")),
        ("沖賣", ("SELL", "LONG", "沖賣")),
    ],
)
def test_mapping_table(db_session, category, expected):
    result = map_statement(db_session, StatementPayload(**base(category, name="台積電")))
    assert isinstance(result, Mapped)
    p = result.rows[0].payload
    assert (p["type"], p["position_side"], p["broker_day_trade_marker"]) == expected
    assert p["broker_subtype"] == CATEGORY_TO_CSV_SIDE[category][0]
    assert p["order_id"] == "XY123"
    assert p["trade_date"] == datetime(2026, 3, 9, tzinfo=timezone.utc)
    c = result.canonical[0]
    assert c["marker"] == expected[2] and c["price"] == "12.3" and c["order_no"] == "XY12300"


def test_unsupported_payload_holds(db_session):
    data = base()
    data.update(lines=[], warnings=["unsupported_category"])
    assert map_statement(db_session, StatementPayload(**data)) == HeldReason("unsupported_category", None)
    data["warnings"] = []
    with pytest.raises(ValidationError):
        StatementPayload(**data)


def test_symbol_resolution_order(db_session, monkeypatch):
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例電子", ["1111"])
    db_session.add(models.Transaction(
        symbol="2222", market="TW", name="範例電子", type="BUY", quantity=1, price=1,
        trade_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    db_session.commit()
    payload = StatementPayload(**base())
    assert map_statement(db_session, payload).rows[0].payload["symbol"] == "1111"
    del broker_cathay_service.NAME_TO_SYMBOL["範例電子"]
    assert map_statement(db_session, payload).rows[0].payload["symbol"] == "2222"
    db_session.query(models.Transaction).delete()
    db_session.commit()
    assert map_statement(db_session, payload).rows[0].payload["symbol"] == "9999"
    data = base()
    data["holdings"] = []
    assert map_statement(db_session, StatementPayload(**data)) == HeldReason("unresolved_symbol", "範例電子")


def test_fingerprint_namespace_disjoint(db_session):
    result = map_statement(db_session, StatementPayload(**base(name="台積電")))
    row = result.rows[0]
    p = row.payload
    args = (p["symbol"], p["type"], p["quantity"], p["price"], p["trade_date"], p["fee"], p["tax"])
    csv_fp = _transaction_fingerprint(*args, order_id="XY123", position_side="LONG")
    full_fp = _transaction_fingerprint(*args, order_id="XY12300", position_side="LONG")
    assert len({row.fingerprint, csv_fp, full_fp}) == 3
    assert row.fingerprint == statement_fingerprint(row, "XY12300")


def mutate(fn):
    data = base()
    fn(data)
    return data


def test_payload_rejects_buy_tax():
    def f(d):
        d["lines"][0]["tax"] = "7"
        d["lines"][0]["net"] = "-12312"
        d["totals"]["tax"] = "7"
        d["totals"]["net"] = "-12312"
        d["net_amount"] = "-12312"
    with pytest.raises(ValidationError):
        StatementPayload(**mutate(f))


def test_payload_rejects_fee_change_matched_in_totals():
    def f(d):
        d["lines"][0]["fee"] = "6"
        d["totals"]["fee"] = "6"
    with pytest.raises(ValidationError):
        StatementPayload(**mutate(f))


def _line(key, value):
    return lambda d: d["lines"][0].__setitem__(key, value)


def _total(key):
    return lambda d: d["totals"].__setitem__(key, str(int(d["totals"][key]) + 1))


NEGATIVES = {
    "qty0": _line("quantity", 0),
    "qty1.5": _line("quantity", 1.5),
    "price0": _line("price", "0"),
    "amount-neg": _line("amount", "-12300"),
    "amount-frac": _line("amount", "12300.5"),
    "fee-neg": _line("fee", "-5"),
    "fee-frac": _line("fee", "5.5"),
    "tax-neg": _line("tax", "-1"),
    "tax-frac": _line("tax", "0.5"),
    "order6": _line("order_no", "XY1230"),
    "order8": _line("order_no", "XY123000"),
    "order-dash": _line("order_no", "XY-1230"),
    "shares-neg": lambda d: d["holdings"][0].__setitem__("shares", -1),
    "code12": lambda d: d["holdings"][0].__setitem__("code", "12"),
    "sum-amount": _total("amount"),
    "sum-fee": _total("fee"),
    "sum-tax": _total("tax"),
    "sum-net": _total("net"),
    "header-net": lambda d: d.__setitem__("net_amount", "-1"),
    "warning-no-bypass": lambda d: (d.__setitem__("warnings", ["unsupported_category"]), _total("amount")(d)),
}


@pytest.mark.parametrize("key", list(NEGATIVES))
def test_payload_field_negatives(key):
    with pytest.raises(ValidationError):
        StatementPayload(**mutate(NEGATIVES[key]))


def test_payload_roundtrip():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = StatementPayload(**raw)
    assert json.loads(payload.model_dump_json()) == raw
    assert payload.lines[0].price == Decimal("12.3")
