## Context

`portfolio_snapshot` rows are written daily by the 15:30 TW scheduler. Users who imported years of historical transactions only see a flat networth line from feature-launch day forward. `price_history` is similarly empty for the past. Existing single-date helper `market_data_service.backfill_date` already pulls TWSE + TPEx daily close into `price_history`; need a range driver around it plus an offline replay to recompute `portfolio_snapshot` rows from cached prices.

## Goals / Non-Goals

**Goals:**
- One-shot, idempotent backfill of historical networth (1–5 years typical).
- Stay polite to TWSE/TPEx (throttle, retry-with-backoff, holiday skip).
- Two phases independently re-runnable: prices vs snapshots.
- Zero impact on the daily-cron snapshot path.

**Non-Goals:**
- Intraday snapshots.
- XIRR on backfilled rows (left `NULL`; XIRR needs cashflow streams already computed on-demand).
- Carry-forward of suspended-trading days (treat missing close as "skip symbol that day"; rare and acceptable for v1).
- Corporate-action split-factor adjustment on backfilled rows. Stock dividends are already captured as zero-cost BUY transactions by the auto-record service, so they ARE included. True split factors (TWTB8U feed) are not retroactively applied to backfilled rows; the live daily-cron path keeps applying them going forward.
- FX / multi-currency (TW-only).
- Automated yearly trickle-backfill (manual invoke only).

## Decisions

### D1: Two-phase split (prices, snapshots) under one endpoint
- **Choice:** Same endpoint, `phase` field selects `prices` / `snapshots` / `both`.
- **Alternatives:** Two endpoints (cleaner URL but doubles router surface); one combined-only (forces re-pull of prices when fixing replay logic).
- **Rationale:** Replay logic will iterate; re-pulling 1 250 trading days each retry is wasteful. Single endpoint keeps URL surface small.

### D2: Date-batched fetch, not per-symbol
- **Choice:** Reuse `fetch_twse_date(date)` + `fetch_tpex_date(date)` (one HTTP call each returns all symbols).
- **Alternatives:** Per-symbol monthly endpoint via twstock (`Stock(symbol).fetch_from(year,month)`); hits same TWSE backend but multiplies calls by symbol count.
- **Rationale:** Date-batched is O(days), per-symbol is O(days × symbols). Holding 20 symbols × 1 250 days = 25 000 calls vs 2 500. Massive rate-limit win.

### D3: Throttle = 1.5 s gap between dates; retry = 2 s → 5 s
- **Choice:** `time.sleep(1.5)` between dates inside backfill driver only.
- **Alternatives:** No throttle (current single-call path); aggressive 0.3 s (risk 429); exponential always-on retry (over-engineered for manual one-shot).
- **Rationale:** Anecdotal TWSE ceiling ~3 req/s → 1.5 s gap = ~0.67/s, well under. Retry with 2 s + 5 s backoff handles transient connection drops without long stalls.

### D4: Holiday probe via empty-payload, not calendar lookup
- **Choice:** If `fetch_twse_date(date)` returns `[]`, treat as holiday/closed, log skip, no sleep.
- **Alternatives:** Hardcode TW holiday list per year (must maintain); query TWSE FBT_RWD calendar once per year (extra endpoint).
- **Rationale:** Probe-on-failure is cheap and self-maintaining. Cost = one wasted HTTP call per holiday (~10/year). Acceptable.

### D5: Holdings-as-of replay reads transactions cumulatively per date
- **Choice:** For each date in range, `SELECT symbol, SUM(quantity * sign(BUY|SELL))` `WHERE trade_date <= date GROUP BY symbol` + add stock-dividend shares whose ex-date `<= date`. Multiply by `price_history.close` for same date.
- **Alternatives:** Incremental running-totals computed forward in memory (faster, harder to verify); per-symbol time series join (Cartesian explosion).
- **Rationale:** Per-date GROUP BY is N small queries (one per date × number-of-held-symbols), easy to test, idempotent. Composite indexes added so queries hit them. For 5 y × ~250 trading days = 1 250 queries; PG handles trivially.

### D6: Idempotent upsert via `Session.merge` on `date` PK
- **Choice:** Re-runs overwrite same-date rows; partial re-runs (e.g., last 30 days) preserve older rows.
- **Alternatives:** `DELETE then INSERT` (loses other-date rows if range mis-specified); `INSERT ... ON CONFLICT` (PG-specific, breaks H2 in tests if we add tests later).
- **Rationale:** Matches existing `portfolio_snapshot_service.write_today_snapshot` pattern.

### D7: No schema changes — existing indexes suffice
- **Choice:** Reuse existing `price_history` composite PK `(symbol, date)` and existing `ix_transactions_symbol_trade_date`.
- **Rationale:** Replay queries (`price_history WHERE symbol=? AND date=?`, `transactions WHERE symbol=? AND trade_date <= ?`) are already index-hit. No migration risk.

## Risks / Trade-offs

- **Risk:** TWSE/TPEx changes their JSON shape mid-backfill → all subsequent dates fail. **Mitigation:** Per-date error isolation logs and continues. User re-runs after parser fix.
- **Risk:** Symbol delisted in past → `price_history.close` missing for some date. **Mitigation:** Replay skips symbol on that date (MV slightly understated). Logged at WARN. Carry-forward deferred to v2.
- **Risk:** User invokes with 10 y range during market hours → ~60 min of background HTTP traffic. **Mitigation:** Endpoint logs progress; user can interrupt; per-phase idempotency means restart is cheap.
- **Risk:** Stock dividend shares not yet applied to historical date when held-quantity needed. **Mitigation:** Holdings-as-of explicitly sums `Dividend.stock_dividend_shares` where `ex_dividend_date <= date AND payable_date <= date` (or use ex-date as recorded; documented in spec).
- **Trade-off:** Single user-blocked HTTP call (synchronous request handler) for up to 30 min — acceptable for personal-use deployment, not for multi-user.

## Migration Plan

1. Deploy code (no schema changes).
2. User triggers `POST /api/portfolio/history/backfill-networth` with desired range.
3. Rollback: remove endpoint and service module; daily-cron path untouched throughout.

## Open Questions

- Should we expose progress streaming (SSE) for long backfills? **Deferred to v2.** v1 = synchronous, returns aggregated counts at end.
- Should phase=both interleave (per-date: fetch prices → replay snapshot) or batch (all prices first, all snapshots second)? **Choice = batch.** Simpler error semantics; replay can re-run independently if interrupted.
