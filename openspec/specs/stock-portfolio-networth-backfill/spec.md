# stock-portfolio-networth-backfill Specification

## Purpose
TBD - created by archiving change add-networth-backfill. Update Purpose after archive.
## Requirements
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

### Requirement: Phase 1 skips inactive dates when an active-date set is provided

The price backfill driver SHALL accept an optional `active_dates: set[date] | None` parameter. When the set is provided and non-empty, the driver SHALL skip every trading-day date that is not in the set: no HTTP request to TWSE or TPEx, no throttle sleep, and no `price_history` write for skipped dates. When the parameter is `None` (the default), the driver SHALL behave exactly as it did before this change.

#### Scenario: Inactive weekday skipped without fetch
- **WHEN** the driver is invoked with `active_dates = {2026-05-15}` over the range `[2026-05-12, 2026-05-16]`
- **THEN** only `2026-05-15` issues HTTP requests; `2026-05-12`, `2026-05-13`, `2026-05-14`, and `2026-05-16` are skipped without any network call or throttle sleep

#### Scenario: None preserves legacy behaviour
- **WHEN** the driver is invoked with `active_dates = None` over any range
- **THEN** every weekday in the range is processed exactly as before (cache-skip via `_existing_price_dates` still applies)

#### Scenario: Inactive-date count surfaced in result
- **WHEN** the driver finishes a run with at least one inactive date skipped
- **THEN** the returned `PriceBackfillResult` SHALL expose a `dates_inactive` counter equal to the number of inactive weekdays skipped, separate from `dates_skipped` (which continues to mean "fetched and both markets empty / holiday")

### Requirement: Phase 2 skips inactive dates when an active-date set is provided

The snapshot replay function SHALL accept an optional `active_dates: set[date] | None` parameter. When the set is provided and non-empty, the function SHALL skip every date that is not in the set: no `portfolio_snapshot` row written for skipped dates. When the parameter is `None`, replay SHALL behave exactly as it did before this change.

#### Scenario: Inactive date produces no snapshot row
- **WHEN** replay runs with `active_dates = {2026-05-15}` over the range `[2026-05-12, 2026-05-16]`
- **THEN** only one `portfolio_snapshot` row is written / upserted (for `2026-05-15`); no rows are written for the other four dates

#### Scenario: Inactive skip does not delete existing rows
- **WHEN** replay runs with an `active_dates` set that excludes a date for which a `portfolio_snapshot` row already exists from a prior full-range run
- **THEN** that pre-existing row SHALL remain untouched (no DELETE)

#### Scenario: SELL trade-day stays active
- **WHEN** the user holds a position from `2022-01-03` (BUY) through `2022-01-05` (SELL closing qty to 0) and triggers a chain covering `[2022-01-01, today]`
- **THEN** the active-date set produced by the orchestrator includes `2022-01-03`, `2022-01-04`, and `2022-01-05`, and replay writes snapshot rows for those three dates only

### Requirement: Holding-interval helper computes per-symbol active dates

The system SHALL provide a helper that, given a database session and an inclusive date range `[from, to]`, returns the union of per-symbol holding intervals clipped to that range, as a `set[date]` of weekday dates only. Intervals SHALL be computed from a chronological walk over `transactions` (signed `quantity`: BUY positive, SELL negative) and `dividends.stock_dividend_shares` (positive). An interval SHALL open on the first date the running qty crosses from 0 to non-zero, and SHALL close inclusively on the date the running qty returns to exactly 0. An interval that never returns to 0 SHALL extend to `to`.

#### Scenario: Closed position yields exact interval
- **WHEN** the user has a BUY of 1000 shares of `2330` on `2022-01-03` and a SELL of 1000 shares on `2022-01-05`, and the helper is called with `[2022-01-01, 2026-05-18]`
- **THEN** the returned set contains `2022-01-03`, `2022-01-04`, and `2022-01-05` and no other dates contributed by `2330`

#### Scenario: Open position extends to range end
- **WHEN** the user has a BUY of 100 shares of `0050` on `2024-06-01` with no subsequent SELL, and the helper is called with `[2024-01-01, 2026-05-18]`
- **THEN** the returned set contains every weekday in `[2024-06-01, 2026-05-18]` contributed by `0050`

#### Scenario: Multi-symbol overlap unions, never double-counts
- **WHEN** the user holds `2330` from `2024-01-15` to `2024-03-15` and `0050` from `2024-02-01` onward, and the helper is called over a range covering both
- **THEN** every weekday in `[2024-01-15, 2024-03-15] ∪ [2024-02-01, today]` appears exactly once in the returned set

#### Scenario: Same-day BUY+SELL yields a single active date
- **WHEN** the user buys 500 shares and sells 500 shares of the same symbol on the same trading day
- **THEN** that single date appears in the active-date set and no surrounding dates are added on its behalf

#### Scenario: Stock-dividend share grant counts as a position change
- **WHEN** the user holds shares on the ex-dividend date and `dividends.stock_dividend_shares > 0` posts on that date
- **THEN** the ex-dividend date is included in the active-date set

#### Scenario: Weekends excluded
- **WHEN** an interval spans a Saturday and Sunday
- **THEN** those weekend dates SHALL NOT appear in the returned set

