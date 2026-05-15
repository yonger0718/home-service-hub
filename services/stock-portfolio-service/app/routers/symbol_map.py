from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import symbol_map_service

router = APIRouter(prefix="/api/portfolio/symbol-map", tags=["Portfolio"])


@router.post("/refresh")
def refresh_symbol_map(db: Session = Depends(get_db)) -> dict:
    """Pull latest TWSE/TPEx codes from twstock and upsert into symbol_map."""
    return symbol_map_service.refresh_all_from_twstock(db)


@router.post("/backfill")
def backfill_symbol_map(
    dry_run: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict:
    """Rewrite transactions.symbol from Chinese names to tickers where resolvable."""
    return symbol_map_service.backfill_transactions(db, dry_run=dry_run)
