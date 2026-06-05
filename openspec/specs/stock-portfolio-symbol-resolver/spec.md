# stock-portfolio-symbol-resolver Specification

## Purpose
TBD - created by archiving change add-symbol-name-resolver. Update Purpose after archive.
## Requirements
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

### Requirement: Resolver lookups accept an optional top-level market scope

`resolve_name(db, name, market='TW')` SHALL accept an optional `market` keyword that defaults to `'TW'`. The lookup SHALL filter on `symbol_map.market` before returning the ticker.

#### Scenario: Default market preserves back-compat

- **WHEN** `resolve_name(db, '鴻海')` is called without specifying `market`
- **THEN** the lookup SHALL apply `market='TW'` and existing callers receive identical results to the pre-migration behavior

#### Scenario: Non-TW market lookup
- **GIVEN** a future `symbol_map` row `(name='Apple Inc', symbol='AAPL', exchange=NULL, market='US')`
- **WHEN** `resolve_name(db, 'Apple Inc', market='US')` is called
- **THEN** the function SHALL return `'AAPL'`

### Requirement: Backfill rewrites resolvable transaction symbols

The service SHALL provide a `backfill_transactions` operation that scans `transactions` for rows whose `symbol` is a non-numeric Chinese name resolvable through the map, and rewrites `symbol` to the resolved ticker. `import_fingerprint` SHALL NOT be recomputed: the original CSV's fingerprint is preserved so future re-imports of the same source CSV still dedupe against the rewritten row.

#### Scenario: Resolvable Chinese name is rewritten
- **GIVEN** a transaction with `symbol='鴻海'`, `import_fingerprint='fp-X'`, and a map entry `('鴻海', '2317')`
- **WHEN** `backfill_transactions(db, dry_run=False)` runs
- **THEN** that row's `symbol` SHALL be `'2317'` and `import_fingerprint` SHALL remain `'fp-X'`

#### Scenario: Unresolvable name is left intact
- **GIVEN** a transaction with `symbol='世紀鋼富邦49購01'` and no matching map entry
- **WHEN** the backfill runs
- **THEN** that row's `symbol` SHALL remain unchanged and the original name SHALL appear in the returned `unresolved` list

#### Scenario: Dry-run makes no writes
- **WHEN** `backfill_transactions(db, dry_run=True)` runs
- **THEN** no `UPDATE transactions` SQL SHALL be committed and the response SHALL still surface the would-be `updated` and `unresolved` counts

### Requirement: Weekly scheduler job refreshes the map

The service SHALL schedule `symbol_map_refresh` to run weekly at 06:00 Asia/Taipei on Mondays via the existing APScheduler.

#### Scenario: Job is registered at startup
- **WHEN** the service boots with `SCHEDULER_ENABLED=true`
- **THEN** the startup log line `event=scheduler.started` SHALL list a job with id `symbol_map_refresh` and trigger `cron[day_of_week=mon,hour=6,minute=0,timezone=Asia/Taipei]`

#### Scenario: Job survives a failing refresh
- **GIVEN** `twstock.__update_codes` raises during a scheduled run
- **WHEN** the job fires
- **THEN** the scheduler SHALL log `scheduler.symbol_map_refresh.failed` and SHALL NOT crash the service

### Requirement: Manual refresh + backfill endpoints

The service SHALL expose authenticated POST endpoints to drive refresh and backfill on demand.

#### Scenario: Manual refresh
- **WHEN** a client `POST`s to `/api/portfolio/symbol-map/refresh`
- **THEN** the service SHALL invoke `refresh_all_from_twstock` and return its result with HTTP 200

#### Scenario: Manual backfill dry-run
- **WHEN** a client `POST`s to `/api/portfolio/symbol-map/backfill?dry_run=true`
- **THEN** the service SHALL invoke `backfill_transactions(dry_run=True)` and return the would-be counts without persisting changes

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

