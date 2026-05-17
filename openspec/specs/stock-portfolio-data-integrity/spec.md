# stock-portfolio-data-integrity Specification

## Purpose
TBD - created by archiving change improve-stock-portfolio-service-reliability. Update Purpose after archive.
## Requirements
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

### Requirement: CSV transaction import fingerprint disambiguates distinct fills via optional order_id

CSV-imported transactions SHALL be deduplicated via `transactions.import_fingerprint`, a SHA256 over a canonical representation of the row. The canonical representation SHALL include an optional per-order identifier (`order_id`) when supplied by the source CSV, so that two otherwise-identical fills on the same day with different `order_id` values produce different fingerprints and both rows are inserted.

The `order_id` source column SHALL be recognised under canonical English (`order_id`) and Traditional Chinese synonyms (`委託書號`, `訂單編號`, `委託編號`). The column SHALL be optional: rows without it SHALL produce the same fingerprint as the pre-feature implementation (no `order_id` segment included in the canonical string).

#### Scenario: Identical same-day fills with distinct order_ids both import

- **GIVEN** two transaction CSV rows with identical `symbol`, `type`, `quantity`, `price`, `trade_date`, `fee`, `tax` but distinct non-empty `order_id` values
- **WHEN** the CSV is committed (not dry-run)
- **THEN** both rows SHALL be inserted as separate transactions with different `import_fingerprint` values

#### Scenario: Identical same-day fills without order_id collide (documented limitation)

- **GIVEN** two transaction CSV rows with identical fingerprint-input columns and no `order_id` column or empty `order_id` cells
- **WHEN** the CSV is committed
- **THEN** the second row SHALL be reported as a duplicate and skipped
- **AND** the import result SHALL still surface `skipped_duplicates >= 1` so the user can detect the collision

#### Scenario: Re-uploading the same CSV with order_ids dedupes cleanly

- **GIVEN** a transaction CSV with `order_id` values has been committed successfully
- **WHEN** the same CSV file is uploaded again
- **THEN** every row SHALL be reported as a duplicate (`created == 0`, `skipped_duplicates == len(rows)`)

#### Scenario: Pre-feature CSVs without order_id produce hashes identical to legacy

- **GIVEN** a transaction CSV with no `order_id` column at all
- **WHEN** rows are parsed under the new code
- **THEN** the computed `import_fingerprint` for each row SHALL equal the fingerprint that the prior implementation would have produced for the same row

#### Scenario: Mixed rows — some with order_id, some without — each get their own hash

- **GIVEN** a transaction CSV where row A has `order_id='OD-1'` and row B has the same fingerprint-input columns but no `order_id`
- **WHEN** the CSV is committed
- **THEN** both rows SHALL be inserted, because row A's fingerprint includes the `order_id` segment and row B's fingerprint does not

#### Scenario: Chinese-named order-id column is recognised

- **GIVEN** a transaction CSV whose header includes `委託書號`
- **WHEN** the parser normalises the header
- **THEN** `委託書號` SHALL be mapped to the canonical `order_id` and used in fingerprint computation

#### Scenario: Whitespace-only order_id treated as empty

- **GIVEN** a CSV row with `order_id='   '` (whitespace only)
- **WHEN** the row is parsed
- **THEN** the fingerprint SHALL be computed as if `order_id` were absent (legacy hash format)

