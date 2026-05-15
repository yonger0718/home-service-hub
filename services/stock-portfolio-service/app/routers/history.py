"""Price history + portfolio snapshot endpoints.

- GET  /api/portfolio/price-history?symbol=&from=&to= — OHLC range query.
- POST /api/portfolio/price-history/backfill?date=&market=  — manual OHLC backfill.
- GET  /api/portfolio/history?from=&to=                    — networth time-series.
- POST /api/portfolio/history/snapshot                     — manual snapshot trigger.
"""

from __future__ import annotations

from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import (
    corporate_action_service,
    market_data_service,
    portfolio_snapshot_service,
)

router = APIRouter(prefix="/api/portfolio/price-history", tags=["Portfolio History"])
snapshot_router = APIRouter(prefix="/api/portfolio/history", tags=["Portfolio Snapshot"])
corp_router = APIRouter(prefix="/api/portfolio/corporate-actions", tags=["Portfolio Corporate Actions"])

_TW_OFFSET = timezone(timedelta(hours=8))
DEFAULT_HISTORY_WINDOW_DAYS = 90


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
        "portfolio_xirr": str(row.portfolio_xirr) if row.portfolio_xirr is not None else None,
    }


@snapshot_router.get("")
def get_networth_history(
    from_date: Optional[dt_date] = Query(default=None, alias="from"),
    to_date: Optional[dt_date] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
) -> list[dict]:
    today = datetime.now(_TW_OFFSET).date()
    end = to_date or today
    start = from_date or (end - timedelta(days=DEFAULT_HISTORY_WINDOW_DAYS))
    rows = portfolio_snapshot_service.list_snapshots(db, from_date=start, to_date=end)
    return [_serialize_snapshot(row) for row in rows]


@snapshot_router.post("/snapshot")
def trigger_snapshot(db: Session = Depends(get_db)) -> dict:
    row = portfolio_snapshot_service.write_today_snapshot(db)
    return _serialize_snapshot(row)


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
