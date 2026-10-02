import time

from sqlalchemy import select, text

from app.models import Account, LedgerEntry
from app.services.moze_csv import parse_moze_csv
from app.services.moze_import_service import IMPORT_LOCK_KEY, replace_ledger
from app.services.transfer_pairing import pair_transfers


def _import(session, data: bytes, renames=None, rates=None) -> dict:
    parsed = parse_moze_csv(data)
    summary = replace_ledger(session, parsed, pair_transfers(parsed.rows), None, renames, rates)
    session.commit()
    return summary


def _entries(session, account_name: str) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry)
            .join(Account, Account.id == LedgerEntry.account_id)
            .where(Account.name == account_name)
            .order_by(LedgerEntry.seq)
        )
    )


def advisory_locks(engine) -> int:
    """Number of sessions currently holding the import advisory lock."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key"),
            {"key": IMPORT_LOCK_KEY},
        ).scalar_one()


def wait_until_unlocked(engine, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while advisory_locks(engine) and time.monotonic() < deadline:
        time.sleep(0.05)
