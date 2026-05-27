## 1. Model + Migration

- [x] 1.1 `app/models/corporate_action.py` — `CorporateAction` with columns: `id` PK, `symbol` String(32) NN, `effective_date` Date NN, `action_type` String(32) NN default `FACE_VALUE_CHANGE`, `ratio` Numeric(18,8) NN check `> 0`, `source` String(32) NN default `TWSE`, `source_event_key` String(128) NN UNIQUE, `raw_payload` JSON nullable, `created_at` server-default; composite index `(symbol, effective_date)`
- [x] 1.2 Alembic revision `i6d7e8f9a0b1_add_corporate_actions_table` after `h5c6d7e8f9a0` with reversible downgrade
- [x] 1.3 Register in `alembic/env.py` and `app/main.py` (`from .models import corporate_action`)

## 2. Fetcher + Service

- [x] 2.1 `app/services/corporate_action_service.py`:
  - `parse_twtb8u(payload, year) -> list[CorporateActionRow]` — JSON parser; ROC date helper; ratio = `pre_close / post_ref`; skips rows missing/zero pre or post; produces `symbol`, `effective_date`, `ratio`, `source_event_key = f"{symbol}_{effective_date.isoformat()}"`, `raw_payload`
  - `fetch_year(year) -> list[CorporateActionRow]` — sync HTTP via existing `_http_get` pattern (or reuse `market_data_service._http_get`); upstream URL `https://www.twse.com.tw/rwd/zh/change/TWTB8U`
  - `upsert_rows(db, rows) -> int` — `Session.merge` keyed by `source_event_key`
  - `backfill_year(db, year) -> dict` — fetch + upsert; returns `{year, rows, written}`
  - `list_actions(db, *, symbol=None, from_date=None, to_date=None) -> list[CorporateAction]` ascending by `effective_date`
  - `get_split_factor(db, symbol, as_of) -> Decimal` — product of `ratio` for all actions on `symbol` with `effective_date <= as_of`; defaults to `Decimal(1)` when none
  - ROC date helper inline (no external dependency)

## 3. Read-time Adjustment in PortfolioService

- [x] 3.1 Extend `_aggregate_active_holdings` to accept an optional `split_factor_for(symbol, trade_date) -> Decimal` callable; when provided, multiply `quantity` and divide cost by the returned factor before accumulation
- [x] 3.2 Extend `get_portfolio_summary` to load all corp actions once, build a per-symbol sorted list, and pass a closure into the helper so each pre-event transaction's quantity is multiplied and price divided by the cumulative factor at its `trade_date`
- [x] 3.3 Ensure existing scenarios (no corp actions) produce byte-identical output to current behaviour

## 4. Endpoints

- [x] 4.1 `GET /api/portfolio/corporate-actions?symbol=&from=&to=` on `history.py` — all params optional; symbol filter exact; default no date filter
- [x] 4.2 `POST /api/portfolio/corporate-actions/backfill?year=YYYY` — manual trigger

## 5. Backend Tests

- [x] 5.1 `tests/unit/test_corporate_action_service.py` — TWTB8U parser happy path, ROC date handling, skip-on-missing-fields, ratio precision, upsert idempotency, factor computation (single action, multiple actions, before/after as_of), list filters
- [x] 5.2 Extend `tests/unit/test_portfolio_service.py` with: no-action baseline unchanged; single 1→10 split on a held symbol adjusts `total_quantity` and `avg_cost`; multiple compound actions on same symbol; action after most recent transaction has no effect
- [x] 5.3 Endpoint tests: GET list filters by symbol + date; POST backfill writes rows

## 6. Frontend Panel (delegated to codex)

- [x] 6.1 Add `CorporateAction` interface to `portfolio.model.ts`
- [x] 6.2 Add `getCorporateActions(symbol?, from?, to?)` to `PortfolioService`
- [x] 6.3 New standalone `corporate-actions-panel` component (PrimeNG `<p-card>` + `<p-table>`); shows date, symbol, ratio, action_type
- [x] 6.4 Embed on dashboard between networth-chart and holdings table

## 7. Verification

- [x] 7.1 Full `pytest` — 167 prior + new tests pass
- [x] 7.2 Alembic upgrade/downgrade clean
- [x] 7.3 `npm run build` clean
- [x] 7.4 Manual: POST backfill for a known year (e.g. one containing a 2330 face-value change if any), verify GET returns rows and dashboard panel renders
