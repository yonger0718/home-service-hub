"""`einvoice_service.lock_conflicts_as_retry` (final review A1): which DB errors become the 409 `retry`."""
import pytest
from psycopg2.errors import DeadlockDetected, LockNotAvailable, SerializationFailure
from sqlalchemy.exc import OperationalError

from app.services.einvoice_service import lock_conflicts_as_retry
from app.services.errors import RETRY, CodedConflictError


@pytest.mark.parametrize("orig", [DeadlockDetected("deadlock detected"), SerializationFailure("could not serialize")])
def test_deadlock_and_serialization_failure_become_retry(orig):
    with pytest.raises(CodedConflictError) as caught:
        with lock_conflicts_as_retry():
            raise OperationalError("UPDATE einvoice ...", {}, orig)
    assert str(caught.value).startswith(RETRY)
    assert caught.value.__cause__.orig is orig


def test_other_operational_errors_pass_through():
    with pytest.raises(OperationalError):
        with lock_conflicts_as_retry():
            raise OperationalError("SELECT ...", {}, LockNotAvailable("lock not available"))
