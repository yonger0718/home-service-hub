## ADDED Requirements

### Requirement: Service maintains a daily FX-rate table keyed by ISO base currency

The service SHALL maintain an `fx_rates` table with primary key `(currency, date)` storing `rate_to_twd NUMERIC(20,8) NOT NULL` and `source VARCHAR(16) NOT NULL DEFAULT 'yfinance'`. The `currency` column SHALL hold ISO base codes only (`USD`, `GBP`); minor-unit variants such as `GBp` SHALL NOT be stored as separate rows and SHALL be derived at read time (`GBp = GBP / 100`).

#### Scenario: Composite primary key prevents duplicates

- **WHEN** two rows are written with the same `(currency, date)`
- **THEN** the table SHALL retain a single row representing the latest write for that key

#### Scenario: Minor units are not stored

- **WHEN** a caller attempts to persist `currency='GBp'`
- **THEN** the service SHALL reject the write (constraint or service-layer validation) — only ISO base codes are permitted

#### Scenario: Rate must be positive

- **WHEN** a write attempts to set `rate_to_twd <= 0`
- **THEN** the database SHALL reject the write through a check constraint

### Requirement: Daily FX-rate fetch via yfinance

The service SHALL expose `fx_rate_service.refresh_today(db) -> RefreshResult` that fetches `USDTWD=X` and `GBPTWD=X` via yfinance and upserts one row per supported currency for today's date in `Asia/Taipei`. The result SHALL include `ok_count`, `skipped_count`, and a list of `errors` for partial failures. A single-ticker failure SHALL NOT abort the batch.

#### Scenario: Successful refresh upserts both rates

- **WHEN** `refresh_today(db)` runs and both yfinance tickers return a regular price
- **THEN** `fx_rates` SHALL contain exactly one row for today for each of `USD` and `GBP`
- **AND** the result SHALL report `ok_count=2`, `skipped_count=0`, `errors=[]`

#### Scenario: Idempotent rerun on same day

- **WHEN** `refresh_today(db)` runs twice on the same calendar date
- **THEN** the row count per `(currency, today)` SHALL remain 1 and the second run SHALL overwrite `rate_to_twd` with the latest fetched value

#### Scenario: Partial failure skips and warns

- **WHEN** yfinance returns valid data for `USDTWD=X` but raises or returns NaN for `GBPTWD=X`
- **THEN** the `USD` row SHALL be upserted, no `GBP` row SHALL be written
- **AND** the result SHALL report `ok_count=1`, `skipped_count=1`, and one entry in `errors` describing the GBP fetch failure

### Requirement: Read API returns the latest FX rate on-or-before a date

The service SHALL expose `fx_rate_service.get_rate(db, currency: str, as_of: date) -> Decimal | None` that returns the most recent `rate_to_twd` for the given ISO currency where `date <= as_of`. When the caller passes `currency='GBp'`, the helper SHALL look up the `GBP` row and divide the rate by `100`.

#### Scenario: Latest rate on-or-before date is returned

- **GIVEN** `fx_rates` rows for `USD` on `2026-06-03` (rate `32.0`) and `2026-06-05` (rate `33.0`)
- **WHEN** `get_rate(db, 'USD', date(2026, 6, 4))` is called
- **THEN** the function SHALL return `Decimal('32.0')`

#### Scenario: GBp derives from GBP row

- **GIVEN** an `fx_rates` row for `GBP` on `2026-06-05` with `rate_to_twd = Decimal('40.0')`
- **WHEN** `get_rate(db, 'GBp', date(2026, 6, 5))` is called
- **THEN** the function SHALL return `Decimal('0.4')`

#### Scenario: No row before date returns None

- **WHEN** `get_rate(db, 'USD', date(2020, 1, 1))` is called against an `fx_rates` table whose earliest USD row is `2026-06-01`
- **THEN** the function SHALL return `None`

### Requirement: Daily scheduler job `fx_rate_refresh`

The service SHALL register an APScheduler job `fx_rate_refresh` running daily at `17:00 Asia/Taipei`, gated by `SCHEDULER_ENABLED=true`. The job SHALL invoke `fx_rate_service.refresh_today` and log start/end with `event=fx_rate_refresh.{started,finished,failed}` plus the `RefreshResult` summary.

#### Scenario: Job is registered at startup

- **WHEN** the service boots with `SCHEDULER_ENABLED=true`
- **THEN** the startup log line `event=scheduler.started` SHALL list a job with id `fx_rate_refresh` and trigger `cron[hour=17,minute=0,timezone=Asia/Taipei]`

#### Scenario: Disabled scheduler skips registration

- **WHEN** the service boots with `SCHEDULER_ENABLED=false`
- **THEN** no `fx_rate_refresh` job SHALL be registered and no FX fetch SHALL occur

#### Scenario: Job failure is logged but does not crash the service

- **GIVEN** `fx_rate_service.refresh_today` raises a transport error
- **WHEN** the scheduled job fires
- **THEN** the scheduler SHALL log `event=fx_rate_refresh.failed` with the exception and the service SHALL continue running
