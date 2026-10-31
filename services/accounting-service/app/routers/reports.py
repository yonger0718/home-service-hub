"""GET /reports/* (openspec add-accounting-reports, design D1): breakdowns over the ledger under the shared inclusion
rules of `report_rules`, positive magnitudes in the main currency."""
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.reports import MONTH_PATTERN, BreakdownOut, BudgetAmendIn, BudgetIn, BudgetStatusListOut, BudgetStatusOut, TrendOut
from ..services import budget_service, report_service
from .errors import service_errors
from ..services.report_rules import Compare, Dimension, Granularity, Metric, ReportFilters

router = APIRouter(prefix="/reports", tags=["Reports"], dependencies=[Depends(method_scope())])

Weekday = Annotated[int, Field(ge=1, le=7)]


def filters_from_wire(
    date_from: date,
    date_to: date,
    *,
    account_id: list[int] | None = None,
    category_id: list[int] | None = None,
    project_id: list[int] | None = None,
    tag: list[str] | None = None,
    merchant: str | None = None,
    counterparty_id: int | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    q: str | None = None,
    weekday: list[int] | None = None,
    include_excluded_projects: bool = False,
) -> ReportFilters:
    """The SPA's wire filter names (`ReportFilters` in reports.model.ts) → the service dataclass."""
    return ReportFilters(
        date_from=date_from, date_to=date_to, account_ids=account_id, category_ids=category_id,
        project_ids=project_id, tags=tag, merchant=merchant, counterparty_id=counterparty_id,
        amount_min=amount_min, amount_max=amount_max, q=q, weekdays=weekday,
        include_excluded_projects=include_excluded_projects,
    )


def report_filters(
    date_from: date,
    date_to: date,
    include_excluded_projects: bool = False,
    account_id: list[int] | None = Query(default=None),
    category_id: list[int] | None = Query(default=None, description="Rows in any of these categories or their sub-categories"),
    project_id: list[int] | None = Query(default=None),
    tag: list[str] | None = Query(default=None, description="Rows carrying ANY of these tags"),
    merchant: str | None = Query(default=None, max_length=100),
    counterparty_id: int | None = None,
    amount_min: Decimal | None = Query(default=None, description="Signed, in the entry's ACCOUNT currency"),
    amount_max: Decimal | None = Query(default=None, description="Signed, in the entry's ACCOUNT currency"),
    q: str | None = Query(default=None, max_length=100),
    weekday: list[Weekday] | None = Query(default=None, description="ISO weekdays, 1 = Monday"),
) -> ReportFilters:
    """The query parameters every /reports read shares (dates inclusive, by entry_date)."""
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not follow date_to")
    return filters_from_wire(
        date_from, date_to, account_id=account_id, category_id=category_id, project_id=project_id, tag=tag,
        merchant=merchant, counterparty_id=counterparty_id, amount_min=amount_min, amount_max=amount_max, q=q,
        weekday=weekday, include_excluded_projects=include_excluded_projects,
    )


@router.get("/breakdown", response_model=BreakdownOut)
def get_breakdown(
    group_by: Dimension,
    metric: Metric = "expense",
    split_by: Dimension | None = None,
    compare: Compare | None = None,
    filters: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
):
    if split_by == group_by:
        raise HTTPException(status_code=422, detail="split_by must differ from group_by")
    return report_service.breakdown(db, filters, metric=metric, group_by=group_by, split_by=split_by, compare=compare)


def _check_span(f: ReportFilters, granularity: Granularity) -> None:
    days = (f.date_to - f.date_from).days
    from_ = f.date_from
    leap_day = (from_.month, from_.day) == (2, 29)
    ten_years = from_.replace(year=from_.year + 10, day=28 if leap_day else from_.day)
    too_long = {"day": days > 400, "week": days > 1100, "month": f.date_to > ten_years}[granularity]
    if too_long:
        limit = {"day": "400 days", "week": "1100 days", "month": "10 years"}[granularity]
        raise HTTPException(status_code=422, detail=f"span exceeds {limit} for {granularity} granularity")


@router.get("/trend", response_model=TrendOut)
def get_trend(
    granularity: Granularity,
    metric: Metric = "expense",
    series: list[int] | None = Query(default=None, description="Top-level category ids, one series each"),
    filters: ReportFilters = Depends(report_filters),
    db: Session = Depends(get_db),
):
    _check_span(filters, granularity)
    if series and not report_service.top_level_category_ids(db, series):
        raise HTTPException(status_code=422, detail="series must be top-level categories")
    return report_service.trend(db, filters, metric=metric, granularity=granularity, series=series)


@router.get("/budgets/status", response_model=BudgetStatusListOut)
def get_budget_status(month: str = Query(pattern=MONTH_PATTERN), db: Session = Depends(get_db)):
    return budget_service.status(db, month)


def _written(db: Session, budget_id: int, month: str | None) -> dict:
    return budget_service.status_of(db, budget_id, month or budget_service.current_month())


@router.post("/budgets", response_model=BudgetStatusOut, status_code=201)
def post_budget(payload: BudgetIn, db: Session = Depends(get_db)):
    with service_errors():
        budget_id = budget_service.create(db, payload)
        db.commit()
        return _written(db, budget_id, payload.effective_from)


@router.put("/budgets/{budget_id}", response_model=BudgetStatusOut)
def put_budget(budget_id: int, payload: BudgetAmendIn, db: Session = Depends(get_db)):
    with service_errors():
        new_id = budget_service.amend(db, budget_id, payload)
        db.commit()
        return _written(db, new_id, payload.effective_from)


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(budget_id: int, db: Session = Depends(get_db)):
    with service_errors():
        budget_service.archive(db, budget_id)
    db.commit()
    return Response(status_code=204)
