## 1. Backend — orchestrator module

- [x] 1.1 Create `app/services/post_import_orchestrator.py` with `ChainResult` dataclass (per-step status, timestamps, error messages) and `run_chain(db, recalc_from, recalc_to, touched_symbols)` coroutine
- [x] 1.2 Implement step 1: call `symbol_map_service.backfill_transactions(db, dry_run=False)`; record result
- [x] 1.3 Implement step 2: call `dividend_event_service.fetch_for_holdings({touched symbols})` for each affected TW year, convert each `DividendEventRow` whose `ex_dividend_date in [recalc_from, recalc_to]` to `HistoricalDividendEvent`, feed through `dividend_auto_record_service.auto_record_for_event`; record per-event failures
- [x] 1.4 Implement step 3: call `networth_backfill_service.run_backfill(db, recalc_from, recalc_to, phase="both")`; record result
- [x] 1.5 Wrap each step in its own try/except so one failure does not skip later steps; log `event=post_import.step_failed step=<name>` with exception
- [x] 1.6 Add module-level `threading.Lock` `_RECALC_LOCK` and acquire it in `schedule_chain_sync` around the whole chain (thread lock, not asyncio.Lock — `BackgroundTasks` spawns a fresh event loop per call, which would orphan an asyncio primitive)
- [x] 1.7 Store the latest `ChainResult` in a module-level dict keyed by start-time so the status endpoint can read it; prune entries older than 10 minutes on each write

## 2. Backend — router wiring

- [x] 2.1 Modify `app/routers/imports.py` `import_transactions` handler to accept `BackgroundTasks` and schedule `post_import_orchestrator.run_chain` when `dry_run=False`, `result.created > 0`, and `POST_IMPORT_RECALC_ENABLED != "false"`; touched symbols + recalc_from derived from the newly-created rows
- [x] 2.2 Same wiring for `import_dividends`
- [x] 2.3 Add `recalc_scheduled: bool` to both endpoints' response body
- [x] 2.4 Add `POST /api/portfolio/imports/recalc` endpoint accepting `{start_date?, end_date?}`; default start to `min(transactions.trade_date)`, end to today_tw; return 409 if no transactions exist
- [x] 2.5 Add `GET /api/portfolio/imports/recalc/status` endpoint reading from the orchestrator's in-memory result dict; return `{state: "idle"}` when no recent run

## 3. Backend — config + feature flag

- [x] 3.1 Read `POST_IMPORT_RECALC_ENABLED` env var (default `true`) inside `post_import_orchestrator.is_enabled()` — no separate `config.py` exists; pattern matches existing modules (`scheduler.is_enabled`, `logging_config`, `twse_client`)
- [x] 3.2 Document the flag in `.env.example` (no per-service README exists — `.env.example` is the canonical reference for all service env vars)

## 4. Backend — tests

- [x] 4.1 Unit test: chain skipped when `inserted_count == 0`
- [x] 4.2 Unit test: chain calls steps in order — symbol-map → dividends → networth
- [x] 4.3 Unit test: step 2 failure does not skip step 3 (mock dividend service to raise)
- [x] 4.4 Unit test: feature-flag off → orchestrator not scheduled
- [x] 4.5 Unit test: manual `/recalc` defaults to `min(trade_date)` and today
- [x] 4.6 Unit test: manual `/recalc` returns 409 when transactions table empty
- [x] 4.7 Unit test: `_RECALC_LOCK` serializes two concurrent chain invocations
- [x] 4.8 Unit test: status endpoint returns `running`, then `completed`/`partial`/`failed`, then `idle` after 10 min

## 5. Frontend — navigation

- [x] 5.1 Add `<a routerLink="/portfolio/import">匯入 CSV</a>` to the portfolio nav block in `frontend/src/app/app.html` (desktop dock + mobile segmented sub-nav)
- [x] 5.2 Verify route already registered in `app.routes.ts` (line 11 — already present)

## 6. Frontend — import page recalc UI

- [x] 6.1 Add `recalcStatus` signal to import page component (replaces separate `recalcRunning`/`lastRecalcState` — single source of truth)
- [x] 6.2 After successful commit response, if `recalc_scheduled === true`, show non-blocking PrimeNG toast "資料重算執行中…" and start polling
- [x] 6.3 Poll `GET /api/portfolio/imports/recalc/status` every 5 s; stop polling when `state !== "running"`
- [x] 6.4 On `completed`: show green success toast "資料重算完成"
- [x] 6.5 On `partial`: show orange warning toast listing failed step names and a "重試" button that calls `POST /api/portfolio/imports/recalc`
- [x] 6.6 On `failed`: show red error toast with the error message + "重試" button
- [x] 6.7 On import page mount, fetch `/recalc/status` once so a refresh-during-recalc still shows the toast

## 7. Frontend — service + model updates

- [x] 7.1 Add `getRecalcStatus()` + `triggerRecalc(range?)` to `PortfolioService`
- [x] 7.2 Add `RecalcStatus`, `RecalcStepResult`, `RecalcTriggerResponse` interfaces to `models/portfolio.model.ts`

## 8. Verification

- [x] 8.1 `pytest tests/unit/ -k post_import` green — 19/19 (full suite 330/330)
- [x] 8.2 `npm test` green — 10/10
- [ ] 8.3 Manual: upload a 50-row CSV → toast appears → poll completes → chart updates within ~30 s for a 1-week range
- [ ] 8.4 Manual: upload a 500-row CSV spanning 2 years → toast appears → chart populates back to earliest date within 5-10 min
- [ ] 8.5 Manual: re-upload same CSV → `inserted_count = 0`, no toast, no TWSE traffic
- [ ] 8.6 Manual: set `POST_IMPORT_RECALC_ENABLED=false`, restart, upload CSV → no chain runs; manual `/recalc` still works
- [x] 8.7 `openspec validate add-post-import-recalc-chain --strict` passes
