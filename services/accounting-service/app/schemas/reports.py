"""Response shapes of GET /reports/* (design D1), field for field the SPA's `reports.model.ts`. Amounts are positive
magnitudes in the main currency, serialized as decimal strings."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class BreakdownSplitOut(BaseModel):
    key: str | None
    label: str
    amount: Decimal
    count: int


class BreakdownRowOut(BaseModel):
    key: str | None
    label: str
    color: str | None
    amount: Decimal
    refund: Decimal
    count: int
    avg: Decimal
    median: Decimal | None = None
    min: Decimal | None = None
    max: Decimal | None = None
    first_date: date | None = None
    last_date: date | None = None
    avg_interval_days: float | None = None
    compare_amount: Decimal | None = None
    splits: list[BreakdownSplitOut] | None = None


class BreakdownTotalsOut(BaseModel):
    expense: Decimal
    income: Decimal
    net: Decimal
    count: int
    refund: Decimal


class BreakdownCompareOut(BaseModel):
    date_from: date
    date_to: date
    totals: BreakdownTotalsOut


class BreakdownOut(BaseModel):
    currency: str
    date_from: date
    date_to: date
    totals: BreakdownTotalsOut
    compare: BreakdownCompareOut | None = None
    rows: list[BreakdownRowOut]
    overlapping: bool
    missing_rates: list[str]
