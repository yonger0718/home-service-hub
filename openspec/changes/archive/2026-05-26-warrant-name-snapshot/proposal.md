## Why

Taiwan warrant codes (e.g. `045378`) are recycled by TWSE after expiry — the same 6-digit code can later be reissued for an unrelated warrant or even a non-warrant instrument. Today, `transactions.name` is captured opportunistically on import and day-trade eligibility is resolved at read-time via a live `symbol_map` lookup. Once `symbol_map` refreshes after a recycle, historical warrant rows would silently show the **new** instrument's name and — worse — flip day-trade eligibility from ineligible (warrant) to eligible (recycled-as-ETF), corrupting realized-P&L tax cost estimates and badges retroactively. No recycled code has hit production yet, but the failure mode is deterministic and silent, so we close the gap before it does.

## What Changes

- Add `instrument_type VARCHAR(64) NULL` column to `transactions`, snapshotted at insert time only when the symbol resolves to a warrant (`symbol_map.type` contains 認購 / 認售 / 牛證 / 熊證). Non-warrant rows leave the column NULL, preserving today's live-lookup behavior.
- Backfill the new column for existing rows whose symbol currently maps to a warrant in `symbol_map`. For most rows the existing `transactions.name` is preserved unchanged. For symbols whose warrant code has already been recycled (so TWSE archive + twstock report the **new** instrument's name), an out-of-band backfill script (`scripts/backfill_warrant_names_from_stonk.py`) overwrites `name` and `instrument_type` from a user-supplied historical-name file (`stonk.json`). Future imports rely on the existing `name_overrides` per-import form field for any newly-discovered recycled warrants.
- Cathay broker import path (`broker_cathay_service._insert_transaction` + both rehash branches) stamps `instrument_type` alongside the existing `broker_day_trade_marker` write.
- Manual entry path (`portfolio_service.create_transaction`) stamps `instrument_type` on insert using the same warrant-only detection.
- `symbol_map_service.is_day_trade_eligible` gains an optional `instrument_type` parameter; when provided, it bypasses the live `symbol_map` lookup so historical warrant rows stay ineligible even after a recycle. `_recompute_day_trade_flags` passes each row's stamped value.
- Out of scope: non-warrant ETF/stock renames, display-time toggle between snapshot and live name, recovery of pre-import historical names from external sources.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-broker-cathay-import`: insert + rehash paths additionally stamp `instrument_type` when the symbol is a warrant.
- `stock-portfolio-realized-pnl`: day-trade eligibility resolution prefers per-row stamped `instrument_type` over live `symbol_map` lookup when present, immunizing historical warrant rows from future warrant-code recycle.

## Impact

- Schema: one new nullable column on `transactions`; two new Alembic revisions (column add + warrant backfill).
- Code: `services/stock-portfolio-service/app/models/portfolio.py`, `app/services/broker_cathay_service.py`, `app/services/portfolio_service.py`, `app/services/symbol_map_service.py`.
- Tests: unit coverage for snapshot-on-insert (warrant + non-warrant), rehash propagation, eligibility-helper preference order, and post-recycle simulation (mutate `symbol_map.type` after insert and confirm eligibility stays as stamped).
- No API/contract change; no frontend change. Backfill is idempotent and read-only against `symbol_map`.
