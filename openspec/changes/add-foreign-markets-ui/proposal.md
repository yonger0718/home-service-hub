## Why

Phases 1 and 2 of the foreign-market rollout shipped end-to-end backend support for US and LSE holdings (schema, frozen-FX cost basis, yfinance daily OHLC, FX rate cron, market-aware quote dispatcher, hybrid-FX revaluation). `GET /api/portfolio/summary` already returns `market`, `native_close`, `native_currency`, and `live_fx_rate_to_twd` on every holding, and transaction CRUD already accepts `market` + `currency` + `fx_rate_to_twd`. The Angular dashboard is still TW-only — it keys holdings by `symbol` alone, hides every new field, and the transaction form has no way to record a foreign trade. Without Phase 3 the user cannot actually use the foreign-market features that already ship in the backend.

## What Changes

- Add market picker (`TW` / `US` / `LSE`) to the transaction create/edit form. Non-TW markets reveal `currency` + `fx_rate_to_twd` inputs; currency is auto-derived from market (`US`→`USD`, `LSE`→`GBP`) with manual override so LSE `GBp` tickers can be recorded.
- Holdings table on the dashboard gets new `Market`, `Native Price`, and `Native Currency` columns; existing TWD columns stay. Foreign rows show `native_close` verbatim with currency suffix (e.g. `8050.00 GBp`, `190.50 USD`); TWD columns continue to drive totals.
- Live FX rate (`live_fx_rate_to_twd`) is surfaced as a tooltip / info badge on the TWD market-value cell for foreign rows so the user can see which rate drove the revaluation.
- Dashboard groups holdings in collapsible per-market sections (TW / US / LSE) — TW section open by default, foreign sections rendered only when foreign rows exist.
- **BREAKING (frontend only)**: Holdings cache and lookup keys switch from `symbol` to composite `${symbol}|${market}` everywhere in `portfolio.service.ts` and downstream components, so a same-named symbol cannot collide across markets.
- Realized P&L list/page surfaces `market` + native amounts alongside the existing TWD figures.
- Vitest unit tests cover the new components and the composite-key contract; no backend changes.

## Capabilities

### New Capabilities
- `frontend-foreign-markets-display`: Market picker on the transaction form, multi-currency holding display rules (GBp verbatim with `/100` only in computed TWD column), per-market grouping on the dashboard, FX rate tooltip on foreign TWD figures.

### Modified Capabilities
- `frontend-portfolio-dashboard`: Holdings table gains market / native-price columns; rows are grouped by market section; cache and selection keys switch from `symbol` to `(symbol, market)` composite.
- `frontend-stock-transactions`: Transaction form accepts market + currency + `fx_rate_to_twd`; non-TW selection reveals the FX inputs and applies eligibility rules from the Phase 1 backend validator.

## Impact

- Code: `frontend/src/app/components/portfolio/**` (dashboard, holdings table, transaction form, realized-pnl list), `frontend/src/app/services/portfolio.service.ts`, `frontend/src/app/models/portfolio.model.ts`.
- APIs: consumed only; no backend signature changes. Phase 2 response fields (`market`, `native_close`, `native_currency`, `live_fx_rate_to_twd`) move from "ignored" to "rendered".
- Dependencies: existing Angular 21 + PrimeNG + Bootstrap 5 only — no new packages.
- Tests: Vitest unit tests for the new components and the composite-key contract; existing dashboard tests updated to reflect the new column shape.
- Risk: composite-key cutover is a frontend-only invariant change — any cached selection or chart that still keys on `symbol` will silently break, so every consumer of `PortfolioService` must be migrated in the same change.
