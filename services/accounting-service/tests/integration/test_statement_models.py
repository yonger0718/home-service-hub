from datetime import date

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
    assert (row.kind, row.op, row.old_account_id, row.new_account_id) == ("account", "update", a.id, a.id)


def test_dirty_trigger_records_insert_with_new_side_only(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    entry = seed.entry(a, "-10", day=date(2026, 9, 3), posted_date=date(2026, 9, 5))
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.row_id, row.op) == ("entry", entry.id, "insert")
    assert (row.old_account_id, row.new_account_id) == (None, a.id)
    assert (row.old_date, row.new_date) == (None, date(2026, 9, 5))
    assert row.old_row is None and row.new_row["id"] == entry.id


def test_dirty_trigger_records_delete_with_old_side_only(db_session, seed):
    a = seed.account("A")
    entry = seed.entry(a, "-10", day=date(2026, 9, 3), posted_date=date(2026, 9, 5))
    db_session.commit()
    entry_id = entry.id
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("DELETE FROM ledger_entry WHERE id = :id"), {"id": entry_id})
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.row_id, row.op) == ("entry", entry_id, "delete")
    assert (row.old_account_id, row.new_account_id) == (a.id, None)
    assert (row.old_date, row.new_date) == (date(2026, 9, 5), None)
    assert row.new_row is None and row.old_row["id"] == entry_id


def test_dirty_trigger_records_entry_group_update(db_session, seed):
    group = seed.group()
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    group.name = "renamed"
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.row_id, row.op) == ("group", group.id, "update")
    assert (row.old_row["name"], row.new_row["name"]) == (None, "renamed")
    assert (row.old_account_id, row.new_account_id) == (None, None)


def test_account_archive_only_change_is_dirty(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    a.is_archived = True
    db_session.commit()
    row = db_session.query(CoverageDirty).one()
    assert (row.kind, row.row_id, row.op, row.old_account_id, row.new_account_id) == (
        "account", a.id, "update", a.id, a.id
    )
    assert (row.old_row["is_archived"], row.new_row["is_archived"]) == (False, True)


def test_account_name_only_change_is_not_dirty(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    a.name = "Renamed"
    db_session.commit()
    assert db_session.query(CoverageDirty).count() == 0


def test_account_dirty_row_tags_action_id_from_transaction_setting(db_session, seed):
    a = seed.account("A")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    db_session.execute(text("SET LOCAL app.reconciliation_action_id = '7'"))
    a.opening_balance = 5
    db_session.commit()
    assert db_session.query(CoverageDirty).one().action_id == 7


def test_dirty_trigger_action_id_is_null_without_the_setting(db_session, seed):
    entry = seed.entry(seed.account("A"), "-10")
    db_session.commit()
    db_session.execute(text("DELETE FROM coverage_dirty"))
    entry.amount = entry.amount - 1
    db_session.commit()
    assert db_session.query(CoverageDirty).one().action_id is None
