"""Response schemas for the reconciliation inbox (spec 2026-10-21 §4)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from .statements import Strict


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
