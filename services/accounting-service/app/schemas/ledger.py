from datetime import date, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

EntryKind = Literal[
    "expense", "income", "transfer_out", "transfer_in", "receivable", "payable",
    "balance_adjustment", "fee", "discount", "reward", "interest", "refund",
]


class AccountOut(BaseModel):
    id: int
    name: str
    currency: str
    opening_balance: Decimal
    balance: Decimal
    entry_count: int


class EntryOut(BaseModel):
    id: int
    kind: EntryKind
    amount: Decimal
    currency: str
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    fx_source: Literal["fx_api", "moze_backup"] | None
    entry_date: date
    entry_time: time | None
    category: str | None
    project: str | None
    name: str | None
    merchant: str | None
    counterparty: str | None
    description: str | None
    tags: list[str]
    parent_entry_id: int | None
    transfer_group_id: UUID | None
    needs_review: bool
    running_balance: Decimal


class EntryPage(BaseModel):
    items: list[EntryOut]
    total: int
    limit: int
    offset: int
