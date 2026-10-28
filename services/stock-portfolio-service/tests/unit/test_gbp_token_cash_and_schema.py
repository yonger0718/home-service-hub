"""#124 / #125: the exact ``GBp`` (pence) token survives the transaction schema, and a pence dividend's cash leg is
posted in pounds. Fictional amounts only."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.schemas.portfolio import TransactionCreate
from app.services.cash_account_service import dividend_cash_amount


def _create(currency: str) -> TransactionCreate:
    return TransactionCreate(symbol="vod", market="lse", type="BUY", quantity=Decimal("10"), price=Decimal("72.5"),
                             currency=currency, fx_rate_to_twd=Decimal("0.41"))


@pytest.mark.parametrize("raw", ["GBp", " GBp "])
def test_transaction_schema_keeps_the_pence_token(raw: str) -> None:
    assert _create(raw).currency == "GBp"


@pytest.mark.parametrize(("raw", "expected"), [("gbp", "GBP"), ("GBP", "GBP"), ("usd", "USD"), ("Gbp", "GBP")])
def test_transaction_schema_uppercases_every_other_code(raw: str, expected: str) -> None:
    assert _create(raw).currency == expected


@pytest.mark.parametrize(("currency", "amount", "expected"), [
    ("GBp", "1234.5650", "12.3457"),  # pence → pounds, half-up to the cash column's 4 places
    (" GBp ", "50", "0.5000"),
    ("GBP", "12.34", "12.34"),  # pounds are never divided
    ("USD", "7.5", "7.5"),
    ("TWD", "100", "100"),
])
def test_dividend_cash_amount(currency: str, amount: str, expected: str) -> None:
    assert dividend_cash_amount(SimpleNamespace(currency=currency, amount=Decimal(amount))) == Decimal(expected)
