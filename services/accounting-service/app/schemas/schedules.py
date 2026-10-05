"""Request and response bodies of /schedules (spec "Definition endpoints", "Instance endpoints")."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .writes import Currency, Int32, Money, NonNegativeMoney, ShortText
from .ledger import CurrencyAmountOut

ScheduleLineKind = Literal["expense", "income", "receivable", "payable", "transfer", "repayment", "collection", "interest"]
IntervalUnit = Literal["day", "week", "month", "year"]
PostingMode = Literal["auto", "confirm"]
DefinitionKind = Literal["recurring", "installment"]
DefinitionStatus = Literal["active", "paused", "ended"]
InstanceStatus = Literal["pending", "posted", "skipped"]


class TemplateLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ScheduleLineKind
    account_id: Int32
    to_account_id: Int32 | None = None
    to_amount: Money | None = None
    counterparty_id: Int32 | None = None
    category_id: Int32 | None = None
    project_id: Int32 | None = None
    amount: Money
    currency: Currency
    loan_entry_id: Int32 | None = None
    name: ShortText | None = None
    merchant: ShortText | None = None


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[TemplateLineIn] = Field(min_length=1, max_length=10)
    description: str | None = None
    tags: list[Annotated[str, Field(max_length=64)]] = []


class LoanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: Int32
    counterparty_id: Int32
    category_id: Int32 | None = None
    amount: Money
    entry_date: date
    name: ShortText | None = None


class DefinitionUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    template: TemplateIn
    interval_unit: IntervalUnit
    interval_n: Annotated[int, Field(ge=1, le=999)] = 1
    anchor_date: date
    day_of_month: Annotated[int, Field(ge=1, le=31)] | None = None
    times: Annotated[int, Field(ge=1, le=9999)] | None = None
    end_date: date | None = None
    total_amount: Money | None = None
    posting_mode: PostingMode | None = None  # None (left out) keeps the stored mode; a PUT never switches it silently


class DefinitionIn(DefinitionUpdateIn):
    kind: DefinitionKind
    loan: LoanIn | None = None
    posting_mode: PostingMode = "auto"


class ResumeIn(BaseModel):
    backlog: Literal["skip", "post"] = "skip"


class ModeIn(BaseModel):
    posting_mode: PostingMode


class InstanceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    amounts: list[NonNegativeMoney] | None = None
    # 套用範圍 (proposal decision 24): 僅這一期 / 這一期與之後 / 全部週期
    scope: Literal["this", "following", "all"] = "this"


class RepostIn(BaseModel):
    amounts: list[NonNegativeMoney] = Field(min_length=1)


class TemplateLineOut(BaseModel):
    kind: ScheduleLineKind
    account_id: int
    to_account_id: int | None
    to_amount: str | None
    counterparty_id: int | None
    category_id: int | None
    project_id: int | None
    amount: str
    currency: str
    loan_entry_id: int | None
    name: str | None
    merchant: str | None
    account_name: str | None
    to_account_name: str | None
    category: str | None
    counterparty: str | None


class TemplateOut(BaseModel):
    lines: list[TemplateLineOut]
    description: str | None
    tags: list[str]


class FailingOut(BaseModel):
    instance_id: int
    due_date: date
    last_error: str


class DefinitionOut(BaseModel):
    id: int
    kind: DefinitionKind
    name: str
    status: DefinitionStatus
    posting_mode: PostingMode
    interval_unit: IntervalUnit
    interval_n: int
    anchor_date: date
    day_of_month: int | None
    first_seq: int
    times: int | None
    end_date: date | None
    total_amount: Decimal | None
    auto_post_from: date
    template: TemplateOut
    created_locally: bool
    template_owner_edited: bool
    imported: bool
    locked: bool
    review_reason: str | None
    generated_until: date | None
    posted_count: int
    skipped_count: int
    pending_count: int
    next_due_date: date | None
    next_amount: list[CurrencyAmountOut]
    remaining: Decimal | None
    repaid: Decimal | None
    loan_entry_id: int | None
    needs_check: bool
    failing: FailingOut | None
    category_icon: str | None
    category_color: str | None


class InstanceLineOut(BaseModel):
    kind: ScheduleLineKind
    account_id: int
    account_name: str | None
    to_account_id: int | None
    to_account_name: str | None
    category: str | None
    counterparty: str | None
    amount: Decimal
    currency: str


class InstanceOut(BaseModel):
    id: int
    definition_id: int
    definition_name: str
    kind: DefinitionKind
    posting_mode: PostingMode
    seq: int
    times: int | None
    due_date: date
    rule_date: date
    status: InstanceStatus
    is_partial: bool
    overdue_days: int
    lines: list[InstanceLineOut]
    totals: list[CurrencyAmountOut]
    amounts: list[str]
    last_error: str | None
    reopened: bool
    edited_by_owner: bool
    note: str | None
    posted_entry_ids: list[int]
    acted_at: datetime | None
    acted_by: Literal["auto", "owner", "import"] | None
    category_icon: str | None
    category_color: str | None


class DefinitionDetailOut(DefinitionOut):
    instances: list[InstanceOut]


class FailedOut(BaseModel):
    instance_id: int
    error: str


class CatchUpOut(BaseModel):
    posted: list[int]
    failed: FailedOut | None
    definition: DefinitionOut


class RunReportOut(BaseModel):
    trigger: str
    today: date
    status: str
    generated: int
    generation_failed: list[int]
    posted: list[int]
    failed: list[int]
    stopped_definitions: list[int]
