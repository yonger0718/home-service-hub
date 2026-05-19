## ADDED Requirements

### Requirement: Holiday-only price_history rows must be removed

The `price_history` table SHALL NOT contain rows whose `date` is a known TW market holiday with no corresponding upstream OHLC data. Such rows shortcut the partial-fetch gate's `_existing_price_dates()` presence check and block legitimate future fetches.

#### Scenario: Known sentinel rows on TW holidays are deleted

- **WHEN** the operator runs `cleanup_historical_partial_dates.py --apply` against a database that contains rows where `(date, source)` ∈ {2026-04-03, 2026-04-06, 2026-05-01} × {TWSE, TPEx}
- **THEN** all matching rows SHALL be removed from `price_history`
- **AND** the post-delete count for those `(date, source)` pairs SHALL be zero

#### Scenario: Re-running the cleanup is a no-op

- **WHEN** the operator re-runs `cleanup_historical_partial_dates.py --apply` after the target rows have already been removed
- **THEN** the script SHALL exit with status 0
- **AND** the script SHALL report zero rows deleted
- **AND** no other rows in `price_history` SHALL be modified

#### Scenario: Dry run does not commit

- **WHEN** the operator runs `cleanup_historical_partial_dates.py` without `--apply`
- **THEN** the script SHALL print the rows that would be deleted
- **AND** the script SHALL NOT commit any deletion
- **AND** the row count in `price_history` for the target dates SHALL be unchanged

#### Scenario: Cleanup is scoped to the listed (date, source) pairs only

- **WHEN** the cleanup script runs against a database that contains additional `price_history` rows on other dates or for other sources
- **THEN** only rows matching the hardcoded `(date, source)` target list SHALL be deleted
- **AND** all other rows SHALL remain unchanged
