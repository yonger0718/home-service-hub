from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.futures_summary import FuturesSummary
from ..services import futures_summary_service, portfolio_snapshot_service

router = APIRouter(prefix="/api/portfolio/futures", tags=["futures"])


@router.get("/summary", response_model=FuturesSummary)
def get_futures_summary(
    as_of: Optional[date_type] = Query(default=None), db: Session = Depends(get_db)
) -> dict:
    return futures_summary_service.summary(db, as_of or portfolio_snapshot_service._today_tw())
