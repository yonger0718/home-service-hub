## 1. Schema + migration

- [x] 1.1 Add `Broker` Python enum (`TW_CATHAY, TW_SINOPAC, TW_MANUAL, IB, FIRSTRADE, SCHWAB, FOREIGN_MANUAL`) at `app/models/portfolio.py`
- [x] 1.2 Add nullable `broker VARCHAR(32)` column to `Transaction` model with CHECK constraint pinned to the enum values
- [x] 1.3 Add new `BrokerCashFlow` SQLAlchemy model at `app/models/portfolio.py` with columns `id, broker, date, type, amount, currency, fx_rate_to_twd, note, import_fingerprint UNIQUE, created_at`
- [x] 1.4 Add new alembic revision: create `broker_cash_flows` table, add `transactions.broker` column, run `UPDATE transactions SET broker='TW_MANUAL' WHERE broker IS NULL`. Downgrade drops both.
- [x] 1.5 Verify alembic round-trip on a fresh local DB: `upgrade head` → `downgrade -1` → `upgrade head` clean

## 2. Schemas (Pydantic)

- [x] 2.1 Add `Broker` enum + `BrokerCashFlowIn` / `BrokerCashFlowOut` / `BrokerCashBalance` to `app/schemas/portfolio.py`
- [x] 2.2 Add optional `broker: Optional[Broker]` field to existing `Transaction` schema (and request/response variants)
- [x] 2.3 Add optional `broker: Optional[Broker]` field to `RealizedPnLEvent` schema

## 3. Cash flow service + API

- [x] 3.1 Create `app/services/cash_flow_service.py` with `write_cash_flows(rows)`, `get_broker_balance(broker, as_of_date)`, `list_balances()` helpers; all idempotent via `import_fingerprint` ON CONFLICT DO NOTHING
- [x] 3.2 Create `app/routers/cash_flows.py` exposing `GET /api/portfolio/broker-cash-flows` (returns `[BrokerCashBalance]`)
- [x] 3.3 Wire the new router into `app/main.py`
- [x] 3.4 Unit test `tests/unit/test_cash_flow_service.py` covering: single deposit balance, mixed deposit/withdrawal sum at as_of_date, duplicate fingerprint skip

## 4. Broker dispatcher

- [x] 4.1 Create `app/services/broker_dispatch_service.py` with `sniff(raw_bytes) -> Optional[Broker]` matching the three header signatures from `design.md` D1
- [x] 4.2 Hook the dispatcher at the top of `app/services/import_service.py.import_csv()`: if sniff returns a broker, delegate to that broker's parser; otherwise run the existing manual path
- [x] 4.3 Unit test `tests/unit/test_broker_dispatch_service.py` covering: IB header, FT header, CS header, unknown header (falls back), empty body

## 5. Firstrade parser

- [x] 5.1 Create `app/services/broker_firstrade_service.py` with `parse(raw_bytes) -> (transactions, cash_flows, errors)`
- [x] 5.2 Parse `日期` (YYYY/M/D), `交易類別` (買進/賣出/存款/利息收入), `數量`, `代號`, `價格`, `金額`; drop `賬戶類別`
- [x] 5.3 BUY/SELL rows: emit transactions with `broker=FIRSTRADE`, `market='US'`, `currency='USD'`, `fee=0`; FX rate from `fx_rates` at `trade_date`; reject row with index if FX missing
- [x] 5.4 `存款` rows: emit cash flow `type='deposit'`
- [x] 5.5 `利息收入` rows: emit cash flow `type='interest'`
- [x] 5.6 Unit test `tests/unit/test_broker_firstrade_service.py` driven by `tests/unit/fixtures/firstrade_sample.csv` (copy of `/home/opc/workspace/home-hub/ft.csv`); cover BUY, SELL, deposit, interest, FX-miss reject, idempotency

## 6. IB parser

- [x] 6.1 Create `app/services/broker_ib_service.py` with `parse(raw_bytes) -> (transactions, cash_flows, errors)`
- [x] 6.2 Read `總結` section to extract `基礎貨幣` (account base currency)
- [x] 6.3 Parse `轉賬歷史` section: `日期` (YYYY-MM-DD), `交易類型` (買/賣/存款), `代碼`, `交易量`, `價格`, `Price Currency`, `佣金` (signed → store positive), `淨金額`
- [x] 6.4 BUY/SELL rows: emit transactions with `broker=IB`, currency from `Price Currency`, `market='LSE'` if ticker resolves with `.L` suffix else `market='US'`, `fee=abs(佣金)`; FX rate from `fx_rates`; reject row with index if FX missing
- [x] 6.5 `存款` rows: emit cash flow `type='deposit'` with currency from 總結 base currency
- [x] 6.6 Unit test `tests/unit/test_broker_ib_service.py` driven by `tests/unit/fixtures/ib_sample.csv`; cover USD BUY, GBP→LSE inference, deposit, base-currency anchor, FX-miss reject

## 7. Schwab parser

- [x] 7.1 Create `app/services/broker_schwab_service.py` with `parse(raw_bytes) -> (transactions, cash_flows, errors)`
- [x] 7.2 Parse columns `Date` (MM/DD/YYYY), `Action`, `Symbol`, `Quantity`, `Price`, `Fees & Comm`, `Amount` (strip `$` prefix)
- [x] 7.3 `Buy` / `Sell` rows: emit transactions with `broker=SCHWAB`, `market='US'`, `currency='USD'`; FX rate from `fx_rates`; reject row if FX missing
- [x] 7.4 `Wire Received` rows: emit cash flow `type='deposit'`; `Wire Sent` → `type='withdrawal'`
- [x] 7.5 Unit test `tests/unit/test_broker_schwab_service.py` driven by `tests/unit/fixtures/schwab_sample.csv` (copy of `/home/opc/workspace/home-hub/cs.csv` + a synthetic Buy row for coverage); cover Wire Received deposit, BUY, FX-miss reject, idempotency

