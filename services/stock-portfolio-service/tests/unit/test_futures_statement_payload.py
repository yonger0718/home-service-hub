"""Payload schemas of the 永豐期貨 statements (spec §5.1-§5.3): identities and enumerations."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.sinopac_futures_statement import (
    DailyPayload,
    FuturesPayload,
    MonthlyPayload,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _daily() -> dict:
    return _load("futures_daily_payload.json")


def _monthly() -> dict:
    return _load("futures_monthly_payload.json")


def test_daily_fixture_validates_and_roundtrips():
    raw = _daily()
    payload = DailyPayload.model_validate(raw)
    assert payload.equity.total_equity == Decimal("151266")
    assert payload.model_dump(mode="json") == raw


def test_monthly_fixture_validates_and_roundtrips():
    raw = _monthly()
    payload = MonthlyPayload.model_validate(raw)
    assert payload.lines.Q == Decimal("100880")
    assert payload.model_dump(mode="json") == raw


def test_discriminator_by_kind():
    adapter = TypeAdapter(FuturesPayload)
    assert isinstance(adapter.validate_python(_daily()), DailyPayload)
    assert isinstance(adapter.validate_python(_monthly()), MonthlyPayload)
    with pytest.raises(ValidationError):
        adapter.validate_python({**_daily(), "kind": "weekly"})


def _bump(path: tuple[str, ...]):
    def apply(raw: dict) -> None:
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = str(Decimal(node[path[-1]]) + 1)

    return apply


def _short_option(raw: dict) -> None:
    # the option position becomes a 賣: premium_value 300 now counts negative, so the printed total is wrong
    raw["positions"][1]["side"] = "賣"


@pytest.mark.parametrize(
    "mutate",
    [
        _bump(("equity", "balance")),
        _bump(("equity", "equity")),
        _bump(("equity", "total_equity")),
        _short_option,
    ],
    ids=["balance", "equity", "total_equity", "sell_counted_positive"],
)
def test_daily_identity_broken(mutate):
    raw = _daily()
    mutate(raw)
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)


def test_daily_sell_position_counts_negative():
    raw = _daily()
    raw["positions"][1]["side"] = "賣"
    raw["equity"]["total_equity"] = "150666"
    assert DailyPayload.model_validate(raw).equity.total_equity == Decimal("150666")


def test_daily_without_trades_allows_null_fills_total():
    raw = _daily()
    raw["fills"] = []
    raw["fills_total"] = None
    DailyPayload.model_validate(raw)


@pytest.mark.parametrize("key", ["M", "Q", "total_equity"])
def test_monthly_identity_broken(key):
    raw = _monthly()
    if key == "total_equity":
        raw["lines"][key] = "101181"
    else:
        raw["lines"][key] = str(Decimal(raw["lines"][key]) + 1)
    with pytest.raises(ValidationError):
        MonthlyPayload.model_validate(raw)


def test_strike_without_right_rejected():
    raw = _daily()
    raw["fills"][1]["right"] = None
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)
    raw = _daily()
    raw["positions"][0]["strike"] = "21000"
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)


def test_unknown_action_rejected():
    raw = _daily()
    raw["fills"][0]["action"] = "買進"
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)


def test_unknown_side_right_direction_rejected():
    for mutate in (
        lambda r: r["positions"][0].__setitem__("side", "long"),
        lambda r: r["fills"][1].__setitem__("right", "Straddle"),
        lambda r: r["deposits"][0].__setitem__("direction", "sideways"),
    ):
        raw = copy.deepcopy(_daily())
        mutate(raw)
        with pytest.raises(ValidationError):
            DailyPayload.model_validate(raw)


def test_month_end_not_last_day_rejected():
    raw = _monthly()
    raw["month_end"] = "2026-03-30"
    with pytest.raises(ValidationError):
        MonthlyPayload.model_validate(raw)


def test_activity_date_outside_month_rejected():
    raw = _monthly()
    raw["activity_dates"] = ["2026-04-01"]
    with pytest.raises(ValidationError):
        MonthlyPayload.model_validate(raw)


def test_bad_sha_and_qty_rejected():
    raw = _daily()
    raw["source"]["sha256"] = "A" * 64
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)
    raw = _daily()
    raw["positions"][0]["qty"] = 0
    with pytest.raises(ValidationError):
        DailyPayload.model_validate(raw)
