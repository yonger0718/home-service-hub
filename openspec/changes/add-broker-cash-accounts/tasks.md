## 1. Backend — schema + migrations

- [x] 1.1 Add `BrokerAccount` SQLAlchemy model under `services/stock-portfolio-service/app/models/broker_account.py` with columns from spec (id, broker enum, nickname, currency, opening_balance, opening_date, is_active, created_at) and the `(broker, nickname)` UNIQUE constraint
- [x] 1.2 Add `CashTransaction` model under `app/models/cash_transaction.py` with all columns from spec, indexes on `account_id`, `txn_date`, `related_transaction_id`, and UNIQUE on `import_fingerprint`
- [x] 1.3 Add `FxRate` model under `app/models/fx_rate.py` with primary key `(date, base_currency, quote_currency)`
- [x] 1.4 Add type enums to `app/models/enums.py` (or equivalent): `BrokerEnum`, `CashTxnType`, `CashTxnSource`
- [x] 1.5 Register the three new models in the models package `__init__.py` so Alembic autogenerate picks them up
- [x] 1.6 Generate Alembic migration `xxxx_create_broker_account.py` and verify upgrade + downgrade work against a fresh test DB
- [x] 1.7 Generate Alembic migration `xxxx_create_cash_transaction.py` (depends on broker_account); verify upgrade + downgrade
- [x] 1.8 Generate Alembic migration `xxxx_create_fx_rate.py`; verify upgrade + downgrade

## 2. Backend — FX rate service

- [x] 2.1 Create `app/services/fx_rate_service.py` with `fetch_and_store(base_currencies, quote_currencies, asof=None)` calling primary `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{slot}/v1/currencies/{base_lc}.json` with fallback to `https://{slot}.currency-api.pages.dev/v1/currencies/{base_lc}.json` (slot = `latest` or `YYYY-MM-DD`); parse `{date, {base_lc}: {quote_lc: rate, ...}}`; upsert rows with `source=fawazahmed0-jsdelivr` or `fawazahmed0-pages`; return `FetchResult` dataclass with `success`, `per_base`, `upserted_count`, `error`
- [x] 2.2 Add `get_rate(date, base, quote)` with exact-match → as-of-fallback → USD-pivot triangulation → None
- [x] 2.3 Wire `fx_rate_service` into the existing scheduler (`app/services/scheduler.py` or equivalent per `stock-portfolio-scheduling` capability) as cron job `fx_rate_daily` at `02:00 Asia/Taipei`; ensure job-level exception catch logs but does not crash scheduler
- [x] 2.4 Add `POST /api/portfolio/fx/refresh` in a new `app/routers/fx_rates.py`, returning the FetchResult shape
- [x] 2.5 Unit tests `tests/unit/test_fx_rate_service.py`: parser, upsert overwrite, network-fail path, exact lookup, as-of fallback, triangulation, missing returns None

## 3. Backend — cash account service + endpoints

- [x] 3.1 Create `app/services/cash_account_service.py` exposing `get_balance(account_id, asof=None)`, `get_balance_history(account_id, date_from, date_to)`, `get_total_balance_in(target_currency, asof=None, include_inactive=False)`
- [x] 3.2 Add fingerprint helper `compute_manual_fingerprint(account_id, txn_date, type, amount, note)` and `compute_backfill_fingerprint(source_table, source_id, leg)` and `compute_csv_fingerprint(broker, transaction_fp, leg)` so all three sources hash deterministically
- [x] 3.3 Add `create_manual_cash_transaction(account_id, payload)` enforcing: currency must match account (unless fx_convert), amount sign normalized by type, `source=manual` server-controlled
- [x] 3.4 Create `app/routers/accounts.py` with all endpoints from spec: GET list (with `in_currency` and `include_inactive` query params), POST create, PATCH update (disallow broker/currency change), GET cash-transactions (paginated, filterable, sortable), POST manual cash-transaction, GET balance-history
- [x] 3.5 Register `accounts` router in `app/main.py` under prefix `/api/portfolio/accounts` and `fx_rates` router under `/api/portfolio/fx`
- [x] 3.6 Unit tests `tests/unit/test_cash_account_service.py`: balance compute (single + multi-row), as-of cutoff, multi-currency aggregate with FX, skipped_currencies, balance-history step-fill
- [x] 3.7 Integration tests `tests/integration/test_accounts_endpoints.py`: create / list / patch account, post manual txn (incl. currency-mismatch rejection), pagination, duplicate-fingerprint 409, balance-history shape

