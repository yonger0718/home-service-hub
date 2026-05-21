## Why

Cathay broker CSV column `買賣別` carries explicit day-trade markers `沖買`/`沖賣`, but `CATHAY_SIDE_MAP` in `services/stock-portfolio-service/app/services/broker_cathay_service.py:30,34` folds both into `("BUY", "LONG")` / `("SELL", "LONG")`, discarding the hint. Downstream `_recompute_day_trade_flags` then falls back to a bucket heuristic (`has_buy AND has_sell same (symbol, date)`), which over-classifies: any unrelated same-day open+close pair on the same symbol is wrongly flagged True. The recent `warrant-day-trade-eligibility` backfill exposed 20 equity rows on dev DB that the heuristic flagged but were never actual 沖買/沖賣 trades. Authoritative broker signal must win over heuristic.

## What Changes

- Persist broker day-trade marker on `transactions` rows. New nullable column `broker_day_trade_marker VARCHAR(8)` — values `沖買`/`沖賣` for explicit Cathay 現股當沖, `NULL` for everything else (manual entries, non-day-trade Cathay rows, other brokers).
- Cathay import (`broker_cathay_service.parse_cathay_rows`) writes `broker_day_trade_marker` into the parsed payload alongside existing fields; `_insert_transaction` and `_commit_rehash` persist it. Stop discarding `side[0]` as a placeholder `broker_subtype` and use the full marker instead.
- `_recompute_day_trade_flags` derivation switches to a priority chain:
  1. **Explicit broker marker present** in bucket → `is_day_trade = True` for every row in that (symbol, calendar_date) bucket (subject to warrant eligibility gate, which still rejects).
  2. **No marker, but bucket has both BUY and SELL** → fall back to legacy heuristic (preserves behavior for manual entries / non-Cathay sources where the user has no broker proof).
  3. Otherwise → `False`.
- Alembic data migration: re-scan all rows whose `import_fingerprint` indicates Cathay origin (heuristic: `position_side != LONG` OR fingerprint shape) and clear `is_day_trade = false` where no broker marker is present and the bucket would now resolve False. Migration must not flip True→True unnecessarily nor touch rows the legacy heuristic gets right.
- **BREAKING (internal only)**: bucket-derivation contract for Cathay-imported rows changes; any test or fixture that asserted the heuristic on a 現買+現賣 pair will see `False` if no marker is present.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `stock-portfolio-broker-cathay-import`: parser must capture `沖買`/`沖賣` marker from `買賣別` and emit it on the parsed row; importer must persist it to a new `broker_day_trade_marker` column.
- `stock-portfolio-realized-pnl`: `is_day_trade` derivation priority chain — broker marker authoritative when present, bucket heuristic fallback only when marker absent.

## Impact

- **Schema**: new nullable column `transactions.broker_day_trade_marker VARCHAR(8)` + matching Alembic upgrade (no downgrade reversal needed beyond `drop_column`).
- **Services**: `broker_cathay_service.py` (parse + insert paths), `portfolio_service._recompute_day_trade_flags` (priority chain).
- **Migrations**: one Alembic revision pair — column add + data backfill that flips wrongly-True equity rows to False.
- **Tests**: unit tests on parser (沖買/沖賣 captured, 現買/現賣 marker NULL), service tests on priority chain (marker→True, no marker bucket→legacy fallback, marker on warrant→False via eligibility gate), Postgres-gated integration test on backfill.
- **Out of scope**: SinoPac and other broker CSVs (no equivalent marker column today); UI surfacing of marker (read-only column for now).
