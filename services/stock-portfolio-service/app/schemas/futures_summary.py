"""Response of ``GET /api/portfolio/futures/summary`` (spec §9); amounts are strings."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class FuturesValueOut(BaseModel):
    total_equity: str
    balance: str
    source_date: str
    source_kind: Literal["daily", "monthly"]
    positions_open: bool
    frozen: bool


class LatestDailyOut(BaseModel):
    statement_date: str
    withdrawable: str
    initial_margin: str
    maintenance_margin: str
    excess_margin: str
    margin_call: str


class MonthOut(BaseModel):
    month: str
    pnl: Optional[str]
    pnl_complete: bool
    pnl_reason: Optional[Literal["no_baseline", "findings"]]
    futures_realized: str


class FuturesSummary(BaseModel):
    as_of: str
    value: Optional[FuturesValueOut]
    latest_daily: Optional[LatestDailyOut]
    month: Optional[MonthOut]
