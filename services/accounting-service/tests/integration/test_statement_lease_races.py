"""Lease fencing under concurrency (design §4.1): a submission's lease check holds the run row lock until its commit,
so finish/renew/reclaim on the same run serialise behind it instead of interleaving with the submission."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import IngestRun
from app.services import statement_ingest_service as svc
from app.services.errors import ConflictError
from tests.helpers import race


@pytest.fixture
def running(db_session):
    """A run claimed by `worker` that already moved to `running`, so a lease check alone writes nothing."""
    r = svc.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = svc.claim_run(db_session, r.id, label="worker")
    svc.require_lease(db_session, r.id, token, label="worker")
    db_session.commit()
    return r.id, token


def _refused_with_old_token(engine, run_id, token):
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        with pytest.raises(ConflictError, match="^lease$"):
            svc.require_lease(session, run_id, token, label="worker")
    finally:
        session.close()


def test_finish_waits_for_an_in_flight_submission(pg_engine, running):
    run_id, token = running
    outcome = race(pg_engine,
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

    outcome = race(pg_engine, submission, lambda b: svc.claim_run(b, run_id, label="worker"))
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

    outcome = race(pg_engine, reclaim, lambda a: svc.renew_lease(a, run_id, token))
    assert isinstance(outcome, ConflictError) and str(outcome) == "lease"
