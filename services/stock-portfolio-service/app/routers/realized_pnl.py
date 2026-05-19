from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.realized_pnl import RealizedPnlPagedOut
from ..services import realized_pnl_service


router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/realized-pnl", response_model=RealizedPnlPagedOut)
def get_realized_pnl(
    symbol: Optional[str] = Query(default=None),
    date_from: Optional[date_type] = Query(default=None),
    date_to: Optional[date_type] = Query(default=None),
    year: Optional[int] = Query(default=None),
    day_trade_only: bool = Query(default=False),
    sort: str = Query(default="trade_date:desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        events = realized_pnl_service.compute_events(
            db,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            year=year,
            day_trade_only=day_trade_only,
            sort=sort,
        )
        filter_scope_total, filter_scope_count, ytd_total, ytd_count = (
            realized_pnl_service.compute_summary(
                db,
                {
                    "symbol": symbol,
                    "date_from": date_from,
                    "date_to": date_to,
                    "year": year,
                    "day_trade_only": day_trade_only,
                },
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "items": events[offset : offset + limit],
        "total": len(events),
        "summary": {
            "filter_scope_total": filter_scope_total,
            "filter_scope_count": filter_scope_count,
            "ytd_total": ytd_total,
            "ytd_count": ytd_count,
        },
    }
