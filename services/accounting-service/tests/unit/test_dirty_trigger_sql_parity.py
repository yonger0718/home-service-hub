"""The migrations inline the dirty-trigger DDL (they import no app code); the latest copy must stay identical to the
model's: the trigger statements from d1f3a7c2e9b4, the function bodies from e2a9c4d1b7f0 (the writer barrier)."""

import importlib.util
from pathlib import Path

import pytest

from app.models import statements as models

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
TABLES = VERSIONS / "d1f3a7c2e9b4_statement_tables.py"
BARRIER = VERSIONS / "e2a9c4d1b7f0_dirty_barrier.py"
FUNCTIONS = ("DIRTY_ENTRY_FUNCTION_SQL", "DIRTY_GROUP_FUNCTION_SQL", "DIRTY_ACCOUNT_FUNCTION_SQL")
TRIGGERS = ("DIRTY_ENTRY_TRIGGER_SQL", "DIRTY_GROUP_TRIGGER_SQL", "DIRTY_ACCOUNT_TRIGGER_SQL")
KILL_SWITCH = (
    "IF NOT COALESCE((SELECT (data->>'dirty_enabled')::boolean FROM reconciliation_settings WHERE id = 1), false) "
    "THEN RETURN NULL; END IF;"
)
PERFORM = "PERFORM pg_advisory_xact_lock_shared(1145655892);"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _body(sql: str) -> str:
    return _norm(sql).split(" BEGIN ", 1)[1]


@pytest.mark.parametrize("name", TRIGGERS)
def test_migration_trigger_sql_matches_the_model(name):
    assert _norm(getattr(_load(TABLES), name)) == _norm(getattr(models, name))


@pytest.mark.parametrize("name", FUNCTIONS)
def test_barrier_migration_function_sql_matches_the_model(name):
    assert _norm(getattr(_load(BARRIER), name)) == _norm(getattr(models, name))


@pytest.mark.parametrize("name", FUNCTIONS)
def test_barrier_downgrade_restores_the_previous_bodies_verbatim(name):
    previous = _norm(getattr(_load(TABLES), name)).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    assert _norm(getattr(_load(BARRIER), "PREVIOUS_" + name)) == previous


@pytest.mark.parametrize("name", FUNCTIONS)
def test_every_trigger_function_starts_with_the_kill_switch_then_the_barrier(name):
    assert models.DIRTY_BARRIER_KEY == 0x44495254 == 1145655892
    assert _body(getattr(models, name)).startswith(f"{KILL_SWITCH} {PERFORM} ")
    body = _body(getattr(models, name))
    assert body.index(PERFORM) < body.index("INSERT INTO coverage_dirty")
