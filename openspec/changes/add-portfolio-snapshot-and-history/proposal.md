## Why

`stock-portfolio-service` exposes a live `PortfolioSummary` via `GET /api/portfolio/summary`, but only "now". For users to see net-worth and unrealized-PnL drift across days, the service has to persist that summary once a day and serve a time-series back. The previous change already registered a daily `portfolio_snapshot` cron at 15:30 Asia/Taipei as a stub; this change wires it up to a real table and exposes a history endpoint, plus a small Angular line chart on the dashboard.

## What Changes

- **`portfolio_snapshot` table** keyed by `date PRIMARY KEY` capturing the five durable totals from `PortfolioSummary` (market value, cost, unrealized PnL, dividends, portfolio xirr).
- **`portfolio_snapshot_service.write_today_snapshot(db)`** — calls the existing `portfolio_service.get_portfolio_summary(db)` and upserts one row for today. Idempotent: a re-run on the same TW calendar day overwrites in place.
- **Scheduler wire-in** — replaces `run_portfolio_snapshot_stub` with the real callable so the 15:30 cron persists today's summary.
- **`GET /api/portfolio/history?from=&to=`** — returns date-ordered snapshots inside the inclusive range; bounded by a sensible default window if both params missing (last 90 days).
- **`POST /api/portfolio/history/snapshot`** — manual trigger (same shape as `/price-history/backfill`) so users can force a snapshot without waiting for cron.
- **Angular `networth-chart` component** — standalone, embedded on the dashboard with 1M/3M/1Y/All window selector; fetches from `/api/portfolio/history`.

## Capabilities

### New Capabilities

- `stock-portfolio-snapshot`: daily persistence of `PortfolioSummary` totals and a time-series query endpoint with manual override.

### Modified Capabilities

- `stock-portfolio-scheduling`: the `portfolio_snapshot` cron job stops being a stub and now persists a real row.

## Impact

- **Code**
  - `services/stock-portfolio-service/app/models/portfolio_snapshot.py` — NEW
  - `services/stock-portfolio-service/alembic/versions/h5c6d7e8f9a0_add_portfolio_snapshot_table.py` — NEW
  - `services/stock-portfolio-service/alembic/env.py` — register new model
  - `services/stock-portfolio-service/app/services/portfolio_snapshot_service.py` — NEW
  - `services/stock-portfolio-service/app/services/scheduler.py` — swap stub for real callable
  - `services/stock-portfolio-service/app/routers/history.py` — add `/history` GET + manual `POST /history/snapshot`
  - `services/stock-portfolio-service/tests/unit/test_portfolio_snapshot_service.py` — NEW
  - `frontend/src/app/models/portfolio.model.ts` — add `NetworthPoint` type
  - `frontend/src/app/services/portfolio.service.ts` — add `getNetworthHistory(from?, to?)`
  - `frontend/src/app/components/portfolio/networth-chart/networth-chart.{ts,html,scss}` — NEW
  - `frontend/src/app/components/portfolio/dashboard/dashboard.{ts,html}` — embed chart + window selector

- **API (additive)**
  - `GET /api/portfolio/history?from=YYYY-MM-DD&to=YYYY-MM-DD`
  - `POST /api/portfolio/history/snapshot`

- **Operational**
  - New table `portfolio_snapshot`. No new env vars.
  - 15:30 TW cron now writes a real row; cron payload size: 1 row/day → trivial.

- **Risks**
  - If `get_portfolio_summary` raises (TWSE outage), snapshot job swallows the error and logs `event=snapshot.failed` so the scheduler thread stays alive. We accept dropping a day in that case.
  - The first snapshot writes a row for "today" only; backfill of historical snapshots is out of scope (no transaction-time-travel logic).
