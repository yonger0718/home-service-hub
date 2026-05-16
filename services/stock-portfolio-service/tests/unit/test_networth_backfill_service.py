"""Networth backfill — driver + replay correctness and idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable, List

import pytest

from app.models import portfolio as portfolio_models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import networth_backfill_service as nbs


# ---------- Fixtures ----------


@dataclass
class _FakeRow:
    symbol: str
    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    turnover: Decimal | None
    source: str


def _row(symbol: str, d: date, close: str, src: str = "TWSE") -> _FakeRow:
    return _FakeRow(
        symbol=symbol,
        date=d,
        open=None,
        high=None,
        low=None,
        close=Decimal(close),
        volume=None,
        turnover=None,
        source=src,
    )


def _seed_tx(
    db,
    *,
    symbol: str,
    side: portfolio_models.TransactionType,
    qty: int,
    price: str,
    trade_date: date,
    fee: str = "0",
):
    tx = portfolio_models.Transaction(
        symbol=symbol,
        type=side,
        quantity=qty,
        price=Decimal(price),
        trade_date=datetime.combine(trade_date, datetime.min.time(), tzinfo=timezone.utc),
        fee=Decimal(fee),
        tax=Decimal("0"),
    )
    db.add(tx)
    db.flush()
    return tx


def _seed_dividend(db, *, symbol: str, amount: str, ex_date: date):
    div = portfolio_models.Dividend(
        symbol=symbol,
        amount=Decimal(amount),
        ex_dividend_date=datetime.combine(ex_date, datetime.min.time(), tzinfo=timezone.utc),
        fee=Decimal("0"),
        tax=Decimal("0"),
        stock_dividend_shares=0,
        source="test",
    )
    db.add(div)
    db.flush()
    return div


def _seed_price(db, *, symbol: str, d: date, close: str, source: str = "TWSE"):
    db.add(
        PriceHistory(
            symbol=symbol,
            date=d,
            close=Decimal(close),
            source=source,
        )
    )
    db.flush()


# ---------- _iter_trading_days ----------


def test_iter_trading_days_skips_weekend():
    days = list(nbs._iter_trading_days(date(2026, 5, 15), date(2026, 5, 18)))
    # Fri 5/15, Sat skip, Sun skip, Mon 5/18
    assert days == [date(2026, 5, 15), date(2026, 5, 18)]


# ---------- _fetch_with_retry ----------


def test_fetch_with_retry_returns_first_success():
    calls = []
    sleeps: List[float] = []

    def fetcher(d):
        calls.append(d)
        return [_row("2330", d, "600")]

    out = nbs._fetch_with_retry(fetcher, date(2026, 5, 14), sleep=sleeps.append)
    assert len(out) == 1
    assert calls == [date(2026, 5, 14)]
    assert sleeps == []


def test_fetch_with_retry_retries_then_succeeds():
    state = {"n": 0}
    sleeps: List[float] = []

    def fetcher(d):
        state["n"] += 1
        if state["n"] < 3:
            return []
        return [_row("2330", d, "600")]

    out = nbs._fetch_with_retry(
        fetcher, date(2026, 5, 14), delays=(0.1, 0.1), sleep=sleeps.append
    )
    assert len(out) == 1
    assert state["n"] == 3
    assert sleeps == [0.1, 0.1]


def test_fetch_with_retry_holiday_returns_empty():
    sleeps: List[float] = []
    out = nbs._fetch_with_retry(
        lambda d: [], date(2026, 5, 14), delays=(0.1, 0.1), sleep=sleeps.append
    )
    assert out == []
    assert sleeps == [0.1, 0.1]


# ---------- backfill_prices_range ----------


def test_backfill_prices_range_throttles_between_dates(db_session):
    sleeps: List[float] = []
    def twse(d):
        return [_row("2330", d, "600")]

    def tpex(d):
        return []
    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 15),
        throttle_sec=1.5,
        sleep=sleeps.append,
        twse_fetcher=twse,
        tpex_fetcher=tpex,
    )
    assert result.dates_processed == 2
    # One throttle sleep between the two trading days (no sleep before first).
    assert 1.5 in sleeps


def test_backfill_prices_range_holiday_skip_no_rows(db_session):
    sleeps: List[float] = []
    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 14),
        throttle_sec=0,
        sleep=sleeps.append,
        twse_fetcher=lambda d: [],
        tpex_fetcher=lambda d: [],
    )
    assert result.dates_processed == 0
    assert result.dates_skipped == 1
    assert db_session.query(PriceHistory).count() == 0


def test_backfill_prices_range_single_source_empty_is_failure_not_holiday(db_session):
    """When one source is cached and the other returns empty, treat the empty
    fetch as a failure (cached side proves the market was open), not a holiday."""
    _seed_price(db_session, symbol="2330", d=date(2026, 5, 14), close="600", source="TWSE")
    db_session.commit()
    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 14),
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=lambda d: [_row("2330", d, "601")],
        tpex_fetcher=lambda d: [],
    )
    assert result.dates_skipped == 0
    assert len(result.errors) == 1
    assert result.errors[0].date == date(2026, 5, 14)
    assert "TPEx" in result.errors[0].reason


def test_backfill_prices_range_skips_already_fetched_dates(db_session):
    """If price_history has rows from BOTH sources for date D, no HTTP call fires."""
    _seed_price(db_session, symbol="2330", d=date(2026, 5, 14), close="600", source="TWSE")
    _seed_price(db_session, symbol="6488", d=date(2026, 5, 14), close="100", source="TPEx")
    db_session.commit()
    twse_calls: List[date] = []
    tpex_calls: List[date] = []

    def twse(d):
        twse_calls.append(d)
        return [_row("2330", d, "601")]

    def tpex(d):
        tpex_calls.append(d)
        return [_row("6488", d, "101")]

    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 14),
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=twse,
        tpex_fetcher=tpex,
    )
    assert twse_calls == []
    assert tpex_calls == []
    assert result.dates_skipped == 1
    assert result.dates_processed == 0


def test_backfill_prices_range_skips_weekend(db_session):
    fetched: List[date] = []

    def twse(d):
        fetched.append(d)
        return [_row("2330", d, "600")]

    nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 16),  # Sat
        date(2026, 5, 17),  # Sun
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=twse,
        tpex_fetcher=lambda d: [],
    )
    assert fetched == []


def test_backfill_prices_range_isolates_failures(db_session):
    def twse(d):
        if d == date(2026, 5, 14):
            raise RuntimeError("boom")
        return [_row("2330", d, "600")]

    result = nbs.backfill_prices_range(
        db_session,
        date(2026, 5, 14),
        date(2026, 5, 15),
        throttle_sec=0,
        sleep=lambda _s: None,
        twse_fetcher=twse,
        tpex_fetcher=lambda d: [],
    )
    assert result.dates_processed == 1
    assert len(result.errors) == 1
    assert result.errors[0].date == date(2026, 5, 14)


# ---------- replay_snapshots_range ----------


def test_replay_simple_buy_and_close(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        price="500",
        trade_date=date(2026, 5, 14),
        fee="20",
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="510")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="520")
    db_session.commit()

    result = nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 15))
    assert result.snapshots_written == 2

    snaps = {
        s.date: s
        for s in db_session.query(PortfolioSnapshot).all()
    }
    # MV: 100*510 = 51000; cost: 100*500 + 20 = 50020
    assert snaps[date(2026, 5, 14)].total_market_value == Decimal("51000")
    assert snaps[date(2026, 5, 14)].total_cost == Decimal("50020")
    assert snaps[date(2026, 5, 14)].total_unrealized_pnl == Decimal("980")
    assert snaps[date(2026, 5, 15)].total_market_value == Decimal("52000")


def test_replay_sell_reduces_holdings_and_cost(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=100,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.SELL,
        qty=40,
        price="600",
        trade_date=date(2026, 5, 15),
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="600")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 15))
    snaps = {s.date: s for s in db_session.query(PortfolioSnapshot).all()}

    # After BUY 100 @ 500: qty=100, cost=50000
    # After SELL 40 @ 600: qty=60, cost reduced by 40*(50000/100)=20000 → cost=30000
    assert snaps[date(2026, 5, 15)].total_market_value == Decimal("36000")  # 60*600
    assert snaps[date(2026, 5, 15)].total_cost == Decimal("30000")


def test_replay_accumulates_dividends_up_to_date(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    _seed_dividend(db_session, symbol=sym, amount="100", ex_date=date(2026, 5, 14))
    _seed_dividend(db_session, symbol=sym, amount="200", ex_date=date(2026, 5, 15))
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="500")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 15))
    snaps = {s.date: s for s in db_session.query(PortfolioSnapshot).all()}
    assert snaps[date(2026, 5, 14)].total_dividends == Decimal("100")
    assert snaps[date(2026, 5, 15)].total_dividends == Decimal("300")


def test_replay_skips_dates_with_no_price_history(db_session, caplog):
    """Full market-holiday dates (no price_history rows at all) must not
    produce a snapshot — otherwise MV=0 would render a spurious crash
    on the chart.
    """
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 14))
    assert db_session.query(PortfolioSnapshot).count() == 0


def test_replay_missing_symbol_price_contributes_zero(db_session, caplog):
    """If market is open (some price row exists) but the specific holding's
    price is missing, that holding contributes 0 to MV — snapshot is still
    written so the time-series stays continuous.
    """
    sym = "2330"
    other = "OTHER"
    d = date(2026, 5, 14)
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=d,
    )
    _seed_price(db_session, symbol=other, d=d, close="1")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, d, d)
    snap = db_session.query(PortfolioSnapshot).one()
    assert snap.total_market_value == Decimal("0")
    assert snap.total_cost == Decimal("5000")


def test_replay_idempotent(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="600")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 14))
    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 14))
    assert db_session.query(PortfolioSnapshot).count() == 1
    snap = db_session.query(PortfolioSnapshot).one()
    assert snap.total_market_value == Decimal("6000")


def test_replay_handles_day_trade_with_sell_id_lower_than_buy(db_session):
    """SELL inserted with smaller id than BUY (e.g. CSV reorder) must still net to zero.

    Regression test: replay used to sort by (trade_date, id) only, so if SELL
    landed with a lower id it'd be processed first against qty=0, silently
    skipped, then BUY would inflate holdings forever.
    """
    sym = "2330"
    d = date(2026, 5, 14)
    # SELL first (smaller id), BUY second (larger id) — wrong chronological order.
    sell = portfolio_models.Transaction(
        symbol=sym,
        type=portfolio_models.TransactionType.SELL,
        quantity=100,
        price=Decimal("510"),
        trade_date=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
        fee=Decimal("0"),
        tax=Decimal("0"),
    )
    db_session.add(sell)
    db_session.flush()  # gets smaller id
    buy = portfolio_models.Transaction(
        symbol=sym,
        type=portfolio_models.TransactionType.BUY,
        quantity=100,
        price=Decimal("500"),
        trade_date=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
        fee=Decimal("0"),
        tax=Decimal("0"),
    )
    db_session.add(buy)
    db_session.flush()
    _seed_price(db_session, symbol=sym, d=d, close="505")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, d, d)
    snap = db_session.query(PortfolioSnapshot).one()
    # Net qty=0 → MV=0, cost=0; realised = (510-500)*100 = 1000
    assert snap.total_market_value == Decimal("0")
    assert snap.total_cost == Decimal("0")
    assert snap.total_realized_pnl == Decimal("1000")


def test_replay_tracks_realized_pnl_day_trade(db_session):
    """Day-trade BUY+SELL same date contributes only to realized_pnl, not MV."""
    sym = "2330"
    d = date(2026, 5, 14)
    _seed_tx(db_session, symbol=sym, side=portfolio_models.TransactionType.BUY,
             qty=100, price="500", trade_date=d, fee="0")
    _seed_tx(db_session, symbol=sym, side=portfolio_models.TransactionType.SELL,
             qty=100, price="510", trade_date=d, fee="0")
    _seed_price(db_session, symbol=sym, d=d, close="505")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, d, d)
    snap = db_session.query(PortfolioSnapshot).one()
    # Held nothing overnight → MV = 0, cost = 0
    assert snap.total_market_value == Decimal("0")
    assert snap.total_cost == Decimal("0")
    # Realised gain: (100 * 510) - (100 * 500) = 1000
    assert snap.total_realized_pnl == Decimal("1000")


def test_replay_realized_pnl_cumulative_across_dates(db_session):
    sym = "2330"
    _seed_tx(db_session, symbol=sym, side=portfolio_models.TransactionType.BUY,
             qty=100, price="500", trade_date=date(2026, 5, 14))
    _seed_tx(db_session, symbol=sym, side=portfolio_models.TransactionType.SELL,
             qty=40, price="600", trade_date=date(2026, 5, 15))
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="600")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 15))
    snaps = {s.date: s for s in db_session.query(PortfolioSnapshot).all()}
    # Day 1: only BUY, no realisation yet
    assert snaps[date(2026, 5, 14)].total_realized_pnl == Decimal("0")
    # Day 2: SELL 40 @ 600, avg cost 500 → (600-500) * 40 = 4000
    assert snaps[date(2026, 5, 15)].total_realized_pnl == Decimal("4000")


def test_replay_skips_weekends(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),  # Thu
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="510")  # Fri
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 18), close="520")  # Mon
    db_session.commit()

    # Range covers Thu-Mon inclusive (Sat 16, Sun 17 inside).
    result = nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 18))
    # 3 weekdays only.
    assert result.snapshots_written == 3
    snap_dates = {s.date for s in db_session.query(PortfolioSnapshot).all()}
    assert date(2026, 5, 16) not in snap_dates
    assert date(2026, 5, 17) not in snap_dates


def test_replay_xirr_null_on_backfilled_rows(db_session):
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    db_session.commit()

    nbs.replay_snapshots_range(db_session, date(2026, 5, 14), date(2026, 5, 14))
    snap = db_session.query(PortfolioSnapshot).one()
    assert snap.portfolio_xirr is None


def test_replay_per_date_failure_preserves_earlier_snapshots(db_session, monkeypatch):
    """A failure on one date must rollback only that date's SAVEPOINT,
    not wipe previously persisted snapshots in the same run."""
    sym = "2330"
    _seed_tx(
        db_session,
        symbol=sym,
        side=portfolio_models.TransactionType.BUY,
        qty=10,
        price="500",
        trade_date=date(2026, 5, 14),
    )
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 14), close="500")
    _seed_price(db_session, symbol=sym, d=date(2026, 5, 15), close="510")
    db_session.commit()

    real_merge = db_session.merge

    def flaky_merge(obj, *args, **kwargs):
        if getattr(obj, "date", None) == date(2026, 5, 15):
            raise RuntimeError("boom on 5/15")
        return real_merge(obj, *args, **kwargs)

    monkeypatch.setattr(db_session, "merge", flaky_merge)

    result = nbs.replay_snapshots_range(
        db_session, date(2026, 5, 14), date(2026, 5, 15)
    )

    assert result.snapshots_written == 1
    assert len(result.errors) == 1
    assert result.errors[0].date == date(2026, 5, 15)

    snaps = {s.date for s in db_session.query(PortfolioSnapshot).all()}
    assert date(2026, 5, 14) in snaps  # earlier date survived the later failure
    assert date(2026, 5, 15) not in snaps


# ---------- run_backfill dispatcher ----------


def test_run_backfill_rejects_unknown_phase(db_session):
    with pytest.raises(ValueError):
        nbs.run_backfill(db_session, date(2026, 5, 14), date(2026, 5, 14), phase="weird")


def test_run_backfill_snapshots_only_does_not_fetch(db_session, monkeypatch):
    called = {"n": 0}

    def _no(_d):
        called["n"] += 1
        return []

    monkeypatch.setattr(nbs.market_data_service, "fetch_twse_date", _no)
    monkeypatch.setattr(nbs.market_data_service, "fetch_tpex_date", _no)
    nbs.run_backfill(
        db_session, date(2026, 5, 14), date(2026, 5, 14), phase="snapshots"
    )
    assert called["n"] == 0


# ---------- Router smoke ----------


def test_endpoint_rejects_inverted_range(client):
    res = client.post(
        "/api/portfolio/history/backfill-networth",
        json={"from": "2026-05-15", "to": "2026-05-14", "phase": "snapshots"},
    )
    assert res.status_code == 400


def test_endpoint_runs_snapshots_phase(client, db_session, monkeypatch):
    # Avoid hitting the network in phase=snapshots
    monkeypatch.setattr(nbs.market_data_service, "fetch_twse_date", lambda d: [])
    monkeypatch.setattr(nbs.market_data_service, "fetch_tpex_date", lambda d: [])
    # Seed at least one price row so the date is not treated as a full
    # market holiday (which would skip the snapshot row).
    _seed_price(db_session, symbol="ANY", d=date(2026, 5, 14), close="1")
    db_session.commit()
    res = client.post(
        "/api/portfolio/history/backfill-networth",
        json={"from": "2026-05-14", "to": "2026-05-14", "phase": "snapshots"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["phase"] == "snapshots"
    assert body["snapshots_written"] == 1
