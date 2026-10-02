# Rebuild Accounting on MOZE Ledger — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the empty legacy accounting-service with an account-based, multi-currency ledger, a MOZE CSV full-replace importer (CLI + REST) that converts foreign-currency rows with cached daily rates, and a read-only accounts view, so the owner can check imported balances against MOZE.

**Architecture:** A pure parser (`moze_csv`) and a pure 3-pass transfer pairer (`transfer_pairing`) feed `moze_import_service`, which takes one PostgreSQL advisory lock, records an `import_run`, fills missing daily rates into the `fx_rate` cache through `fx_rate_service` (fawazahmed0 API) before the ledger transaction, and rewrites all `moze_import` rows in one ledger transaction, converting foreign-currency rows while keeping their original amount. `ledger_service` serves balances and newest-first entry pages with a window-function running balance over the canonical order. The Angular SPA replaces the legacy accounting pages with `/accounting/accounts` and `/accounting/accounts/:id`.

**Tech Stack:** Python 3.13, FastAPI 0.129, SQLAlchemy 2.0.49, Alembic 1.18, requests 2.32 (FX fetch), PostgreSQL 16 (`stonk-postgres-1`), pytest 9; Angular 21 standalone components with signals, Vitest via `ng test`.

**Spec:** `openspec/changes/rebuild-accounting-moze-ledger/` (`proposal.md`, `design.md`, `specs/*/spec.md`)

**In-service API paths.** `frontend/proxy.conf.js` (`"pathRewrite": { "^/api/accounting": "" }`) and Caddy (`handle_path /api/accounting/*` → `172.25.0.1:8000`) both strip the prefix, so the routers serve:

| Public path | In-service path |
|---|---|
| `GET /api/accounting/accounts` | `GET /accounts` |
| `GET /api/accounting/accounts/{id}/entries` | `GET /accounts/{id}/entries` |
| `POST /api/accounting/imports/moze` | `POST /imports/moze` |
| `GET /api/accounting/imports/latest` | `GET /imports/latest` |

## Global Constraints

- `amount` (NUMERIC(20,4), NOT NULL; positive increases the account balance, negative decreases it)
- `currency` (copied from the account at insert; `amount` is always in this currency)
- An account's `currency` SHALL be the `幣種` of its single `初始金額` row. Rows on that account MAY carry a different `幣種`; they are converted as described in "Foreign-currency rows".
- `amount` = `金額 × rate`, rounded half-up to 4 decimal places, where `rate` is the daily rate from the row currency to the account currency on the row's `日期`.
- Rate fetching SHALL happen before the ledger transaction starts.
- The cache SHALL NOT be cleared by imports.
- Transfer pairing SHALL compare the rows' own `幣種` and `金額`, not the converted amounts.
- An account's balance SHALL be `opening_balance + Σ ledger_entry.amount` over its entries.
- The *canonical order* of entries SHALL be `entry_date` ascending, then `entry_time` ascending with NULL times sorting **before** any timed entry on the same date, then `seq` ascending. `seq` is unique, so the order is total.
- Running balances, pagination and every listing SHALL use this one order. Newest-first listings SHALL be its exact reverse.
- In phase 1, the service SHALL expose no endpoint that creates, updates or deletes accounts or entries, other than the import endpoint.
- Every import entry point (CLI and REST) SHALL first acquire one shared PostgreSQL advisory lock (a fixed key), held until the import finishes.
- A refused import SHALL write nothing, not even an `import_run` row. Dry runs SHALL take the same lock.
- Accounts SHALL NOT be deleted by an import. Entries whose `source` is not `moze_import` SHALL NOT be touched. Any error SHALL roll back the entire ledger transaction.
- When the configuration flag `ACCOUNTING_IMPORT_LOCKED = true` is set, the importer SHALL refuse to run.
- Every non-dry-run import attempt SHALL record an `import_run` row. Dry runs SHALL NOT record one.
- Row adjacency SHALL NOT be sufficient on its own; it is only a tie-breaker.
- Both pages SHALL be usable at a 390 px viewport width without horizontal page scrolling.
- Owner data rule (from the change brief): `/home/opc/workspace/MOZE_20261001_170037.csv` is private; it is never copied into the repo, committed, or printed in full. Tests use only small synthetic CSVs built in the tests, and never call the FX API (an autouse fixture blocks `requests.get`; tests pass a fake `http_get`).

## Review Focus

1. Multi-line quoted `描述` fields (268 rows in the real export) must not shift row numbers or break pass-1 adjacency — pinned by `test_quoted_multiline_description_keeps_record_row_numbers` (Task 4) and `test_pass_one_adjacency_counts_records_not_lines` (Task 5).
2. `USDT` is not an ISO-4217 code, so `Intl` currency formatting would throw — pinned by `formats non-ISO MOZE currencies such as USDT without throwing` (Task 13).
3. Filtering entries by kind or date must not change the running balance, which is the true account balance after that entry — pinned by `test_filters_do_not_change_running_balance` (Task 10).
4. Re-importing the same export must not duplicate entries, categories (top-level `parent_id IS NULL` rows need `NULLS NOT DISTINCT`) or projects, and must keep their ids — pinned by `test_reimport_reflects_edits_and_is_idempotent` (Task 6) and `test_top_level_category_names_are_unique_per_kind` (Task 3).
5. A failed import must release the advisory lock; a lock leaked on a pooled connection would refuse every later import until restart — pinned by `test_lock_is_released_after_a_failed_import` (Task 9).

## Known Spec Conflicts

The spec amendments resolved every conflict found while planning:

- Foreign-currency rows are converted (design D11, owner option C).
- `specs/frontend-app-shell/spec.md` carries the MODIFIED delta.
- The real-export scenario counts 130 converted parent entries plus 46 fee children, 176 in total.
- `/accounting/transactions` is in the redirect list.

None remain open.

## File Structure

Backend — `services/accounting-service/`:

| Path | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Pin `sqlalchemy==2.0.49` (2.1 switches the default PG driver to psycopg 3, which is not installed); add `python-multipart` for uploads |
| `app/models/{card,category,payment_method,recurring,transaction}.py` | Delete | Legacy models |
| `app/routers/{cards,categories,payment_methods,recurring,transactions}.py` | Delete | Legacy endpoints |
| `app/schemas/{analytics,card,category,payment_method,recurring,transaction}.py` | Delete | Legacy DTOs |
| `app/services/*.py` (7 legacy files) | Delete | Legacy business logic |
| `tests/integration/test_{cards,categories,payment_methods,recurring_api,transactions}.py`, `tests/unit/test_analytics_logic.py` | Delete | Legacy tests |
| `app/models/ledger.py` | Create | `Account`, `Category`, `Project`, `LedgerEntry` (incl. `original_amount`, `original_currency`, `fx_rate`, `fx_source`), kind/source/fx-source enums |
| `app/models/import_run.py` | Create | `ImportRun` |
| `app/models/fx_rate.py` | Create | `FxRate` daily rate cache (`date`, `base`, `quote`, `rate`, `source`) |
| `app/models/__init__.py` | Modify | Export the new models |
| `app/schemas/__init__.py` | Modify | Empty package marker |
| `app/schemas/ledger.py` | Create | `AccountOut`, `EntryOut`, `EntryPage`, `EntryKind` |
| `app/schemas/imports.py` | Create | `ImportReport` |
| `alembic/versions/5d2e7c9a1b3f_moze_ledger_schema.py` | Create | Drop the 6 empty legacy tables (abort if any has rows), create the ledger schema and `fx_rate`; downgrade restores `8a4c4f9b2d1b` |
| `alembic/env.py` | Modify | Import `app.models`; honour a URL already set on the Alembic config (tests) |
| `app/database.py` | Modify | Add `get_engine()` dependency |
| `app/main.py` | Modify | Register `accounts` and `imports` routers only |
| `app/services/moze_csv.py` | Create | Header check, field parsing, kind mapping, opening balances, account currency from the 初始金額 row → `ParsedFile` |
| `app/services/fx_rate_service.py` | Create | `ensure_rates`: read the `fx_rate` cache, fetch missing days from jsDelivr / pages.dev in parallel, store them |
| `app/services/transfer_pairing.py` | Create | 3-pass pairing → `PairingResult` |
| `app/services/moze_import_service.py` | Create | Full-replace steps 1–6, foreign-currency conversion, report summary, advisory lock, `import_run` lifecycle, CLI |
| `app/services/ledger_service.py` | Create | Balances and entry pages with running balance |
| `app/routers/accounts.py` | Create | `GET /accounts`, `GET /accounts/{id}/entries` |
| `app/routers/imports.py` | Create | `POST /imports/moze`, `GET /imports/latest` |
| `tests/conftest.py` | Modify | Disposable Postgres database per session (+ per-test factory for migrations), `db_session`, `client`, `moze` CSV builders, autouse network block, `fake_http` |
| `tests/unit/test_health.py` | Modify | Assert legacy routes are gone |
| `tests/unit/test_moze_csv.py`, `tests/unit/test_transfer_pairing.py` | Create | Parser and pairing unit tests |
| `tests/integration/test_migration.py`, `test_ledger_model.py`, `test_moze_replace_ledger.py`, `test_moze_renames.py`, `test_fx_rate_service.py`, `test_moze_fx_conversion.py`, `test_moze_import_run.py`, `test_accounts_api.py`, `test_imports_api.py` | Create | Postgres-backed tests |
| `README.md` | Modify | Setup, import CLI, API paths, test database |

Frontend — `frontend/src/app/`:

| Path | Action | Responsibility |
|---|---|---|
| `components/accounting/{card-list,category-list,dashboard,management-center,payment-method-list,recurring-list,transaction-list}/` | Delete | Legacy pages |
| `models/accounting.model.ts` | Replace | Ledger DTO types and kind labels |
| `services/accounting.service.ts` (+ `.spec.ts`) | Replace | `getAccounts`, `getEntries`, `getLatestImport` |
| `components/accounting/format.ts` (+ `.spec.ts`) | Create | `formatAmount`, `isNegative` |
| `components/accounting/accounts/accounts.{ts,html,scss,spec.ts}` | Create | Accounts list page |
| `components/accounting/account-entries/account-entries.{ts,html,scss,spec.ts}` | Create | Entry history page |
| `app.routes.ts`, `app.routes.spec.ts` | Modify / Create | New accounting routes, redirects for the removed pages; route, redirect and dock-highlight tests |
| `components/shell/navigation.ts` | Modify | Accounting group → accounts page + settings |

Outside the repo (operator deploy step, not committed): `/home/opc/workspace/vaultwarden/Caddyfile` `@hub_spa` matcher. Root `.env.example` gains `ACCOUNTING_IMPORT_LOCKED=false`.

## 1. Worktree setup

**Files:** none committed except `services/accounting-service/requirements.txt`.

**Interfaces:** Produces a working `services/accounting-service/.venv`, a `.env` symlink at the worktree root, `frontend/node_modules` and `frontend/src/environments/environment.ts`.

All commands run from `/home/opc/workspace/home-hub-moze`. This shell aliases `rm` and `cp` to their interactive forms; use `git rm`, `rm -f` and `command cp` in every step.

- [ ] 1.1 Link the root `.env` (gitignored; the services and `proxy.conf.js` read `../.env` relative to the repo root). Never print it.

```bash
cd /home/opc/workspace/home-hub-moze
ln -s /home/opc/workspace/home-hub/.env .env
git status --short
```

Expected: `git status --short` prints nothing (`.env` is ignored).

- [ ] 1.2 Pin SQLAlchemy in `services/accounting-service/requirements.txt` so it matches the running service venv (2.0.49). Replace the file content with:

```text
-e ../shared-python-lib
fastapi==0.129.0
uvicorn==0.40.0
requests==2.32.5
alembic==1.18.4
sqlalchemy==2.0.49
```

- [ ] 1.3 Create the venv and install runtime plus test dependencies.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt pytest==9.0.3 httpx==0.28.1
.venv/bin/python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

Expected: `2.0.49`.

- [ ] 1.4 Run the existing suite to prove the baseline.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
```

Expected: `44 passed`.

- [ ] 1.5 Install frontend dependencies and generate `environment.ts`; `set-env.js` also rewrites `angular.json` `allowedHosts`, so restore that file.

```bash
cd /home/opc/workspace/home-hub-moze/frontend
npm ci
node set-env.js
git checkout -- angular.json
npx ng test --watch=false 2>&1 | tail -4
```

Expected: the last lines show `Test Files  … passed` with no failures.

- [ ] 1.6 Commit the pin.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/requirements.txt
git commit -m "build(accounting): pin SQLAlchemy 2.0.49"
```

## 2. Remove the legacy accounting backend

**Files:**
- Delete: `services/accounting-service/app/models/{card,category,payment_method,recurring,transaction}.py`, `app/routers/{cards,categories,payment_methods,recurring,transactions}.py`, `app/schemas/{analytics,card,category,payment_method,recurring,transaction}.py`, `app/services/{accounting_validation,analytics_service,billing_service,card_service,recurring_service,refund_utils,transaction_service}.py`, `tests/integration/test_{cards,categories,payment_methods,recurring_api,transactions}.py`, `tests/unit/test_analytics_logic.py`
- Modify: `app/main.py`, `app/models/__init__.py`, `app/schemas/__init__.py`, `alembic/env.py`
- Test: `tests/unit/test_health.py`

**Interfaces:**
- Consumes: `shared_lib.create_app(title, version, routers, get_db, description, engine, otel_service_name_env, otel_strict) -> FastAPI`
- Produces: `app.main.app` with health routes only; `alembic/env.py` that imports `app.models` as a package and keeps a pre-set `sqlalchemy.url`.

- [ ] 2.1 Write the failing test: append to `services/accounting-service/tests/unit/test_health.py`:

```python


def test_legacy_accounting_routes_are_removed(client: TestClient):
    for path in ("/transactions/", "/categories/", "/cards/", "/payment-methods/", "/recurring/subscriptions"):
        assert client.get(path).status_code == 404, path
```

- [ ] 2.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_health.py
```

Expected: `1 failed, 3 passed` with `AssertionError: /transactions/` / `assert 200 == 404`.

- [ ] 2.3 Delete the legacy code and tests.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service
git rm -q app/models/card.py app/models/category.py app/models/payment_method.py app/models/recurring.py app/models/transaction.py \
  app/routers/cards.py app/routers/categories.py app/routers/payment_methods.py app/routers/recurring.py app/routers/transactions.py \
  app/schemas/analytics.py app/schemas/card.py app/schemas/category.py app/schemas/payment_method.py app/schemas/recurring.py app/schemas/transaction.py \
  app/services/accounting_validation.py app/services/analytics_service.py app/services/billing_service.py app/services/card_service.py \
  app/services/recurring_service.py app/services/refund_utils.py app/services/transaction_service.py \
  tests/integration/test_cards.py tests/integration/test_categories.py tests/integration/test_payment_methods.py \
  tests/integration/test_recurring_api.py tests/integration/test_transactions.py tests/unit/test_analytics_logic.py
: > app/models/__init__.py
: > app/schemas/__init__.py
```

- [ ] 2.4 Replace `services/accounting-service/app/main.py` with:

```python
from shared_lib import create_app

from .database import engine, get_db

app = create_app(
    title="Home Service Hub - Accounting API",
    description="記帳與財務管理微服務。",
    version="2.0.0",
    routers=[],
    get_db=get_db,
    engine=engine,
    otel_service_name_env="OTEL_SERVICE_NAME_ACCOUNTING",
    otel_strict=True,
)
```

- [ ] 2.5 In `services/accounting-service/alembic/env.py`, replace

```python
import app.models.transaction  # noqa: F401
import app.models.card  # noqa: F401
import app.models.category  # noqa: F401
import app.models.payment_method  # noqa: F401
import app.models.recurring  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
```

with

```python
import app.models  # noqa: F401

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
```

(`alembic.ini` has an empty `sqlalchemy.url =`, so production behaviour is unchanged; tests set the URL of their throw-away database.)

- [ ] 2.6 Run the suite.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
```

Expected: `4 passed`.

- [ ] 2.7 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/main.py services/accounting-service/app/models/__init__.py \
  services/accounting-service/app/schemas/__init__.py services/accounting-service/alembic/env.py \
  services/accounting-service/tests/unit/test_health.py
git commit -m "refactor(accounting)!: remove legacy transaction, card and recurring backend"
```

## 3. Ledger schema, migration and Postgres test harness

**Files:**
- Create: `services/accounting-service/app/models/ledger.py`, `app/models/import_run.py`, `app/models/fx_rate.py`, `alembic/versions/5d2e7c9a1b3f_moze_ledger_schema.py`, `tests/integration/test_migration.py`, `tests/integration/test_ledger_model.py`
- Modify: `app/models/__init__.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `ENTRY_KINDS: tuple[str, ...]`, `SYSTEM_KINDS`, `ENTRY_SOURCES`, `FX_SOURCES`, `IMPORT_STATUSES`; ORM classes `Account`, `Category`, `Project`, `LedgerEntry` (with nullable `original_amount NUMERIC(20,4)`, `original_currency VARCHAR(8)`, `fx_rate NUMERIC(20,10)`, `fx_source`), `ImportRun`, `FxRate` (PK `date`, `base`, `quote`); Alembic revision `5d2e7c9a1b3f` (down_revision `8a4c4f9b2d1b`); sequence `ledger_entry_seq_seq`; enum types `entry_kind`, `entry_source`, `import_status`, `fx_source`.
- Produces (fixtures): `pg_engine` (session scope, `Engine` on `accounting_test_<hex>` migrated to head), `db_session` (truncates ledger tables, yields `Session`), `client` (`TestClient` with `get_db` bound to `pg_engine`), `database_factory() -> URL` (fresh empty database, dropped at teardown), `alembic_config(url: URL) -> alembic.config.Config`.

Test-database approach: the existing tests use SQLite `create_all`, which cannot hold `TEXT[]`, `JSONB`, enums or advisory locks. The harness creates a disposable database on the dev Postgres server (`stonk-postgres-1`, credentials read at runtime through `app.database` from the root `.env`; the role has `CREATEDB`), runs `alembic upgrade head`, and drops it with `DROP DATABASE … WITH (FORCE)` at teardown. If the server is unreachable the tests fail loudly instead of skipping.

- [ ] 3.1 Write the failing migration test `services/accounting-service/tests/integration/test_migration.py`:

```python
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

    with pytest.raises(RuntimeError, match="categories, transactions|transactions"):
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
```

- [ ] 3.2 Write the failing model test `services/accounting-service/tests/integration/test_ledger_model.py`:

```python
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Category, LedgerEntry


