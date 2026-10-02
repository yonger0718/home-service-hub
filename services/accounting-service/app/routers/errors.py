"""Map service errors to HTTP: 422 {"detail": [{"loc": [field], ...}]}, 409 lock / conflict, 404 missing."""

from contextlib import contextmanager

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from ..services.edit_lock import EditLockedError
from ..services.errors import ConflictError, NotFoundError, ValidationError


@contextmanager
def service_errors():
    try:
        yield
    except ValidationError as exc:
        raise RequestValidationError([{"loc": [exc.field], "msg": exc.message, "type": "value_error"}]) from exc
    except EditLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
