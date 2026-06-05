from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from app.models.fx_rate import FXRate
from app.models import portfolio as models
from app.models.price_history import PriceHistory
from app.services import portfolio_service


class FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return date(2026, 6, 5)


def _buy(symbol: str, market: str, price: str, fx: str, qty: str = "10") -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        market=market,
        name=symbol,
        type=models.TransactionType.BUY,
        quantity=Decimal(qty),
        price=Decimal(price),
        currency="USD" if market == "US" else "GBP",
        fx_rate_to_twd=Decimal(fx),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 1, 1),
    )


def _price(symbol: str, market: str, close: str, currency: str) -> PriceHistory:
    return PriceHistory(
        symbol=symbol,
        market=market,
        date=date(2026, 6, 5),
        close=Decimal(close),
        currency=currency,
        source="yfinance",
    )


def _fx(currency: str, rate: str) -> FXRate:
    return FXRate(
        currency=currency,
        date=date(2026, 6, 5),
        rate_to_twd=Decimal(rate),
        source="test",
    )


def test_us_holding_revalues_at_live_fx(db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add_all([_buy("AAPL", "US", "100", "30"), _price("AAPL", "US", "110", "USD"), _fx("USD", "32")])
    db_session.commit()

    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        summary = portfolio_service.get_portfolio_summary(db_session)

    holding = summary.holdings[0]
    assert holding.market_value == Decimal("35200.00")
    assert holding.current_price == Decimal("3520.00000000")
    assert holding.avg_cost == Decimal("3000.00")
    assert holding.unrealized_pnl == Decimal("5200.00")
    assert holding.native_close == Decimal("110.0000")
    assert holding.native_currency == "USD"
    assert holding.live_fx_rate_to_twd == Decimal("32.00000000")


def test_lse_gbp_minor_unit_divides_by_100_then_applies_gbp_rate(db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add_all([_buy("VOD", "LSE", "80", "40", qty="100"), _price("VOD", "LSE", "8050.0", "GBp"), _fx("GBP", "40")])
    db_session.commit()

    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        summary = portfolio_service.get_portfolio_summary(db_session)

    holding = summary.holdings[0]
    assert holding.market_value == Decimal("322000.00")
    assert holding.native_close == Decimal("8050.0000")
    assert holding.native_currency == "GBp"
    assert holding.live_fx_rate_to_twd == Decimal("40.00000000")


def test_lse_usd_quoted_holding_uses_usd_rate(db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    tx = _buy("RIO", "LSE", "10", "31", qty="2")
    tx.currency = "USD"
    db_session.add_all([tx, _price("RIO", "LSE", "12", "USD"), _fx("USD", "32")])
    db_session.commit()

    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        summary = portfolio_service.get_portfolio_summary(db_session)

    assert summary.holdings[0].market_value == Decimal("768.00")
    assert summary.holdings[0].live_fx_rate_to_twd == Decimal("32.00000000")


def test_missing_fx_sets_market_value_none_and_partial_status(db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add_all([_buy("AAPL", "US", "100", "30"), _price("AAPL", "US", "110", "USD")])
    db_session.commit()

    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        summary = portfolio_service.get_portfolio_summary(db_session)

    assert summary.holdings[0].market_value is None
    assert summary.quotes_status == "unavailable"


def test_missing_price_sets_market_value_none_and_partial_status(db_session, monkeypatch) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add_all([_buy("AAPL", "US", "100", "30"), _fx("USD", "32")])
    db_session.commit()

    with patch("app.services.portfolio_service.get_stock_quotes", return_value={}):
        summary = portfolio_service.get_portfolio_summary(db_session)

    assert summary.holdings[0].market_value is None
    assert summary.quotes_status == "unavailable"


def test_tw_only_portfolio_summary_is_byte_equal(db_session) -> None:
    db_session.add(
        models.Transaction(
            symbol="0050",
            market="TW",
            name="0050",
            type=models.TransactionType.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
            fee=Decimal("0"),
            tax=Decimal("0"),
            trade_date=datetime(2026, 1, 1),
        )
    )
    db_session.commit()
    quotes = {
        "0050": {
            "symbol": "0050",
            "name": "0050",
            "current_price": Decimal("120"),
            "yesterday_close": Decimal("119"),
        }
    }

    with patch("app.services.portfolio_service.get_stock_quotes", return_value=quotes):
        before = portfolio_service.get_portfolio_summary(db_session).model_dump_json()
    with patch("app.services.portfolio_service.get_stock_quotes", return_value=quotes):
        after = portfolio_service.get_portfolio_summary(db_session).model_dump_json()

    assert after == before
