"""§5.5: a 國泰 CSV upload compares rows on statement-owned dates instead of importing them."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

import pytest

from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction
from app.services import broker_cathay_service, broker_cathay_statement_service, import_service
from app.services.broker_cathay_statement_service import import_statement
from tests.integration.test_cathay_statement_import import D, _line, _payload

D2 = date(2026, 3, 10)
D2_START = datetime(2026, 3, 10, tzinfo=timezone.utc)

CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)
ONE_LINE = [_line("範例電子", "集買", 1000, "12.3", 5, order_no="XY12300")]


def _csv_row(name="範例電子", day="2026/03/09", quantity=1000, price="12.30", fee=5, side="現買",
             order_id="XY123") -> str:
    return f'{name},{day},"{quantity}","0",{side},"{price}","0",{fee},0,0,0,0,0,0,{order_id}'


def _csv(*rows: str) -> bytes:
    return ("根據您篩選的結果，總計有2筆資料\n" + CATHAY_HEADER + "\n".join(rows) + "\n").encode("utf-8")


SPLIT_FILLS = (
    _csv_row(quantity=600, fee=3),
    _csv_row(quantity=400, fee=2),
)
FREE_FILL = _csv_row(name="範例化學", day="2026/03/10", quantity=2000, price="25", fee=21, order_id="XY125")


@pytest.fixture(autouse=True)
def _env(monkeypatch, db_session) -> None:
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例電子", ["9999"])
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例化學", ["9998"])
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    db_session.add(BrokerAccount(
        broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
        opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True,
    ))
    db_session.commit()


def _own_d(db, lines=ONE_LINE) -> None:
    assert import_statement(db, _payload(lines=lines), dry_run=False).outcome == "imported"


def _upload(db, *rows: str, dry_run: bool = False):
    raw = _csv(*rows)
    parsed = broker_cathay_service.parse_cathay_rows(raw)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=dry_run, db=db, parsed=parsed
    )
    return parsed, result


def _statuses(parsed) -> list[str | None]:
    return [row.status for row in parsed.rows]


def _snapshot(db) -> tuple[list[tuple], list[tuple]]:
    db.expire_all()
    transactions = [
        (t.id, t.symbol, t.type, t.position_side, t.quantity, t.price, t.fee, t.tax, t.trade_date,
         t.broker, t.broker_day_trade_marker, t.import_fingerprint)
        for t in db.query(models.Transaction).order_by(models.Transaction.id)
    ]
    cash = [
        (c.id, c.related_transaction_id, c.amount)
        for c in db.query(CashTransaction).order_by(CashTransaction.id)
    ]
    return transactions, cash


def _on(db, day: date) -> list[models.Transaction]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [
        t for t in db.query(models.Transaction).order_by(models.Transaction.id)
        if t.trade_date.replace(tzinfo=timezone.utc) == start
    ]


def test_csv_on_imported_date_is_covered(db_session):
    _own_d(db_session)
    before = _snapshot(db_session)

    parsed, result = _upload(db_session, *SPLIT_FILLS)

    assert _statuses(parsed) == ["covered", "covered"]
    assert result.covered == 2
    assert result.created == 0
    assert result.errors == []
    assert _snapshot(db_session) == before


def test_csv_on_owned_date_mismatch_errors(db_session):
    _own_d(db_session)
    before = _snapshot(db_session)

    parsed, result = _upload(db_session, _csv_row(quantity=600, fee=3), _csv_row(quantity=300, fee=2))

    assert _statuses(parsed) == ["error", "error"]
    assert result.covered == 0
    assert result.created == 0
    assert [e.row_index for e in result.errors] == [1, 2]
    assert all("2026-03-09" in e.message for e in result.errors)
    assert _snapshot(db_session) == before


def test_csv_mixed_owned_and_free_dates(db_session):
    _own_d(db_session)
    statement_rows = [(t.id, t.import_fingerprint) for t in _on(db_session, D)]

    parsed, result = _upload(db_session, *SPLIT_FILLS, FREE_FILL)

    assert _statuses(parsed) == ["covered", "covered", "new"]
    assert result.covered == 2
    assert result.created == 1
    assert result.errors == []
    db_session.expire_all()
    assert [(t.id, t.import_fingerprint) for t in _on(db_session, D)] == statement_rows
    free = _on(db_session, D2)
    assert [t.id for t in free] == result.created_ids
    assert [t.import_fingerprint for t in free] == [parsed.rows[2].fingerprint]
    assert db_session.query(CashTransaction).filter_by(related_transaction_id=free[0].id).count() == 2


def test_csv_owned_mismatch_free_date_still_imports(client, db_session):
    _own_d(db_session)
    before_d = [(t.id, t.import_fingerprint, t.quantity) for t in _on(db_session, D)]

    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("cathay.csv", BytesIO(_csv(_csv_row(quantity=900), FREE_FILL)), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["status"] for row in body["rows"]] == ["error", "new"]
    assert body["created"] == 1
    assert body["covered"] == 0
    assert [e["row_index"] for e in body["errors"]] == [1]
    assert "2026-03-09" in body["errors"][0]["reason"]
    db_session.expire_all()
    assert [(t.id, t.import_fingerprint, t.quantity) for t in _on(db_session, D)] == before_d
    assert [t.id for t in _on(db_session, D2)] == body["created_ids"]


def test_conflict_then_csv_multifill_unchanged(db_session):
    _own_d(db_session)
    corrected = [_line("範例電子", "集買", 1000, "12.3", 6, order_no="XY12300")]
    assert import_statement(db_session, _payload(lines=corrected, sha="b" * 64), dry_run=False).outcome == "conflict"
    before = _snapshot(db_session)

    parsed, result = _upload(db_session, *SPLIT_FILLS)

    assert _statuses(parsed) == ["covered", "covered"]
    assert result.covered == 2
    assert result.errors == []
    assert _snapshot(db_session) == before


def test_dry_run_previews_covered(db_session):
    _own_d(db_session)
    before = _snapshot(db_session)

    parsed, result = _upload(db_session, *SPLIT_FILLS, FREE_FILL, dry_run=True)

    assert result.dry_run is True
    assert _statuses(parsed) == ["covered", "covered", "new"]
    assert result.covered == 2
    assert result.would_insert == 1
    assert result.errors == []
    assert _snapshot(db_session) == before


def test_dry_run_previews_owned_mismatch_as_error(db_session):
    _own_d(db_session)
    before = _snapshot(db_session)

    parsed, result = _upload(db_session, _csv_row(quantity=900), FREE_FILL, dry_run=True)

    assert _statuses(parsed) == ["error", "new"]
    assert result.covered == 0
    assert [e.row_index for e in result.errors] == [1]
    assert _snapshot(db_session) == before


def test_rehash_never_touches_statement_rows(db_session):
    _own_d(db_session)
    statement_rows = [(t.id, t.import_fingerprint) for t in _on(db_session, D)]
    legacy_fp = import_service._transaction_fingerprint(
        "9999", "BUY", 1000, Decimal("12.30"), D2_START, Decimal("5"), Decimal("0")
    )
    legacy = models.Transaction(
        symbol="9999", market="TW", name="範例電子", type=models.TransactionType.BUY,
        quantity=1000, price=Decimal("12.3"), trade_date=D2_START, fee=Decimal("5"),
        tax=Decimal("0"), import_fingerprint=legacy_fp,
    )
    db_session.add(legacy)
    db_session.commit()
    legacy_id = legacy.id
    rows = (_csv_row(), _csv_row(day="2026/03/10", order_id="XY126"))

    preview, _ = _upload(db_session, *rows, dry_run=True)
    parsed, result = _upload(db_session, *rows)

    assert _statuses(preview) == ["covered", "rehash"]
    assert _statuses(parsed) == ["covered", "rehash"]
    assert result.rehashed == 1
    assert result.created == 0
    db_session.expire_all()
    assert [(t.id, t.import_fingerprint) for t in _on(db_session, D)] == statement_rows
    assert [(t.id, t.import_fingerprint) for t in _on(db_session, D2)] == [
        (legacy_id, parsed.rows[1].fingerprint)
    ]


def test_commit_locks_every_batch_date_dry_run_none(db_session, monkeypatch):
    _own_d(db_session)
    calls: list[list[date]] = []
    monkeypatch.setattr(
        broker_cathay_statement_service, "lock_trade_dates",
        lambda db, dates: calls.append(sorted(set(dates))),
    )

    _upload(db_session, *SPLIT_FILLS, FREE_FILL, dry_run=True)
    assert calls == []
    _upload(db_session, *SPLIT_FILLS, FREE_FILL)
    assert calls == [[D, D2]]
