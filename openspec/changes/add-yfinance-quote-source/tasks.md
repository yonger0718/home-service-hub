## 1. Dependencies + Migration

- [x] 1.1 Add `yfinance` to `services/stock-portfolio-service/requirements.txt` (pin a known-working version; verify import succeeds in the service venv)
- [x] 1.2 Add `currency VARCHAR(8) NOT NULL DEFAULT 'TWD'` column to `price_history` model `app/models/price_history.py`
- [x] 1.3 Create new model `app/models/fx_rate.py` for `fx_rates` table: `currency CHAR(3) PK`, `date DATE PK`, `rate_to_twd NUMERIC(20,8) NOT NULL` (CHECK `> 0`), `source VARCHAR(16) NOT NULL DEFAULT 'yfinance'`, `created_at TIMESTAMPTZ`
- [x] 1.4 New alembic revision: `upgrade()` creates `fx_rates`, adds `price_history.currency` with server default `'TWD'`, backfills existing TW rows to `'TWD'`
- [x] 1.5 `downgrade()` drops `fx_rates`, drops `price_history.currency`
- [x] 1.6 Verify `alembic upgrade head` + `alembic downgrade -1` round-trip clean on a fresh local DB

## 2. FX Rate Service

- [x] 2.1 Create `app/services/quotes/__init__.py` package
- [x] 2.2 Implement `app/services/quotes/fx_rate_service.py`:
  - `_SUPPORTED_CURRENCIES = ('USD', 'GBP')`
  - `_YF_TICKER_MAP = {'USD': 'USDTWD=X', 'GBP': 'GBPTWD=X'}`
  - `RefreshResult` dataclass `(ok_count, skipped_count, errors)`
  - `refresh_today(db) -> RefreshResult` — fetches each ticker via yfinance, upserts `fx_rates` row for today (Asia/Taipei), reject `currency='GBp'` writes, skip+warn per ticker
  - `get_rate(db, currency: str, as_of: date) -> Decimal | None` — `MAX(date) <= as_of` lookup; `GBp` → `GBP / 100`
- [x] 2.3 Unit tests `tests/unit/test_fx_rate_service.py`:
  - successful refresh writes USD + GBP rows
  - idempotent rerun overwrites today's rows
  - partial failure (one ticker raises) keeps ok ticker, skips bad one
  - `get_rate('GBp', d)` divides GBP row by 100
  - `get_rate('USD', d)` returns latest on-or-before
  - `get_rate` returns `None` when no row exists before date
  - rejecting `GBp` write at upsert (constraint or service-layer)

## 3. yfinance Fetcher + Dispatcher

- [x] 3.1 Implement `app/services/quotes/yfinance_fetcher.py`:
  - `_SYMBOL_SUFFIX = {'US': '', 'LSE': '.L'}`
  - `QuoteRow` dataclass `(symbol, market, date, open, high, low, close, volume, currency)`
  - `fetch(items: list[tuple[str, str]]) -> tuple[list[QuoteRow], list[str]]` — batched per market, validates `meta.currency` + numeric price, skip+warn on per-ticker fail
  - `refresh_daily_ohlc(db, items)` upserts to `price_history` with `source='yfinance'` and persisted `currency` from `meta.currency`
- [x] 3.2 Implement `app/services/quotes/dispatcher.py`:
  - `_BACKENDS = {'TW': twse_backend, 'US': yfinance_backend, 'LSE': yfinance_backend}`
  - `refresh_daily_ohlc(db, items)` — groups by market, dispatches, aggregates `RefreshResult`
  - `get_quotes(db, items)` — same dispatch pattern; TW route calls existing `twse_service.get_stock_quotes`
  - unknown market → counted in `skipped_count` + `errors`
- [x] 3.3 Unit tests `tests/unit/test_quote_dispatcher.py`:
  - mixed batch (TW + US) dispatches to both backends
  - TW-only batch never calls yfinance backend
  - foreign-only batch never calls TWSE backend
  - unknown market (e.g. `JP`) is skipped + reported
  - bare-symbol legacy call path defaults to TW
