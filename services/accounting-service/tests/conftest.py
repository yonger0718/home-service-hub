import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import SQLALCHEMY_DATABASE_URL, get_db
from app.main import app

SERVICE_DIR = Path(__file__).resolve().parents[1]
LEDGER_TABLES = "ledger_entry, import_run, category, project, account, fx_rate"


@contextmanager
def _disposable_database():
    """Create an empty database in the dev Postgres server and drop it afterwards.

    Credentials come from the root .env via app.database; they are never printed.
    """
    base_url = make_url(SQLALCHEMY_DATABASE_URL)
    name = f"accounting_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError as exc:
        admin.dispose()
        pytest.fail(f"integration tests need the dev Postgres server: {exc.__class__.__name__}")
    try:
        yield base_url.set(database=name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic_config(url: URL) -> Config:
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return config


@pytest.fixture()
def database_factory():
    """Return a callable that creates a fresh, empty database URL (dropped at teardown)."""
    with ExitStack() as stack:
        yield lambda: stack.enter_context(_disposable_database())


@pytest.fixture()
def alembic_config():
    return _alembic_config


@pytest.fixture(scope="session")
def pg_engine():
    with _disposable_database() as url:
        command.upgrade(_alembic_config(url), "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture()
def db_session(pg_engine: Engine):
    with pg_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {LEDGER_TABLES} RESTART IDENTITY CASCADE"))
        conn.execute(text("ALTER SEQUENCE ledger_entry_seq_seq RESTART WITH 1"))
    session = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(pg_engine: Engine, db_session):
    testing_session = sessionmaker(bind=pg_engine, autoflush=False)

    def _override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
