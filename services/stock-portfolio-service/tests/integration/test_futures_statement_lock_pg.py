"""§6.2 on a real Postgres: the futures advisory lock, and the f1futures migration round trip.

Runs only when ``FUTURES_LOCK_TEST_URL`` names a throwaway Postgres server. Every test works in a database this module
creates on that server and drops afterwards; the URL's own database is only used for CREATE / DROP DATABASE, and the
root ``.env`` and ``app.database.engine`` are never used. The lock tests build their schema with ``create_all``; the
migration test builds the e9settle predecessor and runs alembic in-process (``app.database.SQLALCHEMY_DATABASE_URL``
patched to the created database, checked to be on the URL's server before any command).

Lock evidence is deterministic, not timing: the first path is paused while it holds the futures key, and the second
path is seen in ``pg_locks`` waiting (not granted) on that key before the first is released.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import URL, create_engine, func, make_url, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.database import Base
from app.models import portfolio as portfolio_models
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.models.sinopac_futures_statement import DAY_AMOUNTS, FuturesStatementDay, FuturesStatementMonth
from app.services import cash_account_service, portfolio_snapshot_service
from app.services import networth_backfill_service as nb
from app.services import sinopac_futures_series as series
from app.services import sinopac_futures_statement_service as svc
from app.services.sinopac_futures_series import FUTURES_LOCK_KEY
from app.services.sinopac_futures_statement_service import import_statement
from tests.integration.test_futures_statement_import import daily, monthly

PG_URL = os.environ.get("FUTURES_LOCK_TEST_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="FUTURES_LOCK_TEST_URL not set (throwaway Postgres only)")

SERVICE = Path(__file__).parents[2]
TODAY = date(2026, 4, 30)
WAIT_SECONDS = 5
EXCLUSIVE, SHARED = "ExclusiveLock", "ShareLock"
FUTURES_TABLES = ("futures_statement_day", "futures_statement_month", "futures_statement_run")

# Server-side bounds so a stray lock or a stuck statement fails the run instead of hanging it.
_BOUNDS = {"options": "-c lock_timeout=30s -c statement_timeout=60s"}

# libpq keys that pick the database; any of them in the URL query would override the database we connect to.
_DB_ROUTING_KEYS = ("dbname", "database", "service")


def _engine_args(pg_url: str, name: str) -> tuple[URL, dict]:
    """The URL and connect args for the module's own database ``name`` on ``pg_url``'s server.

    Database-routing query keys are dropped and ``dbname`` is pinned in the connect args, which the driver
    applies last, so nothing in ``pg_url`` can route the test engine back to the URL's own database.
    """
    url = make_url(pg_url)
    query = {key: value for key, value in url.query.items() if key not in _DB_ROUTING_KEYS}
    return url.set(database=name, query=query), {**_BOUNDS, "dbname": name}


@contextmanager
def _own_database(prefix: str) -> Iterator[tuple[str, URL, dict]]:
    """Create a database on the URL's server, yield its name / URL / connect args, and drop it afterwards."""
    name = f"{prefix}_{secrets.token_hex(8)}"
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool, connect_args=_BOUNDS)
    created = False
    try:
        try:
            with admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
                # Set before the connection closes, so a failure on close still reaches the DROP below.
                created = True
        except DBAPIError as exc:
            pytest.fail(
                f"cannot CREATE DATABASE on the FUTURES_LOCK_TEST_URL server ({exc.orig!r}); "
                "these tests only run in a database they create themselves",
                pytrace=False,
            )
        url, connect_args = _engine_args(PG_URL, name)
        yield name, url, connect_args
    finally:
        try:
            if created:
                with admin.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            admin.dispose()


def _checked_engine(name: str, url: URL, connect_args: dict, **kwargs):
    engine = create_engine(url, connect_args=connect_args, **kwargs)
    with engine.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar_one()
    if current != name:
        engine.dispose()
        pytest.fail(f"test engine connected to {current!r}, not the created database {name!r}", pytrace=False)
    return engine


@pytest.fixture(scope="module")
def engine():
    with _own_database("futures_lock_test") as (name, url, connect_args):
        engine = _checked_engine(name, url, connect_args)
        try:
            Base.metadata.create_all(engine)
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def lock_db(engine, monkeypatch):
    """The lock tests' database, emptied, with the TW calendar pinned (the import refreshes up to "today")."""
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)
    return engine


# --- helpers -----------------------------------------------------------------------------------------------


