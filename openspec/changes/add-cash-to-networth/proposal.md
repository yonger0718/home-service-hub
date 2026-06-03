## Why

`portfolio_snapshot` and the dashboard networth chart currently track only stock holdings (`total_market_value`, `total_dividends`, etc.). Cash balances live in a separate `cash_transaction` ledger introduced by `add-broker-cash-accounts` and are invisible on the dashboard. The user cannot see total net worth (stocks + cash) at a glance, and the historical chart understates wealth by the amount of idle cash.

## What Changes

- Add `total_cash_twd` column to `portfolio_snapshot` (NUMERIC(20,4) NOT NULL DEFAULT 0); Alembic migration with reversible upgrade/downgrade
- Extend `portfolio_snapshot_service.write_today_snapshot` to sum cash balances across all active accounts at `today` (TWD-converted via `fx_rate_service.get_rate(date, base, "TWD")` with as-of-fallback + USD pivot per existing semantics); skipped accounts (no FX rate available) are recorded in a snapshot-side `skipped_currencies` log line, NOT persisted
- Extend `networth_backfill_service` to compute `total_cash_twd` for each historical date in the backfill window using the same logic
- `GET /api/portfolio/history` response items add `total_cash_twd: string` field
- New `total_assets_twd: string` derived field on response items = `total_market_value + total_cash_twd` (server-side, no extra cost)
- Frontend dashboard networth chart: two overlaid (non-stacked) area series — `總資產` (top, `total_assets_twd`) over `總市值` (`total_market_value`); the vertical gap represents cash. Window selector (1M/3M/1Y/All) unchanged
- New dashboard tile `總資產` above the existing tile row, showing LIVE `summary.total_market_value + Σ get_total_balance_in("TWD")` (current value, not from snapshot)
- LIVE summary endpoint (`GET /api/portfolio/summary`) gains `total_cash_twd: string` and `total_assets_twd: string` derived fields so the tile renders without a second call

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-cash-accounts`: live summary aggregation exposed via portfolio summary endpoint; snapshot writer reads cash totals (introduces dependency from `portfolio_snapshot_service` to `cash_account_service.get_total_balance_in`)
- `frontend-portfolio-dashboard`: networth chart becomes a two-line overlay (`總資產` over `總市值`, gap = cash); new 總資產 tile above existing tile row

## Impact

**Backend** (`services/stock-portfolio-service/`):
- `app/models/portfolio_snapshot.py`: add `total_cash_twd` column
- `alembic/versions/<new>_add_total_cash_twd.py`: ADD COLUMN with default 0; DOWNGRADE drops it
- `app/services/portfolio_snapshot_service.py`: `write_today_snapshot` calls `cash_account_service.get_total_balance_in(db, "TWD", asof=target)` and writes the value
- `app/services/networth_backfill_service.py`: same per-date computation in the historical loop
- `app/routers/history.py`: `_serialize_snapshot` includes `total_cash_twd` + derived `total_assets_twd`
- `app/routers/portfolio.py` (or wherever `GET /summary` lives): include `total_cash_twd` + `total_assets_twd` in the live response
- `app/schemas/portfolio.py`: extend the summary response model
- Tests: extend `tests/unit/test_portfolio_snapshot_service.py` (or add new), `tests/unit/test_networth_backfill_service.py`, `tests/integration/test_history_endpoint.py`, `tests/integration/test_portfolio_summary.py` for the live derived fields

**Frontend** (`frontend/src/app/`):
- `models/portfolio.model.ts`: extend `NetworthPoint` with `total_cash_twd` + `total_assets_twd`; extend `PortfolioSummary` with the same two
- `components/portfolio/dashboard/dashboard.{ts,html,scss}`: two-line overlay datasets (`總資產` + `總市值`), new tile, recompute cache invalidation rules unchanged
- `components/portfolio/dashboard/dashboard.spec.ts`: two-dataset (non-stacked) assertions, tile renders combined total

**Rollout**:
- Deploy backend + migration (`alembic upgrade head`) → existing snapshots keep `total_cash_twd = 0` (NOT NULL with default)
- Run `python -m app.services.networth_backfill_service --rebuild-all` to recompute historical cash for every snapshot date (existing CLI per `reference_realized_pnl_canonical_engine` memory pattern)
- Deploy frontend
- No feature flag — change is additive (`total_cash_twd` defaults to 0, chart shows existing data correctly until backfill completes)
