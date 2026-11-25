"""Futures-only history fill (spec §6.5): ``backfill_futures_snapshots`` and its CLI."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)
from app.services import sinopac_futures_backfill as fill
from app.services import sinopac_futures_series as series

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
ZERO = Decimal("0")
COLUMNS = (
    "total_market_value", "total_cost", "total_unrealized_pnl", "total_dividends", "total_realized_pnl",
    "total_cash_twd", "portfolio_xirr",
)


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


def _snap(db, d, *, mv="1000", cost="800", cash="50", futures="0", dividends="7", realized="3", xirr="0.051234"):
    db.add(PortfolioSnapshot(
        date=d, total_market_value=Decimal(mv), total_cost=Decimal(cost),
        total_unrealized_pnl=Decimal(mv) - Decimal(cost), total_dividends=Decimal(dividends),
        total_realized_pnl=Decimal(realized), total_cash_twd=Decimal(cash), futures_equity_twd=Decimal(futures),
        portfolio_xirr=Decimal(xirr) if xirr is not None else None,
    ))
    db.commit()


def _dump(db):
    db.expire_all()
    return {
        r.date: tuple(getattr(r, c) for c in (*COLUMNS, "futures_equity_twd"))
        for r in db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date)
    }


def test_backfill_updates_only_futures_column(db_session):
    _snap(db_session, date(2026, 3, 9), xirr="0.1")
    _snap(db_session, date(2026, 3, 10), mv="1100", xirr="0.2")
    _snap(db_session, date(2026, 3, 11), mv="1200", futures="100", xirr="0.3")
    _day(db_session, date(2026, 3, 10), "500")
    before = _dump(db_session)

    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)

    assert counts == {"updated": 3, "inserted": 0, "changed": 2, "delta_sum": "900.0000"}
    after = _dump(db_session)
    assert set(after) == set(before)
    for d, values in after.items():
        assert values[:-1] == before[d][:-1]
    assert [after[d][-1] for d in sorted(after)] == [ZERO, Decimal("500"), Decimal("500")]


def test_backfill_only_touches_rows_in_range(db_session):
    _snap(db_session, date(2026, 3, 9))
    _snap(db_session, date(2026, 3, 12))
    _day(db_session, date(2026, 3, 1), "500")
    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 10), date(2026, 3, 31), dry_run=False)
    assert counts["updated"] == 1
    after = _dump(db_session)
    assert after[date(2026, 3, 9)][-1] == ZERO
    assert after[date(2026, 3, 12)][-1] == Decimal("500")


def test_backfill_inserts_forward_filled_row(db_session):
    _snap(db_session, date(2026, 3, 6), mv="900", xirr="0.01")
    _snap(db_session, date(2026, 3, 9), mv="1000", cost="800", cash="50", xirr="0.051234")
    _day(db_session, date(2026, 3, 10), "500")
    _month(db_session, date(2026, 3, 31), "600")
    _snap(db_session, date(2026, 3, 12), mv="1300")
    before = _dump(db_session)

    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)

    after = _dump(db_session)
    assert set(after) == set(before) | {date(2026, 3, 10), date(2026, 3, 31)}
    assert after[date(2026, 3, 10)] == before[date(2026, 3, 9)][:-1] + (Decimal("500"),)
    assert after[date(2026, 3, 31)] == before[date(2026, 3, 12)][:-1] + (Decimal("600"),)
    assert after[date(2026, 3, 10)][COLUMNS.index("portfolio_xirr")] == Decimal("0.051234")
    assert counts == {"updated": 3, "inserted": 2, "changed": 3, "delta_sum": "1600.0000"}


def test_backfill_no_previous_row_inserts_zero_row(db_session):
    _day(db_session, date(2026, 3, 10), "-40")
    _snap(db_session, date(2026, 3, 12))
    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)
    row = db_session.get(PortfolioSnapshot, date(2026, 3, 10))
    assert tuple(getattr(row, c) for c in COLUMNS) == (ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, None)
    assert row.futures_equity_twd == Decimal("-40")
    assert counts == {"updated": 1, "inserted": 1, "changed": 2, "delta_sum": "-80.0000"}


def test_backfill_source_outside_range_not_inserted(db_session):
    _day(db_session, date(2026, 2, 27), "70")
    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)
    assert counts == {"updated": 0, "inserted": 0, "changed": 0, "delta_sum": "0.0000"}
    assert db_session.query(PortfolioSnapshot).count() == 0


def test_backfill_never_deletes(db_session):
    for d in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)):
        _snap(db_session, d, mv="0", cost="0", cash="0", dividends="0", realized="0", xirr=None)
    counts = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)
    assert counts == {"updated": 3, "inserted": 0, "changed": 0, "delta_sum": "0.0000"}
    assert db_session.query(PortfolioSnapshot).count() == 3


def _scenario(db):
    _snap(db, date(2026, 3, 9), xirr="0.1")
    _snap(db, date(2026, 3, 11), mv="1200")
    _day(db, date(2026, 3, 10), "500")
    _day(db, date(2026, 3, 12), "450")


def test_backfill_dry_run_writes_nothing(db_session):
    _scenario(db_session)
    before = _dump(db_session)
    dry = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=True)
    assert _dump(db_session) == before
    real = fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)
    assert dry == real == {"updated": 2, "inserted": 2, "changed": 3, "delta_sum": "1450.0000"}
    assert _dump(db_session) != before


def _cli_session(monkeypatch, db_session):
    import app.database as database

    maker = sessionmaker(autocommit=False, autoflush=False, bind=db_session.get_bind())
    monkeypatch.setattr(database, "SessionLocal", maker)


def test_backfill_cli_dry_run_writes_nothing(db_session, monkeypatch, capsys):
    _scenario(db_session)
    before = _dump(db_session)
    _cli_session(monkeypatch, db_session)
    code = fill._main(["--from", "2026-03-01", "--to", "2026-03-31", "--dry-run"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"updated": 2, "inserted": 2, "changed": 3, "delta_sum": "1450.0000", "dry_run": True,
                   "from": "2026-03-01", "to": "2026-03-31"}
    assert _dump(db_session) == before


def test_backfill_cli_writes_and_defaults_to_today(db_session, monkeypatch, capsys):
    _scenario(db_session)
    _cli_session(monkeypatch, db_session)
    monkeypatch.setattr(fill.portfolio_snapshot_service, "_today_tw", lambda: date(2026, 3, 31))
    assert fill._main(["--from", "2026-03-01"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["to"] == "2026-03-31"
    assert out["inserted"] == 2
    assert db_session.get(PortfolioSnapshot, date(2026, 3, 12)).futures_equity_twd == Decimal("450")


def test_backfill_cli_bad_dates_exit_2(db_session, monkeypatch, capsys):
    _cli_session(monkeypatch, db_session)
    assert fill._main(["--from", "2026-13-01"]) == 2
    assert fill._main(["--from", "2026-03-31", "--to", "2026-03-01"]) == 2


def test_backfill_takes_exclusive_lock(db_session, monkeypatch):
    events: list = []
    real_lock = series.futures_lock
    real_by_date = series.equity_by_date

    def lock(db, *, shared=False):
        events.append(("lock", shared))
        real_lock(db, shared=shared)

    def by_date(db, start, end):
        events.append(("read", None))
        return real_by_date(db, start, end)

    monkeypatch.setattr(series, "futures_lock", lock)
    monkeypatch.setattr(series, "equity_by_date", by_date)
    _scenario(db_session)
    fill.backfill_futures_snapshots(db_session, date(2026, 3, 1), date(2026, 3, 31), dry_run=False)
    assert events[0] == ("lock", False)
    assert ("read", None) in events
