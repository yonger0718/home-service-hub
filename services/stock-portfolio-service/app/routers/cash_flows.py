from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import portfolio as schemas
from ..services import cash_flow_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio Cash Flows"])


@router.get("/broker-cash-flows", response_model=list[schemas.BrokerCashBalance])
def get_broker_cash_flows(
    as_of_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return cash_flow_service.list_balances(db, as_of_date=as_of_date)
