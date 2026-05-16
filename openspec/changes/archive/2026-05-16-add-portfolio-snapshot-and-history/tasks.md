## 1. Model + Migration

- [x] 1.1 New `app/models/portfolio_snapshot.py` — `PortfolioSnapshot` with `date PRIMARY KEY`, `total_market_value` `NUMERIC(20,4)` non-null, `total_cost` `NUMERIC(20,4)` non-null, `total_unrealized_pnl` `NUMERIC(20,4)` non-null, `total_dividends` `NUMERIC(20,4)` non-null, `portfolio_xirr` `NUMERIC(10,6)` nullable, `created_at` server-default
- [x] 1.2 Alembic revision `h5c6d7e8f9a0_add_portfolio_snapshot_table` after `g4b5c6d7e8f9` with reversible downgrade
- [x] 1.3 Register model in `alembic/env.py` and add `from .models import portfolio_snapshot` no-op in `app/main.py`

## 2. Snapshot Service + Scheduler Wire-In

- [x] 2.1 New `app/services/portfolio_snapshot_service.py` — `write_today_snapshot(db, *, today=None) -> PortfolioSnapshot` that calls `portfolio_service.get_portfolio_summary(db)`, extracts the five totals + xirr, upserts via `Session.merge` keyed by today's TW calendar date, commits, and returns the persisted row
- [x] 2.2 Add `list_snapshots(db, *, from_date, to_date) -> list[PortfolioSnapshot]` returning ascending by date with inclusive range
- [x] 2.3 Replace `run_portfolio_snapshot_stub` in `app/services/scheduler.py` with `run_portfolio_snapshot(session_factory)` that wraps `write_today_snapshot` and structlog-logs `{date, total_market_value, total_cost}` on success; catches and logs `event=snapshot.failed` on any exception without re-raising

## 3. Endpoint

- [x] 3.1 Extend `app/routers/history.py` with `GET /api/portfolio/history?from=&to=` — defaults: if both omitted, last 90 days ending today (TW)
- [x] 3.2 Add `POST /api/portfolio/history/snapshot` — manual trigger calling `write_today_snapshot`; returns the persisted row as JSON

## 4. Backend Tests

- [x] 4.1 `tests/unit/test_portfolio_snapshot_service.py` — write_today_snapshot inserts new row, repeat call same day overwrites, multiple dates retrievable, list_snapshots inclusive range + ascending order
- [x] 4.2 Scheduler test: `run_portfolio_snapshot` swallows exceptions raised by `write_today_snapshot` (does not propagate)
- [x] 4.3 Endpoint tests: GET default 90-day window, GET explicit range, POST manual trigger writes and returns row

## 5. Frontend Chart (delegated)

- [x] 5.1 Add `NetworthPoint` interface to `frontend/src/app/models/portfolio.model.ts`
- [x] 5.2 Add `getNetworthHistory(from?: string, to?: string): Observable<NetworthPoint[]>` to `PortfolioService`
- [x] 5.3 New standalone `networth-chart` component (PrimeNG `<p-chart type="line">`) with 1M/3M/1Y/All window selector (PrimeNG `p-selectButton`); fetches on init and on window change
- [x] 5.4 Embed `<app-networth-chart>` on the dashboard above the holdings table

## 6. Verification

- [x] 6.1 Full `pytest` — 158 prior + new tests pass
- [x] 6.2 Alembic upgrade/downgrade clean for the new revision
- [x] 6.3 Manual: hit `POST /api/portfolio/history/snapshot` and `GET /api/portfolio/history` to confirm round-trip (validated end-to-end during the networth-backfill work; replay populated >1000 snapshot rows and the history endpoint serves them with proper interval downsampling)
- [x] 6.4 Manual: chart renders on dashboard with at least one point after a snapshot exists (validated; networth-chart on /portfolio dashboard shows MV / cost / realized P&L lines across full snapshot range)
