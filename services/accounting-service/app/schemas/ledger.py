from datetime import date, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

EntryKind = Literal[
    "expense", "income", "transfer_out", "transfer_in", "receivable", "payable",
    "balance_adjustment", "fee", "discount", "reward", "interest", "refund",
]
EntrySource = Literal["moze_import", "moze_backup", "manual", "hermes", "rule"]
FxSource = Literal["fx_api", "moze_backup", "manual"]
RoundingMode = Literal["keep", "round", "floor", "ceil"]


class RewardRuleOut(BaseModel):
    id: int
    account_id: int
    name: str
    method: Literal["percent", "fixed"]
    rate: Decimal | None
    fixed_amount: Decimal | None
    window: Literal["statement_cycle"]
    posting: Literal["after_window", "after_transaction", "manual"]
    delay_days: int
    post_month_offset: int
    post_day: int
    txn_rounding: RoundingMode
    total_rounding: RoundingMode
    total_cap: Decimal | None
    shared_cap_id: UUID | None
    is_basic: bool
    reward_account_id: int | None
    reward_project_id: int | None
    starts_on: date | None
    ends_on: date | None
    is_enabled: bool
    description: str | None
    sort_order: int
    moze_id: str | None


class AccountOut(BaseModel):
    id: int
    name: str
    currency: str
    opening_balance: Decimal
    balance: Decimal
    balance_main: Decimal | None
    entry_count: int
    group_id: int | None
    group_name: str | None
    icon: str | None
    color: str | None
    is_archived: bool
    include_in_total: bool
    is_credit: bool
    closing_day: int | None
    due_rule: Literal["fixed_day", "days_after_closing"] | None
    due_value: int | None
    credit_limit: Decimal | None
    available_credit: Decimal | None
    combined_account_id: int | None
    credit_sharing_id: UUID | None
    auto_pay_account_id: int | None
    fx_fee_pct: Decimal | None
    fx_fee_rounding: RoundingMode | None
    fx_fee_refundable: bool
    sort_order: int
    settings_locally_edited: bool
    moze_id: str | None
    rule_summaries: list[str]


class AccountDetailOut(AccountOut):
    note: str | None
    reward_rules: list[RewardRuleOut]
    credit_sharing_members: list[int] = []


class EntryGroupSummaryOut(BaseModel):
    id: int
    kind: Literal["split", "reward_claim", "installment"]
    name: str | None
    merchant: str | None  # the entry_group row's own fields, so a client can rebuild PUT /splits from them
    description: str | None
    count: int
    total: Decimal | None
    currency: str


class EntryOut(BaseModel):
    id: int
    kind: EntryKind
    amount: Decimal
    currency: str
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    fx_source: FxSource | None
    entry_date: date
    entry_time: time | None
    posted_date: date
    account_id: int
    account_name: str
    category_id: int | None
    category: str | None
    category_icon: str | None
    category_color: str | None
    project_id: int | None
    project: str | None
    name: str | None
    merchant: str | None
    counterparty_id: int | None
    counterparty: str | None
    description: str | None
    tags: list[str]
    parent_entry_id: int | None
    transfer_group_id: UUID | None
    is_settlement: bool
    is_closed: bool
    group: EntryGroupSummaryOut | None
    rule_names: list[str]
    invoice_number: str | None
    needs_review: bool
    source: EntrySource
    moze_id: str | None
    locked: bool
    running_balance: Decimal
    # Receivable / payable originals: |amount + Σ linked settlements| (0 when closed); null for every other row.
    open_amount: Decimal | None


class EntryPage(BaseModel):
    items: list[EntryOut]
    total: int
    limit: int
    offset: int


class EntryDetailOut(EntryOut):
    invoice_random: str | None
    children: list[EntryOut]
    group_members: list[EntryOut]
    transfer_counterpart: EntryOut | None
    settles: EntryOut | None
    settled_by: list[EntryOut]
    refunds: EntryOut | None
    refunded_by: list[EntryOut]
    rules: list[RewardRuleOut]
    rewards: list[EntryOut]
    is_settled: bool | None
    refunded_amount: Decimal


class MonthSummaryOut(BaseModel):
    month: str
    currency: str
    expense: Decimal
    income: Decimal
    net: Decimal
    missing_rates: list[str]


class DaySummaryOut(BaseModel):
    date: date
    expense: Decimal
    income: Decimal
    count: int


class DailySummaryOut(BaseModel):
    """The month summary's figures per entry_date, for the calendar view; days without counted rows are omitted."""

    month: str
    currency: str
    days: list[DaySummaryOut]
    missing_rates: list[str]


class AccountPeriodSummaryOut(BaseModel):
    """One account over [date_from, date_to] by posted_date, in the account's currency."""

    account_id: int
    currency: str
    date_from: date
    date_to: date
    spend: Decimal
    income: Decimal
    rewards: Decimal
    net: Decimal
    end_balance: Decimal
    count: int


# Settings read shapes; Task 16's settings routers use them as response models.
class AccountGroupOut(BaseModel):
    id: int
    name: str
    sort_order: int
    moze_id: str | None


class CategoryOut(BaseModel):
    id: int
    kind: EntryKind
    parent_id: int | None
    name: str
    icon: str | None
    color: str | None
    sort_order: int
    is_hidden: bool
    default_account_id: int | None
    default_project_id: int | None
    moze_id: str | None
    children: list["CategoryOut"] = []


class ProjectOut(BaseModel):
    id: int
    name: str
    is_archived: bool
    sort_order: int
    moze_id: str | None


class OpenAmountOut(BaseModel):
    currency: str
    amount: Decimal


class CounterpartyOut(BaseModel):
    id: int
    name: str
    moze_id: str | None
    open_amounts: list[OpenAmountOut]
    # Open (unsettled, not closed) receivable / payable originals: never nets the two sides like `open_amounts`.
    open_count: int


class PreferenceOut(BaseModel):
    expense_income_colors: Literal["red_green", "green_red"]
    keypad_layout: Literal["calculator", "phone"]
    week_start: int
    main_currency: str
    hide_rewards_on_timeline: bool
    abbreviate_totals: bool


class FxRateOut(BaseModel):
    date: date  # the requested day
    base: str
    quote: str
    rate: Decimal
    source: str
    rate_date: date  # the release the rate comes from; earlier than `date` for today / future days (latest release)