class _Gate:
    """Pauses the first path once, inside its transaction, while it holds the futures key."""

    def __init__(self) -> None:
        self.target: Session | None = None
        self.reached = threading.Event()
        self.release = threading.Event()

    def armed(self, db: Session) -> bool:
        return db is self.target and not self.reached.is_set()

    def hold(self) -> None:
        self.reached.set()
        self.release.wait(timeout=30)


def _pause_after_apply(monkeypatch, gate: _Gate) -> None:
    """The first import pauses after its row, findings and snapshot refresh are flushed, before its commit."""
    real_apply = svc._apply

    def paused_apply(db, payload):
        result = real_apply(db, payload)
        if gate.armed(db):
            gate.hold()
        return result

    monkeypatch.setattr(svc, "_apply", paused_apply)


def _backend_pid(conn) -> int:
    # Read on the session's own connection, then end that transaction so the service owns the next one.
    pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
    conn.rollback()
    return pid


def _state(future) -> str:
    """A worker that already ended names its exception, so a failure points at the thread, not the poll."""
    if not future.done():
        return "still running"
    exc = future.exception()
    return f"ended with {exc!r}" if exc is not None else "returned without waiting"


def _futures_lock(engine, pid: int, *, granted: bool, mode: str) -> bool:
    with engine.connect() as observer:
        return observer.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = :pid AND granted = :granted"
                " AND mode = :mode AND classid = :hi AND objid = :lo AND objsubid = 1"
            ),
            {"pid": pid, "granted": granted, "mode": mode,
             "hi": FUTURES_LOCK_KEY >> 32, "lo": FUTURES_LOCK_KEY & 0xFFFFFFFF},
        ).scalar_one() == 1


def _race(
    engine,
    gate: _Gate,
    first: Callable[[Session], object],
    second: Callable[[Session], object],
    *,
    first_mode: str,
    second_mode: str,
) -> tuple[object, object]:
    """Run ``first`` until it pauses at ``gate``, start ``second``, see it waiting on the futures key, release.

    Each path has its own connection and session. Returns both results once both have finished.
    """
    pids: dict[str, int] = {}
    conns = {role: engine.connect() for role in ("first", "second")}
    sessions = {role: Session(bind=conn) for role, conn in conns.items()}
    gate.target = sessions["first"]
    second_started = threading.Event()

    def run(role: str, fn):
        pids[role] = _backend_pid(conns[role])
        if role == "second":
            second_started.set()
        return fn(sessions[role])

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                one = pool.submit(run, "first", first)
                assert gate.reached.wait(timeout=10), f"first path never reached its pause; first {_state(one)}"
                assert _futures_lock(engine, pids["first"], granted=True, mode=first_mode), (
                    "first path does not hold the futures key at its pause"
                )
                two = pool.submit(run, "second", second)
                assert second_started.wait(timeout=10), f"second path never started; second {_state(two)}"
                deadline = time.monotonic() + WAIT_SECONDS
                waiting = False
                while not waiting and time.monotonic() < deadline:
                    waiting = _futures_lock(engine, pids["second"], granted=False, mode=second_mode)
                    if not waiting:
                        time.sleep(0.02)
                assert waiting, f"second path was never seen waiting on the futures key; second {_state(two)}"
                assert not two.done(), f"second {_state(two)}"
            finally:
                gate.release.set()
            return one.result(timeout=30), two.result(timeout=30)
    finally:
        for role in ("first", "second"):
            sessions[role].close()
            conns[role].close()


def _import(payload):
    return lambda db: import_statement(db, payload, dry_run=False)


def _commit_import(engine, payload) -> None:
    with Session(engine) as db:
        result = import_statement(db, payload, dry_run=False)
    assert (result.outcome, result.errors) == ("imported", [])


def _snap(engine, *days: date) -> None:
    with Session(engine) as db:
        for d in days:
            db.add(PortfolioSnapshot(
                date=d, total_market_value=Decimal("0"), total_cost=Decimal("0"), total_unrealized_pnl=Decimal("0"),
                total_dividends=Decimal("0"), total_realized_pnl=Decimal("0"), total_cash_twd=Decimal("0"),
                futures_equity_twd=Decimal("0"), portfolio_xirr=None,
            ))
        db.commit()


def _snapshots(engine) -> dict[date, Decimal]:
    with Session(engine) as db:
        return {r.date: r.futures_equity_twd for r in db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date)}


