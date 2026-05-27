# stock-portfolio-corporate-actions Specification

## Purpose
TBD - created by archiving change add-corporate-actions. Update Purpose after archive.
## Requirements
### Requirement: Service persists TWSE face-value changes

The service SHALL maintain a `corporate_actions` table with one row per `(symbol, effective_date)` capturing a ratio and source event key.

#### Scenario: Idempotent upsert by source event key
- **WHEN** the same TWSE row is ingested twice
- **THEN** the table SHALL hold a single row keyed by `source_event_key = "{symbol}_{effective_date.isoformat()}"`

#### Scenario: Ratio is positive
- **WHEN** a write attempts to set `ratio <= 0`
- **THEN** the database SHALL reject the write through a check constraint

#### Scenario: Source defaults to TWSE
- **WHEN** a row is persisted via the fetcher
- **THEN** `source` SHALL be `TWSE` and `action_type` SHALL be `FACE_VALUE_CHANGE`

### Requirement: Backfill endpoint ingests one calendar year

The service SHALL expose `POST /api/portfolio/corporate-actions/backfill?year=YYYY` that fetches the TWTB8U dataset for that year and upserts the parsed rows.

#### Scenario: Backfill returns row + write counts
- **WHEN** the endpoint is called for a year with N parseable rows
- **THEN** the response SHALL contain `{year, rows: N, written: N}` and the table SHALL hold N rows for that year (or merged duplicates if previously fetched)

#### Scenario: Backfill skips rows with missing or zero prices
- **WHEN** an upstream row has missing `pre_close` or `post_ref_price`, or `post_ref_price == 0`
- **THEN** the row SHALL be skipped and SHALL NOT be persisted

### Requirement: List endpoint returns ordered actions with optional filters

The service SHALL expose `GET /api/portfolio/corporate-actions?symbol=&from=&to=` returning rows ascending by `effective_date`.

#### Scenario: Filter by symbol
- **WHEN** the client passes `symbol=2330`
- **THEN** the response SHALL include only rows for that symbol

#### Scenario: Filter by date range
- **WHEN** the client passes `from=A&to=B`
- **THEN** the response SHALL include rows where `A <= effective_date <= B`

#### Scenario: No filters
- **WHEN** all filters are omitted
- **THEN** the response SHALL include every stored row in ascending date order

### Requirement: Cumulative split factor helper

The service SHALL expose a `get_split_factor(db, symbol, as_of) -> Decimal` helper that returns the product of all ratios with `effective_date <= as_of`.

#### Scenario: No actions yields factor 1
- **WHEN** no corporate action exists for `symbol`
- **THEN** the helper SHALL return `Decimal("1")`

#### Scenario: Single action multiplies once
- **GIVEN** one action with `ratio=2`
- **WHEN** the helper is called with `as_of >= effective_date`
- **THEN** it SHALL return `Decimal("2")`

#### Scenario: Compound actions multiply together
- **GIVEN** two actions on the same symbol with `ratio=2` and `ratio=5`
- **WHEN** the helper is called with `as_of` past both events
- **THEN** it SHALL return `Decimal("10")`

#### Scenario: Action after as_of is excluded
- **GIVEN** an action with `effective_date = 2026-06-01`
- **WHEN** the helper is called with `as_of = 2026-05-31`
- **THEN** it SHALL return `Decimal("1")` for that action

