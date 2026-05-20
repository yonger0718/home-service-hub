from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import portfolio as models
from app.services import realized_pnl_service


def _tx(
    *,
    symbol: str,
    side: models.TransactionType,
    quantity: int,
    price: str,
    trade_date: datetime,
    position_side: models.PositionSide = models.PositionSide.LONG,
    name: str | None = None,
    fee: str = "0.00",
    tax: str = "0.00",
    is_day_trade: bool = False,
) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        name=name,
        type=side,
        position_side=position_side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal(fee),
        tax=Decimal(tax),
        trade_date=trade_date,
        is_day_trade=is_day_trade,
    )


def _seed(db_session, rows: list[models.Transaction]) -> None:
    db_session.add_all(rows)
    db_session.commit()


def test_short_sell_alone_emits_no_event(db_session) -> None:
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            )
        ],
    )

    events = realized_pnl_service.compute_events(db_session)

    assert events == []


def test_short_round_trip_zero_fee_realizes_inverted_gain(db_session) -> None:
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=400,
                price="80.00",
                trade_date=datetime(2025, 5, 5, 9, 0),
            ),
        ],
    )

    [event] = realized_pnl_service.compute_events(db_session)

    assert event.position_side == models.PositionSide.SHORT
    assert event.quantity == 400
    assert event.avg_cost_at_sale == Decimal("100")
    assert event.cost_out == Decimal("32000")
    assert event.realized_pnl == Decimal("8000")
    assert event.note is None


def test_partial_short_cover_leaves_residual_inventory(db_session) -> None:
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=400,
                price="80.00",
                trade_date=datetime(2025, 5, 5, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=300,
                price="70.00",
                trade_date=datetime(2025, 5, 6, 9, 0),
            ),
        ],
    )

    events = realized_pnl_service.compute_events(
        db_session, sort="trade_date:asc"
    )

    assert len(events) == 2
    assert events[0].quantity == 400
    assert events[0].realized_pnl == Decimal("8000")
    assert events[1].quantity == 300
    assert events[1].realized_pnl == Decimal("9000")


def test_short_cover_with_no_open_short_flags_no_short_inventory(db_session) -> None:
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=100,
                price="50.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            )
        ],
    )

    [event] = realized_pnl_service.compute_events(db_session)

    assert event.position_side == models.PositionSide.SHORT
    assert event.note == "no_short_inventory"
    assert event.cost_out == Decimal("5000")
    assert event.realized_pnl == Decimal("-5000")


def test_long_and_short_pools_are_independent_same_symbol(db_session) -> None:
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.LONG,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 1, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.LONG,
                quantity=600,
                price="140.00",
                trade_date=datetime(2025, 2, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=500,
                price="150.00",
                trade_date=datetime(2025, 3, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=500,
                price="120.00",
                trade_date=datetime(2025, 4, 1, 9, 0),
            ),
        ],
    )

    events = realized_pnl_service.compute_events(
        db_session, sort="trade_date:asc"
    )

    assert len(events) == 2
    long_event, short_event = events
    assert long_event.position_side == models.PositionSide.LONG
    assert long_event.realized_pnl == Decimal("24000")
    assert short_event.position_side == models.PositionSide.SHORT
    assert short_event.realized_pnl == Decimal("15000")


def test_short_with_fees_subtract_open_proceeds_net(db_session) -> None:
    # 券賣 open: gross 100*1000=100000, fee 63, tax 300 → net 99637
    # 券買 cover: gross 80*1000=80000, fee 22, no tax → cost_total 80022
    # realized = 99637 - 80022 = 19615
    _seed(
        db_session,
        [
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="100.00",
                fee="63.00",
                tax="300.00",
                trade_date=datetime(2025, 5, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                position_side=models.PositionSide.SHORT,
                quantity=1000,
                price="80.00",
                fee="22.00",
                trade_date=datetime(2025, 5, 10, 9, 0),
            ),
        ],
    )

    [event] = realized_pnl_service.compute_events(db_session)

    assert event.position_side == models.PositionSide.SHORT
    assert event.cost_out == Decimal("80022")
    assert event.proceeds_net == Decimal("99637")
    assert event.realized_pnl == Decimal("19615")