## 8. Foreign dividend cron

- [x] 8.1 Create `app/services/foreign_dividend_service.py` with `refresh_today()` iterating open foreign positions and calling `yfinance.Ticker(symbol).dividends`
- [x] 8.2 Upsert into `dividends` keyed on `(symbol, market, ex_dividend_date)`; resolve `currency` from `yfinance.Ticker.fast_info.currency`; resolve `fx_rate_to_twd` from `fx_rates` at ex-date; skip row with structured log on FX miss
- [x] 8.3 Per-ticker isolation: one bad ticker logs `quotes.foreign_dividends.skip` and does not abort the batch
- [x] 8.4 Register `foreign_dividend_refresh` APScheduler job in `app/services/scheduler.py` at `hour=17, minute=45, timezone=Asia/Taipei`, gated by `SCHEDULER_ENABLED`
- [x] 8.5 Unit test `tests/unit/test_foreign_dividend_service.py` with mocked yfinance: two dividend rows upserted; re-run creates zero new rows; FX-miss skip; per-ticker failure isolated
- [x] 8.6 Unit test `tests/unit/test_scheduler.py` extended with assertion that `foreign_dividend_refresh` is registered with the right trigger

## 9. Realized P&L broker passthrough

- [x] 9.1 Extend `iter_realized_events` in `app/services/realized_pnl_service.py` to read `transactions.broker` and emit it on each event
- [x] 9.2 Update `app/routers/portfolio.py` realized-PnL response schema to surface `broker`
- [x] 9.3 Unit test `tests/unit/test_realized_pnl_service.py` covers a foreign SELL event carrying `broker='IB'` and a TW SELL carrying `broker='TW_CATHAY'`

## 10. End-to-end integration

- [x] 10.1 Integration test `tests/integration/test_broker_csv_import.py` uploads each broker's real sample CSV to `POST /api/portfolio/imports/csv` and asserts: equity rows in `transactions` with correct `broker`, cash rows in `broker_cash_flows`, FX rate populated when `fx_rates` is seeded, idempotency on re-upload
- [x] 10.2 Integration test for `GET /api/portfolio/broker-cash-flows` returning one row per active broker after seeded imports
- [x] 10.3 Run full backend pytest suite — confirm baseline + new tests all pass; record counts

## 11. Verification + ship

- [x] 11.1 `./.venv/bin/pytest tests/unit/ -x --tb=short` — all pass
- [x] 11.2 `./.venv/bin/pytest tests/integration/ -x --tb=short` — all pass
- [x] 11.3 `./.venv/bin/python -m app.services.networth_backfill_service --rebuild-all --dry-run` — 0 non-zero deltas (broker column should not perturb the existing TW networth path)
- [x] 11.4 `openspec validate add-foreign-broker-csv-import` clean
- [x] 11.5 Mark every task above complete (`- [x]`) before opening the PR

## 12. Frontend wiring (Phase 5a fold-in)

- [x] 12.1 Extend `frontend/src/app/models/portfolio.model.ts` with `Broker` enum (string-literal union mirroring backend), `BrokerCashFlow` + `BrokerCashBalance` interfaces, optional `broker?: Broker` on `Transaction` and `RealizedPnlEvent`, `BrokerCsvImportResult` interface
- [x] 12.2 Extend `frontend/src/app/services/portfolio.service.ts` with `uploadBrokerCsv(file: File, dryRun: boolean)` (POST multipart to `/api/portfolio/imports/csv`) and `getBrokerCashFlows()` (GET `/api/portfolio/broker-cash-flows`)
- [x] 12.3 Create `frontend/src/app/components/portfolio/broker-import/broker-import.{ts,html,scss}` (standalone) with: file picker, sniffed-broker chip, dry-run preview table of parsed transactions + cash flows, commit button, success/error toast
- [x] 12.4 Register route `/portfolio/import-broker` in `frontend/src/app/app.routes.ts` and add a nav entry from the existing portfolio page
- [x] 12.5 Update `frontend/src/app/components/portfolio/transaction-list/transaction-list.{ts,html}` to render a broker badge alongside the existing market badge when `transaction.broker && transaction.broker !== 'TW_MANUAL'`
- [x] 12.6 Update `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.{ts,html}` to render a broker badge per event (visibility flag: hide column if every event is `TW_MANUAL`/null) and add a broker filter chip row above the table (chips derived from the dataset, default `ALL`)
- [x] 12.7 Update `frontend/src/app/components/portfolio/dashboard/dashboard.{ts,html}` to surface a per-broker cash tile pulled from `getBrokerCashFlows()`, replacing the hand-typed values; existing aggregate cash tile retained for the ALL view
- [x] 12.8 Unit test `frontend/src/app/components/portfolio/broker-import/broker-import.spec.ts` covering: dry-run shows preview, commit toggles success toast, error response surfaces the row-indexed error list
- [x] 12.9 Unit test `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.broker-filter.spec.ts` covering: mixed broker dataset renders the filter row, selecting a broker filters rows, TW-only dataset hides the column + filter
- [x] 12.10 Update existing dashboard / transaction-list / realized-pnl fixtures with the new `broker` field; verify existing tests still pass
- [x] 12.11 `npm test` in `frontend/` — all new and existing tests pass
- [x] 12.12 `npm run build` in `frontend/` — production build succeeds, no new TypeScript errors
