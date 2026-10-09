"""Lease fencing under concurrency (design §4.1): a submission's lease check holds the run row lock until its commit,
so finish/renew/reclaim on the same run serialise behind it instead of interleaving with it.

Each race is coordinated, not timed: the contender reports its backend pid and signals right before its locking call;
the holder commits only after pg_stat_activity shows that backend waiting on a lock."""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models import IngestRun
from app.services import statement_ingest_service as svc
from app.services.errors import ConflictError
from tests.helpers import wait_until_blocked

TIMEOUT = 5.0


@pytest.fixture
def running(db_session):
    """A run claimed by `worker` that already moved to `running`, so a lease check alone writes nothing."""
    r = svc.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = svc.claim_run(db_session, r.id, label="worker")
    svc.require_lease(db_session, r.id, token, label="worker")
    db_session.commit()
    return r.id, token


def contend(engine, holder, contender):
    """Run holder(session) and keep its transaction open; run contender(session) in a thread; prove the contender's
    backend is waiting on a lock before the holder commits; return the contender's outcome ("committed" or the
    exception it raised)."""
    factory = sessionmaker(bind=engine, autoflush=False)
    holder_session, contender_session = factory(), factory()
    about_to_lock = threading.Event()
    pid: list[int] = []
    outcome: list = []

    def run_contender():
        try:
            pid.append(contender_session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            about_to_lock.set()
            contender(contender_session)
            contender_session.commit()
            outcome.append("committed")
        except Exception as exc:  # noqa: BLE001  (the outcome is asserted by the caller)
            contender_session.rollback()
            outcome.append(exc)
        finally:
            about_to_lock.set()
            contender_session.close()  # in the thread that uses it

    thread = threading.Thread(target=run_contender, daemon=True)
    started = False
    committed = False
    try:
        holder(holder_session)
        holder_pid = holder_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread.start()
        started = True
        assert about_to_lock.wait(TIMEOUT) and pid, "the contender never reached its locking call"
        # fails the test unless the contender waits on a lock held by the holder's backend
        wait_until_blocked(engine, pid[0], timeout=TIMEOUT, blocker_pid=holder_pid)
        assert not outcome, "the contender finished while the holder still held the row lock"
        holder_session.commit()
        committed = True
        thread.join(timeout=TIMEOUT)
        assert not thread.is_alive(), f"the contender is still blocked {TIMEOUT} s after the holder committed"
        return outcome[0]
    finally:
        if not committed:
            holder_session.rollback()  # failure path: release the locks so the contender can finish
        if started:
            thread.join(timeout=TIMEOUT)
            if thread.is_alive() and pid:  # still stuck: cancel its backend, then wait (bounded) for it to unwind
                with engine.connect() as conn:
                    conn.execute(text("SELECT pg_cancel_backend(:pid)"), {"pid": pid[0]})
                thread.join(timeout=TIMEOUT)
        holder_session.close()


def _refused_with_old_token(engine, run_id, token):
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        with pytest.raises(ConflictError, match="^lease$"):
            svc.require_lease(session, run_id, token, label="worker")
    finally:
        session.close()


def test_finish_waits_for_an_in_flight_submission(pg_engine, running):
    run_id, token = running
    outcome = contend(pg_engine,
                      lambda a: svc.require_lease(a, run_id, token, label="worker"),
                      lambda b: svc.finish_run(b, run_id, token, status="done", summary={"files": 1}))
    assert outcome == "committed"
    _refused_with_old_token(pg_engine, run_id, token)


def test_reclaim_after_expiry_waits_for_an_in_flight_submission(pg_engine, running, monkeypatch):
    run_id, token = running
    real_now = svc._now

    def submission(a):
        svc.require_lease(a, run_id, token, label="worker")
        monkeypatch.setattr(svc, "_now", lambda: real_now() + svc.LEASE + timedelta(minutes=1))  # lease now expired

    outcome = contend(pg_engine, submission, lambda b: svc.claim_run(b, run_id, label="worker"))
    assert outcome == "committed"
    session = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        row = session.get(IngestRun, run_id)
        assert (row.status, row.attempt) == ("claimed", 2) and row.lease_token != token
    finally:
        session.close()
    _refused_with_old_token(pg_engine, run_id, token)


def test_renew_after_a_reclaim_is_refused(pg_engine, running, monkeypatch):
    run_id, token = running
    real_now = svc._now

    def reclaim(b):
        monkeypatch.setattr(svc, "_now", lambda: real_now() + svc.LEASE + timedelta(minutes=1))
        svc.claim_run(b, run_id, label="worker")
        monkeypatch.setattr(svc, "_now", real_now)  # the old holder's clock still sees its lease as live

    outcome = contend(pg_engine, reclaim, lambda a: svc.renew_lease(a, run_id, token))
    assert isinstance(outcome, ConflictError) and str(outcome) == "lease"
