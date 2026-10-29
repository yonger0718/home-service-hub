from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.cathay_statement import CathayStatementDay, CathayStatementRun

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)


def test_day_row_roundtrip(db_session):
    lines = [{"symbol": "9999", "type": "buy", "quantity": 1000, "price": "10.5", "order_no": "XY12300"}]
    db_session.add(
        CathayStatementDay(
            trade_date=date(2026, 10, 28),
            accepted_kind="imported",
            accepted_sha256="a" * 64,
            accepted_at=NOW,
            canonical_lines=lines,
            last_sha256="a" * 64,
            last_status="imported",
            last_detail={"n": 1},
        )
    )
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(CathayStatementDay, date(2026, 10, 28))
    assert row.canonical_lines == lines
    assert row.last_detail == {"n": 1}
    assert row.accepted_kind == "imported"


def test_status_check_rejects_unknown(db_session):
    db_session.add(CathayStatementDay(trade_date=date(2026, 10, 28), last_status="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_accepted_kind_check_rejects_unknown(db_session):
    db_session.add(CathayStatementDay(trade_date=date(2026, 10, 28), accepted_kind="held"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_run_kind_unique(db_session):
    db_session.add(CathayStatementRun(kind="latest", run_at=NOW, result="ok", files_seen=2, posted=1))
    db_session.flush()
    db_session.add(CathayStatementRun(kind="latest", run_at=NOW, result="failed", code="x"))
    with pytest.raises(IntegrityError):
        db_session.flush()
