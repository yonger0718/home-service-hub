"""Futures equity in net worth (spec §6.5): every total, every snapshot writer, the replay."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)
from app.services import cash_account_service
from app.services import networth_backfill_service as nb
from app.services import portfolio_snapshot_service as snap_svc
from app.services import sinopac_futures_series as series

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
ZERO = Decimal("0")


def _day(db, d, total):
    row = FuturesStatementDay(
        statement_date=d, accepted_sha256="a" * 64, accepted_at=NOW, open_positions=0,
        **{k: Decimal("0") for k in DAY_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.balance = Decimal(total)
    db.add(row)
    db.commit()


def _month(db, month_end, total):
    row = FuturesStatementMonth(
        month=month_end.replace(day=1), month_end=month_end, accepted_sha256="b" * 64, accepted_at=NOW,
        open_positions=0, **{c: Decimal("0") for c in MONTH_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.m = Decimal(total)
    db.add(row)
    db.commit()


def _snap(db, d, *, mv="0", cost="0", cash="0", futures="0", dividends="0", realized="0", xirr=None):
    db.add(PortfolioSnapshot(
        date=d, total_market_value=Decimal(mv), total_cost=Decimal(cost),
        total_unrealized_pnl=Decimal(mv) - Decimal(cost), total_dividends=Decimal(dividends),
        total_realized_pnl=Decimal(realized), total_cash_twd=Decimal(cash), futures_equity_twd=Decimal(futures),
        portfolio_xirr=Decimal(xirr) if xirr is not None else None,
    ))
    db.commit()


def _price(db, d, symbol="9999"):
    db.add(PriceHistory(symbol=symbol, date=d, close=Decimal("10"), source="TWSE"))
    db.commit()


def _cash(value):
    return patch.object(cash_account_service, "get_total_balance_in", return_value=(Decimal(value), []))


class _Summary:
    total_market_value = Decimal("1000")
    total_cost = Decimal("800")
    total_unrealized_pnl = Decimal("200")
    total_dividends = Decimal("0")
    portfolio_xirr = None


# ---------- totals ----------


def test_today_snapshot_includes_futures(db_session):
    _day(db_session, date(2026, 3, 10), "500")
    with _cash("70"), patch.object(snap_svc.portfolio_service, "get_portfolio_summary", return_value=_Summary()):
        row = snap_svc.write_today_snapshot(db_session, today=date(2026, 3, 12))
    assert row.futures_equity_twd == Decimal("500")
    assert row.total_market_value == Decimal("1000")
    assert row.total_cash_twd == Decimal("70")


def test_today_snapshot_before_first_source_is_zero(db_session):
    _day(db_session, date(2026, 3, 10), "500")
    with _cash("0"), patch.object(snap_svc.portfolio_service, "get_portfolio_summary", return_value=_Summary()):
        row = snap_svc.write_today_snapshot(db_session, today=date(2026, 3, 9))
    assert row.futures_equity_twd == ZERO


def test_history_total_includes_futures(client, db_session):
    _snap(db_session, date(2026, 3, 10), mv="100", cost="80", cash="50", futures="30")
    points = client.get("/api/portfolio/history").json()
    assert len(points) == 1
    assert Decimal(points[0]["futures_equity_twd"]) == Decimal("30")
    assert Decimal(points[0]["total_assets_twd"]) == Decimal("180")


def test_summary_total_includes_futures(client, db_session):
    _day(db_session, date(2026, 1, 5), "1234.5")
    with _cash("100"):
        body = client.get("/api/portfolio/summary").json()
    assert Decimal(body["futures_equity_twd"]) == Decimal("1234.5")
    assert Decimal(body["total_assets_twd"]) == Decimal(body["total_market_value"]) + Decimal("100") + Decimal(
        "1234.5")


def test_summary_without_futures_is_zero(client, db_session):
    with _cash("100"):
        body = client.get("/api/portfolio/summary").json()
    assert Decimal(body["futures_equity_twd"]) == ZERO
    assert Decimal(body["total_assets_twd"]) == Decimal(body["total_market_value"]) + Decimal("100")


def test_negative_futures_equity_in_total(client, db_session):
    _day(db_session, date(2026, 1, 5), "-100")
    _snap(db_session, date(2026, 3, 10), mv="100", cash="0", futures="-100")
    point = client.get("/api/portfolio/history").json()[0]
    assert Decimal(point["total_assets_twd"]) == ZERO
    assert Decimal(point["futures_equity_twd"]) == Decimal("-100")
    with _cash("0"):
        body = client.get("/api/portfolio/summary").json()
    assert Decimal(body["futures_equity_twd"]) == Decimal("-100")
    assert Decimal(body["total_assets_twd"]) == Decimal(body["total_market_value"]) - Decimal("100")
    with _cash("0"), patch.object(snap_svc.portfolio_service, "get_portfolio_summary", return_value=_Summary()):
        row = snap_svc.write_today_snapshot(db_session, today=date(2026, 3, 11))
    assert row.futures_equity_twd == Decimal("-100")


# ---------- refresh_snapshot_cash_range ----------


def test_zero_cash_row_with_futures_kept(db_session):
    d = date(2026, 3, 10)
    _snap(db_session, d, cash="100", futures="300")
    with _cash("0"):
        snap_svc.refresh_snapshot_cash_range(db_session, d, d)
    row = db_session.get(PortfolioSnapshot, d)
    assert row is not None
    assert row.total_cash_twd == ZERO
    assert row.futures_equity_twd == Decimal("300")


def test_zero_cash_row_without_futures_still_deleted(db_session):
    d = date(2026, 3, 10)
    _snap(db_session, d, cash="100")
    with _cash("0"):
        snap_svc.refresh_snapshot_cash_range(db_session, d, d)
    assert db_session.get(PortfolioSnapshot, d) is None


def test_cash_insert_carries_futures(db_session):
    _day(db_session, date(2026, 3, 8), "400")
    d = date(2026, 3, 10)
    with _cash("100"), patch.object(snap_svc, "_cash_activity_dates", return_value={d}):
        snap_svc.refresh_snapshot_cash_range(db_session, d, d)
    row = db_session.get(PortfolioSnapshot, d)
    assert row.total_cash_twd == Decimal("100")
    assert row.futures_equity_twd == Decimal("400")


# ---------- replay ----------


@pytest.mark.parametrize("trading_day", [False, True])
def test_replay_writes_futures_only_date(db_session, trading_day):
    d = date(2026, 3, 10)  # a Tuesday; no stock, no cash activity
    _day(db_session, d, "800")
    if trading_day:
        for x in (date(2026, 3, 9), d, date(2026, 3, 11)):
            _price(db_session, x)
    with _cash("0"):
        nb.replay_snapshots_range(db_session, date(2026, 3, 9), date(2026, 3, 11))
    rows = db_session.query(PortfolioSnapshot).all()
    assert [(r.date, r.futures_equity_twd) for r in rows] == [(d, Decimal("800"))]


def test_replay_monthly_source_date_written(db_session):
    _month(db_session, date(2026, 2, 28), "900")  # a Saturday
    with _cash("0"):
        nb.replay_snapshots_range(db_session, date(2026, 2, 27), date(2026, 3, 1))
    rows = db_session.query(PortfolioSnapshot).all()
    assert [(r.date, r.futures_equity_twd) for r in rows] == [(date(2026, 2, 28), Decimal("900"))]


def test_replay_stock_rows_carry_futures(db_session):
    """The trading-day row and the held-stock forward-fill row both take the series value (not the default 0)."""
    from app.models import portfolio as portfolio_models

    _day(db_session, date(2026, 3, 2), "250")
    db_session.add(portfolio_models.Transaction(
        symbol="9999", type=portfolio_models.TransactionType.BUY, quantity=10, price=Decimal("10"),
        fee=Decimal("0"), tax=Decimal("0"), trade_date=datetime(2026, 3, 6, tzinfo=timezone.utc),
    ))
    db_session.commit()
    _price(db_session, date(2026, 3, 6))  # Friday; 03-07 is a Saturday (forward fill)
    with _cash("100"):
        nb.replay_snapshots_range(db_session, date(2026, 3, 6), date(2026, 3, 7))
    rows = {r.date: r for r in db_session.query(PortfolioSnapshot).all()}
    assert set(rows) == {date(2026, 3, 6), date(2026, 3, 7)}
    for row in rows.values():
        assert row.futures_equity_twd == Decimal("250")
        assert row.total_market_value == Decimal("100")


def test_replay_matches_live_writer(db_session):
    d = date(2026, 3, 10)
    _day(db_session, date(2026, 3, 6), "300")
    _day(db_session, d, "650")
    with _cash("70"):
        nb.replay_snapshots_range(db_session, d, d)
        replayed = db_session.get(PortfolioSnapshot, d)
        r_futures = replayed.futures_equity_twd
        r_total = replayed.total_market_value + replayed.total_cash_twd + replayed.futures_equity_twd
        db_session.delete(replayed)
        db_session.commit()
        live = snap_svc.write_today_snapshot(db_session, today=d)
    assert live.futures_equity_twd == r_futures == Decimal("650")
    assert live.total_market_value + live.total_cash_twd + live.futures_equity_twd == r_total == Decimal("720")


def test_phantom_filter_keeps_futures_row(db_session):
    kept, dropped = date(2026, 3, 10), date(2026, 3, 11)
    for x in (kept, dropped):
        _price(db_session, x)
    _snap(db_session, kept, futures="200")  # written by the live writer on a non-source day
    _snap(db_session, dropped)
    with _cash("0"):
        nb.replay_snapshots_range(db_session, kept, dropped)
    assert db_session.get(PortfolioSnapshot, kept).futures_equity_twd == Decimal("200")
    assert db_session.get(PortfolioSnapshot, dropped) is None


# ---------- replay: zero-valued source dates (R14 finding 1) ----------


def _rows(db):
    db.expire_all()
    return {r.date: r for r in db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date)}


@pytest.mark.parametrize("trading_day", [False, True])
def test_replay_positive_to_zero_transition_kept(db_session, trading_day):
    """No stock, cash or (for False) price history: a source of 100 then a source of 0 gives two rows, the second all
    zero (futures 0, total 0), and the phantom filter leaves it."""
    first, second = date(2026, 3, 9), date(2026, 3, 10)
    _day(db_session, first, "100")
    _day(db_session, second, "0")
    if trading_day:
        for x in (first, second):
            _price(db_session, x)
    with _cash("0"):
        nb.replay_snapshots_range(db_session, first, second)
    rows = _rows(db_session)
    assert set(rows) == {first, second}
    assert rows[first].futures_equity_twd == Decimal("100")
    zero = rows[second]
    assert zero.futures_equity_twd == ZERO
    assert zero.total_market_value + zero.total_cash_twd + zero.futures_equity_twd == ZERO


def test_replay_weekend_monthly_zero_source_written(db_session):
    """A month end on a Saturday with no activity and a zero value still gets its row."""
    _day(db_session, date(2026, 2, 27), "100")  # Friday
    _month(db_session, date(2026, 2, 28), "0")  # Saturday
    with _cash("0"):
        nb.replay_snapshots_range(db_session, date(2026, 2, 27), date(2026, 3, 1))
    rows = _rows(db_session)
    assert [(d, r.futures_equity_twd) for d, r in rows.items()] == [
        (date(2026, 2, 27), Decimal("100")),
        (date(2026, 2, 28), ZERO),
    ]


# ---------- replay: held-stock price gap (R14 finding 2) ----------


def _buy(db, d, symbol="9999", qty=10, price="10"):
    from app.models import portfolio as portfolio_models

    db.add(portfolio_models.Transaction(
        symbol=symbol, type=portfolio_models.TransactionType.BUY, quantity=qty, price=Decimal(price),
        fee=Decimal("0"), tax=Decimal("0"), trade_date=datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
    ))
    db.commit()


def _columns(row):
    return {
        c: getattr(row, c) for c in (
            "total_market_value", "total_cost", "total_unrealized_pnl", "total_dividends", "total_realized_pnl",
            "total_cash_twd", "portfolio_xirr",
        )
    }


GAP = date(2026, 3, 10)  # Tuesday: price_history covers it (another symbol) but not the held 9999


def _cash_by_date(values: dict):
    """Cash per as-of date (default 0)."""
    def balance(_db, _currency, *, asof):
        return Decimal(values.get(asof, "0")), []

    return patch.object(cash_account_service, "get_total_balance_in", side_effect=balance)


@pytest.mark.parametrize("source_on_gap", [False, True])
def test_replay_gap_day_existing_futures_row_carries_last_good_day(db_session, source_on_gap):
    """R16: an existing gap row (mv 0 / cost > 0, futures != 0) is kept and rewritten like the skip-day forward
    fill: market and cost of the previous good day, that day's cash, the series futures, XIRR NULL — no dip."""
    good = date(2026, 3, 9)
    _buy(db_session, date(2026, 3, 2))
    _price(db_session, good)  # 9999 priced on 03-09 only: 100 market, 100 cost
    _price(db_session, GAP, symbol="8888")
    _day(db_session, GAP if source_on_gap else date(2026, 3, 6), "300")
    _snap(db_session, GAP, mv="0", cost="100", cash="7", futures="50", dividends="3", realized="4", xirr="0.1234")
    with _cash_by_date({good: "50", GAP: "70"}):
        nb.replay_snapshots_range(db_session, good, GAP)
    rows = _rows(db_session)
    assert GAP in rows
    row, prev = rows[GAP], rows[good]
    assert (row.total_market_value, row.total_cost) == (prev.total_market_value, prev.total_cost) == (
        Decimal("100"), Decimal("100"))
    assert row.total_unrealized_pnl == ZERO
    assert row.total_cash_twd == Decimal("70")
    assert row.futures_equity_twd == Decimal("300")
    assert (row.total_dividends, row.total_realized_pnl) == (ZERO, ZERO)  # the replay's running totals
    assert row.portfolio_xirr is None


