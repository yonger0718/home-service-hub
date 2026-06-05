## Why

Phase 1 (PR #25, merged) added schema room for foreign-market holdings (`market`, `currency`, `fx_rate_to_twd` on transactions/dividends; market-scoped `price_history`). The portfolio service can now accept US and LSE rows, but it has no way to fetch their daily quotes or convert their live market value back to TWD on the dashboard. Phase 2 wires yfinance as the foreign quote source and adds a daily FX-rate cron so foreign holdings revalue end-to-end without touching the existing TWSE/TPEx path or the frozen cost-basis math.

## What Changes

- **NEW** quote dispatcher `app/services/quotes/dispatcher.py` routing `(symbol, market)` → fetcher (TW → existing TWSE/TPEx path; US/LSE → yfinance). Existing call sites that pass bare `symbol` default to `market='TW'` and behave bit-identically.
- **NEW** yfinance fetcher `app/services/quotes/yfinance_fetcher.py` — batched per market, suffix-aware (`.L` for LSE), trusts yfinance `meta.currency` per ticker (handles GBp vs GBP vs USD), skip+warn on per-ticker fail.
- **NEW** FX-rate service `app/services/quotes/fx_rate_service.py` — fetches `USDTWD=X` + `GBPTWD=X` via yfinance, persists ISO-base (`USD`/`GBP`) only; `GBp = GBP/100` derived on read.
- **NEW** `fx_rates` table `(currency, date, rate_to_twd, source)` — PK `(currency, date)`.
- **NEW** scheduler jobs `fx_rate_refresh` (17:00 TW daily) and `foreign_price_refresh` (17:30 TW daily), both gated by `SCHEDULER_ENABLED=true`.
- **MODIFIED** `portfolio_service.get_portfolio_summary` — foreign holdings revalue via `qty × native_close × live_fx_rate_to_twd`; GBp divides native close by 100 first. Cost basis stays frozen (no change to realized PnL math).
- **MODIFIED** `StockHolding` response schema — adds live `fx_rate` and native-currency fields so UI (Phase 3) can show per-currency breakdown.
- **NEW** dependency `yfinance` in `requirements.txt`.

Out of scope: foreign broker CSV import, frontend market picker, real-time/WebSocket quotes, historical foreign-price backfill before deploy date.

## Capabilities

### New Capabilities
- `stock-portfolio-fx-rates`: daily FX-rate snapshot table, yfinance fetcher, scheduled cron, and read API used by market-value revaluation
- `stock-portfolio-quote-dispatch`: market-aware quote-fetcher router with TW (TWSE/TPEx) and foreign (yfinance) backends

### Modified Capabilities
- `stock-portfolio-price-history`: extends fetcher contract to accept `market` parameter and route to yfinance for non-TW; documents `yfinance` as new valid `source` value
- `stock-portfolio-realized-pnl`: extends `PortfolioSummary` / `StockHolding` contract to expose per-currency live FX rate and to revalue foreign positions at live FX (cost basis stays frozen — no change to realized math)

## Impact

- **Code**: new `app/services/quotes/` package; `app/services/portfolio_service.py` summary path; `app/services/scheduler.py` job registration; `app/schemas/portfolio.py` holding response shape.
- **Dependencies**: adds `yfinance` (Python). Network: outbound to Yahoo Finance only (no Yahoo API key required).
- **DB**: one new alembic revision creating `fx_rates` (no backfill — forward-fill from deploy date).
- **Operational**: two daily cron jobs; failure mode is skip+warn (foreign holdings show last-known close, cost-basis-only summary if FX missing).
- **No frontend changes** in this phase (Phase 3).
- **No change** to TW behavior, realized PnL math, or historical snapshots.
