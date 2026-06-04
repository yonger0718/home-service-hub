from datetime import datetime, timezone
from decimal import Decimal

from app.models import portfolio as models


def test_transaction_price_and_fractional_quantity_round_trip(db_session):
    tx = models.Transaction(
        symbol="AAPL",
        market="US",
        name="Apple Inc",
        type=models.TransactionType.BUY,
        quantity=Decimal("0.5"),
        price=Decimal("234.5678"),
        currency="USD",
        fx_rate_to_twd=Decimal("32.50000000"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)

    assert tx.price == Decimal("234.5678")
    assert tx.quantity == Decimal("0.5000")
