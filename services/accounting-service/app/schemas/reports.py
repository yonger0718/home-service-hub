"""Response shapes of GET /reports/* (design D1), field for field the SPA's `reports.model.ts`. Amounts are positive
magnitudes in the main currency, serialized as decimal strings."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from ..services.report_rules import Compare, Dimension, Metric
from .writes import Int32


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


# ---- saved reports (design D4) ------------------------------------------------------------------------------------

PeriodPresetName = Literal["this_month", "last_month", "last_6_months", "last_12_months", "this_year", "last_year", "all"]
ReportChart = Literal["bar", "line", "donut", "table"]


class PresetPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: PeriodPresetName


class DatePeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: date = Field(alias="from")
    to: date


def _decimal_text(value: str) -> str:
    """A finite decimal number as text (what the GET's `Decimal` query parameter accepts), kept as sent."""
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("not a decimal number") from None
    if not number.is_finite():
        raise ValueError("not a finite decimal number")
    return value


DecimalText = Annotated[str, Field(max_length=40), AfterValidator(_decimal_text)]
MAX_FILTER_ITEMS = 100


class ReportFiltersIn(BaseModel):
    """The SPA's wire `ReportFilters` (reports.model.ts); `include_excluded_projects` is a GET flag, never stored.
    As strict as the GET it feeds: text up to 100 characters, decimal bounds, int4 ids, at most 100 items a list."""

    model_config = ConfigDict(extra="forbid")

    account_id: list[Int32] = Field(default=[], max_length=MAX_FILTER_ITEMS)
    category_id: list[Int32] = Field(default=[], max_length=MAX_FILTER_ITEMS)
    project_id: list[Int32] = Field(default=[], max_length=MAX_FILTER_ITEMS)
    tag: list[str] = Field(default=[], max_length=MAX_FILTER_ITEMS)
    merchant: str | None = Field(default=None, max_length=100)
    counterparty_id: Int32 | None = None
    amount_min: DecimalText | None = None
    amount_max: DecimalText | None = None
    q: str | None = Field(default=None, max_length=100)
    weekday: list[Annotated[int, Field(ge=1, le=7)]] = Field(default=[], max_length=MAX_FILTER_ITEMS)


class ReportConfigIn(BaseModel):
    """The versioned `saved_report.config`. Stored dumped by alias so `from` stays `from`."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    period: PresetPeriod | DatePeriod
    metric: Metric
    group_by: Dimension
    split_by: Dimension | None = None
    filters: ReportFiltersIn = ReportFiltersIn()
    compare: Compare | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.split_by is not None and self.split_by == self.group_by:
            raise ValueError("split_by must differ from group_by")
        if isinstance(self.period, DatePeriod) and self.period.from_ > self.period.to:
            raise ValueError("period.from must not follow period.to")
        return self

    def stored(self) -> dict:
        """What the client sent, normalised (alias kept, defaults not invented); `filters` is always present."""
        stored = self.model_dump(mode="json", by_alias=True, exclude_unset=True)
        stored.setdefault("filters", {})
        return stored


class SavedReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    config: ReportConfigIn
    chart: ReportChart
    pinned: bool
    sort_order: Int32 | None = None  # an INTEGER column


class SavedReportOut(BaseModel):
    id: int
    name: str
    config: dict
    chart: ReportChart
    pinned: bool
    sort_order: int
    stale_refs: list[str]
    created_at: datetime
    updated_at: datetime
