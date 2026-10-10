"""Reconciliation schema (design §4). Triggers append to coverage_dirty and never touch their source tables."""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DDL, Date, DateTime, Enum, ForeignKey, ForeignKeyConstraint, Index,
    Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.database import Base
from app.models.ledger import STATEMENT_SOURCE_ROOTS

STATEMENT_KINDS = ("card", "bank")
STATEMENT_FILE_STATUSES = ("new", "unlocked", "parsed", "needs_review", "failed", "ignored")
STATEMENT_FILE_FAILURES = ("password", "no_text_layer", "too_large", "parse", "guardrail", "mapping", "transient", "sandbox")
INGEST_TRIGGERS = ("timer", "owner_cli", "enqueue")
INGEST_MODES = ("live", "backfill")
INGEST_STATUSES = ("queued", "claimed", "running", "done", "failed", "expired")
STATEMENT_MODES = ("historical", "live")
STATEMENT_STATUSES = ("open", "reconciled")
STATEMENT_ORIGINS = ("manual", "import")
LINE_KINDS = ("purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
              "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown")
EVENT_STATUSES = ("live", "retired", "quarantined")
EQUIVALENCES = ("identical", "normalised", "changed", "unpaired")
COVERAGE_ROLES = ("principal", "child", "member")
COVERAGE_STATUSES = ("active", "stale")
MATCH_KINDS = ("auto", "manual", "agent")
CASE_KINDS = ("line_unmatched", "entry_unmatched", "amount_delta", "ambiguous", "duplicate_claim", "balance_gap",
              "statement_conflict", "parse_review", "recheck")
CASE_STATUSES = ("open", "proposed", "resolved", "dismissed", "superseded")
CASE_EXPLANATIONS = ("deferred_next_period", "accepted_exception")
RECONCILIATION_ACTORS = ("owner", "policy")
PROPOSAL_STATUSES = ("pending", "applied", "rejected", "superseded")
ACTION_STATUSES = ("applied", "reverted", "failed")
DIRTY_KINDS = ("entry", "group", "account")
DIRTY_OPS = ("insert", "update", "delete")

statement_kind_enum = Enum(*STATEMENT_KINDS, name="statement_kind")
statement_file_status_enum = Enum(*STATEMENT_FILE_STATUSES, name="statement_file_status")
statement_file_failure_enum = Enum(*STATEMENT_FILE_FAILURES, name="statement_file_failure")
statement_source_root_enum = Enum(*STATEMENT_SOURCE_ROOTS, name="statement_source_root")
ingest_trigger_enum = Enum(*INGEST_TRIGGERS, name="ingest_trigger")
ingest_mode_enum = Enum(*INGEST_MODES, name="ingest_mode")
ingest_status_enum = Enum(*INGEST_STATUSES, name="ingest_status")
statement_mode_enum = Enum(*STATEMENT_MODES, name="statement_mode")
statement_status_enum = Enum(*STATEMENT_STATUSES, name="statement_status")
statement_origin_enum = Enum(*STATEMENT_ORIGINS, name="statement_origin")
line_kind_enum = Enum(*LINE_KINDS, name="statement_line_kind")
event_status_enum = Enum(*EVENT_STATUSES, name="statement_event_status")
equivalence_enum = Enum(*EQUIVALENCES, name="lineage_equivalence")
coverage_role_enum = Enum(*COVERAGE_ROLES, name="coverage_role")
coverage_status_enum = Enum(*COVERAGE_STATUSES, name="coverage_status")
match_kind_enum = Enum(*MATCH_KINDS, name="match_kind")
case_kind_enum = Enum(*CASE_KINDS, name="case_kind")
case_status_enum = Enum(*CASE_STATUSES, name="case_status")
case_explanation_enum = Enum(*CASE_EXPLANATIONS, name="case_explanation")
reconciliation_actor_enum = Enum(*RECONCILIATION_ACTORS, name="reconciliation_actor")
proposal_status_enum = Enum(*PROPOSAL_STATUSES, name="proposal_status")
action_status_enum = Enum(*ACTION_STATUSES, name="action_status")
dirty_kind_enum = Enum(*DIRTY_KINDS, name="dirty_kind")
dirty_op_enum = Enum(*DIRTY_OPS, name="dirty_op")

MONEY = Numeric(20, 4)
NOW = func.now()


