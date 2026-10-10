"""dirty-trigger writer barrier: every dirty trigger takes pg_advisory_xact_lock_shared(DIRTY_BARRIER_KEY)

The sweep (coverage_service.sweep) takes the same key exclusively to read max(coverage_dirty.id); writers hold the
shared lock until commit, so every event id at or below that cap is committed and the watermark never skips an event
whose id was assigned before a later-id event committed (design §4.6 erratum). Function bodies only; the triggers
from d1f3a7c2e9b4 are unchanged.

Revision ID: e2a9c4d1b7f0
Revises: d1f3a7c2e9b4
Create Date: 2026-10-09 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e2a9c4d1b7f0"
down_revision: Union[str, Sequence[str], None] = "d1f3a7c2e9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copied verbatim from app/models/statements.py (migrations do not import app code); parity is unit-tested.
DIRTY_BARRIER_KEY = 0x44495254  # 'DIRT'
DIRTY_ENTRY_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_entry() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock_shared({DIRTY_BARRIER_KEY});
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
DIRTY_GROUP_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_group() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock_shared({DIRTY_BARRIER_KEY});
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
DIRTY_ACCOUNT_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION reconciliation_dirty_account() RETURNS trigger AS $$
DECLARE
    v_action integer := NULLIF(current_setting('app.reconciliation_action_id', true), '')::integer;
BEGIN
    IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock_shared({DIRTY_BARRIER_KEY});
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

# The d1f3a7c2e9b4 bodies (as OR REPLACE), restored by downgrade.
PREVIOUS_DIRTY_ENTRY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION reconciliation_dirty_entry() RETURNS trigger AS $$
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
PREVIOUS_DIRTY_GROUP_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION reconciliation_dirty_group() RETURNS trigger AS $$
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
PREVIOUS_DIRTY_ACCOUNT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION reconciliation_dirty_account() RETURNS trigger AS $$
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


def upgrade() -> None:
    for sql in (DIRTY_ENTRY_FUNCTION_SQL, DIRTY_GROUP_FUNCTION_SQL, DIRTY_ACCOUNT_FUNCTION_SQL):
        op.execute(sql)


def downgrade() -> None:
    for sql in (
        PREVIOUS_DIRTY_ENTRY_FUNCTION_SQL, PREVIOUS_DIRTY_GROUP_FUNCTION_SQL, PREVIOUS_DIRTY_ACCOUNT_FUNCTION_SQL,
    ):
        op.execute(sql)
