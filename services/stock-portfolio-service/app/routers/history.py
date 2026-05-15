"""Price history endpoints.

- GET /api/portfolio/price-history?symbol=&from=&to= — range query for charts.
- POST /api/portfolio/price-history/backfill?date=YYYY-MM-DD&market=TWSE|TPEX|BOTH
  — manual trigger. The scheduler (next milestone) will call the same service
  function on a cron.
"""

from __future__ import annotations

from datetime import date as dt_date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import market_data_service

router = APIRouter(prefix="/api/portfolio/price-history", tags=["Portfolio History"])


@router.get("")
def get_price_history(
    symbol: str = Query(...),
    from_date: dt_date = Query(..., alias="from"),
    to_date: dt_date = Query(..., alias="to"),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = market_data_service.list_history(
        db, symbol=symbol, from_date=from_date, to_date=to_date
    )
    return [
        {
            "symbol": row.symbol,
            "date": row.date.isoformat(),
            "open": str(row.open) if row.open is not None else None,
            "high": str(row.high) if row.high is not None else None,
            "low": str(row.low) if row.low is not None else None,
            "close": str(row.close),
            "volume": row.volume,
            "turnover": str(row.turnover) if row.turnover is not None else None,
            "source": row.source,
        }
        for row in rows
    ]


@router.post("/backfill")
def backfill_price_history(
    date: dt_date = Query(...),
    market: str = Query(default="BOTH", pattern="^(TWSE|TPEX|BOTH)$"),
    db: Session = Depends(get_db),
) -> dict:
    return market_data_service.backfill_date(db, date, market=market)
