"""Snapshot upsert idempotency, range listing, history endpoint."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_snapshot_service as snap_svc


def _fake_summary(market_value="1000", cost="800", unrealized_pnl="200", dividends="50", xirr="0.12", realized_pnl="0"):
    class S:
        total_market_value = Decimal(market_value)
        total_cost = Decimal(cost)
        total_unrealized_pnl = Decimal(unrealized_pnl)
        total_dividends = Decimal(dividends)
        total_realized_pnl = Decimal(realized_pnl)
        portfolio_xirr = Decimal(xirr) if xirr is not None else None
    return S()


def test_write_today_snapshot_inserts_row(db_session):
    target = date(2026, 5, 14)
    with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                      return_value=_fake_summary()):
        row = snap_svc.write_today_snapshot(db_session, today=target)
    assert row.date == target
    assert row.total_market_value == Decimal("1000")
    assert db_session.query(PortfolioSnapshot).count() == 1


def test_write_today_snapshot_is_idempotent_same_day(db_session):
    target = date(2026, 5, 14)
    with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                      return_value=_fake_summary(market_value="1000")):
        snap_svc.write_today_snapshot(db_session, today=target)
    with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                      return_value=_fake_summary(market_value="1500")):
        snap_svc.write_today_snapshot(db_session, today=target)
    rows = db_session.query(PortfolioSnapshot).all()
    assert len(rows) == 1
    assert rows[0].total_market_value == Decimal("1500")


def test_write_today_snapshot_preserves_null_xirr(db_session):
    target = date(2026, 5, 14)
    with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                      return_value=_fake_summary(xirr=None)):
        row = snap_svc.write_today_snapshot(db_session, today=target)
    assert row.portfolio_xirr is None


def test_list_snapshots_returns_inclusive_range_ascending(db_session):
    for d, mv in [
        (date(2026, 5, 10), "100"),
        (date(2026, 5, 12), "200"),
        (date(2026, 5, 14), "300"),
        (date(2026, 5, 16), "400"),
    ]:
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary(market_value=mv)):
            snap_svc.write_today_snapshot(db_session, today=d)
    rows = snap_svc.list_snapshots(db_session, from_date=date(2026, 5, 11), to_date=date(2026, 5, 14))
    assert [r.date for r in rows] == [date(2026, 5, 12), date(2026, 5, 14)]
    assert [r.total_market_value for r in rows] == [Decimal("200"), Decimal("300")]


def test_history_endpoint_no_range_returns_all(client, db_session):
    for d in (date(2020, 1, 1), date(2026, 5, 14)):
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary()):
            snap_svc.write_today_snapshot(db_session, today=d)
    # No from/to → return everything (no implicit 90-day window).
    response = client.get("/api/portfolio/history")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["date"] == "2020-01-01"
    assert body[1]["date"] == "2026-05-14"


def test_list_snapshots_downsample_week_keeps_last_per_iso_week(db_session):
    # 2026-05-11 Mon, 12 Tue, 13 Wed, 14 Thu, 15 Fri (ISO week 20)
    # 2026-05-18 Mon, 19 Tue (ISO week 21)
    days = [date(2026, 5, d) for d in (11, 12, 13, 14, 15, 18, 19)]
    for d in days:
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary()):
            snap_svc.write_today_snapshot(db_session, today=d)
    rows = snap_svc.list_snapshots(db_session, interval="week")
    # One per ISO week, last row in week wins.
    assert [r.date for r in rows] == [date(2026, 5, 15), date(2026, 5, 19)]


def test_list_snapshots_downsample_month_keeps_last_per_month(db_session):
    days = [date(2026, 4, 29), date(2026, 4, 30), date(2026, 5, 14), date(2026, 5, 16)]
    for d in days:
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary()):
            snap_svc.write_today_snapshot(db_session, today=d)
    rows = snap_svc.list_snapshots(db_session, interval="month")
    assert [r.date for r in rows] == [date(2026, 4, 30), date(2026, 5, 16)]


def test_list_snapshots_rejects_unknown_interval(db_session):
    import pytest as _pt
    with _pt.raises(ValueError):
        snap_svc.list_snapshots(db_session, interval="yearly")


def test_history_endpoint_interval_param(client, db_session):
    for d in [date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 15), date(2026, 5, 18)]:
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary()):
            snap_svc.write_today_snapshot(db_session, today=d)
    response = client.get("/api/portfolio/history", params={"interval": "week"})
    assert response.status_code == 200
    dates = [r["date"] for r in response.json()]
    assert dates == ["2026-05-15", "2026-05-18"]


def test_history_endpoint_rejects_invalid_interval(client):
    response = client.get("/api/portfolio/history", params={"interval": "yearly"})
    assert response.status_code == 422


def test_history_endpoint_empty_range(client):
    response = client.get("/api/portfolio/history", params={"from": "2020-01-01", "to": "2020-01-31"})
    assert response.status_code == 200
    assert response.json() == []


def test_history_endpoint_explicit_range(client, db_session):
    for d in (date(2026, 5, 10), date(2026, 5, 12), date(2026, 5, 14)):
        with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                          return_value=_fake_summary()):
            snap_svc.write_today_snapshot(db_session, today=d)
    response = client.get("/api/portfolio/history", params={"from": "2026-05-11", "to": "2026-05-13"})
    assert response.status_code == 200
    body = response.json()
    assert [row["date"] for row in body] == ["2026-05-12"]


def test_manual_snapshot_endpoint(client, db_session):
    with patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                      return_value=_fake_summary()):
        response = client.post("/api/portfolio/history/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert "date" in body
    assert body["total_market_value"] == "1000.0000"
    assert db_session.query(PortfolioSnapshot).count() == 1
