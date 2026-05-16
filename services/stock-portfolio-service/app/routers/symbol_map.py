from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.portfolio import Transaction
from ..models.symbol_map import SymbolMap
from ..services import symbol_map_service

router = APIRouter(prefix="/api/portfolio/symbol-map", tags=["Portfolio"])


@router.get("/names", response_model=dict[str, str])
def symbol_names(db: Session = Depends(get_db)) -> dict[str, str]:
    """Return {symbol: display_name} for every symbol the user has ever traded.

    Lookup order per symbol: latest non-null ``Transaction.name``, then
    ``SymbolMap.name`` (Chinese-name dictionary).
    """
    # Latest non-null name per symbol: pick the row with the max trade_date.
    latest_per_symbol = (
        select(Transaction.symbol, func.max(Transaction.trade_date).label("max_dt"))
        .where(Transaction.name.is_not(None))
        .group_by(Transaction.symbol)
        .subquery()
    )
    tx_rows = db.execute(
        select(Transaction.symbol, Transaction.name)
        .join(
            latest_per_symbol,
            (Transaction.symbol == latest_per_symbol.c.symbol)
            & (Transaction.trade_date == latest_per_symbol.c.max_dt),
        )
        .where(Transaction.name.is_not(None))
    ).all()
    out: dict[str, str] = {}
    for symbol, name in tx_rows:
        if name and symbol not in out:
            out[symbol] = name

    missing = [
        symbol
        for symbol, in db.execute(
            select(Transaction.symbol).distinct()
        ).all()
        if symbol not in out
    ]
    if missing:
        sm_rows = db.execute(
            select(SymbolMap.symbol, SymbolMap.name).where(SymbolMap.symbol.in_(missing))
        ).all()
        for symbol, name in sm_rows:
            out.setdefault(symbol, name)
    return out


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
