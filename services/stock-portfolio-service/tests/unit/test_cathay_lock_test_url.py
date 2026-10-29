"""The Postgres lock test's own-database URL can never route back to the URL's database (AGENT-340)."""
from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

from tests.integration.test_cathay_statement_lock_pg import _engine_args

OWN = "cathay_lock_test_0123456789abcdef"


def _effective_dbname(url, connect_args) -> str:
    # Same merge create_engine does: the dialect's args from the URL, then connect_args on top.
    _, params = PGDialect_psycopg2().create_connect_args(url)
    return {**params, **connect_args}["dbname"]


@pytest.mark.parametrize("pg_url", [
    "postgresql+psycopg2://fictional@localhost/sentinel",
    "postgresql+psycopg2://fictional@localhost/sentinel?dbname=sentinel",
    "postgresql+psycopg2://fictional@localhost/sentinel?database=sentinel&sslmode=disable",
    "postgresql+psycopg2://fictional@localhost/sentinel?service=sentinel",
    "postgresql://fictional@localhost/?dbname=sentinel",
])
def test_own_database_wins_over_url_query(pg_url):
    url, connect_args = _engine_args(pg_url, OWN)
    assert url.database == OWN
    assert not {"dbname", "database", "service"} & set(url.query)
    assert _effective_dbname(url, connect_args) == OWN


def test_other_query_options_kept():
    url, connect_args = _engine_args("postgresql+psycopg2://fictional@localhost/sentinel?sslmode=disable", OWN)
    assert url.query == {"sslmode": "disable"}
    assert connect_args["options"] == "-c lock_timeout=30s -c statement_timeout=60s"
