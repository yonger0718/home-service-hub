"""Active-date optimization for the networth backfill path."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.models import portfolio as portfolio_models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import networth_backfill_service as nbs


def _seed_tx(
    db: Any,
    *,
    symbol: str,
    side: portfolio_models.TransactionType,
    qty: int,
    trade_date: date,
) -> None:
    db.add(
        portfolio_models.Transaction(
            symbol=symbol,
            type=side,
            quantity=qty,
            price=Decimal("10"),
            trade_date=datetime.combine(
                trade_date, datetime.min.time(), tzinfo=timezone.utc
            ),
            fee=Decimal("0"),
            tax=Decimal("0"),
        )
    )
    db.flush()


def _seed_dividend(
    db: Any,
    *,
    symbol: str,
    ex_date: date,
    stock_shares: int,
) -> None:
    db.add(
        portfolio_models.Dividend(
            symbol=symbol,
            amount=Decimal("1"),
            ex_dividend_date=datetime.combine(
                ex_date, datetime.min.time(), tzinfo=timezone.utc
            ),
            fee=Decimal("0"),
            tax=Decimal("0"),
            stock_dividend_shares=stock_shares,
            source="test",
        )
    )
    db.flush()


def _seed_price(db: Any, *, symbol: str, d: date, source: str = "TWSE") -> None:
    db.add(
        PriceHistory(
            symbol=symbol,
            date=d,
            close=Decimal("10"),
            source=source,
        )
    )
    db.flush()


def test_compute_active_dates_closed_position_is_inclusive(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=1000,
        trade_date=date(2022, 1, 3),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=1000,
        trade_date=date(2022, 1, 5),
    )

    assert nbs.compute_active_dates(
        db_session, date(2022, 1, 1), date(2022, 1, 10)
    ) == {
        date(2022, 1, 3),
        date(2022, 1, 4),
        date(2022, 1, 5),
    }


def test_compute_active_dates_open_position_extends_to_range_end(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="0050",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 6, 3),
    )

    assert nbs.compute_active_dates(
        db_session, date(2024, 6, 1), date(2024, 6, 7)
    ) == {
        date(2024, 6, 3),
        date(2024, 6, 4),
        date(2024, 6, 5),
        date(2024, 6, 6),
        date(2024, 6, 7),
    }


def test_compute_active_dates_same_day_buy_sell_is_single_day(db_session: Any) -> None:
    d = date(2026, 5, 15)
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=500,
        trade_date=d,
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=500,
        trade_date=d,
    )

    assert nbs.compute_active_dates(
        db_session, date(2026, 5, 14), date(2026, 5, 18)
    ) == {d}


def test_compute_active_dates_short_position_counts_as_held(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2026, 5, 14),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2026, 5, 18),
    )

    assert nbs.compute_active_dates(
        db_session, date(2026, 5, 14), date(2026, 5, 18)
    ) == {
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
    }


def test_compute_active_dates_unions_overlapping_symbols(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 1, 15),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2024, 1, 17),
    )
    _seed_tx(
        db_session,
        symbol="0050",
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        trade_date=date(2024, 1, 16),
    )

    assert nbs.compute_active_dates(
        db_session, date(2024, 1, 15), date(2024, 1, 19)
    ) == {
        date(2024, 1, 15),
        date(2024, 1, 16),
        date(2024, 1, 17),
        date(2024, 1, 18),
        date(2024, 1, 19),
    }


def test_compute_active_dates_stock_dividend_opens_interval(db_session: Any) -> None:
    _seed_dividend(
        db_session,
        symbol="2330",
        ex_date=date(2026, 5, 14),
        stock_shares=10,
    )

    assert nbs.compute_active_dates(
        db_session, date(2026, 5, 14), date(2026, 5, 15)
    ) == {
        date(2026, 5, 14),
        date(2026, 5, 15),
    }


def test_compute_active_dates_excludes_weekends(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2026, 5, 15),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2026, 5, 18),
    )

    assert nbs.compute_active_dates(
        db_session, date(2026, 5, 15), date(2026, 5, 18)
    ) == {
        date(2026, 5, 15),
        date(2026, 5, 18),
    }


def test_compute_active_dates_can_include_non_trading_dates(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 9, 13),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2024, 9, 16),
    )

    assert nbs.compute_active_dates(
        db_session,
        date(2024, 9, 13),
        date(2024, 9, 16),
        include_non_trading=True,
    ) == {
        date(2024, 9, 13),
        date(2024, 9, 14),
        date(2024, 9, 15),
        date(2024, 9, 16),
    }


def test_compute_active_dates_default_stays_weekday_only(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 9, 13),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2024, 9, 16),
    )

    assert nbs.compute_active_dates(
        db_session, date(2024, 9, 13), date(2024, 9, 16)
    ) == {
        date(2024, 9, 13),
        date(2024, 9, 16),
    }


def test_compute_active_dates_calendar_interval_ends_on_closing_sell(
    db_session: Any,
) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        trade_date=date(2024, 9, 12),
    )
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.SELL,
        qty=100,
        trade_date=date(2024, 9, 13),
    )

    assert nbs.compute_active_dates(
        db_session,
        date(2024, 9, 12),
        date(2024, 9, 16),
        include_non_trading=True,
    ) == {
        date(2024, 9, 12),
        date(2024, 9, 13),
    }


def test_compute_active_dates_empty_portfolio_returns_empty_set(db_session: Any) -> None:
    assert (
        nbs.compute_active_dates(
            db_session, date(2026, 5, 14), date(2026, 5, 18)
        )
        == set()
    )


def test_backfill_prices_range_skips_inactive_weekdays(db_session: Any) -> None:
    calls: list[date] = []
    active = date(2026, 5, 15)

    def _fetch(d: date) -> list[Any]:
        calls.append(d)
        return []

    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 11),
        active,
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=_fetch,
        tpex_fetcher=_fetch,
        active_dates={active},
    )

    assert calls == [active, active, active, active, active, active]
    assert result.dates_inactive == 4
    assert db_session.query(PriceHistory).count() == 0


def test_backfill_prices_range_none_preserves_legacy_behavior(db_session: Any) -> None:
    _seed_price(db_session, symbol="2330", d=date(2026, 5, 14), source="TWSE")
    _seed_price(db_session, symbol="6488", d=date(2026, 5, 14), source="TPEx")
    db_session.commit()

    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 14),
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=lambda _d: [],
        tpex_fetcher=lambda _d: [],
        active_dates=None,
    )

    assert result.dates_skipped == 1
    assert result.dates_inactive == 0


def test_replay_snapshots_range_skips_inactive_dates(db_session: Any) -> None:
    active = date(2026, 5, 15)
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        trade_date=date(2026, 5, 11),
    )
    for d in (
        date(2026, 5, 11),
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
        active,
    ):
        _seed_price(db_session, symbol="2330", d=d)
    db_session.commit()

    result = nbs.replay_snapshots_range(
        db_session,
        date(2026, 5, 11),
        active,
        active_dates={active},
    )

    assert result.dates_inactive == 4
    assert db_session.query(PortfolioSnapshot).count() == 1
    assert db_session.query(PortfolioSnapshot).one().date == active


def test_replay_snapshots_range_keeps_existing_inactive_rows(db_session: Any) -> None:
    inactive = date(2026, 5, 14)
    active = date(2026, 5, 15)
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        trade_date=inactive,
    )
    _seed_price(db_session, symbol="2330", d=inactive)
    _seed_price(db_session, symbol="2330", d=active)
    db_session.add(
        PortfolioSnapshot(
            date=inactive,
            total_market_value=Decimal("0"),
            total_cost=Decimal("0"),
            total_unrealized_pnl=Decimal("0"),
            total_dividends=Decimal("0"),
            total_realized_pnl=Decimal("0"),
            portfolio_xirr=None,
        )
    )
    db_session.commit()

    nbs.replay_snapshots_range(
        db_session,
        inactive,
        active,
        active_dates={active},
    )

    dates = {row.date for row in db_session.query(PortfolioSnapshot).all()}
    assert dates == {inactive, active}


def test_replay_snapshots_range_none_preserves_weekday_replay(db_session: Any) -> None:
    _seed_tx(
        db_session,
        symbol="2330",
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        trade_date=date(2026, 5, 14),
    )
    _seed_price(db_session, symbol="2330", d=date(2026, 5, 14))
    _seed_price(db_session, symbol="2330", d=date(2026, 5, 15))
    db_session.commit()

    result = nbs.replay_snapshots_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 15),
        active_dates=None,
    )

    assert result.snapshots_written == 2
    assert result.dates_inactive == 0
