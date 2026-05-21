## Why

`networth_backfill_service` runs its own realized-PnL replay loop that pre-dates the position_side (LONG/SHORT) feature. Loop ignores `position_side`, so Cathay 融券 / 沖賣 SHORT rows poison `portfolio_snapshot.total_realized_pnl` after every CSV import — short opens silently drop, short closes inflate long qty + cost. Two divergent realized-PnL engines now exist (`realized_pnl_service.iter_realized_events` vs the inline replay); they disagree on every history containing shorts, day-trades, or oversells.

## What Changes

- Refactor `networth_backfill_service._replay_snapshots` to delegate per-date realized PnL to `realized_pnl_service.iter_realized_events` (single source of truth)
- Drop inline realized accumulator in the replay loop; keep only qty/cost roll-up needed for MV + unrealized
- Honor `position_side` in the qty/cost roll-up: SHORT rows neither add to long qty nor contribute to long cost; long-side MV unchanged
- Fix oversell fee/tax allocation (fee+tax pro-rated by `sold/tx_qty`, not subtracted in full)
- Pick up Taiwan day-trade 0.15% tax rule automatically (already encoded in `realized_pnl_service`)
- Add regression tests covering: SHORT open/close, mixed LONG+SHORT same symbol, day-trade 沖賣, oversell, snapshot vs realized-pnl endpoint parity
- One-shot rebuild migration / CLI: `python -m app.services.networth_backfill_service --rebuild-all` to overwrite stale `portfolio_snapshot.total_realized_pnl` rows

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `stock-portfolio-networth-backfill`: replay loop MUST source realized PnL from `realized_pnl_service`; MUST skip SHORT rows in long qty/cost roll-up
- `stock-portfolio-snapshot`: `portfolio_snapshot.total_realized_pnl` MUST match `/api/portfolio/realized-pnl` for any `[from, to]` range
- `stock-portfolio-realized-pnl`: `iter_realized_events` becomes the canonical engine; snapshot consumer added

## Impact

- Code: `services/stock-portfolio-service/app/services/networth_backfill_service.py` (replay loop), import-orchestration step C
- Data: existing `portfolio_snapshot.total_realized_pnl` values stale until rebuild migration runs
- API: `/api/portfolio/imports/recalc/status`, `/api/portfolio/realized-pnl`, `/api/portfolio/networth` — values change post-rebuild
- Tests: `tests/unit/test_networth_backfill_service.py`, `tests/integration/test_imports_recalc.py`
- No schema change; no breaking API change (response shape unchanged, values corrected)
