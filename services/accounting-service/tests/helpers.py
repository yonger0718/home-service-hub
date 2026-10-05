import queue
import threading
import time
from datetime import date, datetime, timezone
from datetime import time as dt_time
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models import Account, LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services.moze_csv import parse_moze_csv
from app.services.moze_backup_import_service import replace_ledger_from_backup
from app.services.moze_import_service import IMPORT_LOCK_KEY, replace_ledger
from app.services.schedule_templates import LINE_KEYS
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


def _import_backup(session, data, renames=None, rates=None, *, strict=False, allow_fx_outliers=False) -> dict:
    """Run the backup full replace on `data` (a BackupData) in `session` and commit; returns the summary."""
    summary = replace_ledger_from_backup(
        session, data, None, renames or {}, rates or {}, strict=strict, allow_fx_outliers=allow_fx_outliers
    )
    session.commit()
    return summary


def _by_moze_id(session, moze_id: str) -> LedgerEntry:
    return session.scalar(select(LedgerEntry).where(LedgerEntry.moze_id == moze_id))


def race(engine, first, second, timeout: float = 5.0):
    """Run first(session) and second(session) in two transactions on separate connections, as two overlapping
    requests would, and return second's outcome ("committed" or the exception it raised).

    `first` runs and keeps its transaction open (holding the row lock it took); `second` runs in a thread and
    must block on that lock (still running after 0.5 s); `first` commits; `second` must then finish within
    `timeout` seconds.
    """
    factory = sessionmaker(bind=engine, autoflush=False)
    first_session, second_session = factory(), factory()
    outcome: queue.Queue = queue.Queue()

    def run_second():
        try:
            second(second_session)
            second_session.commit()
            outcome.put("committed")
        except Exception as exc:  # noqa: BLE001  (the outcome is asserted by the caller)
            second_session.rollback()
            outcome.put(exc)

    try:
        first(first_session)
        thread = threading.Thread(target=run_second, daemon=True)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "the second write must wait for the first transaction's row lock"
        first_session.commit()
        thread.join(timeout=timeout)
        assert not thread.is_alive(), f"the second write is still blocked {timeout} s after the first committed"
        return outcome.get_nowait()
    finally:
        first_session.close()
        second_session.close()


POSTED_AT = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)


def schedule_line(kind: str, account: Account, amount: str, **fields) -> dict:
    """A template line with every key (unused ones None), in the account's currency."""
    line = dict.fromkeys(LINE_KEYS)
    line.update(kind=kind, account_id=account.id, amount=str(amount), currency=account.currency)
    line.update(fields)
    return line


def make_definition(
    session,
    lines: list[dict],
    *,
    kind: str = "recurring",
    name: str = "Netflix",
    interval_unit: str = "month",
    anchor: date = date(2026, 10, 22),
    auto_post_from: date = date(2026, 10, 3),
    description: str | None = None,
    tags=(),
    **columns,
) -> ScheduleDefinition:
    """Insert a definition (flushed, not committed) without generating instances or validating the template."""
    definition = ScheduleDefinition(
        kind=kind, name=name, template={"lines": list(lines), "description": description, "tags": list(tags)},
        interval_unit=interval_unit, anchor_date=anchor, auto_post_from=auto_post_from, **columns,
    )
    session.add(definition)
    session.flush()
    return definition


def make_instance(
    session,
    definition: ScheduleDefinition,
    seq: int,
    day: date,
    *,
    status: str = "pending",
    entries=(),
    acted_by: str | None = None,
    rule_date: date | None = None,
    **columns,
) -> ScheduleInstance:
    """Insert one period; posted / skipped ones get acted_at (POSTED_AT unless given) and acted_by (auto unless given)."""
    acted = {} if status == "pending" else {"acted_at": columns.pop("acted_at", POSTED_AT), "acted_by": acted_by or "auto"}
    instance = ScheduleInstance(
        definition_id=definition.id, seq=seq, rule_date=rule_date or day, due_date=day, status=status,
        posted_entry_ids=[getattr(entry, "id", entry) for entry in entries], **acted, **columns,
    )
    session.add(instance)
    session.flush()
    return instance
