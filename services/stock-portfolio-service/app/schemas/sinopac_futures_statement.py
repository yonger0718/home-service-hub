"""Payloads of the worker's 永豐期貨 daily / monthly statement posts (spec §5) with the portfolio-side identities."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    drive_file_id: str = Field(max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_name: str


class Close(_Strict):
    date: date
    order_no: str
    qty: int = Field(ge=1)
    price: Decimal


class _Contract(_Strict):
    product: str
    month: str = Field(pattern=r"^\d{4}/\d{2}$")
    strike: Decimal | None = None
    right: Literal["Call", "Put"] | None = None

    @model_validator(mode="after")
    def _strike_and_right_together(self):
        if (self.strike is None) != (self.right is None):
            raise ValueError("strike and right must both be null or both be set")
        return self


class Fill(_Contract):
    order_no: str
    action: Literal["新買", "新賣", "平買", "平賣"]
    qty: int = Field(ge=1)
    price: Decimal
    tax: Decimal
    fee: Decimal
    pnl: Decimal
    closes: list[Close] = Field(default_factory=list)


class FillsTotal(_Strict):
    tax: Decimal
    fee: Decimal
    pnl: Decimal


class Position(_Contract):
    opened: date
    side: Literal["買", "賣"]
    entry_price: Decimal
    mark_price: Decimal
    qty: int = Field(ge=1)
    initial_margin: Decimal
    premium_value: Decimal
    unrealized: Decimal


class Deposit(_Strict):
    seq: str
    direction: Literal["in", "out"]
    amount: Decimal


class Equity(_Strict):
    prev_balance: Decimal
    deposits_net: Decimal
    fees_and_tax: Decimal
    futures_realized: Decimal
    premium_net: Decimal
    balance: Decimal
    futures_unrealized: Decimal
    equity: Decimal
    total_equity: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    securities_collateral: Decimal
    excess_margin: Decimal
    margin_call: Decimal
    additional_margin: Decimal
    withdrawable: Decimal


class DailyPayload(_Strict):
    kind: Literal["daily"]
    source: Source
    statement_date: date
    equity: Equity
    deposits: list[Deposit]
    fills: list[Fill]
    fills_total: FillsTotal | None
    positions: list[Position]
    position_lines: dict[str, Decimal]
    warnings: list[str]

    @model_validator(mode="after")
    def _identities(self):
        e = self.equity
        if e.balance != e.prev_balance + e.deposits_net - e.fees_and_tax + e.futures_realized + e.premium_net:
            raise ValueError("balance identity failed")
        if e.equity != e.balance + e.futures_unrealized:
            raise ValueError("equity identity failed")
        premium = sum(
            (-p.premium_value if p.side == "賣" else p.premium_value for p in self.positions), Decimal(0)
        )
        if e.total_equity != e.equity + premium:
            raise ValueError("total_equity identity failed")
        return self


class MonthLines(_Strict):
    A: Decimal
    B: Decimal
    G: Decimal
    H: Decimal
    K: Decimal
    M: Decimal
    N: Decimal
    Q: Decimal
    P: Decimal
    U: Decimal
    V: Decimal
    W: Decimal
    securities_collateral: Decimal
    additional_margin: Decimal
    withdrawable: Decimal
    long_premium_value: Decimal
    short_premium_value: Decimal
    total_equity: Decimal


class MonthlyPayload(_Strict):
    kind: Literal["monthly"]
    source: Source
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    month_end: date
    lines: MonthLines
    activity_dates: list[date]
    open_positions: int = Field(ge=0)
    warnings: list[str]

    @model_validator(mode="after")
    def _checks(self):
        year, month = int(self.month[:4]), int(self.month[5:])
        last = calendar.monthrange(year, month)[1]
        if self.month_end != date(year, month, last):
            raise ValueError("month_end must be the last day of month")
        for d in self.activity_dates:
            if (d.year, d.month) != (year, month):
                raise ValueError("activity_dates must lie inside the month")
        ln = self.lines
        if ln.M != ln.A + ln.B + ln.G - ln.H + ln.K:
            raise ValueError("M identity failed")
        if ln.Q != ln.M + ln.N:
            raise ValueError("Q identity failed")
        if ln.total_equity != ln.Q + ln.long_premium_value - ln.short_premium_value:
            raise ValueError("total_equity identity failed")
        return self


FuturesPayload = Annotated[DailyPayload | MonthlyPayload, Field(discriminator="kind")]
