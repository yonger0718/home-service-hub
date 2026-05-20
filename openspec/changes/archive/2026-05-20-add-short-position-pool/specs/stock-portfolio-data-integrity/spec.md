## ADDED Requirements

### Requirement: Transactions carry a position_side discriminator

The `transactions` table SHALL include a non-null `position_side` column with enum values `LONG` and `SHORT`. Existing rows SHALL be backfilled to `LONG` by migration. New rows SHALL default to `LONG` if the source does not specify otherwise.

#### Scenario: Migration backfills existing rows to LONG

- **WHEN** the Alembic migration adding `position_side` runs forward against a database containing pre-existing transaction rows
- **THEN** every existing row SHALL have `position_side='LONG'` after the migration completes
- **AND** the column SHALL be `NOT NULL`

#### Scenario: Migration is reversible

- **WHEN** Alembic upgrades and downgrades the `position_side` migration
- **THEN** both directions SHALL complete without dropping valid existing transaction data

#### Scenario: Manual API insert without position_side defaults to LONG

- **WHEN** a client posts a transaction payload omitting `position_side`
- **THEN** the persisted row SHALL have `position_side='LONG'`

## MODIFIED Requirements

### Requirement: SELL transactions cannot exceed available holdings

The service SHALL prevent the LONG-pool ledger from going negative for a symbol. Short selling (`position_side='SHORT'` + `type='SELL'`) is supported via the short pool and SHALL bypass the long-ledger non-negativity check. SHORT BUY (cover) SHALL similarly be constrained: cumulative short-cover quantity SHALL NOT exceed the cumulative short-open quantity for the same symbol at that point in ledger order.

#### Scenario: Reject long sell without long holdings

- **WHEN** a client creates a `LONG SELL` transaction for a symbol with no prior available long holdings
- **THEN** the API SHALL return HTTP 400 and SHALL NOT create the transaction

#### Scenario: Accept short sell without prior long holdings

- **WHEN** a client creates a `SHORT SELL` transaction for a symbol with no prior holdings of any kind
- **THEN** the API SHALL create the transaction and the short pool SHALL reflect the new open short position

#### Scenario: Reject short cover greater than open short

- **WHEN** a client creates a `SHORT BUY` transaction whose quantity exceeds the symbol's open short quantity at that point in ledger order
- **THEN** the API SHALL return HTTP 400 and SHALL NOT create the transaction

#### Scenario: Accept valid partial long sell

- **WHEN** a client creates a `LONG SELL` transaction whose quantity is less than or equal to long holdings
- **THEN** the API SHALL create the transaction and portfolio summary SHALL reflect the reduced long holding

#### Scenario: Reject update that creates long oversell

- **WHEN** a client updates an existing transaction and the resulting ledger would make long holdings negative for any affected symbol
- **THEN** the API SHALL return HTTP 400 and SHALL preserve the previous stored transaction

#### Scenario: Same-day transaction ordering is deterministic

- **WHEN** multiple transactions for the same symbol share the same `trade_date`
- **THEN** validation SHALL use deterministic ordering based on `(trade_date, id)` for persisted rows

#### Scenario: Broker CSV import bypasses ledger guard for short opens

- **WHEN** the Cathay rehash / insert path inserts a `SHORT SELL` row for a symbol with no prior holdings
- **THEN** the insert SHALL succeed (the existing broker-import bypass of `validate_holdings_before_sell` extends to short opens)
