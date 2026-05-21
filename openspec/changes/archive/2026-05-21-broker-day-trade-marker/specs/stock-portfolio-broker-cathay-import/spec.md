## MODIFIED Requirements

### Requirement: 國泰 type vocabulary collapses to BUY / SELL

The 國泰 parser SHALL translate `買賣別` values to the home-hub `transactions.type` enum AND to the `transactions.position_side` enum as follows:

| `買賣別` | `type` | `position_side` |
|---|---|---|
| `現買`, `資買`, `沖買` | `BUY` | `LONG` |
| `券買` | `BUY` | `SHORT` |
| `現賣`, `資賣`, `沖賣` | `SELL` | `LONG` |
| `券賣` | `SELL` | `SHORT` |

The two-character prefix (`現`, `資`, `券`, `沖`) SHALL still be preserved in the parsed payload as `broker_subtype` for backward compatibility but SHALL NOT be included in the `import_fingerprint` canonical string nor persisted to the database (the `position_side` column already replaces its de-facto persistence role).

Additionally, the parser SHALL emit `broker_day_trade_marker` on the parsed payload — set to the literal `買賣別` value when it is `沖買` or `沖賣`, and `None` for every other value (including `現買`, `現賣`, `資買`, `資賣`, `券買`, `券賣`). The importer SHALL persist this value to a new nullable column `transactions.broker_day_trade_marker VARCHAR(8)` on both the insert path AND the business-key rehash path (so re-importing the same CSV propagates markers to legacy rows).

#### Scenario: Margin BUY collapses to BUY/LONG with subtype 資

- **GIVEN** a CSV row with `買賣別='資買'`
- **WHEN** the row is parsed
- **THEN** the parsed payload SHALL contain `type='BUY'`, `position_side='LONG'`, `broker_subtype='資'`, and `broker_day_trade_marker=None`

#### Scenario: Day-trade BUY emits 沖買 marker

- **GIVEN** a CSV row with `買賣別='沖買'`
- **WHEN** the row is parsed
- **THEN** the parsed payload SHALL contain `type='BUY'`, `position_side='LONG'`, `broker_subtype='沖'`, and `broker_day_trade_marker='沖買'`

#### Scenario: Day-trade SELL emits 沖賣 marker

- **GIVEN** a CSV row with `買賣別='沖賣'`
- **WHEN** the row is parsed
- **THEN** the parsed payload SHALL contain `type='SELL'`, `position_side='LONG'`, `broker_subtype='沖'`, and `broker_day_trade_marker='沖賣'`

#### Scenario: Cash BUY emits no marker

- **GIVEN** a CSV row with `買賣別='現買'`
- **WHEN** the row is parsed
- **THEN** the parsed payload SHALL contain `broker_day_trade_marker=None`

#### Scenario: Marker persists on insert path

- **GIVEN** a fresh import of a 國泰 CSV containing one `沖買` row and one `現買` row for distinct symbols
- **WHEN** the import commits (non-dry-run)
- **THEN** the new `transactions` row for the `沖買` symbol SHALL have `broker_day_trade_marker='沖買'`
- **AND** the new `transactions` row for the `現買` symbol SHALL have `broker_day_trade_marker IS NULL`

#### Scenario: Marker persists on business-key rehash path

- **GIVEN** an existing `transactions` row whose business key (`symbol+type+position_side+quantity+price+fee+tax+trade_date`) matches a `沖買` row in a new 國泰 CSV upload, AND that existing row has `broker_day_trade_marker IS NULL` (e.g., inserted via a manual entry or pre-feature import)
- **WHEN** the import commits and the rehash branch in `_commit_rehash` triggers (legacy-fingerprint match OR `_business_key_match`)
- **THEN** the existing row's `broker_day_trade_marker` SHALL be updated to `沖買`
- **AND** the `import_fingerprint` and `position_side` updates already performed by the rehash branch SHALL continue unchanged
