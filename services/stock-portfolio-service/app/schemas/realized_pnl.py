from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class RealizedPnlEventOut(BaseModel):
    trade_date: date
    symbol: str
    name: Optional[str] = None
    quantity: int
    sell_price: Decimal
    avg_cost_at_sale: Decimal
    fee: Decimal
    tax: Decimal
    proceeds_gross: Decimal
    proceeds_net: Decimal
    cost_out: Decimal
    realized_pnl: Decimal
    is_day_trade: bool
    position_side: Literal["LONG", "SHORT"] = "LONG"
    broker: Optional[str] = None
    note: Optional[str] = None
    market: str = "TW"
    native_currency: Optional[str] = None
    native_sell_price: Optional[Decimal] = None
    native_proceeds_gross: Optional[Decimal] = None
    native_proceeds: Optional[Decimal] = None
    native_cost: Optional[Decimal] = None
    native_fee: Optional[Decimal] = None
    native_tax: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)


class RealizedPnlSummaryOut(BaseModel):
    filter_scope_total: Decimal
    filter_scope_count: int
    ytd_total: Decimal
    ytd_count: int


class RealizedPnlPagedOut(BaseModel):
    items: List[RealizedPnlEventOut]
    total: int
    summary: RealizedPnlSummaryOut
