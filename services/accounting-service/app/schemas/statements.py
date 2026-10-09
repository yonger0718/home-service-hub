"""Request/response schemas for statement ingest (reconciliation r1a)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import STATEMENT_SOURCE_ROOTS
from app.models.statements import (
    LINE_KINDS,
    STATEMENT_FILE_FAILURES,
    STATEMENT_FILE_STATUSES,
    STATEMENT_KINDS,
)

from .writes import Currency, Int32, SignedMoney

Strict = ConfigDict(extra="forbid")
LineKind = Literal[LINE_KINDS]  # type: ignore[valid-type]
StatementKind = Literal[STATEMENT_KINDS]  # type: ignore[valid-type]
FileStatus = Literal[STATEMENT_FILE_STATUSES]  # type: ignore[valid-type]
FileFailure = Literal[STATEMENT_FILE_FAILURES]  # type: ignore[valid-type]
SourceRoot = Literal[STATEMENT_SOURCE_ROOTS]  # type: ignore[valid-type]


class Lease(BaseModel):
    model_config = Strict
    run_id: Int32
    lease_token: str = Field(min_length=16, max_length=64)


# ---- runs ----
class RunEnqueueOut(BaseModel):
    model_config = Strict
    run_id: int
    status: str
    coalesced: bool


class RunClaimOut(BaseModel):
    model_config = Strict
    run_id: int
    lease_token: str
    lease_expires_at: datetime
    attempt: int


class RunLeaseIn(BaseModel):
    model_config = Strict
    lease_token: str = Field(min_length=16, max_length=64)


class RunFinishIn(Lease):
    status: Literal["done", "failed"]
    summary: dict = {}


class RunOut(BaseModel):
    model_config = Strict
    id: int
    trigger: str
    principal: str | None
    mode: str
    status: str
    claimed_by: str | None
    attempt: int
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict


# ---- files ----
class FileRegisterIn(Lease):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size: int = Field(ge=0)
    kind: StatementKind
    object_key: str = Field(max_length=128)


class FileUpdateIn(Lease):
    status: FileStatus
    failure: FileFailure | None = None
    has_text_layer: bool | None = None
    text_chars: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=0)
    credential_version: str | None = Field(default=None, max_length=64)
    mapping_version: str | None = Field(default=None, max_length=64)
    parser_version: str | None = Field(default=None, max_length=64)
    next_retry_at: datetime | None = None


class FileOut(BaseModel):
    model_config = Strict
    id: int
    sha256: str
    size: int
    kind: str
    account_id: int | None
    object_key: str | None
    status: str
    failure: str | None
    has_text_layer: bool | None
    text_chars: int | None
    pages: int | None
    attempts: int
    next_retry_at: datetime | None
    first_seen_at: datetime
    parsed_at: datetime | None
    run_id: int | None


# ---- sources ----
class SourceRegisterIn(Lease):
    file_id: Int32
    root: SourceRoot
    drive_file_id: str = Field(max_length=128)
    drive_path: str = Field(max_length=512)
    drive_md5: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    drive_size: int = Field(ge=0)


class SourceOut(BaseModel):
    model_config = Strict
    id: int
    file_id: int
    root: str
    drive_file_id: str
    drive_path: str
    drive_md5: str | None
    drive_size: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None


# ---- revisions ----
class LineIn(BaseModel):
    model_config = Strict
    seq: int = Field(ge=1, le=100000)
    txn_date: date | None = None
    posted_date: date
    merchant_raw: str = Field(max_length=256)
    printed_amount: SignedMoney
    foreign_amount: SignedMoney | None = None
    foreign_currency: Currency | None = None
    line_kind: LineKind
    installment_seq: int | None = Field(default=None, ge=1, le=120)
    installment_total: int | None = Field(default=None, ge=1, le=120)
    is_subtotal: bool = False


class RevisionIn(Lease):
    file_id: Int32 | None = None
    account_id: Int32
    kind: StatementKind
    parser: str = Field(max_length=64)
    parser_version: str = Field(max_length=64)
    currency: Currency
    period_start: date
    period_end: date
    closing_date: date | None = None
    due_date: date | None = None
    opening_balance: SignedMoney | None = None
    statement_total: SignedMoney
    minimum_payment: SignedMoney | None = None
    lines: list[LineIn] = Field(max_length=2000)
    raw: dict


class RevisionOut(BaseModel):
    model_config = Strict
    statement_id: int
    revision_id: int
    revision: int
    guardrail_ok: bool
    mode: str
    conflict: bool
    lineage: dict[str, int]
    case_ids: list[int]


# ---- statements ----
class LineOut(BaseModel):
    model_config = Strict
    id: int
    event_id: int | None
    seq: int
    txn_date: date | None
    posted_date: date
    merchant_raw: str
    merchant_norm: str
    printed_amount: Decimal
    flow_amount: Decimal
    foreign_amount: Decimal | None
    foreign_currency: str | None
    line_kind: str
    installment_seq: int | None
    installment_total: int | None


class CaseOut(BaseModel):
    model_config = Strict
    id: int
    kind: str
    status: str
    event_id: int | None
    line_id: int | None
    entry_id: int | None
    explanation: str | None
    candidates: list
    context: dict
    version: int
    created_at: datetime


class StatementOut(BaseModel):
    model_config = Strict
    id: int
    account_id: int
    kind: str
    currency: str
    period_start: date
    period_end: date
    closing_date: date | None
    due_date: date | None
    opening_balance: Decimal | None
    statement_total: Decimal
    minimum_payment: Decimal | None
    origin: str
    mode: str
    status: str
    conflict_open: bool
    needs_recheck: bool
    current_revision_id: int | None
    matched_count: int
    explained_count: int
    open_case_count: int


class StatementDetailOut(StatementOut):
    lines: list[LineOut]
    cases: list[CaseOut]
    stale_events_pending: bool
    revisions: list[dict] = []
