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

### Requirement: Phase 2 forward-fills weekends and holidays during held intervals

The snapshot replay function SHALL write a `portfolio_snapshot` row on every weekend and full-market-holiday date in `[from_d, to_d]` provided that, immediately prior to that date, the user held at least one share of any symbol. The forward-filled row SHALL carry the previous trading day's `total_market_value` and `total_cost`, with `total_dividends` / `total_realized_pnl` advanced to include any events that posted on the non-trading date.

#### Scenario: Single weekend inside held interval gets two filled rows
- **WHEN** the user holds a position across Friday `2024-09-13` (trading) through Monday `2024-09-16` (trading), and replay runs over `[2024-09-13, 2024-09-16]`
- **THEN** snapshot rows exist for `2024-09-13`, `2024-09-14`, `2024-09-15`, and `2024-09-16`, with the Saturday and Sunday rows carrying the Friday `total_market_value` and `total_cost`

#### Scenario: 春節 cluster inside held interval gets forward-filled
- **WHEN** the user holds a position across `2023-01-17` (last trading day before LNY) through `2023-01-30` (first trading day after LNY) and replay runs over a range covering both
- **THEN** snapshot rows exist for every calendar date in `[2023-01-17, 2023-01-30]`, with the closed-market dates (`2023-01-18` through `2023-01-29`) carrying `2023-01-17`'s `total_market_value` and `total_cost`

#### Scenario: Non-trading date after SELL closing position stays empty
- **WHEN** the user has a final SELL on `2024-09-13` (Friday) that brings qty to 0, and replay runs over `[2024-09-13, 2024-09-16]`
- **THEN** a snapshot row exists for `2024-09-13` only; no rows are written for `2024-09-14` (Sat), `2024-09-15` (Sun), or `2024-09-16` (Mon, also inactive because qty=0)

#### Scenario: Forward-fill seeded from prior snapshot when range starts on a non-trading date
- **WHEN** replay is invoked with `from_d = 2023-01-21` (a Saturday during 春節 2023), `to_d = 2023-01-25`, and a snapshot row with `total_market_value > 0` exists in the DB for `2023-01-17`
- **THEN** snapshot rows are written for `2023-01-21` through `2023-01-25` carrying the `2023-01-17` row's values (until a real trading day inside the range overwrites the running state)

#### Scenario: Dividends posted on a non-trading date advance the cumulative total
- **WHEN** a cash dividend has an `ex_dividend_date` on a Saturday inside a held interval
- **THEN** the forward-filled Saturday row's `total_dividends` is greater than the prior Friday's by the dividend amount

### Requirement: Phase 2 treats `mv=0 cost>0` trading-day computations as a data gap

When the per-date market-value computation on a real trading day produces `mv = 0` while `total_cost > 0` — meaning every held symbol's price lookup missed despite the date being in `trading_dates` — the function SHALL NOT write a snapshot row for that date and SHALL NOT update `last_trading_mv` / `last_trading_cost`. The date SHALL be added to `stale_candidates` so the end-of-replay bulk DELETE removes any pre-existing bad row, and subsequent forward-fills SHALL continue to use the prior good snapshot value.

#### Scenario: Trading day with all held-symbol prices missing produces no row
- **WHEN** the user holds 5 symbols on a date that `price_history` contains rows for (so the date passes the `trading_dates` membership check) but none of those rows match any of the 5 held symbols
- **THEN** no `portfolio_snapshot` row is written for that date, `last_trading_mv` retains its previous value, and the date is included in the stale-candidate DELETE batch

#### Scenario: Subsequent forward-fill carries the prior good value, not zero
- **WHEN** the data-gap day above is immediately followed by a held-through weekend
- **THEN** the Saturday / Sunday forward-fill rows carry the MV+cost from the last GOOD trading-day snapshot, not from the gap day

### Requirement: Phase 2 self-heals stale `MV=0 cost>0` rows on skipped dates

When `replay_snapshots_range` would NOT write a snapshot row on a given date (the date is skipped because the user held nothing, or is a holiday not eligible for forward-fill, or any other skip path), the function SHALL DELETE any existing `portfolio_snapshot` row on that date whose `total_market_value = 0` AND `total_cost > 0`. Other pre-existing rows (e.g., legit zero-holding rows where `total_cost = 0`) SHALL remain untouched.

