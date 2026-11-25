"""GET /api/portfolio/futures/summary (spec §9)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)

URL = "/api/portfolio/futures/summary"
NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _day(db, d, total, *, deposits="0", realized="0", balance=None, open_positions=0, findings=None, **extra):
    row = FuturesStatementDay(
        statement_date=d, accepted_sha256="a" * 64, accepted_at=NOW, open_positions=open_positions,
        findings=findings, **{k: Decimal("0") for k in DAY_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.balance = Decimal(balance if balance is not None else total)
    row.deposits_net = Decimal(deposits)
    row.futures_realized = Decimal(realized)
    for k, v in extra.items():
        setattr(row, k, Decimal(v))
    db.add(row)
    db.commit()


def _month(db, month_end, total, *, b="0", k="0", findings=None):
    row = FuturesStatementMonth(
        month=month_end.replace(day=1), month_end=month_end, accepted_sha256="b" * 64, accepted_at=NOW,
        open_positions=0, findings=findings, **{c: Decimal("0") for c in MONTH_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.m = Decimal(total)
    row.b = Decimal(b)
    row.k = Decimal(k)
    db.add(row)
    db.commit()


def get(client, as_of="2026-03-20"):
    response = client.get(URL, params={"as_of": as_of})
    assert response.status_code == 200
    return response.json()


def test_summary_empty(client):
    assert get(client) == {"as_of": "2026-03-20", "value": None, "latest_daily": None, "month": None}


def test_summary_no_baseline_pnl_null(client, db_session):
    _day(db_session, date(2026, 3, 5), "100", deposits="100", realized="7")
    month = get(client)["month"]
    assert month["month"] == "2026-03"
    assert month["pnl"] is None
    assert month["pnl_complete"] is False
    assert month["pnl_reason"] == "no_baseline"
    assert month["futures_realized"] == "7.0000"


def test_summary_month_pnl_daily_only(client, db_session):
    _day(db_session, date(2026, 2, 27), "1000")
    _day(db_session, date(2026, 3, 5), "1300", deposits="200", realized="30")
    _day(db_session, date(2026, 3, 9), "1350", deposits="0", realized="20")
    body = get(client)
    assert body["month"] == {
        "month": "2026-03", "pnl": "150.0000", "pnl_complete": True, "pnl_reason": None,
        "futures_realized": "50.0000",
    }
    assert body["value"]["total_equity"] == "1350.0000"
    assert body["value"]["source_date"] == "2026-03-09"
    assert body["value"]["source_kind"] == "daily"
    assert body["latest_daily"]["statement_date"] == "2026-03-09"


def test_summary_findings_marks_incomplete(client, db_session):
    _day(db_session, date(2026, 2, 27), "1000")
    _day(db_session, date(2026, 3, 5), "1300", deposits="200")
    _day(db_session, date(2026, 3, 9), "1350", findings=[{"code": "prev", "dates": ["2026-03-05"], "letters": []}])
    month = get(client)["month"]
    assert month["pnl"] == "150.0000"
    assert month["pnl_complete"] is False
    assert month["pnl_reason"] == "findings"


def test_summary_month_pnl_with_monthly(client, db_session):
    _month(db_session, date(2026, 2, 28), "1000")
    _month(db_session, date(2026, 3, 31), "1500", b="300", k="40")
    body = get(client, "2026-04-02")
    assert body["month"]["month"] == "2026-04"
    body = get(client, "2026-03-31")
    assert body["month"] == {
        "month": "2026-03", "pnl": "200.0000", "pnl_complete": True, "pnl_reason": None,
        "futures_realized": "40.0000",
    }
    assert body["value"]["source_kind"] == "monthly"
    assert body["latest_daily"] is None


def test_summary_monthly_row_complete(client, db_session):
    _day(db_session, date(2026, 2, 27), "1000")
    _day(db_session, date(2026, 3, 5), "1200", findings=[{"code": "prev", "dates": [], "letters": []}])
    _month(db_session, date(2026, 3, 31), "1500", b="300")
    month = get(client, "2026-03-31")["month"]
    assert month["pnl"] == "200.0000"
    assert month["pnl_complete"] is True
    assert month["pnl_reason"] is None


def test_summary_month_row_findings_do_not_count_when_b_used(client, db_session):
    _day(db_session, date(2026, 2, 27), "1000")
    _day(db_session, date(2026, 3, 5), "1200", deposits="100")
    _month(db_session, date(2026, 3, 31), "1500", b="300", findings=[{"code": "gap", "dates": [], "letters": []}])
    month = get(client, "2026-03-31")["month"]
    assert month["pnl_complete"] is True  # the month row's b is used


def test_summary_frozen(client, db_session):
    _day(db_session, date(2026, 3, 5), "1300", open_positions=2, initial_margin="10", withdrawable="5")
    value = get(client, "2026-03-09")["value"]
    assert value["frozen"] is True
    assert value["positions_open"] is True
    latest = get(client, "2026-03-09")["latest_daily"]
    assert latest["initial_margin"] == "10.0000"
    assert latest["withdrawable"] == "5.0000"
    assert get(client, "2026-03-05")["value"]["frozen"] is False
