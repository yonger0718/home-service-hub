## 1. Schema

- [x] 1.1 Add `broker_day_trade_marker = Column(String(8), nullable=True)` to `services/stock-portfolio-service/app/models/portfolio.py` `Transaction` model, between `tax` and `is_day_trade`.
- [x] 1.2 Create Alembic revision `q4f5g6h7i8j9_add_broker_day_trade_marker_column.py` with `down_revision = "p3e4f5g6h7i8"`. Upgrade: `op.add_column("transactions", sa.Column("broker_day_trade_marker", sa.String(length=8), nullable=True))`. Downgrade: `op.drop_column("transactions", "broker_day_trade_marker")`. Docstring explains the column carries 沖買/沖賣 from Cathay CSV `買賣別` and is consumed by `_recompute_day_trade_flags` priority chain.

## 2. Cathay parser

- [x] 2.1 In `services/stock-portfolio-service/app/services/broker_cathay_service.py` `parse_cathay_rows`, after `mapping = CATHAY_SIDE_MAP.get(side)`, compute `broker_day_trade_marker = side if side in ("沖買", "沖賣") else None` and add it to the `payload` dict alongside existing keys.
- [x] 2.2 In `_insert_transaction`, read `payload["broker_day_trade_marker"]` (default None) and pass it to `models.Transaction(...)` constructor.
- [x] 2.3 In `_commit_rehash`, on the legacy-fingerprint match branch (line ~503) AND the business-key match branch (line ~521), also write `existing.broker_day_trade_marker = row.payload.get("broker_day_trade_marker")` / `business_match.broker_day_trade_marker = ...` immediately before `db.flush()`. Ensures re-import propagates markers to pre-existing rows.

## 3. Recompute priority chain

- [x] 3.1 In `services/stock-portfolio-service/app/services/portfolio_service.py` `_recompute_day_trade_flags`, replace the existing `new_flag` computation with the priority chain from design D2: `marker_present = any(row.broker_day_trade_marker in {"沖買", "沖賣"} for row in bucket)`; if `marker_present` → `new_flag = symbol_map_service.is_day_trade_eligible(db, normalized)`; elif `has_buy and has_sell` → same eligibility-gated `new_flag`; else → `False`.

## 4. Tests — Cathay parser

- [x] 4.1 In `services/stock-portfolio-service/tests/unit/test_broker_cathay_service.py` (or the corresponding existing test module), add cases asserting `parse_cathay_rows` emits `broker_day_trade_marker='沖買'` for a `買賣別='沖買'` row, `'沖賣'` for `沖賣`, and `None` for each of `現買`, `現賣`, `資買`, `資賣`, `券買`, `券賣`.

## 5. Tests — recompute priority chain

- [x] 5.1 In `services/stock-portfolio-service/tests/unit/test_day_trade_eligibility.py`, add `test_marker_pair_same_day_flips_day_trade`: equity symbol, both rows have marker → True.
- [x] 5.2 Add `test_marker_only_on_buy_still_flips_bucket`: only BUY has marker, no opposing SELL in bucket → True (marker authoritative even when one-sided).
- [x] 5.3 Add `test_marker_on_warrant_rejected_by_eligibility_gate`: warrant symbol with both 沖買 + 沖賣 markers → False (eligibility overrides marker).
- [x] 5.4 Add `test_no_marker_equity_pair_falls_back_to_heuristic`: equity, no markers, BUY+SELL same day → True (legacy fallback, branch 2).
- [x] 5.5 Add `test_no_marker_no_pair_stays_false`: equity, no markers, only a BUY → False (branch 3).

## 6. Tests — rehash path

- [x] 6.1 In `services/stock-portfolio-service/tests/unit/test_broker_cathay_service.py` (or the existing rehash test module), add a case asserting that re-importing a CSV with a `沖買` row whose business key matches a pre-existing `broker_day_trade_marker IS NULL` row updates the row's `broker_day_trade_marker` to `'沖買'` (verify via `db.refresh` on the matched row after commit).

## 7. Validation & verification

- [x] 7.1 Run `openspec validate broker-day-trade-marker --strict` from repo root; resolve any errors.
- [x] 7.2 Run `cd services/stock-portfolio-service && pytest tests/unit/test_broker_cathay_service.py tests/unit/test_day_trade_eligibility.py -v`; all green.
- [x] 7.3 Run `cd services/stock-portfolio-service && alembic upgrade head` on a scratch DB; verify column exists. Then `alembic downgrade -1`; verify column dropped.

## 8. Dev-DB cleanup (documentation only)

- [x] 8.1 Append a short note to the migration docstring (revision `q4f5g6h7i8j9`) instructing the operator to re-import the most recent 30-day Cathay CSV after upgrade to propagate markers and let live recompute flip the 20 wrongly-True equity rows. No auto-script.

## 9. Odd-lot rule (D5)

- [x] 9.1 In `services/stock-portfolio-service/app/services/portfolio_service.py` `_recompute_day_trade_flags`, add a module-level helper `def _is_odd_lot(quantity: int) -> bool: return quantity < 1000 or quantity % 1000 != 0`. Split `bucket` into `board_lot = [r for r in bucket if not _is_odd_lot(r.quantity)]`. Compute `marker_present`, `has_buy`, `has_sell` from `board_lot` only (not `bucket`). The priority-chain result becomes `board_flag`. In the final write loop, for each row in `bucket` set `new_flag = False if _is_odd_lot(row.quantity) else board_flag`.
- [x] 9.2 Add unit tests to `services/stock-portfolio-service/tests/unit/test_day_trade_eligibility.py`:
  - `test_odd_lot_pair_with_marker_stays_false` — eligible symbol, BUY 25 + SELL 25 both with markers → False.
  - `test_mixed_odd_lot_and_board_lot_bucket_only_board_flips` — eligible symbol, BUY 1000 + SELL 1000 (board) + BUY 42 (odd) same date → board rows True, odd row False.
  - `test_board_lot_alone_with_odd_lot_opposing_side_stays_false` — eligible symbol, BUY 1000 (board) + SELL 42 (odd) same date → both False (board-lot subset has no SELL).
- [x] 9.3 Create Alembic revision `r5g6h7i8j9k0_backfill_odd_lot_day_trade_flags.py` with `down_revision = "q4f5g6h7i8j9"`. Upgrade runs `UPDATE transactions SET is_day_trade = false WHERE is_day_trade = true AND (quantity < 1000 OR quantity % 1000 != 0)` and prints affected row count. Downgrade no-op. Docstring explains the odd-lot rule (D5) and notes it is safe to re-run.
- [x] 9.4 Run `openspec validate broker-day-trade-marker --strict` from repo root; resolve any errors.
- [x] 9.5 Run `cd services/stock-portfolio-service && .venv/bin/pytest tests/unit/test_day_trade_eligibility.py -v`; all green.
