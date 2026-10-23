"""#115 — LSE dividends stored in pence (``currency='GBp'``) are valued in pounds.

Stored rows stay in pence; only the TWD valuation divides by 100. ``'GBP'``
(pounds) rows are never divided, and the exact ``GBp`` token survives the API
schema and the native-currency bucket of the portfolio summary.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.schemas.portfolio import Dividend as DividendSchema
from app.schemas.portfolio import DividendCreate
from app.services import networth_backfill_service as nbs
from app.services import portfolio_service
from app.services.portfolio_service import _dividend_amount_twd, _pence_divisor


def _row(currency: str | None, amount: str = "1234.5000", fx: str | None = "40.5"):
    return SimpleNamespace(
        id=1,
        symbol="VOD",
        currency=currency,
        amount=Decimal(amount),
        fx_rate_to_twd=Decimal(fx) if fx is not None else None,
        ex_dividend_date=datetime(2026, 3, 1),
    )


# ---------- helper ----------


def test_pence_divisor_exact_token_only() -> None:
    assert _pence_divisor("GBp") == Decimal("100")
    assert _pence_divisor(" GBp ") == Decimal("100")
    assert _pence_divisor("GBP") == Decimal("1")
    assert _pence_divisor("gbp") == Decimal("1")
    assert _pence_divisor("USD") == Decimal("1")
    assert _pence_divisor(None) == Decimal("1")


def test_gbp_pence_dividend_divided_by_100_then_fx() -> None:
    assert _dividend_amount_twd(_row("GBp")) == Decimal("499.9725")


def test_gbp_pounds_dividend_not_divided() -> None:
    assert _dividend_amount_twd(_row("GBP")) == Decimal("1234.5000") * Decimal("40.5")


def test_twd_dividend_passthrough() -> None:
    assert _dividend_amount_twd(_row("TWD", amount="300", fx=None)) == Decimal("300")
    assert _dividend_amount_twd(_row(None, amount="300", fx=None)) == Decimal("300")


def test_gbp_pence_dividend_without_fx_raises() -> None:
    with pytest.raises(ValueError, match="missing fx_rate_to_twd"):
        _dividend_amount_twd(_row("GBp", fx=None))


# ---------- schema ----------


def _orm_dividend(currency: str) -> models.Dividend:
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return models.Dividend(
        id=7,
        symbol="VOD",
        market="LSE",
        amount=Decimal("1234.5000"),
        currency=currency,
        fx_rate_to_twd=Decimal("40.5"),
        ex_dividend_date=now,
        fee=Decimal("0"),
        tax=Decimal("0"),
        stock_dividend_shares=0,
        receipt_status="legacy_unknown",
        revision=1,
        created_at=now,
        updated_at=now,
    )


def test_dividend_schema_keeps_gbp_pence_token_on_output() -> None:
    assert DividendSchema.model_validate(_orm_dividend("GBp")).currency == "GBp"
    assert DividendSchema.model_validate(_orm_dividend(" GBp ")).currency == "GBp"


@pytest.mark.parametrize(("raw", "expected"), [("gbp", "GBP"), ("usd", "USD"), ("GBP", "GBP")])
def test_dividend_schema_uppercases_other_codes(raw: str, expected: str) -> None:
    assert DividendSchema.model_validate(_orm_dividend(raw)).currency == expected


def test_dividend_create_keeps_gbp_pence_token() -> None:
    payload = DividendCreate(
        symbol="VOD",
        market="LSE",
        amount=Decimal("1234.5000"),
        currency="GBp",
        fx_rate_to_twd=Decimal("40.5"),
        ex_dividend_date=datetime(2026, 3, 1),
    )
    assert payload.currency == "GBp"


def test_dividend_create_defaults_and_uppercases() -> None:
    payload = DividendCreate(
        symbol="2330",
        amount=Decimal("10"),
        currency="twd",
        ex_dividend_date=datetime(2026, 3, 1),
    )
    assert payload.currency == "TWD"


# ---------- summary ----------


class FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return date(2026, 6, 5)


def _lse_buy(symbol: str = "VOD", qty: str = "100") -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        market="LSE",
        name=symbol,
        type=models.TransactionType.BUY,
        quantity=Decimal(qty),
        price=Decimal("80"),
        currency="GBP",
        fx_rate_to_twd=Decimal("40"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 1, 1),
    )


def _lse_price(symbol: str = "VOD", close: str = "8050.0") -> PriceHistory:
    return PriceHistory(
        symbol=symbol,
        market="LSE",
        date=date(2026, 6, 5),
        close=Decimal(close),
        currency="GBp",
        source="yfinance",
    )


def _gbp_fx() -> FXRate:
    return FXRate(currency="GBP", date=date(2026, 6, 5), rate_to_twd=Decimal("40"), source="test")


def _lse_dividend(currency: str, amount: str = "1234.5000", symbol: str = "VOD") -> models.Dividend:
    return models.Dividend(
        symbol=symbol,
        market="LSE",
        amount=Decimal(amount),
        currency=currency,
        fx_rate_to_twd=Decimal("40.5"),
        ex_dividend_date=datetime(2026, 3, 1),
        received_date=datetime(2026, 3, 5),
    )


def _summary(db_session, monkeypatch):
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        return portfolio_service.get_portfolio_summary(db_session)


def test_summary_gbp_pence_dividend_valued_in_pounds_and_native_bucket_found(
    db_session, monkeypatch
) -> None:
    db_session.add_all([_lse_buy(), _lse_price(), _gbp_fx(), _lse_dividend("GBp")])
    db_session.commit()

    summary = _summary(db_session, monkeypatch)

    holding = summary.holdings[0]
    assert holding.native_currency == "GBp"
    assert holding.total_dividends == Decimal("499.97")
    assert summary.total_dividends == Decimal("499.97")
    assert holding.total_dividends_native == Decimal("1234.5000")
    # Market bucket keeps the GBp token, so the native total is no longer None.
    lse = summary.market_totals["LSE"]
    assert lse.currency == "GBp"
    assert lse.recorded_dividends == Decimal("1234.5000")


def test_summary_xirr_inflow_uses_divided_value(db_session, monkeypatch) -> None:
    db_session.add_all([_lse_buy(), _lse_price(), _gbp_fx(), _lse_dividend("GBp")])
    db_session.commit()

    captured: list[list[tuple[date, Decimal]]] = []
    real_xirr = portfolio_service._calculate_xirr

    def spy(cash_flows):
        captured.append(list(cash_flows))
        return real_xirr(cash_flows)

    monkeypatch.setattr(portfolio_service, "_calculate_xirr", spy)
    _summary(db_session, monkeypatch)

    inflows = [amt for flows in captured for (_d, amt) in flows if amt == Decimal("499.9725")]
    assert inflows, captured
    assert not [amt for flows in captured for (_d, amt) in flows if amt == Decimal("49997.25")]


def test_summary_gbp_pounds_control_row_not_divided(db_session, monkeypatch) -> None:
    db_session.add_all([_lse_buy(), _lse_price(), _gbp_fx(), _lse_dividend("GBP", amount="12.3450")])
    db_session.commit()

    summary = _summary(db_session, monkeypatch)

    holding = summary.holdings[0]
    assert holding.total_dividends == Decimal("499.97")  # 12.345 × 40.5, no ÷100
    # Native bucket is labelled GBP, the holding quotes in GBp → no native total.
    assert holding.total_dividends_native is None


def test_summary_mixed_gbp_and_gbp_pence_rows_have_no_native_total(
    db_session, monkeypatch
) -> None:
    db_session.add_all(
        [
            _lse_buy(),
            _lse_price(),
            _gbp_fx(),
            _lse_dividend("GBp"),
            _lse_dividend("GBP", amount="12.3450"),
        ]
    )
    db_session.commit()

    summary = _summary(db_session, monkeypatch)

    holding = summary.holdings[0]
    assert holding.total_dividends == Decimal("999.95")  # 499.9725 + 499.9725
    assert holding.total_dividends_native is None


# ---------- networth backfill ----------


def test_backfill_snapshot_after_gbp_pence_dividend_divides_by_100(db_session) -> None:
    d1, d2 = date(2026, 5, 14), date(2026, 5, 15)
    for d in (d1, d2):
        db_session.add(FXRate(currency="GBP", date=d, rate_to_twd=Decimal("41.0"), source="test"))
        db_session.add(
            PriceHistory(
                symbol="VOD", market="LSE", date=d, close=Decimal("180"),
                currency="GBp", source="yfinance",
            )
        )
    db_session.add(
        models.Transaction(
            symbol="VOD",
            market="LSE",
            type=models.TransactionType.BUY,
            position_side=models.PositionSide.LONG,
            quantity=100,
            price=Decimal("180"),
            currency="GBp",
            fx_rate_to_twd=Decimal("0.40"),
            trade_date=datetime(2026, 5, 14, tzinfo=timezone.utc),
            fee=Decimal("0"),
            tax=Decimal("0"),
        )
    )
    db_session.add(
        models.Dividend(
            symbol="VOD",
            market="LSE",
            amount=Decimal("1234.5000"),
            currency="GBp",
            fx_rate_to_twd=Decimal("40.5"),
            ex_dividend_date=datetime(2026, 5, 15, tzinfo=timezone.utc),
            fee=Decimal("0"),
            tax=Decimal("0"),
            stock_dividend_shares=0,
            source="test",
        )
    )
    db_session.commit()

    nbs.replay_snapshots_range(db_session, d1, d2)

    snaps = {s.date: s for s in db_session.query(PortfolioSnapshot).all()}
    assert snaps[d1].total_dividends == Decimal("0")
    # GBp transactions store fx per PENNY: cost basis 100 × 180 × 0.40 is
    # unchanged by the dividend divisor (same as test_replay_lse_gbp_uses_get_rate_divisor).
    assert snaps[d1].total_cost == Decimal("7200")
    assert snaps[d2].total_cost == Decimal("7200")
    assert snaps[d1].total_market_value == Decimal("7380")
    assert snaps[d2].total_dividends == nbs._snapshot_amount(Decimal("499.9725"))
    assert snaps[d2].total_dividends < Decimal("1000")  # not the ×100 pence value