def test_account_names_are_unique(db_session):
    db_session.add(Account(name="Line Bank", currency="TWD"))
    db_session.commit()

    db_session.add(Account(name="Line Bank", currency="TWD"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_sub_category_name_under_different_main_categories(db_session):
    food = Category(kind="expense", name="飲食")
    social = Category(kind="expense", name="社交")
    db_session.add_all([food, social])
    db_session.flush()
    db_session.add_all(
        [
            Category(kind="expense", parent_id=food.id, name="午餐"),
            Category(kind="expense", parent_id=social.id, name="午餐"),
        ]
    )
    db_session.commit()

    lunches = db_session.query(Category).filter_by(name="午餐").all()
    assert sorted(c.parent_id for c in lunches) == sorted([food.id, social.id])


def test_top_level_category_names_are_unique_per_kind(db_session):
    db_session.add(Category(kind="expense", name="飲食"))
    db_session.commit()

    db_session.add(Category(kind="expense", name="飲食"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_entry_defaults_and_numeric_precision(db_session):
    account = Account(name="錢包", currency="TWD", opening_balance=Decimal("2000"))
    db_session.add(account)
    db_session.flush()
    entry = LedgerEntry(
        account_id=account.id,
        kind="expense",
        amount=Decimal("-80.5"),
        currency="TWD",
        entry_date=date(2026, 9, 1),
        entry_time=time(12, 0),
        source="moze_import",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    assert entry.amount == Decimal("-80.5000")
    assert (entry.original_amount, entry.original_currency, entry.fx_rate, entry.fx_source) == (None, None, None, None)
    assert entry.tags == []
    assert entry.needs_review is False
    assert entry.seq >= 1
    assert entry.created_at is not None


def test_converted_entry_keeps_original_amount_currency_and_rate(db_session):
    card = Account(name="華航卡", currency="TWD")
    db_session.add(card)
    db_session.flush()
    entry = LedgerEntry(
        account_id=card.id, kind="expense", amount=Decimal("-360"), currency="TWD",
        original_amount=Decimal("-1800"), original_currency="JPY", fx_rate=Decimal("0.1987662123"), fx_source="fx_api",
        entry_date=date(2026, 7, 10), source="moze_import",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    assert (entry.amount, entry.original_amount, entry.original_currency) == (Decimal("-360.0000"), Decimal("-1800.0000"), "JPY")
    assert (entry.fx_rate, entry.fx_source) == (Decimal("0.1987662123"), "fx_api")
```

- [ ] 3.3 Replace `services/accounting-service/tests/conftest.py` with the Postgres harness:

```python
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
```

- [ ] 3.4 Run the new tests.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration
```

Expected: `ERROR collecting tests/integration/test_ledger_model.py` with `ImportError: cannot import name 'Account' from 'app.models'`.

- [ ] 3.5 Create `services/accounting-service/app/models/ledger.py`:

```python
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import func

from ..database import Base, TimestampMixin

ENTRY_KINDS = (
    "expense",
    "income",
    "transfer_out",
    "transfer_in",
    "receivable",
    "payable",
    "balance_adjustment",
    "fee",
    "discount",
    "reward",
    "interest",
    "refund",
)
SYSTEM_KINDS = ("fee", "discount", "reward", "interest", "balance_adjustment")
ENTRY_SOURCES = ("moze_import", "manual", "hermes")
FX_SOURCES = ("fx_api", "moze_backup")

entry_kind_enum = Enum(*ENTRY_KINDS, name="entry_kind")
entry_source_enum = Enum(*ENTRY_SOURCES, name="entry_source")
fx_source_enum = Enum(*FX_SOURCES, name="fx_source")


class Account(Base, TimestampMixin):
    __tablename__ = "account"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    currency = Column(String(8), nullable=False)
    opening_balance = Column(Numeric(20, 4), nullable=False, server_default=text("0"))
    is_archived = Column(Boolean, nullable=False, server_default=text("false"))


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "parent_id",
            "name",
            name="uq_category_kind_parent_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(entry_kind_enum, nullable=False)
    parent_id = Column(Integer, ForeignKey("category.id"), nullable=True)
    name = Column(String(64), nullable=False)


class Project(Base):
    __tablename__ = "project"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, unique=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"
    __table_args__ = (
        Index("ix_ledger_entry_account_order", "account_id", "entry_date", "entry_time", "seq"),
        Index("ix_ledger_entry_transfer_group_id", "transfer_group_id"),
        Index("ix_ledger_entry_counterparty", "counterparty"),
        Index("ix_ledger_entry_source", "source"),
    )

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False)
    kind = Column(entry_kind_enum, nullable=False)
    amount = Column(Numeric(20, 4), nullable=False)
    currency = Column(String(8), nullable=False)
    original_amount = Column(Numeric(20, 4), nullable=True)
    original_currency = Column(String(8), nullable=True)
    fx_rate = Column(Numeric(20, 10), nullable=True)
    fx_source = Column(fx_source_enum, nullable=True)
    entry_date = Column(Date, nullable=False)
    entry_time = Column(Time, nullable=True)
    category_id = Column(Integer, ForeignKey("category.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    name = Column(String(128), nullable=True)
    merchant = Column(String(128), nullable=True)
    counterparty = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    parent_entry_id = Column(Integer, ForeignKey("ledger_entry.id"), nullable=True)
    transfer_group_id = Column(Uuid, nullable=True)
    needs_review = Column(Boolean, nullable=False, server_default=text("false"))
    source = Column(entry_source_enum, nullable=False)
    import_run_id = Column(Integer, ForeignKey("import_run.id"), nullable=True)
    seq = Column(
        BigInteger,
        nullable=False,
        unique=True,
        server_default=text("nextval('ledger_entry_seq_seq')"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] 3.6 Create `services/accounting-service/app/models/import_run.py`:

```python
from sqlalchemy import Column, DateTime, Enum, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from ..database import Base

IMPORT_STATUSES = ("running", "succeeded", "failed")


class ImportRun(Base):
    __tablename__ = "import_run"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    status = Column(Enum(*IMPORT_STATUSES, name="import_status"), nullable=False)
    row_count = Column(Integer, nullable=True)
    summary = Column(JSONB, nullable=True)
```

- [ ] 3.7 Create `services/accounting-service/app/models/fx_rate.py`:

```python
from sqlalchemy import Column, Date, Numeric, String

from ..database import Base


class FxRate(Base):
    """Daily historical rate cache: 1 unit of `base` = `rate` units of `quote` on `date`."""

    __tablename__ = "fx_rate"

    date = Column(Date, primary_key=True)
    base = Column(String(8), primary_key=True)
    quote = Column(String(8), primary_key=True)
    rate = Column(Numeric(20, 10), nullable=False)
    source = Column(String(32), nullable=False)
```

Replace `services/accounting-service/app/models/__init__.py` with:

```python
from .ledger import (
    ENTRY_KINDS,
    ENTRY_SOURCES,
    FX_SOURCES,
    SYSTEM_KINDS,
    Account,
    Category,
    LedgerEntry,
    Project,
)
from .import_run import IMPORT_STATUSES, ImportRun
from .fx_rate import FxRate
```

- [ ] 3.8 Run again.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration
```

Expected: head is still `8a4c4f9b2d1b`, so `test_upgrade_replaces_legacy_tables_with_ledger_tables` fails on `assert set(LEDGER_TABLES) <= tables`, `test_upgrade_aborts_when_a_legacy_table_has_rows` fails with `DID NOT RAISE`, and every `db_session` test errors with `relation "ledger_entry" does not exist` (the downgrade test passes trivially until the revision exists).

- [ ] 3.9 Create `services/accounting-service/alembic/versions/5d2e7c9a1b3f_moze_ledger_schema.py`:

```python
"""replace legacy accounting tables with the MOZE ledger schema

Revision ID: 5d2e7c9a1b3f
Revises: 8a4c4f9b2d1b
Create Date: 2026-10-01 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "5d2e7c9a1b3f"
down_revision: Union[str, Sequence[str], None] = "8a4c4f9b2d1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Drop order respects foreign keys (children first).
LEGACY_TABLES = (
    "transactions",
    "subscriptions",
    "installments",
    "credit_cards",
    "payment_methods",
    "categories",
)

ENTRY_KINDS = (
    "expense",
    "income",
    "transfer_out",
    "transfer_in",
    "receivable",
    "payable",
    "balance_adjustment",
    "fee",
    "discount",
    "reward",
    "interest",
    "refund",
)
ENTRY_SOURCES = ("moze_import", "manual", "hermes")
IMPORT_STATUSES = ("running", "succeeded", "failed")
FX_SOURCES = ("fx_api", "moze_backup")

entry_kind = postgresql.ENUM(*ENTRY_KINDS, name="entry_kind", create_type=False)
entry_source = postgresql.ENUM(*ENTRY_SOURCES, name="entry_source", create_type=False)
import_status = postgresql.ENUM(*IMPORT_STATUSES, name="import_status", create_type=False)
fx_source = postgresql.ENUM(*FX_SOURCES, name="fx_source", create_type=False)


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    ]


def _assert_legacy_tables_empty(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    non_empty = [
        table_name
        for table_name in LEGACY_TABLES
        if inspector.has_table(table_name)
        and connection.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table_name}")')).scalar_one()
    ]
    if non_empty:
        raise RuntimeError(
            "refusing to drop non-empty legacy accounting tables: " + ", ".join(non_empty)
        )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_legacy_tables_empty(connection)

    inspector = sa.inspect(connection)
    for table_name in LEGACY_TABLES:
        if inspector.has_table(table_name):
            op.drop_table(table_name)

    postgresql.ENUM(*ENTRY_KINDS, name="entry_kind").create(connection)
    postgresql.ENUM(*ENTRY_SOURCES, name="entry_source").create(connection)
    postgresql.ENUM(*IMPORT_STATUSES, name="import_status").create(connection)
    postgresql.ENUM(*FX_SOURCES, name="fx_source").create(connection)
    op.execute("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT")

    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("opening_balance", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_timestamp_columns(),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", entry_kind, nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "kind",
            "parent_id",
            "name",
            name="uq_category_kind_parent_name",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
    )
    op.create_table(
        "import_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("status", import_status, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "ledger_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", entry_kind, nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("original_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("original_currency", sa.String(8), nullable=True),
        sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True),
        sa.Column("fx_source", fx_source, nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_time", sa.Time(), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("merchant", sa.String(128), nullable=True),
        sa.Column("counterparty", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("parent_entry_id", sa.Integer(), sa.ForeignKey("ledger_entry.id"), nullable=True),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", entry_source, nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_run.id"), nullable=True),
        sa.Column(
            "seq",
            sa.BigInteger(),
            nullable=False,
            unique=True,
            server_default=sa.text("nextval('ledger_entry_seq_seq')"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("ALTER SEQUENCE ledger_entry_seq_seq OWNED BY ledger_entry.seq")
    op.create_index(
        "ix_ledger_entry_account_order",
        "ledger_entry",
        ["account_id", "entry_date", "entry_time", "seq"],
    )
    op.create_index("ix_ledger_entry_transfer_group_id", "ledger_entry", ["transfer_group_id"])
    op.create_index("ix_ledger_entry_counterparty", "ledger_entry", ["counterparty"])
    op.create_index("ix_ledger_entry_source", "ledger_entry", ["source"])
    op.create_table(
        "fx_rate",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("base", sa.String(8), primary_key=True),
        sa.Column("quote", sa.String(8), primary_key=True),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fx_rate")
    op.drop_table("ledger_entry")
    op.drop_table("import_run")
    op.drop_table("project")
    op.drop_table("category")
    op.drop_table("account")
    op.execute("DROP TYPE fx_source")
    op.execute("DROP TYPE import_status")
    op.execute("DROP TYPE entry_source")
    op.execute("DROP TYPE entry_kind")

    # Restore the legacy schema exactly as revision 8a4c4f9b2d1b left it.
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("color", sa.String(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "credit_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("billing_day", sa.Integer(), nullable=True),
        sa.Column("reward_cycle_type", sa.String(), nullable=True),
        sa.Column("alert_threshold", sa.Integer(), nullable=True),
        sa.Column("default_payment_method", sa.String(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("sub_type", sa.String(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "installments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("total_amount", sa.Integer(), nullable=True),
        sa.Column("monthly_amount", sa.Integer(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("total_periods", sa.Integer(), nullable=True),
        sa.Column("remaining_periods", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        *_timestamp_columns(),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("item", sa.String(), nullable=True),
        sa.Column("paid_amount", sa.Integer(), nullable=True),
        sa.Column("transaction_amount", sa.Integer(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("credit_cards.id"), nullable=True),
        sa.Column("transaction_type", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("related_transaction_id", sa.Integer(), sa.ForeignKey("transactions.id"), nullable=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("installment_id", sa.Integer(), sa.ForeignKey("installments.id"), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_categories_id", "categories", ["id"])
    op.create_index("ix_categories_name", "categories", ["name"])
    op.create_index("ix_credit_cards_id", "credit_cards", ["id"])
    op.create_index("ix_credit_cards_name", "credit_cards", ["name"])
    op.create_index("ix_payment_methods_id", "payment_methods", ["id"])
    op.create_index("ix_payment_methods_name", "payment_methods", ["name"])
    op.create_index("ix_subscriptions_id", "subscriptions", ["id"])
    op.create_index("ix_installments_id", "installments", ["id"])
    op.create_index("ix_transactions_id", "transactions", ["id"])
```

- [ ] 3.10 Run the whole suite.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
```

Expected: `12 passed`.

- [ ] 3.11 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/models services/accounting-service/alembic/versions/5d2e7c9a1b3f_moze_ledger_schema.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/integration/test_migration.py \
  services/accounting-service/tests/integration/test_ledger_model.py
git commit -m "feat(accounting): add MOZE ledger schema and Postgres test harness"
```

## 4. MOZE CSV parser

**Files:**
- Create: `services/accounting-service/app/services/moze_csv.py`, `tests/unit/test_moze_csv.py`
- Modify: `tests/conftest.py` (append CSV builders)

**Interfaces:**
- Produces: `MOZE_COLUMNS: tuple[str, ...]`, `RECORD_TYPE_TO_KIND: dict[str, str]`, `class MozeImportError(Exception)`, frozen dataclasses `MozeRow(row_no, account, currency, kind, main_category, sub_category, amount: Decimal, fee: Decimal, discount: Decimal, name, merchant, entry_date: date, entry_time: time | None, project, description, tags: tuple[str, ...], counterparty)`, `AccountSpec(name, currency, opening_balance: Decimal)`, `ParsedFile(rows: tuple[MozeRow, ...], accounts: dict[str, AccountSpec], row_count: int)`, `parse_moze_csv(data: bytes) -> ParsedFile`.
- `AccountSpec.currency` is the `幣種` of the account's single 初始金額 row; `MozeRow.currency` is the row's own `幣種` and may differ (converted in Task 8).
- Produces (fixture): `moze` with `row(account, currency, record_type, amount, *, main, sub, fee, discount, name, merchant, date, time, project, description, tags, counterparty) -> str`, `opening(account, currency, amount) -> str`, `csv(*rows, bom=True, newline="\n", header=MOZE_HEADER) -> bytes`, `header: str`.
- Row numbers are spreadsheet rows: the header is row 1, the first record row 2; a quoted multi-line field stays one row.

- [ ] 4.1 Append the synthetic CSV builders to `services/accounting-service/tests/conftest.py`, and add `from types import SimpleNamespace` below `from pathlib import Path` in its imports:

```python


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
```

- [ ] 4.2 Write the failing test `services/accounting-service/tests/unit/test_moze_csv.py`:

```python
from datetime import date, time
from decimal import Decimal

import pytest

from app.services.moze_csv import MozeImportError, parse_moze_csv


def test_header_with_and_without_bom_and_crlf_is_accepted(moze):
    rows = [moze.opening("錢包", "TWD", "2000"), moze.row("錢包", "TWD", "支出", "-120", main="飲食")]
    for data in (moze.csv(*rows), moze.csv(*rows, bom=False, newline="\r\n")):
        parsed = parse_moze_csv(data)
        assert [r.amount for r in parsed.rows] == [Decimal("-120")]


def test_header_missing_column_is_rejected_naming_it(moze):
    header = moze.header.replace(",對象", "")
    with pytest.raises(MozeImportError, match="missing columns: 對象"):
        parse_moze_csv(moze.csv(header=header))


def test_header_with_unexpected_column_is_rejected_naming_it(moze):
    with pytest.raises(MozeImportError, match="unexpected columns: 備註"):
        parse_moze_csv(moze.csv(header=moze.header + ",備註"))


def test_record_types_map_to_kinds_including_refund(moze):
    types = {
        "支出": "expense", "收入": "income", "轉出": "transfer_out", "轉入": "transfer_in",
        "應收款項": "receivable", "應付款項": "payable", "餘額調整": "balance_adjustment",
        "手續費": "fee", "折扣": "discount", "紅利回饋": "reward", "利息": "interest", "退款": "refund",
    }
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        *[moze.row("錢包", "TWD", record_type, "350", main=record_type) for record_type in types],
    )
    parsed = parse_moze_csv(data)
    assert [r.kind for r in parsed.rows] == list(types.values())
    assert parsed.rows[-1].amount == Decimal("350")


def test_initial_amount_sets_opening_balance_without_a_row(moze):
    parsed = parse_moze_csv(moze.csv(moze.opening("去日本的錢", "JPY", "180000")))
    assert parsed.rows == ()
    assert parsed.row_count == 1
    spec = parsed.accounts["去日本的錢"]
    assert (spec.currency, spec.opening_balance) == ("JPY", Decimal("180000"))


def test_unknown_record_type_fails_naming_value_and_row(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "測試", "-1"))
    with pytest.raises(MozeImportError, match="row 3: unknown 記錄類型 '測試'"):
        parse_moze_csv(data)


def test_missing_opening_balance_row_fails_naming_account(moze):
    data = moze.csv(moze.row("Line Bank", "TWD", "支出", "-1", main="飲食"))
    with pytest.raises(MozeImportError, match="account 'Line Bank' has no 初始金額 row"):
        parse_moze_csv(data)


def test_duplicate_opening_balance_rows_fail_naming_account_and_rows(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "2000"), moze.opening("錢包", "TWD", "1500"))
    with pytest.raises(MozeImportError, match="account '錢包' has 2 初始金額 rows: 2, 3"):
        parse_moze_csv(data)


def test_account_currency_comes_from_its_opening_row(moze):
    data = moze.csv(
        moze.row("華航卡", "JPY", "支出", "-1800", main="飲食"),
        moze.opening("華航卡", "TWD", "0"),
        moze.row("華航卡", "TWD", "支出", "-100", main="飲食"),
    )
    parsed = parse_moze_csv(data)
    assert parsed.accounts["華航卡"].currency == "TWD"
    assert [(r.currency, r.amount) for r in parsed.rows] == [("JPY", Decimal("-1800")), ("TWD", Decimal("-100"))]


def test_field_rules_for_dates_times_decimals_and_tags(moze):
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-266", fee="-3", discount="", time="", tags="#午餐; #公司"),
    )
    row = parse_moze_csv(data).rows[0]
    assert row.entry_date == date(2026, 9, 1)
    assert row.entry_time is None
    assert (row.amount, row.fee, row.discount) == (Decimal("-266"), Decimal("-3"), Decimal("0"))
    assert row.tags == ("午餐", "公司")
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", time="08:05"))
    assert parse_moze_csv(data).rows[0].entry_time == time(8, 5)


def test_invalid_date_fails_naming_row(moze):
    data = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", date="2026-09-01"))
    with pytest.raises(MozeImportError, match="row 3: invalid 日期"):
        parse_moze_csv(data)


def test_quoted_multiline_description_keeps_record_row_numbers(moze):
    # Review focus: 268 real rows have multi-line 描述; row numbers count records, not lines.
    data = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", description='"line one, with comma\nline two"'),
        moze.row("錢包", "TWD", "支出", "-2"),
        moze.row("錢包", "TWD", "測試", "-3"),
    )
    with pytest.raises(MozeImportError, match="row 5: unknown 記錄類型"):
        parse_moze_csv(data)
    ok = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", description='"line one, with comma\nline two"'),
        moze.row("錢包", "TWD", "支出", "-2"),
    )
    rows = parse_moze_csv(ok).rows
    assert [r.row_no for r in rows] == [3, 4]
    assert rows[0].description == "line one, with comma\nline two"
```

- [ ] 4.3 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_moze_csv.py
```

Expected: `ModuleNotFoundError: No module named 'app.services.moze_csv'`.

- [ ] 4.4 Create `services/accounting-service/app/services/moze_csv.py`:

```python
"""Parse a MOZE 16-column CSV export into validated rows (no database access)."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

MOZE_COLUMNS = (
    "帳戶", "幣種", "記錄類型", "主類別", "子類別", "金額", "手續費", "折扣",
    "名稱", "商家", "日期", "時間", "專案", "描述", "標籤", "對象",
)

OPENING_RECORD_TYPE = "初始金額"

RECORD_TYPE_TO_KIND = {
    "支出": "expense",
    "收入": "income",
    "轉出": "transfer_out",
    "轉入": "transfer_in",
    "應收款項": "receivable",
    "應付款項": "payable",
    "餘額調整": "balance_adjustment",
    "手續費": "fee",
    "折扣": "discount",
    "紅利回饋": "reward",
    "利息": "interest",
    "退款": "refund",
}


class MozeImportError(Exception):
    """The file cannot be imported; the message names the offending column, row or account."""


@dataclass(frozen=True)
class MozeRow:
    row_no: int  # spreadsheet row number: header is row 1, first record is row 2
    account: str
    currency: str  # the row's own 幣種; may differ from the account currency
    kind: str
    main_category: str
    sub_category: str
    amount: Decimal
    fee: Decimal
    discount: Decimal
    name: str | None
    merchant: str | None
    entry_date: date
    entry_time: time | None
    project: str | None
    description: str | None
    tags: tuple[str, ...]
    counterparty: str | None


@dataclass(frozen=True)
class AccountSpec:
    name: str
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True)
class ParsedFile:
    rows: tuple[MozeRow, ...]  # every non-opening record, in file order
    accounts: dict[str, AccountSpec]  # every account named in the file, in first-appearance order
    row_count: int  # number of data records, including 初始金額 rows


def _check_header(header: list[str]) -> None:
    missing = [c for c in MOZE_COLUMNS if c not in header]
    unexpected = [c for c in header if c not in MOZE_COLUMNS]
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("missing columns: " + ", ".join(missing))
        if unexpected:
            parts.append("unexpected columns: " + ", ".join(unexpected))
        raise MozeImportError("unsupported MOZE CSV header; " + "; ".join(parts))
    if tuple(header) != MOZE_COLUMNS:
        raise MozeImportError("unsupported MOZE CSV header; columns are out of order")


def _decimal(value: str, column: str, row_no: int) -> Decimal:
    if value.strip() == "":
        return Decimal("0")
    try:
        result = Decimal(value.strip())
    except InvalidOperation:
        raise MozeImportError(f"row {row_no}: invalid {column} '{value}'") from None
    if not result.is_finite():
        raise MozeImportError(f"row {row_no}: invalid {column} '{value}'")
    return result


def _date(value: str, row_no: int) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except ValueError:
        raise MozeImportError(f"row {row_no}: invalid 日期 '{value}' (expected YYYY/MM/DD)") from None


def _time(value: str, row_no: int) -> time | None:
    if value.strip() == "":
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise MozeImportError(f"row {row_no}: invalid 時間 '{value}' (expected HH:MM)") from None


def _tags(value: str) -> tuple[str, ...]:
    tags = (part.strip().lstrip("#").strip() for part in value.split(";"))
    return tuple(tag for tag in tags if tag)


def _text(value: str) -> str | None:
    return value if value.strip() else None


def parse_moze_csv(data: bytes) -> ParsedFile:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MozeImportError("file is not UTF-8 encoded") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        raise MozeImportError("file is empty")
    _check_header(header)

    rows: list[MozeRow] = []
    openings: dict[str, list[tuple[int, str, Decimal]]] = {}
    first_seen: dict[str, int] = {}
    row_count = 0

    for row_no, record in enumerate(reader, start=2):
        if not record:
            continue
        row_count += 1
        if len(record) != len(MOZE_COLUMNS):
            raise MozeImportError(f"row {row_no}: expected {len(MOZE_COLUMNS)} fields, got {len(record)}")
        f = dict(zip(MOZE_COLUMNS, record))
        account, currency, record_type = f["帳戶"], f["幣種"], f["記錄類型"]

        first_seen.setdefault(account, row_no)

        if record_type == OPENING_RECORD_TYPE:
            openings.setdefault(account, []).append((row_no, currency, _decimal(f["金額"], "金額", row_no)))
            continue

        kind = RECORD_TYPE_TO_KIND.get(record_type)
        if kind is None:
            raise MozeImportError(f"row {row_no}: unknown 記錄類型 '{record_type}'")

        rows.append(
            MozeRow(
                row_no=row_no,
                account=account,
                currency=currency,
                kind=kind,
                main_category=f["主類別"].strip(),
                sub_category=f["子類別"].strip(),
                amount=_decimal(f["金額"], "金額", row_no),
                fee=_decimal(f["手續費"], "手續費", row_no),
                discount=_decimal(f["折扣"], "折扣", row_no),
                name=_text(f["名稱"]),
                merchant=_text(f["商家"]),
                entry_date=_date(f["日期"], row_no),
                entry_time=_time(f["時間"], row_no),
                project=_text(f["專案"]),
                description=_text(f["描述"]),
                tags=_tags(f["標籤"]),
                counterparty=_text(f["對象"]),
            )
        )

    problems = []
    for account in first_seen:
        found = openings.get(account, [])
        if not found:
            problems.append(f"account '{account}' has no 初始金額 row (first used on row {first_seen[account]})")
        elif len(found) > 1:
            row_list = ", ".join(str(row_no) for row_no, _, _ in found)
            problems.append(f"account '{account}' has {len(found)} 初始金額 rows: {row_list}")
    if problems:
        raise MozeImportError("; ".join(problems))

    # The account currency is the 幣種 of its single 初始金額 row; other rows may differ (converted later).
    accounts = {
        account: AccountSpec(account, openings[account][0][1], openings[account][0][2])
        for account in first_seen
    }
    return ParsedFile(rows=tuple(rows), accounts=accounts, row_count=row_count)
```

- [ ] 4.5 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_moze_csv.py
```

Expected: `12 passed`.

- [ ] 4.6 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/moze_csv.py services/accounting-service/tests/unit/test_moze_csv.py \
  services/accounting-service/tests/conftest.py
git commit -m "feat(accounting): parse and validate MOZE CSV exports"
```

## 5. Transfer pairing

**Files:**
- Create: `services/accounting-service/app/services/transfer_pairing.py`, `tests/unit/test_transfer_pairing.py`

**Interfaces:**
- Consumes: `MozeRow` (Task 4).
- Produces: frozen dataclass `PairingResult(group_by_row: dict[int, uuid.UUID], unpaired_rows: tuple[int, ...], pass_counts: tuple[int, int, int])`, `pair_transfers(rows: Sequence[MozeRow]) -> PairingResult`.
- Rules (spec "Transfer pairing"): pass 1 = the record at `row_no + 1` is an unpaired 轉入 with the same date, time and currency and the exact negated amount; pass 2 = same slot and currency, exact negation, exactly one candidate each way; pass 3 = same slot, different currency, exactly one candidate each way. A paired row is never reconsidered.

- [ ] 5.1 Write the failing test `services/accounting-service/tests/unit/test_transfer_pairing.py`:

```python
from app.services.moze_csv import parse_moze_csv
from app.services.transfer_pairing import pair_transfers


def _parse(moze, *rows):
    openings = [moze.opening(name, cur, "0") for name, cur in (("A", "TWD"), ("B", "TWD"), ("C", "TWD"), ("J", "JPY"))]
    return parse_moze_csv(moze.csv(*openings, *rows)).rows


def test_interleaved_transfers_in_one_minute_pair_by_amount(moze):
    plus_30000, minus_30000, plus_7500, minus_7500 = _parse(
        moze,
        moze.row("A", "TWD", "轉入", "30000"),
        moze.row("B", "TWD", "轉出", "-30000"),
        moze.row("C", "TWD", "轉入", "7500"),
        moze.row("A", "TWD", "轉出", "-7500"),
    )
    result = pair_transfers([plus_30000, minus_30000, plus_7500, minus_7500])
    groups = result.group_by_row
    assert groups[minus_30000.row_no] == groups[plus_30000.row_no]
    assert groups[minus_7500.row_no] == groups[plus_7500.row_no]
    assert groups[minus_30000.row_no] != groups[plus_7500.row_no]
    assert result.unpaired_rows == ()
    assert result.pass_counts == (0, 2, 0)


def test_adjacent_opposite_pair_uses_pass_one(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-100"), moze.row("B", "TWD", "轉入", "100"))
    result = pair_transfers(rows)
    assert result.pass_counts == (1, 0, 0)
    assert len(set(result.group_by_row.values())) == 1


def test_non_adjacent_transfer_is_paired_by_amount(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-22000"),
        moze.row("A", "TWD", "支出", "-1", main="飲食"),
        moze.row("A", "TWD", "支出", "-2", main="飲食"),
        moze.row("B", "TWD", "轉入", "22000"),
    )
    result = pair_transfers(rows)
    out_row, in_row = rows[0].row_no, rows[3].row_no
    assert result.group_by_row[out_row] == result.group_by_row[in_row]
    assert result.pass_counts == (0, 1, 0)


def test_cross_currency_transfer_is_paired_when_unique(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-10000"), moze.row("J", "JPY", "轉入", "46000"))
    result = pair_transfers(rows)
    assert result.group_by_row[rows[0].row_no] == result.group_by_row[rows[1].row_no]
    assert result.pass_counts == (0, 0, 1)


def test_ambiguous_candidates_leave_all_legs_unpaired(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500"),
        moze.row("B", "TWD", "轉出", "-500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("A", "TWD", "轉入", "500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("B", "TWD", "轉入", "500"),
    )
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 4


def test_one_sided_uniqueness_is_not_enough(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500"),
        moze.row("B", "TWD", "轉出", "-500"),
        moze.row("C", "TWD", "支出", "-1", main="飲食"),
        moze.row("C", "TWD", "轉入", "500"),
    )
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 3


def test_same_currency_legs_that_are_not_exact_negations_stay_unpaired(moze):
    rows = _parse(moze, moze.row("A", "TWD", "轉出", "-500"), moze.row("B", "TWD", "轉入", "499"))
    result = pair_transfers(rows)
    assert result.group_by_row == {}
    assert len(result.unpaired_rows) == 2


def test_different_minute_is_never_paired(moze):
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-500", time="10:00"),
        moze.row("B", "TWD", "轉入", "500", time="10:01"),
    )
    assert pair_transfers(rows).group_by_row == {}


def test_pass_one_adjacency_counts_records_not_lines(moze):
    # Review focus: a multi-line 描述 on the 轉出 must not break adjacency.
    rows = _parse(
        moze,
        moze.row("A", "TWD", "轉出", "-100", description='"first line\nsecond line"'),
        moze.row("B", "TWD", "轉入", "100"),
    )
    assert pair_transfers(rows).pass_counts == (1, 0, 0)
```

- [ ] 5.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_transfer_pairing.py
```

Expected: `ModuleNotFoundError: No module named 'app.services.transfer_pairing'`.

- [ ] 5.3 Create `services/accounting-service/app/services/transfer_pairing.py`:

```python
"""Pair MOZE 轉出/轉入 rows into transfers (design D5). Pure function, no database access."""

import uuid
from dataclasses import dataclass
from typing import Sequence

from .moze_csv import MozeRow


@dataclass(frozen=True)
class PairingResult:
    group_by_row: dict[int, uuid.UUID]  # row_no -> transfer_group_id, for every paired leg
    unpaired_rows: tuple[int, ...]  # row_no of every leg left unpaired, in file order
    pass_counts: tuple[int, int, int]  # transfers paired in pass 1, 2 and 3


def _same_slot(a: MozeRow, b: MozeRow) -> bool:
    return a.entry_date == b.entry_date and a.entry_time == b.entry_time


def _opposite(a: MozeRow, b: MozeRow) -> bool:
    return a.currency == b.currency and a.amount == -b.amount


def _cross(a: MozeRow, b: MozeRow) -> bool:
    return a.currency != b.currency


def pair_transfers(rows: Sequence[MozeRow]) -> PairingResult:
    legs = [r for r in rows if r.kind in ("transfer_out", "transfer_in")]
    by_row = {r.row_no: r for r in legs}
    outs = [r for r in legs if r.kind == "transfer_out"]
    ins = [r for r in legs if r.kind == "transfer_in"]
    partner: dict[int, int] = {}

    def free(r: MozeRow) -> bool:
        return r.row_no not in partner

    def unique_matches(match) -> list[tuple[MozeRow, MozeRow]]:
        found = []
        for o in outs:
            if not free(o):
                continue
            candidates = [i for i in ins if free(i) and _same_slot(o, i) and match(o, i)]
            if len(candidates) != 1:
                continue
            i = candidates[0]
            counterparts = [p for p in outs if free(p) and _same_slot(p, i) and match(p, i)]
            if len(counterparts) == 1:
                found.append((o, i))
        return found

    counts = [0, 0, 0]

    # Pass 1: the record right after the 轉出 is its exact opposite.
    for o in outs:
        nxt = by_row.get(o.row_no + 1)
        if nxt is not None and nxt.kind == "transfer_in" and free(nxt) and _same_slot(o, nxt) and _opposite(o, nxt):
            partner[o.row_no], partner[nxt.row_no] = nxt.row_no, o.row_no
            counts[0] += 1

    # Pass 2: same currency, exact opposite, unique in both directions.
    # Pass 3: different currency, unique in both directions.
    for pass_index, match in ((1, _opposite), (2, _cross)):
        for o, i in unique_matches(match):
            partner[o.row_no], partner[i.row_no] = i.row_no, o.row_no
            counts[pass_index] += 1

    group_by_row: dict[int, uuid.UUID] = {}
    for o in outs:
        if not free(o):
            group = uuid.uuid4()
            group_by_row[o.row_no] = group
            group_by_row[partner[o.row_no]] = group
    unpaired = tuple(r.row_no for r in legs if free(r))
    return PairingResult(group_by_row=group_by_row, unpaired_rows=unpaired, pass_counts=tuple(counts))
```

- [ ] 5.4 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_transfer_pairing.py
```

Expected: `9 passed`.

- [ ] 5.5 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/transfer_pairing.py services/accounting-service/tests/unit/test_transfer_pairing.py
git commit -m "feat(accounting): pair MOZE transfers in three conservative passes"
```

## 6. Full-replace ledger write

**Files:**
- Create: `services/accounting-service/app/services/moze_import_service.py`, `tests/integration/test_moze_replace_ledger.py`

**Interfaces:**
- Consumes: `ParsedFile`, `MozeRow` (Task 4); `PairingResult` (Task 5); models (Task 3).
- Produces: `SOURCE = "moze_import"`, `SYSTEM_CATEGORY_NAMES: dict[str, str]`, `replace_ledger(session: Session, parsed: ParsedFile, pairing: PairingResult, import_run_id: int | None) -> dict` — runs full-replace steps 2, 3, 5 and 6 inside the caller's transaction (no commit) and returns the summary `{"kind_counts": dict[str, int], "accounts_created": list[str], "accounts": list[{name, currency, opening_balance, entry_count, balance, converted_entry_count, converted_amount}], "unpaired_transfers": list[{row, account, kind, date, time, amount, currency}], "pairing": {"pass1", "pass2", "pass3"}}` with money as 4-decimal strings.
- `seq` values are taken from `ledger_entry_seq_seq` in one block, sorted, and assigned in file order: parent, then its `fee` child, then its `discount` child.

- [ ] 6.1 Write the failing test `services/accounting-service/tests/integration/test_moze_replace_ledger.py`:

```python
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Category, LedgerEntry, Project
from app.services.moze_csv import parse_moze_csv
from app.services.moze_import_service import replace_ledger
from app.services.transfer_pairing import pair_transfers


def _import(session, data: bytes) -> dict:
    parsed = parse_moze_csv(data)
    summary = replace_ledger(session, parsed, pair_transfers(parsed.rows), None)
    session.commit()
    return summary


def _entries(session, account_name: str) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry)
            .join(Account, Account.id == LedgerEntry.account_id)
            .where(Account.name == account_name)
            .order_by(LedgerEntry.seq)
        )
    )


def _category_paths(session) -> set[str]:
    categories = {c.id: c for c in session.scalars(select(Category))}
    return {
        f"{c.kind}:{categories[c.parent_id].name}/{c.name}" if c.parent_id else f"{c.kind}:{c.name}"
        for c in categories.values()
    }


def test_opening_balance_entries_and_currency(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("去日本的錢", "JPY", "180000"),
            moze.row("去日本的錢", "JPY", "支出", "-1500", main="飲食", sub="午餐"),
        ),
    )
    account = db_session.scalar(select(Account).where(Account.name == "去日本的錢"))
    assert (account.currency, account.opening_balance) == ("JPY", Decimal("180000.0000"))
    [entry] = _entries(db_session, "去日本的錢")
    assert (entry.kind, entry.amount, entry.currency, entry.source) == ("expense", Decimal("-1500.0000"), "JPY", "moze_import")
    assert (entry.original_amount, entry.original_currency, entry.fx_rate, entry.fx_source) == (None, None, None, None)
    assert summary["accounts_created"] == ["去日本的錢"]
    assert summary["accounts"] == [
        {
            "name": "去日本的錢", "currency": "JPY", "opening_balance": "180000.0000", "entry_count": 1,
            "balance": "178500.0000", "converted_entry_count": 0, "converted_amount": "0.0000",
        }
    ]
    assert summary["kind_counts"] == {"expense": 1}


def test_fee_and_discount_columns_become_children_right_after_parent(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("信用卡", "TWD", "0"),
            moze.row("信用卡", "TWD", "支出", "-266", fee="-3", discount="10", main="飲食"),
            moze.row("信用卡", "TWD", "支出", "-50", main="飲食"),
        ),
    )
    expense, fee, discount, second = _entries(db_session, "信用卡")
    assert (expense.kind, expense.amount) == ("expense", Decimal("-266.0000"))
    assert (fee.kind, fee.amount, fee.parent_entry_id) == ("fee", Decimal("-3.0000"), expense.id)
    assert (discount.kind, discount.amount, discount.parent_entry_id) == ("discount", Decimal("10.0000"), expense.id)
    assert [expense.seq + 1, expense.seq + 2, expense.seq + 3] == [fee.seq, discount.seq, second.seq]
    assert (fee.entry_date, fee.entry_time) == (expense.entry_date, expense.entry_time)
    assert db_session.get(Category, fee.category_id).name == "手續費"
    assert sum(e.amount for e in (expense, fee, discount)) == Decimal("-259")


def test_categories_projects_and_refund(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("錢包", "TWD", "0"),
            moze.row("錢包", "TWD", "應收款項", "-100", main="應收款項", sub="代付", project="日本行"),
            moze.row("錢包", "TWD", "應收款項", "-200", main="應收款項", sub="報帳"),
            moze.row("錢包", "TWD", "紅利回饋", "5", main="紅利回饋", sub="紅利回饋"),
            moze.row("錢包", "TWD", "收入", "1000", main="收入", sub="收入"),
            moze.row("錢包", "TWD", "退款", "350", main="飲食", sub="午餐"),
        ),
    )
    assert _category_paths(db_session) == {
        "receivable:應收款項", "receivable:應收款項/代付", "receivable:應收款項/報帳",
        "reward:紅利回饋", "income:收入", "refund:飲食", "refund:飲食/午餐",
    }
    entries = _entries(db_session, "錢包")
    assert db_session.get(Category, entries[0].category_id).name == "代付"
    assert db_session.get(Project, entries[0].project_id).name == "日本行"
    assert (entries[-1].kind, entries[-1].amount) == ("refund", Decimal("350.0000"))


def test_paired_and_unpaired_transfers(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("A", "TWD", "0"),
            moze.opening("B", "TWD", "0"),
            moze.row("A", "TWD", "轉出", "-1000", main="轉帳"),
            moze.row("B", "TWD", "轉入", "1000", main="轉帳"),
            moze.row("A", "TWD", "轉出", "-500", main="轉帳", time="13:00"),
        ),
    )
    out_leg, lonely = _entries(db_session, "A")
    [in_leg] = _entries(db_session, "B")
    assert out_leg.transfer_group_id is not None
    assert out_leg.transfer_group_id == in_leg.transfer_group_id
    assert (out_leg.needs_review, in_leg.needs_review) == (False, False)
    assert (lonely.transfer_group_id, lonely.needs_review) == (None, True)
    assert summary["unpaired_transfers"] == [
        {"row": 6, "account": "A", "kind": "transfer_out", "date": "2026-09-01", "time": "13:00", "amount": "-500.0000", "currency": "TWD"}
    ]
    assert summary["pairing"] == {"pass1": 1, "pass2": 0, "pass3": 0}


def test_reimport_reflects_edits_and_is_idempotent(db_session, moze):
    # Review focus: re-importing must not duplicate entries, categories or projects.
    first = moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", "-120", main="飲食", sub="午餐", project="日常"),
    )
    _import(db_session, first)
    ids_before = (
        db_session.scalar(select(Account.id)),
        sorted(db_session.scalars(select(Category.id))),
        sorted(db_session.scalars(select(Project.id))),
    )
    _import(db_session, first)
    ids_after = (
        db_session.scalar(select(Account.id)),
        sorted(db_session.scalars(select(Category.id))),
        sorted(db_session.scalars(select(Project.id))),
    )
    assert ids_after == ids_before
    assert [e.amount for e in _entries(db_session, "錢包")] == [Decimal("-120.0000")]

    edited = moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", "-150", main="飲食", sub="午餐", project="日常"),
    )
    _import(db_session, edited)
    assert [e.amount for e in _entries(db_session, "錢包")] == [Decimal("-150.0000")]


def test_category_cleanup_keeps_ancestors_and_removes_unused_branches(db_session, moze):
    _import(
        db_session,
        moze.csv(
            moze.opening("錢包", "TWD", "0"),
            moze.row("錢包", "TWD", "支出", "-1", main="飲食", sub="午餐"),
            moze.row("錢包", "TWD", "支出", "-2", main="娛樂", sub="電影", project="舊專案"),
        ),
    )
    _import(
        db_session,
        moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", main="飲食", sub="午餐")),
    )
    assert _category_paths(db_session) == {"expense:飲食", "expense:飲食/午餐"}
    assert list(db_session.scalars(select(Project.name))) == []


def test_entries_from_other_sources_are_untouched(db_session, moze):
    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-1", main="飲食")))
    account = db_session.scalar(select(Account).where(Account.name == "錢包"))
    manual = LedgerEntry(
        account_id=account.id, kind="expense", amount=Decimal("-7"), currency="TWD",
        entry_date=date(2026, 9, 1), source="manual",
    )
    db_session.add(manual)
    db_session.commit()

    _import(db_session, moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "支出", "-2", main="飲食")))
    assert sorted((e.source, e.amount) for e in _entries(db_session, "錢包")) == [
        ("manual", Decimal("-7.0000")),
        ("moze_import", Decimal("-2.0000")),
    ]
```

- [ ] 6.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_replace_ledger.py
```

Expected: `ModuleNotFoundError: No module named 'app.services.moze_import_service'`.

- [ ] 6.3 Create `services/accounting-service/app/services/moze_import_service.py`:

```python
"""MOZE CSV import: transactional full replace of MOZE-sourced ledger data (design D6)."""

from collections import Counter
from decimal import Decimal
from typing import Mapping, Sequence

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..models import SYSTEM_KINDS, Account, Category, LedgerEntry, Project
from .moze_csv import MozeRow, ParsedFile
from .transfer_pairing import PairingResult

SOURCE = "moze_import"

SYSTEM_CATEGORY_NAMES = {
    "fee": "手續費",
    "discount": "折扣",
    "reward": "紅利回饋",
    "interest": "利息",
    "balance_adjustment": "餘額調整",
}


def _money(value: Decimal) -> str:
    return f"{Decimal(value):.4f}"


class _Lookup:
    """Get-or-create cache for categories and projects inside the ledger transaction."""

    def __init__(self, session: Session):
        self.session = session
        self.categories = {(c.kind, c.parent_id, c.name): c.id for c in session.scalars(select(Category))}
        self.projects = {p.name: p.id for p in session.scalars(select(Project))}

    def category_id(self, kind: str, main: str, sub: str) -> int | None:
        if kind in SYSTEM_KINDS:
            return self._category(kind, None, SYSTEM_CATEGORY_NAMES[kind])
        if not main:
            return None
        main_id = self._category(kind, None, main)
        if sub and sub != main:
            return self._category(kind, main_id, sub)
        return main_id

    def _category(self, kind: str, parent_id: int | None, name: str) -> int:
        key = (kind, parent_id, name)
        if key not in self.categories:
            category = Category(kind=kind, parent_id=parent_id, name=name)
            self.session.add(category)
            self.session.flush()
            self.categories[key] = category.id
        return self.categories[key]

    def project_id(self, name: str | None) -> int | None:
        if name is None:
            return None
        if name not in self.projects:
            project = Project(name=name)
            self.session.add(project)
            self.session.flush()
            self.projects[name] = project.id
        return self.projects[name]


def _insert_entries(
    session: Session,
    rows: Sequence[MozeRow],
    accounts: Mapping[str, Account],
    pairing: PairingResult,
    import_run_id: int | None,
) -> list[LedgerEntry]:
    lookup = _Lookup(session)
    total = len(rows) + sum(1 for r in rows if r.fee != 0) + sum(1 for r in rows if r.discount != 0)
    seqs = iter(
        sorted(
            session.scalars(
                text("SELECT nextval('ledger_entry_seq_seq') FROM generate_series(1, :n)"), {"n": total}
            )
        )
    )

    parents: list[tuple[MozeRow, LedgerEntry, list[LedgerEntry]]] = []
    for row in rows:
        account = accounts[row.account]
        parent = LedgerEntry(
            account_id=account.id,
            kind=row.kind,
            amount=row.amount,
            currency=account.currency,
            entry_date=row.entry_date,
            entry_time=row.entry_time,
            category_id=lookup.category_id(row.kind, row.main_category, row.sub_category),
            project_id=lookup.project_id(row.project),
            name=row.name,
            merchant=row.merchant,
            counterparty=row.counterparty,
            description=row.description,
            tags=list(row.tags),
            transfer_group_id=pairing.group_by_row.get(row.row_no),
            needs_review=row.row_no in pairing.unpaired_rows,
            source=SOURCE,
            import_run_id=import_run_id,
            seq=next(seqs),
        )
        children = []
        for kind, amount in (("fee", row.fee), ("discount", row.discount)):
            if amount != 0:
                children.append(
                    LedgerEntry(
                        account_id=account.id,
                        kind=kind,
                        amount=amount,
                        currency=account.currency,
                        entry_date=row.entry_date,
                        entry_time=row.entry_time,
                        category_id=lookup.category_id(kind, "", ""),
                        source=SOURCE,
                        import_run_id=import_run_id,
                        seq=next(seqs),
                    )
                )
        parents.append((row, parent, children))

    session.add_all([parent for _, parent, _ in parents])
    session.flush()
    children_all = []
    for _, parent, children in parents:
        for child in children:
            child.parent_entry_id = parent.id
            children_all.append(child)
    session.add_all(children_all)
    session.flush()
    return [parent for _, parent, _ in parents] + children_all


def _delete_unused_categories_and_projects(session: Session) -> None:
    session.execute(
        text(
            "DELETE FROM category c WHERE c.parent_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id)"
        )
    )
    session.execute(
        text(
            "DELETE FROM category c WHERE c.parent_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id) "
            "AND NOT EXISTS (SELECT 1 FROM category child WHERE child.parent_id = c.id)"
        )
    )
    session.execute(
        text(
            "DELETE FROM project p "
            "WHERE NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.project_id = p.id)"
        )
    )


def _account_report(account: Account, totals: Mapping[int, tuple[int, Decimal, int, Decimal]]) -> dict:
    count, total, converted_count, converted_total = totals.get(account.id, (0, Decimal("0"), 0, Decimal("0")))
    return {
        "name": account.name,
        "currency": account.currency,
        "opening_balance": _money(account.opening_balance),
        "entry_count": count,
        "balance": _money(account.opening_balance + total),
        "converted_entry_count": converted_count,
        "converted_amount": _money(converted_total),
    }


def _leg_report(row: MozeRow) -> dict:
    return {
        "row": row.row_no,
        "account": row.account,
        "kind": row.kind,
        "date": row.entry_date.isoformat(),
        "time": row.entry_time.strftime("%H:%M") if row.entry_time else None,
        "amount": _money(row.amount),
        "currency": row.currency,
    }


def replace_ledger(
    session: Session,
    parsed: ParsedFile,
    pairing: PairingResult,
    import_run_id: int | None,
) -> dict:
    """Run full-replace steps 2, 3, 5 and 6 in the caller's transaction and return the report summary."""
    session.execute(delete(LedgerEntry).where(LedgerEntry.source == SOURCE))

    existing = {a.name: a for a in session.scalars(select(Account))}
    accounts: dict[str, Account] = {}
    created = []
    for name, spec in parsed.accounts.items():
        account = existing.get(name)
        if account is None:
            account = Account(name=name, currency=spec.currency, opening_balance=spec.opening_balance)
            session.add(account)
            created.append(name)
        else:
            account.currency = spec.currency
            account.opening_balance = spec.opening_balance
            account.is_archived = False
        accounts[name] = account
    session.flush()

    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id)

    _delete_unused_categories_and_projects(session)
    session.flush()

    is_converted = LedgerEntry.fx_source.is_not(None)
    totals = {
        account_id: (count, total, converted_count, converted_total)
        for account_id, count, total, converted_count, converted_total in session.execute(
            select(
                LedgerEntry.account_id,
                func.count(),
                func.sum(LedgerEntry.amount),
                func.count().filter(is_converted),
                func.coalesce(func.sum(LedgerEntry.amount).filter(is_converted), 0),
            ).group_by(LedgerEntry.account_id)
        )
    }
    rows_by_no = {row.row_no: row for row in parsed.rows}
    return {
        "kind_counts": dict(sorted(Counter(entry.kind for entry in inserted).items())),
        "accounts_created": created,
        "accounts": [_account_report(account, totals) for account in accounts.values()],
        "unpaired_transfers": [_leg_report(rows_by_no[row_no]) for row_no in pairing.unpaired_rows],
        "pairing": dict(zip(("pass1", "pass2", "pass3"), pairing.pass_counts)),
    }
```

- [ ] 6.4 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_replace_ledger.py
```

Expected: `7 passed`.

- [ ] 6.5 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/moze_import_service.py \
  services/accounting-service/tests/integration/test_moze_replace_ledger.py
git commit -m "feat(accounting): replace MOZE-sourced ledger data in one transaction"
```

## 7. Account renames and disappearing accounts

**Files:**
- Modify: `services/accounting-service/app/services/moze_import_service.py`
- Create: `tests/integration/test_moze_renames.py`

**Interfaces:**
- Produces: `_apply_renames(session: Session, renames: Mapping[str, str]) -> list[dict[str, str]]` (step 1), `_archive_disappeared_accounts(session: Session, named_in_file: set[str]) -> list[str]` (step 4; returns only accounts newly archived), `replace_ledger(session, parsed, pairing, import_run_id, renames: Mapping[str, str] | None = None) -> dict` whose summary gains `accounts_archived: list[str]` and `accounts_renamed: list[{"from", "to"}]`.
- Raises `MozeImportError` naming the account when `old_name` does not exist or `new_name` already exists as a different account.

- [ ] 7.1 Write the failing test `services/accounting-service/tests/integration/test_moze_renames.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Account, LedgerEntry
from app.services.moze_csv import MozeImportError, parse_moze_csv
from app.services.moze_import_service import replace_ledger
from app.services.transfer_pairing import pair_transfers


def _import(session, data: bytes, renames=None) -> dict:
    parsed = parse_moze_csv(data)
    summary = replace_ledger(session, parsed, pair_transfers(parsed.rows), None, renames)
    session.commit()
    return summary


def _balance(session, name: str) -> Decimal:
    account = session.scalar(select(Account).where(Account.name == name))
    total = session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.account_id == account.id))
    return account.opening_balance + total


def _file(moze, name: str) -> bytes:
    return moze.csv(moze.opening(name, "TWD", "1000"), moze.row(name, "TWD", "支出", "-200", main="飲食"))


def test_rename_with_mapping_keeps_one_account(db_session, moze):
    _import(db_session, _file(moze, "A"))
    old_id = db_session.scalar(select(Account.id).where(Account.name == "A"))

    summary = _import(db_session, _file(moze, "B"), renames={"A": "B"})

    assert list(db_session.scalars(select(Account.name))) == ["B"]
    assert db_session.scalar(select(Account.id).where(Account.name == "B")) == old_id
    assert _balance(db_session, "B") == Decimal("800")
    assert summary["accounts_renamed"] == [{"from": "A", "to": "B"}]
    assert summary["accounts_created"] == []
    assert summary["accounts_archived"] == []


def test_rename_without_mapping_archives_old_account_with_zero_balance(db_session, moze):
    _import(db_session, _file(moze, "A"))

    summary = _import(db_session, _file(moze, "B"))

    old = db_session.scalar(select(Account).where(Account.name == "A"))
    assert (old.is_archived, old.opening_balance) == (True, Decimal("0.0000"))
    assert _balance(db_session, "A") == Decimal("0")
    assert _balance(db_session, "B") == Decimal("800")
    assert summary["accounts_archived"] == ["A"]
    assert summary["accounts_created"] == ["B"]


def test_archived_account_reappearing_is_unarchived(db_session, moze):
    _import(db_session, _file(moze, "A"))
    _import(db_session, _file(moze, "B"))

    _import(db_session, _file(moze, "A"))

    account = db_session.scalar(select(Account).where(Account.name == "A"))
    assert (account.is_archived, _balance(db_session, "A")) == (False, Decimal("800"))


def test_rename_of_unknown_account_fails(db_session, moze):
    with pytest.raises(MozeImportError, match="account 'X' does not exist"):
        _import(db_session, _file(moze, "B"), renames={"X": "B"})


def test_rename_onto_existing_other_account_fails(db_session, moze):
    _import(db_session, moze.csv(moze.opening("A", "TWD", "0"), moze.opening("B", "TWD", "0")))
    with pytest.raises(MozeImportError, match="account 'B' already exists"):
        _import(db_session, _file(moze, "B"), renames={"A": "B"})
```

- [ ] 7.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_renames.py
```

Expected: `5 failed`, each with `TypeError: replace_ledger() takes 4 positional arguments but 5 were given`.

- [ ] 7.3 In `moze_import_service.py`, change the import line `from .moze_csv import MozeRow, ParsedFile` to:

```python
from .moze_csv import MozeImportError, MozeRow, ParsedFile
```

- [ ] 7.4 Insert these two functions immediately above `def _account_report(`:

```python
def _apply_renames(session: Session, renames: Mapping[str, str]) -> list[dict[str, str]]:
    applied = []
    for old_name, new_name in renames.items():
        account = session.scalar(select(Account).where(Account.name == old_name))
        if account is None:
            raise MozeImportError(f"rename {old_name}={new_name}: account '{old_name}' does not exist")
        other = session.scalar(select(Account).where(Account.name == new_name))
        if other is not None and other.id != account.id:
            raise MozeImportError(f"rename {old_name}={new_name}: account '{new_name}' already exists")
        account.name = new_name
        session.flush()
        applied.append({"from": old_name, "to": new_name})
    return applied


def _archive_disappeared_accounts(session: Session, named_in_file: set[str]) -> list[str]:
    archived = []
    for account in session.scalars(select(Account).order_by(Account.id)):
        if account.name in named_in_file:
            continue
        has_entries = session.scalar(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.account_id == account.id)
        )
        if has_entries:
            continue
        if not account.is_archived:
            archived.append(account.name)
        account.opening_balance = Decimal("0")
        account.is_archived = True
    session.flush()
    return archived
```

- [ ] 7.5 In `replace_ledger`, replace the signature and docstring

```python
    import_run_id: int | None,
) -> dict:
    """Run full-replace steps 2, 3, 5 and 6 in the caller's transaction and return the report summary."""
    session.execute(delete(LedgerEntry).where(LedgerEntry.source == SOURCE))
```

with

```python
    import_run_id: int | None,
    renames: Mapping[str, str] | None = None,
) -> dict:
    """Run full-replace steps 1-6 in the caller's transaction and return the report summary."""
    renamed = _apply_renames(session, renames or {})

    session.execute(delete(LedgerEntry).where(LedgerEntry.source == SOURCE))
```

- [ ] 7.6 In `replace_ledger`, replace

```python
        accounts[name] = account
    session.flush()

    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id)
```

with

```python
        accounts[name] = account
    session.flush()

    archived = _archive_disappeared_accounts(session, set(parsed.accounts))

    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id)
```

- [ ] 7.7 In the returned summary, replace `        "accounts_created": created,` with:

```python
        "accounts_created": created,
        "accounts_archived": archived,
        "accounts_renamed": renamed,
```

- [ ] 7.8 Run both ledger-write test files.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_renames.py tests/integration/test_moze_replace_ledger.py
```

Expected: `12 passed`.

- [ ] 7.9 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/moze_import_service.py services/accounting-service/tests/integration/test_moze_renames.py
git commit -m "feat(accounting): support MOZE account renames and archive vanished accounts"
```

## 8. FX rates and foreign-currency conversion

**Files:**
- Create: `services/accounting-service/app/services/fx_rate_service.py`, `tests/integration/test_fx_rate_service.py`, `tests/integration/test_moze_fx_conversion.py`
- Modify: `app/services/moze_import_service.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `FxRate` (Task 3), `MozeImportError`, `ParsedFile` (Task 4), `replace_ledger` (Task 7).
- Produces (`fx_rate_service`): `PRIMARY_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json"`, `FALLBACK_URL_TEMPLATE = "https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json"` (same URLs and source labels as `stock-portfolio-service/app/services/fx_rate_service.py`); `HttpGet = Callable[..., Response]`; `RateKey = tuple[date, str, str]` (day, base = row currency, quote = account currency); `class FxRateUnavailableError(MozeImportError)`; `ensure_rates(session: Session, needed: Iterable[RateKey], *, persist: bool, http_get: HttpGet | None = None) -> dict[RateKey, Decimal]` — cached rates make no request; missing days are fetched once per (day, base), up to 8 in parallel (the real export needs about 50 days, roughly 1 s each); rates are quantised to 10 decimals; with `persist` every fetched rate is inserted (`ON CONFLICT DO NOTHING`) and committed even when another rate fails, then `FxRateUnavailableError("no FX rate for JPY→TWD on 2025-06-01: …")` is raised.
- Produces (`moze_import_service`): `required_rates(parsed: ParsedFile) -> set[tuple[date, str, str]]`; `replace_ledger(session, parsed, pairing, import_run_id, renames=None, rates: Mapping[tuple[date, str, str], Decimal] | None = None) -> dict`. A row whose `幣種` differs from its account gets `amount = 金額 × rate` (ROUND_HALF_UP, 4 dp), `original_amount = 金額`, `original_currency = 幣種`, `fx_rate`, `fx_source = 'fx_api'`; its fee/discount children use the same rate and keep their own `original_amount`; a missing rate raises `MozeImportError("row N: no FX rate for …")`. Pairing is untouched (it runs on the parsed rows).
- Produces (fixtures): autouse `no_network` (any real `requests.get` fails the test); `fake_http(responses: dict[str, tuple[int, dict | None]]) -> FakeHttp`, a callable `(url, timeout) -> response` answering URLs that contain a key, recording `calls`, and raising `requests.ConnectionError` otherwise.

- [ ] 8.1 In `services/accounting-service/tests/conftest.py`, add `from requests.exceptions import ConnectionError as RequestsConnectionError` below `from fastapi.testclient import TestClient`, add `from app.services import fx_rate_service` below `from app.main import app`, and insert this block immediately above `@pytest.fixture()` / `def database_factory():`:

```python
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach the real FX API; pass a fake http_get instead."""

    def _blocked(url, *args, **kwargs):
        raise AssertionError(f"unexpected network call in tests: {url}")

    monkeypatch.setattr(fx_rate_service.requests, "get", _blocked)


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
```

- [ ] 8.2 Write the failing test `services/accounting-service/tests/integration/test_fx_rate_service.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import FxRate
from app.services.fx_rate_service import FxRateUnavailableError, ensure_rates

DAY = date(2026, 7, 10)
JPY_DAY_URL = "currency-api@2026-07-10/v1/currencies/jpy.json"


def _jpy(rate: float) -> tuple[int, dict]:
    return 200, {"date": DAY.isoformat(), "jpy": {"twd": rate, "usd": 0.0068}}


def test_missing_rate_is_fetched_from_jsdelivr_and_cached(db_session, fake_http):
    http = fake_http({"cdn.jsdelivr.net/npm/@fawazahmed0/" + JPY_DAY_URL: _jpy(0.2)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.2")}
    assert len(http.calls) == 1
    cached = db_session.scalar(select(FxRate))
    assert (cached.date, cached.base, cached.quote, cached.rate, cached.source) == (
        DAY, "JPY", "TWD", Decimal("0.2000000000"), "fawazahmed0-jsdelivr",
    )


def test_fallback_source_is_used_when_jsdelivr_fails(db_session, fake_http):
    http = fake_http({
        "cdn.jsdelivr.net": (503, None),
        "2026-07-10.currency-api.pages.dev/v1/currencies/jpy.json": _jpy(0.19876621234),
    })

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates[(DAY, "JPY", "TWD")] == Decimal("0.1987662123")
    assert db_session.scalar(select(FxRate.source)) == "fawazahmed0-pages"


def test_cached_rate_makes_no_network_request(db_session, fake_http):
    db_session.add(FxRate(date=DAY, base="JPY", quote="TWD", rate=Decimal("0.2"), source="fawazahmed0-jsdelivr"))
    db_session.commit()
    http = fake_http({})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=True, http_get=http)

    assert rates == {(DAY, "JPY", "TWD"): Decimal("0.2000000000")}
    assert http.calls == []


def test_unobtainable_rate_names_pair_and_date_and_keeps_fetched_ones(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    with pytest.raises(FxRateUnavailableError, match="no FX rate for JPY→TWD on 2025-06-01"):
        ensure_rates(db_session, [(DAY, "JPY", "TWD"), (date(2025, 6, 1), "JPY", "TWD")], persist=True, http_get=http)

    assert [r.date for r in db_session.scalars(select(FxRate))] == [DAY]


def test_without_persist_nothing_is_written(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    rates = ensure_rates(db_session, [(DAY, "JPY", "TWD")], persist=False, http_get=http)

    assert rates[(DAY, "JPY", "TWD")] == Decimal("0.2")
    db_session.rollback()
    assert db_session.scalar(select(FxRate)) is None


def test_one_request_per_day_and_base(db_session, fake_http):
    http = fake_http({JPY_DAY_URL: _jpy(0.2)})

    ensure_rates(db_session, [(DAY, "JPY", "TWD"), (DAY, "JPY", "USD")], persist=True, http_get=http)

    assert len(http.calls) == 1
```

- [ ] 8.3 Write the failing test `services/accounting-service/tests/integration/test_moze_fx_conversion.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Account, LedgerEntry
from app.services.moze_csv import MozeImportError, parse_moze_csv
from app.services.moze_import_service import replace_ledger, required_rates
from app.services.transfer_pairing import pair_transfers

DAY = date(2026, 7, 10)


def _import(session, data: bytes, rates) -> dict:
    parsed = parse_moze_csv(data)
    summary = replace_ledger(session, parsed, pair_transfers(parsed.rows), None, None, rates)
    session.commit()
    return summary


def _entries(session, account_name: str) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry)
            .join(Account, Account.id == LedgerEntry.account_id)
            .where(Account.name == account_name)
            .order_by(LedgerEntry.seq)
        )
    )


def _card(moze, *rows: str) -> bytes:
    return moze.csv(moze.opening("華航卡", "TWD", "0"), *rows)


def test_required_rates_lists_only_foreign_rows(moze):
    parsed = parse_moze_csv(
        _card(
            moze,
            moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date="2026/07/10"),
            moze.row("華航卡", "TWD", "支出", "-100", main="飲食"),
        )
    )
    assert required_rates(parsed) == {(DAY, "JPY", "TWD")}


def test_jpy_expense_on_twd_card_is_converted_keeping_the_original(db_session, moze):
    summary = _import(
        db_session,
        _card(moze, moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date="2026/07/10")),
        {(DAY, "JPY", "TWD"): Decimal("0.2")},
    )

    [entry] = _entries(db_session, "華航卡")
    assert (entry.amount, entry.currency) == (Decimal("-360.0000"), "TWD")
    assert (entry.original_amount, entry.original_currency) == (Decimal("-1800.0000"), "JPY")
    assert (entry.fx_rate, entry.fx_source) == (Decimal("0.2000000000"), "fx_api")
    [account] = summary["accounts"]
    assert (account["balance"], account["converted_entry_count"], account["converted_amount"]) == (
        "-360.0000", 1, "-360.0000",
    )


def test_conversion_rounds_half_up_to_four_decimals(db_session, moze):
    _import(
        db_session,
        _card(moze, moze.row("華航卡", "USD", "支出", "-21.35", main="訂閱", date="2026/07/10")),
        {(DAY, "USD", "TWD"): Decimal("32.1234567891")},
    )
    [entry] = _entries(db_session, "華航卡")
    # -21.35 × 32.1234567891 = -685.835802447... → -685.8358
    assert entry.amount == Decimal("-685.8358")


def test_fee_child_uses_the_same_rate_and_keeps_its_own_original(db_session, moze):
    _import(
        db_session,
        _card(moze, moze.row("華航卡", "JPY", "支出", "-1800", fee="-27", main="飲食", date="2026/07/10")),
        {(DAY, "JPY", "TWD"): Decimal("0.2")},
    )
    expense, fee = _entries(db_session, "華航卡")
    assert (fee.kind, fee.parent_entry_id) == ("fee", expense.id)
    assert (fee.amount, fee.original_amount, fee.original_currency) == (Decimal("-5.4000"), Decimal("-27.0000"), "JPY")
    assert (fee.fx_rate, fee.fx_source) == (Decimal("0.2000000000"), "fx_api")


def test_transfer_pairing_uses_the_rows_own_currency_and_amount(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("台幣", "TWD", "0"),
            moze.opening("日圓", "JPY", "0"),
            moze.row("台幣", "TWD", "轉出", "-10000", main="轉帳", date="2026/07/10"),
            moze.row("日圓", "TWD", "轉入", "10000", main="轉帳", date="2026/07/10"),
        ),
        {(DAY, "TWD", "JPY"): Decimal("4.6")},
    )
    [out_leg] = _entries(db_session, "台幣")
    [in_leg] = _entries(db_session, "日圓")
    assert summary["pairing"] == {"pass1": 1, "pass2": 0, "pass3": 0}
    assert out_leg.transfer_group_id == in_leg.transfer_group_id
    assert (in_leg.amount, in_leg.currency, in_leg.original_amount) == (Decimal("46000.0000"), "JPY", Decimal("10000.0000"))


def test_missing_rate_fails_naming_row_pair_and_date(db_session, moze):
    with pytest.raises(MozeImportError, match="row 3: no FX rate for JPY→TWD on 2026-07-10"):
        _import(db_session, _card(moze, moze.row("華航卡", "JPY", "支出", "-1", main="飲食", date="2026/07/10")), {})
```

- [ ] 8.4 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_fx_rate_service.py tests/integration/test_moze_fx_conversion.py
```

Expected: `ImportError while loading conftest` with `cannot import name 'fx_rate_service' from 'app.services'`.

- [ ] 8.5 Create `services/accounting-service/app/services/fx_rate_service.py`:

```python
"""Daily historical FX rates for converting foreign-currency MOZE rows (design D11).

Rates come from the accounting `fx_rate` cache; missing ones are fetched from the same
fawazahmed0 currency API that stock-portfolio-service uses (jsDelivr, then pages.dev).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable

import requests
from requests import Response
from requests.exceptions import RequestException
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import FxRate
from .moze_csv import MozeImportError

PRIMARY_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json"
FALLBACK_URL_TEMPLATE = "https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json"
PRIMARY_SOURCE_LABEL = "fawazahmed0-jsdelivr"
FALLBACK_SOURCE_LABEL = "fawazahmed0-pages"
HTTP_TIMEOUT_SEC = 10
MAX_PARALLEL_FETCHES = 8
RATE_QUANTUM = Decimal("0.0000000001")  # fx_rate is NUMERIC(20,10)

HttpGet = Callable[..., Response]
RateKey = tuple[date, str, str]  # (day, base = row currency, quote = account currency)


class FxRateUnavailableError(MozeImportError):
    """A needed rate could not be read from the cache or fetched from either source."""


def _fetch_json(http_get: HttpGet, url: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = http_get(url, timeout=HTTP_TIMEOUT_SEC)
        if response.status_code < 200 or response.status_code >= 300:
            return None, f"{response.status_code} from {url}"
        return response.json(), None
    except (RequestException, ValueError) as exc:
        return None, str(exc)


def _fetch_day(http_get: HttpGet, day: date, base: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (rates object for `base`, source label, error) for one day."""
    slot, base_lc = day.isoformat(), base.lower()
    errors = []
    for template, label in ((PRIMARY_URL_TEMPLATE, PRIMARY_SOURCE_LABEL), (FALLBACK_URL_TEMPLATE, FALLBACK_SOURCE_LABEL)):
        payload, error = _fetch_json(http_get, template.format(slot=slot, base_lc=base_lc))
        if payload is not None and isinstance(payload.get(base_lc), dict):
            return payload[base_lc], label, None
        errors.append(error or f"payload missing rates object for {base_lc}")
    return None, "", "; ".join(errors)


def ensure_rates(
    session: Session,
    needed: Iterable[RateKey],
    *,
    persist: bool,
    http_get: HttpGet | None = None,
) -> dict[RateKey, Decimal]:
    """Return every needed rate, fetching missing ones; with `persist`, store fetched rates and commit.

    Raises FxRateUnavailableError naming the pair and date when a rate cannot be obtained.
    """
    needed = sorted(set(needed))
    if not needed:
        return {}
    rates: dict[RateKey, Decimal] = {
        (row.date, row.base, row.quote): row.rate
        for row in session.scalars(
            select(FxRate).where(tuple_(FxRate.date, FxRate.base, FxRate.quote).in_(needed))
        )
    }
    missing = [key for key in needed if key not in rates]
    if not missing:
        return rates

    http_get = http_get or requests.get
    days = sorted({(day, base) for day, base, _ in missing})
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = dict(zip(days, pool.map(lambda key: _fetch_day(http_get, *key), days)))

    new_rows, failures = [], []
    for day, base, quote in missing:
        payload, source, error = fetched[(day, base)]
        raw = payload.get(quote.lower()) if payload is not None else None
        if raw is None:
            reason = error or f"no {quote} rate in the {source} payload"
            failures.append(f"no FX rate for {base}→{quote} on {day.isoformat()}: {reason}")
            continue
        rate = Decimal(str(raw)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        rates[(day, base, quote)] = rate
        new_rows.append({"date": day, "base": base, "quote": quote, "rate": rate, "source": source})

    if persist and new_rows:
        # Keep what was fetched even if another rate failed, so a retry only fetches the gaps.
        session.execute(pg_insert(FxRate).values(new_rows).on_conflict_do_nothing())
        session.commit()
    if failures:
        raise FxRateUnavailableError("; ".join(failures))
    return rates
```

- [ ] 8.6 Run them again.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_fx_rate_service.py tests/integration/test_moze_fx_conversion.py
```

Expected: `6 passed` for `test_fx_rate_service.py`; `test_moze_fx_conversion.py` fails with `ImportError: cannot import name 'required_rates' from 'app.services.moze_import_service'`.

- [ ] 8.7 In `moze_import_service.py`, replace the import line `from decimal import Decimal` with:

```python
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
```

- [ ] 8.8 Insert these helpers immediately above `def _insert_entries(`:

```python
AMOUNT_QUANTUM = Decimal("0.0001")  # amount is NUMERIC(20,4)


def required_rates(parsed: ParsedFile) -> set[tuple[date, str, str]]:
    """(day, row currency, account currency) for every row not recorded in its account's currency."""
    return {
        (row.entry_date, row.currency, parsed.accounts[row.account].currency)
        for row in parsed.rows
        if row.currency != parsed.accounts[row.account].currency
    }


def _converted(amount: Decimal, rate: Decimal | None) -> Decimal:
    if rate is None:
        return amount
    return (amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def _fx_columns(amount: Decimal, currency: str, rate: Decimal | None) -> dict:
    if rate is None:
        return {}
    return {"original_amount": amount, "original_currency": currency, "fx_rate": rate, "fx_source": "fx_api"}
```

- [ ] 8.9 In `_insert_entries`, replace the end of the signature

```python
    pairing: PairingResult,
    import_run_id: int | None,
) -> list[LedgerEntry]:
```

with

```python
    pairing: PairingResult,
    import_run_id: int | None,
    rates: Mapping[tuple[date, str, str], Decimal],
) -> list[LedgerEntry]:
```

- [ ] 8.10 In `_insert_entries`, replace the start of the parent entry

```python
    for row in rows:
        account = accounts[row.account]
        parent = LedgerEntry(
            account_id=account.id,
            kind=row.kind,
            amount=row.amount,
            currency=account.currency,
```

with

```python
    for row in rows:
        account = accounts[row.account]
        rate = None
        if row.currency != account.currency:
            key = (row.entry_date, row.currency, account.currency)
            if key not in rates:
                raise MozeImportError(
                    f"row {row.row_no}: no FX rate for {row.currency}→{account.currency} on {row.entry_date.isoformat()}"
                )
            rate = rates[key]
        parent = LedgerEntry(
            account_id=account.id,
            kind=row.kind,
            amount=_converted(row.amount, rate),
            currency=account.currency,
            **_fx_columns(row.amount, row.currency, rate),
```

- [ ] 8.11 In `_insert_entries`, replace the start of each child entry

```python
                    LedgerEntry(
                        account_id=account.id,
                        kind=kind,
                        amount=amount,
                        currency=account.currency,
```

with

```python
                    LedgerEntry(
                        account_id=account.id,
                        kind=kind,
                        amount=_converted(amount, rate),
                        currency=account.currency,
                        **_fx_columns(amount, row.currency, rate),
```

- [ ] 8.12 In `replace_ledger`, replace the end of the signature and the docstring

```python
    renames: Mapping[str, str] | None = None,
) -> dict:
    """Run full-replace steps 1-6 in the caller's transaction and return the report summary."""
```

with

```python
    renames: Mapping[str, str] | None = None,
    rates: Mapping[tuple[date, str, str], Decimal] | None = None,
) -> dict:
    """Run full-replace steps 1-6 in the caller's transaction and return the report summary.

    `rates` maps (day, row currency, account currency) to the rate for every foreign-currency row.
    """
```

and replace

```python
    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id)
```

with

```python
    inserted = _insert_entries(session, parsed.rows, accounts, pairing, import_run_id, rates or {})
```

- [ ] 8.13 Run the FX and ledger-write tests.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_fx_rate_service.py tests/integration/test_moze_fx_conversion.py tests/integration/test_moze_replace_ledger.py tests/integration/test_moze_renames.py
```

Expected: `24 passed`.

- [ ] 8.14 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/fx_rate_service.py services/accounting-service/app/services/moze_import_service.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/integration/test_fx_rate_service.py \
  services/accounting-service/tests/integration/test_moze_fx_conversion.py
git commit -m "feat(accounting): convert foreign-currency MOZE rows with cached daily FX rates"
```

## 9. Import run lifecycle, advisory lock and CLI

**Files:**
- Modify: `services/accounting-service/app/services/moze_import_service.py`
- Create: `tests/integration/test_moze_import_run.py`

**Interfaces:**
- Consumes: `parse_moze_csv` (Task 4), `pair_transfers` (Task 5), `replace_ledger`, `required_rates`, `fx_rate_service.ensure_rates` (Task 8), `ImportRun` (Task 3).
- Produces: `IMPORT_LOCK_KEY = 0x4D4F5A45`; `class ImportRefusedError(Exception)`, `class ImportLockedError(ImportRefusedError)`, `class ImportAlreadyRunningError(ImportRefusedError)`; `import_locked() -> bool`; `run_report(run: ImportRun) -> dict`; `run_import(engine: Engine, data: bytes, file_name: str, *, dry_run: bool = False, renames: Mapping[str, str] | None = None, http_get: fx_rate_service.HttpGet | None = None) -> dict` returning `{"id": int | None, "status": "succeeded" | "dry_run", "started_at": str, "finished_at": str | None, "file_name": str, "file_sha256": str, "row_count": int | None, "summary": dict}`; `main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int` (0 = success, 1 = refused or failed; report JSON on stdout, errors on stderr).
- Lifecycle on one dedicated connection: `ACCOUNTING_IMPORT_LOCKED` check → `pg_try_advisory_lock` (refuse with `import already running`) → non-dry-run: stale `running` rows → `failed`/`interrupted`, insert `running` row and commit → parse, pair, `ensure_rates(persist=True)` (commits fetched rates before the ledger transaction) → `replace_ledger`, flip run to `succeeded` with summary, commit together → on any exception: rollback, set `failed` with `{"error": str(exc)}` in a new transaction, re-raise → always unlock. A dry run runs the same steps with `ensure_rates(persist=False)` and rolls back, so it writes no table rows, not even `fx_rate` (sequence values it drew are not reused, which is invisible to data). An unobtainable rate is an ordinary failure: the run is recorded `failed` and the ledger is untouched.

- [ ] 9.1 Write the failing test `services/accounting-service/tests/integration/test_moze_import_run.py`:

```python
import json
import time

import pytest
from sqlalchemy import select, text

from app.models import Account
from app.services import moze_import_service
from app.services.moze_csv import MozeImportError
from app.services.moze_import_service import (
    IMPORT_LOCK_KEY,
    ImportAlreadyRunningError,
    ImportLockedError,
    main,
    run_import,
)


def _good(moze, amount="-120") -> bytes:
    return moze.csv(
        moze.opening("錢包", "TWD", "2000"),
        moze.row("錢包", "TWD", "支出", amount, main="飲食", sub="午餐"),
        moze.row("錢包", "TWD", "支出", "-30", main="交通"),
    )


def _snapshot(pg_engine) -> dict:
    with pg_engine.connect() as conn:
        return {
            "accounts": conn.execute(text("SELECT id, name, currency, opening_balance, is_archived FROM account ORDER BY id")).all(),
            "entries": conn.execute(text("SELECT id, account_id, kind, amount, seq FROM ledger_entry ORDER BY id")).all(),
            "categories": conn.execute(text("SELECT id, kind, parent_id, name FROM category ORDER BY id")).all(),
            "projects": conn.execute(text("SELECT id, name FROM project ORDER BY id")).all(),
        }


def _runs(pg_engine) -> list:
    with pg_engine.connect() as conn:
        return conn.execute(text("SELECT id, status, row_count, summary FROM import_run ORDER BY id")).all()


def _advisory_locks(pg_engine) -> int:
    with pg_engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key"),
            {"key": IMPORT_LOCK_KEY},
        ).scalar_one()


def _wait_until_unlocked(pg_engine, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while _advisory_locks(pg_engine) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_successful_import_records_succeeded_run(pg_engine, db_session, moze):
    report = run_import(pg_engine, _good(moze), "moze.csv")

    assert report["status"] == "succeeded"
    assert report["file_name"] == "moze.csv"
    assert len(report["file_sha256"]) == 64
    assert report["row_count"] == 3
    assert report["summary"]["accounts"][0]["balance"] == "1850.0000"
    [run] = _runs(pg_engine)
    assert (run.status, run.row_count) == ("succeeded", 3)


def test_failure_mid_file_leaves_previous_import_and_records_failed_run(pg_engine, db_session, moze):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)
    bad = moze.csv(
        moze.opening("錢包", "TWD", "0"),
        moze.row("錢包", "TWD", "支出", "-1", main="飲食"),
        moze.row("錢包", "TWD", "支出", "-1", main="飲食", date="2026-13-01"),
    )

    with pytest.raises(MozeImportError, match="row 4"):
        run_import(pg_engine, bad, "bad.csv")

    assert _snapshot(pg_engine) == before
    last = _runs(pg_engine)[-1]
    assert last.status == "failed"
    assert "row 4" in last.summary["error"]


def test_rejected_header_writes_no_ledger_data(pg_engine, db_session, moze):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)

    with pytest.raises(MozeImportError, match="missing columns: 對象"):
        run_import(pg_engine, moze.csv(header=moze.header.replace(",對象", "")), "old-format.csv")

    assert _snapshot(pg_engine) == before


def test_dry_run_reports_without_writing_anything(pg_engine, db_session, moze):
    run_import(pg_engine, moze.csv(moze.opening("A", "TWD", "0")), "first.csv")
    before = _snapshot(pg_engine)
    runs_before = _runs(pg_engine)

    report = run_import(pg_engine, moze.csv(moze.opening("B", "TWD", "0")), "next.csv", dry_run=True)

    assert report["status"] == "dry_run"
    assert report["id"] is None
    assert report["summary"]["accounts_created"] == ["B"]
    assert report["summary"]["accounts_archived"] == ["A"]
    assert _snapshot(pg_engine) == before
    assert _runs(pg_engine) == runs_before


def test_second_import_is_refused_while_lock_is_held(pg_engine, db_session, moze):
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        assert _advisory_locks(pg_engine) == 1
        for dry_run in (False, True):
            with pytest.raises(ImportAlreadyRunningError, match="import already running"):
                run_import(pg_engine, _good(moze), "moze.csv", dry_run=dry_run)
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()

    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["entries"] == []


def test_lock_is_released_after_a_failed_import(pg_engine, db_session, moze):
    # Review focus: a leaked advisory lock would refuse every later import.
    with pytest.raises(MozeImportError):
        run_import(pg_engine, moze.csv(moze.row("錢包", "TWD", "支出", "-1")), "bad.csv")

    assert _advisory_locks(pg_engine) == 0
    assert run_import(pg_engine, _good(moze), "good.csv")["status"] == "succeeded"
    assert _advisory_locks(pg_engine) == 0


def test_interrupted_import_is_marked_failed_and_leaves_no_trace(pg_engine, db_session, moze):
    crashed = pg_engine.connect()
    crashed.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
    crashed.execute(
        text("INSERT INTO import_run (started_at, file_name, file_sha256, status) VALUES (now(), 'killed.csv', repeat('0', 64), 'running')")
    )
    crashed.commit()
    crashed.execute(text("INSERT INTO account (name, currency) VALUES ('ghost', 'TWD')"))
    crashed.invalidate()  # the process dies: no commit, the server drops the session and its lock
    crashed.close()
    _wait_until_unlocked(pg_engine)

    run_import(pg_engine, _good(moze), "next.csv")

    runs = _runs(pg_engine)
    assert (runs[0].status, runs[0].summary) == ("failed", {"error": "interrupted"})
    assert runs[1].status == "succeeded"
    assert db_session.scalar(select(Account).where(Account.name == "ghost")) is None


def test_dry_run_does_not_touch_stale_running_rows(pg_engine, db_session, moze):
    with pg_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO import_run (started_at, file_name, file_sha256, status) VALUES (now(), 'killed.csv', repeat('0', 64), 'running')")
        )

    run_import(pg_engine, _good(moze), "check.csv", dry_run=True)

    assert [r.status for r in _runs(pg_engine)] == ["running"]


def test_import_locked_flag_refuses_every_path(pg_engine, db_session, moze, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    for dry_run in (False, True):
        with pytest.raises(ImportLockedError, match="import is locked"):
            run_import(pg_engine, _good(moze), "moze.csv", dry_run=dry_run)
    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["accounts"] == []


def test_cli_dry_run_prints_report_and_writes_nothing(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(_good(moze))

    assert main([str(csv_path), "--dry-run"], engine=pg_engine) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert report["summary"]["kind_counts"] == {"expense": 2}
    assert _runs(pg_engine) == []
    assert _snapshot(pg_engine)["accounts"] == []


def test_cli_import_with_rename(pg_engine, db_session, moze, tmp_path, capsys):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    first.write_bytes(moze.csv(moze.opening("A", "TWD", "1000")))
    second.write_bytes(moze.csv(moze.opening("B", "TWD", "1000")))
    assert main([str(first)], engine=pg_engine) == 0

    assert main([str(second), "--rename", "A=B"], engine=pg_engine) == 0

    assert list(db_session.scalars(select(Account.name))) == ["B"]


def test_cli_refused_while_lock_held_exits_non_zero(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(_good(moze))
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        code = main([str(csv_path)], engine=pg_engine)
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()

    assert code == 1
    assert "import already running" in capsys.readouterr().err
    assert _runs(pg_engine) == []


def test_cli_failed_import_exits_non_zero_with_message(pg_engine, db_session, moze, tmp_path, capsys):
    csv_path = tmp_path / "moze.csv"
    csv_path.write_bytes(moze.csv(moze.row("Line Bank", "TWD", "支出", "-1")))

    assert main([str(csv_path)], engine=pg_engine) == 1
    assert "import failed: account 'Line Bank' has no 初始金額 row" in capsys.readouterr().err


def _foreign(moze, day="2026/07/10") -> bytes:
    return moze.csv(
        moze.opening("華航卡", "TWD", "0"),
        moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date=day),
    )


JPY_2026_07_10 = {"currency-api@2026-07-10/v1/currencies/jpy.json": (200, {"date": "2026-07-10", "jpy": {"twd": 0.2}})}


def test_foreign_rows_use_fetched_rates_and_the_cache_on_reimport(pg_engine, db_session, moze, fake_http):
    first = fake_http(JPY_2026_07_10)
    report = run_import(pg_engine, _foreign(moze), "moze.csv", http_get=first)

    assert report["summary"]["accounts"][0]["balance"] == "-360.0000"
    assert len(first.calls) == 1
    second = fake_http({})
    assert run_import(pg_engine, _foreign(moze), "moze.csv", http_get=second)["status"] == "succeeded"
    assert second.calls == []


def test_unobtainable_rate_fails_before_touching_the_ledger(pg_engine, db_session, moze, fake_http):
    run_import(pg_engine, _good(moze), "first.csv")
    before = _snapshot(pg_engine)

    with pytest.raises(MozeImportError, match="no FX rate for JPY→TWD on 2025-06-01"):
        run_import(pg_engine, _foreign(moze, day="2025/06/01"), "foreign.csv", http_get=fake_http({}))

    assert _snapshot(pg_engine) == before
    last = _runs(pg_engine)[-1]
    assert last.status == "failed"
    assert "JPY→TWD on 2025-06-01" in last.summary["error"]


def test_dry_run_fetches_rates_without_caching_them(pg_engine, db_session, moze, fake_http):
    report = run_import(pg_engine, _foreign(moze), "moze.csv", dry_run=True, http_get=fake_http(JPY_2026_07_10))

    assert report["summary"]["accounts"][0]["converted_entry_count"] == 1
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fx_rate")).scalar_one() == 0
```

- [ ] 9.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_import_run.py
```

Expected: `ImportError: cannot import name 'IMPORT_LOCK_KEY' from 'app.services.moze_import_service'`.

- [ ] 9.3 In `moze_import_service.py`, replace everything from the first line down to and including `SOURCE = "moze_import"` with:

```python
"""MOZE CSV import: transactional full replace of MOZE-sourced ledger data (design D6).

CLI: python -m app.services.moze_import_service <path> [--dry-run] [--rename OLD=NEW ...]
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from ..models import SYSTEM_KINDS, Account, Category, ImportRun, LedgerEntry, Project
from . import fx_rate_service
from .moze_csv import MozeImportError, MozeRow, ParsedFile, parse_moze_csv
from .transfer_pairing import PairingResult, pair_transfers

IMPORT_LOCK_KEY = 0x4D4F5A45  # "MOZE"; one key shared by CLI, REST and dry runs
SOURCE = "moze_import"
```

- [ ] 9.4 Append to the end of `moze_import_service.py`:

```python


class ImportRefusedError(Exception):
    """The import was refused before anything was written."""


class ImportLockedError(ImportRefusedError):
    pass


class ImportAlreadyRunningError(ImportRefusedError):
    pass


def import_locked() -> bool:
    return os.getenv("ACCOUNTING_IMPORT_LOCKED", "").strip().lower() == "true"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_report(run: ImportRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "file_name": run.file_name,
        "file_sha256": run.file_sha256,
        "row_count": run.row_count,
        "summary": run.summary,
    }


def _run_locked(
    conn: Connection,
    data: bytes,
    file_name: str,
    dry_run: bool,
    renames: Mapping[str, str],
    http_get: fx_rate_service.HttpGet | None,
) -> dict:
    sha256 = hashlib.sha256(data).hexdigest()
    with Session(bind=conn, autoflush=False) as session:
        if dry_run:
            started = _now()
            try:
                parsed = parse_moze_csv(data)
                pairing = pair_transfers(parsed.rows)
                rates = fx_rate_service.ensure_rates(
                    session, required_rates(parsed), persist=False, http_get=http_get
                )
                summary = replace_ledger(session, parsed, pairing, None, renames, rates)
            finally:
                session.rollback()
            return {
                "id": None,
                "status": "dry_run",
                "started_at": started.isoformat(),
                "finished_at": _now().isoformat(),
                "file_name": file_name,
                "file_sha256": sha256,
                "row_count": parsed.row_count,
                "summary": summary,
            }

        session.execute(
            update(ImportRun)
            .where(ImportRun.status == "running")
            .values(status="failed", finished_at=_now(), summary={"error": "interrupted"})
        )
        run = ImportRun(started_at=_now(), file_name=file_name, file_sha256=sha256, status="running")
        session.add(run)
        session.commit()
        run_id = run.id

        try:
            parsed = parse_moze_csv(data)
            pairing = pair_transfers(parsed.rows)
            # Rates are fetched and cached (committed) before the ledger transaction starts.
            rates = fx_rate_service.ensure_rates(session, required_rates(parsed), persist=True, http_get=http_get)
            summary = replace_ledger(session, parsed, pairing, run_id, renames, rates)
            run = session.get(ImportRun, run_id)
            run.status = "succeeded"
            run.row_count = parsed.row_count
            run.summary = summary
            run.finished_at = _now()
            session.commit()
        except Exception as exc:
            session.rollback()
            run = session.get(ImportRun, run_id)
            run.status = "failed"
            run.summary = {"error": str(exc)}
            run.finished_at = _now()
            session.commit()
            raise
        return run_report(session.get(ImportRun, run_id))


def run_import(
    engine: Engine,
    data: bytes,
    file_name: str,
    *,
    dry_run: bool = False,
    renames: Mapping[str, str] | None = None,
    http_get: fx_rate_service.HttpGet | None = None,
) -> dict:
    """Import a MOZE CSV. Raises ImportRefusedError (nothing written) or MozeImportError (rolled back).

    `http_get` replaces requests.get for FX rate fetches (tests inject a fake).
    """
    if import_locked():
        raise ImportLockedError("MOZE import is locked (ACCOUNTING_IMPORT_LOCKED=true)")
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY}).scalar_one()
        conn.commit()
        if not acquired:
            raise ImportAlreadyRunningError("import already running")
        try:
            return _run_locked(conn, data, file_name, dry_run, renames or {}, http_get)
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            conn.commit()


def _parse_rename(value: str) -> tuple[str, str]:
    old_name, sep, new_name = value.partition("=")
    if not sep or not old_name or not new_name:
        raise argparse.ArgumentTypeError(f"expected OLD=NEW, got '{value}'")
    return old_name, new_name


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.moze_import_service")
    parser.add_argument("path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rename", action="append", default=[], type=_parse_rename, metavar="OLD=NEW")
    args = parser.parse_args(argv)

    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    try:
        report = run_import(
            engine,
            args.path.read_bytes(),
            args.path.name,
            dry_run=args.dry_run,
            renames=dict(args.rename),
        )
    except ImportRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except MozeImportError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] 9.5 Run the import tests and the earlier ledger-write tests.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_moze_import_run.py tests/integration/test_moze_fx_conversion.py tests/integration/test_moze_renames.py tests/integration/test_moze_replace_ledger.py
```

Expected: `34 passed`.

- [ ] 9.6 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/moze_import_service.py services/accounting-service/tests/integration/test_moze_import_run.py
git commit -m "feat(accounting): serialise MOZE imports with an advisory lock and record import runs"
```

## 10. Read-only account and entry endpoints

**Files:**
- Create: `services/accounting-service/app/services/ledger_service.py`, `app/schemas/ledger.py`, `app/routers/accounts.py`, `tests/integration/test_accounts_api.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces: `ledger_service.list_accounts(db: Session) -> list[dict]` (non-archived, `entry_count` desc then `name`); `ledger_service.list_entries(db: Session, account_id: int, *, limit: int, offset: int, kind: str | None = None, date_from: date | None = None, date_to: date | None = None) -> tuple[int, list[dict]]` (running balance computed over all of the account's entries before filters; order `entry_date DESC, entry_time DESC NULLS LAST, seq DESC`).
- Produces HTTP: `GET /accounts -> list[AccountOut]`; `GET /accounts/{account_id}/entries?limit=50(1..500)&offset=0&kind&date_from&date_to -> EntryPage {items: list[EntryOut], total, limit, offset}`; 404 `{"code": 404, "message": "account not found", "trace_id": …}` for an unknown id; 422 for `limit > 500`. Decimals serialise as strings (`"2299.5000"`). Each entry also carries `original_amount`, `original_currency`, `fx_rate`, `fx_source` (null for same-currency entries); balances and running balances use `amount` only.

- [ ] 10.1 Write the failing test `services/accounting-service/tests/integration/test_accounts_api.py`:

```python
from datetime import date, time
from decimal import Decimal

from app.main import app
from app.models import Account, Category, LedgerEntry, Project


def _account(db, name="錢包", currency="TWD", opening="2000", archived=False) -> Account:
    account = Account(name=name, currency=currency, opening_balance=Decimal(opening), is_archived=archived)
    db.add(account)
    db.flush()
    return account


def _entry(db, account, amount, *, seq, kind="expense", day=1, at=time(12, 0), **extra) -> LedgerEntry:
    entry = LedgerEntry(
        account_id=account.id, kind=kind, amount=Decimal(amount), currency=account.currency,
        entry_date=date(2026, 9, day), entry_time=at, source="moze_import", seq=seq, **extra,
    )
    db.add(entry)
    db.flush()
    return entry


def test_accounts_list_shows_computed_balances_sorted_by_entry_count(client, db_session):
    wallet = _account(db_session, "錢包", opening="2000")
    for seq, amount in enumerate(["-120", "-80.5", "500"], start=1):
        _entry(db_session, wallet, amount, seq=seq)
    jpy = _account(db_session, "去日本的錢", currency="JPY", opening="180000")
    _entry(db_session, jpy, "-1500", seq=10)
    _account(db_session, "舊帳戶", archived=True)
    db_session.commit()

    response = client.get("/accounts")

    assert response.status_code == 200
    assert response.json() == [
        {"id": wallet.id, "name": "錢包", "currency": "TWD", "opening_balance": "2000.0000", "balance": "2299.5000", "entry_count": 3},
        {"id": jpy.id, "name": "去日本的錢", "currency": "JPY", "opening_balance": "180000.0000", "balance": "178500.0000", "entry_count": 1},
    ]


def test_entries_newest_first_with_running_balance_category_and_project(client, db_session):
    wallet = _account(db_session, opening="1000")
    food = Category(kind="expense", name="飲食")
    db_session.add(food)
    db_session.flush()
    lunch = Category(kind="expense", parent_id=food.id, name="午餐")
    trip = Project(name="日本行")
    db_session.add_all([lunch, trip])
    db_session.flush()
    first = _entry(db_session, wallet, "-100", seq=1, day=1, category_id=lunch.id, project_id=trip.id, name="便當")
    _entry(db_session, wallet, "-5", seq=2, day=1, kind="fee", parent_entry_id=first.id, category_id=food.id)
    _entry(db_session, wallet, "300", seq=3, day=2, kind="income")
    db_session.commit()

    body = client.get(f"/accounts/{wallet.id}/entries").json()

    assert body["total"] == 3
    assert [(e["amount"], e["running_balance"]) for e in body["items"]] == [
        ("300.0000", "1195.0000"),
        ("-5.0000", "895.0000"),
        ("-100.0000", "900.0000"),
    ]
    oldest = body["items"][-1]
    assert (oldest["category"], oldest["project"], oldest["name"]) == ("飲食/午餐", "日本行", "便當")
    assert body["items"][1]["category"] == "飲食"
    assert body["items"][1]["parent_entry_id"] == oldest["id"]


def test_converted_entry_counts_only_its_account_currency_amount(client, db_session):
    card = _account(db_session, "華航卡", opening="0")
    _entry(
        db_session, card, "-360", seq=1,
        original_amount=Decimal("-1800"), original_currency="JPY", fx_rate=Decimal("0.2"), fx_source="fx_api",
    )
    db_session.commit()

    [account] = client.get("/accounts").json()
    [entry] = client.get(f"/accounts/{card.id}/entries").json()["items"]

    assert account["balance"] == "-360.0000"
    assert (entry["amount"], entry["currency"], entry["running_balance"]) == ("-360.0000", "TWD", "-360.0000")
    assert (entry["original_amount"], entry["original_currency"], entry["fx_rate"], entry["fx_source"]) == (
        "-1800.0000", "JPY", "0.2000000000", "fx_api",
    )


def test_same_minute_entries_paginate_stably(client, db_session):
    wallet = _account(db_session, opening="0")
    for seq in (10, 11, 12):
        _entry(db_session, wallet, f"-{seq}", seq=seq)
    db_session.commit()

    full = [e["id"] for e in client.get(f"/accounts/{wallet.id}/entries").json()["items"]]
    again = [e["id"] for e in client.get(f"/accounts/{wallet.id}/entries").json()["items"]]
    paged = [
        client.get(f"/accounts/{wallet.id}/entries", params={"limit": 1, "offset": offset}).json()["items"][0]["id"]
        for offset in range(3)
    ]
    assert full == again == paged
    assert len(set(paged)) == 3


def test_untimed_entry_sorts_first_in_its_day(client, db_session):
    wallet = _account(db_session, opening="0")
    timed = _entry(db_session, wallet, "-1", seq=1, at=time(8, 0))
    untimed = _entry(db_session, wallet, "-2", seq=2, at=None)
    db_session.commit()

    items = client.get(f"/accounts/{wallet.id}/entries").json()["items"]

    assert [e["id"] for e in items] == [timed.id, untimed.id]  # newest first = reverse canonical
    assert [e["running_balance"] for e in items] == ["-3.0000", "-2.0000"]


def test_filters_do_not_change_running_balance(client, db_session):
    # Review focus: running balance must be the true account balance even on a filtered page.
    wallet = _account(db_session, opening="1000")
    _entry(db_session, wallet, "-100", seq=1, day=1)
    _entry(db_session, wallet, "500", seq=2, day=2, kind="income")
    _entry(db_session, wallet, "-50", seq=3, day=3)
    db_session.commit()

    by_kind = client.get(f"/accounts/{wallet.id}/entries", params={"kind": "expense"}).json()
    by_date = client.get(f"/accounts/{wallet.id}/entries", params={"date_from": "2026-09-02", "date_to": "2026-09-02"}).json()

    assert [(e["amount"], e["running_balance"]) for e in by_kind["items"]] == [("-50.0000", "1350.0000"), ("-100.0000", "900.0000")]
    assert by_kind["total"] == 2
    assert [(e["amount"], e["running_balance"]) for e in by_date["items"]] == [("500.0000", "1400.0000")]


def test_unknown_account_is_404_and_limit_is_capped(client, db_session):
    assert client.get("/accounts/999/entries").status_code == 404
    wallet = _account(db_session)
    db_session.commit()
    assert client.get(f"/accounts/{wallet.id}/entries", params={"limit": 501}).status_code == 422
    assert client.get(f"/accounts/{wallet.id}/entries").json()["limit"] == 50


def test_no_write_endpoints_for_accounts_or_entries():
    writes = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if getattr(route, "methods", None) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes == []
```

- [ ] 10.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_accounts_api.py
```

Expected: `7 failed, 1 passed`; the failures are `assert 404 == 200` / `KeyError` / `ValueError` because `/accounts` does not exist (only `test_no_write_endpoints_for_accounts_or_entries` passes).

- [ ] 10.3 Create `services/accounting-service/app/services/ledger_service.py`:

```python
"""Read-only ledger queries: balances and entry history in canonical order (design D10)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..models import Account, Category, LedgerEntry, Project


def list_accounts(db: Session) -> list[dict]:
    entry_count = func.count(LedgerEntry.id).label("entry_count")
    rows = db.execute(
        select(
            Account.id,
            Account.name,
            Account.currency,
            Account.opening_balance,
            (Account.opening_balance + func.coalesce(func.sum(LedgerEntry.amount), 0)).label("balance"),
            entry_count,
        )
        .outerjoin(LedgerEntry, LedgerEntry.account_id == Account.id)
        .where(Account.is_archived.is_(False))
        .group_by(Account.id)
        .order_by(entry_count.desc(), Account.name)
    )
    return [dict(row._mapping) for row in rows]


def list_entries(
    db: Session,
    account_id: int,
    *,
    limit: int,
    offset: int,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[int, list[dict]]:
    """Return (total, page) newest first; running_balance is computed over all entries before filtering."""
    running = (
        select(
            LedgerEntry.id.label("entry_id"),
            (
                Account.opening_balance
                + func.sum(LedgerEntry.amount).over(
                    order_by=(LedgerEntry.entry_date, LedgerEntry.entry_time.asc().nulls_first(), LedgerEntry.seq),
                    rows=(None, 0),
                )
            ).label("running_balance"),
        )
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.account_id == account_id)
        .subquery()
    )
    parent_category = aliased(Category)
    filters = [LedgerEntry.account_id == account_id]
    if kind is not None:
        filters.append(LedgerEntry.kind == kind)
    if date_from is not None:
        filters.append(LedgerEntry.entry_date >= date_from)
    if date_to is not None:
        filters.append(LedgerEntry.entry_date <= date_to)

    total = db.scalar(select(func.count()).select_from(LedgerEntry).where(*filters))
    rows = db.execute(
        select(
            LedgerEntry,
            running.c.running_balance,
            Category.name.label("category_name"),
            parent_category.name.label("parent_category_name"),
            Project.name.label("project_name"),
        )
        .join(running, running.c.entry_id == LedgerEntry.id)
        .outerjoin(Category, Category.id == LedgerEntry.category_id)
        .outerjoin(parent_category, parent_category.id == Category.parent_id)
        .outerjoin(Project, Project.id == LedgerEntry.project_id)
        .where(*filters)
        .order_by(LedgerEntry.entry_date.desc(), LedgerEntry.entry_time.desc().nulls_last(), LedgerEntry.seq.desc())
        .limit(limit)
        .offset(offset)
    )
    page = []
    for entry, running_balance, category_name, parent_category_name, project_name in rows:
        if category_name is None:
            category = None
        elif parent_category_name is None:
            category = category_name
        else:
            category = f"{parent_category_name}/{category_name}"
        page.append(
            {
                "id": entry.id,
                "kind": entry.kind,
                "amount": entry.amount,
                "currency": entry.currency,
                "original_amount": entry.original_amount,
                "original_currency": entry.original_currency,
                "fx_rate": entry.fx_rate,
                "fx_source": entry.fx_source,
                "entry_date": entry.entry_date,
                "entry_time": entry.entry_time,
                "category": category,
                "project": project_name,
                "name": entry.name,
                "merchant": entry.merchant,
                "counterparty": entry.counterparty,
                "description": entry.description,
                "tags": entry.tags,
                "parent_entry_id": entry.parent_entry_id,
                "transfer_group_id": entry.transfer_group_id,
                "needs_review": entry.needs_review,
                "running_balance": running_balance,
            }
        )
    return total, page
```

- [ ] 10.4 Create `services/accounting-service/app/schemas/ledger.py`:

```python
from datetime import date, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

EntryKind = Literal[
    "expense", "income", "transfer_out", "transfer_in", "receivable", "payable",
    "balance_adjustment", "fee", "discount", "reward", "interest", "refund",
]


class AccountOut(BaseModel):
    id: int
    name: str
    currency: str
    opening_balance: Decimal
    balance: Decimal
    entry_count: int


class EntryOut(BaseModel):
    id: int
    kind: EntryKind
    amount: Decimal
    currency: str
    original_amount: Decimal | None
    original_currency: str | None
    fx_rate: Decimal | None
    fx_source: Literal["fx_api", "moze_backup"] | None
    entry_date: date
    entry_time: time | None
    category: str | None
    project: str | None
    name: str | None
    merchant: str | None
    counterparty: str | None
    description: str | None
    tags: list[str]
    parent_entry_id: int | None
    transfer_group_id: UUID | None
    needs_review: bool
    running_balance: Decimal


class EntryPage(BaseModel):
    items: list[EntryOut]
    total: int
    limit: int
    offset: int
```

- [ ] 10.5 Create `services/accounting-service/app/routers/accounts.py`:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account
from ..schemas.ledger import AccountOut, EntryKind, EntryPage
from ..services import ledger_service

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return ledger_service.list_accounts(db)


@router.get("/{account_id}/entries", response_model=EntryPage)
def list_entries(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: EntryKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    total, items = ledger_service.list_entries(
        db, account_id, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
```

- [ ] 10.6 In `services/accounting-service/app/main.py`, add `from .routers import accounts` below `from .database import engine, get_db`, and change `routers=[],` to `routers=[accounts.router],`.

- [ ] 10.7 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_accounts_api.py
```

Expected: `8 passed`.

- [ ] 10.8 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/services/ledger_service.py services/accounting-service/app/schemas/ledger.py \
  services/accounting-service/app/routers/accounts.py services/accounting-service/app/main.py \
  services/accounting-service/tests/integration/test_accounts_api.py
git commit -m "feat(accounting): list account balances and entry history with running balance"
```

## 11. Import REST endpoints

**Files:**
- Create: `services/accounting-service/app/schemas/imports.py`, `app/routers/imports.py`, `tests/integration/test_imports_api.py`
- Modify: `app/database.py`, `app/main.py`, `requirements.txt`, `tests/conftest.py`, `tests/integration/test_accounts_api.py`

**Interfaces:**
- Consumes: `run_import`, `run_report`, `ImportLockedError`, `ImportAlreadyRunningError` (Task 9); `MozeImportError` (Task 4; `FxRateUnavailableError` is a subclass, so a missing rate is a 422).
- Produces: `app.database.get_engine() -> Engine`; `POST /imports/moze` (multipart `file` ≤ 20 MB, form field `renames` = JSON object, query `dry_run: bool`) → 200 `ImportReport` | 400 bad `renames` | 409 `import already running` | 413 too large | 422 import error (message names the row/column/account) | 423 import locked; `GET /imports/latest` → 200 `ImportReport` of the newest run (any status) | 404 `no import has run yet`.

- [ ] 11.1 Write the failing test `services/accounting-service/tests/integration/test_imports_api.py`:

```python
import json
import threading

from sqlalchemy import select, text

from app.models import Account, ImportRun
from app.services import moze_import_service
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _file(moze, name="錢包", amount="-120") -> bytes:
    return moze.csv(moze.opening(name, "TWD", "2000"), moze.row(name, "TWD", "支出", amount, main="飲食"))


def _upload(client, data: bytes, *, dry_run=False, renames=None):
    form = {"renames": json.dumps(renames)} if renames is not None else {}
    return client.post(
        "/imports/moze",
        params={"dry_run": str(dry_run).lower()},
        files={"file": ("moze.csv", data, "text/csv")},
        data=form,
    )


def test_import_and_latest_report(client, db_session, moze):
    response = _upload(client, _file(moze))

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "succeeded"
    assert report["summary"]["accounts"][0]["balance"] == "1880.0000"
    latest = client.get("/imports/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == report["id"]


def test_latest_is_404_before_any_import(client, db_session):
    response = client.get("/imports/latest")
    assert response.status_code == 404
    assert response.json()["message"] == "no import has run yet"


def test_dry_run_and_renames_field(client, db_session, moze):
    assert _upload(client, _file(moze, name="A")).status_code == 200

    preview = _upload(client, _file(moze, name="B"), dry_run=True)
    assert preview.status_code == 200
    assert preview.json()["status"] == "dry_run"
    assert preview.json()["summary"]["accounts_archived"] == ["A"]
    assert list(db_session.scalars(select(Account.name))) == ["A"]

    renamed = _upload(client, _file(moze, name="B"), renames={"A": "B"})
    assert renamed.status_code == 200
    assert renamed.json()["summary"]["accounts_renamed"] == [{"from": "A", "to": "B"}]


def test_invalid_renames_field_is_400(client, db_session, moze):
    response = client.post(
        "/imports/moze", files={"file": ("moze.csv", _file(moze), "text/csv")}, data={"renames": "A=B"}
    )
    assert response.status_code == 400


def test_failed_import_is_422_and_latest_returns_the_failed_run(client, db_session, moze):
    assert _upload(client, _file(moze)).status_code == 200
    bad = moze.csv(moze.opening("錢包", "TWD", "0"), moze.row("錢包", "TWD", "測試", "-1"))

    response = _upload(client, bad)

    assert response.status_code == 422
    assert "row 3: unknown 記錄類型 '測試'" in response.json()["message"]
    latest = client.get("/imports/latest").json()
    assert latest["status"] == "failed"
    assert "row 3" in latest["summary"]["error"]


def test_upload_over_20_mb_is_413(client, db_session):
    response = client.post(
        "/imports/moze", files={"file": ("big.csv", b"x" * (20 * 1024 * 1024 + 1), "text/csv")}
    )
    assert response.status_code == 413


def test_locked_import_is_423(client, db_session, moze, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    response = _upload(client, _file(moze))
    assert response.status_code == 423
    assert "import is locked" in response.json()["message"]
    assert db_session.scalar(select(ImportRun)) is None


def test_rest_import_during_cli_import_gets_409_and_cli_data_wins(client, db_session, pg_engine, moze, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_parse = moze_import_service.parse_moze_csv

    def slow_parse(data: bytes):
        entered.set()
        assert release.wait(10)
        return real_parse(data)

    monkeypatch.setattr(moze_import_service, "parse_moze_csv", slow_parse)
    results = []
    cli = threading.Thread(
        target=lambda: results.append(moze_import_service.run_import(pg_engine, _file(moze, name="CLI"), "cli.csv"))
    )
    cli.start()
    assert entered.wait(10)

    response = _upload(client, _file(moze, name="REST"))

    release.set()
    cli.join(10)
    assert response.status_code == 409
    assert response.json()["message"] == "import already running"
    assert results[0]["status"] == "succeeded"
    assert list(db_session.scalars(select(Account.name))) == ["CLI"]
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM import_run")).scalar_one() == 1
        assert conn.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key"), {"key": IMPORT_LOCK_KEY}
        ).scalar_one() == 0
```

- [ ] 11.2 In `tests/integration/test_accounts_api.py`, replace the last test with:

```python
def test_only_write_endpoint_is_the_import():
    writes = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if getattr(route, "methods", None) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes == [("/imports/moze", ["POST"])]
```

- [ ] 11.3 In `tests/conftest.py`, change `from app.database import SQLALCHEMY_DATABASE_URL, get_db` to `from app.database import SQLALCHEMY_DATABASE_URL, get_db, get_engine`, and in the `client` fixture add `    app.dependency_overrides[get_engine] = lambda: pg_engine` directly below `    app.dependency_overrides[get_db] = _override_get_db`.

- [ ] 11.4 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_imports_api.py tests/integration/test_accounts_api.py
```

Expected: `ImportError while loading conftest` with `cannot import name 'get_engine' from 'app.database'`.

- [ ] 11.5 Append to `services/accounting-service/app/database.py`:

```python


def get_engine():
    return engine
```

- [ ] 11.6 Add the multipart dependency: append the line `python-multipart==0.0.28` to `services/accounting-service/requirements.txt`, then install it.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && uv pip install --python .venv/bin/python -r requirements.txt
```

- [ ] 11.7 Create `services/accounting-service/app/schemas/imports.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ImportReport(BaseModel):
    id: int | None
    status: Literal["running", "succeeded", "failed", "dry_run"]
    started_at: datetime
    finished_at: datetime | None
    file_name: str
    file_sha256: str
    row_count: int | None
    summary: dict | None
```

- [ ] 11.8 Create `services/accounting-service/app/routers/imports.py`:

```python
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import get_db, get_engine
from ..models import ImportRun
from ..schemas.imports import ImportReport
from ..services.moze_csv import MozeImportError
from ..services.moze_import_service import (
    ImportAlreadyRunningError,
    ImportLockedError,
    run_import,
    run_report,
)

router = APIRouter(prefix="/imports", tags=["Imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _parse_renames(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        renames = json.loads(raw)
    except json.JSONDecodeError:
        renames = None
    if not isinstance(renames, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in renames.items()
    ):
        raise HTTPException(status_code=400, detail='renames must be a JSON object {"old name": "new name"}')
    return renames


@router.post("/moze", response_model=ImportReport)
def import_moze(
    file: UploadFile = File(...),
    renames: str = Form(default=""),
    dry_run: bool = Query(default=False),
    engine: Engine = Depends(get_engine),
):
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 20 MB")
    rename_map = _parse_renames(renames)
    try:
        return run_import(engine, data, file.filename or "upload.csv", dry_run=dry_run, renames=rename_map)
    except ImportLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ImportAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MozeImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/latest", response_model=ImportReport)
def latest_import(db: Session = Depends(get_db)):
    run = db.scalar(select(ImportRun).order_by(ImportRun.started_at.desc(), ImportRun.id.desc()).limit(1))
    if run is None:
        raise HTTPException(status_code=404, detail="no import has run yet")
    return run_report(run)
```

- [ ] 11.9 Replace `services/accounting-service/app/main.py` with:

```python
from shared_lib import create_app

from .database import engine, get_db
from .routers import accounts, imports

app = create_app(
    title="Home Service Hub - Accounting API",
    description="記帳與財務管理微服務。",
    version="2.0.0",
    routers=[accounts.router, imports.router],
    get_db=get_db,
    engine=engine,
    otel_service_name_env="OTEL_SERVICE_NAME_ACCOUNTING",
    otel_strict=True,
)
```

- [ ] 11.10 Run the whole backend suite.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
```

Expected: `89 passed`.

- [ ] 11.11 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/app/database.py services/accounting-service/app/main.py \
  services/accounting-service/app/schemas/imports.py services/accounting-service/app/routers/imports.py \
  services/accounting-service/requirements.txt services/accounting-service/tests/conftest.py \
  services/accounting-service/tests/integration/test_imports_api.py services/accounting-service/tests/integration/test_accounts_api.py
git commit -m "feat(accounting): add MOZE import upload and latest import report endpoints"
```

## 12. Remove the legacy accounting pages

**Files:**
- Delete: `frontend/src/app/components/accounting/{card-list,category-list,dashboard,management-center,payment-method-list,recurring-list,transaction-list}/`, `frontend/src/app/services/accounting.service.ts`, `frontend/src/app/services/accounting.service.spec.ts`, `frontend/src/app/models/accounting.model.ts`
- Modify: `frontend/src/app/app.routes.ts`, `frontend/src/app/components/shell/navigation.ts`
- Create: `frontend/src/app/app.routes.spec.ts`

**Interfaces:**
- Produces: route `accounting` → redirect `accounting/accounts` (`pathMatch: 'full'`); routes `accounting/{dashboard,transactions,settings,cards,categories,recurring}` → redirect `accounting/accounts` (spec `frontend-app-shell`: old links redirect); `NAV_GROUPS` accounting group `{ defaultPath: '/accounting/accounts', items: [accounts, settings] }` (route id `accounting-dash` removed). Until Task 14, `/accounting/accounts` falls through to `**`.

- [ ] 12.1 Write the failing test `frontend/src/app/app.routes.spec.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { NAV_GROUPS, NAV_ITEMS } from './components/shell/navigation';

const REMOVED_ACCOUNTING_PAGES = ['dashboard', 'transactions', 'settings', 'cards', 'categories', 'recurring'];

describe('accounting routes', () => {
  const paths = routes.map(route => route.path);

  it('redirects the removed accounting pages to the accounts page', () => {
    for (const removed of REMOVED_ACCOUNTING_PAGES) {
      const route = routes.find(r => r.path === `accounting/${removed}`);
      expect(route?.redirectTo).toBe('accounting/accounts');
      expect(route?.loadComponent).toBeUndefined();
    }
  });

  it('redirects /accounting to the accounts page', () => {
    expect(routes.find(route => route.path === 'accounting')?.redirectTo).toBe('accounting/accounts');
  });

  it('points the accounting navigation only at the ledger pages and settings', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting/accounts');
    expect(accounting.items.map(item => item.path)).toEqual(['/accounting/accounts', '/settings']);
    expect(NAV_ITEMS.map(item => item.id)).not.toContain('accounting-dash');
  });
});
```

- [ ] 12.2 Run it.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/app.routes.spec.ts
```

Expected: `3 failed`, e.g. `expected 'accounting/accounts'` but received `undefined` for `accounting/dashboard`'s `redirectTo`.

- [ ] 12.3 Delete the legacy pages, service and model.

```bash
cd /home/opc/workspace/home-hub-moze/frontend/src/app
git rm -r -q components/accounting/card-list components/accounting/category-list components/accounting/dashboard \
  components/accounting/management-center components/accounting/payment-method-list components/accounting/recurring-list \
  components/accounting/transaction-list services/accounting.service.ts services/accounting.service.spec.ts models/accounting.model.ts
```

- [ ] 12.4 In `frontend/src/app/app.routes.ts`, replace the block from `  // Accounting routes` through the old `accounting/recurring` line (the one with `loadComponent`) with:

```typescript
  // Accounting routes
  { path: 'accounting', redirectTo: 'accounting/accounts', pathMatch: 'full' },
  { path: 'accounting/dashboard', redirectTo: 'accounting/accounts' },
  { path: 'accounting/transactions', redirectTo: 'accounting/accounts' },
  { path: 'accounting/settings', redirectTo: 'accounting/accounts' },
  { path: 'accounting/cards', redirectTo: 'accounting/accounts' },
  { path: 'accounting/categories', redirectTo: 'accounting/accounts' },
  { path: 'accounting/recurring', redirectTo: 'accounting/accounts' },
```

- [ ] 12.5 In `frontend/src/app/components/shell/navigation.ts`, replace the accounting group's `defaultPath` and `items` (from `    defaultPath: '/accounting/transactions',` through the `recurring` item line) with:

```typescript
    defaultPath: '/accounting/accounts',
    items: [
      { id: 'accounting', path: '/accounting/accounts', icon: 'pi-wallet', label: '帳戶', title: '記帳帳戶', group: 'accounting' },
      { id: 'settings', path: '/settings', icon: 'pi-cog', label: '設定', title: '設定', group: 'accounting', sub: true, exact: true },
```

- [ ] 12.6 Run the full frontend suite and a build.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false 2>&1 | tail -4 && npx ng build 2>&1 | grep -E "ERROR|complete"
```

Expected: all test files pass (including `app.routes.spec.ts (3 tests)`), and `Application bundle generation complete.` with no `ERROR`.

- [ ] 12.7 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add frontend/src/app/app.routes.ts frontend/src/app/app.routes.spec.ts frontend/src/app/components/shell/navigation.ts
git commit -m "refactor(frontend)!: remove legacy accounting pages"
```

## 13. Ledger data layer and amount formatting

**Files:**
- Create: `frontend/src/app/models/accounting.model.ts`, `frontend/src/app/services/accounting.service.ts`, `frontend/src/app/services/accounting.service.spec.ts`, `frontend/src/app/components/accounting/format.ts`, `frontend/src/app/components/accounting/format.spec.ts`

**Interfaces:**
- Produces types: `EntryKind`, `LedgerAccount`, `LedgerEntry` (incl. `original_amount`, `original_currency`, `fx_rate`, `fx_source`), `EntryPage`, `EntryQuery`, `UnpairedTransfer`, `ImportSummary`, `ImportRun`, `ENTRY_KIND_LABELS: Record<EntryKind, string>`.
- Produces `AccountingService` (root): `getAccounts(): Observable<LedgerAccount[]>` → `GET /api/accounting/accounts`; `getEntries(accountId: number, query?: EntryQuery): Observable<EntryPage>` (omits null/undefined/empty params); `getLatestImport(): Observable<ImportRun | null>` (404 → `null`).
- Produces `formatAmount(value: string | number | null | undefined, currency: string): string` → `"<CODE> <grouped number, 0–2 decimals>"`; `isNegative(value): boolean`.

- [ ] 13.1 Write the failing tests. `frontend/src/app/components/accounting/format.spec.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import { formatAmount, isNegative } from './format';

describe('formatAmount', () => {
  it('groups thousands and keeps up to 2 decimals', () => {
    expect(formatAmount('2299.5000', 'TWD')).toBe('TWD 2,299.5');
    expect(formatAmount('1234567.891', 'TWD')).toBe('TWD 1,234,567.89');
  });

  it('formats negative balances with a minus sign', () => {
    expect(formatAmount('-1500.0000', 'TWD')).toBe('TWD -1,500');
  });

  it('formats JPY without forced decimals', () => {
    expect(formatAmount('180000.0000', 'JPY')).toBe('JPY 180,000');
  });

  it('formats non-ISO MOZE currencies such as USDT without throwing', () => {
    expect(formatAmount('12.3400', 'USDT')).toBe('USDT 12.34');
  });
});

describe('isNegative', () => {
  it('detects negative decimal strings', () => {
    expect(isNegative('-0.0100')).toBe(true);
    expect(isNegative('0.0000')).toBe(false);
  });
});
```

`frontend/src/app/services/accounting.service.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ImportRun } from '../models/accounting.model';
import { AccountingService } from './accounting.service';

describe('AccountingService', () => {
  let service: AccountingService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AccountingService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('lists accounts', () => {
    let names: string[] = [];
    service.getAccounts().subscribe(accounts => (names = accounts.map(a => a.name)));

    httpMock.expectOne('/api/accounting/accounts').flush([
      { id: 1, name: '錢包', currency: 'TWD', opening_balance: '0', balance: '10', entry_count: 1 },
    ]);

    expect(names).toEqual(['錢包']);
  });

  it('sends only the set entry query parameters', () => {
    service.getEntries(7, { limit: 50, offset: 100, kind: 'expense', date_from: null }).subscribe();

    const req = httpMock.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
    expect(req.request.params.keys().sort()).toEqual(['kind', 'limit', 'offset']);
    expect(req.request.params.get('offset')).toBe('100');
    req.flush({ items: [], total: 0, limit: 50, offset: 100 });
  });

  it('maps a 404 from imports/latest to null', () => {
    let latest: ImportRun | null | undefined;
    service.getLatestImport().subscribe(run => (latest = run));

    httpMock
      .expectOne('/api/accounting/imports/latest')
      .flush({ code: 404, message: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });

    expect(latest).toBeNull();
  });

  it('propagates other errors from imports/latest', () => {
    let failed = false;
    service.getLatestImport().subscribe({ error: () => (failed = true) });

    httpMock.expectOne('/api/accounting/imports/latest').flush('boom', { status: 500, statusText: 'Error' });

    expect(failed).toBe(true);
  });
});
```

- [ ] 13.2 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/components/accounting/format.spec.ts --include=src/app/services/accounting.service.spec.ts
```

Expected: build fails with `TS2307: Cannot find module './format'` and `TS2307: Cannot find module './accounting.service'`.

- [ ] 13.3 Create `frontend/src/app/models/accounting.model.ts`:

```typescript
export type EntryKind =
  | 'expense'
  | 'income'
  | 'transfer_out'
  | 'transfer_in'
  | 'receivable'
  | 'payable'
  | 'balance_adjustment'
  | 'fee'
  | 'discount'
  | 'reward'
  | 'interest'
  | 'refund';

export interface LedgerAccount {
  id: number;
  name: string;
  currency: string;
  opening_balance: string;
  balance: string;
  entry_count: number;
}

export interface LedgerEntry {
  id: number;
  kind: EntryKind;
  amount: string;
  currency: string;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  fx_source: 'fx_api' | 'moze_backup' | null;
  entry_date: string;
  entry_time: string | null;
  category: string | null;
  project: string | null;
  name: string | null;
  merchant: string | null;
  counterparty: string | null;
  description: string | null;
  tags: string[];
  parent_entry_id: number | null;
  transfer_group_id: string | null;
  needs_review: boolean;
  running_balance: string;
}

export interface EntryPage {
  items: LedgerEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface EntryQuery {
  limit?: number;
  offset?: number;
  kind?: EntryKind | null;
  date_from?: string | null;
  date_to?: string | null;
}

export interface UnpairedTransfer {
  row: number;
  account: string;
  kind: EntryKind;
  date: string;
  time: string | null;
  amount: string;
  currency: string;
}

export interface ImportSummary {
  kind_counts?: Record<string, number>;
  accounts_created?: string[];
  accounts_archived?: string[];
  accounts_renamed?: { from: string; to: string }[];
  unpaired_transfers?: UnpairedTransfer[];
  error?: string;
}

export interface ImportRun {
  id: number | null;
  status: 'running' | 'succeeded' | 'failed' | 'dry_run';
  started_at: string;
  finished_at: string | null;
  file_name: string;
  file_sha256: string;
  row_count: number | null;
  summary: ImportSummary | null;
}

export const ENTRY_KIND_LABELS: Record<EntryKind, string> = {
  expense: '支出',
  income: '收入',
  transfer_out: '轉出',
  transfer_in: '轉入',
  receivable: '應收款項',
  payable: '應付款項',
  balance_adjustment: '餘額調整',
  fee: '手續費',
  discount: '折扣',
  reward: '紅利回饋',
  interest: '利息',
  refund: '退款',
};
```

- [ ] 13.4 Create `frontend/src/app/services/accounting.service.ts`:

```typescript
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, of, throwError } from 'rxjs';

import { EntryPage, EntryQuery, ImportRun, LedgerAccount } from '../models/accounting.model';

@Injectable({
  providedIn: 'root',
})
export class AccountingService {
  private http = inject(HttpClient);
  private apiUrl = '/api/accounting';

  getAccounts(): Observable<LedgerAccount[]> {
    return this.http.get<LedgerAccount[]>(`${this.apiUrl}/accounts`);
  }

  getEntries(accountId: number, query: EntryQuery = {}): Observable<EntryPage> {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    }
    return this.http.get<EntryPage>(`${this.apiUrl}/accounts/${accountId}/entries`, { params });
  }

  /** Latest import run, or null when no import has run yet (HTTP 404). */
  getLatestImport(): Observable<ImportRun | null> {
    return this.http.get<ImportRun>(`${this.apiUrl}/imports/latest`).pipe(
      catchError((error: HttpErrorResponse) =>
        error.status === 404 ? of(null) : throwError(() => error),
      ),
    );
  }
}
```

- [ ] 13.5 Create `frontend/src/app/components/accounting/format.ts`:

```typescript
const NUMBER_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/**
 * Format a ledger amount in its own currency: code prefix, grouping separators, up to 2 decimals.
 * Uses the code as text (not Intl currency style) because MOZE codes such as USDT are not ISO-4217.
 */
export function formatAmount(value: string | number | null | undefined, currency: string): string {
  const amount = Number(value ?? 0);
  return `${currency} ${NUMBER_FORMAT.format(Number.isFinite(amount) ? amount : 0)}`;
}

export function isNegative(value: string | number | null | undefined): boolean {
  return Number(value ?? 0) < 0;
}
```

- [ ] 13.6 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/components/accounting/format.spec.ts --include=src/app/services/accounting.service.spec.ts
```

Expected: `Test Files  2 passed`, `Tests  9 passed`.

- [ ] 13.7 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add frontend/src/app/models/accounting.model.ts frontend/src/app/services/accounting.service.ts \
  frontend/src/app/services/accounting.service.spec.ts frontend/src/app/components/accounting/format.ts \
  frontend/src/app/components/accounting/format.spec.ts
git commit -m "feat(frontend): add ledger API client and amount formatting"
```

## 14. Accounts list page

**Files:**
- Create: `frontend/src/app/components/accounting/accounts/accounts.ts`, `accounts.html`, `accounts.scss`, `accounts.spec.ts`
- Modify: `frontend/src/app/app.routes.ts`, `frontend/src/app/app.routes.spec.ts`

**Interfaces:**
- Consumes: `AccountingService.getAccounts`, `getLatestImport`; `formatAmount`, `isNegative` (Task 13).
- Produces: `AccountingAccountsComponent` (selector `app-accounting-accounts`) with signals `accounts`, `latestImport`, `loaded`, `loadError`, computed `reviewCount` (= `summary.unpaired_transfers.length`); route `accounting/accounts`. Rows link to `/accounting/accounts/:id`. Narrow layout: grid `name | balance` / `meta | balance`, name ellipsised, balance `nowrap`.

- [ ] 14.1 Write the failing test `frontend/src/app/components/accounting/accounts/accounts.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingAccountsComponent } from './accounts';

const ACCOUNTS: LedgerAccount[] = [
  { id: 3, name: '台新信用卡', currency: 'TWD', opening_balance: '0', balance: '-15230.5000', entry_count: 120 },
  { id: 9, name: '去日本的錢', currency: 'JPY', opening_balance: '180000', balance: '178500.0000', entry_count: 4 },
];

const LATEST: ImportRun = {
  id: 1,
  status: 'succeeded',
  started_at: '2026-10-01T09:00:00+00:00',
  finished_at: '2026-10-01T09:00:02+00:00',
  file_name: 'moze.csv',
  file_sha256: 'a'.repeat(64),
  row_count: 10,
  summary: {
    unpaired_transfers: [
      { row: 5, account: '台新信用卡', kind: 'transfer_out', date: '2026-09-01', time: '12:00', amount: '-500.0000', currency: 'TWD' },
    ],
  },
};

describe('AccountingAccountsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingAccountsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function render(accounts: LedgerAccount[], latest: ImportRun | null) {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/accounts').flush(accounts);
    const latestReq = httpMock.expectOne('/api/accounting/imports/latest');
    if (latest) {
      latestReq.flush(latest);
    } else {
      latestReq.flush({ code: 404, message: 'no import has run yet' }, { status: 404, statusText: 'Not Found' });
    }
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('lists every account with currency, formatted balance and entry count', () => {
    const el = render(ACCOUNTS, LATEST);

    const rows = Array.from(el.querySelectorAll('.account-row'));
    expect(rows.map(r => r.querySelector('.account-name')?.textContent?.trim())).toEqual(['台新信用卡', '去日本的錢']);
    expect(rows[0].querySelector('.account-balance')?.textContent?.trim()).toBe('TWD -15,230.5');
    expect(rows[1].querySelector('.account-balance')?.textContent?.trim()).toBe('JPY 178,500');
    expect(rows[1].querySelector('.account-meta')?.textContent).toContain('4 筆');
    expect(rows[0].getAttribute('href')).toBe('/accounting/accounts/3');
  });

  it('marks negative balances', () => {
    const el = render(ACCOUNTS, LATEST);

    const balances = el.querySelectorAll('.account-balance');
    expect(balances[0].classList).toContain('amount--negative');
    expect(balances[1].classList).not.toContain('amount--negative');
  });

  it('shows the latest import status and entries needing review', () => {
    const el = render(ACCOUNTS, LATEST);

    const status = el.querySelector('.import-status')?.textContent ?? '';
    expect(status).toContain('成功');
    expect(status).toContain('待確認 1 筆');
  });

  it('shows an import-required empty state before any import', () => {
    const el = render([], null);

    expect(el.querySelector('.empty-state')?.textContent).toContain('尚未匯入 MOZE 資料');
    expect(el.querySelector('.import-status')).toBeNull();
  });
});
```

- [ ] 14.2 In `frontend/src/app/app.routes.spec.ts`, add these imports at the top:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
```

