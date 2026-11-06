"""The migration inlines the einvoice unlink trigger DDL (migrations import no app code); it must stay identical to
the model's."""

import importlib.util
from pathlib import Path

import pytest

from app.models import einvoice as models

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "b6e2c8f4a9d1_einvoice_link.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm(sql: str) -> str:
    return " ".join(sql.split())


@pytest.mark.parametrize("name", ("EINVOICE_UNLINK_FUNCTION_SQL", "EINVOICE_UNLINK_TRIGGER_SQL"))
def test_migration_trigger_sql_matches_the_model(name):
    assert _norm(getattr(_load(MIGRATION), name)) == _norm(getattr(models, name))
