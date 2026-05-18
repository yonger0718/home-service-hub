## Why

The `刷新行情` button on `/hub/portfolio` only refreshes the summary card (live TWSE quotes via `GET /api/portfolio/summary`, 30s cache). The chart (`portfolio_snapshot` series) does NOT refresh. During trading hours users hit the button expecting an end-to-end refresh — both the top-card MV and the 總市值 trend line. Today the trend line only updates after the next scheduled snapshot run or a manual `POST /api/portfolio/imports/recalc`. Result: post-button chart still shows yesterday's close while the card shows live intraday MV. Misleading.

## What Changes

- Backend: new `POST /api/portfolio/imports/refresh-quotes` (mounted on the existing imports router so the status surface stays under `/imports/`) — fast-path recalc for today only. Skips `symbol_map_backfill` and `dividend_auto_record`; runs ONLY Phase 1 (price fetch for today) + Phase 2 (snapshot replay for today). Touched-symbols set = current open holdings (qty>0 as of today). Returns immediately after scheduling (BackgroundTasks), same status-polling surface as `/imports/recalc` via `GET /api/portfolio/imports/recalc/status`.
- Frontend: existing `loadSummary()` on `dashboard.ts` first POSTs to `/api/portfolio/imports/refresh-quotes`, polls `/imports/recalc/status` until `state != "running"` (with timeout), then reloads summary + reloads chart history. Button stays in `loading` state across the full chain.
- Concurrency: re-uses the existing `_RECALC_LOCK` in `post_import_orchestrator`; if a full recalc is already running, refresh-quotes returns 409 and the button surfaces a toast.
- Idempotency: safe to spam — backend dedupes via lock; frontend prevents double-submit via `loading` signal.

## Capabilities

### New Capabilities
- _(none)_

### Modified Capabilities
- `stock-portfolio-import-orchestration`: adds a fast-path entrypoint that runs Phase 1+2 only, scoped to a single day with the current open-holdings symbol set; status surface unchanged.

## Impact

- **Affected code:**
  - `services/stock-portfolio-service/app/routers/imports.py` — add `POST /refresh-quotes` handler.
  - `services/stock-portfolio-service/app/services/post_import_orchestrator.py` — add `schedule_quotes_refresh_sync(...)` that runs Phase 1+2 only (or extend `run_chain` with a skip-set; design.md decides).
  - `frontend/src/app/services/portfolio.service.ts` — add `refreshQuotes()` + `getRecalcStatus()`.
  - `frontend/src/app/components/portfolio/dashboard/dashboard.ts` — rewire `loadSummary()` to call refresh-quotes → poll → reload summary + chart.
  - `frontend/src/app/components/portfolio/networth-chart/networth-chart.ts` — expose a public `reload()` so dashboard can re-trigger history fetch.
- **No schema change.** No new migrations.
- **No API breaking change.** New endpoint is additive; existing `/summary`, `/history`, `/imports/recalc` untouched.
- **Tests:** unit for the new orchestrator entrypoint (Phase 1+2 only, correct active_dates), router test (open-holdings derivation, 409 on busy lock), frontend unit on `loadSummary()` happy path + poll timeout.
- **Risk:** Phase 1 today-only fetch can still hit partial-fetch issue (TWSE/TPEx whole-market endpoint returning under-threshold rows) — out of scope for this change, tracked separately as `detect-partial-phase1-fetch`.
