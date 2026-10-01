## Why

The owner keeps all personal bookkeeping in MOZE (iOS, paid) — 6,976 records since 2020-09 across 61 accounts and 4 currencies — and wants to move fully into HomeHub by the end of October 2026. HomeHub's accounting-service cannot hold that data today: it has no account or balance concept, stores TWD-only integer amounts, has single-level categories, and only knows EXPENSE/INCOME. Its database (`accounting_db`) is empty, so the model can be replaced without migrating any data.

This change is the proof-of-concept and the foundation for the whole migration. It establishes an account-based, multi-currency ledger and a MOZE CSV importer whose output reconciles with MOZE's account balances. Entry, reward rules, statements, net worth, receivables views and the Hermes integration follow in later changes (see design.md "Phasing").

## What Changes

- **BREAKING**: Drop the current accounting tables (`transactions`, `categories`, `credit_cards`, `subscriptions`, `installments`, `payment_methods`) and their endpoints. The database holds zero rows, so no data is lost.
- Add the ledger model: `account`, `category` (two levels, scoped by record kind), `project`, `ledger_entry` (signed `NUMERIC(20,4)` amounts with per-entry currency, record kind, source, transfer grouping).
- Add a MOZE CSV importer (CLI + REST) that performs a transactional full replace of MOZE-sourced data, auto-creates accounts, categories and projects, pairs transfers, and produces an import report with per-account balances and unpaired transfers.
- Add read-only REST endpoints: list accounts with balances, list an account's entries, get the latest import report.
- Replace the accounting pages in the Angular SPA with a minimal read-only view: account list with balances and per-account entry history.

## Capabilities

### New Capabilities
- `accounting-ledger`: account, category, project and ledger-entry model; balance computation; read-only REST endpoints.
- `accounting-moze-import`: MOZE 16-column CSV parsing, record-kind mapping, transfer pairing, full-replace import with report.
- `frontend-accounting-ledger`: read-only accounts list and account entry history pages.

### Removed Capabilities
- `frontend-accounting-transactions` and `frontend-accounting-analytics`: superseded by the ledger pages here and by the entry and report pages in later changes.

## Impact

- **Code**: `services/accounting-service/app/{models,routers,services,schemas}/` rewritten; new `app/services/moze_import_service.py`; new Alembic migration; `frontend/src/app/components/accounting/` replaced; `frontend/src/app/services/accounting.service.ts` and `models/accounting.model.ts` replaced; routes in `app.routes.ts` updated.
- **APIs**: all existing `/api/accounting/*` endpoints are removed and replaced by `/api/accounting/accounts`, `/api/accounting/accounts/{id}/entries`, `/api/accounting/imports/moze` and `/api/accounting/imports/latest`.
- **Caddy**: the `@hub_spa` route list must be updated to the new accounting routes.
- **Hermes**: the `homehub-api` skill references removed endpoints and stale ports (`:4200`, `:8080`); it is updated in the Hermes change (phase 4). Until then Hermes must not write to accounting.
- **Out of scope here**: entry UI, reward rules, credit-card statements, recurring records and installments, net worth, reports, receivables view, the agent API, the MOZE changelog digest, MOZE backup-file settings import, linking MOZE accounts to broker accounts.
