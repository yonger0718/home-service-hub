import json
import sys
import uuid
from contextlib import ExitStack, contextmanager
from datetime import date
from decimal import Decimal
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
from app.models import (
    Account,
    Category,
    Counterparty,
    EntryGroup,
    FxRate,
    LedgerEntry,
    Project,
    RewardRule,
    ScheduleDefinition,
    ScheduleInstance,
)
from app.services import fx_rate_service
from tests.helpers import make_account, make_definition, make_entry, make_instance, schedule_line, set_dirty
from app.services.moze_backup_json import parse_backup_doc

SERVICE_DIR = Path(__file__).resolve().parents[1]
# Every ledger table, children first; TRUNCATE ... CASCADE also clears rows the list misses.
# preference is truncated too: settings_service.get_preference recreates the defaults on first read.
LEDGER_TABLES = (
    "reconciliation_audit, reconciliation_action, reconciliation_proposal, reconciliation_case, statement_coverage, "
    "line_lineage, statement_line, statement_event, statement_revision, account_statement, statement_source, "
    "statement_file, ingest_run, coverage_dirty, policy_budget, installment_plan_map, reconciliation_settings, "
    "entry_reward_rule, schedule_instance, schedule_definition, ledger_entry, reward_rule, entry_group, counterparty, "
    "import_run, category, project, account, account_group, fx_rate, preference"
)


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


@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch):
    """The in-process schedule job never starts inside the test process (TestClient runs startup hooks)."""
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", "false")


@pytest.fixture(autouse=True)
def no_api_auth_from_env(monkeypatch):
    """Tokens in a production .env must never break the suite; auth tests set the variables themselves."""
    monkeypatch.delenv("ACCOUNTING_API_TOKENS", raising=False)
    monkeypatch.delenv("ACCOUNTING_DOCS_PUBLIC", raising=False)


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
        engine = create_engine(url, hide_parameters=True)  # same as app.database.engine
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


BACKUP_EXPORTED_AT = "2026-10-01T17:00:37"


def _bk_group(identifier="G-BANK", name="APP_GROUP_BANK", **fields) -> dict:
    return {"identifier": identifier, "name": name, "sequence": 1, "type": 0, **fields}


def _bk_account(identifier="A-WALLET", name="錢包", currency="TWD", **fields) -> dict:
    return {
        "identifier": identifier, "name": name, "mainCurrency": currency, "group": None, "originalAmount": 0,
        "isArchived": False, "sequence": 0, "desc": "", "isBalanceIncluded": True, "isCreditAccount": False,
        "startDay": 1, "paymentDeadlineType": 0, "paymentDeadline": 0, "creditLimit": 0, "combinedAccount": None,
        "creditSharingID": None, "autoPaidAccount": None, "isCurrencyFeeEnabled": False, "feePercentage": 0,
        "feeCalculation": 0, "isRefundWithCurrencyFee": False, "type": 0, "imageName": "",
        "cacheDate": "2026-10-01T00:00:00", "balanceInfo": {}, **fields,
    }


def _bk_category(identifier="C-FOOD", name="CATEGORY_FOOD", type_=1, **fields) -> dict:
    return {
        "identifier": identifier, "name": name, "type": type_, "imageName": "Food", "colorHex": "#f0cd92",
        "isHidden": False, "sequence": 0, **fields,
    }


def _bk_classification(identifier="K-LUNCH", name="午餐", category="C-FOOD", **fields) -> dict:
    return {
        "identifier": identifier, "name": name, "category": category, "defaultAccount": None,
        "defaultProject": None, "isHidden": False, "sequence": 0, "imageName": "", **fields,
    }


def _bk_project(identifier="P-TRIP", name="PROJECT_TRAVEL", **fields) -> dict:
    return {"identifier": identifier, "name": name, "isArchived": False, "sequence": 0, **fields}


def _bk_target(identifier="T-ALAN", name="Alan", **fields) -> dict:
    return {"identifier": identifier, "name": name, "type": 1, "isSettle": False, **fields}


def _bk_record(identifier="R-1", account="A-WALLET", type_=0, price=-100, *, fee=0, bonus=0,
               date="2026-09-01T12:00:00", currency="TWD", **fields) -> dict:
    return {
        "identifier": identifier, "type": type_, "price": price, "fee": fee, "bonus": bonus,
        "total": price + fee + bonus, "currency": currency, "currencyConversion": None, "account": account,
        "project": None, "classification": None, "target": None, "bonusRewards": [], "date": date,
        "chargeDate": fields.pop("chargeDate", date), "name": "", "desc": "", "tags": "", "store": "",
        "feeName": "", "bonusName": "", "transferID": None, "refundID": None, "rewardID": None,
        "rewardRecordID": None, "packageID": None, "relatedID": None, "eventID": None, "feeID": None,
        "isTransferIn": type_ == 2 and price > 0, "isRefund": False, "isEnabled": True, "invoiceNumber": None,
        **fields,
    }


