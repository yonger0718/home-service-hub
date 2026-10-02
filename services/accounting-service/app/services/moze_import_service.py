"""MOZE CSV import: transactional full replace of MOZE-sourced ledger data (design D6)."""

from collections import Counter
from decimal import Decimal
from typing import Mapping, Sequence

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..models import SYSTEM_KINDS, Account, Category, LedgerEntry, Project
from .moze_csv import MozeRow, ParsedFile
from .transfer_pairing import PairingResult

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


def _insert_entries(
    session: Session,
    rows: Sequence[MozeRow],
    accounts: Mapping[str, Account],
    pairing: PairingResult,
    import_run_id: int | None,
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
        parent = LedgerEntry(
            account_id=account.id,
            kind=row.kind,
            amount=row.amount,
            currency=account.currency,
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
                        amount=amount,
                        currency=account.currency,
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
) -> dict:
    """Run full-replace steps 2, 3, 5 and 6 in the caller's transaction and return the report summary."""
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

    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id)

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
        "accounts": [_account_report(account, totals) for account in accounts.values()],
        "unpaired_transfers": [_leg_report(rows_by_no[row_no]) for row_no in pairing.unpaired_rows],
        "pairing": dict(zip(("pass1", "pass2", "pass3"), pairing.pass_counts)),
    }