class IngestRun(Base):
    __tablename__ = "ingest_run"
    __table_args__ = (Index("ix_ingest_run_status", "status"),)
    id = Column(Integer, primary_key=True)
    trigger = Column(ingest_trigger_enum, nullable=False)
    principal = Column(String(64), nullable=False)
    initiator_hint = Column(String(64))
    mode = Column(ingest_mode_enum, nullable=False, server_default=text("'live'"))
    status = Column(ingest_status_enum, nullable=False, server_default=text("'queued'"))
    claimed_by = Column(String(64))
    lease_token = Column(String(64))
    lease_expires_at = Column(DateTime(timezone=True))
    attempt = Column(SmallInteger, nullable=False, server_default=text("0"))
    parser_version = Column(String(64))
    rules_version = Column(String(64))
    policy_config_sha256 = Column(String(64))
    mapping_version = Column(String(64))
    credential_version = Column(String(64))
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class StatementFile(Base):
    __tablename__ = "statement_file"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_statement_file_sha256"),
        Index("ix_statement_file_status", "status"),
    )
    id = Column(Integer, primary_key=True)
    sha256 = Column(String(64), nullable=False)
    size = Column(BigInteger, nullable=False)
    kind = Column(statement_kind_enum, nullable=False)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"))
    object_key = Column(String(128), nullable=False)
    status = Column(statement_file_status_enum, nullable=False, server_default=text("'new'"))
    failure = Column(statement_file_failure_enum)
    has_text_layer = Column(Boolean)
    text_chars = Column(Integer)
    pages = Column(SmallInteger)
    credential_version = Column(String(64))
    mapping_version = Column(String(64))
    parser_version = Column(String(64))
    attempts = Column(SmallInteger, nullable=False, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True))
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    parsed_at = Column(DateTime(timezone=True))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))


class StatementSource(Base):
    __tablename__ = "statement_source"
    __table_args__ = (
        UniqueConstraint("drive_file_id", "drive_md5", name="uq_statement_source_drive_file_md5"),
        Index("ix_statement_source_file_id", "file_id"),
    )
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey("statement_file.id", ondelete="RESTRICT"), nullable=False)
    root = Column(statement_source_root_enum, nullable=False)
    drive_file_id = Column(String(128), nullable=False)
    drive_path = Column(String(512), nullable=False)
    drive_md5 = Column(String(32), nullable=False)
    drive_size = Column(BigInteger, nullable=False)
    path_history = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    removed_at = Column(DateTime(timezone=True))
    superseded_by_source_id = Column(Integer, ForeignKey("statement_source.id", ondelete="SET NULL"))


class AccountStatement(Base):
    __tablename__ = "account_statement"
    __table_args__ = (
        UniqueConstraint("account_id", "currency", "period_end", name="uq_account_statement_identity"),
        CheckConstraint("period_start <= period_end", name="ck_account_statement_period"),
        Index("ix_account_statement_account_id", "account_id"),
        # Circular with statement_revision: use_alter adds it after both tables exist.
        ForeignKeyConstraint(["current_revision_id"], ["statement_revision.id"],
                             name="fk_account_statement_current_revision", use_alter=True, ondelete="RESTRICT"),
    )
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(statement_kind_enum, nullable=False)
    currency = Column(String(8), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    closing_date = Column(Date)
    due_date = Column(Date)
    opening_balance = Column(MONEY)
    statement_total = Column(MONEY, nullable=False)
    minimum_payment = Column(MONEY)
    origin = Column(statement_origin_enum, nullable=False)
    mode = Column(statement_mode_enum, nullable=False)
    status = Column(statement_status_enum, nullable=False, server_default=text("'open'"))
    conflict_open = Column(Boolean, nullable=False, server_default=text("false"))
    needs_recheck = Column(Boolean, nullable=False, server_default=text("false"))
    current_revision_id = Column(Integer)  # FK in __table_args__ (circular)
    swept_through_event_id = Column(BigInteger, nullable=False, server_default=text("0"))
    matched_count = Column(Integer, nullable=False, server_default=text("0"))
    explained_count = Column(Integer, nullable=False, server_default=text("0"))
    open_case_count = Column(Integer, nullable=False, server_default=text("0"))
    note = Column(Text)
    created_run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=NOW)


