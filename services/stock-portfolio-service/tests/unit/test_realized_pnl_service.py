from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models import portfolio as models
from app.models.corporate_action import CorporateAction
from app.services import realized_pnl_service


def _tx(
    *,
    symbol: str,
    side: models.TransactionType,
    quantity: int,
    price: str,
    trade_date: datetime,
    name: str | None = None,
    fee: str = "0.00",
    tax: str = "0.00",
    is_day_trade: bool = False,
) -> models.Transaction:
    return models.Transaction(
        symbol=symbol,
        name=name,
        type=side,
        quantity=quantity,
        price=Decimal(price),
        fee=Decimal(fee),
        tax=Decimal(tax),
        trade_date=trade_date,
        is_day_trade=is_day_trade,
    )


def _seed_transactions(db_session, rows: list[models.Transaction]) -> None:
    db_session.add_all(rows)
    db_session.commit()


def test_multi_buy_sell_uses_moving_average_and_net_proceeds(db_session) -> None:
    _seed_transactions(
        db_session,
        [
            _tx(
                symbol="2330",
                name="台積電",
                side=models.TransactionType.BUY,
                quantity=1000,
                price="100.00",
                trade_date=datetime(2025, 1, 1, 9, 0),
            ),
            _tx(
                symbol="2330",
                name="台積電",
                side=models.TransactionType.BUY,
                quantity=500,
                price="130.00",
                trade_date=datetime(2025, 1, 2, 9, 0),
            ),
            _tx(
                symbol="2330",
                name="台積電",
                side=models.TransactionType.SELL,
                quantity=600,
                price="140.00",
                fee="85.00",
                tax="255.00",
                trade_date=datetime(2025, 1, 3, 9, 0),
                is_day_trade=True,
            ),
        ],
    )

    events = realized_pnl_service.compute_events(db_session)

    assert len(events) == 1
    event = events[0]
    assert event.trade_date == date(2025, 1, 3)
    assert event.symbol == "2330"
    assert event.name == "台積電"
    assert event.quantity == 600
    assert event.sell_price == Decimal("140.00")
    assert event.avg_cost_at_sale == Decimal("110.00")
    assert event.fee == Decimal("85.00")
    assert event.tax == Decimal("255.00")
    assert event.proceeds_gross == Decimal("84000.00")
    assert event.proceeds_net == Decimal("83660.00")
    assert event.cost_out == Decimal("66000.00")
    assert event.realized_pnl == Decimal("17660.00")
    assert event.is_day_trade is True
    assert event.note is None


def test_sell_spanning_corporate_action_split_uses_adjusted_view(db_session) -> None:
    db_session.add_all(
        [
            _tx(
                symbol="2330",
                name="台積電",
                side=models.TransactionType.BUY,
                quantity=1,
                price="600.00",
                trade_date=datetime(2026, 1, 1, 9, 0),
            ),
            CorporateAction(
                symbol="2330",
                effective_date=date(2026, 2, 1),
                ratio=Decimal("10"),
                source="TWSE",
                source_event_key="2330_2026-02-01",
            ),
            _tx(
                symbol="2330",
                name="台積電",
                side=models.TransactionType.SELL,
                quantity=5,
                price="70.00",
                trade_date=datetime(2026, 3, 1, 9, 0),
            ),
        ]
    )
    db_session.commit()

    [event] = realized_pnl_service.compute_events(db_session)

    assert event.quantity == 5
    assert event.avg_cost_at_sale == Decimal("60.00")
    assert event.cost_out == Decimal("300.00")
    assert event.proceeds_net == Decimal("350.00")
    assert event.realized_pnl == Decimal("50.00")


def test_no_inventory_sell_is_emitted_and_flagged(db_session) -> None:
    _seed_transactions(
        db_session,
        [
            _tx(
                symbol="9999",
                side=models.TransactionType.SELL,
                quantity=100,
                price="50.00",
                trade_date=datetime(2025, 3, 1, 9, 0),
            )
        ],
    )

    [event] = realized_pnl_service.compute_events(db_session)

    assert event.symbol == "9999"
    assert event.quantity == 100
    assert event.cost_out == Decimal("0")
    assert event.realized_pnl == Decimal("5000.00")
    assert event.note == "no_long_inventory"


