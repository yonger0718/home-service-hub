"""Request/response schemas for statement ingest (reconciliation r1a)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.models import STATEMENT_SOURCE_ROOTS
from app.models.statements import (
    CASE_KINDS,
    CASE_STATUSES,
    INGEST_STATUSES,
    LINE_KINDS,
    STATEMENT_FILE_FAILURES,
    STATEMENT_FILE_STATUSES,
    STATEMENT_KINDS,
)

from .writes import Currency, Int32, SignedMoney

Strict = ConfigDict(extra="forbid")
CaseStatus = Literal[CASE_STATUSES]  # type: ignore[valid-type]
CaseKind = Literal[CASE_KINDS]  # type: ignore[valid-type]
LineKind = Literal[LINE_KINDS]  # type: ignore[valid-type]
StatementKind = Literal[STATEMENT_KINDS]  # type: ignore[valid-type]
FileStatus = Literal[STATEMENT_FILE_STATUSES]  # type: ignore[valid-type]
FileFailure = Literal[STATEMENT_FILE_FAILURES]  # type: ignore[valid-type]
SourceRoot = Literal[STATEMENT_SOURCE_ROOTS]  # type: ignore[valid-type]
RunStatus = Literal[INGEST_STATUSES]  # type: ignore[valid-type]


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


class RunCreateIn(BaseModel):
    """A worker-created run (timer or owner CLI); `enqueue` runs come from POST /statements/ingest/run only."""
    model_config = Strict
    trigger: Literal["timer", "owner_cli"]
    mode: Literal["live", "backfill"] = "live"
    initiator_hint: str | None = Field(default=None, max_length=64)


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
    credential_version: str | None = None
    mapping_version: str | None = None
    parser_version: str | None = None


class CompositeTarget(BaseModel):
    """One section of a composite statement folder: the account it posts to, and whether the section never
    prints transaction rows (子帳戶, foreign-currency accounts) — see the design spec §3."""
    model_config = Strict
    account_id: Int32
    anchor_only: StrictBool = False


class AccountMapOut(BaseModel):
    model_config = Strict
    account_map: dict[str, int]
    composite_map: dict[str, dict[str, CompositeTarget]] = {}
    mapping_version: str
    closing_days: dict[str, int] = {}  # account id -> closing day of the cards that have one (the parser's hint)
    card_accounts: list[int] = []  # ids of the credit accounts: a mapped file's `kind` follows its account (#205)


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


class SourcesMarkRemovedIn(Lease):
    """Sent only after a COMPLETE Drive listing; an empty list would mark every source removed, so it needs
    `allow_empty`."""
    seen_drive_file_ids: list[str] = Field(max_length=100000)
    allow_empty: bool = False


class SourcesMarkRemovedOut(BaseModel):
    model_config = Strict
    removed: int


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


def unique_line_seq(lines: list[LineIn]) -> list[LineIn]:
    """Stored (non-subtotal) lines are unique by seq within a revision; refuse duplicates as 422, not a 500."""
    seqs = [line.seq for line in lines if not line.is_subtotal]
    if len(seqs) != len(set(seqs)):
        raise ValueError("duplicate seq")
    return lines


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

    _unique_seq = field_validator("lines")(unique_line_seq)


class ManualStatementIn(BaseModel):
    """An owner-entered statement (`POST /accounts/{account_id}/statements`): the header and lines of a `RevisionIn`
    without the worker's run, lease, file and parser fields (the service builds the `RevisionIn`: `file_id=None`,
    `parser="manual"`, `parser_version="1"`, `raw={}`, its own owner_cli run). Same field rules as `RevisionIn`."""
    model_config = Strict
    kind: StatementKind
    currency: Currency
    period_start: date
    period_end: date
    closing_date: date | None = None
    due_date: date | None = None
    opening_balance: SignedMoney | None = None
    statement_total: SignedMoney
    minimum_payment: SignedMoney | None = None
    lines: list[LineIn] = Field(default=[], max_length=2000)

    _unique_seq = field_validator("lines")(unique_line_seq)


class ConfirmIn(BaseModel):
    """`force` confirms despite failing checks and then needs a `note` (422 `note_required`)."""
    model_config = Strict
    force: bool = False
    note: str | None = Field(default=None, max_length=1000)


class RetireIn(BaseModel):
    """Why the statement is retired (recorded on it and in the audit row); a blank note is refused."""
    model_config = Strict
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must not be blank")
        return value


class RetireOut(BaseModel):
    model_config = Strict
    statement_id: int
    mode: str
    already_retired: bool
    released_coverage: int
    retired_events: int
    superseded_cases: int


class ConfirmOut(BaseModel):
    model_config = Strict
    status: str
    checks: dict[str, bool]
    reopened_cases: list[int]
    forced: bool


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
    replayed: bool = False


# ---- statements ----
class CoverageOut(BaseModel):
    """One active coverage row of a line: the ledger entry (or group) it is matched to. `entry_id`/`group_id` go null
    when the ledger row was deleted (the claim is then stale until the next sweep)."""
    model_config = Strict
    entry_id: int | None
    group_id: int | None
    role: str
    flow: Decimal | None
    match_rule: str
    match_kind: str
    status: str


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
    matched: bool
    coverage: list[CoverageOut]
    event_flag: str | None = None  # the line's event flag: `text_changed` after a normalised re-parse (§5.7)
    event_status: str | None = None  # live | retired | quarantined (None when the line has no event)


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


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    case_id: int
    case_version: int
    event_id: int | None
    action: str
    params: dict
    rationale: str
    confidence: Decimal
    author: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    applied_action_id: int | None


class CaseDetailOut(CaseOut):
    statement_id: int
    revision_id: int
    resolved_by: str | None
    resolved_action_id: int | None
    resolved_at: datetime | None
    deferred_to_period_end: date | None
    deferred_to_statement_id: int | None
    note: str | None
    actions_allowed: list[str]
    pending_proposals: list[ProposalOut] = []
    line: LineOut | None
    owner_twins: list[int] = []  # open line_unmatched: owner entries that may record the line (the policy never books it)


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
    incoming_deferrals_pending: int
    retired: bool  # retired as a duplicate (statement_retire_service); such a statement is also historical


class StatementDetailOut(StatementOut):
    lines: list[LineOut]
    cases: list[CaseOut]
    stale_events_pending: bool
    revisions: list[dict] = []


# ---- settings ----
class ReconciliationSettingsIn(BaseModel):
    """`account_map`: Drive folder `<root>/<folder>/<subfolder>` -> account id (the service checks the key shape).
    `dirty_enabled`: the ledger dirty triggers write coverage_dirty only while it is true (a PUT replaces it).
    `composite_map`: manual folder → section key `<suffix>/<CUR>` → target; a PUT replaces it.
    A PUT replaces `rules`, `account_map` and `dirty_enabled` wholesale: an omitted field reverts to its default."""
    model_config = Strict
    account_map: dict[str, Int32] = {}
    composite_map: dict[str, dict[str, CompositeTarget]] = {}
    dirty_enabled: bool = False
    rules: dict = {}  # matching.Rules overrides (settings_service validates keys and types); {} = the defaults
    policy: dict = {}  # agent-autonomy policy overrides (settings_service validates); {} = the defaults
    auto_pay: dict = {}  # card-statement auto-pay settings (settings_service validates); {} = the defaults
    settlement: dict = {}  # brokerage settlement settings (portfolio integration §8; settings_service validates)
    account_siblings: dict[str, list[Int32]] = {}  # account id -> sibling account ids (same currency)


class ReconciliationSettingsOut(BaseModel):
    model_config = Strict
    account_map: dict[str, int]
    composite_map: dict[str, dict[str, CompositeTarget]]
    dirty_enabled: bool
    rules: dict
    rules_version: str
    policy: dict
    policy_version: str
    auto_pay: dict
    settlement: dict
    account_siblings: dict[str, list[int]]
    version: int


class PolicyCountsOut(BaseModel):
    model_config = Strict
    applied: int
    proposed: int


class ReconcileOut(BaseModel):
    model_config = Strict
    claims: int
    cases_opened: list[int]
    explained: int
    unmatched_entries: int
    skipped: str | None
    policy: PolicyCountsOut  # the policy pass run after the matching commit (R1c Task 10; zeros while it is off)
    # portfolio integration spec §4.3, §9: the settlement feed could not be read completely; `settlements` counts
    # booked / ambiguous / pending / mismatch / drift / claimed / unknown_dates and names the `unavailable_code`
    # (empty for a statement without a settlement step)
    settlement_source_unavailable: bool
    settlements: dict


class SweepError(BaseModel):
    model_config = Strict
    statement_id: int
    error: str


class SettlementRunIn(BaseModel):
    model_config = Strict
    source: Literal["portfolio:cathay"]


class SettlementRunOut(BaseModel):
    """Counts and codes only: never an amount, a merchant or a ledger id."""
    model_config = Strict
    status: str
    booked: int
    skipped: int
    housekeeping: dict[str, int]
    flagged: int
    reconciled: int  # flagged statements reconciled in the call (their flag cleared)
    busy: int  # of `errors`, those that yielded `reconcile_busy`: they keep the flag for the sweep
    errors: list[SweepError]  # statement id + code per statement the call could not reconcile


class StandInsOut(BaseModel):
    """The stand-in job's outcome as the sweep reports it: a status code and counts."""
    model_config = Strict
    status: str
    booked: int
    skipped: int
    flagged: int


class SweepOut(BaseModel):
    model_config = Strict
    statements: int
    claims: int
    cases_opened: int
    errors: list[SweepError]
    links_filled: int
    settlement_source_unavailable: int  # statements whose settlement feed could not be read (spec §9)
    stand_ins: StandInsOut
