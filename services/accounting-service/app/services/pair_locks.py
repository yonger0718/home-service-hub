"""The try-lock step shared by the system pairs a provenance row owns (auto-pay spec §4.4 step 0; portfolio
integration spec §4.7 Locks): the owner row (`statement_auto_pay`, `settlement_booking`) `FOR UPDATE NOWAIT`, then its
two legs `FOR UPDATE NOWAIT` in ONE ordered SELECT (the `locked_with_legs` order), each refreshed from the database.
A held row or leg raises `CodedConflictError(busy_code)` — never a wait: a ledger writer locks the legs first and
then needs the owner row through the ON DELETE SET NULL foreign keys, and a reconcile holding its statement row may
be waiting for the owner row, so a blocking lock here could close a cycle. Each lock runs in a savepoint, so a busy
result leaves the session usable; the caller wraps the steps it needs undone together in its own savepoint
(`lock_row_and_legs` does it for both). Model-agnostic: everything else of each lifecycle stays with its service."""
from __future__ import annotations

from psycopg2.errors import LockNotAvailable
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import LedgerEntry
from app.services.errors import CodedConflictError


def lock_row(db: Session, model, row_id: int, *, busy_code: str, label: str):
    """The `model` row `row_id` locked `FOR UPDATE NOWAIT` and refreshed (None when it is gone). Held → `busy_code`."""
    try:
        with db.begin_nested():
            return db.scalar(select(model).where(model.id == row_id)
                             .with_for_update(nowait=True).execution_options(populate_existing=True))
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(busy_code, f"{label} {row_id} is locked by another write") from exc
        raise


def lock_legs(db: Session, leg_ids: tuple[int | None, int | None], *, busy_code: str,
              label: str) -> tuple[LedgerEntry | None, LedgerEntry | None]:
    """The (out, in) legs locked in ONE `SELECT … FOR UPDATE NOWAIT` ordered by id and refreshed; a leg that is gone
    (its id None, or the row deleted) comes back None. Held → `busy_code`."""
    ids = [i for i in leg_ids if i is not None]
    if not ids:
        return None, None
    try:
        with db.begin_nested():
            legs = {leg.id: leg for leg in db.scalars(
                select(LedgerEntry).where(LedgerEntry.id.in_(ids)).order_by(LedgerEntry.id)
                .with_for_update(nowait=True).execution_options(populate_existing=True))}
    except OperationalError as exc:
        if isinstance(exc.orig, LockNotAvailable):
            raise CodedConflictError(busy_code, f"a leg of {label} is locked by another write") from exc
        raise
    out_id, in_id = leg_ids
    return legs.get(out_id) if out_id is not None else None, legs.get(in_id) if in_id is not None else None


def lock_row_and_legs(db: Session, row, *, leg_ids: tuple[int | None, int | None],
                      busy_code: str) -> tuple[LedgerEntry | None, LedgerEntry | None]:
    """`row` (refreshed in place) then `leg_ids`, both NOWAIT inside one savepoint: a held leg rolls the row lock back
    with it, so a busy result leaves no lock behind. `leg_ids` are the caller's read of the row's leg columns; the
    columns only ever go to NULL (ON DELETE SET NULL), so a leg deleted since that read comes back None. The caller
    re-checks the refreshed row (status, legs) under the locks."""
    label = f"{type(row).__tablename__} row"
    locks = db.begin_nested()
    try:
        lock_row(db, type(row), row.id, busy_code=busy_code, label=label)
        legs = lock_legs(db, leg_ids, busy_code=busy_code, label=f"{label} {row.id}")
    except BaseException:
        if locks.is_active:
            locks.rollback()
        raise
    locks.commit()  # release the savepoint; the row and leg locks stay until the caller commits
    return legs
