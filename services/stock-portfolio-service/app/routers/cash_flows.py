from __future__ import annotations

import hashlib
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import portfolio as portfolio_models
from ..models.broker_account import BrokerAccount, BrokerEnum
from ..models.cash_transaction import CashTxnType
from ..schemas import cash_account as cash_schemas
from ..schemas import portfolio as schemas
from ..services import cash_account_service, cash_flow_service

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio Cash Flows"])


_PHASE4_TO_TXN_TYPE: dict[schemas.BrokerCashFlowType, CashTxnType] = {
    schemas.BrokerCashFlowType.DEPOSIT: CashTxnType.DEPOSIT,
    schemas.BrokerCashFlowType.WITHDRAWAL: CashTxnType.WITHDRAW,
    schemas.BrokerCashFlowType.INTEREST: CashTxnType.INTEREST_IN,
    schemas.BrokerCashFlowType.DIVIDEND_CASH: CashTxnType.DIVIDEND_CASH,
    schemas.BrokerCashFlowType.FEE: CashTxnType.FEE,
}


@router.get("/broker-cash-flows", response_model=list[schemas.BrokerCashBalance])
def get_broker_cash_flows(
    as_of_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return cash_flow_service.list_balances(db, as_of_date=as_of_date)


@router.post(
    "/broker-cash-flows",
    response_model=schemas.BrokerCashBalance,
    status_code=201,
)
def create_broker_cash_flow(
    payload: schemas.BrokerCashFlowIn,
    db: Session = Depends(get_db),
):
    """Manual per-broker cash entry.

    TW_CATHAY writes to the legacy ``cash_transaction`` ledger so the
    accounts page balance history and snapshots stay coherent. Foreign
    brokers write to ``broker_cash_flows``. Returns the resulting (broker,
    currency) balance so the caller can update its tile inline.
    """
    broker_value = (
        payload.broker.value
        if hasattr(payload.broker, "value")
        else str(payload.broker)
    )
    if broker_value == portfolio_models.Broker.TW_CATHAY.value:
        account_id = (
            db.query(BrokerAccount.id)
            .filter(
                BrokerAccount.broker == BrokerEnum.CATHAY,
                BrokerAccount.currency == payload.currency,
                BrokerAccount.is_active.is_(True),
            )
            .order_by(BrokerAccount.id.asc())
            .scalar()
        )
        if account_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"No active Cathay {payload.currency} account",
            )
        txn_type = _PHASE4_TO_TXN_TYPE[payload.type]
        cash_account_service.create_manual_cash_transaction(
            db,
            account_id,
            cash_schemas.CashTransactionCreate(
                txn_date=payload.date,
                type=txn_type,
                amount=payload.amount,
                currency=payload.currency,
                note=payload.note,
            ),
        )
    else:
        if payload.import_fingerprint:
            fingerprint = payload.import_fingerprint
        else:
            raw = (
                f"manual|{broker_value}|{payload.date.isoformat()}|"
                f"{payload.type.value}|{uuid.uuid4().hex}"
            )
            fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        cash_flow_service.write_cash_flows(
            db,
            [
                cash_flow_service.CashFlowRow(
                    broker=broker_value,
                    date=payload.date,
                    type=payload.type.value,
                    amount=payload.amount,
                    currency=payload.currency,
                    fx_rate_to_twd=payload.fx_rate_to_twd,
                    note=payload.note,
                    import_fingerprint=fingerprint,
                )
            ],
        )
    balances = cash_flow_service.list_balances(db, as_of_date=payload.date)
    for row in balances:
        if row["broker"] == broker_value and row["currency"] == payload.currency:
            return row
    return {
        "broker": broker_value,
        "currency": payload.currency,
        "balance": Decimal("0"),
        "as_of_date": payload.date,
    }
