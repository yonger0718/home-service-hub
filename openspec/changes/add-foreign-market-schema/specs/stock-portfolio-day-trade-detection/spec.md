## MODIFIED Requirements

### Requirement: Same-symbol same-date BUY and SELL are flagged as day trades

The service SHALL set `is_day_trade=true` on every transaction whose `(symbol, calendar_date)` bucket contains at least one BUY and at least one SELL, AND SHALL clear the flag when that condition no longer holds. Detection SHALL be gated on `market='TW'`: transactions with any non-TW market SHALL always have `is_day_trade=false` regardless of same-day BUY+SELL pairing, because the day-trade concept (沖買/沖賣 with TW half-tax) is TWSE-specific and does not generalize to US or LSE markets.

#### Scenario: Second leg arrives — both TW rows flip true
- **GIVEN** a TW BUY for symbol `S` on date `D` exists with `is_day_trade=false`
- **WHEN** a TW SELL for the same symbol on the same calendar date is created
- **THEN** both the existing BUY and the new SELL SHALL have `is_day_trade=true` after commit

#### Scenario: Different calendar date — flag stays false
- **GIVEN** a TW BUY for symbol `S` on date `D` exists with `is_day_trade=false`
- **WHEN** a TW SELL for the same symbol on date `D+1` is created
- **THEN** both transactions SHALL have `is_day_trade=false`

#### Scenario: Delete clears the flag
- **GIVEN** a TW BUY and a TW SELL for symbol `S` on date `D` both flagged `is_day_trade=true`
- **WHEN** the SELL is deleted
- **THEN** the remaining BUY SHALL have `is_day_trade=false`

#### Scenario: Update moves a row to a new bucket
- **GIVEN** a TW BUY for symbol `S` on date `D` exists with `is_day_trade=true` (paired with a TW SELL on `D`)
- **WHEN** the BUY is updated to date `D+1`
- **THEN** the SELL on `D` SHALL have `is_day_trade=false` and the updated BUY on `D+1` SHALL be evaluated against the `D+1` bucket

#### Scenario: Calendar date uses UTC for the active TW market window
- **WHEN** a transaction's `trade_date` is stored
- **THEN** the bucket key SHALL be the UTC calendar date of `trade_date`, which aligns with TW market hours (01:00–05:30 UTC)

#### Scenario: Non-TW market is never day-trade flagged
- **GIVEN** a US BUY for symbol `AAPL` on date `D` and a US SELL for `AAPL` on the same date `D`
- **WHEN** day-trade detection runs across the ledger
- **THEN** both rows SHALL have `is_day_trade=false`

#### Scenario: Mixed-market same-day rows do not cross-pair
- **GIVEN** a TW BUY for symbol `S` on date `D` and a US SELL for the same string symbol `S` on date `D`
- **WHEN** day-trade detection runs
- **THEN** neither row SHALL flip to `is_day_trade=true`, because day-trade pairing is scoped within `market='TW'` and symbols belong to disjoint markets
