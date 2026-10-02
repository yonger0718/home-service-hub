import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

LEGACY_TABLES = (
    "categories",
    "credit_cards",
    "installments",
    "payment_methods",
    "subscriptions",
    "transactions",
)
LEDGER_TABLES = ("account", "category", "fx_rate", "import_run", "ledger_entry", "project")


def _schema(url) -> dict:
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        schema: dict = {"tables": tables}
        for table in tables:
            if table == "alembic_version":
                continue
            schema[table] = {
                "columns": sorted(
                    (c["name"], str(c["type"]), c["nullable"], str(c.get("default")))
                    for c in inspector.get_columns(table)
                ),
                "pk": inspector.get_pk_constraint(table)["constrained_columns"],
                "fks": sorted(
                    (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                    for fk in inspector.get_foreign_keys(table)
                ),
                "indexes": sorted(
                    (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(table)
                ),
                "uniques": sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)),
            }
        return schema
    finally:
        engine.dispose()


def _version(url) -> str:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_upgrade_replaces_legacy_tables_with_ledger_tables(database_factory, alembic_config):
    url = database_factory()
    command.upgrade(alembic_config(url), "head")

    tables = set(_schema(url)["tables"])
    assert set(LEDGER_TABLES) <= tables
    assert not (set(LEGACY_TABLES) & tables)


def test_upgrade_aborts_when_a_legacy_table_has_rows(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "8a4c4f9b2d1b")
    engine = create_engine(url)
    with engine.begin() as conn:
        category_id = conn.execute(
            text("INSERT INTO categories (name) VALUES ('legacy') RETURNING id")
        ).scalar_one()
        conn.execute(
            text("INSERT INTO transactions (category_id, item) VALUES (:c, 'legacy row')"),
            {"c": category_id},
        )
    engine.dispose()
    before = _schema(url)

    with pytest.raises(RuntimeError, match="transactions, categories"):
        command.upgrade(config, "head")

    assert _schema(url) == before
    assert _version(url) == "8a4c4f9b2d1b"


def test_downgrade_restores_revision_8a4c4f9b2d1b_schema(database_factory, alembic_config):
    reference_url = database_factory()
    command.upgrade(alembic_config(reference_url), "8a4c4f9b2d1b")

    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "8a4c4f9b2d1b")

    assert _version(url) == "8a4c4f9b2d1b"
    assert _schema(url) == _schema(reference_url)
