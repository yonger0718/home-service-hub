## 1. Backend — windowed XIRR helper

- [x] 1.1 Add `_window_start(today: date, window: Literal["1m","3m","1y","ytd"]) -> date` helper in `services/stock-portfolio-service/app/services/portfolio_service.py`, using `dateutil.relativedelta` for month/year arithmetic and Jan 1 of the current year for YTD.
- [x] 1.2 Add `_calculate_windowed_xirr(window_start, today, cashflows, opening_mv, closing_mv) -> Optional[Decimal]` helper that filters `cashflows` to the inclusive `[window_start, today]` range, prepends the opening outflow when `opening_mv` is not `None` and `> 0`, appends the terminal inflow, and delegates to the existing `_calculate_xirr`.

## 2. Backend — portfolio-level windowed XIRR

- [x] 2.1 In `get_portfolio_summary`, after `all_cashflows` is built and `total_market_value` is computed, look up the closest `portfolio_snapshot` with `date <= window_start` for each of the four windows (single query returning the four matches).
- [x] 2.2 For each window, build the cashflow series via the helper from 1.2 and assign the result to `portfolio_xirr_1m / 3m / 1y / ytd`. When no snapshot exists the field stays `None`.
- [x] 2.3 Add the four new optional `Decimal` fields to `PortfolioSummary` in `services/stock-portfolio-service/app/schemas/portfolio.py` and pass them through in the `return schemas.PortfolioSummary(...)` call.

## 3. Backend — per-stock windowed XIRR

- [x] 3.1 Add a helper that, given the in-memory transaction list and a symbol, returns the net quantity at `window_start` by replaying BUY minus SELL up to (but excluding) `window_start`.
- [x] 3.2 Add a helper that, given a symbol and `window_start`, returns the `price_history.close` for `window_start` or the nearest previous trading-day row within 7 calendar days, else `None`.
- [x] 3.3 In the per-holding loop, compute opening market value per window. When `qty_at_window_start > 0` and the opening price lookup succeeds, pass `opening_mv = qty * close`. When `qty_at_window_start <= 0` (holding opened in window), pass `opening_mv = None` so the helper from 1.2 omits the opening outflow. When the price lookup fails, set the per-stock field to `None`.
- [x] 3.4 Add the four new optional `Decimal` fields (`xirr_1m`, `xirr_3m`, `xirr_1y`, `xirr_ytd`) to `StockHolding` in `services/stock-portfolio-service/app/schemas/portfolio.py` and pass them through in `holdings_list.append(schemas.StockHolding(...))`.

## 4. Backend tests

- [x] 4.1 Add `services/stock-portfolio-service/tests/unit/test_windowed_xirr.py` covering: (a) all four windows populated when snapshots + prices exist, (b) `null` per window when the corresponding snapshot or price row is missing, (c) per-stock fallback (no opening outflow) when the holding opens entirely inside the window, (d) inclusive edge-date cashflow inclusion, (e) 7-day previous-trading-day price lookup hits and misses.
- [x] 4.2 Extend the existing summary-endpoint integration test (or add a new one under `tests/integration/`) so a fixture portfolio with at least one snapshot and one `price_history` row per window asserts that the new fields are populated on `GET /api/portfolio/summary`.
- [x] 4.3 Run `pytest tests/unit/test_windowed_xirr.py tests/unit/test_xirr.py tests/unit/test_portfolio_service.py tests/integration/` from `services/stock-portfolio-service/` and confirm green.

## 5. Frontend — model + service

- [x] 5.1 Add `portfolio_xirr_1m / 3m / 1y / ytd` (all `number | null`) to the `PortfolioSummary` interface and `xirr_1m / 3m / 1y / ytd` to the `StockHolding` interface in `frontend/src/app/models/portfolio.model.ts`.

## 6. Frontend — dashboard UI

- [x] 6.1 In `frontend/src/app/components/portfolio/dashboard/dashboard.ts`, add a signal `xirrWindow = signal<'1m'|'3m'|'1y'|'ytd'|'all'>('1y')` and a helper that picks the correct field from a summary or holding based on the signal.
- [x] 6.2 In `dashboard.html`, replace the single XIRR card value with a chip selector (`1M / 3M / 1Y / YTD / 全部`) bound to that signal. Render the picked field via the existing `formatXirr` pipe; render `—` when the picked field is `null`.
- [x] 6.3 In the per-stock expanded detail row, replace the existing "年化報酬率" value with the same window-driven lookup; render `—` for `null`.
- [x] 6.4 Add a tooltip on the rendered `—` placeholders explaining the gap and pointing at `python -m app.services.networth_backfill_service --rebuild-all`. Reuse `pTooltip` (already present elsewhere in the dashboard).
- [x] 6.5 Style the chip selector in `dashboard.scss` to match existing chip patterns in the project (e.g., the realized-pnl year preset chips).

## 7. Frontend tests

- [x] 7.1 Add or extend `frontend/src/app/components/portfolio/dashboard/dashboard.spec.ts` so it asserts: (a) default chip is `1Y`, (b) switching the chip changes the rendered card value to the matching field, (c) `null` field renders `—` with the tooltip directive present.
- [x] 7.2 Run `cd frontend && npm test -- --watch=false` and confirm green.

## 8. Verification

- [x] 8.1 `openspec validate add-windowed-xirr --strict` passes from the repo root.
- [ ] 8.2 Manual: start the stack, load the dashboard, switch every chip, expand a holding, confirm the new values match an offline-computed XIRR for at least one window. Trigger a null case by querying a fresh DB with no snapshots and confirm `—` + tooltip render.
