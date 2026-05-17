## Context

CSV import (`POST /api/portfolio/import/transactions/commit` and `/dividends/commit`) currently inserts rows in a single transaction and returns a count. Three independent downstream systems hold derived state:

1. **`symbol_map`** — maps Chinese names → tickers, used by transaction listing UI.
2. **`dividend_event` cache** — populated by `dividend_event_service` from TWT48U OpenAPI + multi-source fallback.
3. **`portfolio_snapshot`** — daily networth row, written by `portfolio_snapshot_service` (daily cron) and `networth_backfill_service` (range backfill).

Today, none of these refresh when import runs. The user sees inconsistent state until each system's cron fires (worst case: ~24h for snapshot). The CSV upload page also exists (route `/portfolio/import`) but is not linked from `app.html` nav, so most users never find it.

## Goals / Non-Goals

**Goals:**
- Make import the single user action that fully refreshes derived state.
- Keep import endpoint response fast (< 500 ms): chain runs in `BackgroundTasks`, not blocking the HTTP response.
- Provide a manual re-trigger endpoint for retries after partial failures.
- Surface chain progress in the import UI so users know recalc is running.

**Non-Goals:**
- Schema migrations (no new tables/columns).
- Fix fingerprint collision for genuinely identical same-day fills — separate change.
- Real-time push of chain progress (SSE/WebSocket) — toast-on-completion is enough.
- Parallelism inside the chain — sequential is simpler and fits TWSE rate limits.
- Persistent task queue (Celery/RQ) — `BackgroundTasks` is enough for single-user deployment.

## Decisions

### D1 — `BackgroundTasks`, not Celery
FastAPI `BackgroundTasks` runs after the response is sent, in-process. No broker, no worker, no extra Docker service. Fits the single-instance deployment. If the process crashes mid-chain, manual `/recalc` endpoint covers the retry path.

Alternative: Celery + Redis. Rejected — overkill for one user; introduces a new failure mode (broker outage) and one more service to monitor.

### D2 — Backfill window = `min(new_tx.trade_date) → today`
Moving-average cost basis cascades forward, so any backdated import requires recomputing every snapshot from that date onward. There is no cheaper correct window. (Earlier exploration considered "only newly-affected dates" but it collapses to the same range whenever any tx is backdated, which is the common case.)

If today's daily snapshot already exists, `networth_backfill_service` overwrites it with `merge` — safe.

### D3 — Skip-on-zero-inserts guard
```python
if import_result.inserted_count == 0:
    return  # idempotent re-upload, nothing to recalculate
```
Prevents unnecessary TWSE traffic and snapshot churn on re-uploads. Relies on existing fingerprint UNIQUE constraint to make `inserted_count` accurate.

### D4 — Sequential chain, fail-loud
Steps run sequentially: symbol-name backfill → dividend re-fetch → networth backfill. Each step's errors are caught at the step boundary and recorded in a `ChainResult` object; chain continues to the next step. Final result includes a per-step `status: "ok" | "partial" | "failed"`.

Rationale: a TWSE outage during dividend fetch should not block the networth backfill from running with the price data we already have cached.

### D5 — Manual re-trigger endpoint accepts a date range
```
POST /api/portfolio/import/recalc
Body: { "start_date": "2024-01-15", "end_date": "2026-05-17" }
```
Defaults: `start_date = min(transactions.trade_date)`, `end_date = today`. Lets the user re-run the chain after fixing transient TWSE errors without re-uploading the CSV.

### D6 — Frontend toast, not blocking modal
Import commit returns immediately (200 OK with the row count). UI shows a non-blocking "Recalculation running…" toast. Toast updates to success / partial / failed via a polling call to `GET /api/portfolio/import/recalc/status` (in-memory status object keyed by request id).

Alternative: block the import UI until chain finishes. Rejected — TW history backfill can take 5-10 min; blocking would feel broken.

## Risks / Trade-offs

- **[Risk]** `BackgroundTasks` state is in-process — restart mid-chain loses the run.
  **Mitigation**: manual `/recalc` endpoint covers retries; status object is best-effort and not persisted.
- **[Risk]** Two imports in quick succession overlap chains, contending for TWSE rate limit.
  **Mitigation**: use an `asyncio.Lock` keyed on "recalc-chain-running"; second import's chain waits, OR returns 409 if user prefers. Default: serialize via lock.
- **[Risk]** User loses their toast (refresh) and assumes chain failed.
  **Mitigation**: `/recalc/status` returns last result for ~10 minutes after completion; UI fetches on import-page mount.
- **[Risk]** Networth backfill of large range slows TWSE for other features.
  **Mitigation**: existing `networth_backfill_service` already uses the cached `twse_client` with 3 req/sec throttle.

## Migration Plan

No data migration. Deploy order:
1. Backend ship: `post_import_orchestrator` module + router wiring + `/recalc` endpoint. Old import path keeps working (chain just doesn't fire if feature-flag off).
2. Frontend ship: nav link + toast + status polling.
3. Verify on staging with a 50-row CSV golden path; then with a 500-row historical CSV.

Rollback: feature-flag `POST_IMPORT_RECALC_ENABLED=false` (env var) short-circuits the orchestrator call. Manual `/recalc` still works.

## Open Questions

None blocking. (UI placement — nav link vs. button — confirmed as nav link in `app.html`.)
