from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import fx_rate_service
from ..services.quotes import fx_rate_service as quotes_fx_service

router = APIRouter(prefix="/api/portfolio/fx", tags=["Portfolio"])


class FxRateLookupResponse(BaseModel):
    currency: str
    date: date
    rate_to_twd: Decimal | None


@router.get("/rate", response_model=FxRateLookupResponse)
def lookup_fx_rate(
    currency: str = Query(..., min_length=3, max_length=3),
    on: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> FxRateLookupResponse:
    """Latest fx_rates.rate_to_twd on or before ``on`` (defaults today).

    Drives the add-transaction form's FX auto-fill. Returns ``rate_to_twd:
    null`` when no coverage exists so the UI can fall back to manual entry.
    """
    raw_cur = currency.strip()
    is_gbp_pence = raw_cur == "GBp"
    cur = "GBp" if is_gbp_pence else raw_cur.upper()
    if cur == "TWD":
        return FxRateLookupResponse(currency=cur, date=on or date.today(), rate_to_twd=Decimal("1"))
    base_currency = "GBP" if is_gbp_pence else cur
    asof = on or date.today()
    rate = quotes_fx_service.get_rate(db, base_currency, asof)
    if rate is not None and is_gbp_pence:
        rate = rate / Decimal("100")
    return FxRateLookupResponse(currency=cur, date=asof, rate_to_twd=rate)


class FxRefreshRequest(BaseModel):
    base_currencies: list[str] | None = None
    quote_currencies: list[str] | None = None
    asof: date | None = None


class PerBaseResultResponse(BaseModel):
    success: bool
    upserted: int
    source_url: str | None
    error: str | None


class FetchResultResponse(BaseModel):
    success: bool
    per_base: dict[str, PerBaseResultResponse]
    upserted_count: int
    error: str | None


@router.post("/refresh", response_model=FetchResultResponse)
def refresh_fx_rates(
    payload: FxRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> FetchResultResponse:
    try:
        result = fx_rate_service.fetch_and_store(
            db,
            base_currencies=(
                payload.base_currencies
                if payload and payload.base_currencies is not None
                else fx_rate_service.DEFAULT_BASE_CURRENCIES
            ),
            quote_currencies=(
                payload.quote_currencies
                if payload and payload.quote_currencies is not None
                else fx_rate_service.DEFAULT_QUOTE_CURRENCIES
            ),
            asof=payload.asof if payload else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FetchResultResponse(
        success=result.success,
        per_base={
            base: PerBaseResultResponse(
                success=per_base.success,
                upserted=per_base.upserted,
                source_url=per_base.source_url,
                error=per_base.error,
            )
            for base, per_base in result.per_base.items()
        },
        upserted_count=result.upserted_count,
        error=result.error,
    )
