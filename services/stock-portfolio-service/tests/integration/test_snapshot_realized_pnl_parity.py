"""Snapshot replay realized-PnL parity with the canonical event engine."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.models import portfolio as portfolio_models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services.networth_backfill_service import replay_snapshots_range
from app.services.portfolio_service import _load_adjusted_transactions
from app.services.realized_pnl_service import iter_realized_events


_TOLERANCE = Decimal("0.01")


def _at(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def _seed_tx(
    db: Any,
    *,
    symbol: str,
    tx_type: portfolio_models.TransactionType,
    quantity: int,
    price: str,
    trade_date: date,
    position_side: portfolio_models.PositionSide = portfolio_models.PositionSide.LONG,
    fee: str = "0",
    tax: str = "0",
    is_day_trade: bool = False,
) -> None:
    db.add(
        portfolio_models.Transaction(
            symbol=symbol,
            type=tx_type,
            position_side=position_side,
            quantity=quantity,
            price=Decimal(price),
            trade_date=_at(trade_date),
            fee=Decimal(fee),
            tax=Decimal(tax),
            is_day_trade=is_day_trade,
        )
    )
    db.flush()


def _seed_price(db: Any, *, symbol: str, d: date, close: str = "100") -> None:
    db.add(
        PriceHistory(
            symbol=symbol,
            date=d,
            close=Decimal(close),
            source="TWSE",
        )
    )
    db.flush()


def test_snapshot_realized_pnl_matches_engine_for_mixed_histories(
    db_session: Any,
) -> None:
    start = date(2026, 5, 11)
    end = date(2026, 5, 18)

    # LONG round-trip.
    _seed_tx(
        db_session,
        symbol="1101",
        tx_type=portfolio_models.TransactionType.BUY,
        quantity=100,
        price="100",
        trade_date=date(2026, 5, 11),
    )
    _seed_tx(
        db_session,
        symbol="1101",
        tx_type=portfolio_models.TransactionType.SELL,
        quantity=100,
        price="120",
        trade_date=date(2026, 5, 12),
    )

    # SHORT 融券 round-trip.
    _seed_tx(
        db_session,
        symbol="2202",
        tx_type=portfolio_models.TransactionType.SELL,
        quantity=100,
        price="50",
        trade_date=date(2026, 5, 13),
        position_side=portfolio_models.PositionSide.SHORT,
    )
    _seed_tx(
        db_session,
        symbol="2202",
        tx_type=portfolio_models.TransactionType.BUY,
        quantity=100,
        price="40",
        trade_date=date(2026, 5, 14),
        position_side=portfolio_models.PositionSide.SHORT,
    )

    # Day-trade 沖賣 pair.
    _seed_tx(
        db_session,
        symbol="3303",
        tx_type=portfolio_models.TransactionType.SELL,
        quantity=50,
        price="80",
        trade_date=date(2026, 5, 15),
        position_side=portfolio_models.PositionSide.SHORT,
        tax="6",
        is_day_trade=True,
    )
    _seed_tx(
        db_session,
        symbol="3303",
        tx_type=portfolio_models.TransactionType.BUY,
        quantity=50,
        price="70",
        trade_date=date(2026, 5, 15),
        position_side=portfolio_models.PositionSide.SHORT,
        is_day_trade=True,
    )

    # Oversell.
    _seed_tx(
        db_session,
        symbol="4404",
        tx_type=portfolio_models.TransactionType.BUY,
        quantity=100,
        price="10",
        trade_date=date(2026, 5, 18),
    )
    _seed_tx(
        db_session,
        symbol="4404",
        tx_type=portfolio_models.TransactionType.SELL,
        quantity=150,
        price="20",
        trade_date=date(2026, 5, 18),
    )

    for d in (
        date(2026, 5, 11),
        date(2026, 5, 12),
        date(2026, 5, 13),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
    ):
        _seed_price(db_session, symbol="1101", d=d)
        _seed_price(db_session, symbol="2202", d=d)
        _seed_price(db_session, symbol="3303", d=d)
        _seed_price(db_session, symbol="4404", d=d)
    db_session.commit()

    replay_snapshots_range(db_session, start, end)

    events = list(iter_realized_events(_load_adjusted_transactions(db_session)))
    snapshots = {
        snapshot.date: snapshot
        for snapshot in db_session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.date >= start, PortfolioSnapshot.date <= end)
        .all()
    }
    assert snapshots

    for snapshot_date, snapshot in snapshots.items():
        expected = sum(
            (
                event.realized_pnl
                for event in events
                if event.trade_date <= snapshot_date
            ),
            Decimal("0"),
        )
        assert abs(Decimal(snapshot.total_realized_pnl) - expected) <= _TOLERANCE