def _day_findings(engine) -> dict[date, list]:
    with Session(engine) as db:
        return {r.statement_date: r.findings for r in db.scalars(select(FuturesStatementDay))}


def _prev(d: str) -> dict:
    return {"code": "prev", "dates": [d], "letters": []}


class _Summary:
    total_market_value = Decimal("1000")
    total_cost = Decimal("800")
    total_unrealized_pnl = Decimal("200")
    total_dividends = Decimal("0")
    portfolio_xirr = None


# --- imports serialise -------------------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["earlier_first", "later_first"])
def test_two_dates_serialise(lock_db, monkeypatch, order):
    """The second import waits, then sees the first's row: 03-09's ``prev`` is computed against 03-06's balance."""
    _snap(lock_db, date(2026, 3, 6), date(2026, 3, 9), date(2026, 3, 10))
    early = daily("2026-03-06", prev="0", balance="90", sha="a")
    late = daily("2026-03-09", prev="100", balance="150", sha="b")  # 100 != 90 → a prev finding on 03-09
    first, second = (early, late) if order == "earlier_first" else (late, early)
    gate = _Gate()
    _pause_after_apply(monkeypatch, gate)

    r1, r2 = _race(lock_db, gate, _import(first), _import(second), first_mode=EXCLUSIVE, second_mode=EXCLUSIVE)

    assert [(r.outcome, r.errors) for r in (r1, r2)] == [("imported", [])] * 2
    if order == "earlier_first":
        assert r2.findings == [_prev("2026-03-06")]  # 03-09 computed against the row committed before it read
    else:
        assert r1.findings == []  # 03-09 alone; 03-06's import then recomputed it as the next daily row
    assert _day_findings(lock_db) == {date(2026, 3, 6): [], date(2026, 3, 9): [_prev("2026-03-06")]}
    assert _snapshots(lock_db) == {
        date(2026, 3, 6): Decimal("90"), date(2026, 3, 9): Decimal("150"), date(2026, 3, 10): Decimal("150"),
    }


@pytest.mark.parametrize("order", ["daily_first", "monthly_first"])
def test_daily_and_monthly_serialise(lock_db, monkeypatch, order):
    """A monthly import never misses an uncommitted daily row of its month (a ``gap``), and a daily import
    recomputes the committed monthly row of its month."""
    _snap(lock_db, date(2026, 3, 6), date(2026, 4, 1))
    day = daily("2026-03-09", prev="0", balance="150", sha="a")  # deposits_net 150
    month = monthly("2026-03", a="0", b="150", activity=("2026-03-09",), sha="e")  # M 150 = the day's balance
    first, second = (day, month) if order == "daily_first" else (month, day)
    gate = _Gate()
    _pause_after_apply(monkeypatch, gate)

    r1, r2 = _race(lock_db, gate, _import(first), _import(second), first_mode=EXCLUSIVE, second_mode=EXCLUSIVE)

    assert [(r.outcome, r.errors) for r in (r1, r2)] == [("imported", [])] * 2
    if order == "daily_first":
        assert r2.findings == []  # the month saw 03-09: no gap, no diff
    else:
        assert r1.findings == [{"code": "gap", "dates": ["2026-03-09"], "letters": []}]  # alone, before the day
    with Session(lock_db) as db:
        assert db.get(FuturesStatementMonth, date(2026, 3, 1)).findings == []
    assert _day_findings(lock_db) == {date(2026, 3, 9): []}
    assert _snapshots(lock_db) == {date(2026, 3, 6): Decimal("0"), date(2026, 4, 1): Decimal("150")}


# --- shared holders wait for an import -----------------------------------------------------------------------


def test_snapshot_writer_waits_for_import(lock_db, monkeypatch):
    """The live writer's shared lock waits for the import's commit; it then writes the committed series value."""
    _commit_import(lock_db, daily("2026-03-06", prev="0", balance="90", sha="a"))
    monkeypatch.setattr(portfolio_snapshot_service.portfolio_service, "get_portfolio_summary", lambda db: _Summary())
    gate = _Gate()
    _pause_after_apply(monkeypatch, gate)

    imported, row = _race(
        lock_db, gate,
        _import(daily("2026-03-09", prev="90", balance="150", sha="b")),
        lambda db: portfolio_snapshot_service.write_today_snapshot(db, today=date(2026, 3, 10)).futures_equity_twd,
        first_mode=EXCLUSIVE, second_mode=SHARED,
    )

    assert (imported.outcome, imported.errors) == ("imported", [])
    with Session(lock_db) as db:
        committed = series.equity_at(db, date(2026, 3, 10))
    assert committed.source_date == date(2026, 3, 9)
    assert row == committed.total_equity == Decimal("150")
    assert _snapshots(lock_db) == {date(2026, 3, 10): Decimal("150")}


