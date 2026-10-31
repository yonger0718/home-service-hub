"""Response shapes of GET /reports/* (design D1), field for field the SPA's `reports.model.ts`. Amounts are positive
magnitudes in the main currency, serialized as decimal strings."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


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


class TrendBucketOut(BaseModel):
    start: date
    values: dict[str, Decimal]


class TrendOut(BaseModel):
    currency: str
    granularity: Literal["month", "week", "day"]
    buckets: list[TrendBucketOut]
    average: dict[str, Decimal]


# ---- budgets (design D3) ----------------------------------------------------------------------------------------

BudgetScope = Literal["total", "category", "project"]
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class BudgetIn(BaseModel):
    """`POST /reports/budgets`. `effective_from` is required for `total` / `category` (checked by the service) and
    ignored for `project`."""

    scope: BudgetScope
    category_id: int | None = None
    project_id: int | None = None
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    effective_from: str | None = Field(default=None, pattern=MONTH_PATTERN)


class BudgetAmendIn(BaseModel):
    """`PUT /reports/budgets/{id}`: a new limit from `effective_from` (ignored for a project budget)."""

    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    effective_from: str | None = Field(default=None, pattern=MONTH_PATTERN)


class BudgetStatusOut(BaseModel):
    id: int
    scope: BudgetScope
    category_id: int | None
    project_id: int | None
    label: str
    color: str | None
    limit: Decimal
    used: Decimal
    remaining: Decimal
    ratio: float
    over: bool
    effective_from: str | None


class BudgetStatusListOut(BaseModel):
    month: str
    currency: str
    time_progress: float
    budgets: list[BudgetStatusOut]
