"""GET /api/portfolio/upcoming-events — merged ex-dividend + face-value feed."""
from __future__ import annotations

import logging
from datetime import date as dt_date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import corporate_action_service, dividend_event_service
from ..services.portfolio_service import get_active_holdings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])

_TW_OFFSET = timezone(timedelta(hours=8))


def _today_tw() -> dt_date:
    return datetime.now(_TW_OFFSET).date()


def _classify_dividend(cash, stock) -> str:
    if cash is not None and cash > 0 and stock is not None and stock > 0:
        return "BOTH"
    if cash is not None and cash > 0:
        return "CASH_DIV"
    if stock is not None and stock > 0:
        return "STOCK_DIV"
    return "CASH_DIV"


@router.get("/upcoming-events")
def get_upcoming_events(
    from_: Optional[dt_date] = Query(default=None, alias="from"),
    db: Session = Depends(get_db),
) -> list[dict]:
    pivot = from_ or _today_tw()
    active = get_active_holdings(db)
    held_symbols = set(active.keys())
    name_for: dict[str, Optional[str]] = {
        sym: (info.get("name") if isinstance(info, dict) else None)
        for sym, info in active.items()
    }

    out: list[dict] = []

    for row in dividend_event_service.fetch_upcoming_for_holdings(held_symbols, from_date=pivot):
        out.append(
            {
                "date": row.ex_dividend_date.isoformat(),
                "symbol": row.symbol,
                "name": name_for.get(row.symbol),
                "type": _classify_dividend(row.cash_dividend, row.stock_dividend),
                "cash_dividend": str(row.cash_dividend) if row.cash_dividend is not None else None,
                "stock_dividend_shares": (
                    str(row.stock_dividend) if row.stock_dividend is not None else None
                ),
                "ratio": None,
                "reference_price_change": None,
                "source": row.source,
            }
        )

    for action in corporate_action_service.list_actions(db, from_date=pivot):
        if action.symbol not in held_symbols:
            continue
        out.append(
            {
                "date": action.effective_date.isoformat(),
                "symbol": action.symbol,
                "name": name_for.get(action.symbol),
                "type": "FACE_VALUE",
                "cash_dividend": None,
                "stock_dividend_shares": None,
                "ratio": str(action.ratio),
                "reference_price_change": None,
                "source": action.source,
            }
        )

    out.sort(key=lambda r: (r["date"], r["symbol"]))
    return out
