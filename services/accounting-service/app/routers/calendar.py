"""Read-only business-day calendar for the SPA (#197): the fixed Taiwan holiday table plus the owner's
`auto_pay.extra_holidays` / `extra_workdays` overrides, so the frontend shifts card due dates exactly as the
auto-pay job does."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.reconciliation import BusinessDaysOut
from ..services import settings_service
from ..services.auto_pay_service import calendar_sets
from ..services.business_days import LAST_KNOWN_DAY, TW_HOLIDAYS

router = APIRouter(prefix="/calendar", tags=["Calendar"], dependencies=[Depends(method_scope())])

MAX_SPAN_DAYS = 1100


@router.get("/business-days", response_model=BusinessDaysOut)
def get_business_days(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    db: Session = Depends(get_db),
) -> BusinessDaysOut:
    """Holidays (fixed table ∪ `extra_holidays`) and make-up workdays (`extra_workdays`) within [from, to]. Weekends
    are not listed: the client treats Saturday and Sunday as closed, and a listed workday wins over everything, as
    `business_days.is_business_day` does."""
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="to must not precede from")
    if date_to - date_from > timedelta(days=MAX_SPAN_DAYS):
        raise HTTPException(status_code=422, detail=f"span exceeds {MAX_SPAN_DAYS} days")
    extra_holidays, extra_workdays = calendar_sets(settings_service.read_reconciliation_settings(db)["auto_pay"])
    in_range = lambda day: date_from <= day <= date_to  # noqa: E731
    return BusinessDaysOut(
        holidays=sorted(day for day in TW_HOLIDAYS | extra_holidays if in_range(day)),
        workdays=sorted(day for day in extra_workdays if in_range(day)),
        last_known_day=LAST_KNOWN_DAY,
    )
