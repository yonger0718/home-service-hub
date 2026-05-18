## Why

Phase 1 whole-market price fetch (TWSE `MI_INDEX` + TPEx `daily`) can return a 200 OK response with a row count far below the actual market size (partial response — upstream truncation, parser drop, schema drift, or network cutoff). The current `_existing_price_dates()` presence-only check in `networth_backfill_service` treats any persisted row as "this date is covered", so a single stray row from a partial response permanently locks the date out of future retries. Phase 2 (snapshot replay) then computes net-worth on the partial price set, producing under-reported market value (observed 2026-05-18: net-worth chart dropped without an underlying transaction change).

## What Changes

- Add per-source rolling-30-day-median row-count baseline computed from `price_history`.
- Before upserting a Phase 1 fetch, compare today's row count against the baseline; if count is **< 80%** of the median (per source), classify the response as **partial** and skip the upsert (no `price_history` rows inserted for that source + date).
- Emit a structured warning log with `source`, `date`, `fetched_rows`, `baseline_median`, `ratio`.
- During cold-start (fewer than 10 prior trading days of `price_history` for the source), skip the partial check and log `insufficient_baseline`.
- No DB schema change. No new endpoint. No retry logic change inside `_fetch_with_retry`.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-networth-backfill`: add a partial-response gate between fetch and upsert so under-baseline whole-market responses do not poison the date.

## Impact

- Code: `services/stock-portfolio-service/app/services/networth_backfill_service.py` (new `_is_partial_response` helper + call site before upsert), plus unit + integration tests.
- DB: no schema change. New read-only query against `price_history` (`SELECT date, COUNT(*) GROUP BY date` over last ~45 calendar days, per source).
- Observability: one new warning log key `phase1.partial_fetch_skipped`.
- Behavior: partial-fetch dates remain absent from `price_history` until a future fetch returns a full payload — Phase 2 will still find no prices for that date (same as a holiday skip), which is the intended fail-safe.
- Downstream: `stock-portfolio-market-data-resilience` consumers unaffected (no new endpoint, no error class).
