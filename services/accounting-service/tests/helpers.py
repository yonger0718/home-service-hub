import time
from datetime import date
from datetime import time as dt_time
from decimal import Decimal

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


def make_account(session, name: str = "錢包", currency: str = "TWD", opening: str = "0", **columns) -> Account:
    """Insert an account (flushed, not committed); `columns` sets any other Account column."""
    account = Account(name=name, currency=currency, opening_balance=Decimal(opening), **columns)
    session.add(account)
    session.flush()
    return account


def make_entry(
    session,
    account: Account,
    amount: str,
    *,
    kind: str = "expense",
    entry_date: date = date(2026, 9, 1),
    entry_time: dt_time | None = None,
    source: str = "manual",
    **columns,
) -> LedgerEntry:
    """Insert an entry in the account's currency (flushed, not committed); posted_date defaults to entry_date."""
    entry = LedgerEntry(
        account_id=account.id, kind=kind, amount=Decimal(amount), currency=account.currency,
        entry_date=entry_date, entry_time=entry_time, source=source, **columns,
    )
    session.add(entry)
    session.flush()
    return entry
