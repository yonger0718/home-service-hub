from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FxRate
from ..schemas.ledger import FxRateOut, PreferenceOut
from ..schemas.writes import PreferenceIn
from ..services import fx_rate_service, settings_service

router = APIRouter(tags=["Settings"])

CURRENCY_PATTERN = r"^[A-Za-z0-9]{3,8}$"


@router.get("/preference", response_model=PreferenceOut)
def get_preference(db: Session = Depends(get_db)):
    preference = settings_service.get_preference(db)
    db.commit()
    return preference


@router.put("/preference", response_model=PreferenceOut)
def update_preference(payload: PreferenceIn, db: Session = Depends(get_db)):
    preference = settings_service.update_preference(db, payload)
    db.commit()
    return preference


@router.get("/fx-rate", response_model=FxRateOut)
def get_fx_rate(
    day: date = Query(alias="date"),
    base: str = Query(pattern=CURRENCY_PATTERN),
    quote: str = Query(pattern=CURRENCY_PATTERN),
    db: Session = Depends(get_db),
):
    base, quote = base.upper(), quote.upper()
    try:
        rate = fx_rate_service.get_rate(db, day, base, quote)
    except fx_rate_service.FxRateUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    source = "identity" if base == quote else db.get(FxRate, (day, base, quote)).source
    return {"date": day, "base": base, "quote": quote, "rate": rate, "source": source}


# Task 16 adds /account-groups, /categories, /projects and /counterparties here.
