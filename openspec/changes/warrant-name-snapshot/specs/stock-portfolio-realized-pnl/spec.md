## ADDED Requirements

### Requirement: Day-trade eligibility prefers per-row stamped `instrument_type` over live `symbol_map` lookup

`symbol_map_service.is_day_trade_eligible` SHALL accept an optional `instrument_type` argument. When the caller passes a non-None value (including the empty string), the helper SHALL determine eligibility from that value alone and SHALL NOT query `symbol_map`. When the argument is `None`, the helper SHALL preserve the existing behavior (live `symbol_map` lookup, fail-open on unmapped / NULL).

`_recompute_day_trade_flags` SHALL pass each row's `instrument_type` into the eligibility helper so that historical warrant rows stay ineligible regardless of subsequent `symbol_map` mutations (e.g. warrant-code recycle).

#### Scenario: Stamped warrant type forces eligibility False even after symbol_map flips
- **WHEN** a transactions row has `instrument_type = '上市認購(售)權證'` and `symbol_map.type` for the same symbol has been mutated to `'上市ETF'`
- **THEN** `is_day_trade_eligible(db, symbol, instrument_type='上市認購(售)權證')` returns `False`
- **AND** `_recompute_day_trade_flags` does NOT flip that row's `is_day_trade` to True even when paired with an opposing same-day transaction

#### Scenario: NULL stamped value falls through to live lookup
- **WHEN** a transactions row has `instrument_type IS NULL` and the symbol maps to `symbol_map.type = '上市ETF'`
- **THEN** `is_day_trade_eligible(db, symbol, instrument_type=None)` returns `True`
- **AND** existing same-day BUY+SELL heuristic behavior is preserved

#### Scenario: Empty-string stamped value is authoritative non-warrant
- **WHEN** the caller passes `instrument_type=''`
- **THEN** the helper returns `True` (no warrant tokens present) without querying `symbol_map`, preserving snapshot-first precedence even when the stamped value is blank

#### Scenario: Non-warrant stamped value returns True without symbol_map query
- **WHEN** a transactions row has `instrument_type = '上市ETF'` (defensive case: backfill should not stamp this, but the helper still handles it)
- **THEN** `is_day_trade_eligible(db, symbol, instrument_type='上市ETF')` returns `True` without touching `symbol_map`

#### Scenario: Backfill migration stamps existing warrant rows from current symbol_map
- **WHEN** the warrant-backfill migration runs against a DB containing warrant rows with `instrument_type IS NULL`
- **THEN** every row whose symbol matches a `symbol_map` entry with type containing 認購 / 認售 / 牛證 / 熊證 receives the matching `symbol_map.type` value
- **AND** non-warrant rows remain `instrument_type IS NULL`
- **AND** re-running the migration is a no-op (idempotent)
