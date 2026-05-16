"""Price history + portfolio snapshot endpoints.

- GET  /api/portfolio/price-history?symbol=&from=&to= — OHLC range query.
- POST /api/portfolio/price-history/backfill?date=&market=  — manual OHLC backfill.
- GET  /api/portfolio/history?from=&to=                    — networth time-series.
- POST /api/portfolio/history/snapshot                     — manual snapshot trigger.
"""

from __future__ import annotations

from datetime import date as dt_date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import (
    corporate_action_service,
    market_data_service,
    networth_backfill_service,
    portfolio_snapshot_service,
)

router = APIRouter(prefix="/api/portfolio/price-history", tags=["Portfolio History"])
snapshot_router = APIRouter(prefix="/api/portfolio/history", tags=["Portfolio Snapshot"])
corp_router = APIRouter(prefix="/api/portfolio/corporate-actions", tags=["Portfolio Corporate Actions"])



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


def _serialize_snapshot(row) -> dict:
    return {
        "date": row.date.isoformat(),
        "total_market_value": str(row.total_market_value),
        "total_cost": str(row.total_cost),
        "total_unrealized_pnl": str(row.total_unrealized_pnl),
        "total_dividends": str(row.total_dividends),
        "total_realized_pnl": str(row.total_realized_pnl),
        "portfolio_xirr": str(row.portfolio_xirr) if row.portfolio_xirr is not None else None,
    }


@snapshot_router.get("")
def get_networth_history(
    from_date: Optional[dt_date] = Query(default=None, alias="from"),
    to_date: Optional[dt_date] = Query(default=None, alias="to"),
    interval: str = Query(default="day", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = portfolio_snapshot_service.list_snapshots(
        db, from_date=from_date, to_date=to_date, interval=interval
    )
    return [_serialize_snapshot(row) for row in rows]


@snapshot_router.post("/snapshot")
def trigger_snapshot(db: Session = Depends(get_db)) -> dict:
    row = portfolio_snapshot_service.write_today_snapshot(db)
    return _serialize_snapshot(row)


class NetworthBackfillRequest(BaseModel):
    from_date: dt_date = Field(..., alias="from")
    to_date: dt_date = Field(..., alias="to")
    phase: str = Field(default="both", pattern="^(prices|snapshots|both)$")
    throttle_sec: float = Field(default=networth_backfill_service.DEFAULT_THROTTLE_SEC, ge=0)

    model_config = {"populate_by_name": True}


@snapshot_router.post("/backfill-networth")
def backfill_networth(
    body: NetworthBackfillRequest,
    db: Session = Depends(get_db),
) -> dict:
    if body.from_date > body.to_date:
        raise HTTPException(status_code=400, detail="from must be <= to")
    result = networth_backfill_service.run_backfill(
        db,
        body.from_date,
        body.to_date,
        phase=body.phase,
        throttle_sec=body.throttle_sec,
    )
    return {
        "phase": body.phase,
        "from": body.from_date.isoformat(),
        "to": body.to_date.isoformat(),
        "dates_processed": result.dates_processed,
        "dates_skipped": result.dates_skipped,
        "snapshots_written": result.snapshots_written,
        "rows_written": result.rows_written,
        "errors": [
            {"date": e.date.isoformat(), "reason": e.reason} for e in result.errors
        ],
    }


def _serialize_corp_action(row) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "effective_date": row.effective_date.isoformat(),
        "action_type": row.action_type,
        "ratio": str(row.ratio),
        "source": row.source,
        "source_event_key": row.source_event_key,
    }


@corp_router.get("")
def list_corporate_actions(
    symbol: Optional[str] = Query(default=None),
    from_date: Optional[dt_date] = Query(default=None, alias="from"),
    to_date: Optional[dt_date] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = corporate_action_service.list_actions(
        db, symbol=symbol, from_date=from_date, to_date=to_date
    )
    return [_serialize_corp_action(row) for row in rows]


@corp_router.post("/backfill")
def backfill_corporate_actions(
    year: int = Query(..., ge=1900, le=2999),
    db: Session = Depends(get_db),
) -> dict:
    return corporate_action_service.backfill_year(db, year)