@pytest.mark.parametrize("prior", ["seeded", "replayed", "surviving", "none"])
def test_replay_gap_source_date_without_row_carries_market(db_session, prior):
    """R16: a gap source date with no row gets one carrying the last good market value and cost (the replay's
    last trading day, else the latest surviving row before it), that day's cash and the series futures; never a
    fetched or invented price."""
    _buy(db_session, date(2026, 3, 2))
    _price(db_session, GAP, symbol="8888")
    _day(db_session, GAP, "800")
    start, expected = GAP, (ZERO, ZERO)
    if prior == "seeded":  # a row before the range seeds last_trading_mv / last_trading_cost
        _snap(db_session, date(2026, 3, 9), mv="120", cost="100", cash="50", xirr="0.1")
        expected = (Decimal("120"), Decimal("100"))
    elif prior == "replayed":  # 9999 priced on 03-09: the replay writes that trading day first
        _price(db_session, date(2026, 3, 9))
        start, expected = date(2026, 3, 9), (Decimal("100"), Decimal("100"))
    elif prior == "surviving":  # no trading day in range; a kept weekend row in range is the fallback
        _snap(db_session, date(2026, 3, 8), mv="150", cost="100", cash="5")
        start, expected = date(2026, 3, 8), (Decimal("150"), Decimal("100"))
    with _cash("50"):
        nb.replay_snapshots_range(db_session, start, GAP)
    rows = _rows(db_session)
    assert GAP in rows
    row = rows[GAP]
    assert (row.total_market_value, row.total_cost) == expected
    assert row.total_unrealized_pnl == expected[0] - expected[1]
    assert row.total_cash_twd == Decimal("50")
    assert row.futures_equity_twd == Decimal("800")
    assert row.portfolio_xirr is None


