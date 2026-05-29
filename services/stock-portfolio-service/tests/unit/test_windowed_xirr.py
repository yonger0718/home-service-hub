from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from app.models import portfolio as models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import portfolio_service


class FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return date(2026, 5, 29)


def _dt(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time())


def _buy(symbol: str, trade_date: date, qty: int = 100) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        name=symbol,
        type=models.TransactionType.BUY,
        quantity=qty,
        price=Decimal("10"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=_dt(trade_date),
    )


def _sell(symbol: str, trade_date: date, qty: int = 20) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        name=symbol,
        type=models.TransactionType.SELL,
        quantity=qty,
        price=Decimal("12"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=_dt(trade_date),
    )


def _snapshot(snapshot_date: date, market_value: str = "1000") -> PortfolioSnapshot:
    return PortfolioSnapshot(
        date=snapshot_date,
        total_market_value=Decimal(market_value),
        total_cost=Decimal("900"),
        total_unrealized_pnl=Decimal("100"),
        total_dividends=Decimal("0"),
        total_realized_pnl=Decimal("0"),
        portfolio_xirr=None,
    )


def _price(symbol: str, price_date: date, close: str = "10") -> PriceHistory:
    return PriceHistory(
        symbol=symbol,
        date=price_date,
        close=Decimal(close),
        source="TWSE",
    )


def _seed_base_position(db_session) -> None:
    db_session.add(_buy("0050", date(2025, 1, 10), qty=100))
    db_session.add(_buy("0050", date(2026, 4, 29), qty=10))
    db_session.commit()


def _seed_all_window_support(db_session) -> None:
    for window_date in (
        date(2026, 4, 29),
        date(2026, 2, 28),
        date(2025, 5, 29),
        date(2026, 1, 1),
    ):
        db_session.add(_snapshot(window_date))
        db_session.add(_price("0050", window_date))
    db_session.commit()


def _quote() -> dict[str, dict[str, Decimal | str]]:
    return {
        "0050": {
            "symbol": "0050",
            "name": "0050",
            "current_price": Decimal("12"),
            "yesterday_close": Decimal("11"),
            "time": "13:30:00",
        }
    }


def test_window_start_uses_calendar_windows() -> None:
    today = date(2026, 5, 29)

    assert portfolio_service._window_start(today, "1m") == date(2026, 4, 29)
    assert portfolio_service._window_start(today, "3m") == date(2026, 2, 28)
    assert portfolio_service._window_start(today, "1y") == date(2025, 5, 29)
    assert portfolio_service._window_start(today, "ytd") == date(2026, 1, 1)


def test_calculate_windowed_xirr_includes_edge_date_cashflows() -> None:
    captured = {}

    def fake_xirr(cashflows):
        captured["cashflows"] = cashflows
        return Decimal("0.123456")

    window_start = date(2026, 4, 29)
    today = date(2026, 5, 29)
    cashflows = [
        (date(2026, 4, 28), Decimal("-999")),
        (window_start, Decimal("-10")),
        (date(2026, 5, 15), Decimal("5")),
        (today, Decimal("20")),
        (date(2026, 5, 30), Decimal("999")),
    ]

    with patch.object(portfolio_service, "_calculate_xirr", side_effect=fake_xirr):
        result = portfolio_service._calculate_windowed_xirr(
            window_start,
            today,
            cashflows,
            Decimal("1000"),
            Decimal("1100"),
        )

    assert result == Decimal("0.123456")
    assert captured["cashflows"] == [
        (window_start, Decimal("-1000")),
        (window_start, Decimal("-10")),
        (date(2026, 5, 15), Decimal("5")),
        (today, Decimal("20")),
        (today, Decimal("1100")),
    ]


def test_quantity_at_window_start_replays_prior_long_transactions_only() -> None:
    window_start = date(2026, 4, 29)
    transactions = [
        _buy("0050", date(2026, 4, 1), qty=100),
        _sell("0050", date(2026, 4, 15), qty=20),
        _buy("0050", window_start, qty=50),
        _buy("2330", date(2026, 4, 1), qty=999),
    ]

    assert (
        portfolio_service._quantity_at_window_start(transactions, "0050", window_start)
        == Decimal("80")
    )


def test_opening_price_lookup_uses_exact_or_previous_trading_day_within_7_days(db_session) -> None:
    window_start = date(2026, 4, 29)
    db_session.add_all(
        [
            _price("0050", window_start - portfolio_service._ONE_DAY * 8, "9"),
            _price("0050", window_start - portfolio_service._ONE_DAY * 6, "11"),
            _price("0050", window_start, "12"),
        ]
    )
    db_session.commit()

    assert (
        portfolio_service._lookup_window_open_price(db_session, "0050", window_start)
        == Decimal("12.0000")
    )

    db_session.query(PriceHistory).filter(PriceHistory.date == window_start).delete()
    db_session.commit()
    assert (
        portfolio_service._lookup_window_open_price(db_session, "0050", window_start)
        == Decimal("11.0000")
    )

    db_session.query(PriceHistory).filter(
        PriceHistory.date == window_start - portfolio_service._ONE_DAY * 6
    ).delete()
    db_session.commit()
    assert portfolio_service._lookup_window_open_price(db_session, "0050", window_start) is None


@patch("app.services.portfolio_service.get_stock_quotes")
def test_summary_populates_all_windowed_xirr_fields_when_snapshots_and_prices_exist(
    mock_get_quotes, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    _seed_base_position(db_session)
    _seed_all_window_support(db_session)
    mock_get_quotes.return_value = _quote()

    summary = portfolio_service.get_portfolio_summary(db_session)
    holding = summary.holdings[0]

    assert summary.portfolio_xirr_1m is not None
    assert summary.portfolio_xirr_3m is not None
    assert summary.portfolio_xirr_1y is not None
    assert summary.portfolio_xirr_ytd is not None
    assert holding.xirr_1m is not None
    assert holding.xirr_3m is not None
    assert holding.xirr_1y is not None
    assert holding.xirr_ytd is not None
    assert summary.portfolio_xirr is not None
    assert holding.xirr is not None


@patch("app.services.portfolio_service.get_stock_quotes")
def test_summary_returns_none_for_windows_missing_snapshot_or_price(
    mock_get_quotes, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    _seed_base_position(db_session)
    db_session.add(_snapshot(date(2026, 4, 29)))
    db_session.add(_price("0050", date(2026, 4, 29)))
    db_session.commit()
    mock_get_quotes.return_value = _quote()

    summary = portfolio_service.get_portfolio_summary(db_session)
    holding = summary.holdings[0]

    assert summary.portfolio_xirr_1m is not None
    assert summary.portfolio_xirr_3m is None
    assert summary.portfolio_xirr_1y is None
    assert summary.portfolio_xirr_ytd is None
    assert holding.xirr_1m is not None
    assert holding.xirr_3m is None
    assert holding.xirr_1y is None
    assert holding.xirr_ytd is None


@patch("app.services.portfolio_service.get_stock_quotes")
def test_per_stock_window_omits_opening_outflow_when_holding_opens_inside_window(
    mock_get_quotes, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(portfolio_service, "date_type", FrozenDate)
    db_session.add(_buy("0050", date(2026, 5, 1), qty=100))
    db_session.add(_snapshot(date(2026, 4, 29)))
    db_session.commit()
    mock_get_quotes.return_value = _quote()

    summary = portfolio_service.get_portfolio_summary(db_session)

    assert summary.holdings[0].xirr_1m is not None
