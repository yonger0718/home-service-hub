"""Router tests for PATCH /api/portfolio/dividends/{id}/deductions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models.portfolio import Dividend
from app.routers import portfolio as portfolio_router

TW = timezone(timedelta(hours=8))
BASE = "/api/portfolio/dividends"


def _dividend(db, *, status="pending", revision=1) -> Dividend:
    row = Dividend(
        symbol="2330", market="TW", currency="TWD",
        amount=Decimal("9590"), fee=Decimal("10"), tax=Decimal("0"),
        quantity_at_record_date=Decimal("20000"), cash_dividend_per_share=Decimal("0.48"),
        stock_dividend_shares=0, ex_dividend_date=datetime(2026, 7, 1, tzinfo=TW), received_date=None,
        receipt_status=status, revision=revision, source="auto:TWT49U", entitlement_key=f"key-{status}",
    )
    db.add(row)
    db.commit()
    return row


def _reason(resp) -> str:
    """HTTPException detail as served: shared_lib.errors wraps it as {code, message, trace_id}."""
    body = resp.json()
    assert body["code"] == resp.status_code
    return body["message"]


def _patch(client, dividend_id, **body):
    payload = {"fee": "0", "tax": "0", "revision": 1}
    payload.update(body)
    return client.patch(f"{BASE}/{dividend_id}/deductions", json=payload)


def test_updates_deductions_and_returns_dividend(client, db_session):
    div = _dividend(db_session)

    resp = client.patch(f"{BASE}/{div.id}/deductions", json={"fee": 0, "tax": 0, "revision": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == div.id
    assert Decimal(body["amount"]) == Decimal("9600")
    assert Decimal(body["fee"]) == Decimal("0")
    assert Decimal(body["tax"]) == Decimal("0")
    assert body["revision"] == 2
    db_session.rollback()
    assert db_session.get(Dividend, div.id).amount == Decimal("9600")


def test_stale_revision_is_409_with_service_text(client, db_session):
    div = _dividend(db_session, revision=2)

    resp = _patch(client, div.id, revision=1)

    assert resp.status_code == 409
    assert _reason(resp) == "entitlement changed; reload before editing"
    db_session.rollback()
    row = db_session.get(Dividend, div.id)
    assert (row.amount, row.fee, row.revision) == (Decimal("9590"), Decimal("10"), 2)


@pytest.mark.parametrize("body", [{"fee": -1}, {"tax": "-0.01"}, {"revision": 0}])
def test_negative_or_invalid_values_are_rejected_by_schema(client, db_session, body):
    div = _dividend(db_session)

    resp = _patch(client, div.id, **body)

    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)  # pydantic validation errors


def test_more_than_two_decimal_places_is_rejected_by_schema(client, db_session):
    div = _dividend(db_session)

    resp = client.patch(f"{BASE}/{div.id}/deductions", json={"fee": 1.005, "tax": 0, "revision": 1})

    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)


def test_deductions_exceeding_gross_are_422_with_string_detail(client, db_session):
    div = _dividend(db_session)

    resp = _patch(client, div.id, fee="9000", tax="600.01")

    assert resp.status_code == 422
    assert _reason(resp) == "deductions exceed the gross dividend"
    db_session.rollback()
    assert db_session.get(Dividend, div.id).revision == 1


def test_unknown_dividend_is_404(client, db_session):
    resp = _patch(client, 999)

    assert resp.status_code == 404
    assert _reason(resp) == "dividend not found"


def test_legacy_row_is_409(client, db_session):
    div = _dividend(db_session, status="legacy_unknown")

    resp = _patch(client, div.id)

    assert resp.status_code == 409
    assert _reason(resp) == "legacy or unresolved dividend requires explicit resolution"


@pytest.mark.parametrize("exc,detail", [
    (OperationalError("UPDATE dividends", {}, Exception("database is locked")),
     "Concurrent edit or database contention; reload before retry"),
    (IntegrityError("UPDATE dividends", {}, Exception("CHECK constraint failed")),
     "deductions conflict with the ledger; reload"),
])
def test_database_errors_are_409_with_fixed_text_and_rollback(client, db_session, monkeypatch, exc, detail):
    def boom(*args, **kwargs):
        raise exc

    rollbacks = []
    real_rollback = db_session.rollback
    monkeypatch.setattr(portfolio_router, "update_deductions", boom)
    monkeypatch.setattr(db_session, "rollback", lambda: (rollbacks.append(True), real_rollback())[1])

    resp = _patch(client, 1)

    assert resp.status_code == 409
    assert _reason(resp) == detail
    assert rollbacks
