"""statement reconciliation tables, dirty triggers and entry_source 'statement'

Revision ID: d1f3a7c2e9b4
Revises: c4e8b2f1a7d3
Create Date: 2026-10-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d1f3a7c2e9b4"
down_revision: Union[str, Sequence[str], None] = "c4e8b2f1a7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same names and values as app.models.statements (migrations do not import app code).
NEW_ENUMS = {
    "statement_kind": ("card", "bank"),
    "statement_file_status": ("new", "unlocked", "parsed", "needs_review", "failed", "ignored"),
    "statement_file_failure": (
        "password", "no_text_layer", "too_large", "parse", "guardrail", "mapping", "transient", "sandbox",
    ),
    "statement_source_root": ("mail", "manual"),
    "ingest_trigger": ("timer", "owner_cli", "enqueue"),
    "ingest_mode": ("live", "backfill"),
    "ingest_status": ("queued", "claimed", "running", "done", "failed", "expired"),
    "statement_mode": ("historical", "live"),
    "statement_status": ("open", "reconciled"),
    "statement_origin": ("manual", "import"),
    "statement_line_kind": (
        "purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
        "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown",
    ),
    "statement_event_status": ("live", "retired", "quarantined"),
    "lineage_equivalence": ("identical", "normalised", "changed", "unpaired"),
    "coverage_role": ("principal", "child", "member"),
    "coverage_status": ("active", "stale"),
    "match_kind": ("auto", "manual", "agent"),
    "case_kind": (
        "line_unmatched", "entry_unmatched", "amount_delta", "ambiguous", "duplicate_claim", "balance_gap",
        "statement_conflict", "parse_review", "recheck",
    ),
    "case_status": ("open", "proposed", "resolved", "dismissed", "superseded"),
    "case_explanation": ("deferred_next_period", "accepted_exception"),
    "reconciliation_actor": ("owner", "policy"),
    "proposal_status": ("pending", "applied", "rejected", "superseded"),
    "action_status": ("applied", "reverted", "failed"),
    "dirty_kind": ("entry", "group", "account"),
    "dirty_op": ("insert", "update", "delete"),
}
MONEY = sa.Numeric(20, 4)
JSONB = postgresql.JSONB
EMPTY_JSON_OBJECT = sa.text("'{}'::jsonb")
EMPTY_JSON_ARRAY = sa.text("'[]'::jsonb")
EMPTY_INT_ARRAY = sa.text("'{}'::integer[]")
FALSE = sa.text("false")
ZERO = sa.text("0")
ONE = sa.text("1")

# Trigger SQL copied verbatim from app/models/statements.py (migrations do not import app code).
DIRTY_ENTRY_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_entry() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
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
DIRTY_GROUP_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_group() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
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
DIRTY_ACCOUNT_FUNCTION_SQL = """
CREATE FUNCTION reconciliation_dirty_account() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
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

