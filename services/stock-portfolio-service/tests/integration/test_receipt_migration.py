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


def test_real_alembic_head_upgrade_and_a4_downgrade_preserve_deployed_data(tmp_path, monkeypatch):
    """Exercise the unmodified graph/env against a synthetic deployed-a4 database.

    The version row is part of the predecessor fixture, not a stamp command or
    a workaround for resolving a fork. All transitions use ordinary Alembic APIs.
    Older migrations are PostgreSQL-specific; this is not a base-to-head test.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import app.database as database

    service = Path(__file__).parents[2]
    url = f"sqlite:///{tmp_path / 'deployed-a4.db'}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('a4p5q6r7s8t9')")
        conn.exec_driver_sql('CREATE TABLE dividends (id INTEGER PRIMARY KEY, amount NUMERIC NOT NULL, stock_dividend_shares INTEGER NOT NULL DEFAULT 0, received_date DATETIME, CONSTRAINT ck_dividends_amount_positive CHECK (amount > 0))')
        conn.exec_driver_sql('CREATE TABLE broker_account (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE cash_transaction (id INTEGER PRIMARY KEY, related_dividend_id INTEGER, amount NUMERIC)')
        conn.exec_driver_sql('CREATE TABLE transactions (id INTEGER PRIMARY KEY, market VARCHAR(8), broker VARCHAR(32))')
        conn.exec_driver_sql('CREATE TABLE broker_cash_flows (id INTEGER PRIMARY KEY, broker VARCHAR(32), amount NUMERIC, note TEXT)')
        conn.exec_driver_sql("INSERT INTO dividends VALUES (1, 3090, 0, '2026-09-10'), (2, 990, 0, NULL)")
        conn.exec_driver_sql('INSERT INTO broker_account VALUES (1)')
        conn.exec_driver_sql('INSERT INTO cash_transaction VALUES (1, 1, 3090), (2, NULL, 42)')
        conn.exec_driver_sql("INSERT INTO transactions VALUES (1, 'TW', 'TW_CATHAY'), (2, 'US', 'FOREIGN_MANUAL')")
        conn.exec_driver_sql("INSERT INTO broker_cash_flows VALUES (1, 'TW_CATHAY', 12345, 'preserve original deposit')")

    def snapshot():
        with engine.connect() as conn:
            return {
                table: conn.exec_driver_sql(f'SELECT {columns} FROM {table} ORDER BY id').all()
                for table, columns in {
                    'dividends': 'id, amount, stock_dividend_shares, received_date',
                    'broker_account': '*', 'cash_transaction': '*',
                    'transactions': '*', 'broker_cash_flows': '*',
                }.items()
            }

    before = snapshot()
    monkeypatch.setattr(database, 'SQLALCHEMY_DATABASE_URL', url)
    config = Config(str(service / 'alembic.ini'))
    config.set_main_option('script_location', str(service / 'alembic'))
    command.upgrade(config, 'head')
    assert ScriptDirectory.from_config(config).get_revision('a5receipt').down_revision == 'a4p5q6r7s8t9'
    assert snapshot() == before
    with engine.begin() as conn:
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == 'a5receipt'
        assert conn.exec_driver_sql('SELECT receipt_status, receipt_date, payment_date, entitlement_key FROM dividends').all() == [('legacy_unknown', None, None, None)] * 2
        conn.exec_driver_sql("UPDATE dividends SET receipt_status='pending', entitlement_key='synthetic' WHERE id=2")
    with pytest.raises(RuntimeError, match='Cannot downgrade'):
        command.downgrade(config, 'a4p5q6r7s8t9')
    assert snapshot() == before
    with engine.begin() as conn:
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == 'a5receipt'
        assert conn.exec_driver_sql('SELECT receipt_status FROM dividends WHERE id=2').scalar_one() == 'pending'
        # Restore this synthetic fixture only, to exercise the permitted branch.
        conn.exec_driver_sql("UPDATE dividends SET receipt_status='legacy_unknown', entitlement_key=NULL WHERE id=2")
    command.downgrade(config, 'a4p5q6r7s8t9')
    assert snapshot() == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == 'a4p5q6r7s8t9'
        assert 'receipt_status' not in {c['name'] for c in sa.inspect(conn).get_columns('dividends')}
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    assert snapshot() == before
    engine.dispose()


def test_real_graph_has_one_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    service = Path(__file__).parents[2]
    config = Config(str(service / 'alembic.ini'))
    config.set_main_option('script_location', str(service / 'alembic'))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert 'a4p5q6r7s8t9' not in heads, 'receipt migration must descend from a4'
    assert script.get_current_head() == 'a5receipt'


def test_fresh_database_real_environment_traverses_every_revision_once(tmp_path, monkeypatch):
    """Graph-only base-to-head traversal; historical DDL is deliberately NOT run.

    Actual env.py/command upgrade and version-table writes execute on empty
    SQLite. Migration bodies are instrumented to observe selection and order;
    PostgreSQL-specific DDL is not claimed to execute in this graph regression.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import app.database as database

    service = Path(__file__).parents[2]
    config = Config(str(service / 'alembic.ini'))
    config.set_main_option('script_location', str(service / 'alembic'))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    assert len(revisions) == len(list((service / 'alembic/versions').glob('*.py')))
    assert len({r.revision for r in revisions}) == len(revisions)
    visited = []
    for revision in revisions:
        monkeypatch.setattr(revision.module, 'upgrade', lambda r=revision: visited.append(r.revision))
    monkeypatch.setattr(ScriptDirectory, 'from_config', lambda config: script)
    url = f"sqlite:///{tmp_path / 'fresh-graph.db'}"
    monkeypatch.setattr(database, 'SQLALCHEMY_DATABASE_URL', url)
    command.upgrade(config, 'head')
    assert visited == [r.revision for r in reversed(revisions)]
    assert len(visited) == len(set(visited))
    start = visited.index('l9a0b1c2d3e4')
    assert visited[start:start + 4] == ['l9a0b1c2d3e4', 'm0b1c2d3e4f5', 'm0b1c2d3e4f6', 'n1c2d3e4f5g6']
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == 'a5receipt'
        assert sa.inspect(conn).get_table_names() == ['alembic_version']
    engine.dispose()