def test_filters_and_sorts_events(db_session) -> None:
    _seed_transactions(
        db_session,
        [
            _tx(
                symbol="2330",
                side=models.TransactionType.BUY,
                quantity=100,
                price="100.00",
                trade_date=datetime(2024, 12, 31, 9, 0),
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=10,
                price="150.00",
                trade_date=datetime(2025, 1, 15, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.BUY,
                quantity=100,
                price="50.00",
                trade_date=datetime(2025, 2, 1, 9, 0),
            ),
            _tx(
                symbol="6488",
                side=models.TransactionType.SELL,
                quantity=10,
                price="70.00",
                trade_date=datetime(2025, 2, 2, 9, 0),
                is_day_trade=True,
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=10,
                price="130.00",
                trade_date=datetime(2026, 1, 1, 9, 0),
            ),
        ],
    )

    assert [e.symbol for e in realized_pnl_service.compute_events(db_session, symbol="2330")] == [
        "2330",
        "2330",
    ]
    assert [
        e.trade_date
        for e in realized_pnl_service.compute_events(
            db_session,
            date_from=date(2025, 2, 1),
            date_to=date(2025, 12, 31),
        )
    ] == [date(2025, 2, 2)]
    assert [
        e.trade_date for e in realized_pnl_service.compute_events(db_session, year=2026)
    ] == [date(2026, 1, 1)]
    assert [
        e.symbol for e in realized_pnl_service.compute_events(db_session, day_trade_only=True)
    ] == ["6488"]
    assert [
        e.realized_pnl
        for e in realized_pnl_service.compute_events(db_session, sort="realized_pnl:asc")
    ] == [Decimal("200.00"), Decimal("300.00"), Decimal("500.00")]
    assert [
        e.trade_date
        for e in realized_pnl_service.compute_events(db_session, sort="trade_date:asc")
    ] == [date(2025, 1, 15), date(2025, 2, 2), date(2026, 1, 1)]


def test_symbol_filter_uses_prefix_match(db_session) -> None:
    _seed_transactions(
        db_session,
        [
            _tx(
                symbol="0050",
                side=models.TransactionType.BUY,
                quantity=100,
                price="100.00",
                trade_date=datetime(2025, 1, 1, 9, 0),
            ),
            _tx(
                symbol="0050",
                side=models.TransactionType.SELL,
                quantity=10,
                price="120.00",
                trade_date=datetime(2025, 1, 2, 9, 0),
            ),
            _tx(
                symbol="0056",
                side=models.TransactionType.BUY,
                quantity=100,
                price="30.00",
                trade_date=datetime(2025, 1, 3, 9, 0),
            ),
            _tx(
                symbol="0056",
                side=models.TransactionType.SELL,
                quantity=10,
                price="35.00",
                trade_date=datetime(2025, 1, 4, 9, 0),
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.BUY,
                quantity=100,
                price="500.00",
                trade_date=datetime(2025, 1, 5, 9, 0),
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=10,
                price="600.00",
                trade_date=datetime(2025, 1, 6, 9, 0),
            ),
        ],
    )

    # "00" matches every 00xxx ETF (0050 + 0056), excludes 2330
    assert sorted(
        {e.symbol for e in realized_pnl_service.compute_events(db_session, symbol="00")}
    ) == ["0050", "0056"]
    # "005" still matches both
    assert sorted(
        {e.symbol for e in realized_pnl_service.compute_events(db_session, symbol="005")}
    ) == ["0050", "0056"]
    # "0050" narrows to single ticker
    assert {
        e.symbol for e in realized_pnl_service.compute_events(db_session, symbol="0050")
    } == {"0050"}
    # "2" matches only 2330
    assert {
        e.symbol for e in realized_pnl_service.compute_events(db_session, symbol="2")
    } == {"2330"}


def test_explicit_date_range_takes_precedence_over_year(db_session) -> None:
    _seed_transactions(
        db_session,
        [
            _tx(
                symbol="2330",
                side=models.TransactionType.BUY,
                quantity=100,
                price="100.00",
                trade_date=datetime(2024, 12, 31, 9, 0),
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=10,
                price="150.00",
                trade_date=datetime(2025, 1, 15, 9, 0),
            ),
            _tx(
                symbol="2330",
                side=models.TransactionType.SELL,
                quantity=10,
                price="130.00",
                trade_date=datetime(2026, 1, 1, 9, 0),
            ),
        ],
    )

    events = realized_pnl_service.compute_events(
        db_session,
        year=2025,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
    )

    assert [event.trade_date for event in events] == [date(2026, 1, 1)]
