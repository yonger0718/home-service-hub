"""Read-only ledger feed the accounting service pulls (國泰 settlements and a point-in-time valuation)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class SettlementItem(BaseModel):
    source_id: str  # "ct:<cash_transaction.id>"
    type: str
    amount: str


class SettlementRow(BaseModel):
    """One trade day of 國泰 cash movements.

    `net_amount` is the statement's cash net (淨收付) on a bookable statement day, the legs' net on a CSV-only
    day, and the statement's 應收付 on a `margin_day` row — never book from a row whose `bookable` is false.
    """

    trade_date: str
    settlement_date: Optional[str]
    date_source: Literal["statement", "legs"]
    net_amount: str
    legs_net: str
    currency: Literal["TWD"] = "TWD"
    bookable: bool
    reason: Optional[Literal["margin_day", "legs_disagree"]]
    items: list[SettlementItem]
    fingerprint: str


class SettlementsOut(BaseModel):
    rows: list[SettlementRow]
    bounds: dict[str, str]
    complete: bool


class DividendRow(BaseModel):
    id: int
    symbol: str
    receipt_date: str
    amount: str  # net of 匯費 / 補充保費, as stored
    currency: str
    fingerprint: str  # "dividend:<id>"


class DividendsOut(BaseModel):
    rows: list[DividendRow]
    complete: bool


class ValuationOut(BaseModel):
    broker: str
    currency: Literal["TWD"] = "TWD"
    as_of: str
    priced_through: Optional[str]
    market_value: str
    cash: str
    total: str
    complete: bool
    missing: list[str]
