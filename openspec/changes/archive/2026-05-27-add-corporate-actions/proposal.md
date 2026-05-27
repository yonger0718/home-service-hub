## Why

TWSE issues face-value changes ("面額變更") that change a symbol's share count and reference price in lockstep — the same effect as a stock split. Today, `stock-portfolio-service` has no awareness of these events: a 1-to-10 face-value change on a held symbol silently breaks `total_quantity` and `avg_cost` until the user re-enters every historical transaction. The goal of this change is to persist those events from TWSE and adjust `PortfolioSummary` at read time so historical holdings remain consistent with current market quotes — without rewriting transaction rows.

## What Changes

- **`corporate_actions` table** — one row per `(symbol, effective_date)` capturing the ratio, source event key, and raw payload for audit. Composite unique on `source_event_key` so repeat backfills are idempotent.
- **Ported `TwseSplitFetcher` (synchronous, symbol-keyed)** — hits TWTB8U JSON, parses ROC dates, computes `ratio = pre_close / post_ref`. Reuses the existing `bootstrap_truststore` + `get_tls_mode` policy from `market_data_service`.
- **`corporate_action_service`** — `backfill_year(db, year)`, `list_actions(db, *, symbol, from_date, to_date)`, and `get_split_factor(db, symbol, as_of)` returning the cumulative ratio of every action with `effective_date <= as_of`.
- **Read-time adjustment in `portfolio_service`** — when aggregating holdings and cost, each pre-event transaction's `quantity` is multiplied by the cumulative ratio and `price` is divided by it. Transaction rows are NOT mutated; the adjustment is applied per call and is reversible by deleting the corp-action row.
- **Endpoints** — `GET /api/portfolio/corporate-actions?symbol=&from=&to=` and `POST /api/portfolio/corporate-actions/backfill?year=`.
- **Frontend side-panel** — list of corp actions for the active dashboard symbol set, shown next to the holdings table.

### Out of scope

- Transaction-row mutation (audit-row migration) — read-time only.
- Capital reductions (`減資`), spin-offs, mergers — defer; require different ratio semantics.
- Automatic scheduler trigger — manual backfill only for this change; cron can be added later.

## Capabilities

### New Capabilities

- `stock-portfolio-corporate-actions`: persistence of TWSE face-value changes per symbol with idempotent backfill, list/query, and a cumulative split-factor helper.

### Modified Capabilities

- `stock-portfolio-data-integrity`: `PortfolioSummary` SHALL apply the cumulative split factor to historical transactions at read time when computing `total_quantity`, `avg_cost`, and unrealized PnL.

## Impact

- **Code**
  - `services/stock-portfolio-service/app/models/corporate_action.py` — NEW
  - `services/stock-portfolio-service/alembic/versions/i6d7e8f9a0b1_add_corporate_actions_table.py` — NEW
  - `services/stock-portfolio-service/alembic/env.py` — register model
  - `services/stock-portfolio-service/app/services/corporate_action_service.py` — NEW (fetcher + persistence + factor helper)
  - `services/stock-portfolio-service/app/services/portfolio_service.py` — apply split factor in aggregation paths
  - `services/stock-portfolio-service/app/routers/history.py` — add GET + POST corp-action endpoints
  - `services/stock-portfolio-service/app/main.py` — register model, no router change
  - `services/stock-portfolio-service/tests/unit/test_corporate_action_service.py` — NEW
  - `services/stock-portfolio-service/tests/unit/test_portfolio_service.py` — extend with split-applied scenarios
  - `frontend/src/app/models/portfolio.model.ts` — add `CorporateAction` interface
  - `frontend/src/app/services/portfolio.service.ts` — add `getCorporateActions()`
  - `frontend/src/app/components/portfolio/corporate-actions-panel/*` — NEW
  - `frontend/src/app/components/portfolio/dashboard/dashboard.{ts,html}` — embed panel

- **API (additive)**
  - `GET /api/portfolio/corporate-actions?symbol=&from=&to=`
  - `POST /api/portfolio/corporate-actions/backfill?year=`

- **Operational**
  - New table `corporate_actions`. No new env vars.

- **Risks**
  - Floating-point precision: ratios stored as `NUMERIC(18,8)`. Cumulative-factor math uses `Decimal` end-to-end.
  - Read-time adjustment runs on every `get_portfolio_summary` call; for accounts with many corp actions this adds a constant-factor cost. Acceptable until benchmarked otherwise.
  - Manual backfill window: callers must hit the endpoint per year for now; missing a year silently leaves holdings unadjusted for that span. Documented as a known limitation.
