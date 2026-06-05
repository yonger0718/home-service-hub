## Why

Portfolio service today is TWSE-only. Symbol uniqueness, price storage, and money columns all assume Taiwan market + TWD. Want to track US (NASDAQ/NYSE) and LSE holdings alongside existing TW data. Phase 1 lays the schema groundwork — adds market, currency, and frozen FX columns so later phases (quote fetcher, UI) can layer on without re-migrating production data.

Schema-only because production already has ~years of TW data; widening before introducing new code paths is safer than coupling migration to feature work.

## What Changes

- Add `market VARCHAR(8) NOT NULL DEFAULT 'TW'` to `transactions`, `dividends`, `price_history`, `corporate_actions` (semantics: top-level market — `TW`, `US`, `LSE`)
- **BREAKING** Rename existing `symbol_map.market` (TWSE/TPEx sub-exchange semantics) → `symbol_map.exchange`; then add new `symbol_map.market VARCHAR(8) NOT NULL DEFAULT 'TW'` with TW/US/LSE semantics
- Add `currency CHAR(3) NOT NULL DEFAULT 'TWD'` to `transactions`, `dividends`
- Add `fx_rate_to_twd NUMERIC(20,8) NULL` to `transactions`, `dividends` — frozen FX rate at trade / ex-date for hybrid cost-basis (NULL when currency='TWD')
- **BREAKING** Bump `transactions.price` `Numeric(12,2)` → `Numeric(18,4)` (US cents, LSE GBp sub-pence)
- **BREAKING** Bump `transactions.quantity` `Integer` → `Numeric(18,4)` (US fractional shares, DRIP reinvest)
- **BREAKING** Bump `dividends.amount` `Numeric(12,2)` → `Numeric(18,4)`
- **BREAKING** Refresh PK on `price_history`: `(symbol, date)` → `(symbol, market, date)`
- Refresh index on `transactions`: `ix_transactions_symbol_trade_date` → `ix_transactions_symbol_market_trade_date`
- Realized P&L engine (`iter_realized_events`): if `fx_rate_to_twd IS NOT NULL`, multiply native price by frozen rate before existing TWD math. TW rows (`fx_rate_to_twd IS NULL`) hit unchanged branch.
- Symbol lookups gain optional `market` filter, default `'TW'`. All existing call sites keep working.
- Day-trade detection: gate on `market == 'TW'`; foreign markets always `is_day_trade=False`.
- Cathay broker import path explicitly stamps `market='TW'`, `currency='TWD'`, `fx_rate_to_twd=NULL` (was implicit).

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `stock-portfolio-realized-pnl`: cost-basis math gains a "if frozen FX present, use it" branch; new fields persisted on transactions and dividends.
- `stock-portfolio-broker-cathay-import`: Cathay path must explicitly populate `market='TW'`, `currency='TWD'`, `fx_rate_to_twd=NULL` on inserted rows.
- `stock-portfolio-day-trade-detection`: detector returns False whenever `market != 'TW'`.
- `stock-portfolio-price-history`: PK widens to include `market`; readers/writers must pass market alongside symbol.
- `stock-portfolio-symbol-resolver`: lookups become `(symbol, market)`-scoped with `'TW'` default for back-compat.

## Impact

- **Migrations**: 1 alembic revision; touches 5 tables. Numeric widening on `price`/`amount` is metadata-only in PostgreSQL; `quantity Integer → Numeric` rewrites the `transactions` table (~50k rows in prod — acceptable).
- **Models**: `app/models/portfolio.py`, `app/models/price_history.py`, `app/models/symbol_map.py`, `app/models/corporate_action.py` gain new columns.
- **Schemas**: Pydantic schemas in `app/schemas/` accept new optional fields with TW/TWD defaults.
- **Services touched**: `app/services/realized_pnl_service.py` (FX branch in iterator), `app/services/broker_cathay_service.py` (explicit market/currency), `app/services/portfolio_service.py` (default market on symbol queries), `app/services/day_trade_detection_service.py` (market gate), `app/services/price_history_service.py` (read/write paths gain market arg).
- **Tests**: existing suite passes unchanged (defaults preserve TW behavior). New unit tests cover realized P&L with non-null `fx_rate_to_twd` and day-trade detection rejecting non-TW markets.
- **No new behavior** in this phase: no yfinance fetcher, no quote dispatcher, no scheduler additions, no frontend changes, no foreign-broker importer (all deferred to later phases).
- **fx_rate table** untouched (already exists from cash work, just sits empty until Phase 2 backfills foreign rates).
- **Cash subsystem** untouched (`broker_account` + `cash_transaction` already multi-currency).