def _bk_transfer(identifier="X-1", out_record="R-OUT", in_record="R-IN", exchange_rate=1, **fields) -> dict:
    return {"identifier": identifier, "outRecord": out_record, "inRecord": in_record, "exchangeRate": exchange_rate, **fields}


def _bk_package(identifier="PK-1", records=("R-1",), type_=0, event_type=0, **fields) -> dict:
    return {
        "identifier": identifier, "type": type_, "eventType": event_type, "records": list(records), "name": "",
        "store": "", "desc": "", **fields,
    }


def _bk_period(identifier="PER-1", *, unit=2, days=21, times=0, type_=0, start="2026-10-21T00:00:00", **fields) -> dict:
    return {
        "identifier": identifier, "unit": unit, "days": days, "times": times, "type": type_, "startDate": start,
        "count": 1, "startIndex": 1, **fields,
    }


def _bk_installment(identifier="INS-1", *, day_of_month=9, dates=(), times=36, total=300000, remainder=0, **fields) -> dict:
    return {
        "identifier": identifier, "account": "A-WALLET", "dayOfMonth": day_of_month,
        "dateInfo": {str(index): day for index, day in enumerate(dates)}, "times": times, "total": total,
        "installment": 0, "remainder": remainder, "startDate": dates[0] if dates else "2026-01-01T00:00:00",
        "interestType": 0, "interestRate": 0, **fields,
    }


def _bk_rule(identifier="B-1", account="A-CARD", **fields) -> dict:
    return {
        "identifier": identifier, "accountID": account, "name": "回饋", "desc": "", "type": 0,
        "rewardPercentage": 0.01, "rewardAmount": 0, "rewardPeriodType": 0, "rewardTimeType": 2,
        "rewardDelayDays": 0, "rewardMonth": 1, "rewardDay": 15, "rewardCalculation": 0,
        "totalRewardCalculation": 1, "rewardLimit": 0, "totalRewardLimit": 0, "rewardSharingID": None,
        "spendThreshold": 0, "totalSpendThreshold": 0, "minCountThreshold": 0, "isBasic": False,
        "rewardAccountID": account, "rewardProjectID": None, "startDate": "2026-01-01T00:00:00",
        "dueDate": "2026-12-31T23:59:59", "isEnabled": True, "sequence": 0, **fields,
    }


def _bk_conversion(record_id="R-1", rate=0.2163, base="TWD", target="JPY") -> dict:
    """MOZE stores exchangeRate as units of `base` per 1 unit of `target` (TWD per JPY by default)."""
    return {"recordID": record_id, "exchangeRate": rate, "baseCurrencyCode": base, "targetCurrencyCode": target}


def _bk_preference(**fields) -> dict:
    return {
        "identifier": "PREF", "expenseIncomeColor": 0, "numberPadType": 1, "firstWeekday": 1,
        "mainCurrency": "TWD", "hideRewardsOnHome": False, "isTotalBalanceAbbreviate": True, **fields,
    }


def _bk_doc(*, exported_at=BACKUP_EXPORTED_AT, accounts=(), groups=(), categories=(), classifications=(),
            projects=(), targets=(), records=(), transfers=(), packages=(), rules=(), conversions=(),
            sharings=(), credit_sharings=(), periods=(), installments=(), preference=None) -> dict:
    return {
        "exported_at": exported_at,
        "info": "version: 205",
        "classes": {
            "AHAccount": list(accounts), "AHAccountGroup": list(groups), "AHCategory": list(categories),
            "AHClassification": list(classifications), "AHProject": list(projects), "AHTarget": list(targets),
            "AHRecord": list(records), "AHTransfer": list(transfers), "AHPackage": list(packages),
            "AHBonusReward": list(rules), "AHCurrencyConversion": list(conversions),
            "AHBonusRewardSharing": list(sharings), "AHCreditSharing": list(credit_sharings),
            "AHPeriod": list(periods), "AHInstallment": list(installments),
            "AHPreference": [preference or _bk_preference()],
            "AHAppConfig": [{"identifier": "CFG", "timeZoneName": "Asia/Taipei"}],
        },
    }


