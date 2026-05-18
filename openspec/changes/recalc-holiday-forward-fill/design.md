## Context

Today's `replay_snapshots_range` (`networth_backfill_service.py:345-519`) walks every day in `[from_d, to_d]` and writes a snapshot row only when ALL of:
1. The date is a weekday (Mon-Fri) — weekend dates are skipped.
2. The date is in the active-date set (when provided) — no-holding dates skipped.
3. The date is in `trading_dates = {d for (sym, d) in price_map.keys()}` — full-market holidays skipped.

This produces a sparse table. The chart bridges short gaps via Chart.js line interpolation. Long gaps (春節 cluster, 中秋連假) plus pre-existing stale `MV=0 cost>0` rows in the DB cause the visible bug: the chart drops to zero on dates the user actually held a position.

Verified DB sample (May 2026): stale rows on 2022-01-27→2022-02-04 (春節), 2022-04-04/05 (清明連假), 2023-01-18→01-25 (春節 2023), all with `MV=0` and `cost > 0`. These were written by an earlier Phase 2 implementation that didn't skip holidays; they have never been overwritten because the current Phase 2 skips holiday dates entirely (no DELETE either).

## Goals / Non-Goals

**Goals:**
- Every weekend / holiday date inside an active holding interval gets a `portfolio_snapshot` row carrying the previous trading day's MV.
- Pre-existing `MV=0 cost>0` rows are self-healed by Phase 2 (DELETE on next chain run).
- No frontend / API change — chart sees a dense, honest series.
- Forward-fill is gated by "prior trading day's MV exists and the user held something then" — never invents value out of nothing.
- Idempotent: re-running the chain on the same range produces the same result.

**Non-Goals:**
- Holiday calendar caching (option A earlier — separate concern, defer).
- Multi-currency / FX adjustment.
- Realized P&L event table (separate change).
- Updating the daily-cron path (live `daily_snapshot_job`) — already writes a row per trading day; weekends/holidays on the live path also drop in (no forward-fill on cron). Out of scope here; only the backfill / chain-driven replay path is touched.

## Decisions

### D1: Forward-fill inside the same `replay_snapshots_range` loop

Add the forward-fill branch directly in the existing per-date loop, NOT a second pass. The loop already maintains running `qty[sym]`, `cost[sym]`, `cumulative_realized`, and `cumulative_dividends`. On a "would-have-skipped" date (weekend, holiday, or inactive), inspect the prior-trading-day MV held in a new local `last_trading_mv: Decimal | None` variable and decide whether to write a forward-fill row.

**Rationale:** single pass, no extra DB round-trip, no risk of state drift between two passes.

**Alternative:** post-Phase-2 sweep over the date range filling holes. Rejected — duplicates the loop's state-tracking logic, more LoC, easier to drift from cron path's semantics.

### D2: Forward-fill gating

Write a forward-fill row on date `cur` if and only if:
1. `cur` would otherwise be skipped (weekend OR holiday OR not in active_dates), AND
2. `last_trading_mv is not None` (a real trading-day computation has occurred earlier in the loop, OR a snapshot row exists in the DB for the prior trading day — see D3), AND
3. `sum(qty.values()) > 0` at this point (user held something as of last tx walked, which is consistent with prior trading day given no tx fires on holidays).

**Rationale:** condition 3 lets a closed-position no-holding gap stay empty (correct), while a held-through-holiday gap fills (correct).

### D3: Bootstrapping `last_trading_mv` at range start

If `from_d` itself is a weekend/holiday and the user already held something then (continuation from an earlier date), the loop must seed `last_trading_mv` from the DB. On range entry, look up the most-recent `portfolio_snapshot.total_market_value` where `date < from_d` and `total_market_value > 0`, and use it as the initial `last_trading_mv`.

**Rationale:** without this, a chain triggered with `recalc_from = first holiday of a cluster` would never have a seed value, and the cluster would stay empty.

**Alternative:** widen `recalc_from` automatically to the last trading day before. Rejected — leaks logic into the orchestrator and would re-fetch already-cached prices.

### D4: Stale-row self-heal

On every iteration where Phase 2 would NOT write a snapshot row (any skip path), execute a single DELETE for any pre-existing `portfolio_snapshot` row on `cur` whose `total_market_value = 0` AND `total_cost > 0`. Group these deletes into one bulk DELETE statement at end-of-replay to avoid 1000+ round-trips on a large recalc.

**Rationale:** self-healing converges on a clean table after one full recalc; no migration script needed; safe (we only delete rows that match the bug signature).

**Alternative:** delete every pre-existing row in `[from_d, to_d]` before Phase 2 starts, let Phase 2 rebuild. Rejected — too destructive; users may have manually-written or daily-cron-written rows we shouldn't touch.

### D5: Forward-filled row content

A forward-filled row on date `cur` SHALL have:
- `date = cur`
- `total_market_value = last_trading_mv`
- `total_cost = last_trading_cost`
- `total_unrealized_pnl = last_trading_mv - last_trading_cost`
- `total_dividends = cumulative_dividends` as of `cur` (dividends CAN have an ex-date on a holiday, so this still advances per-iteration)
- `total_realized_pnl = cumulative_realized` as of `cur` (no SELL fires on a holiday, so equals prior trading day)
- `portfolio_xirr = NULL` (preserves existing scenario)

**Rationale:** matches what a stock-market-closed day SHOULD look like — position unchanged, dividends posted if any.

### D6: Active-date set semantics extended to include held-through holidays

`compute_active_dates` currently returns weekday dates only (Mon-Fri). To make Phase 2's forward-fill correctly recognize "user held through holiday X" without re-computing holdings, the helper SHALL be extended with an optional `include_non_trading: bool = False` flag. When `True`, the returned set includes ALL calendar dates in held intervals (weekends + holidays inclusive), and Phase 2 uses this expanded set to decide whether to forward-fill.

**Rationale:** keeps interval math in one place. Phase 1 keeps calling with the default (weekday-only) signature, no change.

**Alternative:** Phase 2 re-walks `transactions` to compute holdings on the fly. Rejected — duplicates the helper's logic.

## Risks / Trade-offs

- **Risk: forward-fill on a date where user held nothing on the prior trading day** → ghost row with wrong value. **Mitigation:** D2 condition 3 (`sum(qty.values()) > 0`) gates the write.

- **Risk: stale-row DELETE removes legitimate zero rows** (e.g., user genuinely had no holdings on date X but a daily cron row landed there). **Mitigation:** delete signature requires BOTH `MV=0` AND `cost > 0`. A legit zero-holdings row has `cost = 0` and is preserved.

- **Risk: bootstrapping `last_trading_mv` from DB returns stale data** if user has rolled their date range backwards. **Mitigation:** lookup is scoped to `date < from_d` only; Phase 2 immediately overwrites once the first real trading day in range is processed.

- **Trade-off: row count grows ~30%** (~115 extra rows/yr per active holding). Negligible — `portfolio_snapshot` is small.

- **Trade-off: cron path remains sparse on weekends/holidays.** Acceptable — the cron path runs only on trading days by design, and the next backfill / manual recalc will fill the holes.

## Migration Plan

No data migration. First chain run that covers a stale-row date range auto-deletes the bad rows and writes forward-filled replacements.

Rollback: revert the PR. Stale rows already cleaned by self-heal remain deleted; chart returns to its pre-fix interpolated behaviour on long gaps.

## Open Questions

- _(none — D1–D6 close the loop.)_
