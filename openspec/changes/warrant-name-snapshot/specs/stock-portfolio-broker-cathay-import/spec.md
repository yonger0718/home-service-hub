## ADDED Requirements

### Requirement: 國泰 import path snapshots `instrument_type` for warrant rows

The 國泰 import path SHALL snapshot the symbol's current `symbol_map.type` onto `transactions.instrument_type` at insert time when the symbol resolves to a warrant (type contains any of 認購 / 認售 / 牛證 / 熊證). For non-warrant symbols and for symbols absent from `symbol_map`, `instrument_type` SHALL be left NULL.

The snapshot SHALL be applied on:

1. The first-time insert path (`_insert_transaction`).
2. The legacy-fingerprint rehash branch (alongside the existing `broker_day_trade_marker` write).
3. The business-key rehash branch (alongside the existing `broker_day_trade_marker` write).

#### Scenario: Warrant insert stamps instrument_type from symbol_map
- **WHEN** a 國泰 CSV row whose symbol maps to `symbol_map.type = '上市認購(售)權證'` is inserted
- **THEN** the resulting `transactions` row has `instrument_type = '上市認購(售)權證'`

#### Scenario: Non-warrant insert leaves instrument_type NULL
- **WHEN** a 國泰 CSV row whose symbol maps to `symbol_map.type = '上市ETF'` is inserted
- **THEN** the resulting `transactions` row has `instrument_type IS NULL`

#### Scenario: Unmapped symbol leaves instrument_type NULL
- **WHEN** a 國泰 CSV row whose symbol is absent from `symbol_map` is inserted
- **THEN** the resulting `transactions` row has `instrument_type IS NULL`

#### Scenario: Legacy-fingerprint rehash stamps instrument_type when symbol is warrant
- **WHEN** the legacy-fingerprint rehash branch matches an existing warrant row
- **THEN** the row's `instrument_type` is set to the current `symbol_map.type` even if the column was previously NULL

#### Scenario: Business-key rehash stamps instrument_type when symbol is warrant
- **WHEN** the business-key rehash branch matches an existing warrant row
- **THEN** the row's `instrument_type` is set to the current `symbol_map.type`

#### Scenario: Rehash on non-warrant row leaves instrument_type NULL
- **WHEN** either rehash branch matches an existing row whose symbol is not a warrant in `symbol_map`
- **THEN** the row's `instrument_type` remains NULL (no spurious write)

#### Scenario: Rehash preserves an already-stamped instrument_type after warrant-code recycle
- **WHEN** either rehash branch matches an existing row whose `instrument_type` is already non-NULL (e.g. `'上櫃認購(售)權證'`) AND the symbol's current `symbol_map.type` has changed to a different value (e.g. `'上市ETF'` after recycle)
- **THEN** the existing `instrument_type` value SHALL be preserved unchanged (the rehash MUST NOT overwrite the historical snapshot with the post-recycle live value)
