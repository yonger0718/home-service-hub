"""Per-row ``status`` on import rows (redesign v2 PR 6, Task 1b, Option B).

Every dry-run and commit path stamps each ``ParsedRow`` with ``new`` /
``duplicate`` / ``unresolved`` / ``error`` (plus ``rehash`` on Cathay, Task 7)
so the preview table can render the row's fate directly; the counters stay
consistent with the statuses.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from app.models import portfolio as models
from app.models.fx_rate import FXRate
from app.routers import imports as imports_router
from app.services import broker_cathay_service, import_service
from app.services.per_date_verify import OverrideValidation

TX_HEADER = "symbol,type,quantity,price,trade_date,fee,tax,name\n"
DIV_HEADER = "symbol,amount,ex_dividend_date,received_date\n"
FIXTURES = Path(__file__).parent / "fixtures"

ROW_A = "2330,BUY,10,600.00,2026-05-15T01:30:00Z,28,0,台積電"
ROW_B = "2330,BUY,5,610.00,2026-05-16T01:30:00Z,28,0,台積電"
ROW_C = "0050,BUY,20,140.00,2026-05-17T01:30:00Z,10,0,元大台灣50"
DIV_A = "0050,500.00,2026-06-30T00:00:00Z,2026-07-15T00:00:00Z"
DIV_B = "2330,1200.50,2026-07-01T00:00:00Z,"
DIV_C = "0056,300.00,2026-07-02T00:00:00Z,"


def _tx_csv(*rows: str) -> bytes:
    return (TX_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _div_csv(*rows: str) -> bytes:
    return (DIV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _statuses(rows) -> list:
    return [row.status for row in rows]


def _wire_statuses(rows: list[dict]) -> Counter:
    return Counter(row["status"] for row in rows)


# ---------- generic transactions ----------


def test_generic_transactions_dry_run_marks_stored_and_in_batch_duplicates(db_session):
    import_service.commit_transactions(
        db_session, import_service.parse_transactions_csv(_tx_csv(ROW_A)), dry_run=False
    )

    parsed = import_service.parse_transactions_csv(_tx_csv(ROW_A, ROW_B, ROW_B, ROW_C))
    result = import_service.commit_transactions(db_session, parsed, dry_run=True)

    assert _statuses(parsed.rows) == ["duplicate", "new", "duplicate", "new"]
    counts = Counter(_statuses(parsed.rows))
    assert result.skipped_duplicates == 2 == counts["duplicate"]
    assert result.parsed - result.skipped_duplicates == counts["new"] == 2
    assert db_session.query(models.Transaction).count() == 1


def test_generic_transactions_commit_new_rows_have_created_ids(db_session):
    import_service.commit_transactions(
        db_session, import_service.parse_transactions_csv(_tx_csv(ROW_A)), dry_run=False
    )

    parsed = import_service.parse_transactions_csv(_tx_csv(ROW_A, ROW_B, ROW_B, ROW_C))
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert _statuses(parsed.rows) == ["duplicate", "new", "duplicate", "new"]
    counts = Counter(_statuses(parsed.rows))
    assert result.created == len(result.created_ids) == counts["new"] == 2
    assert result.skipped_duplicates == counts["duplicate"] == 2


def test_generic_transactions_commit_row_rejected_at_persist_is_error(db_session):
    sell = "2330,SELL,10,600.00,2026-05-15T01:30:00Z,28,0,台積電"
    parsed = import_service.parse_transactions_csv(_tx_csv(sell, ROW_C))
    result = import_service.commit_transactions(db_session, parsed, dry_run=False)

    assert _statuses(parsed.rows) == ["error", "new"]
    assert [error.row_index for error in result.errors] == [1]
    assert result.created == 1


def test_generic_transactions_parse_errors_stay_out_of_rows(db_session):
    parsed = import_service.parse_transactions_csv(
        _tx_csv(ROW_A, "2330,BUY,abc,600,2026-05-15T01:30:00Z,0,0,")
    )
    result = import_service.commit_transactions(db_session, parsed, dry_run=True)

    assert [row.row_index for row in parsed.rows] == [1]
    assert _statuses(parsed.rows) == ["new"]
    assert [error.row_index for error in result.errors] == [2]


# ---------- dividends ----------


def test_dividends_dry_run_marks_stored_and_in_batch_duplicates(db_session):
    import_service.commit_dividends(
        db_session, import_service.parse_dividends_csv(_div_csv(DIV_A)), dry_run=False
    )

    parsed = import_service.parse_dividends_csv(_div_csv(DIV_A, DIV_B, DIV_B, DIV_C))
    result = import_service.commit_dividends(db_session, parsed, dry_run=True)

    assert _statuses(parsed.rows) == ["duplicate", "new", "duplicate", "new"]
    counts = Counter(_statuses(parsed.rows))
    assert result.skipped_duplicates == 2 == counts["duplicate"]
    assert result.parsed - result.skipped_duplicates == counts["new"]


def test_dividends_commit_new_rows_have_created_ids(db_session):
    import_service.commit_dividends(
        db_session, import_service.parse_dividends_csv(_div_csv(DIV_A)), dry_run=False
    )

    parsed = import_service.parse_dividends_csv(_div_csv(DIV_A, DIV_B, DIV_B, DIV_C))
    result = import_service.commit_dividends(db_session, parsed, dry_run=False)

    assert _statuses(parsed.rows) == ["duplicate", "new", "duplicate", "new"]
    assert result.created == len(result.created_ids) == 2
    assert result.skipped_duplicates == 2


# ---------- broker (IB / Firstrade / Schwab) ----------


def _seed_usd(db_session, *dates: date) -> None:
    for d in dates:
        db_session.merge(
            FXRate(currency="USD", date=d, rate_to_twd=Decimal("31.42"), source="test")
        )
    db_session.commit()


FIRSTRADE_DATES = (
    date(2026, 4, 16),
    date(2026, 5, 14),
    date(2026, 5, 15),
    date(2026, 5, 18),
    date(2026, 6, 3),
    date(2026, 6, 4),
    date(2026, 6, 5),
)


def _firstrade() -> bytes:
    return (FIXTURES / "firstrade_sample.csv").read_bytes()


def test_broker_dry_run_marks_new_then_duplicate_after_commit(db_session):
    _seed_usd(db_session, *FIRSTRADE_DATES)

    preview = import_service.parse_transactions_csv(_firstrade())
    dry = import_service.commit_transactions(db_session, preview, dry_run=True)
    assert set(_statuses(preview.rows)) == {"new"}
    assert dry.skipped_duplicates == 0

    first = import_service.parse_transactions_csv(_firstrade())
    committed = import_service.commit_transactions(db_session, first, dry_run=False)
    assert Counter(_statuses(first.rows))["new"] == committed.created
    assert Counter(_statuses(first.rows))["duplicate"] == committed.skipped_duplicates

    again = import_service.parse_transactions_csv(_firstrade())
    result = import_service.commit_transactions(db_session, again, dry_run=True)
    assert set(_statuses(again.rows)) == {"duplicate"}
    assert result.skipped_duplicates == len(again.rows)


def test_broker_missing_fx_rows_are_error(db_session):
    parsed = import_service.parse_transactions_csv(_firstrade())
    result = import_service.commit_transactions(db_session, parsed, dry_run=True)

    errored = {row.row_index for row in parsed.rows if row.status == "error"}
    assert errored == {error.row_index for error in result.errors}
    assert errored  # USD rows without an FX rate
    assert result.skipped_duplicates == Counter(_statuses(parsed.rows))["duplicate"]


# ---------- Cathay ----------


CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)


def _cathay_csv(*rows: str) -> bytes:
    return ("根據您篩選的結果，總計有N筆資料\n" + CATHAY_HEADER + "\n".join(rows) + "\n").encode(
        "utf-8"
    )


def _cathay_row(*, name: str = "晶宏", price: str = "56.3", order_id: str = "aT532") -> str:
    return (
        f'{name},2026/05/08,"1,000","-56,322",現買,"{price}","56,300",'
        f"22,0,0,0,0,0,0,{order_id}"
    )


@pytest.fixture
def cathay_names(monkeypatch):
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "晶宏", ["3141"])

    def fake_verify_overrides(**kwargs):
        return [
            OverrideValidation(
                name="新光金",
                code="2887",
                status="name_mismatch",
                expected_name="新光金",
                fetched_name="台新新光金",
            )
        ]

    monkeypatch.setattr(
        broker_cathay_service.per_date_verify, "verify_overrides", fake_verify_overrides
    )


def _cathay_upload_rows() -> bytes:
    return _cathay_csv(
        _cathay_row(order_id="aT532"),            # already stored → duplicate
        _cathay_row(price="57", order_id="aT700"),  # new
        _cathay_row(name="新光金", order_id="aT800"),  # override not verified → unresolved
        _cathay_row(name="不存在", order_id="bad"),  # unresolved name → not a row
    )


def _seed_cathay_stored(db_session) -> None:
    broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_cathay_row(order_id="aT532")), dry_run=False, db=db_session
    )


def test_cathay_dry_run_statuses_match_would_counts(db_session, cathay_names):
    _seed_cathay_stored(db_session)
    raw = _cathay_upload_rows()
    overrides = {"新光金": "2887"}
    parsed = broker_cathay_service.parse_cathay_rows(raw, name_overrides=overrides)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=True, db=db_session, name_overrides=overrides, parsed=parsed
    )

    assert _statuses(parsed.rows) == ["duplicate", "new", "unresolved"]
    counts = Counter(_statuses(parsed.rows))
    assert result.would_insert == counts["new"] == 1
    assert result.would_skip_duplicate == counts["duplicate"] == 1
    assert result.would_rehash == counts["rehash"] == 0
    assert result.skipped_unverified == counts["unresolved"] == 1
    # The unresolvable name never becomes a row; it is counted, not stamped.
    assert result.skipped_unresolved == 1
    assert "不存在" not in {row.payload["name"] for row in parsed.rows}


def test_cathay_dry_run_stored_and_in_batch_twin_are_duplicate(db_session, cathay_names):
    """An exact repeated line (same 委託書號) shares the new-scheme fingerprint, so the
    second copy is an in-batch would-skip (not a rehash), like the stored duplicate."""
    _seed_cathay_stored(db_session)
    twin = _cathay_row(price="57", order_id="aT700")
    raw = _cathay_csv(_cathay_row(order_id="aT532"), twin, twin)
    parsed = broker_cathay_service.parse_cathay_rows(raw)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=True, db=db_session, parsed=parsed
    )

    assert _statuses(parsed.rows) == ["duplicate", "new", "duplicate"]
    counts = Counter(_statuses(parsed.rows))
    assert result.would_skip_duplicate == 2 == counts["duplicate"]
    assert result.would_rehash == 0
    assert result.would_insert == counts["new"] == 1
    assert db_session.query(models.Transaction).count() == 1


def _seed_cathay_legacy(db_session, line: str, *, business_key_only: bool = False) -> None:
    """Store a pre-scheme trade the Cathay rehash will re-fingerprint in place.

    Default: stored under the row's legacy fingerprint (primary rehash path).
    ``business_key_only``: stored at 13:30 TPE with no fingerprint, so only the
    same-day business-key fallback (``_business_key_match``) can pair it.
    """
    row = broker_cathay_service.parse_cathay_rows(_cathay_csv(line)).rows[0]
    payload = row.payload
    trade_date = payload["trade_date"]
    fingerprint: str | None = broker_cathay_service._legacy_fingerprint(row)
    if business_key_only:
        trade_date = datetime.combine(trade_date.date(), datetime.min.time()).replace(
            hour=5, minute=30, tzinfo=timezone.utc
        )
        fingerprint = None
    db_session.add(
        models.Transaction(
            symbol=payload["symbol"],
            name=payload["name"],
            type=models.TransactionType(payload["type"]),
            quantity=payload["quantity"],
            price=payload["price"],
            trade_date=trade_date,
            fee=payload["fee"],
            tax=payload["tax"],
            import_fingerprint=fingerprint,
        )
    )
    db_session.commit()


REHASH_LINE = _cathay_row(price="57", order_id="aT700")


def test_cathay_dry_run_rehash_row_is_rehash(db_session, cathay_names):
    _seed_cathay_legacy(db_session, _cathay_row())

    raw = _cathay_csv(_cathay_row())
    parsed = broker_cathay_service.parse_cathay_rows(raw)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=True, db=db_session, parsed=parsed
    )

    assert result.would_rehash == 1
    assert result.would_skip_duplicate == 0
    assert _statuses(parsed.rows) == ["rehash"]


@pytest.mark.parametrize("business_key_only", [False, True])
def test_cathay_dry_run_duplicate_and_rehash_are_distinct(
    db_session, cathay_names, business_key_only
):
    """A stored new-scheme row is a would-skip ``duplicate``; a legacy row the
    batch re-fingerprints is ``rehash``. #duplicate == would_skip_duplicate and
    #rehash == would_rehash (both rehash sites: legacy fingerprint, business key)."""
    _seed_cathay_stored(db_session)
    _seed_cathay_legacy(db_session, REHASH_LINE, business_key_only=business_key_only)

    raw = _cathay_csv(_cathay_row(order_id="aT532"), REHASH_LINE)
    parsed = broker_cathay_service.parse_cathay_rows(raw)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=True, db=db_session, parsed=parsed
    )

    assert _statuses(parsed.rows) == ["duplicate", "rehash"]
    counts = Counter(_statuses(parsed.rows))
    assert result.would_skip_duplicate == counts["duplicate"] == 1
    assert result.would_rehash == counts["rehash"] == 1
    assert result.would_insert == counts["new"] == 0
    assert result.skipped_duplicates == 0


