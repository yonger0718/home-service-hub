## ADDED Requirements

### Requirement: Backfill enumerates cash-only activity dates

`networth_backfill_service` SHALL include `cash_transaction.txn_date` distinct values in its snapshot-date enumeration, in addition to the existing stock-activity dates. The resulting snapshot-date set SHALL be the union of stock-activity dates and cash-activity dates. When the union is non-empty, a snapshot row SHALL be written/upserted for every date in the set, using the existing per-date `total_cash_twd` computation via `cash_account_service.get_total_balance_in(db, "TWD", asof=date)`.

When no stock activity exists at all but cash activity does, the backfill window SHALL start at `min(cash_transaction.txn_date)` instead of failing or no-opping.

#### Scenario: Cash-only date gets a snapshot row

- **GIVEN** the user has zero stock transactions and one cash deposit dated `2025-04-01`
- **WHEN** the operator runs `python -m app.services.networth_backfill_service --rebuild-all`
- **THEN** a `portfolio_snapshot` row exists with `snapshot_date = 2025-04-01`
- **AND** the row's `total_cash_twd` equals the cash balance as of `2025-04-01`
- **AND** the row's `total_market_value` is `0` (no holdings)

#### Scenario: Cash-only period after liquidation gets snapshot rows

- **GIVEN** the user sold all stock on `2025-09-30` and made cash withdrawals on `2025-10-15` and `2026-01-10`
- **WHEN** the operator runs `--rebuild-all`
- **THEN** snapshot rows exist for `2025-10-15` and `2026-01-10` even though those dates have no stock activity
- **AND** each row's `total_cash_twd` reflects the cash balance as of that date

#### Scenario: Mixed activity dates form a union

- **GIVEN** stock transactions exist on `[2025-01-15, 2025-06-20]` and cash transactions exist on `[2025-03-10, 2025-06-20, 2025-11-05]`
- **WHEN** the operator runs `--rebuild-all`
- **THEN** snapshot rows are written for `[2025-01-15, 2025-03-10, 2025-06-20, 2025-11-05]` (union of both sets, with `2025-06-20` deduplicated)

#### Scenario: Empty ledger is still a no-op

- **GIVEN** no stock transactions and no cash transactions exist
- **WHEN** the operator runs `--rebuild-all`
- **THEN** no `portfolio_snapshot` rows are written
- **AND** the run completes without error

#### Scenario: Dry-run does not write cash-only snapshot rows

- **GIVEN** the user has cash-only activity and runs `--rebuild-all --dry-run`
- **WHEN** the backfill completes
- **THEN** the planned date set logged includes cash-activity dates
- **AND** no rows are persisted (dry-run respects the cash-only enumeration just like it did stock-only)
