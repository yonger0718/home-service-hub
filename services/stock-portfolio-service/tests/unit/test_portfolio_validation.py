from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import portfolio as models


def _valid_transaction_payload() -> dict:
    return {
        "symbol": "0050.tw",
        "name": "元大台灣50",
        "type": "BUY",
        "quantity": 10,
        "price": "100.00",
        "trade_date": "2026-01-01T09:00:00",
        "fee": "0.00",
        "tax": "0.00",
    }


def _valid_dividend_payload() -> dict:
    return {
        "symbol": "0056.two",
        "amount": "25.00",
        "ex_dividend_date": "2026-02-01T09:00:00",
        "received_date": "2026-02-10T09:00:00",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "   "),
        ("quantity", 0),
        ("price", "0.00"),
        ("fee", "-1.00"),
        ("tax", "-1.00"),
    ],
)
def test_create_transaction_rejects_invalid_payload_without_writes(client, db_session, field, value):
    payload = _valid_transaction_payload()
    payload[field] = value

    response = client.post("/api/portfolio/transactions", json=payload)

    assert response.status_code == 422
    assert db_session.query(models.Transaction).count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "   "),
        ("quantity", 0),
        ("price", "0.00"),
        ("fee", "-1.00"),
        ("tax", "-1.00"),
    ],
)
def test_update_transaction_rejects_invalid_payload_without_writes(client, db_session, field, value):
    transaction = models.Transaction(
        symbol="0050",
        name="元大台灣50",
        type=models.TransactionType.BUY,
        quantity=10,
        price=Decimal("100.00"),
        fee=Decimal("0.00"),
        tax=Decimal("0.00"),
        trade_date=datetime(2026, 1, 1, 9, 0),
    )
    db_session.add(transaction)
    db_session.commit()

    payload = _valid_transaction_payload()
    payload[field] = value

    response = client.put(f"/api/portfolio/transactions/{transaction.id}", json=payload)

    assert response.status_code == 422
    db_session.refresh(transaction)
    assert transaction.symbol == "0050"
    assert transaction.quantity == 10
    assert transaction.price == Decimal("100.00")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "   "),
        ("amount", "0.00"),
    ],
)
def test_create_dividend_rejects_invalid_payload_without_writes(client, db_session, field, value):
    payload = _valid_dividend_payload()
    payload[field] = value

    response = client.post("/api/portfolio/dividends", json=payload)

    assert response.status_code == 422
    assert db_session.query(models.Dividend).count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "   "),
        ("amount", "0.00"),
    ],
)
def test_update_dividend_rejects_invalid_payload_without_writes(client, db_session, field, value):
    dividend = models.Dividend(
        symbol="0056",
        amount=Decimal("25.00"),
        ex_dividend_date=datetime(2026, 2, 1, 9, 0),
        received_date=datetime(2026, 2, 10, 9, 0),
    )
    db_session.add(dividend)
    db_session.commit()

    payload = _valid_dividend_payload()
    payload[field] = value

    response = client.put(f"/api/portfolio/dividends/{dividend.id}", json=payload)

    assert response.status_code == 422
    db_session.refresh(dividend)
    assert dividend.symbol == "0056"
    assert dividend.amount == Decimal("25.00")


def test_transaction_db_constraints_reject_invalid_rows(db_session):
    transaction = models.Transaction(
        symbol="   ",
        name="Invalid",
        type=models.TransactionType.BUY,
        quantity=0,
        price=Decimal("0.00"),
        fee=Decimal("-1.00"),
        tax=Decimal("-1.00"),
        trade_date=datetime(2026, 1, 1, 9, 0),
    )

    db_session.add(transaction)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_response_schema_tolerates_high_precision_decimal(db_session, client):
    """Defense-in-depth: if DB ever contains a Decimal with >2 decimal places
    (despite NUMERIC(12,2) currently preventing it), the response endpoint
    must still serialize without raising ValidationError.
    """
    transaction = models.Transaction(
        symbol="0050",
        name="元大台灣50",
        type=models.TransactionType.BUY,
        quantity=10,
        price=Decimal("100.1234"),
        fee=Decimal("1.5678"),
        tax=Decimal("0.0001"),
        trade_date=datetime(2026, 1, 1, 9, 0),
    )
    db_session.add(transaction)
    db_session.commit()

    response = client.get("/api/portfolio/transactions")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] and body["items"][0]["symbol"] == "0050"


def test_dividend_db_constraints_reject_invalid_rows(db_session):
    dividend = models.Dividend(
        symbol="   ",
        amount=Decimal("0.00"),
        ex_dividend_date=datetime(2026, 2, 1, 9, 0),
        received_date=datetime(2026, 2, 10, 9, 0),
    )

    db_session.add(dividend)
    with pytest.raises(IntegrityError):
        db_session.commit()