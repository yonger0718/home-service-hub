# stock-portfolio-price-history Specification

## Purpose
TBD - created by archiving change merge-stonk-portfolio-features. Update Purpose after archive.
## Requirements
### Requirement: Service stores daily OHLC for TWSE and TPEx symbols

The service SHALL maintain a `price_history` table keyed by `(symbol, date)` capturing daily open, high, low, close, volume, turnover, and source for each trading day.

#### Scenario: Composite primary key prevents duplicates
- **WHEN** two rows with the same `(symbol, date)` are written
- **THEN** the table SHALL retain a single row representing the latest write for that key

#### Scenario: Close must be positive
- **WHEN** a write attempts to set `close <= 0`
- **THEN** the database SHALL reject the write through a check constraint

#### Scenario: Source is recorded
- **WHEN** a row is persisted
- **THEN** the `source` column SHALL be either `TWSE` or `TPEx`

### Requirement: Backfill endpoint ingests one trading day from TWSE, TPEx, or both

The service SHALL expose `POST /api/portfolio/price-history/backfill?date=YYYY-MM-DD&market=TWSE|TPEX|BOTH` that fetches, parses, and upserts the daily OHLC for the requested trading day.

#### Scenario: Repeat backfill of the same date is idempotent
- **WHEN** the same date is backfilled twice
- **THEN** the row count for that date SHALL be unchanged after the second call

#### Scenario: Backfill respects the market filter
- **WHEN** `market=TWSE` is requested
- **THEN** only the TWSE fetcher SHALL run and the response SHALL include `twse_rows` and `tpex_rows=0`

#### Scenario: Unknown market is rejected
- **WHEN** `market` is not in `{TWSE, TPEX, BOTH}` (case-sensitive)
- **THEN** the API SHALL return HTTP 422

#### Scenario: TLS fallback policy is honoured
- **WHEN** the upstream TWSE or TPEx endpoint fails initial verified TLS and `TWSE_TLS_MODE=fallback`
- **THEN** the fetcher SHALL retry once with `verify=False` and log a warning; under `TWSE_TLS_MODE=verify` it SHALL fail without retrying

### Requirement: Range query returns ordered history for a normalised symbol

The service SHALL expose `GET /api/portfolio/price-history?symbol=&from=&to=` returning rows in ascending date order, with symbol normalisation that strips an optional `.TW` or `.TWO` suffix.

#### Scenario: Symbol normalisation
- **WHEN** a client queries with `symbol=2330.TW`
- **THEN** the service SHALL return rows stored under `symbol=2330`

#### Scenario: Inclusive date range
- **WHEN** a client queries with `from=A` and `to=B`
- **THEN** the response SHALL include rows where `A <= date <= B` and SHALL order them ascending by date

#### Scenario: Empty range
- **WHEN** no rows exist in the requested range
- **THEN** the response SHALL be an empty array with HTTP 200

