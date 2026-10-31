"""The inclusion rules shared by the month/daily summaries and the /reports endpoints (reports PR A).

`ledger_service` imports the pure helpers from here (and keeps its old private names as aliases), so this module
must not import `ledger_service` at module level; the few helpers that still live there are reached lazily.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import and_, extract, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..models import LedgerEntry, Project

EXPENSE_KINDS = ("expense", "fee")
INCOME_KINDS = ("income", "reward", "interest", "discount")
AMOUNT_QUANTUM = Decimal("0.0001")

Metric = Literal["expense", "income", "net", "count"]
Dimension = Literal[
    "category", "subcategory", "account", "project", "merchant", "tag", "counterparty", "month", "week", "weekday", "day"
]
Granularity = Literal["month", "week", "day"]
Compare = Literal["previous", "last_year"]


@dataclass(frozen=True)
class ReportFilters:
    date_from: date
    date_to: date
    account_ids: list[int] | None = None
    category_ids: list[int] | None = None
    project_ids: list[int] | None = None
    tags: list[str] | None = None
    merchant: str | None = None
    counterparty_id: int | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    q: str | None = None
    weekdays: list[int] | None = None
    include_excluded_projects: bool = False


def convert(amount: Decimal, rate: Decimal | None) -> Decimal | None:
    if rate is None:
        return None
    return (amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def spend_filter():
    """Rows that make up spending: negative expense / fee amounts and every refund (refunds are positive and
    reduce the spending of the period they are dated / posted in)."""
    return or_(
        and_(LedgerEntry.kind.in_(EXPENSE_KINDS), LedgerEntry.amount < 0),
        LedgerEntry.kind == "refund",
    )


def income_filter(hide_rewards: bool = False):
    """Rows that make up income: positive income / reward / interest / discount amounts, rewards dropped when
    `hide_rewards`."""
    kinds = tuple(kind for kind in INCOME_KINDS if not (hide_rewards and kind == "reward"))
    return and_(LedgerEntry.kind.in_(kinds), LedgerEntry.amount > 0)


def counted_filter(hide_rewards: bool = False):
    return or_(spend_filter(), income_filter(hide_rewards))


def period_bounds(month: str) -> tuple[date, date]:
    """[first day, first day of the next month) for a YYYY-MM month."""
    year, month_no = (int(part) for part in month.split("-"))
    return date(year, month_no, 1), date(year + (month_no == 12), month_no % 12 + 1, 1)


def week_start(d: date) -> date:
    """The Monday of `d`'s ISO week."""
    return d - timedelta(days=d.weekday())


def _shift_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))


def compare_window(date_from: date, date_to: date, compare: Compare) -> tuple[date, date]:
    """The comparison range. `last_year`: both dates a year earlier (Feb 29 → Feb 28). `previous`: a range of whole
    calendar months moves back by its number of months; any other range by its own inclusive day count, ending the
    day before `date_from`."""
    if compare == "last_year":
        return _shift_months(date_from, -12), _shift_months(date_to, -12)
    if date_from.day == 1 and date_to.day == calendar.monthrange(date_to.year, date_to.month)[1]:
        months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
        start = _shift_months(date_from, -months)
        return start, _shift_months(date_to, -months)
    days = (date_to - date_from).days + 1
    return date_from - timedelta(days=days), date_from - timedelta(days=1)


def excluded_projects_filter(db: Session) -> ColumnElement:
    """Rows not belonging to a project flagged `exclude_from_reports`."""
    return or_(
        LedgerEntry.project_id.is_(None),
        LedgerEntry.project_id.not_in(select(Project.id).where(Project.exclude_from_reports.is_(True))),
    )


def entry_filters(db: Session, f: ReportFilters) -> list:
    """WHERE clauses for the entries a report covers (dates inclusive)."""
    from . import ledger_service  # lazy: ledger_service imports this module

    filters = [LedgerEntry.entry_date >= f.date_from, LedgerEntry.entry_date <= f.date_to]
    if f.q:
        filters.append(ledger_service._text_filter(f.q))
    if f.account_ids is not None:
        filters.append(LedgerEntry.account_id.in_(f.account_ids))
    if f.counterparty_id is not None:
        filters.append(LedgerEntry.counterparty_id == f.counterparty_id)
    filters += ledger_service._attribute_filters(
        db, tags=f.tags, category_ids=f.category_ids, merchant=f.merchant,
        amount_min=f.amount_min, amount_max=f.amount_max,
    )
    if f.project_ids:
        filters.append(LedgerEntry.project_id.in_(f.project_ids))
    if f.weekdays:
        filters.append(extract("isodow", LedgerEntry.entry_date).in_(f.weekdays))
    if not f.include_excluded_projects:
        filters.append(excluded_projects_filter(db))
    return filters
