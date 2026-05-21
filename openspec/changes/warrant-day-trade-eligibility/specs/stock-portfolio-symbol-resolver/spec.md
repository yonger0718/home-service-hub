## MODIFIED Requirements

### Requirement: Service caches a Chinese-name → ticker map from twstock

The service SHALL maintain a `symbol_map` table mapping listed-company display names (TWSE + TPEx) to their numeric tickers and instrument types, sourced from the `twstock` library, and refresh it on demand or on a weekly cron. Each row SHALL carry `name`, `symbol`, `market`, and `type` (instrument classification such as `股票`, `ETF`, `認購權證`, `認售權證`, `牛證`, `熊證`). `type` MAY be NULL for rows written before the column was populated.

#### Scenario: Refresh upserts all known names

- **GIVEN** `twstock.codes` exposes entries for `2317`, `2330`, and `0050`
- **WHEN** `refresh_all_from_twstock` runs
- **THEN** the `symbol_map` table SHALL contain one row per `(name, symbol, market, type)` and the row count SHALL match the dictionary

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

## ADDED Requirements

### Requirement: Day-trade eligibility lookup

The service SHALL expose `is_day_trade_eligible(db, symbol) -> bool` returning whether a symbol is eligible for Taiwan 現股當沖 (day-trade) classification. A symbol SHALL be considered INELIGIBLE if its `symbol_map.type` CONTAINS any of `{"認購", "認售", "牛證", "熊證"}` as a substring. All other resolvable types SHALL be considered ELIGIBLE. Unmapped symbols (no `symbol_map` row), rows whose `type` is NULL, and rows whose `type` is an empty string SHALL fail-open and return ELIGIBLE.

#### Scenario: Listed call+put warrant is ineligible

- **GIVEN** `symbol_map` contains `(symbol='045378', type='上市認購(售)權證')`
- **WHEN** `is_day_trade_eligible(db, '045378')` is called
- **THEN** the function SHALL return `False`

#### Scenario: OTC call+put warrant is ineligible

- **GIVEN** `symbol_map` contains `(symbol='738910', type='上櫃認購(售)權證')`
- **WHEN** `is_day_trade_eligible(db, '738910')` is called
- **THEN** the function SHALL return `False`

#### Scenario: 牛熊證 is ineligible

- **GIVEN** `symbol_map` contains `(symbol='082300', type='牛證')`
- **WHEN** `is_day_trade_eligible(db, '082300')` is called
- **THEN** the function SHALL return `False`

#### Scenario: Equity is eligible

- **GIVEN** `symbol_map` contains `(symbol='2330', type='股票')`
- **WHEN** `is_day_trade_eligible(db, '2330')` is called
- **THEN** the function SHALL return `True`

#### Scenario: Unmapped symbol is eligible (fail-open)

- **GIVEN** `symbol_map` contains no row for `'9999'`
- **WHEN** `is_day_trade_eligible(db, '9999')` is called
- **THEN** the function SHALL return `True`

#### Scenario: Row with NULL or empty type is eligible (fail-open)

- **GIVEN** `symbol_map` contains `(symbol='1234', type=NULL)` and `(symbol='5678', type='')`
- **WHEN** `is_day_trade_eligible(db, '1234')` and `is_day_trade_eligible(db, '5678')` are called
- **THEN** both calls SHALL return `True`
