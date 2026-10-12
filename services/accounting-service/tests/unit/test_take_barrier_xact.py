"""`coverage_service.take_barrier_xact` and the sweep's session-level `_take_barrier(wait=True)` map every lock
failure at the barrier to 409 `sweep_barrier_busy` (rulings 15/26; R1c Task 7 fix round 1, final review I3): a lock
timeout, a cancel, and a detected deadlock (the transaction is aborted either way; a 409 beats a 500). The database
is mocked."""
from unittest.mock import MagicMock

import pytest
from psycopg2.errors import DeadlockDetected, LockNotAvailable, QueryCanceled
from sqlalchemy.exc import OperationalError

from app.services import coverage_service as cov
from app.services.errors import CodedConflictError


def _db(failure: Exception) -> MagicMock:
    db = MagicMock()
    db.begin_nested.return_value.__exit__.return_value = False  # a savepoint never swallows the error
    calls = {"n": 0}

    def execute(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # current_setting('lock_timeout')
            result = MagicMock()
            result.scalar_one.return_value = "0"
            return result
        if calls["n"] == 3:  # pg_advisory_(xact_)lock, after the SET LOCAL
            raise OperationalError("SELECT pg_advisory_lock(...)", {}, failure)
        return MagicMock()

    db.execute.side_effect = execute
    return db


@pytest.mark.parametrize("failure", [DeadlockDetected(), LockNotAvailable(), QueryCanceled()],
                         ids=["deadlock", "lock_timeout", "canceled"])
def test_a_failed_barrier_wait_is_sweep_barrier_busy(failure):
    with pytest.raises(CodedConflictError) as info:
        cov.take_barrier_xact(_db(failure))
    assert info.value.code == "sweep_barrier_busy"


def test_other_operational_errors_propagate():
    with pytest.raises(OperationalError):
        cov.take_barrier_xact(_db(RuntimeError("connection lost")))


@pytest.mark.parametrize("failure", [DeadlockDetected(), LockNotAvailable(), QueryCanceled()],
                         ids=["deadlock", "lock_timeout", "canceled"])
def test_a_failed_session_barrier_wait_is_sweep_barrier_busy(failure):
    db = _db(failure)
    with pytest.raises(CodedConflictError) as info:
        cov._take_barrier(db, wait=True)
    assert info.value.code == "sweep_barrier_busy"
    assert db.execute.call_count == 3  # never acquired: no unlock attempted


def test_other_operational_errors_at_the_session_barrier_propagate():
    with pytest.raises(OperationalError):
        cov._take_barrier(_db(RuntimeError("connection lost")), wait=True)