# Each count blocks the downgrade: the previous revision cannot hold what reconciliation recorded. Every dropped
# table is guarded except coverage_dirty (derived from the ledger by the triggers); reconciliation_settings only
# once it has been saved: every PUT bumps `version`, so the rule is `version <> 1` alone (a row still at version 1,
# whatever its data, e.g. the default `{"dirty_enabled": false}`, was never saved by the owner).
DOWNGRADE_GUARDS = {
    "statement-created entries": "SELECT count(*) FROM ledger_entry WHERE source = 'statement'",
    "coverage rows": "SELECT count(*) FROM statement_coverage",
    "reconciliation actions": "SELECT count(*) FROM reconciliation_action",
    "audit rows": "SELECT count(*) FROM reconciliation_audit",
    "ingest runs": "SELECT count(*) FROM ingest_run",
    "reconciliation proposals": "SELECT count(*) FROM reconciliation_proposal",
    "reconciliation cases": "SELECT count(*) FROM reconciliation_case",
    "line lineage rows": "SELECT count(*) FROM line_lineage",
    "statement lines": "SELECT count(*) FROM statement_line",
    "statement events": "SELECT count(*) FROM statement_event",
    "statement revisions": "SELECT count(*) FROM statement_revision",
    "statements": "SELECT count(*) FROM account_statement",
    "statement sources": "SELECT count(*) FROM statement_source",
    "statement files": "SELECT count(*) FROM statement_file",
    "policy budget rows": "SELECT count(*) FROM policy_budget",
    "installment plan mappings": "SELECT count(*) FROM installment_plan_map",
    "settings changed": "SELECT count(*) FROM reconciliation_settings WHERE version <> 1",
    "accounts with statement settings": (
        "SELECT count(*) FROM account WHERE statement_password_rule IS NOT NULL OR statement_live_from IS NOT NULL "
        "OR statement_source_root IS NOT NULL"
    ),
}
# Children before parents; the three circular FKs are dropped before any table.
TABLES_IN_DROP_ORDER = (
    "reconciliation_audit", "reconciliation_action", "reconciliation_proposal", "reconciliation_case",
    "statement_coverage", "line_lineage", "statement_line", "statement_event", "statement_revision",
    "account_statement", "statement_source", "statement_file", "ingest_run", "coverage_dirty", "policy_budget",
    "installment_plan_map", "reconciliation_settings",
)
# The tables carrying the triggers (and the account columns) are locked too: the guard reads ledger_entry.
LOCKED_TABLES = ("ledger_entry", "entry_group", "account", *TABLES_IN_DROP_ORDER)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*NEW_ENUMS[name], name=name, create_type=False)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _tstz(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _fk(column: str, target: str, ondelete: str = "RESTRICT", nullable: bool = True) -> sa.Column:
    return sa.Column(column, sa.Integer(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _str(name: str, length: int, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(length), nullable=nullable)


def upgrade() -> None:
    # The new entry_source value in its own committed step (ALTER TYPE ... ADD VALUE cannot be used in the same
    # transaction).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'statement'")

    connection = op.get_bind()
    for name, values in NEW_ENUMS.items():
        postgresql.ENUM(*values, name=name).create(connection)

    op.add_column("account", _str("statement_password_rule", 64))
    op.add_column("account", sa.Column("statement_live_from", sa.Date(), nullable=True))
    op.add_column("account", sa.Column("statement_source_root", _enum("statement_source_root"), nullable=True))

    op.create_table(
        "ingest_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", _enum("ingest_trigger"), nullable=False),
        _str("principal", 64, nullable=False),
        _str("initiator_hint", 64),
        sa.Column("mode", _enum("ingest_mode"), nullable=False, server_default=sa.text("'live'")),
        sa.Column("status", _enum("ingest_status"), nullable=False, server_default=sa.text("'queued'")),
        _str("claimed_by", 64),
        _str("lease_token", 64),
        _tstz("lease_expires_at"),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default=ZERO),
        _str("parser_version", 64),
        _str("rules_version", 64),
        _str("policy_config_sha256", 64),
        _str("mapping_version", 64),
        _str("credential_version", 64),
        _ts("requested_at"),
        _tstz("started_at"),
        _tstz("finished_at"),
        sa.Column("summary", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
    )
    op.create_index("ix_ingest_run_status", "ingest_run", ["status"])

    op.create_table(
        "statement_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        _str("sha256", 64, nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("kind", _enum("statement_kind"), nullable=False),
        _fk("account_id", "account.id"),
        _str("object_key", 128, nullable=False),
        sa.Column("status", _enum("statement_file_status"), nullable=False, server_default=sa.text("'new'")),
        sa.Column("failure", _enum("statement_file_failure"), nullable=True),
        sa.Column("has_text_layer", sa.Boolean(), nullable=True),
        sa.Column("text_chars", sa.Integer(), nullable=True),
        sa.Column("pages", sa.SmallInteger(), nullable=True),
        _str("credential_version", 64),
        _str("mapping_version", 64),
        _str("parser_version", 64),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default=ZERO),
        _tstz("next_retry_at"),
        _ts("first_seen_at"),
        _tstz("parsed_at"),
        _fk("run_id", "ingest_run.id"),
        sa.UniqueConstraint("sha256", name="uq_statement_file_sha256"),
    )
    op.create_index("ix_statement_file_status", "statement_file", ["status"])

    op.create_table(
        "statement_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("file_id", "statement_file.id", nullable=False),
        sa.Column("root", _enum("statement_source_root"), nullable=False),
        _str("drive_file_id", 128, nullable=False),
        _str("drive_path", 512, nullable=False),
        _str("drive_md5", 32, nullable=False),
        sa.Column("drive_size", sa.BigInteger(), nullable=False),
        sa.Column("path_history", JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        _tstz("removed_at"),
        _fk("superseded_by_source_id", "statement_source.id", ondelete="SET NULL"),
        sa.UniqueConstraint("drive_file_id", "drive_md5", name="uq_statement_source_drive_file_md5"),
    )
    op.create_index("ix_statement_source_file_id", "statement_source", ["file_id"])

    op.create_table(
        "account_statement",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("account_id", "account.id", nullable=False),
        sa.Column("kind", _enum("statement_kind"), nullable=False),
        _str("currency", 8, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("closing_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("opening_balance", MONEY, nullable=True),
        sa.Column("statement_total", MONEY, nullable=False),
        sa.Column("minimum_payment", MONEY, nullable=True),
        sa.Column("origin", _enum("statement_origin"), nullable=False),
        sa.Column("mode", _enum("statement_mode"), nullable=False),
        sa.Column("status", _enum("statement_status"), nullable=False, server_default=sa.text("'open'")),
        sa.Column("conflict_open", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("needs_recheck", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("current_revision_id", sa.Integer(), nullable=True),  # FK added below (circular)
        sa.Column("swept_through_event_id", sa.BigInteger(), nullable=False, server_default=ZERO),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("explained_count", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("open_case_count", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("note", sa.Text(), nullable=True),
        _fk("created_run_id", "ingest_run.id"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("account_id", "currency", "period_end", name="uq_account_statement_identity"),
        sa.CheckConstraint("period_start <= period_end", name="ck_account_statement_period"),
    )
    op.create_index("ix_account_statement_account_id", "account_statement", ["account_id"])

    op.create_table(
        "statement_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("statement_id", "account_statement.id", nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        _fk("file_id", "statement_file.id"),
        _str("parser", 64, nullable=False),
        _str("parser_version", 64, nullable=False),
        sa.Column("raw", JSONB(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("closing_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("opening_balance", MONEY, nullable=True),
        sa.Column("statement_total", MONEY, nullable=False),
        sa.Column("minimum_payment", MONEY, nullable=True),
        sa.Column("guardrail", JSONB(), nullable=False),
        sa.Column("guardrail_ok", sa.Boolean(), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=FALSE),
        _ts("created_at"),
        _fk("run_id", "ingest_run.id"),
        sa.UniqueConstraint("statement_id", "revision", name="uq_statement_revision_number"),
    )

    op.create_table(
        "statement_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("statement_id", "account_statement.id", nullable=False),
        _fk("first_revision_id", "statement_revision.id", nullable=False),
        sa.Column("first_line_id", sa.Integer(), nullable=True),  # FKs added below (circular with statement_line)
        sa.Column("current_line_id", sa.Integer(), nullable=True),
        sa.Column("status", _enum("statement_event_status"), nullable=False, server_default=sa.text("'live'")),
        _ts("created_at"),
    )
    op.create_index("ix_statement_event_statement_id", "statement_event", ["statement_id"])

    op.create_table(
        "statement_line",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("revision_id", "statement_revision.id", nullable=False),
        _fk("event_id", "statement_event.id", nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        _str("canonical_key", 64, nullable=False),
        _str("logical_key", 80, nullable=False),
        sa.Column("txn_date", sa.Date(), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=False),
        _str("merchant_raw", 256, nullable=False),
        _str("merchant_norm", 256, nullable=False),
        sa.Column("printed_amount", MONEY, nullable=False),
        sa.Column("flow_amount", MONEY, nullable=False),
        sa.Column("foreign_amount", MONEY, nullable=True),
        _str("foreign_currency", 8),
        sa.Column("line_kind", _enum("statement_line_kind"), nullable=False),
        sa.Column("installment_seq", sa.SmallInteger(), nullable=True),
        sa.Column("installment_total", sa.SmallInteger(), nullable=True),
        sa.UniqueConstraint("revision_id", "seq", name="uq_statement_line_revision_seq"),
    )
    op.create_index("ix_statement_line_event_id", "statement_line", ["event_id"])
    op.create_index("ix_statement_line_revision_logical", "statement_line", ["revision_id", "logical_key"])

    op.create_table(
        "line_lineage",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("event_id", "statement_event.id", nullable=False),
        _fk("old_line_id", "statement_line.id"),
        _fk("new_line_id", "statement_line.id"),
        sa.Column("equivalence", _enum("lineage_equivalence"), nullable=False),
        sa.Column("transferred", sa.Boolean(), nullable=False, server_default=FALSE),
        _ts("created_at"),
    )
    op.create_index("ix_line_lineage_event_id", "line_lineage", ["event_id"])

    op.create_table(
        "statement_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("statement_id", "account_statement.id", nullable=False),
        _fk("event_id", "statement_event.id", nullable=False),
        _fk("line_id", "statement_line.id", nullable=False),
        _fk("entry_id", "ledger_entry.id", ondelete="SET NULL"),
        _fk("group_id", "entry_group.id", ondelete="SET NULL"),
        sa.Column("role", _enum("coverage_role"), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("match_kind", _enum("match_kind"), nullable=False),
        _str("match_rule", 32, nullable=False),
        sa.Column("status", _enum("coverage_status"), nullable=False, server_default=sa.text("'active'")),
        _str("stale_reason", 64),
        _fk("run_id", "ingest_run.id"),
        _ts("created_at"),
    )
    op.create_index(
        "ux_statement_coverage_active_entry", "statement_coverage", ["entry_id"], unique=True,
        postgresql_where=sa.text("status = 'active' AND entry_id IS NOT NULL"),
    )
    op.create_index("ix_statement_coverage_statement_id", "statement_coverage", ["statement_id"])
    op.create_index("ix_statement_coverage_event_id", "statement_coverage", ["event_id"])

    op.create_table(
        "coverage_dirty",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", _enum("dirty_kind"), nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("op", _enum("dirty_op"), nullable=False),
        sa.Column("old_account_id", sa.Integer(), nullable=True),
        sa.Column("new_account_id", sa.Integer(), nullable=True),
        sa.Column("old_date", sa.Date(), nullable=True),
        sa.Column("new_date", sa.Date(), nullable=True),
        sa.Column("old_row", JSONB(), nullable=True),
        sa.Column("new_row", JSONB(), nullable=True),
        sa.Column("action_id", sa.Integer(), nullable=True),
        _ts("happened_at"),
    )
    op.create_index("ix_coverage_dirty_old_account", "coverage_dirty", ["old_account_id", "id"])
    op.create_index("ix_coverage_dirty_new_account", "coverage_dirty", ["new_account_id", "id"])

    op.create_table(
        "reconciliation_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("statement_id", "account_statement.id", nullable=False),
        _fk("revision_id", "statement_revision.id", nullable=False),
        sa.Column("kind", _enum("case_kind"), nullable=False),
        _fk("event_id", "statement_event.id"),
        _fk("line_id", "statement_line.id"),
        _fk("entry_id", "ledger_entry.id", ondelete="SET NULL"),
        sa.Column("candidates", JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        sa.Column("context", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("status", _enum("case_status"), nullable=False, server_default=sa.text("'open'")),
        sa.Column("explanation", _enum("case_explanation"), nullable=True),
        sa.Column("deferred_to_period_end", sa.Date(), nullable=True),
        _fk("deferred_to_statement_id", "account_statement.id", ondelete="SET NULL"),
        sa.Column("resolved_by", _enum("reconciliation_actor"), nullable=True),
        sa.Column("resolved_action_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=ONE),
        sa.Column("note", sa.Text(), nullable=True),
        _ts("created_at"),
        _tstz("resolved_at"),
    )
    op.create_index("ix_reconciliation_case_statement_status", "reconciliation_case", ["statement_id", "status"])
    op.create_index("ix_reconciliation_case_event_id", "reconciliation_case", ["event_id"])

    op.create_table(
        "reconciliation_proposal",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("case_id", "reconciliation_case.id", nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        _fk("event_id", "statement_event.id"),
        _str("action", 32, nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        _str("rationale", 1000, nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        _str("author", 64, nullable=False),
        sa.Column("status", _enum("proposal_status"), nullable=False, server_default=sa.text("'pending'")),
        _ts("created_at"),
        _tstz("decided_at"),
        sa.Column("applied_action_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_reconciliation_proposal_case_status", "reconciliation_proposal", ["case_id", "status"])

    op.create_table(
        "reconciliation_action",
        sa.Column("id", sa.Integer(), primary_key=True),
        _str("idempotency_key", 64, nullable=False),
        _fk("event_id", "statement_event.id"),
        _str("effect_slot", 16, nullable=False),
        _fk("case_id", "reconciliation_case.id", nullable=False),
        _fk("proposal_id", "reconciliation_proposal.id"),
        _str("action", 32, nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("actor", _enum("reconciliation_actor"), nullable=False),
        _fk("run_id", "ingest_run.id"),
        sa.Column("status", _enum("action_status"), nullable=False),
        sa.Column("created_entry_ids", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=EMPTY_INT_ARRAY),
        sa.Column("deleted_entry_ids", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=EMPTY_INT_ARRAY),
        sa.Column("touched_entry_ids", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=EMPTY_INT_ARRAY),
        sa.Column("touched_group_ids", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=EMPTY_INT_ARRAY),
        sa.Column("before", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("after", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("inverse", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("reverted_by_action_id", sa.Integer(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("idempotency_key", name="uq_reconciliation_action_idempotency"),
    )
    op.create_index(
        "ux_reconciliation_action_effect", "reconciliation_action", ["event_id", "action", "effect_slot"],
        unique=True, postgresql_where=sa.text("status = 'applied'"),
    )
    op.create_index("ix_reconciliation_action_case_id", "reconciliation_action", ["case_id"])

    op.create_table(
        "reconciliation_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("statement_id", "account_statement.id"),
        _fk("case_id", "reconciliation_case.id"),
        _fk("action_id", "reconciliation_action.id"),
        _fk("run_id", "ingest_run.id"),
        _str("event", 48, nullable=False),
        _str("actor", 64, nullable=False),
        _str("request_id", 64),
        sa.Column("detail", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        _ts("created_at"),
    )
    op.create_index("ix_reconciliation_audit_statement_id", "reconciliation_audit", ["statement_id", "id"])

    op.create_table(
        "policy_budget",
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("currency", sa.String(8), primary_key=True),
        sa.Column("used_amount", MONEY, nullable=False, server_default=ZERO),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default=ZERO),
    )

    op.create_table(
        "installment_plan_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        _fk("account_id", "account.id", nullable=False),
        _str("plan_key", 300, nullable=False),
        _fk("definition_id", "schedule_definition.id", nullable=False),
        sa.Column("confirmed_by", _enum("reconciliation_actor"), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("account_id", "plan_key", name="uq_installment_plan_map_account_key"),
    )

    op.create_table(
        "reconciliation_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False, server_default=ONE),
        sa.Column("data", JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("version", sa.Integer(), nullable=False, server_default=ONE),
        _ts("updated_at"),
        sa.CheckConstraint("id = 1", name="ck_reconciliation_settings_single_row"),
    )

    # Circular FKs, added once both ends exist (the models declare them with use_alter=True).
    op.create_foreign_key(
        "fk_account_statement_current_revision", "account_statement", "statement_revision",
        ["current_revision_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_statement_event_first_line", "statement_event", "statement_line", ["first_line_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_statement_event_current_line", "statement_event", "statement_line", ["current_line_id"], ["id"],
        ondelete="SET NULL",
    )

    for sql in (
        DIRTY_ENTRY_FUNCTION_SQL, DIRTY_ENTRY_TRIGGER_SQL, DIRTY_GROUP_FUNCTION_SQL, DIRTY_GROUP_TRIGGER_SQL,
        DIRTY_ACCOUNT_FUNCTION_SQL, DIRTY_ACCOUNT_TRIGGER_SQL,
    ):
        op.execute(sql)


def _assert_downgradable(connection: sa.Connection) -> None:
    blockers = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            blockers.append(f"{count} {label}")
    if blockers:
        raise RuntimeError("refusing to downgrade d1f3a7c2e9b4: " + "; ".join(blockers))


def downgrade() -> None:
    connection = op.get_bind()
    for table in LOCKED_TABLES:
        op.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
    _assert_downgradable(connection)

    op.execute("DROP TRIGGER trg_account_reconciliation_dirty ON account")
    op.execute("DROP FUNCTION reconciliation_dirty_account()")
    op.execute("DROP TRIGGER trg_entry_group_reconciliation_dirty ON entry_group")
    op.execute("DROP FUNCTION reconciliation_dirty_group()")
    op.execute("DROP TRIGGER trg_ledger_entry_reconciliation_dirty ON ledger_entry")
    op.execute("DROP FUNCTION reconciliation_dirty_entry()")

    op.drop_constraint("fk_statement_event_current_line", "statement_event", type_="foreignkey")
    op.drop_constraint("fk_statement_event_first_line", "statement_event", type_="foreignkey")
    op.drop_constraint("fk_account_statement_current_revision", "account_statement", type_="foreignkey")
    for table in TABLES_IN_DROP_ORDER:
        op.drop_table(table)  # drops its indexes and constraints with it

    op.drop_column("account", "statement_source_root")
    op.drop_column("account", "statement_live_from")
    op.drop_column("account", "statement_password_rule")
    for name in reversed(list(NEW_ENUMS)):
        op.execute(f"DROP TYPE {name}")
    # 'statement' stays in entry_source: the guard above guarantees no row uses it, and shrinking the enum would
    # rewrite ledger_entry (c4e8b2f1a7d3's downgrade recreates the type without it anyway).