and add to the `describe` block:

```typescript

  it('serves the accounts list page', () => {
    expect(paths).toContain('accounting/accounts');
  });

  it('lands a bookmarked /accounting/cards on the accounts page', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
    const harness = await RouterTestingHarness.create();

    await harness.navigateByUrl('/accounting/cards');

    expect(TestBed.inject(Router).url).toBe('/accounting/accounts');
  });
```

- [ ] 14.3 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/components/accounting/accounts/accounts.spec.ts --include=src/app/app.routes.spec.ts
```

Expected: build fails with `TS2307: Cannot find module './accounts'`.

- [ ] 14.4 Create `frontend/src/app/components/accounting/accounts/accounts.ts`:

```typescript
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { formatAmount, isNegative } from '../format';

const STATUS_LABELS: Record<ImportRun['status'], string> = {
  running: '匯入中',
  succeeded: '成功',
  failed: '失敗',
  dry_run: '試算',
};

@Component({
  selector: 'app-accounting-accounts',
  standalone: true,
  imports: [DatePipe, RouterLink],
  templateUrl: './accounts.html',
  styleUrl: './accounts.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingAccountsComponent implements OnInit {
  private accountingService = inject(AccountingService);

  readonly accounts = signal<LedgerAccount[]>([]);
  readonly latestImport = signal<ImportRun | null>(null);
  readonly loaded = signal(false);
  readonly loadError = signal(false);
  readonly reviewCount = computed(
    () => this.latestImport()?.summary?.unpaired_transfers?.length ?? 0,
  );
  readonly formatAmount = formatAmount;
  readonly isNegative = isNegative;

  ngOnInit(): void {
    forkJoin({
      accounts: this.accountingService.getAccounts(),
      latest: this.accountingService.getLatestImport(),
    }).subscribe({
      next: ({ accounts, latest }) => {
        this.accounts.set(accounts);
        this.latestImport.set(latest);
        this.loaded.set(true);
      },
      error: () => {
        this.loadError.set(true);
        this.loaded.set(true);
      },
    });
  }

  statusLabel(run: ImportRun): string {
    return STATUS_LABELS[run.status];
  }
}
```

- [ ] 14.5 Create `frontend/src/app/components/accounting/accounts/accounts.html`:

```html
<section class="ledger-accounts">
  @if (latestImport(); as run) {
    <p class="import-status" [class.import-status--failed]="run.status === 'failed'">
      最近匯入 {{ run.started_at | date: 'yyyy-MM-dd HH:mm' }} · {{ statusLabel(run) }}
      · 待確認 {{ reviewCount() }} 筆
    </p>
  }

  @if (loadError()) {
    <p class="load-error">帳戶讀取失敗，請稍後再試。</p>
  } @else if (loaded() && accounts().length === 0) {
    <div class="empty-state">
      <i class="pi pi-upload"></i>
      <h4>尚未匯入 MOZE 資料</h4>
      <p>
        請先匯入 MOZE 匯出的 CSV：<code>python -m app.services.moze_import_service &lt;檔案&gt;</code>，
        或上傳至 <code>POST /api/accounting/imports/moze</code>。
      </p>
    </div>
  } @else {
    <ul class="account-list">
      @for (account of accounts(); track account.id) {
        <li>
          <a class="account-row" [routerLink]="['/accounting/accounts', account.id]">
            <span class="account-name">{{ account.name }}</span>
            <span class="account-meta">{{ account.currency }} · {{ account.entry_count }} 筆</span>
            <span class="account-balance" [class.amount--negative]="isNegative(account.balance)">
              {{ formatAmount(account.balance, account.currency) }}
            </span>
          </a>
        </li>
      }
    </ul>
  }
</section>
```

- [ ] 14.6 Create `frontend/src/app/components/accounting/accounts/accounts.scss`:

```scss
.ledger-accounts {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-width: 100%;
  min-width: 0;
}

.import-status {
  color: var(--app-text-muted);
  font-size: 0.85rem;
  font-weight: 600;
  margin: 0;
  overflow-wrap: anywhere;
}

.import-status--failed,
.load-error {
  color: var(--app-danger);
}

.account-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
}