class StatementRevision(Base):
    __tablename__ = "statement_revision"
    __table_args__ = (UniqueConstraint("statement_id", "revision", name="uq_statement_revision_number"),)
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    revision = Column(Integer, nullable=False)
    file_id = Column(Integer, ForeignKey("statement_file.id", ondelete="RESTRICT"))
    parser = Column(String(64), nullable=False)
    parser_version = Column(String(64), nullable=False)
    raw = Column(JSONB, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    closing_date = Column(Date)
    due_date = Column(Date)
    opening_balance = Column(MONEY)
    statement_total = Column(MONEY, nullable=False)
    minimum_payment = Column(MONEY)
    guardrail = Column(JSONB, nullable=False)
    guardrail_ok = Column(Boolean, nullable=False)
    conflict = Column(Boolean, nullable=False, server_default=text("false"))
    rejected = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))


class StatementEvent(Base):
    __tablename__ = "statement_event"
    __table_args__ = (
        Index("ix_statement_event_statement_id", "statement_id"),
        # Circular with statement_line: use_alter adds them after both tables exist.
        ForeignKeyConstraint(["first_line_id"], ["statement_line.id"], name="fk_statement_event_first_line",
                             use_alter=True, ondelete="RESTRICT"),
        ForeignKeyConstraint(["current_line_id"], ["statement_line.id"], name="fk_statement_event_current_line",
                             use_alter=True, ondelete="SET NULL"),
    )
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    first_revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    first_line_id = Column(Integer)  # FKs in __table_args__ (circular with statement_line)
    current_line_id = Column(Integer)
    status = Column(event_status_enum, nullable=False, server_default=text("'live'"))
    flag = Column(String(32), nullable=True)  # 'text_changed' after a normalised re-parse (§5.7)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class StatementLine(Base):
    __tablename__ = "statement_line"
    __table_args__ = (
        UniqueConstraint("revision_id", "seq", name="uq_statement_line_revision_seq"),
        Index("ix_statement_line_event_id", "event_id"),
        Index("ix_statement_line_revision_logical", "revision_id", "logical_key"),
    )
    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    seq = Column(Integer, nullable=False)
    canonical_key = Column(String(64), nullable=False)
    logical_key = Column(String(80), nullable=False)
    txn_date = Column(Date)
    posted_date = Column(Date, nullable=False)
    merchant_raw = Column(String(256), nullable=False)
    merchant_norm = Column(String(256), nullable=False)
    printed_amount = Column(MONEY, nullable=False)
    flow_amount = Column(MONEY, nullable=False)
    foreign_amount = Column(MONEY)
    foreign_currency = Column(String(8))
    line_kind = Column(line_kind_enum, nullable=False)
    installment_seq = Column(SmallInteger)
    installment_total = Column(SmallInteger)


class LineLineage(Base):
    __tablename__ = "line_lineage"
    __table_args__ = (Index("ix_line_lineage_event_id", "event_id"),)
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    old_line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    new_line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    equivalence = Column(equivalence_enum, nullable=False)
    transferred = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class StatementCoverage(Base):
    __tablename__ = "statement_coverage"
    __table_args__ = (
        Index("ux_statement_coverage_active_entry", "entry_id", unique=True,
              postgresql_where=text("status = 'active' AND entry_id IS NOT NULL")),
        Index("ix_statement_coverage_statement_id", "statement_id"),
        Index("ix_statement_coverage_event_id", "event_id"),
    )
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"), nullable=False)
    line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"), nullable=False)
    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"))
    group_id = Column(Integer, ForeignKey("entry_group.id", ondelete="SET NULL"))
    role = Column(coverage_role_enum, nullable=False)
    snapshot = Column(JSONB, nullable=False)
    match_kind = Column(match_kind_enum, nullable=False)
    match_rule = Column(String(32), nullable=False)
    status = Column(coverage_status_enum, nullable=False, server_default=text("'active'"))
    stale_reason = Column(String(64))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class CoverageDirty(Base):
    __tablename__ = "coverage_dirty"
    __table_args__ = (
        Index("ix_coverage_dirty_old_account", "old_account_id", "id"),
        Index("ix_coverage_dirty_new_account", "new_account_id", "id"),
    )
    id = Column(BigInteger, primary_key=True)
    kind = Column(dirty_kind_enum, nullable=False)
    row_id = Column(Integer, nullable=False)
    op = Column(dirty_op_enum, nullable=False)
    old_account_id = Column(Integer)
    new_account_id = Column(Integer)
    old_date = Column(Date)
    new_date = Column(Date)
    old_row = Column(JSONB)
    new_row = Column(JSONB)
    action_id = Column(Integer)
    happened_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationCase(Base):
    __tablename__ = "reconciliation_case"
    __table_args__ = (
        Index("ix_reconciliation_case_statement_status", "statement_id", "status"),
        Index("ix_reconciliation_case_event_id", "event_id"),
    )
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"), nullable=False)
    revision_id = Column(Integer, ForeignKey("statement_revision.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(case_kind_enum, nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    line_id = Column(Integer, ForeignKey("statement_line.id", ondelete="RESTRICT"))
    entry_id = Column(Integer, ForeignKey("ledger_entry.id", ondelete="SET NULL"))
    candidates = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    context = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status = Column(case_status_enum, nullable=False, server_default=text("'open'"))
    explanation = Column(case_explanation_enum)
    deferred_to_period_end = Column(Date)
    deferred_to_statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="SET NULL"))
    resolved_by = Column(reconciliation_actor_enum)
    resolved_action_id = Column(Integer)
    version = Column(Integer, nullable=False, server_default=text("1"))
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    resolved_at = Column(DateTime(timezone=True))


