"""§5.3 on a real Postgres: a statement commit and a 國泰 CSV commit for one date serialise on the date lock.

Runs only when ``CATHAY_PG_TEST_URL`` names a throwaway Postgres server. The module creates its own database there
(schema built with ``create_all``) and drops it afterwards; the URL's own database is never touched, and the root
``.env`` and ``app.database.engine`` are never used. The first path is paused while holding the lock; the
second path is seen waiting in ``pg_locks`` before the first is released — the deterministic evidence.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import URL, create_engine, event, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.database import Base
from app.models import portfolio as models
from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction
from app.models.cathay_statement import CathayStatementDay
from app.services import broker_cathay_service, broker_cathay_statement_service
from app.services.broker_cathay_statement_service import import_statement
from tests.integration.test_cathay_csv_owned_dates import ONE_LINE, _csv, _csv_row
from tests.integration.test_cathay_statement_import import D, _payload

PG_URL = os.environ.get("CATHAY_PG_TEST_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="CATHAY_PG_TEST_URL not set (throwaway Postgres only)")

HOLDINGS = [{"code": "9999", "name": "範例電子", "shares": 1000}]
WAIT_SECONDS = 5


def _key(day: date) -> int:
    return 0x43415448 << 32 | day.toordinal()


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


@pytest.fixture(scope="module")
def engine():
    """A database this module creates on the URL's server and drops afterwards.

    The database the URL names is only used to issue CREATE / DROP DATABASE; nothing in it is read, dropped or
    truncated. The test engine must report the created database as ``current_database()`` before any write.
    """
    name = f"cathay_lock_test_{secrets.token_hex(8)}"
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool, connect_args=_BOUNDS)
    created = False
    engine = None
    try:
        try:
            with admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        except DBAPIError as exc:
            pytest.fail(
                f"cannot CREATE DATABASE on the CATHAY_PG_TEST_URL server ({exc.orig!r}); "
                "the lock test only runs in a database it creates itself",
                pytrace=False,
            )
        created = True
        url, connect_args = _engine_args(PG_URL, name)
        engine = create_engine(url, connect_args=connect_args)
        with engine.connect() as conn:
            current = conn.execute(text("SELECT current_database()")).scalar_one()
        if current != name:
            pytest.fail(f"test engine connected to {current!r}, not the created database {name!r}", pytrace=False)
        Base.metadata.create_all(engine)
        yield engine
    finally:
        try:
            if engine is not None:
                engine.dispose()
        finally:
            try:
                if created:
                    with admin.connect() as conn:
                        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            finally:
                admin.dispose()


@pytest.fixture(autouse=True)
def _clean(engine, monkeypatch) -> None:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "範例電子", ["9999"])
    monkeypatch.setenv("CASH_LEG_ENABLED", "true")
    with Session(engine) as db:
        db.add(BrokerAccount(
            broker=BrokerEnum.CATHAY, nickname="Cathay TWD", currency="TWD",
            opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True,
        ))
        db.commit()


def _statement(db: Session):
    return import_statement(db, _payload(lines=ONE_LINE, holdings=HOLDINGS), dry_run=False)


def _csv_commit(db: Session, *rows: str):
    raw = _csv(*(rows or (_csv_row(),)))
    parsed = broker_cathay_service.parse_cathay_rows(raw)
    result = broker_cathay_service.parse_cathay_transactions_csv(raw, dry_run=False, db=db, parsed=parsed)
    return parsed, result


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


def _advisory(engine, pid: int, key: int, *, granted: bool) -> bool:
    with engine.connect() as observer:
        return observer.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = :pid"
                " AND granted = :granted AND classid = :hi AND objid = :lo AND objsubid = 1"
            ),
            {"pid": pid, "granted": granted, "hi": key >> 32, "lo": key & 0xFFFFFFFF},
        ).scalar_one() == 1


@pytest.mark.parametrize("order", ["statement_first", "csv_first"])
def test_statement_first_csv_waits(engine, monkeypatch, order):
    key = _key(D)
    first_locked, release, second_started = threading.Event(), threading.Event(), threading.Event()
    pids: dict[str, int] = {}
    conns = {role: engine.connect() for role in ("first", "second")}
    sessions = {role: Session(bind=conn) for role, conn in conns.items()}
    real_lock = broker_cathay_statement_service.lock_trade_dates

    def paused_lock(db, dates):
        real_lock(db, dates)
        if db is sessions["first"]:
            first_locked.set()
            release.wait(timeout=30)

    monkeypatch.setattr(broker_cathay_statement_service, "lock_trade_dates", paused_lock)
    statement_role, csv_role = ("first", "second") if order == "statement_first" else ("second", "first")

    def run(role):
        pids[role] = _backend_pid(conns[role])
        if role == "second":
            second_started.set()
        db = sessions[role]
        return _statement(db) if role == statement_role else _csv_commit(db)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                first = pool.submit(run, "first")
                assert first_locked.wait(timeout=10), f"first path never took the lock; first {_state(first)}"
                assert _advisory(engine, pids["first"], key, granted=True)
                second = pool.submit(run, "second")
                assert second_started.wait(timeout=10), f"second path never started; second {_state(second)}"
                deadline = time.monotonic() + WAIT_SECONDS
                waiting = False
                while not waiting and time.monotonic() < deadline:
                    waiting = _advisory(engine, pids["second"], key, granted=False)
                    if not waiting:
                        time.sleep(0.02)
                assert waiting, f"second path was never seen waiting on the date lock; second {_state(second)}"
                assert not second.done(), f"second {_state(second)}"
            finally:
                release.set()
            outcomes = {"first": first.result(timeout=30), "second": second.result(timeout=30)}
    finally:
        for role in ("first", "second"):
            sessions[role].close()
            conns[role].close()

    statement = outcomes[statement_role]
    parsed, csv_result = outcomes[csv_role]
    with Session(engine) as db:
        rows = db.query(models.Transaction).order_by(models.Transaction.id).all()
        cash = db.query(CashTransaction).order_by(CashTransaction.id).all()
        days = db.query(CathayStatementDay).all()
        statement_fps = [
            row.fingerprint
            for row in broker_cathay_statement_service.map_statement(db, _payload(lines=ONE_LINE)).rows
        ]

    if order == "statement_first":
        assert statement.outcome == "imported"
        assert [row.import_fingerprint for row in rows] == statement_fps
        assert [row.id for row in rows] == statement.created_ids
        assert [row.status for row in parsed.rows] == ["covered"]
        assert (csv_result.covered, csv_result.created, csv_result.errors) == (1, 0, [])
    else:
        assert [row.status for row in parsed.rows] == ["new"]
        assert (csv_result.created, csv_result.errors) == (1, [])
        assert [row.import_fingerprint for row in rows] == [parsed.rows[0].fingerprint]
        assert [row.id for row in rows] == csv_result.created_ids
        assert statement.outcome == "verified"
        assert (statement.counts["created"], statement.errors) == (0, [])
    assert all(row.trade_date == datetime(D.year, D.month, D.day, tzinfo=timezone.utc) for row in rows)
    # Every cash leg belongs to a surviving transaction, two legs (trade + fee) per transaction.
    assert sorted(leg.related_transaction_id for leg in cash) == sorted(row.id for row in rows for _ in range(2))
    assert [(day.trade_date, day.accepted_kind, day.last_status) for day in days] == [
        (D, statement.outcome, statement.outcome)
    ]


def test_lock_order_ascending(engine):
    days = [date(2026, 3, 11), date(2026, 3, 9), date(2026, 3, 10)]
    keys: list[int] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            keys.append(parameters["k"])

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as db:
            parsed, result = _csv_commit(
                db,
                _csv_row(day="2026/03/11", order_id="XY125"),
                _csv_row(day="2026/03/09", order_id="XY123"),
                _csv_row(day="2026/03/10", order_id="XY124"),
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert (result.created, result.errors) == (3, [])
    assert keys == [_key(day) for day in sorted(days)]
