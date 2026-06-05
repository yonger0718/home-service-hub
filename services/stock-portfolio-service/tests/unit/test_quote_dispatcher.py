from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.price_history import PriceHistory
from app.services.quotes import dispatcher
from app.services.quotes.fx_rate_service import RefreshResult


@dataclass
class _Backend:
    refresh_calls: list
    quote_calls: list

    def refresh_daily_ohlc(self, db, items):
        self.refresh_calls.append(list(items))
        return RefreshResult(ok_count=len(items), skipped_count=0, errors=[])

    def get_quotes(self, db, items):
        self.quote_calls.append(list(items))
        return {(symbol, market): {"symbol": symbol, "market": market} for symbol, market in items}


def test_mixed_batch_dispatches_to_tw_and_foreign_backends(monkeypatch) -> None:
    tw = _Backend([], [])
    yf = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    monkeypatch.setattr(dispatcher, "yfinance_backend", yf)

    result = dispatcher.refresh_daily_ohlc(object(), [("2330", "TW"), ("AAPL", "US")])

    assert result.ok_count == 2
    assert tw.refresh_calls == [[("2330", "TW")]]
    assert yf.refresh_calls == [[("AAPL", "US")]]


def test_tw_only_batch_never_calls_yfinance(monkeypatch) -> None:
    tw = _Backend([], [])
    yf = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    monkeypatch.setattr(dispatcher, "yfinance_backend", yf)

    dispatcher.refresh_daily_ohlc(object(), [("2330", "TW")])

    assert tw.refresh_calls == [[("2330", "TW")]]
    assert yf.refresh_calls == []


def test_foreign_only_batch_never_calls_twse(monkeypatch) -> None:
    tw = _Backend([], [])
    yf = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    monkeypatch.setattr(dispatcher, "yfinance_backend", yf)

    dispatcher.refresh_daily_ohlc(object(), [("AAPL", "US"), ("VOD", "LSE")])

    assert tw.refresh_calls == []
    assert yf.refresh_calls == [[("AAPL", "US")], [("VOD", "LSE")]]


def test_unknown_market_is_skipped_and_reported(monkeypatch) -> None:
    tw = _Backend([], [])
    yf = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    monkeypatch.setattr(dispatcher, "yfinance_backend", yf)

    result = dispatcher.refresh_daily_ohlc(object(), [("7203", "JP")])

    assert result.ok_count == 0
    assert result.skipped_count == 1
    assert "JP" in result.errors[0]
    assert tw.refresh_calls == []
    assert yf.refresh_calls == []


def test_bare_symbol_get_quotes_defaults_to_tw(monkeypatch) -> None:
    tw = _Backend([], [])
    yf = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    monkeypatch.setattr(dispatcher, "yfinance_backend", yf)

    quotes = dispatcher.get_quotes(object(), ["2330"])

    assert quotes == {("2330", "TW"): {"symbol": "2330", "market": "TW"}}
    assert tw.quote_calls == [[("2330", "TW")]]
    assert yf.quote_calls == []


def test_mixed_get_quotes_dispatches_tw_and_yfinance_without_attribute_error(
    db_session,
    monkeypatch,
) -> None:
    tw = _Backend([], [])
    monkeypatch.setattr(dispatcher, "twse_backend", tw)
    db_session.add(
        PriceHistory(
            symbol="AAPL",
            market="US",
            date=date(2026, 6, 5),
            close=Decimal("195.50"),
            currency="USD",
            source="yfinance",
        )
    )
    db_session.commit()

    quotes = dispatcher.get_quotes(db_session, [("AAPL", "US"), ("2330", "TW")])

    assert quotes[("AAPL", "US")]["close"] == Decimal("195.5000")
    assert quotes[("AAPL", "US")]["currency"] == "USD"
    assert quotes[("2330", "TW")] == {"symbol": "2330", "market": "TW"}
    assert tw.quote_calls == [[("2330", "TW")]]