def test_history_total_on_gap_source_date(client, db_session):
    """/history on a gap source date: previous good day's market + that day's cash + the series futures."""
    good = date(2026, 3, 9)
    _buy(db_session, date(2026, 3, 2))
    _price(db_session, good)
    _price(db_session, GAP, symbol="8888")
    _day(db_session, GAP, "800")
    with _cash_by_date({good: "50", GAP: "70"}):
        nb.replay_snapshots_range(db_session, good, GAP)
    points = {p["date"]: p for p in client.get("/api/portfolio/history").json()}
    prev, gap = points[good.isoformat()], points[GAP.isoformat()]
    assert Decimal(gap["total_assets_twd"]) == Decimal(prev["total_market_value"]) + Decimal("70") + Decimal("800")
    assert Decimal(gap["total_assets_twd"]) == Decimal("970")


WEEKEND = date(2026, 3, 7)  # Saturday at the range start: stock held, no prior mv>0 row, so no held forward fill


@pytest.mark.parametrize("prior", ["surviving", "none", "zero_series"])
def test_replay_skip_day_futures_row_kept(db_session, prior):
    """R15 + R17: a skip-day row the stale delete would drop (mv 0, cost > 0) is kept when the series is non-zero
    and rewritten with the R16 carry: market and cost of the latest surviving row before it (``last_trading_mv`` is
    None at the range start), else 0 / 0; that day's cash; the series futures; XIRR NULL. A zero-series control is
    still deleted."""
    sunday = date(2026, 3, 8)
    _buy(db_session, date(2026, 3, 2))
    if prior != "zero_series":
        _day(db_session, date(2026, 3, 6), "300")  # Friday source; the weekend is not a source date
    start = sunday
    if prior == "surviving":  # a kept Saturday row in range (mv > 0, never stale-deleted) is the carry source
        _snap(db_session, WEEKEND, mv="150", cost="100", cash="5")
        start = WEEKEND
    _snap(db_session, sunday, mv="0", cost="100", cash="7", futures="0" if prior == "zero_series" else "50",
          dividends="3", realized="4", xirr="0.1234")
    with _cash("999"):
        nb.replay_snapshots_range(db_session, start, sunday)
    row = _rows(db_session).get(sunday)
    if prior == "zero_series":
        assert row is None
        return
    assert row is not None
    expected = (Decimal("150"), Decimal("100")) if prior == "surviving" else (ZERO, ZERO)
    assert (row.total_market_value, row.total_cost) == expected
    assert row.total_unrealized_pnl == expected[0] - expected[1]
    assert row.total_cash_twd == Decimal("999")
    assert row.futures_equity_twd == Decimal("300")
    assert (row.total_dividends, row.total_realized_pnl) == (ZERO, ZERO)  # the replay's running totals
    assert row.portfolio_xirr is None