## 4. Backend — transaction CRUD sync

- [x] 4.1 Extend `portfolio_service.create_transaction` to also create linked `cash_transaction` rows (settle + fee + tax) inside the same DB session, tagged `source=auto_derive`
- [x] 4.2 Extend `portfolio_service.update_transaction` to upsert linked cash rows matching the new fee / tax / quantity / price; create missing rows for legacy transactions
- [x] 4.3 Extend `portfolio_service.delete_transaction` to explicitly delete linked cash rows in the same DB session (FK is SET NULL, not CASCADE)
- [x] 4.4 Resolve the default account: use the active `(broker=cathay, currency=TWD)` row for manual TWD transactions; raise a clear error if zero or multiple match
- [x] 4.5 Unit tests `tests/unit/test_transaction_cash_sync.py`: BUY/SELL emits expected legs, fee update propagates, delete removes legs, legacy transaction gains legs on first update

## 5. Backend — backfill CLI

- [x] 5.1 Create `app/services/cash_backfill_service.py` with `replay_all(dry_run=False)` iterating `transactions` and `dividends` in trade_date order and emitting cash rows per the leg table in design
- [x] 5.2 Tag rows `source=csv_import` if the originating transaction's `import_fingerprint` starts with the Cathay marker, else `source=auto_derive`
- [x] 5.3 Add `__main__` entry so `python -m app.services.cash_backfill_service --all [--dry-run]` works
- [x] 5.4 Exit non-zero with a clear message if zero `(broker=cathay)` accounts exist
- [x] 5.5 Unit tests `tests/unit/test_cash_backfill_service.py`: first-run counts, idempotency on re-run, dry-run writes nothing, missing-account error
- [x] 5.6 Invariant test `tests/unit/test_cash_balance_invariant.py`: `cash_account_service.get_balance(account)` == manually summed cash_transaction.amount + opening_balance for fixture portfolio

## 6. Backend — Cathay importer cash leg

- [x] 6.1 Extend `broker_cathay_service` insert path to emit linked settle / fee / tax cash rows inside the same DB transaction
- [x] 6.2 Extend legacy-fingerprint rehash branch to also rewrite (or insert if missing) linked cash rows' `import_fingerprint` and `amount`
- [x] 6.3 Extend business-key rehash branch with the same logic as 6.2
- [x] 6.4 Extend dividend-row commit path to emit `dividend_cash` rows
- [x] 6.5 Resolve target account: active `(broker=cathay, currency=TWD)` row; fail batch with HTTP 412 and a clear message if not exactly one
- [x] 6.6 Gate the new emission behind a feature flag (env var, e.g. `CASH_LEG_ENABLED`) so the code can ship dark and be enabled after backfill completes
- [x] 6.7 Integration tests `tests/integration/test_cathay_import_cash_leg.py`: insert path emits legs, rehash updates legs, missing legs created on rehash, duplicate import is no-op for cash rows, missing-account aborts

## 7. Frontend — models + service

- [x] 7.1 Add types to `frontend/src/app/models/portfolio.model.ts`: `BrokerEnum`, `CashTransactionType` (matching backend enum exactly), `CashTransactionSource`, `BrokerAccount`, `CashTransaction`, `BalancePoint`, `AccountSummary`, `FxFetchResult`, `CreateBrokerAccount`, `CreateCashTransaction`
- [x] 7.2 Extend `frontend/src/app/services/portfolio.service.ts` with typed methods: `getAccounts(opts?)`, `createAccount(body)`, `patchAccount(id, patch)`, `getCashTransactions(id, query)`, `createCashTransaction(id, body)`, `getBalanceHistory(id, range)`, `refreshFxRates(opts?)`

