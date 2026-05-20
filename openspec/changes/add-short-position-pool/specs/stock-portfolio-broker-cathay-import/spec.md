## MODIFIED Requirements

### Requirement: 國泰 type vocabulary collapses to BUY / SELL

The 國泰 parser SHALL translate `買賣別` values to the home-hub `transactions.type` enum AND to the `transactions.position_side` enum as follows:

| `買賣別` | `type` | `position_side` |
|---|---|---|
| `現買`, `資買`, `沖買` | `BUY` | `LONG` |
| `券買` | `BUY` | `SHORT` |
| `現賣`, `資賣`, `沖賣` | `SELL` | `LONG` |
| `券賣` | `SELL` | `SHORT` |

The two-character prefix (`現`, `資`, `券`, `沖`) SHALL still be preserved in the parsed payload as `broker_subtype` for backward compatibility but SHALL NOT be included in the `import_fingerprint` canonical string nor persisted to the database (the new `position_side` column replaces its de-facto persistence role).

#### Scenario: Margin BUY collapses to BUY/LONG with subtype 資

- **GIVEN** a CSV row with `買賣別='資買'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='BUY'`, `position_side='LONG'`, `broker_subtype='資'`

#### Scenario: Day-trade SELL collapses to SELL/LONG with subtype 沖

- **GIVEN** a CSV row with `買賣別='沖賣'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='SELL'`, `position_side='LONG'`, `broker_subtype='沖'`

#### Scenario: Short open (券賣) collapses to SELL/SHORT

- **GIVEN** a CSV row with `買賣別='券賣'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='SELL'`, `position_side='SHORT'`, `broker_subtype='券'`

#### Scenario: Short cover (券買) collapses to BUY/SHORT

- **GIVEN** a CSV row with `買賣別='券買'`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `type='BUY'`, `position_side='SHORT'`, `broker_subtype='券'`

#### Scenario: Subtype does not affect fingerprint, but position_side does

- **GIVEN** two CSV rows identical in every fingerprint field but one with `買賣別='現買'` (LONG) and one with `買賣別='券買'` (SHORT)
- **WHEN** both are parsed (both become `type='BUY'`)
- **THEN** their `import_fingerprint` values SHALL differ because `position_side` is a fingerprint segment

## ADDED Requirements

### Requirement: 國泰 parser folds 利息 and 券手續費/標借費 into `fee`

The 國泰 parser SHALL compute `fee` as the sum of `手續費` + `利息` + `券手續費/標借費`, all read from the CSV row. Each component SHALL default to `0` if the column is absent or blank. The aggregated `fee` SHALL be the single value persisted in `transactions.fee` and used in the `import_fingerprint`.

#### Scenario: 資賣 row folds 利息 into fee

- **GIVEN** a CSV row with `買賣別='資賣'`, `手續費=62`, `利息=23`, `券手續費/標借費=0`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `fee=85`

#### Scenario: 券賣 row folds 券手續費 into fee

- **GIVEN** a CSV row with `買賣別='券賣'`, `手續費=63`, `利息=0`, `券手續費/標借費=63`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `fee=126`

#### Scenario: 券買 row folds 利息 (cover interest) into fee

- **GIVEN** a CSV row with `買賣別='券買'`, `手續費=22`, `利息=88`, `券手續費/標借費=0`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `fee=110`

#### Scenario: 現買/現賣 rows are unaffected (zero in all extra columns)

- **GIVEN** a CSV row with `買賣別='現買'`, `手續費=22`, `利息=0`, `券手續費/標借費=0`
- **WHEN** the row is parsed
- **THEN** the payload SHALL have `fee=22` (no change vs pre-feature behavior)

### Requirement: 國泰 parser writes position_side to transactions

The Cathay rehash / insert path SHALL persist the parsed `position_side` value to the new `transactions.position_side` column. When a CSV row is matched to an existing DB row through the rehash path (legacy fingerprint match or business-key match), the matched row's `position_side` SHALL be updated to the recomputed CSV value alongside its fingerprint rehash.

Legacy rows that cannot be matched (typically because the new fee-folding formula in `parse_cathay_rows` produces a different `fee` than what the older importer or manual entry persisted) WILL NOT be overwritten on re-import — the rehash falls through to `_insert_transaction` and creates a duplicate row instead. Operators MUST correct such legacy 短 rows via a targeted SQL `UPDATE ... SET position_side='SHORT' WHERE id IN (...)` rather than re-importing.

#### Scenario: Insert path persists position_side

- **GIVEN** a 國泰 CSV row with `買賣別='券賣'` and no existing matching transaction
- **WHEN** the CSV is uploaded with `dry_run=false`
- **THEN** the newly inserted `transactions` row SHALL have `position_side='SHORT'`

#### Scenario: Matched-row rehash overwrites position_side

- **GIVEN** an existing `transactions` row with `position_side='LONG'` whose `import_fingerprint` equals `_legacy_fingerprint(row)` for a 國泰 CSV row with `買賣別='券賣'` AND whose stored `fee` / `tax` match the new parser's computed values
- **WHEN** the CSV is uploaded with `dry_run=false`
- **THEN** the existing row's `position_side` SHALL be updated to `'SHORT'` alongside the `import_fingerprint` rehash

#### Scenario: Unmatched legacy row produces a duplicate insert, not an overwrite

- **GIVEN** an existing `transactions` row whose stored `fee` differs from the new parser's folded fee (e.g. legacy row has `fee=39` because only `手續費` was persisted; new parser computes `fee=141` from `手續費 + 利息 + 券手續費`)
- **WHEN** the CSV is re-uploaded with `dry_run=false`
- **THEN** the legacy fingerprint and business-key lookups SHALL both miss, the row SHALL be inserted as a new `transactions` row with the new fee + `position_side='SHORT'`, and the legacy LONG row SHALL remain unchanged (operator's responsibility to SQL-patch / delete)
