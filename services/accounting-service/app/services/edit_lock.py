"""Cutover lock (design D19): MOZE-sourced rows are read-only until ACCOUNTING_IMPORT_LOCKED=true.

The same flag refuses every import after cutover, so the importers read `import_locked` from here too.
"""

import os

from sqlalchemy import or_, select
from sqlalchemy.orm import object_session

from ..models import MOZE_SOURCES, EntryGroup, LedgerEntry  # MOZE_SOURCES re-exported (Task 2 defines it)

LOCK_MESSAGE = "locked_until_cutover"


class EditLockedError(Exception):
    def __init__(self, message: str = LOCK_MESSAGE):
        super().__init__(message)


def import_locked() -> bool:
    """True after cutover: imports are refused and every row is editable."""
    return os.getenv("ACCOUNTING_IMPORT_LOCKED", "").strip().lower() == "true"


def is_moze_sourced(target: LedgerEntry | EntryGroup) -> bool:
    """An entry from either MOZE importer, any row with a moze_id, or a group with such a member."""
    if target.moze_id is not None:
        return True
    if isinstance(target, LedgerEntry):
        return target.source in MOZE_SOURCES
    session = object_session(target)
    if session is None:
        return False
    member = session.scalar(
        select(LedgerEntry.id)
        .where(
            LedgerEntry.group_id == target.id,
            or_(LedgerEntry.source.in_(MOZE_SOURCES), LedgerEntry.moze_id.is_not(None)),
        )
        .limit(1)
    )
    return member is not None


def assert_editable(target: LedgerEntry | EntryGroup) -> None:
    """Raise EditLockedError("locked_until_cutover") for a MOZE-sourced entry or group before cutover."""
    if not import_locked() and is_moze_sourced(target):
        raise EditLockedError()
