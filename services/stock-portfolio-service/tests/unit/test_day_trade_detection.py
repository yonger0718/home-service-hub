"""Day-trade auto-detection on create / update / delete.

A transaction is flagged ``is_day_trade=True`` when the same symbol has BOTH
a BUY and a SELL on the same calendar trade date. Every row in the bucket
shares the flag, so adding the second side flips both, and removing one side
flips the survivor back to False.
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.models import portfolio as models
from app.schemas import portfolio as schemas
from app.services import portfolio_service as svc


def _make_payload(
    *,
    symbol: str = "2330",
    tx_type: str = "BUY",
    quantity: int = 1000,
    price: str = "600.00",
    trade_date: datetime | None = None,
) -> schemas.TransactionCreate:
    return schemas.TransactionCreate(
        symbol=symbol,
        type=schemas.TransactionType(tx_type),
        quantity=quantity,
        price=Decimal(price),
        trade_date=trade_date or datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
    )


def test_single_buy_is_not_day_trade(db_session):
    buy = svc.create_transaction(db_session, _make_payload(tx_type="BUY"))
    assert buy.is_day_trade is False


def test_buy_then_sell_same_day_flips_both_flags(db_session):
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session,
        _make_payload(tx_type="BUY", trade_date=trade_day),
    )
    sell = svc.create_transaction(
        db_session,
        _make_payload(tx_type="SELL", trade_date=trade_day),
    )
    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is True
    assert sell.is_day_trade is True


def test_buy_and_sell_different_days_are_not_day_trade(db_session):
    day1 = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 16, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session, _make_payload(tx_type="BUY", trade_date=day1)
    )
    sell = svc.create_transaction(
        db_session, _make_payload(tx_type="SELL", trade_date=day2)
    )
    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_different_symbols_same_day_are_not_day_trade(db_session):
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session,
        _make_payload(symbol="2330", tx_type="BUY", trade_date=trade_day),
    )
    # Need a pre-existing BUY on 0050 for the SELL to be valid.
    svc.create_transaction(
        db_session,
        _make_payload(
            symbol="0050",
            tx_type="BUY",
            trade_date=datetime(2026, 5, 14, 1, 30, tzinfo=timezone.utc),
        ),
    )
    sell = svc.create_transaction(
        db_session,
        _make_payload(symbol="0050", tx_type="SELL", trade_date=trade_day),
    )
    db_session.refresh(buy)
    db_session.refresh(sell)
    assert buy.is_day_trade is False
    assert sell.is_day_trade is False


def test_delete_sell_clears_buy_flag(db_session):
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session, _make_payload(tx_type="BUY", trade_date=trade_day)
    )
    sell = svc.create_transaction(
        db_session, _make_payload(tx_type="SELL", trade_date=trade_day)
    )
    db_session.refresh(buy)
    assert buy.is_day_trade is True

    svc.delete_transaction(db_session, sell.id)
    db_session.refresh(buy)
    assert buy.is_day_trade is False


def test_update_moving_sell_to_different_day_clears_both_flags(db_session):
    day1 = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 16, 1, 30, tzinfo=timezone.utc)
    buy = svc.create_transaction(
        db_session, _make_payload(tx_type="BUY", trade_date=day1)
    )
    sell = svc.create_transaction(
        db_session, _make_payload(tx_type="SELL", trade_date=day1)
    )
    db_session.refresh(buy)
    assert buy.is_day_trade is True

    moved = svc.update_transaction(
        db_session,
        sell.id,
        _make_payload(tx_type="SELL", trade_date=day2),
    )
    db_session.refresh(buy)
    assert moved is not None
    assert buy.is_day_trade is False
    assert moved.is_day_trade is False


def test_update_moving_sell_into_existing_buy_day_flips_flags(db_session):
    day1 = datetime(2026, 5, 14, 1, 30, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    buy_day1 = svc.create_transaction(
        db_session, _make_payload(tx_type="BUY", trade_date=day1)
    )
    buy_day2 = svc.create_transaction(
        db_session, _make_payload(tx_type="BUY", trade_date=day2)
    )
    sell_day1 = svc.create_transaction(
        db_session, _make_payload(tx_type="SELL", trade_date=day1)
    )
    db_session.refresh(buy_day1)
    db_session.refresh(buy_day2)
    db_session.refresh(sell_day1)
    assert buy_day1.is_day_trade is True
    assert sell_day1.is_day_trade is True
    assert buy_day2.is_day_trade is False

    moved = svc.update_transaction(
        db_session,
        sell_day1.id,
        _make_payload(tx_type="SELL", trade_date=day2),
    )
    db_session.refresh(buy_day1)
    db_session.refresh(buy_day2)
    assert moved is not None
    assert buy_day1.is_day_trade is False
    assert buy_day2.is_day_trade is True
    assert moved.is_day_trade is True


def test_day_trade_exposed_in_get_transactions_response(client):
    trade_day = datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc)
    iso_day = trade_day.isoformat()
    client.post(
        "/api/portfolio/transactions",
        json={
            "symbol": "2330",
            "type": "BUY",
            "quantity": 1000,
            "price": "600.00",
            "trade_date": iso_day,
            "fee": "0.00",
            "tax": "0.00",
        },
    )
    client.post(
        "/api/portfolio/transactions",
        json={
            "symbol": "2330",
            "type": "SELL",
            "quantity": 1000,
            "price": "610.00",
            "trade_date": iso_day,
            "fee": "0.00",
            "tax": "0.00",
        },
    )

    response = client.get("/api/portfolio/transactions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert all(row["is_day_trade"] is True for row in body["items"])
