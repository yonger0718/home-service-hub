## ADDED Requirements

### Requirement: Cash CRUD rewrites snapshots across affected date range

When `create_manual_cash_transaction` or `delete_manual_cash_transaction` commits a change, the system SHALL rewrite every `portfolio_snapshot` row from `min(txn_date, today)` through `today` inclusive, using `portfolio_snapshot_service.refresh_snapshot_cash_range`. The deleted transaction's `txn_date` SHALL be captured before the delete commit.

A snapshot-refresh failure SHALL roll back via the existing exception-handling path so the cash-side commit remains intact and the failure is logged (already established in PR #23 and unchanged).

#### Scenario: Create with today's date refreshes only today

- **GIVEN** today is `2026-06-04` and the user creates a cash transaction with `txn_date = 2026-06-04`
- **WHEN** the create commit succeeds
- **THEN** `refresh_snapshot_cash_range` is called with `start_date = end_date = 2026-06-04`
- **AND** only today's snapshot row is upserted

#### Scenario: Backdated create refreshes range to today

- **GIVEN** today is `2026-06-04` and the user creates a cash transaction with `txn_date = 2025-12-31`
- **WHEN** the create commit succeeds
- **THEN** `refresh_snapshot_cash_range` is called with `start_date = 2025-12-31` and `end_date = 2026-06-04`
- **AND** every existing `portfolio_snapshot` row between those dates is upserted with the recomputed `total_cash_twd`

#### Scenario: Backdated delete refreshes range using captured txn_date

- **GIVEN** today is `2026-06-04` and a cash transaction with `txn_date = 2025-08-15` exists
- **WHEN** the user deletes it and the delete commit succeeds
- **THEN** the row's `txn_date = 2025-08-15` was captured into a local variable before commit
- **AND** `refresh_snapshot_cash_range` is called with `start_date = 2025-08-15` and `end_date = 2026-06-04`

#### Scenario: Future-dated create clamps end_date to today

- **GIVEN** today is `2026-06-04` and the user creates a cash transaction with `txn_date = 2026-07-01`
- **WHEN** the create commit succeeds
- **THEN** `refresh_snapshot_cash_range` is called with `start_date = 2026-06-04` and `end_date = 2026-06-04`
- **AND** future-dated rows are not written (their balance derivation excludes future txns until that date arrives)

#### Scenario: Range-refresh failure rolls back snapshot writes but preserves cash commit

- **GIVEN** the cash-side create commit succeeded
- **WHEN** `refresh_snapshot_cash_range` raises an exception mid-range
- **THEN** the session is rolled back per the existing guard
- **AND** the failure is logged at WARN with the date range and exception
- **AND** the cash transaction itself is NOT removed (it was committed in the prior step)
