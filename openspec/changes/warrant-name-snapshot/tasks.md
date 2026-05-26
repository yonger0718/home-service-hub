## 1. Schema

- [x] 1.1 Add `instrument_type = Column(String(64), nullable=True)` to `services/stock-portfolio-service/app/models/portfolio.py` `Transaction` model (place adjacent to `name`)
- [x] 1.2 Create Alembic revision `s6h7i8j9k0l1_add_instrument_type_column.py` (down_revision = `r5g6h7i8j9k0`) that adds the column as nullable; docstring cross-references the warrant-recycle motivation and the follow-up backfill revision
- [x] 1.3 Create Alembic revision `t7i8j9k0l1m2_backfill_warrant_instrument_type.py` (down_revision = `s6h7i8j9k0l1`) that runs the warrant-only UPDATE described in design D4; logs affected row count

## 2. Symbol-map service helpers

- [x] 2.1 Add `lookup_warrant_type(db, symbol) -> Optional[str]` to `app/services/symbol_map_service.py` — returns `symbol_map.type` if it contains any of `_INELIGIBLE_TYPE_SUBSTRINGS`, else `None`
- [x] 2.2 Extend `is_day_trade_eligible` signature to `(db, symbol, instrument_type: Optional[str] = None)`; when `instrument_type` is non-empty, derive the result from that string alone (no DB query); when `None`/empty, preserve today's live-lookup behavior
- [x] 2.3 Add unit tests covering: stamped warrant → False, stamped non-warrant → True, NULL/empty → live lookup, unmapped symbol with NULL stamped → True (fail-open preserved)

## 3. Recompute pipeline

- [x] 3.1 Update `_recompute_day_trade_flags` in `app/services/portfolio_service.py` to pass each row's `instrument_type` through to `is_day_trade_eligible`
- [x] 3.2 Add unit test: insert a warrant row with `instrument_type` stamped, mutate `symbol_map.type` to a non-warrant value, run recompute, assert `is_day_trade` stays False (post-recycle simulation)

## 4. Insert paths

- [x] 4.1 In `app/services/broker_cathay_service.py` `_insert_transaction`, call `lookup_warrant_type` and stamp `instrument_type` on the new `Transaction` row when non-None
- [x] 4.2 In `app/services/broker_cathay_service.py` legacy-fingerprint rehash branch, write `existing.instrument_type = lookup_warrant_type(db, existing.symbol)` adjacent to the existing `broker_day_trade_marker` write; same in the business-key rehash branch
- [x] 4.3 In `app/services/portfolio_service.py` `create_transaction`, stamp `instrument_type` via `lookup_warrant_type` on insert
- [x] 4.4 Unit tests for `broker_cathay_service`: parameterized insert + both rehash branches × {warrant, non-warrant, unmapped} fixtures asserting expected `instrument_type` value
- [x] 4.5 Unit test for `portfolio_service.create_transaction`: warrant fixture stamps, ETF fixture leaves NULL

## 5. Migration verification

- [x] 5.1 Run `alembic upgrade head` against a throwaway test DB seeded with warrant + non-warrant rows; assert warrant rows get stamped, non-warrant rows stay NULL
- [x] 5.2 Re-run `alembic upgrade head` on the same DB and assert affected count is 0 (idempotent)
- [x] 5.3 Verify rollback path: `alembic downgrade -2` removes both the column and the backfill cleanly without dropping non-`instrument_type` data

## 6. Validation + archive

- [x] 6.1 `openspec validate warrant-name-snapshot --strict` clean
- [x] 6.2 Full test pass: `cd services/stock-portfolio-service && pytest tests/unit/ -k "broker_cathay or day_trade or portfolio_service or symbol_map"`
- [ ] 6.3 PR commit + push following project conventions; await CodeRabbit then merge
- [x] 6.4 Apply migrations on dev DB via `.venv/bin/alembic upgrade head`; verify warrant rows stamped and non-warrant rows untouched with a SELECT count probe

## 7. Historical-name backfill (post-recycle recovery)

- [x] 7.1 Create `services/stock-portfolio-service/scripts/backfill_warrant_names_from_stonk.py` that reads `stonk.json` (path passed as `--stonk-json`), previews diffs in dry-run mode (default), and writes only when `--commit` is passed; resolves `instrument_type` from `(market, 購|售)` and writes both `name` + `instrument_type`
- [x] 7.2 Run the script in dry-run against dev DB; verify expected symbol set (>= the 5 originally-discovered warrants)
- [x] 7.3 Apply with `--commit`; recompute `is_day_trade` for affected (symbol, date) buckets; verify all affected warrant rows resolve to `is_day_trade = False`

## 8. Rehash preserves stamped instrument_type (recycle defense)

- [x] 8.1 In `broker_cathay_service.py` both rehash branches, guard the `instrument_type` write with `if existing.instrument_type is None:` so an already-stamped historical row is never clobbered by a current `lookup_warrant_type` result (which can flip after warrant-code recycle)
- [x] 8.2 Add parameterized unit test asserting both rehash branches preserve a pre-stamped `'上櫃認購(售)權證'` even when `symbol_map.type` now reports a non-warrant value (post-recycle simulation)
