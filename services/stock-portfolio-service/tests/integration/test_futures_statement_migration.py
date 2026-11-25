"""Migration executes against synthetic SQLite only (same mechanism as test_statement_facts_migration)."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

TABLES = {"futures_statement_day", "futures_statement_month", "futures_statement_run"}


def _load():
    path = Path(__file__).parents[2] / "alembic/versions/f1futures_statement.py"
    spec = importlib.util.spec_from_file_location("futures_statement_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_chain():
    migration = _load()
    assert (migration.revision, migration.down_revision) == ("f1futures", "e9settle")


def test_futures_tables_and_column_round_trip_on_sqlite():
    migration = _load()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE portfolio_snapshot (date DATE PRIMARY KEY, total_market_value NUMERIC(20,4) NOT NULL)"
        )
        conn.exec_driver_sql("INSERT INTO portfolio_snapshot VALUES ('2026-11-24', 10)")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            insp = sa.inspect(conn)
            assert TABLES <= set(insp.get_table_names())
            cols = {c["name"]: c for c in insp.get_columns("portfolio_snapshot")}
            assert "futures_equity_twd" in cols and not cols["futures_equity_twd"]["nullable"]
            assert conn.exec_driver_sql("SELECT futures_equity_twd FROM portfolio_snapshot").scalar() == 0
            day = {c["name"]: c for c in insp.get_columns("futures_statement_day")}
            assert {"statement_date", "equity", "fills_total", "ack_findings_sha", "findings_since"} <= set(day)
            assert not day["equity"]["nullable"] and day["fills_total"]["nullable"]
            month = {c["name"] for c in insp.get_columns("futures_statement_month")}
            assert {"month", "month_end", "a", "w", "long_premium_value", "activity_dates"} <= month
            migration.downgrade()
            insp = sa.inspect(conn)
            assert TABLES.isdisjoint(insp.get_table_names())
            assert "futures_equity_twd" not in {c["name"] for c in insp.get_columns("portfolio_snapshot")}
    engine.dispose()
