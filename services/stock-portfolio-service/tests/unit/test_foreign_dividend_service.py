from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.services import foreign_dividend_service


def _seed_tx(
    db_session,
    *,
    symbol: str,
    market: str,
    quantity: str,
    side: models.TransactionType = models.TransactionType.BUY,
) -> None:
    db_session.add(
        models.Transaction(
            symbol=symbol,
            market=market,
            type=side,
            quantity=Decimal(quantity),
            price=Decimal("100"),
            currency="USD",
            fx_rate_to_twd=Decimal("31"),
            fee=Decimal("0"),
            tax=Decimal("0"),
            trade_date=datetime(2026, 1, 1),
        )
    )


def _seed_fx(db_session, *dates: date) -> None:
    for d in dates:
        db_session.merge(
            FXRate(
                currency="USD",
                date=d,
                rate_to_twd=Decimal("31.50000000"),
                source="test",
            )
        )
    db_session.commit()


class FakeTicker:
    calls: list[str] = []
    failures: set[str] = set()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        FakeTicker.calls.append(symbol)
        if symbol in FakeTicker.failures:
            raise RuntimeError("yfinance down")
        self.fast_info = {"currency": "USD"}

    @property
    def dividends(self):
        if self.symbol == "AAPL":
            return pd.Series(
                [Decimal("0.24"), Decimal("0.25")],
                index=pd.to_datetime(["2026-05-15", "2026-02-14"]),
            )
        return pd.Series([Decimal("0.10")], index=pd.to_datetime(["2026-05-15"]))


def test_refresh_today_upserts_dividends_and_is_idempotent(db_session, monkeypatch) -> None:
    FakeTicker.calls = []
    FakeTicker.failures = set()
    monkeypatch.setattr(foreign_dividend_service.yf, "Ticker", FakeTicker)
    _seed_tx(db_session, symbol="AAPL", market="US", quantity="10")
    _seed_fx(db_session, date(2026, 5, 15), date(2026, 2, 14))

    first = foreign_dividend_service.refresh_today(db_session)
    second = foreign_dividend_service.refresh_today(db_session)

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert db_session.query(models.Dividend).count() == 2
    row = db_session.query(models.Dividend).filter_by(symbol="AAPL").first()
    assert row.currency == "USD"
    assert row.fx_rate_to_twd == Decimal("31.50000000")


def test_refresh_today_skips_missing_fx_and_isolates_ticker_failure(
    db_session, monkeypatch
) -> None:
    FakeTicker.calls = []
    FakeTicker.failures = {"MSFT"}
    monkeypatch.setattr(foreign_dividend_service.yf, "Ticker", FakeTicker)
    _seed_tx(db_session, symbol="AAPL", market="US", quantity="10")
    _seed_tx(db_session, symbol="MSFT", market="US", quantity="10")
    _seed_tx(db_session, symbol="VOD", market="LSE", quantity="10")
    _seed_fx(db_session, date(2026, 5, 15))

    result = foreign_dividend_service.refresh_today(db_session)

    assert result["inserted"] == 2
    assert result["skipped"] == 2
    assert db_session.query(models.Dividend).count() == 2
    assert "MSFT" in FakeTicker.calls
    assert "VOD.L" in FakeTicker.calls


def test_refresh_today_does_not_fetch_closed_positions(db_session, monkeypatch) -> None:
    FakeTicker.calls = []
    FakeTicker.failures = set()
    monkeypatch.setattr(foreign_dividend_service.yf, "Ticker", FakeTicker)
    _seed_tx(db_session, symbol="UUUU", market="US", quantity="10")
    _seed_tx(
        db_session,
        symbol="UUUU",
        market="US",
        quantity="10",
        side=models.TransactionType.SELL,
    )
    _seed_tx(db_session, symbol="2330", market="TW", quantity="10")

    result = foreign_dividend_service.refresh_today(db_session)

    assert result["requested"] == 0
    assert FakeTicker.calls == []