- [x] 3.4 Unit tests `tests/unit/test_yfinance_fetcher.py` (mocked yfinance):
  - US ticker fetched without suffix
  - LSE ticker fetched with `.L` suffix
  - GBp ticker stores `currency='GBp'` and native pence price
  - missing `meta.currency` → ticker skipped, no row written
  - missing `regularMarketPrice` → ticker skipped
  - one bad ticker does not abort batch siblings

## 4. Scheduler Wiring

- [x] 4.1 Extend `app/services/scheduler.py`:
  - register `fx_rate_refresh` job — cron `hour=17, minute=0, timezone='Asia/Taipei'`, gated by `SCHEDULER_ENABLED`
  - register `foreign_price_refresh` job — cron `hour=17, minute=30, timezone='Asia/Taipei'`, gated by `SCHEDULER_ENABLED`
  - foreign job queries distinct `(symbol, market)` with open net qty where `market != 'TW'`, then calls `dispatcher.refresh_daily_ohlc`
  - log `event=fx_rate_refresh.{started,finished,failed}` and `event=foreign_price_refresh.{started,finished,failed}` with summary
- [x] 4.2 Unit tests `tests/unit/test_scheduler_foreign_jobs.py`:
  - both jobs registered when `SCHEDULER_ENABLED=true`
  - both jobs absent when `SCHEDULER_ENABLED=false`
  - `foreign_price_refresh` selects only open non-TW positions (closed + TW filtered out)
  - empty foreign ledger short-circuits without dispatcher call
  - job failure logs and does not raise

## 5. Read-Path Revaluation

- [x] 5.1 Add `native_close: Decimal | None`, `native_currency: str | None`, `live_fx_rate_to_twd: Decimal | None` to `StockHolding` schema in `app/schemas/portfolio.py`
- [x] 5.2 Add helper `_revalue_foreign_holding(db, holding)` in `app/services/portfolio_service.py`:
  - latest `price_history` row for `(symbol, market)` → `native_close`, `currency`
  - GBp → divide by 100; base currency → `USD`/`GBP`
  - `live_fx = fx_rate_service.get_rate(db, base, today)`
  - returns `market_value_twd` + the three response fields
- [x] 5.3 Modify `get_portfolio_summary` to dispatch foreign holdings through `_revalue_foreign_holding`; keep existing TW path untouched (no behavior diff for TW-only ledgers)
- [x] 5.4 If `live_fx is None` OR `native_close is None` for a foreign holding, `market_value_twd=None` and `summary.quotes_status` reflects `partial`/`unavailable`
- [x] 5.5 Unit tests `tests/unit/test_portfolio_summary_foreign_revalue.py`:
  - US USD holding revalues at live FX (frozen cost, live mv)
  - LSE GBp divides by 100 then applies GBP rate
  - LSE USD-quoted holding uses USD rate (no GBp branch)
  - missing FX row → `market_value_twd=None`, status `partial`
  - missing price row → same partial behavior
  - TW-only portfolio: bit-equal `PortfolioSummary` before/after change (regression guard)

## 6. Integration + Verification

- [x] 6.1 Integration test `tests/integration/test_fx_rates_endpoint.py` — write USD + GBP rows, call `get_rate` for various dates and currencies; verify GBp divide-by-100
- [x] 6.2 Integration test `tests/integration/test_yfinance_live_fetch.py` marked `@pytest.mark.live` (skipped by default) — fetches `AAPL`, `VOD.L`, `MSFT` against real yfinance, asserts shape
- [x] 6.3 Manual smoke: run service, trigger `fx_rate_service.refresh_today` once, verify `fx_rates` populated; trigger `foreign_price_refresh` once with a stub open US position, verify `price_history` populated with `source='yfinance'`
- [x] 6.4 Full unit suite: `pytest tests/unit/ -x` — verify Phase 1 tests still pass (624 baseline + new tests)
- [x] 6.5 Full integration suite: `pytest tests/integration/ -x` — Phase 1 integration tests still pass
- [x] 6.6 Backfill dry-run: `python -m app.services.networth_backfill_service --rebuild-all --dry-run` — verify 0 non-zero deltas (this change must not touch historical snapshot precision)

## 7. Docs

- [x] 7.1 Update service README with `SCHEDULER_ENABLED` env var, the two new cron jobs, and yfinance dependency note
- [x] 7.2 Note in CLAUDE.md that foreign holdings revalue at live FX while cost basis stays frozen (Phase 2 hybrid FX model)
