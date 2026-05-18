## Context

`networth_backfill_service._fetch_with_retry()` retries empty fetches (2s + 5s backoff) but does NOT inspect row count of non-empty responses. `_existing_price_dates()` uses `SELECT DISTINCT (source, date) FROM price_history`, so a single row from a partial response marks the date as "covered" and prevents future re-fetches.

Observed failure mode (2026-05-18): net-worth chart dropped without any transaction change. Today's row counts for the latest snapshot: TWSE 1353, TPEx 5303. Past partial responses are suspected but not directly logged; no row-count history captured today.

This change adds a single gate between fetch and upsert: compare today's count against a rolling median of recent successful counts, per source. The gate runs on the whole-market path only (`_fetch_with_retry` → upsert in `networth_backfill_service`). It does not touch per-symbol fast-path quote refresh, which already trusts upstream.

## Goals / Non-Goals

**Goals:**
- Prevent partial whole-market responses from polluting `price_history` and locking dates out of future retries.
- Make detection observable via a structured warning log so we can correlate future drops with upstream behavior.
- Self-adjust to actual market size — no hardcoded floor that goes stale.
- Zero schema change; pure read-only addition.

**Non-Goals:**
- Auto-retry partial responses (Q2 = A: skip + log; next scheduled fetch retries naturally).
- Mark partially-persisted dates as `is_partial` (rejected — no schema migration, no consumer-side flag handling).
- Expose a manual retry endpoint (rejected — already covered by `刷新行情` button and the next-day scheduled fetch).
- Detect partials in `_fetch_with_retry` itself (rejected — couples threshold logic with HTTP retry; partial responses are unlikely to recover within 7s).
- Backfill missing data for dates already locked by past partial responses (out of scope; could be a follow-up `cleanup-historical-partials` change).

## Decisions

### D1. Threshold = rolling-30-day median, drop ratio < 80%

Compare today's fetched row count against the median of the last 30 trading-day counts for that source. If `today / median < 0.8`, classify as partial.

**Alternatives considered:**
- *Static floor (e.g. TWSE < 800)*: brittle — TPEx endpoint returns mixed types (stocks ~800, +ETFs ~1200, +warrants ~5000+). A single floor cannot distinguish "full but smaller market" from "partial fetch when warrants are missing". Rejected.
- *Both static + median*: extra knobs, two thresholds to tune, no marginal benefit. Rejected.

**Tunables (constants in `networth_backfill_service.py`):**
- `PARTIAL_FETCH_RATIO = 0.8` — flag if today < 80% of median.
- `PARTIAL_FETCH_MIN_BASELINE_DAYS = 10` — cold-start warm-up threshold.
- `PARTIAL_FETCH_BASELINE_WINDOW_DAYS = 30` — rolling window size.
- `PARTIAL_FETCH_LOOKBACK_DAYS = 45` — calendar-day cap on the SQL `WHERE date >= today - N` clause (covers 30 trading days + weekends/holidays).

### D2. Action on partial = skip persist + log warning

Return early before the upsert; emit one warning log with full diagnostic context. No rows persisted for the partial source on the partial date. The other source for the same date is unaffected (TWSE and TPEx evaluated independently).

**Alternatives considered:**
- *Persist with `is_partial` flag*: requires schema migration, and every downstream query / Phase 2 path must handle the flag — high risk of forgetting and using partial data anyway. Rejected.
- *Skip + explicit retry endpoint*: redundant with existing `刷新行情` button + next-day scheduler. Rejected.

### D3. Check location = after `_fetch_with_retry`, before upsert

The existing flow at `networth_backfill_service.py:289–311` is:
1. Empty-fetch branch (`if not twse_rows and not tpex_rows`) at lines 289–308 handles holidays / fetch failures and `continue`s before reaching the upsert.
2. Combined upsert at line 311: `market_data_service.upsert_rows(db, [*twse_rows, *tpex_rows])`.

The partial-fetch gate sits **between** these: for each non-empty source, call `_is_partial_response(...)`; on `True`, zero out that source's `rows` list (so the splat into `upsert_rows` becomes a no-op for that source) and append a `BackfillError`. The upsert call site itself is unchanged in shape — preserving single-transaction semantics for the non-partial source.

If both sources are classified partial, add a final `if not twse_rows and not tpex_rows: continue` before the upsert to avoid calling `upsert_rows(db, [])`.

**Alternatives considered:**
- *Inside `_fetch_with_retry`*: couples threshold detection with HTTP retry; would waste 7s of backoff retries on partial responses that almost never recover within seconds; harder to disable for debugging. Rejected.
- *Per-source `if ... continue`*: would skip BOTH sources because they share the same loop body. Rejected — must use per-source `rows = []` zeroing instead.
- *Splitting the upsert into two separate `upsert_rows` calls per source*: more invasive change, splits the single-transaction guarantee. Rejected in favour of the zero-out approach.

### D4. Per-source baselines

TWSE and TPEx maintain separate median baselines. Their row counts differ by orders of magnitude (TWSE ~1300, TPEx ~5300 today) and an underfetch on one source should not falsely lower the baseline for the other.

### D5. Baseline query

```sql
SELECT date, COUNT(*) AS n
FROM price_history
WHERE source = :source
  AND date >= :today - INTERVAL '45 days'
  AND date < :today
GROUP BY date
ORDER BY date DESC
LIMIT 30;
```

Take Python `statistics.median()` over the `n` values. Excludes today's in-progress date by the `date < :today` clause. Zero-row dates (holidays, prior partial-fetch skips) do not appear in the GROUP BY result and therefore do not depress the median — this is intentional. The trade-off: a stretch of recent holidays merely shrinks the baseline list length toward the cold-start threshold (which would then short-circuit the gate via `phase1.partial_check_skipped_cold_start`), it does not inflate the median.

### D6. Cold-start handling

If the query returns fewer than `PARTIAL_FETCH_MIN_BASELINE_DAYS` rows, skip the partial check entirely and log `phase1.partial_check_skipped_cold_start` once per (source, date). Always upsert in cold-start mode — we have no signal to reject on.

## Risks / Trade-offs

- **[Risk]** The rolling median can drift down if many recent days were themselves partial (poisoning the baseline). → Mitigation: 80% ratio gives some headroom; manual recovery would be to run a one-shot SQL to delete obviously partial dates from `price_history` and let the next fetch refill. Document this in the change's tasks.md as a manual runbook step if it occurs.
- **[Risk]** A legitimately-smaller market day (e.g. half-day session, major delisting wave) flagged as partial. → Mitigation: 80% ratio cushion; warning log shows the count so operator can spot-check and force a refresh.
- **[Risk]** Cold-start mode (first 10 days of data) silently accepts partial responses. → Accepted: production already has many months of `price_history`; cold-start only matters in dev / fresh DB scenarios where the operator is already watching.
- **[Trade-off]** Skipping persist means Phase 2 sees no prices for the partial date, so net-worth for that date will look like a holiday gap until the next successful fetch. This is the intended fail-safe — preferable to silently locking partial data.

## Migration Plan

No migration. Pure additive read + log + early-return. Deploy = restart `stock-portfolio-service`. Rollback = revert the commit; previously-partial dates that were correctly skipped remain skipped (which is what we want anyway).

## Open Questions

None — Q1/Q2/Q3 resolved (B / A / A).
