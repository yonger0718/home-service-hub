import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import SQLALCHEMY_DATABASE_URL, get_db, get_engine
from app.main import app
from app.services import fx_rate_service

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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach the real FX API; pass a fake http_get instead."""

    def _blocked(url, *args, **kwargs):
        raise AssertionError(f"unexpected network call in tests: {url}")

    monkeypatch.setattr(fx_rate_service.requests, "get", _blocked)


@pytest.fixture(autouse=True)
def no_import_lock_from_env(monkeypatch):
    """A production .env setting must never break the suite; lock tests set the flag themselves."""
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict | None:
        return self._payload


class FakeHttp:
    """Fake requests.get: answers URLs containing a known fragment, records every call."""

    def __init__(self, responses: dict[str, tuple[int, dict | None]]):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: int) -> FakeResponse:
        self.calls.append(url)
        for fragment, (status, payload) in self.responses.items():
            if fragment in url:
                return FakeResponse(status, payload)
        raise RequestsConnectionError(f"unreachable: {url}")


@pytest.fixture()
def fake_http():
    """Build a fake FX source: fake_http({"currency-api@2026-07-10/v1/currencies/jpy.json": (200, payload)})."""
    return FakeHttp


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
    app.dependency_overrides[get_engine] = lambda: pg_engine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


MOZE_HEADER = "帳戶,幣種,記錄類型,主類別,子類別,金額,手續費,折扣,名稱,商家,日期,時間,專案,描述,標籤,對象"


def _moze_row(
    account: str,
    currency: str,
    record_type: str,
    amount: str,
    *,
    main: str = "",
    sub: str = "",
    fee: str = "0",
    discount: str = "0",
    name: str = "",
    merchant: str = "",
    date: str = "2026/09/01",
    time: str = "12:00",
    project: str = "",
    description: str = "",
    tags: str = "",
    counterparty: str = "",
) -> str:
    return ",".join(
        [account, currency, record_type, main, sub, amount, fee, discount, name, merchant,
         date, time, project, description, tags, counterparty]
    )


def _moze_opening(account: str, currency: str, amount: str) -> str:
    return _moze_row(account, currency, "初始金額", amount, fee="", discount="", date="", time="")


def _moze_csv(*rows: str, bom: bool = True, newline: str = "\n", header: str = MOZE_HEADER) -> bytes:
    body = newline.join([header, *rows]) + newline
    return (("\ufeff" if bom else "") + body).encode("utf-8")


@pytest.fixture()
def moze():
    """Builders for small synthetic MOZE CSV files (never real data)."""
    return SimpleNamespace(row=_moze_row, opening=_moze_opening, csv=_moze_csv, header=MOZE_HEADER)
