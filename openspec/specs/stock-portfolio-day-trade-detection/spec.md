# stock-portfolio-day-trade-detection Specification

## Purpose
TBD - created by archiving change merge-stonk-portfolio-features. Update Purpose after archive.
## Requirements
### Requirement: Same-symbol same-date BUY and SELL are flagged as day trades

The service SHALL set `is_day_trade=true` on every transaction whose `(symbol, calendar_date)` bucket contains at least one BUY and at least one SELL, and SHALL clear the flag when that condition no longer holds.

#### Scenario: Second leg arrives — both rows flip true
- **GIVEN** a BUY for symbol `S` on date `D` exists with `is_day_trade=false`
- **WHEN** a SELL for the same symbol on the same calendar date is created
- **THEN** both the existing BUY and the new SELL SHALL have `is_day_trade=true` after commit

#### Scenario: Different calendar date — flag stays false
- **GIVEN** a BUY for symbol `S` on date `D` exists with `is_day_trade=false`
- **WHEN** a SELL for the same symbol on date `D+1` is created
- **THEN** both transactions SHALL have `is_day_trade=false`

#### Scenario: Delete clears the flag
- **GIVEN** a BUY and a SELL for symbol `S` on date `D` both flagged `is_day_trade=true`
- **WHEN** the SELL is deleted
- **THEN** the remaining BUY SHALL have `is_day_trade=false`

#### Scenario: Update moves a row to a new bucket
- **GIVEN** a BUY for symbol `S` on date `D` exists with `is_day_trade=true` (paired with a SELL on `D`)
- **WHEN** the BUY is updated to date `D+1`
- **THEN** the SELL on `D` SHALL have `is_day_trade=false` and the updated BUY on `D+1` SHALL be evaluated against the `D+1` bucket

#### Scenario: Calendar date uses UTC for the active TW market window
- **WHEN** a transaction's `trade_date` is stored
- **THEN** the bucket key SHALL be the UTC calendar date of `trade_date`, which aligns with TW market hours (01:00–05:30 UTC)

### Requirement: `is_day_trade` is server-derived, not client-supplied

The API SHALL NOT accept `is_day_trade` on transaction create or update requests. The flag SHALL be derived from the ledger and SHALL only appear on transaction responses.

#### Scenario: Client cannot set the flag directly
- **WHEN** a client submits a transaction payload containing `is_day_trade`
- **THEN** the field SHALL be ignored and the persisted value SHALL be derived from the ledger

