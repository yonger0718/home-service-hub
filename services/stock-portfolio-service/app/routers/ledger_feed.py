from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.ledger_feed import SettlementsOut, ValuationOut
from ..services import ledger_feed_service

router = APIRouter(prefix="/api/portfolio/ledger-feed", tags=["ledger-feed"])

# the accounting client keys on this body, not on the app-wide error envelope
_UNKNOWN_BROKER = JSONResponse(status_code=404, content={"detail": "unknown_broker"})


@router.get("/settlements", response_model=SettlementsOut)
def get_settlements(
    broker: str = Query(),
    settle_from: date_type = Query(),
    settle_to: date_type = Query(),
    trade_from: date_type = Query(alias="from"),
    trade_to: date_type = Query(alias="to"),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    try:
        return ledger_feed_service.settlements(
            db, broker, settle_from=settle_from, settle_to=settle_to, trade_from=trade_from, trade_to=trade_to
        )
    except ledger_feed_service.UnknownBroker:
        return _UNKNOWN_BROKER


@router.get("/valuation", response_model=ValuationOut)
def get_valuation(
    broker: str = Query(),
    as_of: date_type = Query(),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    try:
        return ledger_feed_service.valuation(db, broker, as_of)
    except ledger_feed_service.UnknownBroker:
        return _UNKNOWN_BROKER
