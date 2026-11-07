"""Serialization of category creation by key (#291, AGENT-430).

`uq_category_kind_parent_name` (NULLS NOT DISTINCT) makes an INSERT of a key wait for another transaction's
uncommitted INSERT of the same key: a unique-check wait, which NOWAIT and lock_timeout tricks do not turn into a
try-lock (ON CONFLICT waits too). The system and backfill categories are created on demand by ledger writers
(`create_balance_adjustment`, fee children, reward posting) that may then wait on the dirty barrier, and by
reconciliation actions that already hold it: barrier holder T inserting the key waits on writer W, W waits on T's
barrier, 40P01.

So every writer that creates a category, or renames one onto a key, first takes a transaction-level advisory lock
on that key (its own two-int key space, apart from the bigint keys used elsewhere; a hash collision only serializes
two keys) and re-reads under it. Ordinary writers wait for it: its holder is a writer that is creating the same key
and waits on nothing this writer holds. A barrier holder only tries it (409 `retry` when another transaction is
creating the key), so it never waits; once it holds the lock, no other transaction can have an uncommitted row of
that key, and its INSERT cannot wait. The retired MOZE importers create categories outside this protocol (they never
run alongside reconciliation)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import reward_engine
from .errors import RETRY, CodedConflictError

CATEGORY_KEY_SPACE = 291  # first int of pg_advisory_xact_lock(int, int) for category keys


def lock_category_key(db: Session, kind: str, parent_id: int | None, name: str) -> None:
    """Hold the advisory lock of this category key until the transaction ends (see the module note). Call it before
    the duplicate / existence re-read and the INSERT or rename. Under the dirty barrier it never waits: another
    transaction creating the key gives 409 `retry`."""
    key = func.hashtext(f"{kind}\x1f{'' if parent_id is None else parent_id}\x1f{name}")
    if reward_engine.barrier_held(db):
        if not db.scalar(select(func.pg_try_advisory_xact_lock(CATEGORY_KEY_SPACE, key))):
            raise CodedConflictError(RETRY, "a category this action needs is being created; try again")
    else:
        db.execute(select(func.pg_advisory_xact_lock(CATEGORY_KEY_SPACE, key)))
