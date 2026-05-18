## Why

The post-import recalc chain currently walks every weekday in `[recalc_from, recalc_to]` for both Phase 1 (price fetch) and Phase 2 (snapshot replay), even when the user held zero shares of any symbol during long stretches of that range. A single CSV import containing a 2022 transaction triggers ~1100 weekdays of work — Phase 1 dominates (~1.5s throttle × 1100 = ~30min cold wall time). For closed positions (BUY+SELL fully unwound) this is almost entirely wasted work.

## What Changes

- Compute per-symbol active holding intervals from `transactions` (and stock-dividend share grants), union them into an `active_dates` set bounded by `[recalc_from, recalc_to]`.
- Phase 1 (`backfill_prices_range`) only fetches market data on dates in `active_dates`; non-active weekdays are skipped without an HTTP request and without throttle sleep.
- Phase 2 (`replay_snapshots_range`) only writes `portfolio_snapshot` rows on dates in `active_dates`; non-active dates are skipped (no row written, no recompute).
- SELL transactions are always retained in the active set (a SELL that closes a position belongs to the interval's last day, so realized P&L math is unaffected).
- Active-date computation is a single SQL aggregation over `transactions` + `dividends`, run once at the top of `run_chain`, then passed into both phase functions.

## Capabilities

### New Capabilities
- _(none)_

### Modified Capabilities
- `stock-portfolio-networth-backfill`: Phase 1 and Phase 2 gain an `active_dates` filter; weekdays where the user held nothing skip both HTTP fetch and snapshot upsert.
- `stock-portfolio-import-orchestration`: `run_chain` computes the active-date set before kicking off Phase 1 / Phase 2 and passes it down.

## Impact

- **Affected code:**
  - `services/stock-portfolio-service/app/services/networth_backfill_service.py` — both `backfill_prices_range` and `replay_snapshots_range` gain an optional `active_dates: set[date] | None` parameter; when provided, dates outside the set are skipped.
  - `services/stock-portfolio-service/app/services/post_import_orchestrator.py` — `run_chain` derives `active_dates` from the DB once and forwards into both step runners.
  - New helper (likely in `networth_backfill_service` or new `holding_intervals.py`) to compute per-symbol intervals + union into the active-date set.
- **No schema change.** No new migrations. Behaviour change is read-only over `transactions` + `dividends`.
- **No API change.** `/api/portfolio/imports/recalc` body unchanged; response counts unchanged.
- **Tests:** existing networth backfill + orchestrator tests stay valid; add unit tests for the interval-union helper + integration test that proves a fully-closed 2022 position skips Phase 1 fetch for all dates after the SELL.
- **Risk:** if the helper miscomputes intervals, snapshots/prices for held dates could be skipped — guarded by tests covering open positions, closed positions, multi-symbol overlap, and stock-dividend share grants.
