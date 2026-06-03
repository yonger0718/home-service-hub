from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.broker_account import BrokerAccount
from ..models.cash_transaction import CashTxnType
from ..schemas.cash_account import (
    AccountsListOut,
    BalanceHistoryOut,
    BrokerAccountCreate,
    BrokerAccountOut,
    BrokerAccountPatch,
    CashTransactionCreate,
    CashTransactionOut,
    CashTransactionPaged,
)
from ..services import cash_account_service

router = APIRouter(
    prefix="/api/portfolio/accounts",
    tags=["portfolio-accounts"],
)


def _account_out(db: Session, account_id: int, in_currency: str | None = None) -> BrokerAccountOut:
    accounts = cash_account_service.list_accounts(
        db,
        include_inactive=True,
        in_currency=in_currency,
    )
    for account in accounts.items:
        if account.id == account_id:
            return account
    raise HTTPException(status_code=404, detail="account not found")


@router.get("/", response_model=AccountsListOut)
def get_accounts(
    include_inactive: bool = Query(default=False),
    in_currency: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AccountsListOut:
    return cash_account_service.list_accounts(
        db,
        include_inactive=include_inactive,
        in_currency=in_currency,
    )


@router.post("/", response_model=BrokerAccountOut)
def create_account(
    payload: BrokerAccountCreate,
    db: Session = Depends(get_db),
) -> BrokerAccountOut:
    try:
        account = cash_account_service.create_account(db, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="account already exists") from exc
    return _account_out(db, account.id)


@router.patch("/{account_id}", response_model=BrokerAccountOut)
def patch_account(
    account_id: int,
    payload: BrokerAccountPatch,
    db: Session = Depends(get_db),
) -> BrokerAccountOut:
    try:
        account = cash_account_service.patch_account(db, account_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="account already exists") from exc
    return _account_out(db, account.id)


@router.get("/{account_id}/cash-transactions", response_model=CashTransactionPaged)
def get_cash_transactions(
    account_id: int,
    date_from: date_type | None = Query(default=None),
    date_to: date_type | None = Query(default=None),
    type: CashTxnType | None = Query(default=None),
    sort: str = Query(default="txn_date:desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=200),
    merge_related: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if db.get(BrokerAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    try:
        items, total = cash_account_service.list_cash_transactions(
            db,
            account_id,
            date_from=date_from,
            date_to=date_to,
            type_=type,
            sort=sort,
            offset=offset,
            limit=limit,
            merge_related=merge_related,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post("/{account_id}/cash-transactions", response_model=CashTransactionOut)
def create_cash_transaction(
    account_id: int,
    payload: CashTransactionCreate,
    db: Session = Depends(get_db),
) -> CashTransactionOut:
    try:
        return cash_account_service.create_manual_cash_transaction(db, account_id, payload)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="cash transaction already exists") from exc
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.delete("/{account_id}/cash-transactions/{txn_id}")
def delete_cash_transaction(
    account_id: int,
    txn_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    try:
        deleted_id = cash_account_service.delete_manual_cash_transaction(db, account_id, txn_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="cash transaction not found") from exc
    except ValueError as exc:
        if str(exc) == "not_manual":
            return JSONResponse(
                status_code=403,
                content={"detail": "only manual cash transactions can be deleted"},
            )
        raise
    return {"deleted_id": deleted_id}


@router.get("/{account_id}/balance-history", response_model=BalanceHistoryOut)
def get_balance_history(
    account_id: int,
    date_from: date_type = Query(...),
    date_to: date_type = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        points = cash_account_service.get_balance_history(
            db,
            account_id,
            date_from,
            date_to,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc
    account = db.get(BrokerAccount, account_id)
    return {
        "account_id": account_id,
        "currency": account.currency,
        "points": points,
    }
