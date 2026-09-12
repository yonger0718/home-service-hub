"""Migration executes against synthetic SQLite only, including rollback guard."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_legacy_upgrade_preserves_cash_and_unknown_receipt_and_safe_downgrade():
    path = Path(__file__).parents[2] / 'alembic/versions/a5receipt_pending_dividends.py'
    spec = importlib.util.spec_from_file_location('receipt_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE dividends (id INTEGER PRIMARY KEY, amount NUMERIC NOT NULL, stock_dividend_shares INTEGER NOT NULL DEFAULT 0, received_date DATETIME, CONSTRAINT ck_dividends_amount_positive CHECK (amount > 0))')
        conn.exec_driver_sql('CREATE TABLE broker_account (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE cash_transaction (id INTEGER PRIMARY KEY, related_dividend_id INTEGER, amount NUMERIC)')
        conn.exec_driver_sql("INSERT INTO dividends VALUES (1, 3090, 0, '2026-09-10')")
        conn.exec_driver_sql('INSERT INTO cash_transaction VALUES (1, 1, 3090)')
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            row = conn.execute(sa.text('SELECT receipt_status, receipt_date, payment_date, entitlement_key FROM dividends')).one()
            assert tuple(row) == ('legacy_unknown', None, None, None)
            assert conn.execute(sa.text('SELECT amount FROM cash_transaction')).scalar() == 3090
            conn.exec_driver_sql("UPDATE dividends SET receipt_status='pending', entitlement_key='synthetic'")
            with pytest.raises(RuntimeError, match='Cannot downgrade'):
                migration.downgrade()
            conn.exec_driver_sql("UPDATE dividends SET receipt_status='legacy_unknown', entitlement_key=NULL")
            migration.downgrade()
            assert 'receipt_status' not in {c['name'] for c in sa.inspect(conn).get_columns('dividends')}
            assert conn.execute(sa.text('SELECT amount FROM cash_transaction')).scalar() == 3090
    engine.dispose()