def test_replay_waits_for_import(lock_db, monkeypatch):
    """The replay's shared lock waits for the import's commit; it then writes the new source date's row."""
    _commit_import(lock_db, daily("2026-03-06", prev="0", balance="90", sha="a"))
    gate = _Gate()
    _pause_after_apply(monkeypatch, gate)

    imported, replay = _race(
        lock_db, gate,
        _import(daily("2026-03-09", prev="90", balance="150", sha="b")),
        lambda db: nb.replay_snapshots_range(db, date(2026, 3, 6), date(2026, 3, 10)),
        first_mode=EXCLUSIVE, second_mode=SHARED,
    )

    assert (imported.outcome, imported.errors) == ("imported", [])
    assert replay.errors == []
    # Futures-only dates: a row on each source date, each with the committed series value.
    assert _snapshots(lock_db) == {date(2026, 3, 6): Decimal("90"), date(2026, 3, 9): Decimal("150")}


def test_shared_lock_survives_replay_savepoints(lock_db, monkeypatch):
    """The replay's per-date SAVEPOINTs never release its shared lock: an import started after the replay has
    written (and released the SAVEPOINT of) at least one row waits until the replay's final commit, and then
    refreshes the replay's rows from its own date on."""
    _commit_import(lock_db, daily("2026-03-02", prev="0", balance="300", sha="a"))
    with Session(lock_db) as db:
        db.add(portfolio_models.Transaction(
            symbol="9999", type=portfolio_models.TransactionType.BUY, quantity=10, price=Decimal("10"),
            fee=Decimal("0"), tax=Decimal("0"), trade_date=datetime(2026, 3, 2, tzinfo=timezone.utc),
        ))
        for offset in range(5):  # Mon 03-02 .. Fri 03-06: a held-stock row every day
            db.add(PriceHistory(symbol="9999", date=date(2026, 3, 2) + timedelta(days=offset), close=Decimal("10"),
                                source="TWSE"))
        db.commit()

    gate = _Gate()
    seen: dict[str, object] = {}
    real_cash = cash_account_service.get_total_balance_in

    def paused_cash(db, *args, **kwargs):
        # Called per date while building that date's row, outside any SAVEPOINT; pause once a row is written.
        out = real_cash(db, *args, **kwargs)
        if gate.armed(db):
            written = db.scalar(select(func.count()).select_from(PortfolioSnapshot))
            if written >= 1:
                seen.update(rows=written, nested=db.in_nested_transaction())
                gate.hold()
        return out

    monkeypatch.setattr(cash_account_service, "get_total_balance_in", paused_cash)

    replay, imported = _race(
        lock_db, gate,
        lambda db: nb.replay_snapshots_range(db, date(2026, 3, 2), date(2026, 3, 6)),
        _import(daily("2026-03-04", prev="300", balance="400", sha="b")),
        first_mode=SHARED, second_mode=EXCLUSIVE,
    )

    assert seen["rows"] >= 1 and seen["nested"] is False  # paused after a released SAVEPOINT, lock still granted
    assert replay.errors == [] and replay.snapshots_written == 5
    assert (imported.outcome, imported.errors) == ("imported", [])
    # The import refreshed the replay's committed rows from 03-04 on; had it not waited, they would keep 300.
    assert _snapshots(lock_db) == {
        date(2026, 3, 2): Decimal("300"), date(2026, 3, 3): Decimal("300"),
        date(2026, 3, 4): Decimal("400"), date(2026, 3, 5): Decimal("400"), date(2026, 3, 6): Decimal("400"),
    }


# --- migration ---------------------------------------------------------------------------------------------


_JSON_COLUMNS = {
    "futures_statement_day": {"deposits", "fills", "fills_total", "positions", "position_lines", "last_detail",
                              "findings"},
    "futures_statement_month": {"activity_dates", "last_detail", "findings"},
}
_CHECKS = {
    "futures_statement_day": {"ck_futures_statement_day_last_status", "ck_futures_statement_day_ack_status"},
    "futures_statement_month": {"ck_futures_statement_month_last_status", "ck_futures_statement_month_ack_status"},
    "futures_statement_run": {"ck_futures_statement_run_kind", "ck_futures_statement_run_result"},
}
_SNAPSHOT_DAY = date(2026, 3, 9)


