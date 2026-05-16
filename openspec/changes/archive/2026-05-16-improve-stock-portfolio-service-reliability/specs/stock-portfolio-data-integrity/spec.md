## ADDED Requirements

### Requirement: Transaction and dividend inputs are validated at API boundary

The service SHALL reject portfolio transaction and dividend create/update requests with invalid numeric values or blank symbols before writing to the database.

#### Scenario: Reject invalid transaction payload
- **WHEN** a client submits a transaction with blank `symbol`, `quantity <= 0`, `price <= 0`, `fee < 0`, or `tax < 0`
- **THEN** the API SHALL return a client error and SHALL NOT create or update the transaction

#### Scenario: Reject invalid dividend payload
- **WHEN** a client submits a dividend with blank `symbol` or `amount <= 0`
- **THEN** the API SHALL return a client error and SHALL NOT create or update the dividend

#### Scenario: Normalize symbol input
- **WHEN** a client submits a symbol with leading/trailing whitespace or a `.TW` / `.TWO` suffix
- **THEN** the service SHALL store the normalized symbol used by existing portfolio calculations

### Requirement: Database constraints protect portfolio invariants

The database schema SHALL enforce the same core portfolio data invariants as the API layer for transactions and dividends.

#### Scenario: Direct write violates transaction invariant
- **WHEN** a direct database write attempts to insert or update a transaction with blank `symbol`, `quantity <= 0`, `price <= 0`, `fee < 0`, or `tax < 0`
- **THEN** the database SHALL reject the write through check constraints

#### Scenario: Direct write violates dividend invariant
- **WHEN** a direct database write attempts to insert or update a dividend with blank `symbol` or `amount <= 0`
- **THEN** the database SHALL reject the write through check constraints

#### Scenario: Migration is reversible
- **WHEN** Alembic upgrades and downgrades the portfolio constraint migration
- **THEN** both directions SHALL complete without dropping valid existing transaction or dividend data

### Requirement: SELL transactions cannot exceed available holdings

The service SHALL prevent transaction ledgers from going negative for a symbol. Short selling is not supported.

#### Scenario: Reject sell without holdings
- **WHEN** a client creates a SELL transaction for a symbol with no prior available holdings
- **THEN** the API SHALL return HTTP 400 and SHALL NOT create the transaction

#### Scenario: Reject sell greater than available shares
- **WHEN** a client creates a SELL transaction whose quantity exceeds the symbol's available holdings at that point in ledger order
- **THEN** the API SHALL return HTTP 400 and SHALL NOT create the transaction

#### Scenario: Accept valid partial sell
- **WHEN** a client creates a SELL transaction whose quantity is less than or equal to available holdings
- **THEN** the API SHALL create the transaction and portfolio summary SHALL reflect the reduced holding

#### Scenario: Reject update that creates oversell
- **WHEN** a client updates an existing transaction and the resulting ledger would make holdings negative for any affected symbol
- **THEN** the API SHALL return HTTP 400 and SHALL preserve the previous stored transaction

#### Scenario: Same-day transaction ordering is deterministic
- **WHEN** multiple transactions for the same symbol share the same `trade_date`
- **THEN** validation SHALL use deterministic ordering based on `(trade_date, id)` for persisted rows

### Requirement: Update endpoints preserve omitted optional fields

Transaction and dividend update paths SHALL NOT overwrite optional stored fields with `None` merely because the client omitted those fields from the request payload.

#### Scenario: Omitted transaction trade date is preserved
- **WHEN** an existing transaction has `trade_date` set and a client updates another field without sending `trade_date`
- **THEN** the stored `trade_date` SHALL remain unchanged

#### Scenario: Omitted dividend received date is preserved
- **WHEN** an existing dividend has `received_date` set and a client updates another field without sending `received_date`
- **THEN** the stored `received_date` SHALL remain unchanged

### Requirement: Portfolio schemas use supported Pydantic v2 model config

Response schemas that read from ORM objects SHALL use Pydantic v2 `ConfigDict` rather than deprecated class-based `Config`.

#### Scenario: Tests run without Pydantic config deprecation warnings
- **WHEN** the stock portfolio service test suite runs
- **THEN** it SHALL NOT emit Pydantic class-based `Config` deprecation warnings from portfolio schemas
