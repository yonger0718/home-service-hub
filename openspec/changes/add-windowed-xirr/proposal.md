## Why

The existing lifetime portfolio XIRR (annualized return) blows up to extreme values when the holding window is short. A -5% paper loss accumulated over 30 days currently annualizes to roughly -46%; -8% over the same window prints near -65%. Users seeing "-60.22%" assume the math is broken when it is mathematically correct but unhelpful: the annualization compounds a brief, small loss across a full year. Adding fixed-window XIRRs (1M / 3M / 1Y / YTD) lets the user compare returns over a stable denominator (opening market value) and across windows that match how people actually read fund performance, so the headline number stays sane while still being a true annualized rate.

## What Changes

- Add four new windowed XIRR fields at the portfolio level: `portfolio_xirr_1m`, `portfolio_xirr_3m`, `portfolio_xirr_1y`, `portfolio_xirr_ytd`.
- Add the same four fields at the per-stock holding level on `StockHolding`.
- Keep the existing lifetime `portfolio_xirr` field unchanged for backwards compatibility.
- Compute each windowed XIRR with cashflows scoped to `[window_start, today]`, plus an opening outflow equal to `-(market_value at window_start)` and a terminal inflow equal to `+(current market_value)`.
- Source the portfolio-level opening value from `portfolio_snapshot` (closest snapshot `<= window_start`) and the per-stock opening value from `price_history.close[window_start]` (nearest previous trading day fallback) times the replayed quantity-at-window-start.
- Return `null` for any window where the required snapshot or price-history row is missing; document `python -m app.services.networth_backfill_service --rebuild-all` as the way to fill the gap.
- Frontend: replace the single XIRR card and the per-stock "年化報酬率" expanded row value with a chip selector (`1M / 3M / 1Y / YTD / 全部`) that drives which field renders. Null values render as `—` with a tooltip pointing at the backfill CLI.

## Capabilities

### New Capabilities
- `stock-portfolio-xirr`: Annualized portfolio and per-stock return reporting (lifetime + fixed windows), including the opening-market-value sourcing rules, gap handling, and the API/UI contract for window selection.

### Modified Capabilities
<!-- none — the new fields are additive and the lifetime XIRR behavior is unchanged. -->

## Impact

- `services/stock-portfolio-service/app/services/portfolio_service.py` — new `_calculate_windowed_xirr` helper; `get_portfolio_summary` extended to compute four portfolio + four per-stock windowed values per call.
- `services/stock-portfolio-service/app/schemas/portfolio.py` — new optional Decimal fields on `PortfolioSummary` and `StockHolding`.
- `services/stock-portfolio-service/tests/unit/test_windowed_xirr.py` (new) + integration coverage in the existing summary-endpoint test.
- `frontend/src/app/components/portfolio/dashboard/dashboard.{ts,html,scss}` — XIRR card chip selector + per-holding expanded row.
- `frontend/src/app/models/portfolio.model.ts` — extend `PortfolioSummary` and `StockHolding` interfaces with the four new fields.
- Depends on existing `portfolio_snapshot`, `price_history`, and `networth_backfill_service` capabilities; no new tables, migrations, or external services.
- No breaking changes to existing endpoints or schemas — fields are additive and optional.
