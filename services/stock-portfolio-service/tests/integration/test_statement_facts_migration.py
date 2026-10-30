"""Migration executes against synthetic SQLite only."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_statement_facts_columns_round_trip_on_sqlite():
    path = Path(__file__).parents[2] / "alembic/versions/e9settle_cathay_statement_facts.py"
    spec = importlib.util.spec_from_file_location("statement_facts_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE cathay_statement_day (trade_date DATE PRIMARY KEY, last_status VARCHAR(16))")
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            cols = {c["name"]: c for c in sa.inspect(conn).get_columns("cathay_statement_day")}
            assert {"settlement_date", "net_amount", "cash_net"} <= set(cols) and all(cols[k]["nullable"] for k in ("settlement_date", "net_amount", "cash_net"))
            migration.downgrade()
            assert {"settlement_date", "net_amount", "cash_net"}.isdisjoint({c["name"] for c in sa.inspect(conn).get_columns("cathay_statement_day")})
    engine.dispose()
