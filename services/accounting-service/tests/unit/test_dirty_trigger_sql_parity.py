"""The migration inlines the dirty-trigger DDL (it imports no app code); it must stay identical to the model's."""

import importlib.util
from pathlib import Path

import pytest

from app.models import statements as models

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "d1f3a7c2e9b4_statement_tables.py"
NAMES = (
    "DIRTY_ENTRY_FUNCTION_SQL", "DIRTY_ENTRY_TRIGGER_SQL", "DIRTY_GROUP_FUNCTION_SQL", "DIRTY_GROUP_TRIGGER_SQL",
    "DIRTY_ACCOUNT_FUNCTION_SQL", "DIRTY_ACCOUNT_TRIGGER_SQL",
)
KILL_SWITCH = (
    "IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) "
    "THEN RETURN NULL; END IF;"
)


def _migration():
    spec = importlib.util.spec_from_file_location("statement_tables_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm(sql: str) -> str:
    return " ".join(sql.split())


@pytest.mark.parametrize("name", NAMES)
def test_migration_trigger_sql_matches_the_model(name):
    assert _norm(getattr(_migration(), name)) == _norm(getattr(models, name))


@pytest.mark.parametrize("name", [n for n in NAMES if n.endswith("_FUNCTION_SQL")])
def test_every_trigger_function_starts_with_the_kill_switch(name):
    assert _norm(getattr(models, name)).split(" BEGIN ", 1)[1].startswith(KILL_SWITCH)
