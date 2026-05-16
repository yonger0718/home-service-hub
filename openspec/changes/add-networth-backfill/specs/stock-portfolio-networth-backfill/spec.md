## ADDED Requirements

### Requirement: Range price backfill driver
The system SHALL provide a backfill function that, given an inclusive date range `[from, to]`, fetches TWSE and TPEx daily close prices for every trading day in the range and persists them into `price_history` via the existing single-date upsert path.

#### Scenario: Per-trading-day iteration with weekend skip
- **WHEN** the driver is invoked with a range spanning a weekend
- **THEN** no HTTP request is issued for Saturday or Sunday dates

#### Scenario: Holiday detected via empty payload
- **WHEN** TWSE returns an empty list for a weekday date
- **THEN** the date is logged as a holiday-skip, no `price_history` row is inserted for that date, and the driver continues to the next date without sleeping

#### Scenario: Throttle gap respected between dates
- **WHEN** the driver moves from one trading-day fetch to the next
- **THEN** at least `throttle_sec` seconds elapse between the start of consecutive HTTP requests

#### Scenario: Per-date failure isolation
- **WHEN** TWSE or TPEx returns an error (non-200, timeout, parse failure) for a single date
- **THEN** the failure is logged with the date, the database transaction for that date is rolled back, and the driver continues to the next date

#### Scenario: Retry with backoff on transient failure
- **WHEN** a single-date fetch fails with a transient error (timeout or 5xx)
- **THEN** the driver retries the fetch after a 2-second pause, and once more after a further 5-second pause, before declaring the date failed

#### Scenario: Idempotent re-run
- **WHEN** the driver is invoked twice over the same range
- **THEN** the second run produces no duplicate rows; `price_history.merge()` upserts on composite PK `(symbol, date)`

### Requirement: Snapshot replay from historical prices
The system SHALL provide a snapshot-replay function that, given an inclusive date range `[from, to]`, recomputes `portfolio_snapshot` rows from `transactions`, `dividends`, and `price_history` already present in the database.

#### Scenario: Holdings-as-of-date calculated from transactions
- **WHEN** replay processes date `D`
- **THEN** holdings per symbol equal the signed sum of `transactions.quantity` for that symbol where `trade_date <= D` plus the sum of `dividends.stock_dividend_shares` for that symbol where `ex_dividend_date <= D`

#### Scenario: Market value from same-date price_history
- **WHEN** replay computes market value for symbol `S` on date `D`
- **THEN** the value is `holdings[S] * price_history.close where symbol=S and date=D`

#### Scenario: Missing price treated as zero contribution
- **WHEN** no `price_history` row exists for symbol `S` on date `D`
- **THEN** symbol `S` contributes zero to that date's market value and a WARN is logged once per symbol-date pair

#### Scenario: Cumulative dividends summed
- **WHEN** replay computes `total_dividends` for date `D`
- **THEN** the value equals the sum of `dividends.amount` where `ex_dividend_date <= D`

#### Scenario: XIRR left null on backfilled rows
- **WHEN** replay writes a snapshot row
- **THEN** `portfolio_xirr` is `NULL`

#### Scenario: Idempotent upsert on date PK
- **WHEN** replay is invoked twice over the same range
- **THEN** the second run overwrites the same `portfolio_snapshot.date` rows via `Session.merge`, with no duplicate or orphan rows

### Requirement: Backfill HTTP endpoint
The system SHALL expose `POST /api/portfolio/history/backfill-networth` accepting a JSON body and returning aggregated counts.

#### Scenario: Phase=prices triggers only price backfill
- **WHEN** the endpoint receives `{from, to, phase: "prices"}`
- **THEN** only the price-range driver runs; `portfolio_snapshot` rows are not modified

#### Scenario: Phase=snapshots triggers only replay
- **WHEN** the endpoint receives `{from, to, phase: "snapshots"}`
- **THEN** only the replay function runs; no external HTTP requests are issued

#### Scenario: Phase=both runs prices then snapshots
- **WHEN** the endpoint receives `{from, to, phase: "both"}`
- **THEN** the driver runs to completion first, then the replay runs over the same range

#### Scenario: Response includes aggregated counts
- **WHEN** the endpoint returns
- **THEN** the body contains `dates_processed`, `dates_skipped`, `snapshots_written`, and a `errors` list with `{date, reason}` per failed date

### Requirement: Replay queries use existing indexes
The system SHALL rely on existing schema for index-hit replay queries: `price_history` composite primary key `(symbol, date)` and `transactions` composite index `ix_transactions_symbol_trade_date`. No new migrations.

#### Scenario: No new migrations introduced
- **WHEN** the change is deployed
- **THEN** `alembic heads` is unchanged from before the change
