## Why

Portfolio service today only models holdings; cash sitting at each broker is invisible. With four real brokerage relationships (Cathay 國泰, SinoPac 永豐, Firstrade, IB, CS) across TWD/USD/GBP, the user cannot answer "how much idle cash do I have, and where." Existing Cathay CSV imports already contain every cash leg (settlement, fees, tax, dividends) — that data is currently discarded. Without a cash model, the dashboard's "total assets" figure ignores the largest non-equity component of the portfolio.

## What Changes

- Add `broker_account` model and CRUD: one row per broker × currency, opening-balance seed.
- Add `cash_transaction` model: signed amounts, typed (deposit / withdraw / buy_settle / sell_settle / fee / tax / dividend_cash / interest_in / margin_interest / wire_fee / fx_convert), linked back to source transaction or dividend when auto-derived.
- Add `fx_rate` model + daily refresh job pulling TWD/USD, TWD/GBP, TWD/JPY from `fawazahmed0/exchange-api` (CDN-fronted static JSON via jsdelivr, with `currency-api.pages.dev` fallback). Historical date-slot URLs enable gap-backfill when the scheduler misses a day.
- Extend `broker_cathay_service` import path to emit a `cash_transaction` row for every buy/sell/fee/tax/dividend it creates (source = `csv_import`).
- Extend `portfolio_service.create_transaction` / `update_transaction` / `delete_transaction` to sync a linked `cash_transaction` row (source = `auto_derive`) when manual transactions are edited.
- Add `cash_backfill_service` CLI: replay every existing `transactions` + `dividends` row into `cash_transaction`, idempotent via `import_fingerprint`.
- Add new REST endpoints under `/api/portfolio/accounts/*`: list accounts, create account, list cash transactions for an account, post manual deposit/withdraw, balance-over-time series.
- Add new Angular page `/portfolio/accounts` and `/portfolio/accounts/:id`: account cards with native + TWD-converted balance, cash transaction list with filters, manual deposit/withdraw form, balance-over-time chart.
- Add nav entry "現金帳戶" alongside 交易紀錄 / 股息 / 已實現損益.

## Capabilities

### New Capabilities
- `stock-portfolio-cash-accounts`: broker account model, cash transaction ledger, FX-rate-aware multi-currency balance compute, backfill from existing transactions/dividends, REST endpoints.
- `stock-portfolio-fx-rates`: daily FX rate fetch from `open.er-api.com`, persistence, look-up service for cross-currency aggregation.
- `frontend-portfolio-accounts`: Angular page (`/portfolio/accounts` + detail), accounts list cards, cash transaction list, manual deposit/withdraw form, balance-over-time chart, nav entry.

### Modified Capabilities
- `stock-portfolio-broker-cathay-import`: Cathay importer must emit a linked `cash_transaction` row for every transaction/dividend it creates (source = `csv_import`); idempotency must extend across both tables.

## Impact

- **Code**: `services/stock-portfolio-service/app/{models,routers,services}/`, `frontend/src/app/{components/portfolio/accounts,services,models}/`, new Alembic migrations (3 tables).
- **APIs**: new `/api/portfolio/accounts/*` endpoints; existing `POST /api/portfolio/transactions` side-effects now write a linked cash row.
- **Dependencies**: outbound HTTPS to `cdn.jsdelivr.net` (primary) and `currency-api.pages.dev` (fallback) — no API key, no rate limit (CDN-fronted static JSON). Reuses existing APScheduler if present; otherwise introduces minimal scheduler scaffold scoped to the FX job.
- **Data**: existing Cathay-imported `transactions` + `dividends` rows must be backfilled via CLI before the accounts page is meaningful — documented as a one-shot operator step.
- **Out of scope (TODO)**: US/LSE/JP holdings tracking + non-TWSE quote feeds; SinoPac/Firstrade/IB/CS broker-statement CSV importers (manual cash entry only); reconciliation diff against broker-reported balances; merging account balance into existing `networth_snapshot`.
