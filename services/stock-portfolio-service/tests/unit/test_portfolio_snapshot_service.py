"""Snapshot upsert idempotency, range listing, history endpoint."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_snapshot_service as snap_svc


@pytest.fixture(autouse=True)
def _stub_cash_service():
    """Isolate snapshot tests from cash_account_service DB state."""
    with patch.object(
        snap_svc.cash_account_service,
        "get_total_balance_in",
        return_value=(Decimal("0"), []),
    ) as stub:
        yield stub


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
    with (
        patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                     return_value=_fake_summary()),
        patch.object(snap_svc.cash_account_service, "get_total_balance_in",
                     return_value=(Decimal("0"), [])),
    ):
        row = snap_svc.write_today_snapshot(db_session, today=target)
    assert row.date == target
    assert row.total_market_value == Decimal("1000")
    assert db_session.query(PortfolioSnapshot).count() == 1


def test_write_today_snapshot_carries_cash_total(db_session):
    target = date(2026, 5, 14)
    with (
        patch.object(
            snap_svc.portfolio_service,
            "get_portfolio_summary",
            return_value=_fake_summary(),
        ),
        patch.object(
            snap_svc.cash_account_service,
            "get_total_balance_in",
            return_value=(Decimal("150500"), []),
        ) as cash_total,
    ):
        row = snap_svc.write_today_snapshot(db_session, today=target)

    assert row.total_cash_twd == Decimal("150500")
    cash_total.assert_called_once_with(db_session, "TWD", asof=target)


def test_write_today_snapshot_warns_skipped_currency_and_writes_cash(db_session, caplog):
    target = date(2026, 5, 14)
    with (
        patch.object(
            snap_svc.portfolio_service,
            "get_portfolio_summary",
            return_value=_fake_summary(),
        ),
        patch.object(
            snap_svc.cash_account_service,
            "get_total_balance_in",
            return_value=(Decimal("100000"), ["JPY"]),
        ),
    ):
        row = snap_svc.write_today_snapshot(db_session, today=target)

    assert row.total_cash_twd == Decimal("100000")
    assert "snapshot total_cash_twd skipped currencies: ['JPY']" in caplog.text


def test_write_today_snapshot_zero_accounts_writes_zero_cash(db_session):
    target = date(2026, 5, 14)
    with (
        patch.object(snap_svc.portfolio_service, "get_portfolio_summary",
                     return_value=_fake_summary()),
        patch.object(snap_svc.cash_account_service, "get_total_balance_in",
                     return_value=(Decimal("0"), [])),
    ):
        row = snap_svc.write_today_snapshot(db_session, today=target)

    assert row.total_cash_twd == Decimal("0")


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


def test_refresh_snapshot_cash_range_updates_only_cash_and_inserts_cash_only(
    db_session,
):
    existing_date = date(2026, 6, 1)
    insert_date = date(2026, 6, 2)
    skip_date = date(2026, 6, 3)
    db_session.add(
        PortfolioSnapshot(
            date=existing_date,
            total_market_value=Decimal("1000"),
            total_cost=Decimal("700"),
            total_unrealized_pnl=Decimal("300"),
            total_dividends=Decimal("10"),
            total_realized_pnl=Decimal("5"),
            total_cash_twd=Decimal("1"),
            portfolio_xirr=Decimal("0.12"),
        )
    )
    db_session.commit()

    cash_by_date = {
        existing_date: Decimal("250"),
        insert_date: Decimal("500"),
        skip_date: Decimal("0"),
    }

    def cash_total(_db, _currency, asof=None):
        return cash_by_date[asof], []

    with (
        patch.object(
            snap_svc.cash_account_service,
            "get_total_balance_in",
            side_effect=cash_total,
        ),
        patch.object(
            snap_svc,
            "_cash_activity_dates",
            return_value={insert_date},
        ),
    ):
        snap_svc.refresh_snapshot_cash_range(
            db_session,
            existing_date,
            skip_date,
        )

    rows = {
        row.date: row
        for row in db_session.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date)
    }
    assert set(rows) == {existing_date, insert_date}

    existing = rows[existing_date]
    assert existing.total_cash_twd == Decimal("250")
    assert existing.total_market_value == Decimal("1000")
    assert existing.total_cost == Decimal("700")
    assert existing.total_unrealized_pnl == Decimal("300")
    assert existing.total_dividends == Decimal("10")
    assert existing.total_realized_pnl == Decimal("5")
    assert existing.portfolio_xirr == Decimal("0.120000")

    inserted = rows[insert_date]
    assert inserted.total_cash_twd == Decimal("500")
    assert inserted.total_market_value == Decimal("0")
    assert inserted.total_cost == Decimal("0")
    assert inserted.total_unrealized_pnl == Decimal("0")
    assert inserted.total_dividends == Decimal("0")
    assert inserted.total_realized_pnl == Decimal("0")
    assert inserted.portfolio_xirr is None


def test_refresh_snapshot_cash_range_inserts_row_for_negative_cash(db_session):
    """Backdated withdrawal on a date with no prior snapshot leaves cash
    negative (e.g. staking liability). The row MUST be written so the chart
    surfaces the overdraft instead of hiding it. Date is in
    cash_activity_dates because the txn itself happens on that date."""
    target = date(2026, 6, 5)

    with (
        patch.object(
            snap_svc.cash_account_service,
            "get_total_balance_in",
            return_value=(Decimal("-500"), []),
        ),
        patch.object(snap_svc, "_cash_activity_dates", return_value={target}),
    ):
        snap_svc.refresh_snapshot_cash_range(db_session, target, target)

    row = db_session.get(PortfolioSnapshot, target)
    assert row is not None
    assert row.total_cash_twd == Decimal("-500")
    assert row.total_market_value == Decimal("0")


def test_refresh_snapshot_cash_range_skips_insert_on_non_activity_date(db_session):
    """Backdated CRUD walks every day in range, but cash-only rows must
    only be inserted on dates with actual cash activity. Without this gate
    a 1-year backdated deposit would insert ~365 phantom rows."""
    start = date(2026, 6, 1)
    end = date(2026, 6, 3)
    activity_date = date(2026, 6, 1)  # only this day has cash activity

    with (
        patch.object(
            snap_svc.cash_account_service,
            "get_total_balance_in",
            return_value=(Decimal("1000"), []),
        ),
        patch.object(snap_svc, "_cash_activity_dates", return_value={activity_date}),
    ):
        snap_svc.refresh_snapshot_cash_range(db_session, start, end)

    rows = {row.date for row in db_session.query(PortfolioSnapshot).all()}
    assert rows == {activity_date}, (
        f"only the activity date may be inserted; got {rows}"
    )


def test_refresh_snapshot_cash_range_deletes_helper_only_row_when_cash_zero(db_session):
    """A helper-created cash-only row whose cash later drops back to zero
    must be removed so the chart does not show a phantom zero point."""
    target = date(2026, 6, 6)
    db_session.add(
        PortfolioSnapshot(
            date=target,
            total_market_value=Decimal("0"),
            total_cost=Decimal("0"),
            total_unrealized_pnl=Decimal("0"),
            total_dividends=Decimal("0"),
            total_realized_pnl=Decimal("0"),
            total_cash_twd=Decimal("400"),
            portfolio_xirr=None,
        )
    )
    db_session.commit()

    with patch.object(
        snap_svc.cash_account_service,
        "get_total_balance_in",
        return_value=(Decimal("0"), []),
    ):
        snap_svc.refresh_snapshot_cash_range(db_session, target, target)

    assert db_session.get(PortfolioSnapshot, target) is None


def test_refresh_snapshot_cash_range_keeps_stock_row_when_cash_zero(db_session):
    """A row owned by replay_snapshots_range (stock columns non-zero) must
    NOT be deleted when cash drops to zero — just update the cash column."""
    target = date(2026, 6, 7)
    db_session.add(
        PortfolioSnapshot(
            date=target,
            total_market_value=Decimal("1500"),
            total_cost=Decimal("1000"),
            total_unrealized_pnl=Decimal("500"),
            total_dividends=Decimal("0"),
            total_realized_pnl=Decimal("0"),
            total_cash_twd=Decimal("250"),
            portfolio_xirr=None,
        )
    )
    db_session.commit()

    with patch.object(
        snap_svc.cash_account_service,
        "get_total_balance_in",
        return_value=(Decimal("0"), []),
    ):
        snap_svc.refresh_snapshot_cash_range(db_session, target, target)

    row = db_session.get(PortfolioSnapshot, target)
    assert row is not None
    assert row.total_cash_twd == Decimal("0")
    assert row.total_market_value == Decimal("1500")


def test_refresh_snapshot_cash_range_inverted_range_noops(db_session):
    with patch.object(
        snap_svc.cash_account_service,
        "get_total_balance_in",
    ) as cash_total:
        snap_svc.refresh_snapshot_cash_range(
            db_session,
            date(2026, 6, 3),
            date(2026, 6, 1),
        )

    cash_total.assert_not_called()
    assert db_session.query(PortfolioSnapshot).count() == 0


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