@pytest.mark.parametrize("business_key_only", [False, True])
def test_cathay_commit_rehashed_row_is_rehash(db_session, cathay_names, business_key_only):
    _seed_cathay_stored(db_session)
    _seed_cathay_legacy(db_session, REHASH_LINE, business_key_only=business_key_only)

    raw = _cathay_csv(_cathay_row(order_id="aT532"), REHASH_LINE)
    parsed = broker_cathay_service.parse_cathay_rows(raw)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=False, db=db_session, parsed=parsed
    )

    assert _statuses(parsed.rows) == ["duplicate", "rehash"]
    counts = Counter(_statuses(parsed.rows))
    assert result.skipped_duplicates == counts["duplicate"] == 1
    assert result.rehashed == counts["rehash"] == 1
    assert result.created == len(result.created_ids) == counts["new"] == 0
    # Re-fingerprinted in place: no row was inserted.
    assert db_session.query(models.Transaction).count() == 2


def test_cathay_commit_new_rows_have_created_ids(db_session, cathay_names):
    _seed_cathay_stored(db_session)
    raw = _cathay_csv(_cathay_row(order_id="aT532"), _cathay_row(price="57", order_id="aT700"))
    parsed = broker_cathay_service.parse_cathay_rows(raw)

    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=False, db=db_session, parsed=parsed
    )

    assert _statuses(parsed.rows) == ["duplicate", "new"]
    assert result.created == len(result.created_ids) == 1
    assert result.skipped_duplicates == 1


