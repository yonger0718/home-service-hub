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