class ReconciliationProposal(Base):
    __tablename__ = "reconciliation_proposal"
    __table_args__ = (Index("ix_reconciliation_proposal_case_status", "case_id", "status"),)
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"), nullable=False)
    case_version = Column(Integer, nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    action = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False)
    rationale = Column(String(1000), nullable=False)
    confidence = Column(Numeric(4, 3), nullable=False)
    author = Column(String(64), nullable=False)
    status = Column(proposal_status_enum, nullable=False, server_default=text("'pending'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)
    decided_at = Column(DateTime(timezone=True))
    applied_action_id = Column(Integer)


class ReconciliationAction(Base):
    __tablename__ = "reconciliation_action"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reconciliation_action_idempotency"),
        Index("ux_reconciliation_action_effect", "event_id", "action", "effect_slot", unique=True,
              postgresql_where=text("status = 'applied'")),
        Index("ix_reconciliation_action_case_id", "case_id"),
    )
    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    event_id = Column(Integer, ForeignKey("statement_event.id", ondelete="RESTRICT"))
    effect_slot = Column(String(16), nullable=False)
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"), nullable=False)
    proposal_id = Column(Integer, ForeignKey("reconciliation_proposal.id", ondelete="RESTRICT"))
    action = Column(String(32), nullable=False)
    params = Column(JSONB, nullable=False)
    actor = Column(reconciliation_actor_enum, nullable=False)
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    status = Column(action_status_enum, nullable=False)
    created_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    deleted_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    touched_entry_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    touched_group_ids = Column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    before = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    after = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    inverse = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reverted_by_action_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationAudit(Base):
    __tablename__ = "reconciliation_audit"
    __table_args__ = (Index("ix_reconciliation_audit_statement_id", "statement_id", "id"),)
    id = Column(Integer, primary_key=True)
    statement_id = Column(Integer, ForeignKey("account_statement.id", ondelete="RESTRICT"))
    case_id = Column(Integer, ForeignKey("reconciliation_case.id", ondelete="RESTRICT"))
    action_id = Column(Integer, ForeignKey("reconciliation_action.id", ondelete="RESTRICT"))
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="RESTRICT"))
    event = Column(String(48), nullable=False)
    actor = Column(String(64), nullable=False)
    request_id = Column(String(64))
    detail = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class PolicyBudget(Base):
    __tablename__ = "policy_budget"
    month = Column(Date, primary_key=True)
    currency = Column(String(8), primary_key=True)
    used_amount = Column(MONEY, nullable=False, server_default=text("0"))
    used_count = Column(Integer, nullable=False, server_default=text("0"))


