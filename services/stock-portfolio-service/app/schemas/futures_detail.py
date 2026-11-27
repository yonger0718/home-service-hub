"""Responses of ``GET /api/portfolio/futures/months`` and ``/detail`` (期貨明細); amounts are strings."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class FuturesMonths(BaseModel):
    months: list[str]


class CloseOut(BaseModel):
    date: str
    qty: int
    price: str


class FillOut(BaseModel):
    kind: Literal["future", "option"]
    order_no: str
    product: str
    month: str
    strike: Optional[str]
    right: Optional[Literal["Call", "Put"]]
    action: Literal["新買", "新賣", "平買", "平賣"]
    qty: int
    price: str
    tax: str
    fee: str
    pnl: str
    closes: list[CloseOut]


class FillsTotalOut(BaseModel):
    tax: str
    fee: str
    pnl: str


class DepositOut(BaseModel):
    direction: Literal["in", "out"]
    amount: str


class DayOut(BaseModel):
    date: str
    no_trade: bool
    fills: list[FillOut]
    fills_total: Optional[FillsTotalOut]
    deposits: list[DepositOut]


class DetailSummaryOut(BaseModel):
    futures_realized: str
    premium_net: str
    fees_and_tax: str
    deposits_net: str
    source: Literal["monthly", "daily_sum"]
    complete: bool
    reason: Optional[Literal["findings"]]
    missing_days: int


class PositionOut(BaseModel):
    product: str
    month: str
    strike: Optional[str]
    right: Optional[Literal["Call", "Put"]]
    side: Literal["買", "賣"]
    qty: int
    entry_price: str
    mark_price: str
    unrealized: str
    premium_value: str


class PositionsOut(BaseModel):
    date: str
    month_end_may_differ: bool
    items: list[PositionOut]


class FuturesDetail(BaseModel):
    month: str
    summary: DetailSummaryOut
    days: list[DayOut]
    positions: Optional[PositionsOut]
