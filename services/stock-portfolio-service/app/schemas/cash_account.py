from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models.broker_account import BrokerEnum
from ..models.cash_transaction import CashTxnSource, CashTxnType


def _normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ValueError("currency must be a 3-letter ISO code")
    return normalized


class BrokerAccountBase(BaseModel):
    broker: BrokerEnum
    nickname: str
    currency: str
    opening_balance: Decimal = Decimal("0")
    opening_date: date
    is_active: bool = True

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return _normalize_currency(value)


class BrokerAccountCreate(BrokerAccountBase):
    pass


class BrokerAccountPatch(BaseModel):
    nickname: str | None = None
    opening_balance: Decimal | None = None
    opening_date: date | None = None
    is_active: bool | None = None


class BrokerAccountOut(BrokerAccountBase):
    id: int
    created_at: datetime
    native_balance: Decimal
    target_balance: Decimal | None = None
    target_currency: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CashTransactionBase(BaseModel):
    txn_date: date
    type: CashTxnType
    amount: Decimal
    currency: str
    note: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return _normalize_currency(value)


class CashTransactionCreate(CashTransactionBase):
    pass


class CashTransactionOut(CashTransactionBase):
    id: int
    type: CashTxnType | Literal["trade"]
    account_id: int
    related_transaction_id: int | None = None
    related_dividend_id: int | None = None
    source: CashTxnSource
    import_fingerprint: str
    created_at: datetime
    child_legs: list["CashTransactionOut"] | None = None

    model_config = ConfigDict(from_attributes=True)


CashTransactionOut.model_rebuild()


class CashTransactionPaged(BaseModel):
    items: list[CashTransactionOut]
    total: int
    offset: int
    limit: int


class BalancePoint(BaseModel):
    date: date
    balance: Decimal


class BalanceHistoryOut(BaseModel):
    account_id: int
    currency: str
    points: list[BalancePoint]


class AccountsListOut(BaseModel):
    items: list[BrokerAccountOut]
    target_currency: str | None = None
    total_target_balance: Decimal | None = None
    skipped_currencies: list[str] = Field(default_factory=list)