def _bk_write(tmp_path: Path, doc: dict, name: str = "backup.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _bk_data(**kwargs):
    return parse_backup_doc(_bk_doc(**kwargs))


@pytest.fixture()
def backup():
    """Builders for synthetic converter JSON (never real data): backup.doc(...), backup.data(...), backup.write(...)."""
    return SimpleNamespace(
        group=_bk_group, account=_bk_account, category=_bk_category, classification=_bk_classification,
        project=_bk_project, target=_bk_target, record=_bk_record, transfer=_bk_transfer, package=_bk_package,
        period=_bk_period, installment=_bk_installment,
        rule=_bk_rule, conversion=_bk_conversion, preference=_bk_preference, doc=_bk_doc, write=_bk_write,
        data=_bk_data, exported_at=BACKUP_EXPORTED_AT,
    )


FAKE_EXPORTER = Path(__file__).resolve().parent / "fake_exporter.py"


@pytest.fixture()
def fake_exporter(tmp_path):
    """Command that stands in for the Node converter: fake_exporter(doc) copies `doc` to --out; exit_code fails."""

    def build(doc: dict | None = None, *, exit_code: int = 0, message: str = "") -> list[str]:
        prepared = tmp_path / f"prepared-{uuid.uuid4().hex[:8]}.json"
        prepared.write_text(json.dumps(doc or {}, ensure_ascii=False), encoding="utf-8")
        return [sys.executable, str(FAKE_EXPORTER), str(prepared), str(exit_code), message]

    return build


class Seed:
    """Synthetic rows for the write tests (never owner data). Every builder flushes and returns the row."""

    def __init__(self, session):
        self.db = session

    def _add(self, row):
        self.db.add(row)
        self.db.flush()
        return row

    def account(self, name="錢包", currency="TWD", opening="0", **extra) -> Account:
        return make_account(self.db, name, currency, opening, **extra)

    def category(self, name, kind="expense", parent=None, **extra) -> Category:
        return self._add(Category(kind=kind, name=name, parent_id=parent.id if parent else None, **extra))

    def project(self, name) -> Project:
        return self._add(Project(name=name))

    def counterparty(self, name) -> Counterparty:
        return self._add(Counterparty(name=name))

    def group(self, kind="split", **extra) -> EntryGroup:
        return self._add(EntryGroup(kind=kind, **extra))

    def rule(self, account, name="回饋", *, enabled=True, starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31), **extra) -> RewardRule:
        return self._add(
            RewardRule(
                account_id=account.id, name=name, method="percent", rate=Decimal("1"), posting="after_window",
                reward_account_id=account.id, is_enabled=enabled, starts_on=starts_on, ends_on=ends_on, **extra,
            )
        )

    def entry(self, account, amount, *, kind="expense", day=date(2026, 9, 1), source="manual", **extra) -> LedgerEntry:
        extra.setdefault("posted_date", day)
        return make_entry(self.db, account, amount, kind=kind, entry_date=day, source=source, **extra)

    def line(self, kind, account, amount, **fields) -> dict:
        return schedule_line(kind, account, amount, **fields)

    def definition(self, lines, **fields) -> ScheduleDefinition:
        return make_definition(self.db, lines, **fields)

    def instance(self, definition, seq, day, **fields) -> ScheduleInstance:
        return make_instance(self.db, definition, seq, day, **fields)

    def fx(self, day, base, quote, rate) -> FxRate:
        return self._add(FxRate(date=day, base=base, quote=quote, rate=Decimal(rate), source="test"))


@pytest.fixture()
def dirty_on(db_session):
    """The dirty triggers write only while reconciliation_settings.data.dirty_enabled is true."""
    set_dirty(db_session, True)


@pytest.fixture()
def matcher_off(monkeypatch):
    """submit_revision without the reconcile pass (its hook returns no cases): for tests of the revision, lineage and
    sweep services that hand-craft coverage and count cases (tests/integration/test_reconciliation_service.py covers
    the hook)."""
    from app.services import statement_revision_service

    monkeypatch.setattr(statement_revision_service, "reconciliation_hook", lambda *args: [])


@pytest.fixture()
def seed(db_session):
    """seed.account(...), seed.entry(account, "-100", kind=..., source=..., ...) and friends on db_session."""
    return Seed(db_session)


@pytest.fixture()
def today(monkeypatch):
    """today(date(2026, 10, 3)) fixes ledger_service._today(), the Taipei date every schedule service reads."""
    from app.services import ledger_service

    def set_today(day: date) -> date:
        monkeypatch.setattr(ledger_service, "_today", lambda: day)
        return day

    return set_today
