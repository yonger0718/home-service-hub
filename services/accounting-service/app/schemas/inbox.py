"""Schemas for the reconciliation inbox (spec 2026-10-21 §4–§5)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    group_id: int | None = None  # set for a split member (2026-10-24 spec §5.3: the sheet shows 拆帳成員)


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


class InboxTransferAdjust(BaseModel):
    model_config = Strict
    entry_id: int | None  # None on an ambiguous case: the owner picks one of `candidate_entry_ids`
    line_id: int
    candidate_entry_ids: list[int]


class InboxPeerCandidate(BaseModel):
    model_config = Strict
    line_id: int
    statement_id: int
    account_name: str
    posted_date: date
    amount: Decimal


class InboxPairTransfer(BaseModel):
    model_config = Strict
    peer_candidates: list[InboxPeerCandidate]


class InboxTransfer(BaseModel):
    model_config = Strict
    adjust_transfer: InboxTransferAdjust | None = None
    pair_transfer: InboxPairTransfer | None = None


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
    transfer: InboxTransfer | None = None


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


# --- owner-taught rules (spec §6.4) ---------------------------------------------------------------------------------

class OwnerRuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    account_id: int
    enabled: bool
    line_kind: str
    merchant_pattern: str
    flow_sign: str
    amount: Decimal | None
    action: str
    params: dict
    created_from_case_id: int | None
    created_at: datetime
    last_applied_at: datetime | None
    applied_count: int
    note: str | None


class OwnerRulePatchIn(BaseModel):
    model_config = Strict
    enabled: bool