def test_replay_gap_non_source_zero_series_still_deleted(db_session):
    """Pre-futures history: a gap day with no futures source and a zero series keeps the legacy stale delete."""
    _buy(db_session, date(2026, 3, 2))
    _price(db_session, GAP, symbol="8888")
    _snap(db_session, GAP, mv="0", cost="100", cash="7")
    with _cash("7"):
        nb.replay_snapshots_range(db_session, GAP, GAP)
    assert GAP not in _rows(db_session)


# ---------- the shared lock ----------


def _record_lock(monkeypatch, events):
    real_lock = series.futures_lock
    real_by_date = series.equity_by_date
    real_at = series.equity_at
    real_sources = series.source_dates

    def lock(db, *, shared=False):
        events.append(("lock", shared))
        real_lock(db, shared=shared)

    def by_date(db, start, end):
        events.append(("read", None))
        return real_by_date(db, start, end)

    def at(db, d):
        events.append(("read", None))
        return real_at(db, d)

    def sources(db):
        events.append(("read", None))
        return real_sources(db)

    monkeypatch.setattr(series, "futures_lock", lock)
    monkeypatch.setattr(series, "equity_by_date", by_date)
    monkeypatch.setattr(series, "equity_at", at)
    monkeypatch.setattr(series, "source_dates", sources)


def test_today_snapshot_takes_shared_lock_before_reading(db_session, monkeypatch):
    events: list = []
    _record_lock(monkeypatch, events)
    with _cash("0"), patch.object(snap_svc.portfolio_service, "get_portfolio_summary", return_value=_Summary()):
        snap_svc.write_today_snapshot(db_session, today=date(2026, 3, 10))
    assert events and events[0] == ("lock", True)
    assert ("read", None) in events


def test_cash_range_takes_shared_lock_before_reading(db_session, monkeypatch):
    events: list = []
    _record_lock(monkeypatch, events)
    d = date(2026, 3, 10)
    with _cash("100"), patch.object(snap_svc, "_cash_activity_dates", return_value={d}):
        snap_svc.refresh_snapshot_cash_range(db_session, d, d)
    assert events and events[0] == ("lock", True)
    assert ("read", None) in events


def test_replay_takes_shared_lock_before_reading(db_session, monkeypatch):
    events: list = []
    _record_lock(monkeypatch, events)
    with _cash("0"):
        nb.replay_snapshots_range(db_session, date(2026, 3, 9), date(2026, 3, 10))
    assert events[0] == ("lock", True)
    assert events.count(("lock", True)) == 1
    assert ("read", None) in events
