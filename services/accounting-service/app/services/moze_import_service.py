"""MOZE CSV import: transactional full replace of MOZE-sourced ledger data (design D6).

CLI: python -m app.services.moze_import_service <path> [--dry-run] [--rename OLD=NEW ...]
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from ..models import SYSTEM_KINDS, Account, Category, ImportRun, LedgerEntry, Project
from . import fx_rate_service
from .moze_csv import MozeImportError, MozeRow, ParsedFile, parse_moze_csv
from .transfer_pairing import PairingResult, pair_transfers

IMPORT_LOCK_KEY = 0x4D4F5A45  # "MOZE"; one key shared by CLI, REST and dry runs
SOURCE = "moze_import"

SYSTEM_CATEGORY_NAMES = {
    "fee": "手續費",
    "discount": "折扣",
    "reward": "紅利回饋",
    "interest": "利息",
    "balance_adjustment": "餘額調整",
}


def _money(value: Decimal) -> str:
    return f"{Decimal(value):.4f}"


class _Lookup:
    """Get-or-create cache for categories and projects inside the ledger transaction."""

    def __init__(self, session: Session):
        self.session = session
        self.categories = {(c.kind, c.parent_id, c.name): c.id for c in session.scalars(select(Category))}
        self.projects = {p.name: p.id for p in session.scalars(select(Project))}

    def category_id(self, kind: str, main: str, sub: str) -> int | None:
        if kind in SYSTEM_KINDS:
            return self._category(kind, None, SYSTEM_CATEGORY_NAMES[kind])
        if not main:
            return None
        main_id = self._category(kind, None, main)
        if sub and sub != main:
            return self._category(kind, main_id, sub)
        return main_id

    def _category(self, kind: str, parent_id: int | None, name: str) -> int:
        key = (kind, parent_id, name)
        if key not in self.categories:
            category = Category(kind=kind, parent_id=parent_id, name=name)
            self.session.add(category)
            self.session.flush()
            self.categories[key] = category.id
        return self.categories[key]

    def project_id(self, name: str | None) -> int | None:
        if name is None:
            return None
        if name not in self.projects:
            project = Project(name=name)
            self.session.add(project)
            self.session.flush()
            self.projects[name] = project.id
        return self.projects[name]


AMOUNT_QUANTUM = Decimal("0.0001")  # amount is NUMERIC(20,4)


def required_rates(parsed: ParsedFile) -> set[tuple[date, str, str]]:
    """(day, row currency, account currency) for every row not recorded in its account's currency."""
    return {
        (row.entry_date, row.currency, parsed.accounts[row.account].currency)
        for row in parsed.rows
        if row.currency != parsed.accounts[row.account].currency
    }


