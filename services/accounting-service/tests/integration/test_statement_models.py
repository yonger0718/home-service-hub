from sqlalchemy import text

from app.models import (
    AccountStatement, CoverageDirty, IngestRun, StatementEvent, StatementFile, StatementLine, StatementRevision,
    StatementSource, ReconciliationSettings,
)


def test_statement_tables_exist_after_migration(db_session):
    names = {row[0] for row in db_session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert {
        "ingest_run", "statement_file", "statement_source", "account_statement", "statement_revision", "statement_event",
        "statement_line", "line_lineage", "statement_coverage", "coverage_dirty", "reconciliation_case",
        "reconciliation_proposal", "reconciliation_action", "reconciliation_audit", "policy_budget",
        "installment_plan_map", "reconciliation_settings",
    } <= names


def test_entry_source_enum_has_statement(db_session):
    values = {row[0] for row in db_session.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text"))}
    assert "statement" in values


def test_dirty_trigger_records_old_and_new_on_account_move(db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    entry = seed.entry(a, "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    entry.account_id = b.id
    db_session.commit()
    rows = db_session.query(CoverageDirty).order_by(CoverageDirty.id).all()
    assert [(r.kind, r.op, r.old_account_id, r.new_account_id) for r in rows] == [("entry", "update", a.id, b.id)]
    assert rows[0].old_row["account_id"] == a.id and rows[0].new_row["account_id"] == b.id


def test_dirty_trigger_ignores_noop_update(db_session, seed):
    entry = seed.entry(seed.account("A"), "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("UPDATE ledger_entry SET name = name WHERE id = :id"), {"id": entry.id})
    db_session.commit()
    assert db_session.query(CoverageDirty).count() == 0


def test_dirty_trigger_tags_action_id_from_transaction_setting(db_session, seed):
    entry = seed.entry(seed.account("A"), "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("SET LOCAL app.reconciliation_action_id = '42'"))
    entry.amount = entry.amount - 1
    db_session.commit()
    assert db_session.query(CoverageDirty).one().action_id == 42


def test_account_config_change_is_dirty(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    a.opening_balance = 5
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.op, row.new_account_id) == ("account", "update", a.id)