def test_cathay_commit_rejected_by_parse_error_clears_every_status(db_session, cathay_names):
    """A parse error rejects the whole Cathay batch: nothing is written and no row
    was judged individually, so every row (the override-unverified one too) carries
    ``status is None`` instead of a stale ``unresolved``."""
    raw = _cathay_csv(
        _cathay_row(price="57", order_id="aT700"),
        _cathay_row(name="新光金", order_id="aT800"),  # override not verified → filtered out
        _cathay_row(price="abc", order_id="aT900"),  # 成交價 not a decimal → parse error
    )
    overrides = {"新光金": "2887"}
    parsed = broker_cathay_service.parse_cathay_rows(raw, name_overrides=overrides)
    assert parsed.errors and len(parsed.rows) == 2

    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=False, db=db_session, name_overrides=overrides, parsed=parsed
    )

    assert result.created == 0 and result.created_ids == []
    assert result.skipped_unverified == 1
    assert _statuses(parsed.rows) == [None, None]
    assert db_session.query(models.Transaction).count() == 0


# ---------- router / serializer ----------


def test_transactions_endpoint_dry_run_rows_carry_status(client, db_session):
    client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", BytesIO(_tx_csv(ROW_A)), "text/csv")},
    )

    response = client.post(
        "/api/portfolio/imports/transactions?dry_run=true",
        files={"file": ("tx.csv", BytesIO(_tx_csv(ROW_A, ROW_B, ROW_B)), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["status"] for row in body["rows"]] == ["duplicate", "new", "duplicate"]
    assert body["skipped_duplicates"] == 2
    assert body["counts"]["skipped"] == 2


def test_dividends_endpoint_dry_run_rows_carry_status(client):
    response = client.post(
        "/api/portfolio/imports/dividends?dry_run=true",
        files={"file": ("div.csv", BytesIO(_div_csv(DIV_A, DIV_A)), "text/csv")},
    )

    assert response.status_code == 200
    assert [row["status"] for row in response.json()["rows"]] == ["new", "duplicate"]


def test_cathay_endpoint_dry_run_rows_carry_status(client, db_session, cathay_names):
    _seed_cathay_stored(db_session)

    response = client.post(
        "/api/portfolio/imports/transactions?dry_run=true",
        files={"file": ("cathay.csv", BytesIO(_cathay_upload_rows()), "text/csv")},
        data={"name_overrides": '{"新光金":"2887"}'},
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["status"] for row in body["rows"]] == ["duplicate", "new", "unresolved"]
    assert body["would_insert"] == 1
    assert body["would_skip_duplicate"] == 1


def test_broker_csv_endpoint_dry_run_rows_carry_status(client, db_session):
    _seed_usd(db_session, *FIRSTRADE_DATES)
    upload = lambda dry: client.post(  # noqa: E731
        f"/api/portfolio/imports/csv?dry_run={'true' if dry else 'false'}",
        files={"file": ("firstrade.csv", BytesIO(_firstrade()), "text/csv")},
    )

    preview = upload(True).json()
    assert preview["transactions"] and preview["cash_flows"]
    assert all(row["status"] == "new" for row in preview["transactions"] + preview["cash_flows"])
    assert preview["counts"]["skipped"] == 0

    assert upload(False).json()["created"] > 0

    again = upload(True).json()
    rows = again["transactions"] + again["cash_flows"]
    assert _wire_statuses(rows) == Counter({"duplicate": len(rows)})
    assert again["counts"]["skipped"] == _wire_statuses(rows)["duplicate"]


def test_serializer_emits_null_status_for_unstamped_row():
    parsed = import_service.parse_transactions_csv(_tx_csv(ROW_A))
    assert parsed.rows[0].status is None

    serialized = imports_router._serialize_parse(parsed)

    assert "status" in serialized[0]
    assert serialized[0]["status"] is None
