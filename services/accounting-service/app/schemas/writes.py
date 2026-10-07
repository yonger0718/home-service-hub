"""Request bodies for ledger and settings writes (phase 2a). Amounts are unsigned unless named otherwise."""

from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from .ledger import EntryDetailOut, EntryKind

MAX_ABS_AMOUNT = Decimal("1e16")  # NUMERIC(20,4) holds 16 integer digits


def _four_places(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("must be a finite number")
    if value.normalize().as_tuple().exponent < -4:
        raise ValueError("at most 4 decimal places")
    if abs(value) >= MAX_ABS_AMOUNT:
        raise ValueError("too large for NUMERIC(20,4)")
    return value


SignedMoney = Annotated[Decimal, AfterValidator(_four_places)]
Money = Annotated[Decimal, Field(gt=0), AfterValidator(_four_places)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0), AfterValidator(_four_places)]
MAX_RATE = Decimal("1e9")
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
Rate = Annotated[Decimal, Field(gt=0, le=MAX_RATE)]
Int32 = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]  # ids and sort_order are INTEGER columns
Currency = Annotated[str, Field(min_length=1, max_length=8, pattern=r"^[A-Z0-9]+$")]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")]
ShortText = Annotated[str, Field(max_length=128)]
EditableKind = Literal["expense", "income", "receivable", "payable"]
RoundingMode = Literal["keep", "round", "floor", "ceil"]


class ChildIn(BaseModel):
    amount: Money
    name: ShortText | None = None


class EntryIn(BaseModel):
    account_id: Int32
    kind: EditableKind
    amount: Money | None = None
    original_amount: Money | None = None
    original_currency: Currency | None = None
    fx_rate: Rate | None = None
    entry_date: date
    entry_time: time | None = None
    posted_date: date | None = None
    category_id: Int32 | None = None
    project_id: Int32 | None = None
    name: ShortText | None = None
    merchant: ShortText | None = None
    counterparty_id: Int32 | None = None
    description: str | None = None
    tags: list[str] = []
    invoice_number: Annotated[str, Field(max_length=16)] | None = None
    invoice_random: Annotated[str, Field(max_length=8)] | None = None
    fee: ChildIn | None = None
    discount: ChildIn | None = None
    reward_rule_ids: list[Int32] = []


class EntryUpdateIn(EntryIn):
    pass


MAX_SPLIT_MEMBERS = 50  # POST /splits and convert; PUT /splits/{id} allows max(50, the group's current member count)
ClientKey = Annotated[str, Field(min_length=1, max_length=64)]


class SplitMemberIn(EntryIn):
    """A full split member. `id` names an existing member (PUT /splits/{id} upsert, the convert anchor); without
    it the member is new. `client_key` is echoed in the response and never stored. entry_date, entry_time,
    posted_date, project_id and tags default to the group's when omitted (split_service.member_payload)."""

    id: Int32 | None = None
    client_key: ClientKey | None = None
    entry_date: date | None = None


class SplitKeepIn(BaseModel):
    """A metadata-only reference to an existing member (the only form a protected member accepts). Omitted fields
    stay as stored; sent fields, `null` and `[]` included, are applied. Amount, kind, account, category,
    counterparty, FX, fee / discount, rule, merchant, invoice and date fields are refused (extra="forbid")."""

    model_config = ConfigDict(extra="forbid")

    id: Int32
    keep: Literal[True]
    client_key: ClientKey | None = None
    name: ShortText | None = None
    project_id: Int32 | None = None
    tags: list[str] | None = None
    description: str | None = None


def _member_form(value) -> str:
    """Discriminator of SplitIn.members: `keep: true` selects SplitKeepIn, anything else a full member."""
    keep = value.get("keep") if isinstance(value, dict) else getattr(value, "keep", None)
    return "keep" if keep is True else "full"


SplitMemberItem = Annotated[
    Annotated[SplitMemberIn, Tag("full")] | Annotated[SplitKeepIn, Tag("keep")],
    Discriminator(_member_form),
]


class TransferIn(BaseModel):
    from_account_id: Int32
    to_account_id: Int32
    out_amount: Money
    in_amount: Money | None = None
    entry_date: date
    entry_time: time | None = None
    posted_date: date | None = None
    category_id: Int32 | None = None
    name: ShortText | None = None
    merchant: ShortText | None = None
    description: str | None = None
    project_id: Int32 | None = None
    tags: list[str] = []
    out_fee: ChildIn | None = None
    out_discount: ChildIn | None = None
    in_fee: ChildIn | None = None
    in_discount: ChildIn | None = None
    reward_rule_ids: list[Int32] = []


