from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import fx_rate_service

router = APIRouter(prefix="/api/portfolio/fx", tags=["Portfolio"])


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
