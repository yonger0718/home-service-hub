"""Integration coverage for auto-recording dividends with mixed position sides."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.portfolio import Dividend, PositionSide, Transaction, TransactionType
from app.services.dividend_auto_record_service import auto_record_for_event
from app.services.dividend_history_service import HistoricalDividendEvent


TW = timezone(timedelta(hours=8))


def _add_trade(
    db,
    *,
    symbol: str,
    qty: int,
    trade_date: date,
    type_: TransactionType,
    position_side: PositionSide,
) -> None:
    db.add(
        Transaction(
            symbol=symbol,
            type=type_,
            position_side=position_side,
            quantity=qty,
            price=Decimal("100.00"),
            trade_date=datetime.combine(trade_date, datetime.min.time(), tzinfo=TW),
            fee=Decimal("0"),
            tax=Decimal("0"),
            is_day_trade=False,
        )
    )
    db.flush()


def test_auto_record_uses_long_qty_when_long_and_short_exist(db_session):
    ex_date = date(2026, 6, 15)
    _add_trade(
        db_session,
        symbol="2330",
        qty=1000,
        trade_date=date(2026, 5, 1),
        type_=TransactionType.BUY,
        position_side=PositionSide.LONG,
    )
    _add_trade(
        db_session,
        symbol="2330",
        qty=500,
        trade_date=date(2026, 5, 10),
        type_=TransactionType.SELL,
        position_side=PositionSide.SHORT,
    )
    event = HistoricalDividendEvent(
        symbol="2330",
        ex_date=ex_date,
        cash_dividend_per_share=Decimal("2.0"),
        stock_dividend_per_thousand=None,
        previous_close=None,
        reference_price=None,
        source="TWT49U",
    )

    auto_record_for_event(db_session, event)

    row = db_session.query(Dividend).one()
    assert row.quantity_at_record_date == Decimal("1000")
    assert row.amount == Decimal("1990.00")
