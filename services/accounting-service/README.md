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
