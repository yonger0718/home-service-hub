## 1. Backend: orchestrator fast-path entrypoint

- [x] 1.1 Add `schedule_quotes_refresh_sync(session_factory, *, touched_symbols)` to `services/stock-portfolio-service/app/services/post_import_orchestrator.py`. Resolves `today = today_tw()`, acquires `_RECALC_LOCK` (blocking — caller checks `acquire(blocking=False)` first; see 2.2), runs `asyncio.run(run_chain_quotes_only(...))`, releases lock.
- [x] 1.2 Add `async def run_chain_quotes_only(session_factory, *, today, touched_symbols) -> ChainResult` that mirrors `run_chain` but only invokes `_step_networth_backfill(session_factory, today, today)`. Stores the result in `_LATEST_RESULTS` with same `_store(...)` plumbing.
- [x] 1.3 Unit test: `run_chain_quotes_only` produces `ChainResult` with one step (`networth_backfill`), `recalc_from == recalc_to == today`, and stores into `_LATEST_RESULTS`. Use monkeypatched `_step_networth_backfill` returning a canned `StepResult`.
- [x] 1.4 Unit test: `schedule_quotes_refresh_sync` holds `_RECALC_LOCK` for the chain duration (assert lock is held while the step runs, released after).

## 2. Backend: HTTP endpoint

- [x] 2.1 Add `POST /refresh-quotes` handler in `services/stock-portfolio-service/app/routers/imports.py`. Resolves `touched_symbols` via SQL: `SELECT symbol, SUM(CASE WHEN type='BUY' THEN quantity ELSE -quantity END) AS qty FROM transactions WHERE trade_date <= today_utc_eod GROUP BY symbol HAVING qty > 0`. Returns 204 if empty.
- [x] 2.2 Non-blocking lock check: if `post_import_orchestrator._RECALC_LOCK.acquire(blocking=False)` fails, return HTTPException(409, "recalc in progress"). On success, IMMEDIATELY release the lock and schedule the background task (the task will re-acquire when it runs — small race window acceptable for single-user system; document in code comment).
- [x] 2.3 Background task: `background_tasks.add_task(post_import_orchestrator.schedule_quotes_refresh_sync, SessionLocal, touched_symbols=touched)`. Response: HTTP 202 `{"refresh_scheduled": true, "date": today.isoformat(), "touched_symbols": sorted(touched)}`.
- [x] 2.4 Router test (FastAPI TestClient): seed 2 open holdings + 1 closed → endpoint returns 202 with both open symbols, BackgroundTasks queued (assert via mock).
- [x] 2.5 Router test: empty portfolio → 204.
- [x] 2.6 Router test: simulate held lock → 409.

## 3. Backend: integration

- [x] 3.1 Integration test in `tests/integration/test_post_import_recalc_chain.py`: seed 1 open holding, monkey-patch TWSE/TPEx fetchers to return today's price, hit `POST /refresh-quotes`, await background task (or call `schedule_quotes_refresh_sync` directly), assert: `price_history` has today's row, `portfolio_snapshot` has today's row, `latest_status()` returns 1-step `completed`.

## 4. Frontend: service method

- [x] 4.1 In `frontend/src/app/services/portfolio.service.ts`, add `refreshQuotes(): Observable<{refresh_scheduled: boolean, date: string, touched_symbols: string[]}>` — `POST /api/portfolio/imports/refresh-quotes`.
- [x] 4.2 Add `getRecalcStatus(): Observable<RecalcStatus>` — `GET /api/portfolio/imports/recalc/status`. Define `RecalcStatus` interface in `models/portfolio.model.ts` matching the orchestrator's `_serialize` shape (`state`, `started_at`, `finished_at`, `steps`).

## 5. Frontend: chart reload hook

- [x] 5.1 In `frontend/src/app/components/portfolio/networth-chart/networth-chart.ts`, expose a public `reload(): void` method that re-triggers the same `getNetworthHistory(...)` call ngOnInit makes. Keep the existing ngOnInit call unchanged.
- [x] 5.2 In `dashboard.ts`, add `@ViewChild(NetworthChartComponent) chart!: NetworthChartComponent;` so dashboard can call `this.chart.reload()`.

## 6. Frontend: rewire button

- [x] 6.1 Refactor `loadSummary()` in `dashboard.ts`:
  ```
  loadSummary() {
    this.loading.set(true);
    this.portfolioService.refreshQuotes().subscribe({
      next: () => this.pollRecalcStatus(),
      error: (err) => {
        if (err.status === 409) this.showToast('另一筆重算進行中, 稍候再試');
        else if (err.status !== 204) console.error(err);
        this.reloadSummaryAndChart();
      }
    });
  }
  ```
- [x] 6.2 Add `pollRecalcStatus()` private method: poll `getRecalcStatus()` every 1000ms, stop when `state in {"completed","partial","failed"}` OR 30s elapsed. On stop: call `reloadSummaryAndChart()`.
- [x] 6.3 Add `reloadSummaryAndChart()` private method: call existing `getSummary()` subscription path (rename current body) + `this.chart?.reload()`. Sets `loading.set(false)` on summary resolve.
- [x] 6.4 Add minimal toast wiring — use existing `MessageService` if already in app, else just `console.warn`. (Check `app.config.ts` for `MessageService` provider before adding.)

## 7. Frontend: tests

- [x] 7.1 `dashboard.spec.ts` (Vitest): mock `PortfolioService.refreshQuotes` + `getRecalcStatus`. Assert: click → refreshQuotes called → poll until `state="completed"` → `getSummary` + `chart.reload` invoked. Use fake timers for the 1s poll interval.
- [x] 7.2 `dashboard.spec.ts`: 409 path → toast/warn fired, summary still reloaded.
- [x] 7.3 `dashboard.spec.ts`: poll timeout (30s elapsed without completion) → still reloads summary + chart.

## 8. Verification

- [x] 8.1 `cd services/stock-portfolio-service && pytest tests/unit/test_post_import_orchestrator.py tests/integration/test_post_import_recalc_chain.py -x` — green (existing + new).
- [x] 8.2 `cd services/stock-portfolio-service && pytest` — full suite green.
- [x] 8.3 `cd frontend && npm test -- --run` — green.
- [ ] 8.4 Manual smoke: start stack, open `/hub/portfolio` during/after TW market hours, click 刷新行情 → button shows loading spinner → resolves within ~5s → top card MV matches latest TWSE quote → chart's last point updates to today's MV.
- [ ] 8.5 Manual smoke: hammer the button 5x rapidly → backend lock prevents thrash, only 1-2 chains run (others 409), UI shows toast on 409.
- [x] 8.6 `openspec validate refresh-quotes-on-button --strict` passes.
