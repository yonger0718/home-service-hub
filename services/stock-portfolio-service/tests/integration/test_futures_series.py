from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)
from app.services import sinopac_futures_series as series

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


def _day(db, d, total, *, balance="0", open_positions=0):
    row = FuturesStatementDay(
        statement_date=d, accepted_sha256="a" * 64, accepted_at=NOW, open_positions=open_positions,
        **{k: Decimal("0") for k in DAY_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.balance = Decimal(balance)
    db.add(row)
    db.flush()


def _month(db, month_end, total, *, m="0", open_positions=0):
    row = FuturesStatementMonth(
        month=month_end.replace(day=1), month_end=month_end, accepted_sha256="b" * 64,
        accepted_at=NOW, open_positions=open_positions, **{k: Decimal("0") for k in MONTH_AMOUNTS},
    )
    row.total_equity = Decimal(total)
    row.m = Decimal(m)
    db.add(row)
    db.flush()


def test_no_source_before_first(db_session):
    _day(db_session, date(2026, 3, 10), "100")
    assert series.equity_at(db_session, date(2026, 3, 9)) is None


def test_daily_carries_until_next(db_session):
    _day(db_session, date(2026, 3, 10), "100", balance="90")
    _day(db_session, date(2026, 3, 13), "200")
    v = series.equity_at(db_session, date(2026, 3, 12))
    assert (v.total_equity, v.balance, v.source_date, v.source_kind) == (
        Decimal("100"), Decimal("90"), date(2026, 3, 10), "daily")
    assert series.equity_at(db_session, date(2026, 3, 13)).total_equity == Decimal("200")


def test_monthly_wins_tie(db_session):
    _day(db_session, date(2026, 3, 31), "100")
    _month(db_session, date(2026, 3, 31), "150", m="140")
    v = series.equity_at(db_session, date(2026, 3, 31))
    assert (v.source_kind, v.total_equity, v.balance) == ("monthly", Decimal("150"), Decimal("140"))


def test_monthly_then_next_daily(db_session):
    _month(db_session, date(2026, 3, 31), "150")
    _day(db_session, date(2026, 4, 2), "300")
    assert series.equity_at(db_session, date(2026, 4, 1)).source_kind == "monthly"
    assert series.equity_at(db_session, date(2026, 4, 2)).source_kind == "daily"


def test_frozen_flag(db_session):
    _day(db_session, date(2026, 3, 10), "100", open_positions=2)
    assert series.equity_at(db_session, date(2026, 3, 10)).frozen(date(2026, 3, 10)) is False
    v = series.equity_at(db_session, date(2026, 3, 11))
    assert v.positions_open and v.frozen(date(2026, 3, 11)) is True
    _day(db_session, date(2026, 3, 20), "100", open_positions=0)
    assert series.equity_at(db_session, date(2026, 3, 25)).frozen(date(2026, 3, 25)) is False


def test_monthly_positions_open(db_session):
    _month(db_session, date(2026, 3, 31), "150", open_positions=1)
    assert series.equity_at(db_session, date(2026, 4, 5)).positions_open is True


def test_equity_by_date_matches_equity_at(db_session):
    _day(db_session, date(2026, 3, 5), "10", balance="1", open_positions=1)
    _day(db_session, date(2026, 3, 20), "20")
    _day(db_session, date(2026, 3, 31), "30")
    _month(db_session, date(2026, 3, 31), "35", m="3")
    _day(db_session, date(2026, 4, 8), "40")
    start = date(2026, 2, 25)
    got = series.equity_by_date(db_session, start, start + timedelta(days=39))
    assert len(got) == 40
    for i in range(40):
        d = start + timedelta(days=i)
        assert got[d] == series.equity_at(db_session, d), d


def test_equity_by_date_range_inside_source(db_session):
    _day(db_session, date(2026, 3, 5), "10")
    got = series.equity_by_date(db_session, date(2026, 3, 8), date(2026, 3, 9))
    assert got[date(2026, 3, 8)].source_date == date(2026, 3, 5)


def test_source_dates(db_session):
    _day(db_session, date(2026, 3, 10), "1")
    _month(db_session, date(2026, 3, 31), "1")
    assert series.source_dates(db_session) == {date(2026, 3, 10), date(2026, 3, 31)}


def test_lock_key_and_sqlite_noop(db_session):
    assert series.FUTURES_LOCK_KEY == 0x46555455 << 32
    series.futures_lock(db_session)
    series.futures_lock(db_session, shared=True)


def test_lock_sql_on_postgres():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    series.futures_lock(db)
    series.futures_lock(db, shared=True)
    sqls = [str(c.args[0]) for c in db.execute.call_args_list]
    assert sqls == ["SELECT pg_advisory_xact_lock(:k)", "SELECT pg_advisory_xact_lock_shared(:k)"]
    assert db.execute.call_args.args[1] == {"k": series.FUTURES_LOCK_KEY}