class SplitIn(BaseModel):
    """Body of POST /splits, PUT /splits/{id} and PUT /entries/{id}/split. Group fields plus 1+ members. The
    schema-level checks (duplicate ids, POST >= 2 without ids, convert: exactly the anchor with an id) run before
    any database read; PUT's <= max(50, current) needs the group, so it runs after the group lookup (404 group,
    409 group_scheduled come first). See the precedence chain in openspec accounting-ledger "Split write phases…"."""

    name: ShortText | None = None
    merchant: ShortText | None = None
    description: str | None = None
    entry_date: date
    entry_time: time | None = None
    posted_date: date | None = None
    project_id: Int32 | None = None
    tags: list[str] = []
    members: list[SplitMemberItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_ids_and_client_keys(self) -> "SplitIn":
        ids = [member.id for member in self.members if member.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("members: duplicate id")
        keys = [member.client_key for member in self.members if member.client_key is not None]
        if len(keys) != len(set(keys)):
            raise ValueError("members: duplicate client_key")
        return self


class SettleIn(BaseModel):
    account_id: Int32
    amount: Money
    entry_date: date
    entry_time: time | None = None
    description: str | None = None


class RefundIn(BaseModel):
    account_id: Int32 | None = None
    amount: Money
    entry_date: date
    entry_time: time | None = None
    description: str | None = None


class BalanceAdjustmentIn(BaseModel):
    account_id: Int32
    target_balance: SignedMoney
    entry_date: date
    entry_time: time | None = None
    description: str | None = None


class AccountIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    currency: Currency
    opening_balance: SignedMoney = Decimal("0")
    is_archived: bool = False
    group_id: Int32 | None = None
    icon: Annotated[str, Field(max_length=16)] | None = None
    color: Color | None = None
    note: str | None = None
    sort_order: Int32 = 0
    include_in_total: bool = True
    is_credit: bool = False
    closing_day: Annotated[int, Field(ge=1, le=31)] | None = None
    due_rule: Literal["fixed_day", "days_after_closing"] | None = None
    due_value: Annotated[int, Field(ge=0, le=120)] | None = None
    credit_limit: NonNegativeMoney | None = None
    combined_account_id: Int32 | None = None
    credit_sharing_id: UUID | None = None
    # 額度共用: the full list of the OTHER accounts sharing this account's limit. Omitted (None) keeps the old
    # behaviour (credit_sharing_id written as sent); present, the service rebuilds the set from it atomically.
    credit_sharing_members: list[Int32] | None = None
    auto_pay_account_id: Int32 | None = None
    fx_fee_pct: Annotated[Decimal, Field(ge=0, le=100, max_digits=6, decimal_places=3)] | None = None
    fx_fee_rounding: RoundingMode | None = None
    fx_fee_refundable: bool = False


class AccountGroupIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    sort_order: Int32 = 0


class CategoryIn(BaseModel):
    kind: EntryKind
    parent_id: Int32 | None = None
    name: Annotated[str, Field(min_length=1, max_length=64)]
    icon: Annotated[str, Field(max_length=16)] | None = None
    color: Color | None = None
    sort_order: Int32 = 0
    is_hidden: bool = False


class ProjectIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    is_archived: bool = False
    sort_order: Int32 = 0


class CounterpartyIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_income_colors: Literal["red_green", "green_red"]
    keypad_layout: Literal["calculator", "phone"]
    week_start: int = Field(ge=0, le=6)
    main_currency: str = Field(pattern=r"^[A-Z0-9]{3,8}$")
    hide_rewards_on_timeline: bool
    abbreviate_totals: bool


class OrderIn(BaseModel):
    ids: list[Int32] = Field(min_length=1)


class EntryWriteOut(EntryDetailOut):
    """Detail of the written entry plus the foreign-transaction fee the client may add (never added silently)."""

    proposed_fee: Decimal | None = None


class TransferOut(BaseModel):
    transfer_group_id: UUID
    out_entry_id: int
    in_entry_id: int


class SplitMemberRefOut(BaseModel):
    id: int
    client_key: str | None


class SplitOut(BaseModel):
    """Every split write (POST /splits, PUT /splits/{id} incl. dissolve, PUT /entries/{id}/split): ids and client
    keys in request order; group_id is null after a dissolve."""

    group_id: int | None
    member_ids: list[int]
    members: list[SplitMemberRefOut]
