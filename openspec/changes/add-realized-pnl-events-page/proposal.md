## Why

Portfolio service exposes a cumulative `total_realized_pnl` number on the dashboard but provides no way to see *which* trades produced that number. Users cannot audit a year-to-date figure, file taxes, or investigate a surprising aggregate without re-deriving events from raw transactions by hand.

## What Changes

- Add `GET /api/portfolio/realized-pnl` endpoint that returns one event per SELL transaction (moving-average cost basis), plus filter-scope and YTD aggregates.
- Refactor the existing moving-average loop in `portfolio_service._step_transactions` into a shared pure helper so the summary path and the new events path use a single source of truth for cost-basis math (no behavior change for the dashboard).
- Add a new Angular page at `/portfolio/realized-pnl` with an aggregate header (filter-scope total + YTD), filter bar (symbol, date range, year preset, day-trade toggle, sort), expandable event rows, and pagination.
- Add a top-level nav entry "已實現損益" alongside existing 交易紀錄 / 股息 links.
- Pure compute-on-read. No new tables, no migrations.

## Capabilities

### New Capabilities
- `stock-portfolio-realized-pnl`: per-SELL realized profit-and-loss event listing, with filtering, aggregate summary, and the invariant that the sum of events equals the dashboard's cumulative realized total.

### Modified Capabilities
<!-- None: existing summary endpoints keep identical behavior; only an internal helper is extracted. -->

## Impact

- Backend: `services/stock-portfolio-service/app/services/portfolio_service.py` (refactor extract), new `app/services/realized_pnl_service.py`, new `app/routers/realized_pnl.py`, new `app/schemas/realized_pnl.py`, registered in `app/main.py`. New tests under `tests/unit/` and `tests/integration/`.
- Frontend: new `frontend/src/app/components/portfolio/realized-pnl/` component, new route in `app.routes.ts`, new method on `portfolio.service.ts`, new interfaces in `models/portfolio.model.ts`, new nav link.
- No DB schema change, no Alembic migration, no new dependencies.
- Reads route through corporate-action-adjusted transaction view (same as existing summary path), so corporate-action retro splits and dividend recalc chains do not need event-side invalidation.
