## ADDED Requirements

### Requirement: Service caches a Chinese-name → ticker map from twstock

The service SHALL maintain a `symbol_map` table mapping listed-company display names (TWSE + TPEx) to their numeric tickers, sourced from the `twstock` library, and refresh it on demand or on a weekly cron.

#### Scenario: Refresh upserts all known names
- **GIVEN** `twstock.codes` exposes entries for `2317`, `2330`, and `0050`
- **WHEN** `refresh_all_from_twstock` runs
- **THEN** the `symbol_map` table SHALL contain one row per `(name, symbol, market)` and the row count SHALL match the dictionary

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
