"""Budgets (design D3): monthly `total` / `category` limits that change by adding a row from a month on, and one
whole-life `project` limit. Status reuses the report aggregation (`report_service`) under the shared inclusion rules.

Identity = `(total)`, `(category, category_id)` or `(project, project_id)`. The row in force for a month is the
non-archived row of the identity with the greatest `effective_from <= month`; archiving ends the whole identity.
"""

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Budget, Category, Project
from ..schemas.reports import BudgetAmendIn, BudgetIn
from . import ledger_service, report_service
from .errors import ConflictError, NotFoundError, ValidationError
from .report_rules import AMOUNT_QUANTUM, ReportFilters, entry_filters, period_bounds, spend_filter

TOTAL_LABEL = "全部支出"
_ALL_TIME = (date.min, date.max)


def _identity(row: Budget):
    return [Budget.scope == row.scope, Budget.category_id.is_(None) if row.category_id is None
            else Budget.category_id == row.category_id,
            Budget.project_id.is_(None) if row.project_id is None else Budget.project_id == row.project_id]


def _live(db: Session, budget_id: int) -> Budget:
    row = db.get(Budget, budget_id)
    if row is None or row.archived:
        raise NotFoundError("budget not found")
    return row


def time_progress(month: str) -> float:
    start, end = period_bounds(month)
    today = ledger_service._today()
    if today >= end:
        return 1.0
    if today < start:
        return 0.0
    return round(today.day / calendar.monthrange(start.year, start.month)[1], 4)


def _used(db: Session, where: list, dimensions: tuple, rates_for) -> dict:
    raw = report_service._group_rows(db, [*where, spend_filter()], dimensions)
    return {keys: report_service._amount(sums, "expense")
            for keys, sums in report_service._convert_groups(raw, rates_for(raw)).items()}


def status(db: Session, month: str) -> dict:
    """Every budget in force for `month` with its usage, ordered total, categories by name, projects by name."""
    start, end = period_bounds(month)
    main = ledger_service.main_currency(db)
    rows = list(db.scalars(select(Budget).where(Budget.archived.is_(False)).order_by(Budget.id)))
    in_force: dict[tuple, Budget] = {}
    for row in rows:
        if row.scope != "project":
            if row.effective_from is None or row.effective_from > month:
                continue
            key = (row.scope, row.category_id)
            if key not in in_force or row.effective_from > in_force[key].effective_from:
                in_force[key] = row
        else:
            in_force[(row.scope, row.project_id)] = row

    rate_cache: dict[str, Decimal] = {}

    def rates_for(raw: list[tuple]) -> dict[str, Decimal]:
        wanted = {r[1] for r in raw} - set(rate_cache)
        if wanted:
            rate_cache.update({c: r for c, r in ledger_service.latest_rates(db, wanted, main).items() if r is not None})
        return rate_cache

    month_filters = ReportFilters(date_from=start, date_to=end - timedelta(days=1))
    used_total = used_category = used_project = {}
    kinds = {scope for scope, _ in in_force}
    if "total" in kinds:
        used_total = _used(db, entry_filters(db, month_filters), (), rates_for)
    if "category" in kinds:
        used_category = _used(db, entry_filters(db, month_filters), ("category",), rates_for)
    if "project" in kinds:
        ids = [key for scope, key in in_force if scope == "project"]
        everything = ReportFilters(date_from=_ALL_TIME[0], date_to=_ALL_TIME[1], project_ids=ids,
                                   include_excluded_projects=True)
        used_project = _used(db, entry_filters(db, everything), ("project",), rates_for)

    categories = {c.id: c for c in db.scalars(select(Category).where(
        Category.id.in_([k for s, k in in_force if s == "category"] or [0])))}
    projects = {p.id: p for p in db.scalars(select(Project).where(
        Project.id.in_([k for s, k in in_force if s == "project"] or [0])))}

    items = []
    for (scope, target), row in in_force.items():
        if scope == "total":
            label, color, used, order = TOTAL_LABEL, None, used_total.get((), Decimal(0)), (0, "")
        elif scope == "category":
            category = categories[target]
            label, color, used = category.name, category.color, used_category.get((str(target),), Decimal(0))
            order = (1, label)
        else:
            label, color, used = projects[target].name, None, used_project.get((str(target),), Decimal(0))
            order = (2, label)
        limit = row.amount
        items.append((order, row.id, {
            "id": row.id, "scope": scope, "category_id": row.category_id, "project_id": row.project_id,
            "label": label, "color": color, "limit": limit, "used": used, "remaining": limit - used,
            "ratio": round(float(used / limit), 4), "over": used > limit, "effective_from": row.effective_from,
        }))
    return {"month": month, "currency": main, "time_progress": time_progress(month),
            "budgets": [item for _, _, item in sorted(items, key=lambda t: (t[0], t[1]))]}


def status_of(db: Session, budget_id: int, month: str) -> dict:
    """The status entry of the row `budget_id` for `month` (the row in force then)."""
    for item in status(db, month)["budgets"]:
        if item["id"] == budget_id:
            return item
    raise NotFoundError("budget not found")


def current_month() -> str:
    return ledger_service._today().strftime("%Y-%m")


def _require_month(effective_from: str | None) -> str:
    if effective_from is None:
        raise ValidationError("effective_from", "effective_from is required for total and category budgets")
    return effective_from


def _starting_in(db: Session, identity: list, month: str) -> Budget | None:
    return db.scalar(select(Budget).where(*identity, Budget.archived.is_(False), Budget.effective_from == month))


def create(db: Session, payload: BudgetIn) -> int:
    category_id = project_id = None
    month = None
    if payload.scope == "category":
        category_id = payload.category_id
        if category_id is None:
            raise ValidationError("category_id", "category_id is required for a category budget")
        category = db.get(Category, category_id)
        if category is None or category.parent_id is not None:
            raise ValidationError("category_id", "category_id must be an existing top-level category")
    elif payload.scope == "project":
        project_id = payload.project_id
        if project_id is None:
            raise ValidationError("project_id", "project_id is required for a project budget")
        if db.get(Project, project_id) is None:
            raise ValidationError("project_id", "project not found")
    if payload.scope != "project":
        month = _require_month(payload.effective_from)

    row = Budget(scope=payload.scope, category_id=category_id, project_id=project_id, amount=payload.amount,
                 effective_from=month)
    if _starting_in(db, _identity(row), month) if month else db.scalar(
            select(Budget.id).where(*_identity(row), Budget.archived.is_(False))):
        raise ConflictError(f"budget already starts in {month}" if month else "project already has a budget")
    db.add(row)
    db.flush()
    return row.id


def amend(db: Session, budget_id: int, payload: BudgetAmendIn) -> int:
    """New limit; returns the id of the row now in force from `effective_from` (the same id for a project)."""
    row = _live(db, budget_id)
    if row.scope == "project":
        row.amount = payload.amount
        db.flush()
        return row.id
    month = _require_month(payload.effective_from)
    existing = _starting_in(db, _identity(row), month)
    if existing is not None:
        existing.amount = payload.amount
        db.flush()
        return existing.id
    new = Budget(scope=row.scope, category_id=row.category_id, project_id=None, amount=payload.amount,
                 effective_from=month)
    db.add(new)
    db.flush()
    return new.id


def archive(db: Session, budget_id: int) -> None:
    row = _live(db, budget_id)
    for item in db.scalars(select(Budget).where(*_identity(row), Budget.archived.is_(False))):
        item.archived = True
    db.flush()
