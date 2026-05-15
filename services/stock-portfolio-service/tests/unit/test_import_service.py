"""CSV importer: parsing, fingerprint stability, dedupe, dry-run, day-trade interaction."""

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

import pytest

from app.models import portfolio as models
from app.services import import_service


TX_HEADER = "symbol,type,quantity,price,trade_date,fee,tax,name\n"
DIV_HEADER = "symbol,amount,ex_dividend_date,received_date\n"


def _tx_csv(*rows: str) -> bytes:
    return (TX_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _div_csv(*rows: str) -> bytes:
    return (DIV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def test_parse_transactions_happy_path():
    raw = _tx_csv(
        "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電",
        "0050.TW,SELL,5,140.50,2026-05-15T02:00:00Z,5,14,",
    )
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    assert parsed.rows[0].payload["symbol"] == "2330"
    assert parsed.rows[0].payload["price"] == Decimal("600.00")
    assert parsed.rows[0].payload["trade_date"] == datetime(
        2026, 5, 15, 1, 30, tzinfo=timezone.utc
    )
    assert parsed.rows[1].payload["symbol"] == "0050"  # .TW stripped
    assert parsed.rows[1].payload["name"] is None
    assert parsed.rows[0].fingerprint != parsed.rows[1].fingerprint


def test_parse_transactions_rejects_bad_header():
    bad = b"sym,type,qty\n2330,BUY,10\n"
    with pytest.raises(ValueError, match="header"):
        import_service.parse_transactions_csv(bad)


@pytest.mark.parametrize(
    ("row", "needle"),
    [
        (",BUY,10,600,2026-05-15T01:30:00Z,0,0,", "'symbol'"),
        ("2330,HOLD,10,600,2026-05-15T01:30:00Z,0,0,", "BUY or SELL"),
        ("2330,BUY,0,600,2026-05-15T01:30:00Z,0,0,", "positive"),
        ("2330,BUY,abc,600,2026-05-15T01:30:00Z,0,0,", "integer"),
        ("2330,BUY,10,-1,2026-05-15T01:30:00Z,0,0,", "positive"),
        ("2330,BUY,10,xyz,2026-05-15T01:30:00Z,0,0,", "decimal"),
        ("2330,BUY,10,600,not-a-date,0,0,", "ISO 8601"),
        ("2330,BUY,10,600,2026-05-15T01:30:00Z,-1,0,", "non-negative"),
    ],
)
def test_parse_transactions_row_errors(row, needle):
    parsed = import_service.parse_transactions_csv(_tx_csv(row))
    assert parsed.rows == []
    assert len(parsed.errors) == 1
    assert needle in parsed.errors[0].message


def test_fingerprint_stable_across_invocations():
    row = "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電"
    fp1 = import_service.parse_transactions_csv(_tx_csv(row)).rows[0].fingerprint
    fp2 = import_service.parse_transactions_csv(_tx_csv(row)).rows[0].fingerprint
    assert fp1 == fp2


def test_fingerprint_changes_when_price_changes():
    row_a = "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電"
    row_b = "2330,BUY,10,601.00,2026-05-15T01:30:00Z,28,0,台積電"
    fp_a = import_service.parse_transactions_csv(_tx_csv(row_a)).rows[0].fingerprint
    fp_b = import_service.parse_transactions_csv(_tx_csv(row_b)).rows[0].fingerprint
    assert fp_a != fp_b


def test_commit_transactions_inserts_rows(db_session):
    raw = _tx_csv("2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    parsed = import_service.parse_transactions_csv(raw)
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.parsed == 1
    assert result.created == 1
    assert result.skipped_duplicates == 0
    assert result.errors == []
    persisted = db_session.query(models.Transaction).one()
    assert persisted.symbol == "2330"
    assert persisted.import_fingerprint == parsed.rows[0].fingerprint


def test_commit_transactions_dry_run_writes_nothing(db_session):
    raw = _tx_csv("2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    parsed = import_service.parse_transactions_csv(raw)
    result = import_service.commit_transactions(db_session, parsed, dry_run=True)

    assert result.dry_run is True
    assert result.parsed == 1
    assert result.created == 0
    assert db_session.query(models.Transaction).count() == 0


def test_commit_transactions_dedupes_on_second_upload(db_session):
    raw = _tx_csv("2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    parsed = import_service.parse_transactions_csv(raw)
    import_service.commit_transactions(db_session, parsed, dry_run=False)
    result2 = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result2.created == 0
    assert result2.skipped_duplicates == 1
    assert db_session.query(models.Transaction).count() == 1


def test_commit_transactions_dedupes_duplicates_within_one_csv(db_session):
    row = "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電"
    raw = _tx_csv(row, row)
    parsed = import_service.parse_transactions_csv(raw)
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.created == 1
    assert result.skipped_duplicates == 1
    assert db_session.query(models.Transaction).count() == 1


def test_imported_buy_and_sell_same_day_get_day_trade_flag(db_session):
    raw = _tx_csv(
        "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電",
        "2330,SELL,10,610.00,2026-05-15T02:00:00Z,28,18,台積電",
    )
    parsed = import_service.parse_transactions_csv(raw)
    import_service.commit_transactions(db_session, parsed, dry_run=False)

    rows = db_session.query(models.Transaction).all()
    assert {row.is_day_trade for row in rows} == {True}


def test_commit_transactions_collects_ledger_errors(db_session):
    raw = _tx_csv("2330,SELL,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    parsed = import_service.parse_transactions_csv(raw)
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert result.created == 0
    assert len(result.errors) == 1
    assert "Cannot sell" in result.errors[0].message
    assert db_session.query(models.Transaction).count() == 0


def test_parse_dividends_happy_path():
    raw = _div_csv(
        "0050,500.00,2026-06-30T00:00:00Z,2026-07-15T00:00:00Z",
        "2330.TW,1200.50,2026-07-01T00:00:00Z,",
    )
    parsed = import_service.parse_dividends_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    assert parsed.rows[0].payload["amount"] == Decimal("500.00")
    assert parsed.rows[1].payload["symbol"] == "2330"
    assert parsed.rows[1].payload["received_date"] is None


def test_commit_dividends_dedupes_on_second_upload(db_session):
    raw = _div_csv("0050,500.00,2026-06-30T00:00:00Z,2026-07-15T00:00:00Z")
    parsed = import_service.parse_dividends_csv(raw)
    first = import_service.commit_dividends(db_session, parsed, dry_run=False)
    second = import_service.commit_dividends(db_session, parsed, dry_run=False)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped_duplicates == 1
    assert db_session.query(models.Dividend).count() == 1


def test_endpoint_returns_serialized_result(client, db_session):
    raw = _tx_csv("2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", BytesIO(raw), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parsed"] == 1
    assert body["created"] == 1
    assert body["dry_run"] is False
    assert body["rows"][0]["payload"]["symbol"] == "2330"
    assert db_session.query(models.Transaction).count() == 1


def test_endpoint_dry_run_query_param(client, db_session):
    raw = _tx_csv("2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電")
    response = client.post(
        "/api/portfolio/imports/transactions?dry_run=true",
        files={"file": ("tx.csv", BytesIO(raw), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["created"] == 0
    assert db_session.query(models.Transaction).count() == 0


def test_endpoint_rejects_bad_header(client):
    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", BytesIO(b"sym,foo\n1,2\n"), "text/csv")},
    )
    assert response.status_code == 400
    body = response.json()
    error_text = body.get("detail") or body.get("message") or ""
    assert "header" in error_text
