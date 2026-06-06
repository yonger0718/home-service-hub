from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from app.models import portfolio as models
from app.models.fx_rate import FXRate


FIXTURES = Path(__file__).parents[1] / "unit/fixtures"


def _seed_fx(db_session, currency: str, *dates: date) -> None:
    for d in dates:
        db_session.merge(
            FXRate(
                currency=currency,
                date=d,
                rate_to_twd=Decimal("31.42000000") if currency == "USD" else Decimal("42.00000000"),
                source="test",
            )
        )
    db_session.commit()


def _upload(client, filename: str):
    raw = (FIXTURES / filename).read_bytes()
    return client.post(
        "/api/portfolio/imports/csv",
        files={"file": (filename, BytesIO(raw), "text/csv")},
    )


def test_broker_csv_imports_stamp_broker_cash_flows_and_dedupe(client, db_session) -> None:
    _seed_fx(
        db_session,
        "USD",
        date(2026, 4, 16),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    )

    for filename, broker in [
        ("ib_sample.csv", "IB"),
        ("firstrade_sample.csv", "FIRSTRADE"),
        ("schwab_sample.csv", "SCHWAB"),
    ]:
        response = _upload(client, filename)
        assert response.status_code == 200
        body = response.json()
        assert body["created"] > 0
        assert body["errors"] == []
        assert db_session.query(models.Transaction).filter_by(broker=broker).count() > 0
        assert db_session.query(models.BrokerCashFlow).filter_by(broker=broker).count() > 0

        second = _upload(client, filename)
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["created"] == 0
        assert second_body["skipped_duplicates"] == body["created"]


def test_broker_cash_flow_endpoint_returns_one_row_per_active_broker(
    client, db_session
) -> None:
    _seed_fx(
        db_session,
        "USD",
        date(2026, 4, 16),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    )
    for filename in ("ib_sample.csv", "firstrade_sample.csv", "schwab_sample.csv"):
        assert _upload(client, filename).status_code == 200

    response = client.get("/api/portfolio/broker-cash-flows")

    assert response.status_code == 200
    brokers = {row["broker"] for row in response.json()}
    assert brokers == {"IB", "FIRSTRADE", "SCHWAB"}
