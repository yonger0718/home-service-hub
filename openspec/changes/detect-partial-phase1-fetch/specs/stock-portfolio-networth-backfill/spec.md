## ADDED Requirements

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
