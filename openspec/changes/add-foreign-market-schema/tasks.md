## 1. Alembic Migration

- [x] 1.1 Generate new revision: `alembic revision -m "add foreign market schema"` under `services/stock-portfolio-service/alembic/versions/`
- [x] 1.2 In `upgrade()`: add `market VARCHAR(8) NOT NULL DEFAULT 'TW'` to `transactions`, `dividends`, `price_history`, `corporate_actions`
- [x] 1.3 In `upgrade()`: rename `symbol_map.market` → `symbol_map.exchange`, then add new `symbol_map.market VARCHAR(8) NOT NULL DEFAULT 'TW'`
- [x] 1.4 In `upgrade()`: add `currency CHAR(3) NOT NULL DEFAULT 'TWD'` and `fx_rate_to_twd NUMERIC(20,8) NULL` to `transactions` and `dividends`
- [x] 1.5 In `upgrade()`: widen `transactions.price` and `dividends.amount` from `Numeric(12,2)` → `Numeric(18,4)` (metadata-only)
- [x] 1.6 In `upgrade()`: widen `transactions.quantity` from `Integer` → `Numeric(18,4)` (table rewrite — note in revision docstring)
- [x] 1.7 In `upgrade()`: drop `pk_price_history`, recreate as `PRIMARY KEY (symbol, market, date)`
- [x] 1.8 In `upgrade()`: drop `ix_transactions_symbol_trade_date`, create `ix_transactions_symbol_market_trade_date` on `(symbol, market, trade_date)`
- [x] 1.9 In `downgrade()`: reverse every step above; document one-way risk once foreign rows exist
- [x] 1.10 Verify `alembic upgrade head` + `alembic downgrade -1` round-trip clean on a fresh local DB (exact-revision round-trip passed live PG: `upgrade y2n3o4p5q6r7` → `downgrade x1m2n3o4p5q6` → `upgrade y2n3o4p5q6r7`; generic `upgrade head` blocked by pre-existing duplicate revision `m0b1c2d3e4f5` baseline issue, unrelated to this change)

## 2. ORM Models

- [x] 2.1 `app/models/portfolio.py` — `Transaction`: add `market`, `currency`, `fx_rate_to_twd`; widen `price` to `Numeric(18, 4)`; widen `quantity` to `Numeric(18, 4)`
- [x] 2.2 `app/models/portfolio.py` — `Dividend`: add `market`, `currency`, `fx_rate_to_twd`; widen `amount` to `Numeric(18, 4)`
- [x] 2.3 `app/models/price_history.py` — add `market` column; update `PrimaryKeyConstraint` to `(symbol, market, date)`
- [x] 2.4 `app/models/corporate_action.py` — add `market` column
- [x] 2.5 `app/models/symbol_map.py` — rename `market` → `exchange`, add new `market` column with TW default

## 3. Pydantic Schemas

- [x] 3.1 `app/schemas/portfolio.py` — `TransactionCreate` / `TransactionUpdate` / `TransactionResponse`: add optional `market` (default `'TW'`), `currency` (default `'TWD'`), `fx_rate_to_twd` (default `None`); widen `price` / `quantity` types to `Decimal`
- [x] 3.2 `app/schemas/portfolio.py` — `DividendCreate` / `DividendUpdate` / `DividendResponse`: same field additions; widen `amount` to `Decimal(18, 4)` constraint

## 4. Service-Layer Branches

- [x] 4.1 `app/services/realized_pnl_service.py` — `iter_realized_events`: add `_to_twd_per_share(row)` helper applying `row.price * row.fx_rate_to_twd` when frozen rate present; raise `ValueError` if `currency != 'TWD'` AND `fx_rate_to_twd IS NULL`
- [x] 4.2 `app/services/realized_pnl_service.py` — verify TWD-only fixtures produce bit-identical output (no regression on TW data)
- [x] 4.3 `app/services/day_trade_detection_service.py` — gate bucket evaluation on `market='TW'`; non-TW rows always emit `is_day_trade=False`
- [x] 4.4 `app/services/broker_cathay_service.py` — explicitly pass `market='TW'`, `currency='TWD'`, `fx_rate_to_twd=None` to every `Transaction(...)` constructor call
- [x] 4.5 `app/services/symbol_resolver_service.py` (and module that hosts `resolve_name`) — add `market: str = 'TW'` keyword; filter `SymbolMap.market == market`
- [x] 4.6 `app/services/symbol_map_service.py` — `refresh_all_from_twstock` writes `market='TW'`, `exchange=<TWSE|TPEx>` (instead of writing TWSE/TPEx to `market`)
- [x] 4.7 `app/services/market_data_service.py` / `app/services/price_history_service.py` — read/write paths pass `market='TW'` explicitly when querying or writing `price_history` for TW data; query API param `market` (TWSE/TPEX/BOTH) remains TW-sub-exchange filter — document distinction inline

## 5. Tests

- [x] 5.1 Add unit test: realized PnL on TWD-only fixture returns same output as pre-migration (lock-in regression test)
- [x] 5.2 Add unit test: realized PnL on USD BUY (`fx=32.0`) + USD SELL (`fx=33.0`) computes correct TWD cost / proceeds / PnL per spec scenario
- [x] 5.3 Add unit test: realized PnL raises `ValueError` when `currency != 'TWD'` and `fx_rate_to_twd is None`
- [x] 5.4 Add unit test: dividend in USD converts via frozen rate into `total_dividends`
- [x] 5.5 Add unit test: day-trade detector returns False for US same-day BUY+SELL
- [x] 5.6 Add unit test: day-trade detector does not cross-pair TW row with US row sharing the same string symbol
- [x] 5.7 Add unit test: `resolve_name(db, name)` defaults to `market='TW'`; explicit `market='US'` filters correctly
- [x] 5.8 Add unit test: Cathay importer persists `market='TW'`, `currency='TWD'`, `fx_rate_to_twd=None` on a sample CSV row
- [x] 5.9 Add integration test: `Transaction(price=Decimal('234.5678'), quantity=Decimal('0.5'))` round-trips through DB without precision loss
- [x] 5.10 Run existing `pytest tests/unit/ tests/integration/` — every prior test must pass unchanged

## 6. Verification

- [ ] 6.1 Run `python -m app.services.networth_backfill_service --rebuild-all --dry-run`; compare snapshot output to pre-migration row totals (must match)
- [x] 6.2 Run `pytest` end-to-end, confirm no regressions in realized PnL, snapshot, day-trade, broker import, symbol resolver
- [ ] 6.3 Manual smoke: insert a TW transaction via API with no market/currency fields → row appears as TW/TWD/NULL
- [ ] 6.4 Manual smoke: insert a foreign transaction via direct SQL `(market='US', currency='USD', fx_rate_to_twd=32.5)` → realized PnL endpoint computes correct TWD value when paired with matching SELL

## 7. Documentation

- [ ] 7.1 Update `services/stock-portfolio-service/README.md` (or equivalent) noting Phase 1 schema readiness; Phase 2 (quote fetcher) and Phase 3 (UI) are queued
- [x] 7.2 Inline comment on `symbol_map.exchange` documenting the rename rationale for future readers
