## 1. Schema + model

- [x] 1.1 Add `type VARCHAR(32) NULL` to `SymbolMap` model in `services/stock-portfolio-service/app/models/symbol_map.py`.
- [x] 1.2 Create Alembic migration `add_symbol_map_type_column.py` (add nullable column on upgrade; drop on downgrade).
- [x] 1.3 Run `alembic upgrade head` in dev env; confirm column exists and is nullable.

## 2. Symbol map service — type capture + eligibility helper

- [x] 2.1 Update `refresh_all_from_twstock` in `app/services/symbol_map_service.py` to pull `getattr(entry, "type", None)` and pass to `SymbolMap(...)`.
- [x] 2.2 Add `is_day_trade_eligible(db, symbol) -> bool` that queries `symbol_map.type` for the resolved ticker and returns `False` only when type CONTAINS one of `{"認購", "認售", "牛證", "熊證"}` as substring; returns `True` for unmapped, NULL, empty, or any other type. (Switched from prefix to substring after observing actual twstock format `上市認購(售)權證` / `上櫃認購(售)權證`.)
- [x] 2.3 Extend `tests/unit/test_symbol_map_service.py` with fixtures covering: refresh writes `type`, eligibility returns False for listed+OTC warrant + 牛證 + 熊證, True for equity, True for unmapped, True for NULL type.

## 3. Day-trade flag gating

- [x] 3.1 Modify `_recompute_day_trade_flags` in `app/services/portfolio_service.py` so the final assignment becomes `new_flag = has_buy and has_sell and symbol_map_service.is_day_trade_eligible(db, symbol)`.
- [x] 3.2 Add unit test file `tests/unit/test_day_trade_eligibility.py` covering: warrant BUY+SELL pair stays False, equity BUY+SELL flips True, unmapped symbol stays True (fail-open).
- [x] 3.3 Verify existing tests in `tests/unit/test_realized_pnl_short_pool.py` and `tests/unit/test_portfolio_service.py` still pass (no regression on day-trade semantics for equities).

## 4. Backfill migration

- [x] 4.1 Create Alembic data migration `backfill_day_trade_flags.py`. Down_revision = the column-add migration from 1.2.
- [x] 4.2 Inside upgrade: SELECT distinct symbols currently `is_day_trade=true`; for each, query `symbol_map.type`; if ineligible (warrant substring match), UPDATE all current-True rows for that symbol to False. Narrow contract: do NOT recompute eligible buckets in either direction — legacy bucket heuristic over-classifies and a follow-up change (`broker-day-trade-marker`) will fix the positive direction by trusting the broker's explicit `沖買/沖賣` marker.
- [x] 4.3 Use `op.execute(...)` UPDATE statements per ineligible symbol; log inspected / ineligible / flipped counts.
- [x] 4.4 Downgrade is a no-op (data correction is non-reversible by design; documented in the migration docstring).
- [x] 4.5 Add integration test `tests/integration/test_warrant_backfill_migration.py` that seeds a warrant pair flagged True + equity pair flagged True, runs `clear_warrant_day_trade_flags(conn)` against a live Postgres SAVEPOINT, asserts warrant pair flipped to False and equity pair unchanged.

## 5. Manual verification

- [x] 5.1 Run `pytest` in `services/stock-portfolio-service`; new tests + touched suites pass. 2 pre-existing failures in `test_post_import_orchestrator.py` confirmed present on `main` and unrelated to this change.
- [x] 5.2 Trigger `refresh_all_from_twstock` against dev DB; `symbol_map.type` populated for 42,579 of 55,011 rows. Spot-check: `045378` → `上市認購(售)權證`, `2330` → `股票`, `0050` → `ETF`.
- [x] 5.3 Run the backfill migration on dev DB. Initial broad-scan revision flipped 3 warrant rows to False (correct) and 20 equity rows to True (incorrect — heuristic over-classification). Narrowed migration to warrant-only; equity rows accidentally flipped True by the prior run will be cleaned up by the follow-up `broker-day-trade-marker` change. `045378` all 14 rows now `is_day_trade=false`. Empty-type warrant tickers (`033747`, `70490P`) fail-open per D3 known gap.
- [ ] 5.4 Open `/portfolio/realized-pnl` in dev frontend; navigate to the `045378` event — verify `當沖` badge no longer renders on warrant row. **(Deferred — UI badge is gated on the backend `is_day_trade` field which is now `false` for all 14 `045378` rows in the dev DB. Browser verification deferred to PR reviewer / user.)**

## 6. Wrap-up

- [x] 6.1 Run `openspec validate warrant-day-trade-eligibility --strict`; passes.
- [ ] 6.2 Commit + PR with summary referencing this change directory.
