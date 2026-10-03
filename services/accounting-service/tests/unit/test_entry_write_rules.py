from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.schemas.writes import ChildIn, EntryIn
from app.services.entry_write_service import proposed_fx_fee, signed


@pytest.mark.parametrize(
    "kind, expected",
    [("expense", "-42.5"), ("receivable", "-42.5"), ("income", "42.5"), ("payable", "42.5")],
)
def test_signed_applies_the_sign_of_the_kind(kind, expected):
    assert signed(kind, Decimal("42.5")) == Decimal(expected)


def test_signed_refuses_kinds_without_a_sign_rule():
    with pytest.raises(ValueError, match="transfer_out"):
        signed("transfer_out", Decimal("1"))


@pytest.mark.parametrize(
    "rounding, currency, amount, expected",
    [
        ("round", "TWD", "-1170", "18"),
        ("floor", "TWD", "-1170", "17"),
        ("ceil", "TWD", "-1170", "18"),
        ("keep", "TWD", "-1170", "17.5500"),
        (None, "TWD", "-1170", "17.5500"),
        ("round", "USD", "-33.333", "0.50"),
        ("floor", "USD", "-33.333", "0.49"),
        ("ceil", "USD", "-33.333", "0.50"),
    ],
)
def test_proposed_fx_fee_rounds_per_account_setting(rounding, currency, amount, expected):
    account = SimpleNamespace(currency=currency, fx_fee_pct=Decimal("1.5"), fx_fee_rounding=rounding)
    assert str(proposed_fx_fee(account, Decimal(amount))) == expected


def test_no_proposed_fee_without_a_percentage():
    account = SimpleNamespace(currency="TWD", fx_fee_pct=None, fx_fee_rounding="round")
    assert proposed_fx_fee(account, Decimal("-100")) is None


@pytest.mark.parametrize("value", ["1.23456", "0.00001", "1e-5"])
def test_amounts_with_more_than_four_decimals_are_rejected(value):
    with pytest.raises(PydanticValidationError, match="at most 4 decimal places"):
        ChildIn(amount=value)


@pytest.mark.parametrize("value", ["1.2345", "1.10000", "100", "1E+3"])
def test_four_decimals_or_fewer_are_accepted(value):
    assert ChildIn(amount=value).amount == Decimal(value)


def test_amounts_are_unsigned_and_positive():
    with pytest.raises(PydanticValidationError):
        EntryIn(account_id=1, kind="expense", amount="-5", entry_date="2026-09-01")
    with pytest.raises(PydanticValidationError):
        ChildIn(amount="0")