.account-row {
  align-items: center;
  border: 1px solid var(--app-border);
  border-radius: 0.75rem;
  color: var(--app-text);
  display: grid;
  gap: 0.15rem 0.75rem;
  grid-template-areas:
    'name balance'
    'meta balance';
  grid-template-columns: minmax(0, 1fr) auto;
  padding: 0.75rem 1rem;
  text-decoration: none;
}

.account-name {
  font-weight: 800;
  grid-area: name;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-meta {
  color: var(--app-text-muted);
  font-size: 0.8rem;
  grid-area: meta;
}

.account-balance {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  grid-area: balance;
  text-align: right;
  white-space: nowrap;
}

.amount--negative {
  color: var(--app-danger);
}

.empty-state {
  color: var(--app-text-muted);
  padding: 2rem 1rem;
  text-align: center;

  code {
    overflow-wrap: anywhere;
  }
}
```

- [ ] 14.7 In `frontend/src/app/app.routes.ts`, add below the `accounting/recurring` redirect line:

```typescript
  { path: 'accounting/accounts', loadComponent: () => import('./components/accounting/accounts/accounts').then(m => m.AccountingAccountsComponent) },
```

- [ ] 14.8 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/components/accounting/accounts/accounts.spec.ts --include=src/app/app.routes.spec.ts
```

Expected: `Test Files  2 passed`, `Tests  9 passed`.

- [ ] 14.9 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add frontend/src/app/components/accounting/accounts frontend/src/app/app.routes.ts frontend/src/app/app.routes.spec.ts
git commit -m "feat(frontend): add accounting accounts list page"
```

## 15. Account entry history page

**Files:**
- Create: `frontend/src/app/components/accounting/account-entries/account-entries.ts`, `account-entries.html`, `account-entries.scss`, `account-entries.spec.ts`
- Modify: `frontend/src/app/app.routes.ts`, `frontend/src/app/app.routes.spec.ts`

**Interfaces:**
- Consumes: `AccountingService.getAccounts`, `getEntries`; `ENTRY_KIND_LABELS`; `formatAmount`, `isNegative`.
- Produces: `AccountingAccountEntriesComponent` (selector `app-accounting-account-entries`), `PAGE_SIZE = 50`; `originalTitle(entry: LedgerEntry): string | null` (amount tooltip `原幣 JPY -1,800 · 匯率 0.2` for converted entries, `null` otherwise); `load(reset: boolean): void` (offset 0 when `reset`, else current length), `setKind(value: string)`, `setDateFrom(value: string)`, `setDateTo(value: string)` (each reloads from offset 0), `hasMore` computed; route `accounting/accounts/:id`. Below 576 px each row collapses to `date | amount` / `category | name`; kind, project and running balance are hidden there.

- [ ] 15.1 Write the failing test `frontend/src/app/components/accounting/account-entries/account-entries.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting, TestRequest } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EntryPage, LedgerEntry } from '../../../models/accounting.model';
import { AccountingAccountEntriesComponent } from './account-entries';

