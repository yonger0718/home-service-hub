from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db
from ..schemas.portfolio import ExDividendRecord
from ..services import dividend_event_service
from ..services.exdividend_service import fetch_upcoming_exdividends
from ..services.portfolio_service import get_active_holdings
from shared_lib import get_tracer

router = APIRouter(
    prefix="/api/portfolio",
    tags=["Portfolio"]
)

tracer = get_tracer("stock-portfolio-service")


@router.get("/ex-dividends/upcoming", response_model=List[ExDividendRecord])
def get_upcoming_exdividends(db: Session = Depends(get_db)):
    """
    Return upcoming ex-dividend announcements from TWSE for stocks
    currently held in the portfolio (quantity > 0).
    """
    with tracer.start_as_current_span("get_upcoming_exdividends") as span:
        active_holdings = get_active_holdings(db)
        held_symbols = set(active_holdings.keys())
        span.set_attribute("portfolio.active_symbol_count", len(held_symbols))

        records = fetch_upcoming_exdividends(held_symbols)
        span.set_attribute("portfolio.exdividend_record_count", len(records))
        return records


@router.get("/dividend-events")
def get_dividend_events(
    year: Optional[int] = Query(default=None, ge=1900, le=2999),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Merged dividend / ex-rights events across TWT48U, TWT49U, and TPEx."""
    active_holdings = get_active_holdings(db)
    held_symbols = set(active_holdings.keys())
    rows = dividend_event_service.fetch_for_holdings(held_symbols, year=year)
    return [
        {
            "symbol": row.symbol,
            "ex_dividend_date": row.ex_dividend_date.isoformat(),
            "cash_dividend": str(row.cash_dividend) if row.cash_dividend is not None else None,
            "stock_dividend": str(row.stock_dividend) if row.stock_dividend is not None else None,
            "source": row.source,
        }
        for row in rows
    ]
