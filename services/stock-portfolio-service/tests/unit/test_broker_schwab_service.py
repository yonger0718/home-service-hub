from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.services import broker_schwab_service, import_service


FIXTURE = Path(__file__).parent / "fixtures/schwab_sample.csv"


def _seed_usd_fx(db_session, *dates: date) -> None:
    for d in dates:
        db_session.merge(
            FXRate(
                currency="USD",
                date=d,
                rate_to_twd=Decimal("31.42000000"),
                source="test",
            )
        )
    db_session.commit()


def test_parse_schwab_sample_emits_wire_and_buy() -> None:
    parsed = broker_schwab_service.parse(FIXTURE.read_bytes())

    assert parsed.errors == []
    cash = parsed.rows[0].payload
    buy = parsed.rows[1].payload
    assert cash["broker"] == "SCHWAB"
    assert cash["cash_flow_type"] == "deposit"
    assert cash["amount"] == Decimal("1500.00")
    assert buy["broker"] == "SCHWAB"
    assert buy["symbol"] == "AAPL"
    assert buy["market"] == "US"
    assert buy["currency"] == "USD"
    assert buy["quantity"] == Decimal("10")
    assert buy["price"] == Decimal("190.50")
    assert buy["fee"] == Decimal("0.00")


def test_schwab_commit_populates_fx_and_is_idempotent(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 4))
    parsed = broker_schwab_service.parse(FIXTURE.read_bytes())

    first = import_service.commit_transactions(db_session, parsed, dry_run=False)
    second = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert first.created == 2
    assert first.errors == []
    assert second.created == 0
    assert second.skipped_duplicates == 2
    assert db_session.query(models.Transaction).one().broker == "SCHWAB"
    assert db_session.query(models.BrokerCashFlow).one().fx_rate_to_twd == Decimal("31.42000000")


def test_schwab_missing_fx_rejects_transaction(db_session) -> None:
    parsed = broker_schwab_service.parse(FIXTURE.read_bytes())

    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.created == 0
    assert len(result.errors) == 2
    assert "missing FX rate for 2026-06-04 USD" in result.errors[0].message
