from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.services import broker_ib_service, import_service


FIXTURE = Path(__file__).parent / "fixtures/ib_sample.csv"


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


def test_parse_ib_sample_reads_base_currency_equity_fee_and_deposit() -> None:
    parsed = broker_ib_service.parse(FIXTURE.read_bytes())

    assert parsed.errors == []
    transactions = [row for row in parsed.rows if row.payload["_kind"] == "transaction"]
    cash_flows = [row for row in parsed.rows if row.payload["_kind"] == "cash_flow"]
    assert len(transactions) == 3
    assert len(cash_flows) == 1
    first = transactions[0].payload
    assert first["broker"] == "IB"
    assert first["symbol"] == "ACWD"
    assert first["quantity"] == Decimal("1.0")
    assert first["price"] == Decimal("325.05")
    assert first["currency"] == "USD"
    assert first["fee"] == Decimal("1.78")
    assert first["market"] == "US"
    deposit = cash_flows[0].payload
    assert deposit["cash_flow_type"] == "deposit"
    assert deposit["currency"] == "USD"
    assert deposit["amount"] == Decimal("3000.0")


def test_ib_gbp_row_infers_lse_market() -> None:
    raw = (
        "Statement,Header,域名稱,域值\n"
        "總結,Header,域名稱,域值\n"
        "總結,Data,基礎貨幣,USD\n"
        "轉賬歷史,Header,日期,賬戶,說明,交易類型,代碼,交易量,價格,Price Currency,總額,佣金,淨金額\n"
        "轉賬歷史,Data,2026-06-02,U,VOD trade,買,VOD,2,70.5,GBP,-141,-1,-142\n"
    ).encode("utf-8")

    parsed = broker_ib_service.parse(raw)

    assert parsed.errors == []
    assert parsed.rows[0].payload["market"] == "LSE"
    assert parsed.rows[0].payload["currency"] == "GBP"


def test_ib_commit_populates_fx_and_rejects_missing_fx(db_session) -> None:
    _seed_fx(db_session, "USD", date(2026, 6, 1), date(2026, 6, 2))
    parsed = broker_ib_service.parse(FIXTURE.read_bytes())

    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.errors == []
    assert result.created == 4
    assert db_session.query(models.Transaction).count() == 3
    assert db_session.query(models.BrokerCashFlow).count() == 1

    raw_missing = (
        "Statement,Header,域名稱,域值\n"
        "總結,Header,域名稱,域值\n"
        "總結,Data,基礎貨幣,USD\n"
        "轉賬歷史,Header,日期,賬戶,說明,交易類型,代碼,交易量,價格,Price Currency,總額,佣金,淨金額\n"
        "轉賬歷史,Data,2026-06-03,U,AAPL trade,買,AAPL,1,100,USD,-100,-1,-101\n"
    ).encode("utf-8")
    missing = import_service.commit_transactions(
        db_session, broker_ib_service.parse(raw_missing), dry_run=False
    )
    assert missing.created == 0
    assert "missing FX rate for 2026-06-03 USD" in missing.errors[0].message
