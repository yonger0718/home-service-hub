## Why

Portfolio dashboard's networth chart only has data from the day the snapshot scheduler started running (post feature launch). Users with months/years of prior transactions see a flat or empty history. Need a one-shot, idempotent backfill that recreates historical `portfolio_snapshot` rows from existing transactions + dividends + freshly-pulled TWSE/TPEx daily close prices.

## What Changes

- New endpoint `POST /api/portfolio/history/backfill-networth` accepting `{from, to, phase}` where `phase ∈ {prices, snapshots, both}`.
- New service `app/services/networth_backfill_service.py` with two phase functions:
  - `backfill_prices_range(db, from, to, throttle_sec)`: per-trading-day driver around existing `market_data_service.backfill_date`. Adds weekend skip, empty-payload holiday probe, throttle gap, retry-with-backoff on transient failure, per-date error isolation.
  - `replay_snapshots_range(db, from, to)`: per-date pure-DB recomputation. Holdings-as-of from transactions + stock-dividends. Market value from `price_history.close`. Cost basis = cumulative buy − sell cost. `total_dividends` = cumulative `Dividend.amount` up to date. Upsert via `Session.merge` on `date` PK.
- `portfolio_xirr` left `NULL` on backfilled rows.
- No schema changes: `price_history` already has composite PK `(symbol, date)` and `transactions` already has index `ix_transactions_symbol_trade_date`.

## Capabilities

### New Capabilities
- `stock-portfolio-networth-backfill`: one-shot historical recompute of `portfolio_snapshot` from existing transactions/dividends + freshly-pulled `price_history`. Covers two phases (prices, snapshots), trading-calendar handling, rate-limit pacing, idempotency.

### Modified Capabilities
<!-- none -->

## Impact

- Affected code: `app/routers/history.py` (new endpoint), `app/services/market_data_service.py` (extract retry-with-backoff helper for backfill path only — daily-cron path unchanged), `app/services/portfolio_snapshot_service.py` (extract holdings-as-of logic if reusable, otherwise leave intact), new `app/services/networth_backfill_service.py`.
- Schema: no changes; relies on existing composite PK on `price_history(symbol, date)` and existing `ix_transactions_symbol_trade_date`.
- External calls: TWSE MI_INDEX + TPEx dailyQuotes per trading day in range. Throttled at 1.5 s gap (~40 calls/min). 5 y backfill ≈ ~2 500 calls, ~30 min wall-time. No new dependencies.
- Tests: new `tests/unit/test_networth_backfill_service.py` + router smoke test.
