from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from ..database import get_db
from ..schemas import portfolio as schemas
from ..services import portfolio_service
from ..models import portfolio as models

router = APIRouter(
    prefix="/api/portfolio",
    tags=["Portfolio"]
)

@router.get("/summary", response_model=schemas.PortfolioSummary)
def get_summary(db: Session = Depends(get_db)):
    return portfolio_service.get_portfolio_summary(db)

@router.post("/transactions", response_model=schemas.Transaction)
def create_transaction(transaction: schemas.TransactionCreate, db: Session = Depends(get_db)):
    try:
        return portfolio_service.create_transaction(db, transaction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.put("/transactions/{transaction_id}", response_model=schemas.Transaction)
def update_transaction(transaction_id: int, transaction: schemas.TransactionCreate, db: Session = Depends(get_db)):
    try:
        updated = portfolio_service.update_transaction(db, transaction_id, transaction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated

@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    success = portfolio_service.delete_transaction(db, transaction_id)
    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"message": "Transaction deleted"}

@router.get("/transactions", response_model=schemas.PagedTransactions)
def get_transactions(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    symbol: Optional[str] = Query(default=None),
    date_from: Optional[date_type] = Query(default=None),
    date_to: Optional[date_type] = Query(default=None),
    side: Optional[Literal["BUY", "SELL"]] = Query(default=None),
    sort: str = Query(default="trade_date:desc"),
    db: Session = Depends(get_db),
):
    try:
        sort_field, sort_dir = portfolio_service._parse_sort(
            sort, portfolio_service._TRANSACTION_SORT_FIELDS
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    items, total = portfolio_service.list_transactions(
        db,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        side=side,
        sort_field=sort_field,
        sort_dir=sort_dir,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total}

@router.post("/dividends", response_model=schemas.Dividend)
def create_dividend(dividend: schemas.DividendCreate, db: Session = Depends(get_db)):
    return portfolio_service.create_dividend(db, dividend)

@router.put("/dividends/{dividend_id}", response_model=schemas.Dividend)
def update_dividend(dividend_id: int, dividend: schemas.DividendCreate, db: Session = Depends(get_db)):
    updated = portfolio_service.update_dividend(db, dividend_id, dividend)
    if not updated:
        raise HTTPException(status_code=404, detail="Dividend not found")
    return updated

@router.delete("/dividends/{dividend_id}")
def delete_dividend(dividend_id: int, db: Session = Depends(get_db)):
    success = portfolio_service.delete_dividend(db, dividend_id)
    if not success:
        raise HTTPException(status_code=404, detail="Dividend not found")
    return {"message": "Dividend deleted"}

@router.get("/dividends", response_model=schemas.PagedDividends)
def get_dividends(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    symbol: Optional[str] = Query(default=None),
    date_from: Optional[date_type] = Query(default=None),
    date_to: Optional[date_type] = Query(default=None),
    source: Optional[str] = Query(default=None),
    sort: str = Query(default="ex_dividend_date:desc"),
    db: Session = Depends(get_db),
):
    try:
        sort_field, sort_dir = portfolio_service._parse_sort(
            sort, portfolio_service._DIVIDEND_SORT_FIELDS
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    items, total = portfolio_service.list_dividends(
        db,
        symbol=symbol,
        date_from=date_from,
        date_to=date_to,
        source=source,
        sort_field=sort_field,
        sort_dir=sort_dir,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total}