def _converted(amount: Decimal, rate: Decimal | None) -> Decimal:
    if rate is None:
        return amount
    return (amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def _fx_columns(amount: Decimal, currency: str, rate: Decimal | None) -> dict:
    if rate is None:
        return {}
    return {"original_amount": amount, "original_currency": currency, "fx_rate": rate, "fx_source": "fx_api"}


def _insert_entries(
    session: Session,
    rows: Sequence[MozeRow],
    accounts: Mapping[str, Account],
    pairing: PairingResult,
    import_run_id: int | None,
    rates: Mapping[tuple[date, str, str], Decimal],
) -> list[LedgerEntry]:
    lookup = _Lookup(session)
    total = len(rows) + sum(1 for r in rows if r.fee != 0) + sum(1 for r in rows if r.discount != 0)
    seqs = iter(
        sorted(
            session.scalars(
                text("SELECT nextval('ledger_entry_seq_seq') FROM generate_series(1, :n)"), {"n": total}
            )
        )
    )

    parents: list[tuple[MozeRow, LedgerEntry, list[LedgerEntry]]] = []
    for row in rows:
        account = accounts[row.account]
        rate = None
        if row.currency != account.currency:
            key = (row.entry_date, row.currency, account.currency)
            if key not in rates:
                raise MozeImportError(
                    f"row {row.row_no}: no FX rate for {row.currency}→{account.currency} on {row.entry_date.isoformat()}"
                )
            rate = rates[key]
        parent = LedgerEntry(
            account_id=account.id,
            kind=row.kind,
            amount=_converted(row.amount, rate),
            currency=account.currency,
            **_fx_columns(row.amount, row.currency, rate),
            entry_date=row.entry_date,
            entry_time=row.entry_time,
            category_id=lookup.category_id(row.kind, row.main_category, row.sub_category),
            project_id=lookup.project_id(row.project),
            name=row.name,
            merchant=row.merchant,
            counterparty=row.counterparty,
            description=row.description,
            tags=list(row.tags),
            transfer_group_id=pairing.group_by_row.get(row.row_no),
            needs_review=row.row_no in pairing.unpaired_rows,
            source=SOURCE,
            import_run_id=import_run_id,
            seq=next(seqs),
        )
        children = []
        for kind, amount in (("fee", row.fee), ("discount", row.discount)):
            if amount != 0:
                children.append(
                    LedgerEntry(
                        account_id=account.id,
                        kind=kind,
                        amount=_converted(amount, rate),
                        currency=account.currency,
                        **_fx_columns(amount, row.currency, rate),
                        entry_date=row.entry_date,
                        entry_time=row.entry_time,
                        category_id=lookup.category_id(kind, "", ""),
                        source=SOURCE,
                        import_run_id=import_run_id,
                        seq=next(seqs),
                    )
                )
        parents.append((row, parent, children))

    session.add_all([parent for _, parent, _ in parents])
    session.flush()
    children_all = []
    for _, parent, children in parents:
        for child in children:
            child.parent_entry_id = parent.id
            children_all.append(child)
    session.add_all(children_all)
    session.flush()
    return [parent for _, parent, _ in parents] + children_all


def _delete_unused_categories_and_projects(session: Session) -> None:
    session.execute(
        text(
            "DELETE FROM category c WHERE c.parent_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id)"
        )
    )
    session.execute(
        text(
            "DELETE FROM category c WHERE c.parent_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id) "
            "AND NOT EXISTS (SELECT 1 FROM category child WHERE child.parent_id = c.id)"
        )
    )
    session.execute(
        text(
            "DELETE FROM project p "
            "WHERE NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.project_id = p.id)"
        )
    )


def _apply_renames(session: Session, renames: Mapping[str, str]) -> list[dict[str, str]]:
    applied = []
    for old_name, new_name in renames.items():
        account = session.scalar(select(Account).where(Account.name == old_name))
        if account is None:
            raise MozeImportError(f"rename {old_name}={new_name}: account '{old_name}' does not exist")
        other = session.scalar(select(Account).where(Account.name == new_name))
        if other is not None and other.id != account.id:
            raise MozeImportError(f"rename {old_name}={new_name}: account '{new_name}' already exists")
        account.name = new_name
        session.flush()
        applied.append({"from": old_name, "to": new_name})
    return applied


def _archive_disappeared_accounts(session: Session, named_in_file: set[str]) -> list[str]:
    archived = []
    for account in session.scalars(select(Account).order_by(Account.id)):
        if account.name in named_in_file:
            continue
        has_entries = session.scalar(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.account_id == account.id)
        )
        if has_entries:
            continue
        if not account.is_archived:
            archived.append(account.name)
        account.opening_balance = Decimal("0")
        account.is_archived = True
    session.flush()
    return archived


def _account_report(account: Account, totals: Mapping[int, tuple[int, Decimal, int, Decimal]]) -> dict:
    count, total, converted_count, converted_total = totals.get(account.id, (0, Decimal("0"), 0, Decimal("0")))
    return {
        "name": account.name,
        "currency": account.currency,
        "opening_balance": _money(account.opening_balance),
        "entry_count": count,
        "balance": _money(account.opening_balance + total),
        "converted_entry_count": converted_count,
        "converted_amount": _money(converted_total),
    }


def _leg_report(row: MozeRow) -> dict:
    return {
        "row": row.row_no,
        "account": row.account,
        "kind": row.kind,
        "date": row.entry_date.isoformat(),
        "time": row.entry_time.strftime("%H:%M") if row.entry_time else None,
        "amount": _money(row.amount),
        "currency": row.currency,
    }


def replace_ledger(
    session: Session,
    parsed: ParsedFile,
    pairing: PairingResult,
    import_run_id: int | None,
    renames: Mapping[str, str] | None = None,
    rates: Mapping[tuple[date, str, str], Decimal] | None = None,
) -> dict:
    """Run full-replace steps 1-6 in the caller's transaction and return the report summary.

    `rates` maps (day, row currency, account currency) to the rate for every foreign-currency row.
    """
    renamed = _apply_renames(session, renames or {})

    session.execute(delete(LedgerEntry).where(LedgerEntry.source == SOURCE))

    existing = {a.name: a for a in session.scalars(select(Account))}
    accounts: dict[str, Account] = {}
    created = []
    for name, spec in parsed.accounts.items():
        account = existing.get(name)
        if account is None:
            account = Account(name=name, currency=spec.currency, opening_balance=spec.opening_balance)
            session.add(account)
            created.append(name)
        else:
            account.currency = spec.currency
            account.opening_balance = spec.opening_balance
            account.is_archived = False
        accounts[name] = account
    session.flush()

    archived = _archive_disappeared_accounts(session, set(parsed.accounts))

    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id, rates or {})

    _delete_unused_categories_and_projects(session)
    session.flush()

    is_converted = LedgerEntry.fx_source.is_not(None)
    totals = {
        account_id: (count, total, converted_count, converted_total)
        for account_id, count, total, converted_count, converted_total in session.execute(
            select(
                LedgerEntry.account_id,
                func.count(),
                func.sum(LedgerEntry.amount),
                func.count().filter(is_converted),
                func.coalesce(func.sum(LedgerEntry.amount).filter(is_converted), 0),
            ).group_by(LedgerEntry.account_id)
        )
    }
    rows_by_no = {row.row_no: row for row in parsed.rows}
    return {
        "kind_counts": dict(sorted(Counter(entry.kind for entry in inserted).items())),
        "accounts_created": created,
        "accounts_archived": archived,
        "accounts_renamed": renamed,
        "accounts": [_account_report(account, totals) for account in accounts.values()],
        "unpaired_transfers": [_leg_report(rows_by_no[row_no]) for row_no in pairing.unpaired_rows],
        "pairing": dict(zip(("pass1", "pass2", "pass3"), pairing.pass_counts)),
    }


