from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
    FuturesStatementRun,
)

NOW = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


def _day(**kw):
    base = dict(
        statement_date=date(2026, 11, 24),
        accepted_sha256="a" * 64,
        accepted_at=NOW,
        drive_file_id="drv-1",
        **{k: Decimal("1.5") for k in DAY_AMOUNTS},
    )
    base.update(kw)
    return FuturesStatementDay(**base)


def _month(**kw):
    base = dict(
        month=date(2026, 10, 1),
        month_end=date(2026, 10, 30),
        accepted_sha256="b" * 64,
        accepted_at=NOW,
        **{k: Decimal("2.25") for k in MONTH_AMOUNTS},
    )
    base.update(kw)
    return FuturesStatementMonth(**base)


def test_day_row_roundtrip(db_session):
    json_cols = dict(
        deposits=[{"kind": "in", "amount": "100"}],
        fills=[{"product": "範例期貨", "order_no": "sA1B2-01", "qty": 1}],
        fills_total={"qty": 1},
        positions=[{"product": "範例選", "qty": -1}],
        position_lines=[{"raw": "line"}],
        last_detail={"n": 1},
        findings=[{"code": "x"}],
    )
    db_session.add(
        _day(
            open_positions=2,
            last_sha256="a" * 64,
            last_status="imported",
            last_at=NOW,
            since=NOW,
            findings_sha="c" * 64,
            findings_since=NOW,
            ack_sha256="a" * 64,
            ack_status="duplicate",
            ack_at=NOW,
            ack_findings_sha="c" * 64,
            **json_cols,
        )
    )
    db_session.add(_day(statement_date=date(2026, 11, 23), fills_total=None))
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(FuturesStatementDay, date(2026, 11, 24))
    for key, value in json_cols.items():
        assert getattr(row, key) == value
    assert row.open_positions == 2
    assert row.ack_status == "duplicate"
    assert row.equity == Decimal("1.5000")
    assert all(getattr(row, k) == Decimal("1.5") for k in DAY_AMOUNTS)
    assert db_session.get(FuturesStatementDay, date(2026, 11, 23)).fills_total is None


def test_day_status_check_rejects_unknown(db_session):
    db_session.add(_day(last_status="held"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_day_ack_status_check_rejects_unknown(db_session):
    db_session.add(_day(ack_status="held"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_month_row_roundtrip(db_session):
    db_session.add(
        _month(
            activity_dates=["2026-10-05", "2026-10-06"],
            open_positions=0,
            last_status="conflict",
            last_detail={"why": "sha"},
            findings=[{"code": "y"}],
        )
    )
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(FuturesStatementMonth, date(2026, 10, 1))
    assert row.month_end == date(2026, 10, 30)
    assert row.activity_dates == ["2026-10-05", "2026-10-06"]
    assert row.open_positions == 0
    assert row.last_detail == {"why": "sha"}
    assert all(getattr(row, k) == Decimal("2.25") for k in MONTH_AMOUNTS)


def test_month_status_check_rejects_unknown(db_session):
    db_session.add(_month(last_status="mismatch"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_run_kind_check(db_session):
    db_session.add(FuturesStatementRun(kind="bogus", run_at=NOW, result="ok"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_snapshot_futures_default_zero(db_session):
    db_session.add(
        PortfolioSnapshot(
            date=date(2026, 11, 24),
            total_market_value=1,
            total_cost=1,
            total_unrealized_pnl=0,
            total_dividends=0,
        )
    )
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(PortfolioSnapshot, date(2026, 11, 24)).futures_equity_twd == 0
