## Why

The `portfolio_snapshot` table is currently sparse — Phase 2 skips weekends, market holidays (both TWSE+TPEx empty), and (since the active-date optimization) no-holding days. The frontend net-worth chart relies on Chart.js line interpolation to bridge the gaps, which works visually only when the gap is a 2-day weekend. For 4-to-7-day Taiwan holiday clusters (春節, 中秋連假, 國慶連假), and for legacy rows already in the table that were written before holiday-skip existed, the chart shows misleading totals: `MV=0` while `cost>0`, producing visible "drops to zero" on the 總市值 series. This contradicts reality — on a holiday the user still holds their position; the previous trading day's close is the honest carry-forward.

## What Changes

- Phase 2 (`replay_snapshots_range`) SHALL write a `portfolio_snapshot` row on every weekend and holiday in the requested range, using the previous trading day's market value (forward-fill) when the user held something on that prior trading day. When the user held nothing on the prior trading day, no row is written (matches active-date semantics).
- The forward-filled row SHALL carry the same `total_market_value` as the prior trading day's snapshot, the prior day's `total_cost` (no tx happened on a holiday), and the cumulative `total_dividends` / `total_realized_pnl` as of that date.
- Phase 2 SHALL self-heal pre-existing stale `MV=0 cost>0` rows: when about to skip a date that already has a snapshot row in the DB with `total_cost > 0`, the function SHALL DELETE that stale row instead of leaving it. New forward-filled rows (from this change) overwrite via the existing `merge`-on-PK path.
- The active-date set used by Phase 1 (price fetch) SHALL remain unchanged: holidays still skip the HTTP fetch (no data to fetch), and Phase 1's `dates_inactive` semantics stand. Forward-fill lives entirely in Phase 2.

## Capabilities

### New Capabilities
- _(none)_

### Modified Capabilities
- `stock-portfolio-networth-backfill`: Phase 2 changes from "trading-day-only writes" to "trading-day computes + forward-fill on weekends/holidays during held intervals + self-heal stale rows".

## Impact

- **Affected code:**
  - `services/stock-portfolio-service/app/services/networth_backfill_service.py` — `replay_snapshots_range` gains forward-fill branches inside its per-date loop; stale-row deletion helper added.
- **No schema change.** No new migrations.
- **No API surface change.** Response shape of `/api/portfolio/history` unchanged.
- **Row count growth:** `portfolio_snapshot` gains ~115 extra rows/year (52 × 2 weekend + ~11 holidays) per user actively holding through the period. Trivial relative to existing trading-day rows.
- **Cleanup:** stale `MV=0 cost>0` rows already in the DB are deleted by the first chain run that covers their date range. No separate migration script.
- **Tests:** unit tests for forward-fill correctness (Mon→Sat weekend, 春節 7-day cluster, holiday-on-day-after-SELL semantics) + stale-row self-heal scenario; integration test verifies chart-relevant date coverage.
- **Risk:** if forward-fill writes a row on a date when the user genuinely held nothing on the prior trading day, the chart would show stale value. Mitigated by gating forward-fill on "prior trading day's holdings > 0" check.