#### Scenario: Stale row on a holiday after SELL-closing the position is deleted
- **WHEN** a `portfolio_snapshot` row exists for `2023-01-23` with `total_market_value = 0` and `total_cost = 521478.2241`, the user closed their position before that holiday cluster, and replay runs over a range that includes `2023-01-23`
- **THEN** the row for `2023-01-23` is removed from `portfolio_snapshot` after replay completes

#### Scenario: Legit zero-holdings row with cost=0 is preserved
- **WHEN** a `portfolio_snapshot` row exists for `2024-05-01` with `total_market_value = 0` AND `total_cost = 0` (user genuinely held nothing), and replay runs over a range that includes `2024-05-01`
- **THEN** the row is NOT deleted

#### Scenario: Self-heal batched into a single DELETE per replay
- **WHEN** replay encounters 50 stale `MV=0 cost>0` rows across the range
- **THEN** a single bulk DELETE statement is issued at end-of-replay (not 50 separate round-trips)

### Requirement: Active-date helper exposes calendar-inclusive holding intervals

`compute_active_dates` SHALL accept an optional `include_non_trading: bool = False` parameter. When `True`, the returned set includes every calendar date (Mon-Sun) inside each held interval, not just weekdays. When `False` (default), behaviour is unchanged.

#### Scenario: include_non_trading=True returns weekend dates inside intervals
- **WHEN** the user holds shares from `2024-09-13` (Friday) through `2024-09-16` (Monday) and the helper is called with `include_non_trading=True`
- **THEN** the returned set includes `2024-09-14` (Sat) and `2024-09-15` (Sun)

#### Scenario: Default call shape unchanged
- **WHEN** the helper is called without `include_non_trading`
- **THEN** the returned set contains only weekdays in held intervals, identical to today's behaviour

### Requirement: Whole-market fetch rejects under-baseline partial responses

For each whole-market Phase 1 fetch (TWSE `MI_INDEX` or TPEx daily), the system SHALL compare the fetched row count against a per-source rolling-30-day median computed from `price_history` and SHALL skip the upsert when the fetched count falls below a configured ratio (default 0.8) of that median.

#### Scenario: Full response passes the gate
- **WHEN** TWSE returns 1350 rows for a date and the rolling 30-day TWSE median is 1300
- **THEN** the gate computes ratio 1350/1300 ≈ 1.04 ≥ 0.8 and the system proceeds with the normal upsert into `price_history`

#### Scenario: Partial response is rejected
- **WHEN** TWSE returns 400 rows for a date and the rolling 30-day TWSE median is 1300
- **THEN** the gate computes ratio 400/1300 ≈ 0.31 < 0.8, no rows are inserted into `price_history` for that (source, date), and a warning log `phase1.partial_fetch_skipped` is emitted with fields `source=TWSE`, `date=<date>`, `fetched_rows=400`, `baseline_median=1300`, `ratio=0.31`

#### Scenario: Per-source independence
- **WHEN** the TWSE fetch for a date is classified partial but the TPEx fetch for the same date passes its own baseline check
- **THEN** the TPEx rows for that date SHALL be upserted normally and only the TWSE source SHALL be skipped

#### Scenario: Cold-start skips the check
- **WHEN** the `price_history` table has fewer than 10 prior trading days of rows for the source under evaluation
- **THEN** the gate SHALL NOT reject the response, the system SHALL upsert all fetched rows, and a single info log `phase1.partial_check_skipped_cold_start` SHALL be emitted with the source and date

#### Scenario: Empty response is not classified partial
- **WHEN** TWSE returns 0 rows for a weekday date after the existing empty-response retry has exhausted
- **THEN** the partial-fetch gate SHALL NOT run and the existing holiday-skip path SHALL handle the date (no `price_history` insert, no `phase1.partial_fetch_skipped` log)

#### Scenario: Baseline excludes the current date
- **WHEN** the baseline query runs for a fetch on date `D`
- **THEN** the SQL `WHERE` clause SHALL filter `date < D` so that the in-progress date never contributes to its own median

