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
        ("2330,HOLD,10,600,2026-05-15T01:30:00Z,0,0,", "BUY/SELL"),
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
        "2330,BUY,1000,600.00,2026-05-15T01:30:00Z,28,0,台積電",
        "2330,SELL,1000,610.00,2026-05-15T02:00:00Z,28,18,台積電",
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


# ---------- Localised headers + Chinese type values ----------


def test_parse_transactions_accepts_chinese_headers():
    raw = (
        "代號,類別,股數,價格,交易日期,手續費,稅金,名稱\n"
        "2330,買進,10,600.00,2026-05-15T01:30:00Z,28,0,台積電\n"
        "0050,賣出,5,140.50,2026-05-15T02:00:00Z,5,14,\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    assert parsed.rows[0].payload["type"] == "BUY"
    assert parsed.rows[1].payload["type"] == "SELL"
    assert parsed.rows[0].payload["symbol"] == "2330"


def test_parse_transactions_mixes_english_and_chinese_columns():
    raw = (
        "代碼,type,股數,price,trade_date,fee,tax,name\n"
        "2330,買,10,600,2026-05-15T01:30:00Z,28,0,\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert parsed.rows[0].payload["type"] == "BUY"


def test_parse_transactions_ignores_unknown_extra_columns():
    raw = (
        "symbol,type,quantity,price,trade_date,fee,tax,name,備註\n"
        "2330,BUY,10,600,2026-05-15T01:30:00Z,0,0,,broker-export\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 1


def test_parse_transactions_no_header_mode():
    raw = b"2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,\n"
    parsed = import_service.parse_transactions_csv(raw, has_header=False)
    assert parsed.errors == []
    assert len(parsed.rows) == 1
    assert parsed.rows[0].payload["symbol"] == "2330"


def test_parse_dividends_accepts_chinese_headers():
    raw = (
        "代號,金額,除息日,入帳日\n"
        "2330,3000,2026-06-12T00:00:00Z,2026-07-15T00:00:00Z\n"
    ).encode("utf-8")
    parsed = import_service.parse_dividends_csv(raw)
    assert parsed.errors == []
    assert parsed.rows[0].payload["symbol"] == "2330"
    assert parsed.rows[0].payload["amount"] == Decimal("3000")


def test_endpoint_has_header_false_query(client):
    raw = b"2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,\n"
    response = client.post(
        "/api/portfolio/imports/transactions?has_header=false",
        files={"file": ("tx.csv", BytesIO(raw), "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1


# ---------------------------------------------------------------------------
# order_id (委託書號) fingerprint disambiguation — fixes identical-fill collision.
# ---------------------------------------------------------------------------


def _fp(**kwargs) -> str:
    """Helper: call _transaction_fingerprint with a fixed set of fingerprint inputs.

    Overrides via **kwargs let each test mutate just the field it cares about.
    """

    base = dict(
        symbol="0050",
        type_="BUY",
        quantity=1000,
        price=Decimal("50.0000"),
        trade_date=datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc),
        fee=Decimal("0"),
        tax=Decimal("0"),
    )
    base.update(kwargs)
    return import_service._transaction_fingerprint(**base)


def test_transaction_fingerprint_without_order_id_matches_legacy_canonical():
    """Hash with no order_id == hash of the pre-feature canonical string format."""
    from hashlib import sha256

    legacy_canonical = "|".join(
        (
            import_service.SOURCE_TRANSACTIONS,
            "0050",
            "BUY",
            "1000",
            "50.0000",
            datetime(2026, 5, 15, 1, 30, tzinfo=timezone.utc).isoformat(),
            "0.0000",
            "0.0000",
        )
    )
    expected = sha256(legacy_canonical.encode("utf-8")).hexdigest()
    assert _fp() == expected
    assert _fp(order_id=None) == expected
    assert _fp(order_id="") == expected


def test_transaction_fingerprint_with_order_id_differs_from_legacy():
    assert _fp() != _fp(order_id="OD-1")


def test_transaction_fingerprint_distinct_order_ids_produce_distinct_hashes():
    assert _fp(order_id="A") != _fp(order_id="B")


def test_parse_transactions_identical_fills_with_distinct_order_ids():
    raw = (
        "symbol,type,quantity,price,trade_date,fee,tax,name,order_id\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-1\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-2\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    assert parsed.rows[0].fingerprint != parsed.rows[1].fingerprint
    assert parsed.rows[0].payload["order_id"] == "OD-1"
    assert parsed.rows[1].payload["order_id"] == "OD-2"


def test_parse_transactions_identical_fills_without_order_ids_collide(db_session):
    """Documented limitation: identical same-day fills without order_id collide."""
    raw = _tx_csv(
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,",
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,",
    )
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    # Both rows produce the same hash → commit dedupes the second.
    assert parsed.rows[0].fingerprint == parsed.rows[1].fingerprint
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)
    assert result.created == 1
    assert result.skipped_duplicates == 1


def test_parse_transactions_accepts_委託書號_synonym():
    raw = (
        "代號,類別,股數,價格,交易日期,手續費,稅金,名稱,委託書號\n"
        "0050,買進,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-9\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert parsed.rows[0].payload["order_id"] == "OD-9"
    # And the hash includes the order_id segment.
    assert parsed.rows[0].fingerprint == _fp(order_id="OD-9")


def test_parse_transactions_whitespace_order_id_treated_as_empty():
    raw = (
        "symbol,type,quantity,price,trade_date,fee,tax,name,order_id\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,   \n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.rows[0].payload["order_id"] is None
    assert parsed.rows[0].fingerprint == _fp()  # legacy hash


def test_parse_transactions_mixed_with_and_without_order_id():
    raw = (
        "symbol,type,quantity,price,trade_date,fee,tax,name,order_id\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-1\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,\n"
    ).encode("utf-8")
    parsed = import_service.parse_transactions_csv(raw)
    assert parsed.errors == []
    assert len(parsed.rows) == 2
    assert parsed.rows[0].fingerprint != parsed.rows[1].fingerprint


def test_commit_transactions_with_order_ids_reimport_is_noop(db_session):
    raw = (
        "symbol,type,quantity,price,trade_date,fee,tax,name,order_id\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-1\n"
        "0050,BUY,1000,50.00,2026-05-15T01:30:00Z,0,0,,OD-2\n"
    ).encode("utf-8")
    first = import_service.commit_transactions(
        db_session, import_service.parse_transactions_csv(raw), dry_run=False
    )
    assert first.created == 2
    second = import_service.commit_transactions(
        db_session, import_service.parse_transactions_csv(raw), dry_run=False
    )
    assert second.created == 0
    assert second.skipped_duplicates == 2
