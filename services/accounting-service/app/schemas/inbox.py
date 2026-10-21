"""Schemas for the reconciliation inbox (spec 2026-10-21 §4–§5)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .statements import Strict
from .writes import Int32


class InboxLine(BaseModel):
    model_config = Strict
    id: int
    date: date
    merchant: str
    amount: Decimal
    line_kind: str


class InboxEntry(BaseModel):
    model_config = Strict
    id: int
    date: date
    title: str
    amount: Decimal
    account_id: int


class InboxCandidate(BaseModel):
    model_config = Strict
    entry_id: int
    date: date
    title: str
    amount: Decimal
    reason: str | None
    residual: str | None
    date_distance: int | None


class InboxProposal(BaseModel):
    model_config = Strict
    id: int
    action: str
    params: dict
    author: str
    rationale: str


class InboxRuleHint(BaseModel):
    model_config = Strict
    merchant_pattern: str
    line_kind: str
    flow_sign: str
    amount: str
    default_category_id: int | None


class InboxStatementRef(BaseModel):
    model_config = Strict
    id: int
    period_end: date


class InboxItem(BaseModel):
    model_config = Strict
    case_id: int
    case_version: int
    kind: str
    status: str
    question: str
    folded: bool
    group_key: str | None
    statement: InboxStatementRef
    line: InboxLine | None
    entry: InboxEntry | None
    candidates: list[InboxCandidate]
    proposal: InboxProposal | None
    actions: list[str]
    rule_hint: InboxRuleHint | None


class InboxAccount(BaseModel):
    model_config = Strict
    id: int
    name: str
    currency: str
    is_credit: bool
    items: list[InboxItem]


class InboxCounts(BaseModel):
    model_config = Strict
    ask: int
    ask_items: int
    proposed: int
    balance_gap: int
    settlement: int
    other: int


class InboxOut(BaseModel):
    model_config = Strict
    generated_at: datetime
    counts: InboxCounts
    accounts: list[InboxAccount]


# --- POST /reconciliation/inbox/apply (spec §5) ---------------------------------------------------------------------

class InboxCaseRef(BaseModel):
    model_config = Strict
    case_id: Int32
    case_version: Int32


class InboxLearnIn(BaseModel):
    model_config = Strict
    amount_exact: bool = True


class InboxApplyIn(BaseModel):
    model_config = Strict
    cases: list[InboxCaseRef] = Field(min_length=1, max_length=50)
    action: Literal["record", "match_candidate", "accept_bank_amount", "defer", "dismiss", "anchor"]
    params: dict = Field(default_factory=dict)
    answer: str = Field(min_length=1, max_length=80)
    learn: InboxLearnIn | None = None


class InboxApplyResult(BaseModel):
    model_config = Strict
    case_id: int
    status: Literal["applied", "replayed", "refused"]
    action_id: int | None
    code: str | None


class InboxApplyOut(BaseModel):
    model_config = Strict
    results: list[InboxApplyResult]
    rule_id: int | None
    rule_error: str | None
