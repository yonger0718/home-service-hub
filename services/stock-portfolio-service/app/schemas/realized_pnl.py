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
    note: Optional[str] = None

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
