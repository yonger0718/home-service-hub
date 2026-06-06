from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.services import broker_firstrade_service, import_service


FIXTURE = Path(__file__).parent / "fixtures/firstrade_sample.csv"


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


def test_parse_firstrade_sample_emits_equity_and_cash_rows() -> None:
    parsed = broker_firstrade_service.parse(FIXTURE.read_bytes())

    assert parsed.errors == []
    assert len([row for row in parsed.rows if row.payload["_kind"] == "transaction"]) == 8
    assert len([row for row in parsed.rows if row.payload["_kind"] == "cash_flow"]) == 3
    buy = parsed.rows[0].payload
    assert buy["broker"] == "FIRSTRADE"
    assert buy["market"] == "US"
    assert buy["currency"] == "USD"
    assert buy["symbol"] == "UUUU"
    assert buy["type"] == "BUY"
    assert buy["quantity"] == Decimal("10")
    assert buy["price"] == Decimal("15.65")
    assert buy["fee"] == Decimal("0")
    assert "account_class" not in buy
    sell = next(
        row.payload
        for row in parsed.rows
        if row.payload["_kind"] == "transaction" and row.payload["type"] == "SELL"
    )
    assert sell["quantity"] == Decimal("27")
    assert sell["price"] == Decimal("18.71")
    deposit = next(row.payload for row in parsed.rows if row.payload.get("cash_flow_type") == "deposit")
    assert deposit["amount"] == Decimal("2500.00")
    interest = next(row.payload for row in parsed.rows if row.payload.get("cash_flow_type") == "interest")
    assert interest["amount"] == Decimal("0.05")


def test_firstrade_commit_populates_fx_and_is_idempotent(db_session) -> None:
    _seed_usd_fx(
        db_session,
        date(2026, 4, 16),
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 18),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    )
    parsed = broker_firstrade_service.parse(FIXTURE.read_bytes())

    first = import_service.commit_transactions(db_session, parsed, dry_run=False)
    second = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert first.errors == []
    assert first.created == 11
    assert second.created == 0
    assert second.skipped_duplicates == 11
    tx = db_session.query(models.Transaction).filter_by(symbol="UUUU").first()
    assert tx.broker == "FIRSTRADE"
    assert tx.fx_rate_to_twd == Decimal("31.42000000")
    assert db_session.query(models.BrokerCashFlow).count() == 3


def test_firstrade_missing_fx_rejects_only_that_row(db_session) -> None:
    _seed_usd_fx(db_session, date(2026, 6, 5))
    raw = (
        '"日期","交易類別","數量","說明","代號","賬戶類別","價格","金額"\n'
        '"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"\n'
        '"2026/6/4","買進","27","Energy Fuels Inc","UUUU","融資","17.99","-485.73"\n'
    ).encode("utf-8")
    parsed = broker_firstrade_service.parse(raw)

    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.created == 1
    assert len(result.errors) == 1
    assert result.errors[0].row_index == 2
    assert "missing FX rate for 2026-06-04 USD" in result.errors[0].message
