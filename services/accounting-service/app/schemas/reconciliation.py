"""Request/response schemas for reconciliation actions and their audit trail (R1c)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .statements import ProposalOut, Strict  # noqa: F401  (ProposalOut lives with CaseDetailOut)


class ActionIn(BaseModel):
    model_config = Strict
    action: str = Field(min_length=1, max_length=32)
    params: dict = Field(default_factory=dict)


class DismissIn(BaseModel):
    model_config = Strict
    note: str = Field(min_length=1, max_length=1000)


class ActionLine(BaseModel):
    """The case's current bank line (`base.current_line`), so a 已處理 row can be identified (inbox final review)."""
    date: date
    merchant: str
    amount: Decimal


class ActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    case_id: int
    proposal_id: int | None
    action: str
    params: dict
    actor: str
    status: str
    effect_slot: str
    created_entry_ids: list[int]
    deleted_entry_ids: list[int]
    touched_entry_ids: list[int]
    touched_group_ids: list[int]
    reverted_by_action_id: int | None
    created_at: datetime
    replayed: bool = False
    label: str | None = None
    answer: str | None = None
    line: ActionLine | None = None  # filled by GET /reconciliation/actions only
    # #433: the peer line's case an adjust_transfer / pair_transfer resolved (its stored `inverse`); None otherwise
    peer_case_id: int | None = None


class AutoPayRowOut(BaseModel):
    """One `statement_auto_pay` row (one per statement). `pay_account_id` is null for a `no_link` skip (the table
    stores the card itself there)."""
    statement_id: int
    card_account_id: int
    pay_account_id: int | None
    amount: Decimal
    due_date: date
    pay_date: date
    status: str
    reason: str | None
    updated_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    statement_id: int | None
    case_id: int | None
    action_id: int | None
    run_id: int | None
    event: str
    actor: str
    request_id: str | None
    detail: dict
    created_at: datetime


class ProposalIn(BaseModel):
    model_config = Strict
    action: str = Field(min_length=1, max_length=32)
    params: dict = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=1000)
    confidence: Decimal = Field(ge=0, le=1)


class ProposalRejectIn(BaseModel):
    model_config = Strict
    note: str | None = Field(default=None, max_length=1000)



class BusinessDaysOut(BaseModel):
    """`GET /calendar/business-days?from=&to=`: closed days and make-up workdays within the range (weekends implied)."""

    holidays: list[date]
    workdays: list[date]
    last_known_day: date
