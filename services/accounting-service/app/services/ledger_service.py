"""Read-only ledger queries: balances and entry history in canonical order (design D10)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..models import Account, Category, Counterparty, LedgerEntry, Project


def list_accounts(db: Session) -> list[dict]:
    entry_count = func.count(LedgerEntry.id).label("entry_count")
    rows = db.execute(
        select(
            Account.id,
            Account.name,
            Account.currency,
            Account.opening_balance,
            (Account.opening_balance + func.coalesce(func.sum(LedgerEntry.amount), 0)).label("balance"),
            entry_count,
        )
        .outerjoin(LedgerEntry, LedgerEntry.account_id == Account.id)
        .where(Account.is_archived.is_(False))
        .group_by(Account.id)
        .order_by(entry_count.desc(), Account.name)
    )
    return [dict(row._mapping) for row in rows]


def list_entries(
    db: Session,
    account_id: int,
    *,
    limit: int,
    offset: int,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[int, list[dict]]:
    """Return (total, page) newest first; running_balance is computed over all entries before filtering."""
    running = (
        select(
            LedgerEntry.id.label("entry_id"),
            (
                Account.opening_balance
                + func.sum(LedgerEntry.amount).over(
                    order_by=(LedgerEntry.entry_date, LedgerEntry.entry_time.asc().nulls_first(), LedgerEntry.seq),
                    rows=(None, 0),
                )
            ).label("running_balance"),
        )
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.account_id == account_id)
        .subquery()
    )
    parent_category = aliased(Category)
    filters = [LedgerEntry.account_id == account_id]
    if kind is not None:
        filters.append(LedgerEntry.kind == kind)
    if date_from is not None:
        filters.append(LedgerEntry.entry_date >= date_from)
    if date_to is not None:
        filters.append(LedgerEntry.entry_date <= date_to)

    total = db.scalar(select(func.count()).select_from(LedgerEntry).where(*filters))
    rows = db.execute(
        select(
            LedgerEntry,
            running.c.running_balance,
            Category.name.label("category_name"),
            parent_category.name.label("parent_category_name"),
            Project.name.label("project_name"),
            Counterparty.name.label("counterparty_name"),
        )
        .join(running, running.c.entry_id == LedgerEntry.id)
        .outerjoin(Category, Category.id == LedgerEntry.category_id)
        .outerjoin(parent_category, parent_category.id == Category.parent_id)
        .outerjoin(Project, Project.id == LedgerEntry.project_id)
        .outerjoin(Counterparty, Counterparty.id == LedgerEntry.counterparty_id)
        .where(*filters)
        .order_by(LedgerEntry.entry_date.desc(), LedgerEntry.entry_time.desc().nulls_last(), LedgerEntry.seq.desc())
        .limit(limit)
        .offset(offset)
    )
    page = []
    for entry, running_balance, category_name, parent_category_name, project_name, counterparty_name in rows:
        if category_name is None:
            category = None
        elif parent_category_name is None:
            category = category_name
        else:
            category = f"{parent_category_name}/{category_name}"
        page.append(
            {
                "id": entry.id,
                "kind": entry.kind,
                "amount": entry.amount,
                "currency": entry.currency,
                "original_amount": entry.original_amount,
                "original_currency": entry.original_currency,
                "fx_rate": entry.fx_rate,
                "fx_source": entry.fx_source,
                "entry_date": entry.entry_date,
                "entry_time": entry.entry_time,
                "category": category,
                "project": project_name,
                "name": entry.name,
                "merchant": entry.merchant,
                "counterparty": counterparty_name,
                "description": entry.description,
                "tags": entry.tags,
                "parent_entry_id": entry.parent_entry_id,
                "transfer_group_id": entry.transfer_group_id,
                "needs_review": entry.needs_review,
                "running_balance": running_balance,
            }
        )
    return total, page
