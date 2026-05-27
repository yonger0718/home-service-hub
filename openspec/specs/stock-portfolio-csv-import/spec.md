# stock-portfolio-csv-import Specification

## Purpose
TBD - created by archiving change merge-stonk-portfolio-features. Update Purpose after archive.
## Requirements
### Requirement: Service supports CSV bulk import for transactions and dividends

The service SHALL accept multipart CSV uploads to bulk-create transactions and dividends, with a dry-run preview mode and a commit mode.

#### Scenario: Upload transactions CSV in dry-run mode
- **WHEN** a client POSTs a transactions CSV to `/api/portfolio/imports/transactions?dry_run=true`
- **THEN** the API SHALL parse and validate every row, SHALL return a preview of parsed rows and a row-indexed error list, and SHALL NOT write any transactions to the database

#### Scenario: Upload transactions CSV in commit mode
- **WHEN** a client POSTs a transactions CSV to `/api/portfolio/imports/transactions?dry_run=false`
- **THEN** the API SHALL write every valid row that does not duplicate an existing fingerprint and SHALL return counts for created, skipped (duplicate), and rejected (invalid) rows

#### Scenario: Reject CSV with wrong header
- **WHEN** the uploaded CSV does not match the canonical column order for its kind
- **THEN** the API SHALL return a client error and SHALL NOT write any rows

#### Scenario: Reject CSV larger than the size cap
- **WHEN** the uploaded file exceeds 5 MiB
- **THEN** the API SHALL return a client error before parsing

#### Scenario: Per-row validation rejects bad data without aborting the import
- **WHEN** a CSV contains a mix of valid and invalid rows
- **THEN** the API SHALL accept the valid rows in commit mode, report the invalid rows in the error list with row indexes, and SHALL NOT raise a 500

### Requirement: CSV imports are idempotent via SHA256 row fingerprint

The service SHALL stamp a SHA256 `import_fingerprint` on every imported transaction and dividend and SHALL reject duplicate rows.

#### Scenario: Repeat upload of the same CSV creates no new rows
- **WHEN** the same CSV is uploaded twice in commit mode
- **THEN** the second upload SHALL produce zero new transactions or dividends and SHALL report the matching rows as skipped duplicates

#### Scenario: Duplicate rows within a single CSV are deduplicated
- **WHEN** a single CSV contains two rows with identical canonical fields
- **THEN** the importer SHALL persist only one row and report the other as a skipped duplicate

#### Scenario: Whitespace differences do not change fingerprint
- **WHEN** two rows differ only in leading/trailing whitespace or thousands separators
- **THEN** they SHALL produce the same fingerprint and the second SHALL be deduplicated

#### Scenario: Meaningful value differences do change fingerprint
- **WHEN** two rows differ in price, quantity, fee, tax, or date
- **THEN** they SHALL produce different fingerprints and both SHALL be persistable

