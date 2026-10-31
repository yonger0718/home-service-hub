"""GET /reports/* (openspec add-accounting-reports, design D1): breakdowns over the ledger under the shared inclusion
rules of `report_rules`, positive magnitudes in the main currency."""
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.reports import BreakdownOut
from ..services import report_service
from ..services.report_rules import Compare, Dimension, Metric, ReportFilters

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
