## Context

`PortfolioDashboardComponent.loadSummary()` is the single entry triggered by the `刷新行情` button. It currently calls `GET /api/portfolio/summary` only — which routes through `portfolio_service.get_portfolio_summary` → `get_stock_quotes(active_symbols)` (live TWSE quotes, 30s `quote_cache_ttl_sec`). The chart component (`NetworthChartComponent`) reads `GET /api/portfolio/history` which queries `portfolio_snapshot`, last written by either (a) the daily snapshot job, or (b) `POST /api/portfolio/imports/recalc`. So during market hours: top card refreshes, chart line stops at yesterday's close. Users perceive a discrepancy.

Existing recalc chain (`post_import_orchestrator.run_chain`) is heavy — runs symbol-map backfill (no-op when nothing new), dividend auto-record (TWSE network roundtrip per year-of-touched-symbols), and networth backfill across full requested range. Wrong shape for a per-button-press, today-only refresh.

## Goals / Non-Goals

**Goals:**
- One button click triggers: latest TWSE prices fetched + cached, today's `portfolio_snapshot` row written/upserted, summary card + chart both reflect the new state.
- Sub-5s perceived latency under normal conditions.
- Re-entrant safe: rapid clicks coalesce, no DB-write races.
- Fail-soft: TWSE outage shows the previous snapshot + a non-blocking toast.

**Non-Goals:**
- Replacing the daily scheduled snapshot job.
- Backfilling historical gaps (use `/imports/recalc` for that).
- Detecting under-threshold Phase 1 partial fetches — tracked as `detect-partial-phase1-fetch`.
- Dividend re-fetch on refresh (auto job covers it daily; per-click would 10x slow the path).
- Symbol-map backfill on refresh (only new transactions need it; refresh path has none).

## Decisions

### D1: New orchestrator entrypoint `schedule_quotes_refresh_sync`, not a flag on `run_chain`

`run_chain` already has 3 hard-coded steps. Adding a `skip_steps: set[str]` parameter would scatter conditionals through the loop. Cleaner: new top-level function that calls **only** `_step_networth_backfill` (Phase 1 + Phase 2) for `[today, today]`. Reuses the lock, the result-store, and the status surface. The status JSON's `steps` list will have exactly one entry (`networth_backfill`), which the frontend already tolerates (it polls `state`, not step count).

**Alternative considered:** add `steps: list[str] = ["all"]` param to `run_chain`. Rejected — `run_chain`'s 3-step shape is a feature (predictable status payload for `/imports/recalc/status` consumers). Better to fork.

### D2: Today-only date range, active-holdings symbol set

`recalc_from = recalc_to = post_import_orchestrator.today_tw()`. Touched symbols derived from the same `compute_active_dates` machinery: query open holdings as of today (`sum(qty) > 0` across all transactions ≤ today, grouped by symbol). If `touched == ∅`, return 204 — nothing to refresh.

**Alternative considered:** pass `touched_symbols=set()` and let downstream filter. Rejected — symbol set is what Phase 1 uses to drive whole-market fetch + per-symbol filtering; empty set is ambiguous.

### D3: Phase 1 always runs; Phase 1 cache (price_history PK) handles dedupe

Calling `run_backfill(phase="both")` for `[today, today]` will:
- Phase 1: hit TWSE+TPEx whole-market endpoint, upsert into `price_history`. Existing 1.5s throttle reduced to 0 for single-day calls (no rate-limit risk on one date). Actually — keep the default throttle; single day = single fetch per source = no throttle invoked.
- Phase 2: snapshot replay for today using fresh `price_history` rows. Idempotent via `merge()` on PK.

**Alternative considered:** call live-quote endpoint (`mis.twse.com.tw/getStockInfo.jsp`) instead of whole-market. Rejected — different data source, would also need a separate persistence path. Reuses zero existing code.

### D4: Frontend polling loop in `loadSummary()`

After `POST /refresh-quotes` 202 response, poll `GET /imports/recalc/status` every 1s with a 30s timeout. On `state == "completed" | "partial"`, reload `summary` + chart. On `state == "failed"` show a toast and reload summary anyway (cached live quote still works). On timeout, log warning + reload summary anyway. Button `loading` stays true until poll resolves.

**Alternative considered:** server-side `await` until chain done, return 200 with fresh summary. Rejected — couples HTTP request lifetime to background-task lifetime, and the existing `BackgroundTasks` + lock pattern is the project convention.

### D5: 409 on lock-held

If `_RECALC_LOCK` is already held (a full recalc is mid-flight), `POST /refresh-quotes` returns 409 with `{"detail": "recalc in progress"}`. Frontend shows toast "另一筆重算進行中, 稍候再試". This is the existing `BackgroundTasks` pattern's natural shape: we don't want to queue a second job behind a 30-min recalc.

**Alternative considered:** `tryacquire` with short timeout, then enqueue. Rejected — adds queue state to the orchestrator; not worth it for a single-user system.

### D6: `NetworthChartComponent.reload()` public method

Today the chart fetches once in `ngOnInit`. Add a `public reload()` method that re-runs the same fetch. Dashboard calls it after refresh-quotes finishes. No new component, no new template.

**Alternative considered:** use a shared signal/service that the chart subscribes to. Rejected — over-engineering for a single call site.

## Risks / Trade-offs

- **[Risk] Phase 1 partial fetch returns under-threshold rows** (the 2026-05-18 bug) → Mitigation: out of scope; tracked separately. Refresh-quotes will exhibit the same bug pattern until that fix lands. Acceptable: today's chart point may be wrong, but tomorrow's whole-market job will rewrite it; users can manually clear and rerun.
- **[Risk] Lock held by long-running daily recalc blocks button** → Mitigation: 409 response + toast. User can retry in 30 min when daily run finishes.
- **[Risk] Network roundtrip on every click (TWSE+TPEx whole-market)** → Mitigation: Phase 1 dedupes via `price_history` PK; if a row for today exists, `_existing_price_dates` skips the fetch. So second click within the same day is fast (Phase 2 only).
- **[Risk] Status-poll race: two tabs, one click each** → Both share `_LATEST_RESULTS` keyed by `started_at`. Polling `latest_status()` returns the most recent. Acceptable: both tabs see the same final state.
- **[Risk] Today is a non-trading day (weekend/holiday)** → Phase 1 returns empty; Phase 2 forward-fills from prior trading day's snapshot (the PR #8 behavior). Chart line stays flat across the weekend. Same as the daily job's behavior. Acceptable.
