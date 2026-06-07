# Stock Portfolio Service

FastAPI service for a Taiwan-equities portfolio with broker CSV imports, on-demand TWSE/TPEx pricing, ex-dividend tracking, day-trade detection, corporate-action adjustments, and a daily net-worth time series.

## Endpoints

All routes are prefixed `/api/portfolio` (Angular dev proxies `/api/portfolio` here).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/transactions`, `/dividends`, `/summary`, `/holdings`, `/realized-pnl` | core portfolio reads |
| `POST` | `/imports/transactions`, `/imports/dividends` | CSV upload (multipart, `dry_run=true` previews) |
| `POST` | `/imports/recalc`, `/imports/refresh-quotes`, `/imports/verify-symbol` | post-import reconciliation |
| `GET`  | `/imports/recalc/status` | recalc progress |
| `GET`  | `/ex-dividends/upcoming` | TWT48U upcoming cash dividend feed |
| `GET`  | `/dividend-events?year=YYYY` | merged TWSE TWT48U + TWT49U + TPEx OTC dividend rows |
| `GET`  | `/price-history?symbol=&from=&to=` | per-symbol daily OHLC |
| `POST` | `/price-history/backfill?date=YYYY-MM-DD&market=TWSE\|TPEX\|BOTH` | one-day market backfill |
| `GET`  | `/history?from=&to=&interval=day\|week\|month` | networth time series |
| `POST` | `/history/snapshot`, `/history/backfill-networth` | snapshot management |
| `GET`  | `/corporate-actions?symbol=&from=&to=` | face-value-change rows |
| `POST` | `/corporate-actions/backfill?year=YYYY` | scrape TWSE TWTB8U and persist for one year |
| `GET`  | `/symbol-map`, `POST` `/symbol-map/refresh`, `POST` `/symbol-map/backfill` | name→ticker map |

## Scheduler

In-process APScheduler (`BackgroundScheduler`, `Asia/Taipei`) boots on FastAPI startup when `SCHEDULER_ENABLED` is truthy. Phase 2 foreign-market jobs use `yfinance` for Yahoo Finance quote/FX snapshots; no Yahoo API key is required, but outbound network access to Yahoo Finance must be available.

| Job ID | Cron | Action |
|---|---|---|
| `tw_daily_prices` | `17:00 mon-fri` | `market_data_service.backfill_date(today, market="BOTH")` |
| `quote_refresh` | `*/15 9-13 mon-fri` (gated by `is_tw_market_session`) | refresh quotes for active holdings |
| `portfolio_snapshot` | `15:30 mon-fri` | write today's networth snapshot row |
| `fx_rate_refresh` | `17:00 daily` | fetch USD/TWD and GBP/TWD into `fx_rates` via yfinance |
| `foreign_price_refresh` | `17:30 daily` | refresh yfinance OHLC for open non-TW holdings |

Env toggle: `SCHEDULER_ENABLED=false` skips boot entirely (used in tests + CI).

## Structured logging

`structlog` emits one JSON object per record by default:

```
{"event": "scheduler.started", "level": "info", "logger": "app.services.scheduler", ...}
```

Switch the renderer for local dev:

```bash
LOG_FORMAT=console uvicorn app.main:app --port 8001
```

`LOG_FORMAT=json` (default) ships structured records to Loki via the OTel Collector; `LOG_FORMAT=console` prints human-readable lines. Stdlib `logging.getLogger(__name__)` callers are bridged automatically — no migration required.

## Day-trade detection

`is_day_trade` is auto-derived on `create_transaction` / `update_transaction` / `delete_transaction`. A buy + sell of the same symbol on the same TW calendar date flips both legs to `is_day_trade=true`, gated by `is_day_trade_eligible` (warrants + 牛熊證 rejected via per-row `instrument_type` snapshot or live `symbol_map` fallback).

## Corporate-action adjustments

`get_portfolio_summary` retroactively divides historical `quantity` × multiplies historical `price` by accumulated face-value-change factors so cost-basis math survives splits/reverse-splits without touching transaction rows. Backfill via `POST /corporate-actions/backfill?year=YYYY` (TWSE TWTB8U scraper).

## Networth backfill CLI

After deploying realized-PnL replay fixes, rebuild stale snapshot rows explicitly:

```bash
python -m app.services.networth_backfill_service --rebuild-all --dry-run
python -m app.services.networth_backfill_service --rebuild-all
```

Dry-run prints per-date realized-PnL diffs and does not write rows.

## Name-map refresh

Regenerate the bundled broker name map (used by CSV import to resolve Chinese names → tickers):

```bash
python scripts/refresh_name_to_symbol.py
```

Rewrites `app/data/name_to_symbol.json` by enumerating `twstock.codes` (TWSE listed + TPEx OTC + ETFs + ETNs + active warrants). Only prerequisite is an up-to-date `twstock` dependency.

## Post-import recalc optimization

Post-import networth recalculation derives the weekdays where the portfolio actually had exposure and limits both historical price fetches and snapshot replay to those active dates. A fully closed historical position no longer causes the service to walk every later weekday in the requested range. In recalc status, `dates_inactive` counts weekdays skipped because no symbol was held; it is separate from `dates_skipped`, which means a fetched date where both markets were empty (e.g. a holiday).

Snapshot replay keeps Phase 1 price fetches weekday-only, but forward-fills weekends and full-market holidays inside held intervals with the previous trading day's market value and cost so historical charts stay dense through long closures. When replay skips a date instead of writing a row, it also self-heals legacy stale snapshots matching `total_market_value = 0` and `total_cost > 0`; recalc status exposes the cleanup count as `stale_rows_deleted`.

## Broker cash accounts rollout

`broker_account` + `cash_transaction` ledger is gated behind `CASH_LEG_ENABLED` (default `false`). Cathay CSV imports and `create_transaction` / `update_transaction` / `delete_transaction` only emit cash legs when the flag is on. Rollout sequence:

1. **Deploy with flag off.** Migrations run idempotently; existing flows untouched. Use `GET /api/portfolio/accounts` to confirm the empty endpoint responds.
2. **Seed the Cathay account.** `POST /api/portfolio/accounts` with `{broker: "TW_CATHAY", currency: "TWD", opening_balance: "<seed>"}` then dry-run the backfill: `python -m app.services.cash_backfill_service --dry-run`. The dry run prints the per-source counts (`csv_import` from existing Cathay transactions/dividends, `auto_derive` from manual rows) without writing.
3. **Commit the backfill.** Re-run without `--dry-run`. Idempotency is enforced via `import_fingerprint` on both transactions and dividends, so reruns are safe.
4. **Flip the flag.** Set `CASH_LEG_ENABLED=true` in the service env and restart. New manual transactions and CSV imports now write linked cash rows.
5. **Onboard the rest.** Repeat step 2 for SinoPac / Firstrade / IB / Schwab accounts (one row per broker × currency). Phase 4 broker importers already emit `broker_cash_flows`; this rollout layer folds them into the unified `cash_transaction` ledger.

Frontend `/portfolio/accounts` page renders the resulting balances (native + TWD-converted), a typed cash transaction list with filters, a manual deposit/withdraw form, and a balance-over-time chart. The 合併同筆交易 toggle on the detail page collapses settle/fee/tax legs into a single row per trade (per-account `localStorage` persisted). Manual rows can be deleted with the trash icon — `auto_derive` and `csv_import` rows are locked.
