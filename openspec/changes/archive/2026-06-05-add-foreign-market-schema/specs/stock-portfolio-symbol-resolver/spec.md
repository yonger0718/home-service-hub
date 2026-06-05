## MODIFIED Requirements

### Requirement: Service caches a Chinese-name → ticker map from twstock

The service SHALL maintain a `symbol_map` table mapping listed-company display names (TWSE + TPEx) to their numeric tickers and instrument types, sourced from the `twstock` library, and refresh it on demand or on a weekly cron. Each row SHALL carry `name`, `symbol`, `exchange` (TWSE / TPEx — the Taiwan sub-exchange formerly stored in the `market` column), `market` (top-level — `TW` / `US` / `LSE`, defaulting to `'TW'`), and `type` (instrument classification such as `股票`, `ETF`, `認購權證`, `認售權證`, `牛證`, `熊證`). `type` MAY be NULL for rows written before the column was populated.

#### Scenario: Refresh upserts all known names

- **GIVEN** `twstock.codes` exposes entries for `2317`, `2330`, and `0050`
- **WHEN** `refresh_all_from_twstock` runs
- **THEN** the `symbol_map` table SHALL contain one row per `(name, symbol, exchange, type)` with `market='TW'` and the row count SHALL match the dictionary

#### Scenario: Refresh is idempotent

- **GIVEN** `refresh_all_from_twstock` has already populated the table once
- **WHEN** it runs again against the same `twstock.codes` snapshot
- **THEN** no rows SHALL be inserted or duplicated and existing rows' `updated_at` SHALL be advanced

#### Scenario: Resolution

- **GIVEN** `symbol_map` contains a row `(name='鴻海', symbol='2317')`
- **WHEN** `resolve_name(db, '鴻海')` is called
- **THEN** the function SHALL return `'2317'`

#### Scenario: Unknown name returns None

- **WHEN** `resolve_name(db, '不存在')` is called and no map entry exists
- **THEN** the function SHALL return `None`

#### Scenario: Type is populated for warrants and equities

- **GIVEN** `twstock.codes['045378'].type == '認購權證'` and `twstock.codes['2330'].type == '股票'`
- **WHEN** `refresh_all_from_twstock` runs
- **THEN** the `symbol_map` row for `045378` SHALL have `type='認購權證'` and the row for `2330` SHALL have `type='股票'`

#### Scenario: Existing rows backfill to `market='TW'`

- **WHEN** the migration adds the new `market` column to an existing populated `symbol_map`
- **THEN** every pre-existing row SHALL have `market='TW'` and the renamed `exchange` column SHALL retain its pre-migration `TWSE` / `TPEx` value

## ADDED Requirements

### Requirement: Resolver lookups accept an optional top-level market scope

`resolve_name(db, name, market='TW')` SHALL accept an optional `market` keyword that defaults to `'TW'`. The lookup SHALL filter on `symbol_map.market` before returning the ticker.

#### Scenario: Default market preserves back-compat

- **WHEN** `resolve_name(db, '鴻海')` is called without specifying `market`
- **THEN** the lookup SHALL apply `market='TW'` and existing callers receive identical results to the pre-migration behavior

#### Scenario: Non-TW market lookup
- **GIVEN** a future `symbol_map` row `(name='Apple Inc', symbol='AAPL', exchange=NULL, market='US')`
- **WHEN** `resolve_name(db, 'Apple Inc', market='US')` is called
- **THEN** the function SHALL return `'AAPL'`
