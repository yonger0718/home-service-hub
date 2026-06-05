from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from app.models.price_history import PriceHistory
from app.services.quotes import yfinance_fetcher


def _df(
    close: str,
    *,
    open_: str = "1",
    high: str = "2",
    low: str = "1",
    volume: int = 100,
    day: date = date(2026, 6, 5),
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [Decimal(open_)],
            "High": [Decimal(high)],
            "Low": [Decimal(low)],
            "Close": [Decimal(close)],
            "Volume": [volume],
        },
        index=pd.to_datetime([day]),
    )


class _FakeTicker:
    metadata: dict[str, dict[str, object]] = {}

    def __init__(self, yf_symbol: str) -> None:
        self.yf_symbol = yf_symbol

    def get_history_metadata(self) -> dict[str, object]:
        value = self.metadata[self.yf_symbol]
        if isinstance(value, Exception):
            raise value
        return value


def test_us_ticker_fetched_without_suffix(monkeypatch) -> None:
    seen = []

    def fake_download(tickers, **kwargs):
        seen.extend(tickers)
        return pd.concat({"AAPL": _df("190.50")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"AAPL": {"currency": "USD", "regularMarketPrice": "190.50"}}

    rows, errors = yfinance_fetcher.fetch([("AAPL", "US")])

    assert seen == ["AAPL"]
    assert errors == []
    assert rows[0].symbol == "AAPL"
    assert rows[0].market == "US"


def test_lse_ticker_fetched_with_l_suffix(monkeypatch) -> None:
    seen = []

    def fake_download(tickers, **kwargs):
        seen.extend(tickers)
        return pd.concat({"VOD.L": _df("8050.0")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"VOD.L": {"currency": "GBp", "regularMarketPrice": "8050.0"}}

    rows, errors = yfinance_fetcher.fetch([("VOD", "LSE")])

    assert seen == ["VOD.L"]
    assert errors == []
    assert rows[0].currency == "GBp"
    assert rows[0].close == Decimal("8050.0")


def test_lse_ticker_with_existing_suffix_is_not_double_suffixed(monkeypatch) -> None:
    seen = []

    def fake_download(tickers, **kwargs):
        seen.extend(tickers)
        return pd.concat({"VOD.L": _df("8050.0")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"VOD.L": {"currency": "GBp", "regularMarketPrice": "8050.0"}}

    rows, errors = yfinance_fetcher.fetch([("VOD.L", "LSE")])

    assert seen == ["VOD.L"]
    assert errors == []
    assert rows[0].symbol == "VOD.L"


def test_get_quotes_reads_latest_price_history(db_session) -> None:
    db_session.add_all(
        [
            PriceHistory(
                symbol="AAPL",
                market="US",
                date=date(2026, 6, 4),
                close=Decimal("190.00"),
                currency="USD",
                source="yfinance",
            ),
            PriceHistory(
                symbol="AAPL",
                market="US",
                date=date(2026, 6, 5),
                close=Decimal("195.50"),
                currency="USD",
                source="yfinance",
            ),
        ]
    )
    db_session.commit()

    quotes = yfinance_fetcher.get_quotes(db_session, [("AAPL", "US")])

    assert quotes[("AAPL", "US")]["close"] == Decimal("195.5000")
    assert quotes[("AAPL", "US")]["currency"] == "USD"


def test_gbp_minor_unit_is_persisted_verbatim(db_session, monkeypatch) -> None:
    def fake_download(tickers, **kwargs):
        return pd.concat({"VOD.L": _df("8050.0")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"VOD.L": {"currency": "GBp", "regularMarketPrice": "8050.0"}}

    result = yfinance_fetcher.refresh_daily_ohlc(db_session, [("VOD", "LSE")])

    assert result.ok_count == 1
    row = db_session.query(PriceHistory).one()
    assert row.symbol == "VOD"
    assert row.market == "LSE"
    assert row.close == Decimal("8050.0000")
    assert row.currency == "GBp"
    assert row.source == "yfinance"


def test_missing_currency_skips_ticker_without_writing(db_session, monkeypatch) -> None:
    def fake_download(tickers, **kwargs):
        return pd.concat({"AAPL": _df("190.50")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"AAPL": {"regularMarketPrice": "190.50"}}

    result = yfinance_fetcher.refresh_daily_ohlc(db_session, [("AAPL", "US")])

    assert result.ok_count == 0
    assert result.skipped_count == 1
    assert "missing currency" in result.errors[0]
    assert db_session.query(PriceHistory).count() == 0


def test_missing_regular_market_price_skips_ticker(monkeypatch) -> None:
    def fake_download(tickers, **kwargs):
        return pd.concat({"AAPL": _df("190.50")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {"AAPL": {"currency": "USD"}}

    rows, errors = yfinance_fetcher.fetch([("AAPL", "US")])

    assert rows == []
    assert "regularMarketPrice" in errors[0]


def test_one_bad_ticker_does_not_abort_siblings(monkeypatch) -> None:
    def fake_download(tickers, **kwargs):
        return pd.concat({"AAPL": _df("190.50"), "ZZZZ": _df("1.0")}, axis=1)

    monkeypatch.setattr(yfinance_fetcher.yf, "download", fake_download)
    monkeypatch.setattr(yfinance_fetcher.yf, "Ticker", _FakeTicker)
    _FakeTicker.metadata = {
        "AAPL": {"currency": "USD", "regularMarketPrice": "190.50"},
        "ZZZZ": RuntimeError("not found"),
    }

    rows, errors = yfinance_fetcher.fetch([("AAPL", "US"), ("ZZZZ", "US")])

    assert [row.symbol for row in rows] == ["AAPL"]
    assert len(errors) == 1
    assert "ZZZZ" in errors[0]
