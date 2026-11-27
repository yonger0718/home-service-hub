from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.futures_detail import FuturesDetail, FuturesMonths
from ..schemas.futures_summary import FuturesSummary
from ..services import futures_detail_service, futures_summary_service, portfolio_snapshot_service

router = APIRouter(prefix="/api/portfolio/futures", tags=["futures"])


@router.get("/summary", response_model=FuturesSummary)
def get_futures_summary(
    as_of: Optional[date_type] = Query(default=None), db: Session = Depends(get_db)
) -> dict:
    return futures_summary_service.summary(db, as_of or portfolio_snapshot_service._today_tw())


@router.get("/months", response_model=FuturesMonths)
def get_futures_months(db: Session = Depends(get_db)) -> dict:
    return futures_detail_service.months(db)


@router.get("/detail", response_model=FuturesDetail)
def get_futures_detail(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), db: Session = Depends(get_db)
) -> dict:
    result = futures_detail_service.detail(db, month)
    if result is None:
        raise HTTPException(status_code=404, detail="no futures statements for this month")
    return result