def _predecessor(engine) -> None:
    """The e9settle schema (the models without the futures tables and column) with its version row and one
    existing snapshot row. Older revisions assume a pre-alembic schema, so base → head cannot run on an empty
    database; the version row is part of this fixture, not a stamp."""
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in FUTURES_TABLES])
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE portfolio_snapshot DROP COLUMN futures_equity_twd")
        conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('e9settle')")
        conn.execute(
            text(
                "INSERT INTO portfolio_snapshot (date, total_market_value, total_cost, total_unrealized_pnl,"
                " total_dividends) VALUES (:d, 10, 8, 2, 0)"
            ),
            {"d": _SNAPSHOT_DAY},
        )


def _version(conn) -> str:
    return conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()


def _assert_head(engine) -> None:
    with engine.connect() as conn:
        assert _version(conn) == "f1futures"
        insp = sa.inspect(conn)
        assert set(FUTURES_TABLES) <= set(insp.get_table_names())
        for table in FUTURES_TABLES:
            columns = {c["name"]: c for c in insp.get_columns(table)}
            assert set(columns) == {c.name for c in Base.metadata.tables[table].columns}, table
            assert {c["name"] for c in insp.get_check_constraints(table)} >= _CHECKS[table], table
            for name in _JSON_COLUMNS.get(table, ()):
                assert isinstance(columns[name]["type"], postgresql.JSONB), (table, name)
        futures = {c["name"]: c for c in insp.get_columns("portfolio_snapshot")}["futures_equity_twd"]
        assert not futures["nullable"]
        assert (futures["type"].precision, futures["type"].scale) == (20, 4)
        assert conn.execute(
            text("SELECT futures_equity_twd FROM portfolio_snapshot WHERE date = :d"), {"d": _SNAPSHOT_DAY}
        ).scalar_one() == Decimal("0")
    # The CHECK constraints are enforced, not only named.
    for table, row in (
        ("futures_statement_run", "(kind, run_at, result) VALUES ('other', now(), 'ok')"),
        ("futures_statement_day", "(statement_date, accepted_sha256, accepted_at, last_status, "
                                  + ", ".join(DAY_AMOUNTS) + ") VALUES ('2026-03-09', 'x', now(), 'bogus', "
                                  + ", ".join("0" for _ in DAY_AMOUNTS) + ")"),
    ):
        with pytest.raises(IntegrityError, match="ck_"), engine.begin() as conn:
            conn.exec_driver_sql(f"INSERT INTO {table} {row}")


def _assert_downgraded(engine) -> None:
    with engine.connect() as conn:
        assert _version(conn) == "e9settle"
        insp = sa.inspect(conn)
        assert set(FUTURES_TABLES).isdisjoint(insp.get_table_names())
        assert "futures_equity_twd" not in {c["name"] for c in insp.get_columns("portfolio_snapshot")}
        assert conn.execute(
            text("SELECT total_market_value FROM portfolio_snapshot WHERE date = :d"), {"d": _SNAPSHOT_DAY}
        ).scalar_one() == Decimal("10")


def test_migration_upgrade_downgrade_upgrade(monkeypatch):
    """f1futures on Postgres: upgrade head, downgrade -1, upgrade head, in-process against the created database."""
    from alembic import command
    from alembic.config import Config

    import app.database as database

    with _own_database("futures_migration_test") as (name, url, connect_args):
        engine = _checked_engine(name, url, connect_args, poolclass=NullPool)
        try:
            _predecessor(engine)
            # alembic/env.py reads this attribute when each command runs; nothing else routes the migration.
            monkeypatch.setattr(database, "SQLALCHEMY_DATABASE_URL", url.render_as_string(hide_password=False))
            resolved, server = make_url(database.SQLALCHEMY_DATABASE_URL), make_url(PG_URL)
            assert (resolved.host, resolved.port) == (server.host, server.port)
            assert resolved.database == name and not set(resolved.query) & set(_DB_ROUTING_KEYS)
            print(f"alembic target: {resolved.render_as_string(hide_password=True)}")

            config = Config(str(SERVICE / "alembic.ini"))
            config.set_main_option("script_location", str(SERVICE / "alembic"))
            command.upgrade(config, "head")
            _assert_head(engine)
            command.downgrade(config, "-1")
            _assert_downgraded(engine)
            command.upgrade(config, "head")
            _assert_head(engine)
        finally:
            engine.dispose()