## 8. Frontend — accounts list page

- [x] 8.1 Create standalone component `components/portfolio/accounts/accounts-list.component.{ts,html,scss}` rendering the TWD-total summary card, optional `skipped_currencies` footnote, and a grid of account cards (broker, nickname, currency, native_balance, target_balance, is_active badge)
- [x] 8.2 Add "新增帳戶" button + create-account modal posting to `POST /api/portfolio/accounts` and refreshing the list on success
- [x] 8.3 Make each card a router link to `/portfolio/accounts/:id`
- [x] 8.4 Add `/portfolio/accounts` route to `app.routes.ts` pointing at the new component
- [x] 8.5 Component tests `accounts-list.component.spec.ts`: renders one card per active account, TWD total reflects sum, skipped-currency footnote shows, create modal POSTs and refetches

## 9. Frontend — account detail page

- [x] 9.1 Create standalone component `components/portfolio/accounts/account-detail.component.{ts,html,scss}` with three sections: header (metadata + edit modal), balance-over-time chart (ECharts), cash transactions list
- [x] 9.2 Implement balance-over-time chart with window selector (1M / 3M / 1Y / All, default 3M), refetching on window change
- [x] 9.3 Implement cash transactions list using existing `hub-modern-list` card layout, filters (date range, type multi-select), sort dropdown, paginator (25 / 50 / 100, page-size persisted to localStorage)
- [x] 9.4 Add "新增交易" button + manual-entry modal posting to `POST /api/portfolio/accounts/{id}/cash-transactions` and refreshing list + chart on success
- [x] 9.5 Add `/portfolio/accounts/:id` route to `app.routes.ts` pointing at the new component
- [x] 9.6 Component tests `account-detail.component.spec.ts`: chart fetches default 3M window, window switch refetches, list paginates, filter narrows results, manual-entry posts and refreshes

## 10. Frontend — nav entry

- [x] 10.1 Add `現金帳戶` link to the portfolio nav alongside `交易紀錄` / `股息` / `已實現損益`, linking to `/portfolio/accounts`
- [x] 10.2 Ensure the link's active state matches both `/portfolio/accounts` and `/portfolio/accounts/:id`

## 11. Operational rollout

- [x] 11.1 Document the rollout in `services/stock-portfolio-service/README.md`: phase 0 deploy (flag off) → phase 1 create Cathay account + dry-run backfill → phase 2 commit backfill → phase 3 flip flag → phase 4 create non-Cathay accounts
- [x] 11.2 Operator runs `python -m app.services.cash_backfill_service --all --dry-run` against the live DB, captures counts in the rollout doc (2196 txns + 70 dividends → 5173 projected rows)
- [x] 11.3 Operator runs the same command without `--dry-run` to commit backfill (5173 inserted, idempotent re-run = 0)
- [x] 11.4 Operator flips `CASH_LEG_ENABLED` and restarts the stock-portfolio-service
- [x] 11.5 Operator triggers `POST /api/portfolio/fx/refresh` to seed the first day's FX rates (6 rows: USD/TWD/GBP/JPY ↔)
- [x] 11.6 Operator verifies the new page renders, balance matches expectation, balance-over-time chart shows historical curve

## 12. Verification

- [x] 12.1 `cd services/stock-portfolio-service && pytest tests/unit/` clean
- [x] 12.2 `pytest tests/integration/` clean
- [x] 12.3 `cd frontend && npm test` clean
- [x] 12.4 `npm run build` succeeds without new errors
- [x] 12.5 Manual smoke (after rollout phase 3): create a manual deposit on a Firstrade account, confirm it appears in the list and updates the chart; edit a Cathay transaction's fee, confirm the linked cash row updates
