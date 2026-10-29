import os

os.environ.setdefault("SCHEDULER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests marked live",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: tests that call live external services")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="need --live option to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _never_reach_the_configured_database(monkeypatch):
    """app.database loads the root .env with override=True at import, so the setdefault above loses to the file. The
    scheduler and the post-import chain open app.database.SessionLocal (the configured database, production on the
    host) instead of the test session; keep both off for every test. Tests that exercise either enable the flag
    themselves and fake the work."""
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("POST_IMPORT_RECALC_ENABLED", "false")


@pytest.fixture(autouse=True)
def _fresh_post_import_results():
    """The post-import chain keeps its latest results in process (latest_status prefers the newest import); an import
    chain run by an earlier test must not answer a later test's status read (#211)."""
    from app.services import post_import_orchestrator as orch

    with orch._RESULTS_LOCK:
        orch._LATEST_RESULTS.clear()
    yield
    with orch._RESULTS_LOCK:
        orch._LATEST_RESULTS.clear()


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # NOTE: Tests use create_all() instead of Alembic for speed.
    # Prod uses Alembic migrations only (see alembic/ directory).
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