function entry(id: number, overrides: Partial<LedgerEntry> = {}): LedgerEntry {
  return {
    id,
    kind: 'expense',
    amount: '-120.0000',
    currency: 'TWD',
    original_amount: null,
    original_currency: null,
    fx_rate: null,
    fx_source: null,
    entry_date: '2026-09-01',
    entry_time: '12:30:00',
    category: '飲食/午餐',
    project: '日本行',
    name: '便當',
    merchant: '池上',
    counterparty: null,
    description: null,
    tags: [],
    parent_entry_id: null,
    transfer_group_id: null,
    needs_review: false,
    running_balance: '1880.0000',
    ...overrides,
  };
}

describe('AccountingAccountEntriesComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountingAccountEntriesComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: convertToParamMap({ id: '7' }) } } },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  function expectEntries(): TestRequest {
    return httpMock.expectOne(r => r.url === '/api/accounting/accounts/7/entries');
  }

  function page(items: LedgerEntry[], total: number, offset = 0): EntryPage {
    return { items, total, limit: 50, offset };
  }

  function render(first: EntryPage) {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    httpMock.expectOne('/api/accounting/accounts').flush([
      { id: 7, name: '錢包', currency: 'TWD', opening_balance: '2000', balance: '1880.0000', entry_count: 1 },
    ]);
    const req = expectEntries();
    expect(req.request.params.get('offset')).toBe('0');
    expect(req.request.params.get('limit')).toBe('50');
    req.flush(first);
    fixture.detectChanges();
    return fixture;
  }

  it('shows each entry with date, kind, category, name, project, amount and running balance', () => {
    const fixture = render(page([entry(1)], 1));
    const row = (fixture.nativeElement as HTMLElement).querySelector('.entry')!;

    expect(row.querySelector('.entry-date')?.textContent).toContain('2026-09-01 12:30');
    expect(row.querySelector('.entry-kind')?.textContent?.trim()).toBe('支出');
    expect(row.querySelector('.entry-category')?.textContent?.trim()).toBe('飲食/午餐');
    expect(row.querySelector('.entry-name')?.textContent?.trim()).toBe('便當 · 池上');
    expect(row.querySelector('.entry-project')?.textContent?.trim()).toBe('日本行');
    expect(row.querySelector('.entry-amount')?.textContent?.trim()).toBe('TWD -120');
    expect(row.querySelector('.entry-running')?.textContent?.trim()).toBe('TWD 1,880');
    expect((fixture.nativeElement as HTMLElement).querySelector('h2')?.textContent).toContain('錢包');
  });

  it('loads more entries with the next offset', () => {
    const fixture = render(page([entry(1)], 2));
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('.load-more')!;

    button.click();
    const req = expectEntries();
    expect(req.request.params.get('offset')).toBe('1');
    req.flush(page([entry(2)], 2, 1));
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry');
    expect(rows.length).toBe(2);
    expect((fixture.nativeElement as HTMLElement).querySelector('.load-more')).toBeNull();
  });

  it('reloads from offset 0 when the kind or date filter changes', () => {
    const fixture = render(page([entry(1)], 1));
    const el = fixture.nativeElement as HTMLElement;

    const kind = el.querySelector<HTMLSelectElement>('.filter-kind')!;
    kind.value = 'transfer_out';
    kind.dispatchEvent(new Event('change'));
    let req = expectEntries();
    expect(req.request.params.get('kind')).toBe('transfer_out');
    expect(req.request.params.get('offset')).toBe('0');
    req.flush(page([], 0));

    const from = el.querySelector<HTMLInputElement>('.filter-from')!;
    from.value = '2026-09-01';
    from.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('date_from')).toBe('2026-09-01');
    expect(req.request.params.get('kind')).toBe('transfer_out');
    req.flush(page([], 0));

    const to = el.querySelector<HTMLInputElement>('.filter-to')!;
    to.value = '2026-09-30';
    to.dispatchEvent(new Event('change'));
    req = expectEntries();
    expect(req.request.params.get('date_to')).toBe('2026-09-30');
    req.flush(page([], 0));
  });

  it('marks entries that need review', () => {
    const fixture = render(page([entry(1, { kind: 'transfer_out', needs_review: true }), entry(2)], 2));
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry');

    expect(rows[0].classList).toContain('entry--review');
    expect(rows[0].querySelector('.review-marker')?.textContent).toContain('待確認');
    expect(rows[1].querySelector('.review-marker')).toBeNull();
  });

  it('shows an untimed entry with its date only', () => {
    const fixture = render(page([entry(1, { entry_time: null })], 1));
    const date = (fixture.nativeElement as HTMLElement).querySelector('.entry-date')?.textContent?.trim();
    expect(date).toBe('2026-09-01');
  });

  it('shows the original amount and rate of a converted entry as a tooltip', () => {
    const fixture = render(
      page([
        entry(1, { amount: '-360.0000', original_amount: '-1800.0000', original_currency: 'JPY', fx_rate: '0.2000000000', fx_source: 'fx_api' }),
        entry(2),
      ], 2),
    );
    const amounts = (fixture.nativeElement as HTMLElement).querySelectorAll('.entry-amount');

    expect(amounts[0].getAttribute('title')).toBe('原幣 JPY -1,800 · 匯率 0.2');
    expect(amounts[1].hasAttribute('title')).toBe(false);
  });
});
```

- [ ] 15.2 In `frontend/src/app/app.routes.spec.ts`, add `import { DockComponent } from './components/dock/dock';`, change the navigation import to `import { NAV_GROUPS, NAV_ITEMS, navItemForUrl } from './components/shell/navigation';`, and add to the `describe` block:

```typescript

  it('serves the account entry history page', () => {
    expect(paths).toContain('accounting/accounts/:id');
  });

  it('keeps the Accounting dock item highlighted on an account history page', () => {
    TestBed.configureTestingModule({ imports: [DockComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(DockComponent);

    fixture.componentRef.setInput('activeId', navItemForUrl('/accounting/accounts/5').id);
    fixture.detectChanges();

    const active = (fixture.nativeElement as HTMLElement).querySelector('.dock-item[aria-current="page"]');
    expect(navItemForUrl('/accounting/accounts/5').id).toBe('accounting');
    expect(active?.getAttribute('aria-label')).toBe('記帳帳戶');
    expect(active?.classList).toContain('active');
  });
```

The final file must equal:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { DockComponent } from './components/dock/dock';
import { NAV_GROUPS, NAV_ITEMS, navItemForUrl } from './components/shell/navigation';

const REMOVED_ACCOUNTING_PAGES = ['dashboard', 'transactions', 'settings', 'cards', 'categories', 'recurring'];

describe('accounting routes', () => {
  const paths = routes.map(route => route.path);

  it('redirects the removed accounting pages to the accounts page', () => {
    for (const removed of REMOVED_ACCOUNTING_PAGES) {
      const route = routes.find(r => r.path === `accounting/${removed}`);
      expect(route?.redirectTo).toBe('accounting/accounts');
      expect(route?.loadComponent).toBeUndefined();
    }
  });

  it('redirects /accounting to the accounts page', () => {
    expect(routes.find(route => route.path === 'accounting')?.redirectTo).toBe('accounting/accounts');
  });

  it('points the accounting navigation only at the ledger pages and settings', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting/accounts');
    expect(accounting.items.map(item => item.path)).toEqual(['/accounting/accounts', '/settings']);
    expect(NAV_ITEMS.map(item => item.id)).not.toContain('accounting-dash');
  });

  it('serves the accounts list page', () => {
    expect(paths).toContain('accounting/accounts');
  });

  it('lands a bookmarked /accounting/cards on the accounts page', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
    const harness = await RouterTestingHarness.create();

    await harness.navigateByUrl('/accounting/cards');

    expect(TestBed.inject(Router).url).toBe('/accounting/accounts');
  });

  it('serves the account entry history page', () => {
    expect(paths).toContain('accounting/accounts/:id');
  });

  it('keeps the Accounting dock item highlighted on an account history page', () => {
    TestBed.configureTestingModule({ imports: [DockComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(DockComponent);

    fixture.componentRef.setInput('activeId', navItemForUrl('/accounting/accounts/5').id);
    fixture.detectChanges();

    const active = (fixture.nativeElement as HTMLElement).querySelector('.dock-item[aria-current="page"]');
    expect(navItemForUrl('/accounting/accounts/5').id).toBe('accounting');
    expect(active?.getAttribute('aria-label')).toBe('記帳帳戶');
    expect(active?.classList).toContain('active');
  });
});
```

- [ ] 15.3 Run them.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false --include=src/app/components/accounting/account-entries/account-entries.spec.ts --include=src/app/app.routes.spec.ts
```

Expected: build fails with `TS2307: Cannot find module './account-entries'`.

- [ ] 15.4 Create `frontend/src/app/components/accounting/account-entries/account-entries.ts`:

```typescript
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import {
  ENTRY_KIND_LABELS,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { formatAmount, isNegative } from '../format';

export const PAGE_SIZE = 50;

@Component({
  selector: 'app-accounting-account-entries',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './account-entries.html',
  styleUrl: './account-entries.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingAccountEntriesComponent implements OnInit {
  private accountingService = inject(AccountingService);
  private route = inject(ActivatedRoute);

  readonly accountId = Number(this.route.snapshot.paramMap.get('id'));
  readonly account = signal<LedgerAccount | null>(null);
  readonly entries = signal<LedgerEntry[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly loadError = signal(false);
  readonly kind = signal<EntryKind | null>(null);
  readonly dateFrom = signal<string | null>(null);
  readonly dateTo = signal<string | null>(null);
  readonly hasMore = computed(() => this.entries().length < this.total());

  readonly kindOptions = Object.entries(ENTRY_KIND_LABELS) as [EntryKind, string][];
  readonly kindLabels = ENTRY_KIND_LABELS;
  readonly formatAmount = formatAmount;
  readonly isNegative = isNegative;

  ngOnInit(): void {
    this.accountingService.getAccounts().subscribe({
      next: accounts => this.account.set(accounts.find(a => a.id === this.accountId) ?? null),
    });
    this.load(true);
  }

  load(reset: boolean): void {
    this.loading.set(true);
    this.loadError.set(false);
    const offset = reset ? 0 : this.entries().length;
    this.accountingService
      .getEntries(this.accountId, {
        limit: PAGE_SIZE,
        offset,
        kind: this.kind(),
        date_from: this.dateFrom(),
        date_to: this.dateTo(),
      })
      .subscribe({
        next: page => {
          this.entries.set(reset ? page.items : [...this.entries(), ...page.items]);
          this.total.set(page.total);
          this.loading.set(false);
        },
        error: () => {
          this.loadError.set(true);
          this.loading.set(false);
        },
      });
  }

  setKind(value: string): void {
    this.kind.set(value ? (value as EntryKind) : null);
    this.load(true);
  }

  setDateFrom(value: string): void {
    this.dateFrom.set(value || null);
    this.load(true);
  }

  setDateTo(value: string): void {
    this.dateTo.set(value || null);
    this.load(true);
  }

  when(entry: LedgerEntry): string {
    return entry.entry_time ? `${entry.entry_date} ${entry.entry_time.slice(0, 5)}` : entry.entry_date;
  }

  /** Tooltip for converted foreign-currency entries: the amount as recorded and the rate used. */
  originalTitle(entry: LedgerEntry): string | null {
    if (entry.original_amount === null || entry.original_currency === null) {
      return null;
    }
    return `原幣 ${formatAmount(entry.original_amount, entry.original_currency)} · 匯率 ${Number(entry.fx_rate)}`;
  }

  title(entry: LedgerEntry): string {
    return [entry.name, entry.merchant].filter(Boolean).join(' · ');
  }
}
```

- [ ] 15.5 Create `frontend/src/app/components/accounting/account-entries/account-entries.html`:

```html
<section class="ledger-entries">
  <header class="entries-header">
    <a routerLink="/accounting/accounts" class="back-link"><i class="pi pi-arrow-left"></i> 帳戶</a>
    <h2>{{ account()?.name ?? '帳戶 #' + accountId }}</h2>
    @if (account(); as a) {
      <p class="entries-balance" [class.amount--negative]="isNegative(a.balance)">
        {{ formatAmount(a.balance, a.currency) }}
      </p>
    }
  </header>

  <div class="entries-filters">
    <label>
      <span>類型</span>
      <select class="filter-kind" (change)="setKind($any($event.target).value)">
        <option value="">全部</option>
        @for (option of kindOptions; track option[0]) {
          <option [value]="option[0]">{{ option[1] }}</option>
        }
      </select>
    </label>
    <label>
      <span>起</span>
      <input class="filter-from" type="date" (change)="setDateFrom($any($event.target).value)" />
    </label>
    <label>
      <span>迄</span>
      <input class="filter-to" type="date" (change)="setDateTo($any($event.target).value)" />
    </label>
  </div>

  @if (loadError()) {
    <p class="load-error">紀錄讀取失敗，請稍後再試。</p>
  }

  <ul class="entry-list">
    @for (entry of entries(); track entry.id) {
      <li class="entry" [class.entry--review]="entry.needs_review">
        <span class="entry-date">
          {{ when(entry) }}
          @if (entry.needs_review) {
            <span class="review-marker" title="未配對的轉帳，請確認">待確認</span>
          }
        </span>
        <span class="entry-kind">{{ kindLabels[entry.kind] }}</span>
        <span class="entry-category">{{ entry.category ?? '—' }}</span>
        <span class="entry-name">{{ title(entry) }}</span>
        <span class="entry-project">{{ entry.project ?? '' }}</span>
        <span
          class="entry-amount"
          [class.amount--negative]="isNegative(entry.amount)"
          [attr.title]="originalTitle(entry)">
          {{ formatAmount(entry.amount, entry.currency) }}
        </span>
        <span class="entry-running">{{ formatAmount(entry.running_balance, entry.currency) }}</span>
      </li>
    } @empty {
      @if (!loading()) {
        <li class="entries-empty">沒有符合條件的紀錄</li>
      }
    }
  </ul>

  @if (hasMore()) {
    <button type="button" class="load-more" [disabled]="loading()" (click)="load(false)">
      載入更多（{{ entries().length }} / {{ total() }}）
    </button>
  }
</section>
```

- [ ] 15.6 Create `frontend/src/app/components/accounting/account-entries/account-entries.scss`:

```scss
.ledger-entries {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-width: 100%;
  min-width: 0;
}

.entries-header h2 {
  font-size: 1.15rem;
  font-weight: 800;
  margin: 0.25rem 0 0;
  overflow-wrap: anywhere;
}

.back-link {
  color: var(--app-text-muted);
  font-size: 0.85rem;
  text-decoration: none;
}

.entries-balance {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  margin: 0.25rem 0 0;
}

.entries-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;

  label {
    display: flex;
    flex: 1 1 8rem;
    flex-direction: column;
    font-size: 0.8rem;
    gap: 0.15rem;
    min-width: 0;
  }

  select,
  input {
    max-width: 100%;
    min-width: 0;
  }
}

.entry-list {
  display: flex;
  flex-direction: column;
  list-style: none;
  margin: 0;
  padding: 0;
}

.entry {
  align-items: baseline;
  border-bottom: 1px solid var(--app-border);
  display: grid;
  gap: 0.25rem 0.75rem;
  grid-template-areas: 'date kind category name project amount running';
  grid-template-columns: 9.5rem 5rem minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 0.8fr) auto auto;
  padding: 0.5rem 0;
}

.entry > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-date { grid-area: date; }
.entry-kind { grid-area: kind; }
.entry-category { grid-area: category; }
.entry-name { grid-area: name; }
.entry-project { grid-area: project; color: var(--app-text-muted); }

.entry-amount,
.entry-running {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.entry-amount { grid-area: amount; font-weight: 700; }
.entry-running { grid-area: running; color: var(--app-text-muted); }

.entry--review {
  background: color-mix(in srgb, var(--app-danger) 8%, transparent);
}

.review-marker {
  border: 1px solid var(--app-danger);
  border-radius: 0.5rem;
  color: var(--app-danger);
  font-size: 0.7rem;
  margin-left: 0.25rem;
  padding: 0 0.3rem;
}

.amount--negative {
  color: var(--app-danger);
}

.load-error {
  color: var(--app-danger);
}

.entries-empty {
  color: var(--app-text-muted);
  padding: 1rem 0;
  text-align: center;
}

@media (max-width: 575.98px) {
  .entry {
    grid-template-areas:
      'date amount'
      'category name';
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .entry-kind,
  .entry-project,
  .entry-running {
    display: none;
  }

  .entry-name {
    text-align: right;
  }
}
```

- [ ] 15.7 In `frontend/src/app/app.routes.ts`, add below the `accounting/accounts` route:

```typescript
  { path: 'accounting/accounts/:id', loadComponent: () => import('./components/accounting/account-entries/account-entries').then(m => m.AccountingAccountEntriesComponent) },
```

- [ ] 15.8 Run the full frontend suite and a build.

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false 2>&1 | tail -4 && npx ng build 2>&1 | grep -E "ERROR|complete"
```

Expected: all test files pass (`account-entries.spec.ts (6 tests)`, `app.routes.spec.ts (7 tests)`), and `Application bundle generation complete.` with no `ERROR`.

- [ ] 15.9 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add frontend/src/app/components/accounting/account-entries frontend/src/app/app.routes.ts frontend/src/app/app.routes.spec.ts
git commit -m "feat(frontend): add account entry history page"
```

## 16. Docs, config flag and Caddy route list

**Files:**
- Modify: `services/accounting-service/README.md`, `.env.example`
- Operator-only (outside the repo, not committed): `/home/opc/workspace/vaultwarden/Caddyfile`

**Interfaces:** documents `ACCOUNTING_IMPORT_LOCKED`, FX egress, the CLI and the in-service API paths; Caddy serves the SPA for `/accounting`, `/accounting/accounts`, `/accounting/accounts/<n>` and the five removed pages (so the SPA can redirect bookmarked links).

- [ ] 16.1 Replace `services/accounting-service/README.md` with:

```markdown
Accounting Service
==================

FastAPI service holding the MOZE-based personal ledger: accounts, categories, projects and ledger entries.

Setup
-----

    uv venv --python 3.13 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    .venv/bin/alembic upgrade head
    .venv/bin/uvicorn app.main:app --port 8000

Configuration comes from the repository root `.env` (`POSTGRES_*`, `DB_HOST`, `ACCOUNTING_DB`, OpenTelemetry variables).
Set `ACCOUNTING_IMPORT_LOCKED=true` after the cutover to refuse every further MOZE import.
Imports with foreign-currency rows need outbound HTTPS to `cdn.jsdelivr.net` (fallback `currency-api.pages.dev`) for daily rates; fetched rates are cached in the `fx_rate` table and never cleared.

MOZE import
-----------

    .venv/bin/python -m app.services.moze_import_service <export.csv> --dry-run
    .venv/bin/python -m app.services.moze_import_service <export.csv> [--rename OLD=NEW ...]

An import replaces every `moze_import` entry in one transaction and records an `import_run`.
Only one import runs at a time (PostgreSQL advisory lock); a second one is refused.

API
---

Paths inside the service; the dev proxy and Caddy add the `/api/accounting` prefix.

- `GET /accounts`
- `GET /accounts/{id}/entries?limit=50&offset=0&kind=&date_from=&date_to=`
- `POST /imports/moze` (multipart `file`, optional `renames` JSON object, `?dry_run=true`)
- `GET /imports/latest`

Tests
-----

    .venv/bin/pytest

Integration tests create a throw-away database `accounting_test_<random>` on the Postgres server named in the root `.env` and drop it afterwards.
```

- [ ] 16.2 In the root `.env.example`, add below `OTEL_SERVICE_NAME_ACCOUNTING=accounting-service`:

```text
ACCOUNTING_IMPORT_LOCKED=false
```

- [ ] 16.3 Verify the backend suite still passes.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
```

Expected: `89 passed`.

- [ ] 16.4 Commit.

```bash
cd /home/opc/workspace/home-hub-moze
git add services/accounting-service/README.md .env.example
git commit -m "docs(accounting): document MOZE import, API paths and import lock flag"
```

- [ ] 16.5 **Operator deploy step (not committed; run only when this branch is deployed together with the new SPA build).** Edit the `@hub_spa` matcher in place. The container bind-mounts the file, so the edit must keep the inode (no `sed -i`).

```bash
cd /home/opc/workspace/vaultwarden
command cp -p Caddyfile Caddyfile.bak-moze-ledger
python3 - <<'EOF'
path = "Caddyfile"
text = open(path, encoding="utf-8").read()
old = '|/accounting/(dashboard|transactions|settings|cards|categories|recurring))$"'
new = '|/accounting|/accounting/accounts|/accounting/accounts/[0-9]+|/accounting/(dashboard|transactions|settings|cards|categories|recurring))$"'
assert text.count(old) == 1, "unexpected @hub_spa line"
with open(path, "r+", encoding="utf-8") as handle:  # same inode
    handle.write(text.replace(old, new))
    handle.truncate()
EOF
grep -n '@hub_spa path_regexp' Caddyfile
docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
curl -s -o /dev/null -w '%{http_code}\n' https://oracle.saola-mamba.ts.net/hub/accounting/accounts/12
curl -s -o /dev/null -w '%{http_code}\n' https://oracle.saola-mamba.ts.net/hub/accounting/cards
curl -s -o /dev/null -w '%{http_code}\n' https://oracle.saola-mamba.ts.net/hub/accounting/transactions
```

Expected: the `grep` line ends in `|/accounting|/accounting/accounts|/accounting/accounts/[0-9]+|/accounting/(dashboard|transactions|settings|cards|categories|recurring))$"`, `Valid configuration`, then `200`, `200` and `200` (the SPA then redirects both old paths to `/accounting/accounts`).

## 17. Verify against the real MOZE export

**Files:** none committed. Reports go to `/home/opc/workspace/moze-verify/` (outside the repo). The CSV is read in place and never printed.

**Interfaces:** Consumes the CLI (Task 9), the API (Tasks 10–11) and the pages (Tasks 14–15). Uses a fresh database `accounting_moze_verify` (spec: "imported against a fresh database"), so the live `accounting_db` behind `:8000` is not migrated before deploy. Needs outbound HTTPS to `cdn.jsdelivr.net` (or `currency-api.pages.dev`) for about 50 days of JPY/USD/TWD rates; the dry run fetches them without caching, the real import fetches and caches them.

- [ ] 17.1 Run both full suites.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/pytest -q -p no:warnings
cd /home/opc/workspace/home-hub-moze/frontend && npx ng test --watch=false 2>&1 | tail -4
```

Expected: `89 passed`; all frontend test files pass.

- [ ] 17.2 Create the fresh database and point the worktree `.env` at it (the symlink becomes a filtered private copy; nothing is printed).

```bash
docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_moze_verify'
cd /home/opc/workspace/home-hub-moze
rm -f .env
sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_moze_verify/' \
    -e 's/^ACCOUNTING_SERVICE_PORT=.*/ACCOUNTING_SERVICE_PORT=8010/' \
    /home/opc/workspace/home-hub/.env > .env
chmod 600 .env
mkdir -p /home/opc/workspace/moze-verify
cd services/accounting-service && .venv/bin/alembic upgrade head
```

Expected: Alembic logs `Running upgrade  -> aaaa59cad2a5`, `aaaa59cad2a5 -> 8a4c4f9b2d1b` and `8a4c4f9b2d1b -> 5d2e7c9a1b3f`.

- [ ] 17.3 Dry run and check the spec numbers.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service
.venv/bin/python -m app.services.moze_import_service /home/opc/workspace/MOZE_20261001_170037.csv --dry-run \
  > /home/opc/workspace/moze-verify/dry-run.json; echo "exit=$?"
.venv/bin/python - <<'EOF'
import json
report = json.load(open("/home/opc/workspace/moze-verify/dry-run.json"))
summary = report["summary"]
print(report["status"], report["row_count"], len(summary["accounts"]), summary["pairing"], len(summary["unpaired_transfers"]))
print(dict(sorted(summary["kind_counts"].items())))
converted = [a for a in summary["accounts"] if a["converted_entry_count"]]
print(len(converted), sum(a["converted_entry_count"] for a in converted))
EOF
docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d accounting_moze_verify -At -c "SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM import_run), (SELECT count(*) FROM fx_rate)"'
```

Expected: `exit=0`; `dry_run 6976 61 {'pass1': 316, 'pass2': 176, 'pass3': 17} 0`; `{'balance_adjustment': 70, 'discount': 12, 'expense': 2520, 'fee': 313, 'income': 216, 'interest': 36, 'payable': 39, 'receivable': 617, 'reward': 2327, 'transfer_in': 509, 'transfer_out': 509}` (fee = 64 records + 249 fee columns, discount = 8 + 4); `9 176` (130 foreign-currency rows + 46 fee children of those rows, on 9 accounts); and `0|0|0|0` (dry run wrote nothing, not even rates). Measured on 2026-10-02: about 9 s with an empty rate cache.

- [ ] 17.4 Real import, timed.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service
/usr/bin/time -f "elapsed=%e s" .venv/bin/python -m app.services.moze_import_service /home/opc/workspace/MOZE_20261001_170037.csv \
  > /home/opc/workspace/moze-verify/import.json; echo "exit=$?"
docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d accounting_moze_verify -At -c "SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM ledger_entry WHERE needs_review), (SELECT count(DISTINCT transfer_group_id) FROM ledger_entry), (SELECT status FROM import_run ORDER BY id DESC LIMIT 1)"'
docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d accounting_moze_verify -At -c "SELECT count(*) FILTER (WHERE parent_entry_id IS NULL), count(*), count(DISTINCT account_id) FROM ledger_entry WHERE fx_source = '"'"'fx_api'"'"'" -c "SELECT count(*) FROM fx_rate"'
.venv/bin/python - <<'EOF'
import json
for account in json.load(open("/home/opc/workspace/moze-verify/import.json"))["summary"]["accounts"]:
    if account["converted_entry_count"]:
        print(account["name"], account["currency"], account["converted_entry_count"], account["converted_amount"], account["balance"])
EOF
```

Expected: `elapsed=` below 30 s; `exit=0`; `61|7168|0|509|succeeded` (7,168 = 6,915 records + 249 fee + 4 discount children); `130|176|9` (130 converted row entries — the spec's "exactly 130 entries across 9 accounts" — plus 46 converted fee children); a non-zero `fx_rate` count (53 on 2026-10-02); then 9 lines `name currency converted_entry_count converted_amount balance`, one per converted account. Keep that list for 17.7.

- [ ] 17.5 Serve the API on :8010 and the SPA on :4300 against the verify database. Start each command as a long-running background process (e.g. `run_in_background`), not in the foreground.

```bash
cd /home/opc/workspace/home-hub-moze/services/accounting-service && .venv/bin/uvicorn app.main:app --port 8010
```

```bash
cd /home/opc/workspace/home-hub-moze/frontend && npx ng serve --port 4300
```

When `curl -s http://localhost:8010/health` returns `{"status":"ok"}`, check the account count:

```bash
curl -s http://localhost:8010/accounts | python3 -c "import json, sys; print(len(json.load(sys.stdin)))"
```

Expected: `61`.

- [ ] 17.6 Mobile check: from the owner's machine run `ssh -L 4300:localhost:4300 opc@<this host>`, open `http://localhost:4300/hub/accounting/accounts` and one account's history in Chrome DevTools at 390 × 844, and run `document.documentElement.scrollWidth <= window.innerWidth` in the console on both pages.

Expected: `true` on both; every balance readable; history rows show date + amount on line 1 and category + name on line 2.

- [ ] 17.7 Owner checklist (manual):
  - Compare the balance shown on `/accounting/accounts` with MOZE for at least 10 accounts, including 3 credit cards and 1 JPY account, all chosen among the 52 accounts **not** in the 17.4 converted list. These must match exactly. Record pass/fail per account (initials only, no amounts) in the PR description. A credit-card mismatch equal to that card's fee or discount total means design D4 (balance effect = 金額 + 手續費 + 折扣) is wrong: stop and report.
  - For each of the 9 converted accounts, note the difference between HomeHub and MOZE next to its `converted_amount` from 17.4. A small difference is expected (design D11: MOZE used its own rate or the bank's amount) and is informational, not a failure.

- [ ] 17.8 Clean up: stop both servers, restore the `.env` symlink, drop the verify database once the owner has finished.

```bash
pkill -f "[u]vicorn app.main:app --port 8010"
pkill -f "[n]g serve --port 4300"
cd /home/opc/workspace/home-hub-moze && rm -f .env && ln -s /home/opc/workspace/home-hub/.env .env
git status --short
docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" accounting_moze_verify'
```

Expected: `git status --short` prints nothing.