class InstallmentPlanMap(Base):
    __tablename__ = "installment_plan_map"
    __table_args__ = (UniqueConstraint("account_id", "plan_key", name="uq_installment_plan_map_account_key"),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    plan_key = Column(String(300), nullable=False)
    definition_id = Column(Integer, ForeignKey("schedule_definition.id", ondelete="RESTRICT"), nullable=False)
    confirmed_by = Column(reconciliation_actor_enum, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ReconciliationSettings(Base):
    __tablename__ = "reconciliation_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_reconciliation_settings_single_row"),)
    id = Column(Integer, primary_key=True, autoincrement=False, server_default=text("1"))
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    version = Column(Integer, nullable=False, server_default=text("1"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=NOW)


# --- dirty triggers (design §4.6) -------------------------------------------------------------------
# Writer barrier (§4.6 erratum): every trigger that appends an event first takes this advisory key SHARED for the
# rest of its transaction; the sweep takes it EXCLUSIVE (session level, briefly) to read max(id), so every id at or
# below that cap is committed — bigserial ids are assigned at insert, not at commit.
DIRTY_BARRIER_KEY = 0x44495254  # 'DIRT'
_BARRIER = f"PERFORM pg_advisory_xact_lock_shared({DIRTY_BARRIER_KEY});"
DIRTY_ENTRY_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_entry() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    {_BARRIER}
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) - 'updated_at') = (to_jsonb(NEW) - 'updated_at') THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_account_id, new_account_id, old_date, new_date, old_row, new_row, action_id)
    VALUES (
        'entry',
        COALESCE(NEW.id, OLD.id),
        lower(TG_OP)::dirty_op,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.account_id END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.account_id END,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.posted_date END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.posted_date END,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
        v_action
    );
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_ENTRY_TRIGGER_SQL = """
CREATE TRIGGER trg_ledger_entry_reconciliation_dirty
AFTER INSERT OR UPDATE OR DELETE ON ledger_entry
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_entry()
"""
DIRTY_GROUP_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_group() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    {_BARRIER}
    IF TG_OP = 'UPDATE' AND to_jsonb(OLD) = to_jsonb(NEW) THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_row, new_row, action_id)
    VALUES ('group', COALESCE(NEW.id, OLD.id), lower(TG_OP)::dirty_op,
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END, v_action);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_GROUP_TRIGGER_SQL = """
CREATE TRIGGER trg_entry_group_reconciliation_dirty
AFTER INSERT OR UPDATE OR DELETE ON entry_group
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_group()
"""
DIRTY_ACCOUNT_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_account() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    {_BARRIER}
    IF OLD.opening_balance IS NOT DISTINCT FROM NEW.opening_balance
       AND OLD.currency IS NOT DISTINCT FROM NEW.currency
       AND OLD.combined_account_id IS NOT DISTINCT FROM NEW.combined_account_id
       AND OLD.closing_day IS NOT DISTINCT FROM NEW.closing_day
       AND OLD.due_rule IS NOT DISTINCT FROM NEW.due_rule
       AND OLD.due_value IS NOT DISTINCT FROM NEW.due_value
       AND OLD.is_credit IS NOT DISTINCT FROM NEW.is_credit
       AND OLD.is_archived IS NOT DISTINCT FROM NEW.is_archived THEN
        RETURN NULL;
    END IF;
    INSERT INTO coverage_dirty (kind, row_id, op, old_account_id, new_account_id, old_row, new_row, action_id)
    VALUES ('account', NEW.id, 'update', OLD.id, NEW.id, to_jsonb(OLD), to_jsonb(NEW), v_action);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
DIRTY_ACCOUNT_TRIGGER_SQL = """
CREATE TRIGGER trg_account_reconciliation_dirty
AFTER UPDATE ON account
FOR EACH ROW EXECUTE FUNCTION reconciliation_dirty_account()
"""


# Each trigger hangs off the table it fires on, as ledger.py does for the posted-date trigger: create_all may
# create coverage_dirty before those tables, and plpgsql resolves coverage_dirty only when the trigger runs.
from app.models.ledger import Account, EntryGroup, LedgerEntry  # noqa: E402

event.listen(LedgerEntry.__table__, "after_create", DDL(DIRTY_ENTRY_FUNCTION_SQL))
event.listen(LedgerEntry.__table__, "after_create", DDL(DIRTY_ENTRY_TRIGGER_SQL))
event.listen(EntryGroup.__table__, "after_create", DDL(DIRTY_GROUP_FUNCTION_SQL))
event.listen(EntryGroup.__table__, "after_create", DDL(DIRTY_GROUP_TRIGGER_SQL))
event.listen(Account.__table__, "after_create", DDL(DIRTY_ACCOUNT_FUNCTION_SQL))
event.listen(Account.__table__, "after_create", DDL(DIRTY_ACCOUNT_TRIGGER_SQL))