class ImportRefusedError(Exception):
    """The import was refused before anything was written."""


class ImportLockedError(ImportRefusedError):
    pass


class ImportAlreadyRunningError(ImportRefusedError):
    pass


def import_locked() -> bool:
    return os.getenv("ACCOUNTING_IMPORT_LOCKED", "").strip().lower() == "true"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_report(run: ImportRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "file_name": run.file_name,
        "file_sha256": run.file_sha256,
        "row_count": run.row_count,
        "summary": run.summary,
    }


def _replace_from_csv(
    session: Session,
    data: bytes,
    run_id: int | None,
    renames: Mapping[str, str],
    http_get: fx_rate_service.HttpGet | None,
    *,
    persist_rates: bool,
) -> tuple[ParsedFile, dict]:
    """Parse, pair, resolve FX rates, then replace the ledger (uncommitted).

    ensure_rates commits what it persists, so it runs before any ledger work on this session.
    """
    parsed = parse_moze_csv(data)
    pairing = pair_transfers(parsed.rows)
    rates = fx_rate_service.ensure_rates(session, required_rates(parsed), persist=persist_rates, http_get=http_get)
    return parsed, replace_ledger(session, parsed, pairing, run_id, renames, rates)


def _run_locked(
    conn: Connection,
    data: bytes,
    file_name: str,
    dry_run: bool,
    renames: Mapping[str, str],
    http_get: fx_rate_service.HttpGet | None,
) -> dict:
    sha256 = hashlib.sha256(data).hexdigest()
    with Session(bind=conn, autoflush=False) as session:
        if dry_run:
            started = _now()
            try:
                parsed, summary = _replace_from_csv(session, data, None, renames, http_get, persist_rates=False)
            finally:
                session.rollback()
            return {
                "id": None,
                "status": "dry_run",
                "started_at": started.isoformat(),
                "finished_at": _now().isoformat(),
                "file_name": file_name,
                "file_sha256": sha256,
                "row_count": parsed.row_count,
                "summary": summary,
            }

        session.execute(
            update(ImportRun)
            .where(ImportRun.status == "running")
            .values(status="failed", finished_at=_now(), summary={"error": "interrupted"})
        )
        run = ImportRun(started_at=_now(), file_name=file_name, file_sha256=sha256, status="running")
        session.add(run)
        session.commit()
        run_id = run.id

        try:
            parsed, summary = _replace_from_csv(session, data, run_id, renames, http_get, persist_rates=True)
            run = session.get(ImportRun, run_id)
            run.status = "succeeded"
            run.row_count = parsed.row_count
            run.summary = summary
            run.finished_at = _now()
            session.commit()
        except Exception as exc:
            session.rollback()
            run = session.get(ImportRun, run_id)
            run.status = "failed"
            run.summary = {"error": str(exc)}
            run.finished_at = _now()
            session.commit()
            raise
        return run_report(session.get(ImportRun, run_id))


def run_import(
    engine: Engine,
    data: bytes,
    file_name: str,
    *,
    dry_run: bool = False,
    renames: Mapping[str, str] | None = None,
    http_get: fx_rate_service.HttpGet | None = None,
) -> dict:
    """Import a MOZE CSV. Raises ImportRefusedError (nothing written) or MozeImportError (rolled back).

    `http_get` replaces requests.get for FX rate fetches (tests inject a fake).
    """
    if import_locked():
        raise ImportLockedError("MOZE import is locked (ACCOUNTING_IMPORT_LOCKED=true)")
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY}).scalar_one()
        conn.commit()
        if not acquired:
            raise ImportAlreadyRunningError("import already running")
        try:
            return _run_locked(conn, data, file_name, dry_run, renames or {}, http_get)
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            conn.commit()


def _parse_rename(value: str) -> tuple[str, str]:
    old_name, sep, new_name = value.partition("=")
    if not sep or not old_name or not new_name:
        raise argparse.ArgumentTypeError(f"expected OLD=NEW, got '{value}'")
    return old_name, new_name


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.moze_import_service")
    parser.add_argument("path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rename", action="append", default=[], type=_parse_rename, metavar="OLD=NEW")
    args = parser.parse_args(argv)

    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    try:
        report = run_import(
            engine,
            args.path.read_bytes(),
            args.path.name,
            dry_run=args.dry_run,
            renames=dict(args.rename),
        )
    except ImportRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except MozeImportError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
