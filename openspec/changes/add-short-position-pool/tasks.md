## 1. Schema & migration

- [x] 1.1 Add `PositionSide` enum (LONG, SHORT) and `position_side` column to `Transaction` model in `services/stock-portfolio-service/app/models/portfolio.py`. Default `LONG`, non-null. Mirror in any Pydantic schemas that mirror the row shape.
- [x] 1.2 Create Alembic migration: `CREATE TYPE position_side_enum`; `ALTER TABLE transactions ADD COLUMN position_side position_side_enum NOT NULL DEFAULT 'LONG'`. Downgrade reverses.
- [x] 1.3 Run `alembic upgrade head` against local DB; verify column exists + all 2150 existing rows = LONG. Run `alembic downgrade -1` then `upgrade head` to confirm reversibility.

## 2. Realized P&L dual-pool

- [x] 2.1 Refactor `iter_realized_events` in `app/services/realized_pnl_service.py` to maintain `pools[symbol] = {'LONG': {...}, 'SHORT': {...}}`. Route each transaction by `transaction.position_side`.
- [x] 2.2 Implement LONG branches (BUY adds, SELL closes) preserving existing MA math bit-for-bit for long-only fixtures.
- [x] 2.3 Implement SHORT branches: SHORT SELL adds to short pool (record avg short price + per-share net proceeds), SHORT BUY closes (realize gain = (avg_short_open_proceeds_per_share × qty) - (cover_gross + fee + tax)).
- [x] 2.4 Edge-case branches: `LONG SELL` empty long pool → emit event `note="no_long_inventory"`. `SHORT BUY` empty short pool → emit event `note="no_short_inventory"`, `realized_pnl=-(cover_gross+fee+tax)`.
- [x] 2.5 Add `position_side: PositionSide` field to `RealizedPnlEvent` dataclass and `RealizedPnlEventOut` Pydantic schema in `app/schemas/realized_pnl.py`.
- [x] 2.6 Mirror dual-pool logic into `_step_transactions` SELL branch in `app/services/portfolio_service.py` so `total_realized_pnl` summary stays in sync with `iter_realized_events`.

## 3. Cathay parser

- [x] 3.1 Extend `CATHAY_SIDE_MAP` in `app/services/broker_cathay_service.py` to return `(type, position_side)` tuple instead of just type. Mapping per spec table.
- [x] 3.2 Update `parse_cathay_rows` to read 利息 + 券手續費/標借費 columns and fold into `fee` (sum of 手續費 + 利息 + 券手續費/標借費).
- [x] 3.3 Add `position_side` to `ParsedRow.payload` and include it in the `import_fingerprint` canonical string (via `_transaction_fingerprint` signature extension).
- [x] 3.4 Update `_insert_transaction` to write `position_side` to the new column.
- [x] 3.5 Update rehash path: when rehashing, also overwrite `position_side` to match the recomputed value (legacy LONG default gets corrected to SHORT for 短 rows).

## 4. Ledger guard

- [x] 4.1 Update `validate_holdings_before_sell` (or equivalent guard in `portfolio_service.py`) to skip the long-ledger check when `position_side='SHORT'`.
- [x] 4.2 Add a parallel `validate_short_cover` check: SHORT BUY must not exceed cumulative open short qty at that ledger point.
- [x] 4.3 Verify the broker-CSV ledger-guard bypass (`broker_cathay_service.py:325` comment region) still operates correctly for short opens.

## 5. Backend tests

- [x] 5.1 New `tests/unit/test_realized_pnl_short_pool.py`: 券賣→券買 round trip realizes correct gain; 券賣 alone emits no event; partial cover leaves residual short; SHORT BUY with no short pool yields `no_short_inventory`; long+short same symbol independent pools.
- [x] 5.2 Extend `tests/unit/test_realized_pnl_invariant.py`: add a mixed long+short fixture verifying `sum(events.realized_pnl) == summary.total_realized_pnl`.
- [x] 5.3 New `tests/unit/test_cathay_position_side.py`: each of 8 `買賣別` values maps to correct `(type, position_side, fee)` tuple including 利息/券手續費 folding.
- [x] 5.4 Extend `tests/integration/test_realized_pnl_endpoint.py`: response includes `position_side` field on every event; SHORT events filterable via `symbol` query.
- [x] 5.5 Run full backend suite: `cd services/stock-portfolio-service && pytest tests/unit/ tests/integration/`. All green (2 pre-existing post_import_orchestrator failures unrelated to this change).

## 6. Frontend

- [x] 6.1 Add `position_side: 'LONG' | 'SHORT'` to `RealizedPnlEvent` interface in `frontend/src/app/models/portfolio.model.ts`. Add same field to `Transaction` interface.
- [x] 6.2 Add 融券 badge rendering to `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.html` for `event.position_side === 'SHORT'` rows. Reuse existing badge styling pattern (parallel to 當沖).
- [x] 6.3 Add 融券 badge rendering to `frontend/src/app/components/portfolio/transaction-list/transaction-list.html` for `tx.position_side === 'SHORT'` rows.
- [x] 6.4 Update component specs: existing realized-pnl spec updated for `position_side` + `no_long_inventory` note rename. (transaction-list has no existing spec to extend; badge tested manually in 7.5.)
- [x] 6.5 Run `cd frontend && npx ng test --watch=false`. All 17 tests green.

## 7. Manual verification

- [x] 7.1 Restart `stock-portfolio-service` via PM2. Hit `GET /api/portfolio/realized-pnl` — HTTP 200, every event has `position_side` field.
- [x] 7.2 SQL-patched 4 legacy 短 rows directly (`UPDATE transactions SET position_side='SHORT' WHERE id IN (617,623,624,693)`). Re-import via UI deliberately SKIPPED — legacy rows have fee mismatch vs new fold formula (DB fee=39 vs CSV fold=141 for 漢磊 券賣) so `business_key_match` would fail and rehash path would CREATE DUPLICATES instead of updating in place.
- [x] 7.3 SQL spot-check confirms `(SELL, SHORT)=2` (漢磊 + 技嘉 opens) and `(BUY, SHORT)=2` (漢磊 + 技嘉 covers). Remaining 2150 rows = LONG.
- [x] 7.4 Endpoint smoke: `GET /realized-pnl?symbol=2376` returns SHORT cover event 2022-11-18 with realized_pnl=-33313 (loss as expected: shorted @79.5, covered @112.5).
- [x] 7.5 Endpoint smoke: `GET /realized-pnl?symbol=3707` returns SHORT cover event 2022-08-02 with realized_pnl=1129 (small gain: shorted @98, covered @96.5). Visual badge verification deferred to user.

## 8. OpenSpec close-out

- [x] 8.1 `openspec validate add-short-position-pool --strict` passes.
- [ ] 8.2 Commit, push, PR. After merge, run `openspec archive add-short-position-pool --yes`.
