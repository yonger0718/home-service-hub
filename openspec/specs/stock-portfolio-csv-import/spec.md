# stock-portfolio-csv-import Specification

## Purpose
TBD - created by archiving change merge-stonk-portfolio-features. Update Purpose after archive.
## Requirements
### Requirement: Service supports CSV bulk import for transactions and dividends

The service SHALL accept multipart CSV uploads to bulk-create transactions and dividends, with a dry-run preview mode and a commit mode. The service SHALL accept both the canonical manual CSV format and broker-native CSV formats (IB, Firstrade, Schwab); the importer SHALL pick the right parser via the broker dispatcher and SHALL stamp `transactions.broker` per imported row.

#### Scenario: Upload transactions CSV in dry-run mode
- **WHEN** a client POSTs a transactions CSV to `/api/portfolio/imports/transactions?dry_run=true`
- **THEN** the API SHALL parse and validate every row, SHALL return a preview of parsed rows and a row-indexed error list, and SHALL NOT write any transactions to the database

#### Scenario: Upload transactions CSV in commit mode
- **WHEN** a client POSTs a transactions CSV to `/api/portfolio/imports/transactions?dry_run=false`
- **THEN** the API SHALL write every valid row that does not duplicate an existing fingerprint and SHALL return counts for created, skipped (duplicate), and rejected (invalid) rows

#### Scenario: Reject CSV with wrong header
- **WHEN** the uploaded CSV does not match the canonical column order for its kind AND does not match any broker signature
- **THEN** the API SHALL return a client error and SHALL NOT write any rows

#### Scenario: Reject CSV larger than the size cap
- **WHEN** the uploaded file exceeds 5 MiB
- **THEN** the API SHALL return a client error before parsing

#### Scenario: Per-row validation rejects bad data without aborting the import
- **WHEN** a CSV contains a mix of valid and invalid rows
- **THEN** the API SHALL accept the valid rows in commit mode, report the invalid rows in the error list with row indexes, and SHALL NOT raise a 500

#### Scenario: Broker upload stamps broker per row
- **WHEN** a client uploads an IB / Firstrade / Schwab CSV in commit mode
- **THEN** every resulting `transactions` row SHALL carry the matching `broker` value (`IB`, `FIRSTRADE`, `SCHWAB`)

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

### Requirement: CSV import dispatcher sniffs broker format from header

The `POST /api/portfolio/imports/csv` endpoint SHALL sniff the first lines of the upload and route to the matching broker parser without requiring the client to specify the broker. The dispatcher SHALL detect:

- IB: first line begins with `Statement,Header,域名稱,域值`
- Firstrade: header row contains both `交易類別` and `代號`
- Schwab: header is `"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"`

Any upload whose header does not match a broker signature SHALL fall through to the existing manual CSV path with no behaviour change. The dispatcher SHALL stamp `transactions.broker` per row with the matching broker enum value (`IB`, `FIRSTRADE`, `SCHWAB`); the manual path SHALL stamp `TW_MANUAL` by default. (Per-row or per-upload broker override is not part of this change — `transactions.broker` is set from the sniffed dispatcher result alone.)

#### Scenario: IB statement is routed to the IB parser
- **WHEN** a client uploads a CSV whose first line is `Statement,Header,域名稱,域值`
- **THEN** the dispatcher SHALL invoke the IB parser and SHALL NOT invoke the manual or Firstrade parsers

#### Scenario: Firstrade statement is routed to the Firstrade parser
- **WHEN** a client uploads a CSV whose first row contains both `交易類別` and `代號`
- **THEN** the dispatcher SHALL invoke the Firstrade parser

#### Scenario: Schwab statement is routed to the Schwab parser
- **WHEN** a client uploads a CSV whose first row is `"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"`
- **THEN** the dispatcher SHALL invoke the Schwab parser

#### Scenario: Unknown header falls back to manual CSV path
- **WHEN** a client uploads a CSV whose first row does not match any broker signature
- **THEN** the dispatcher SHALL hand the upload to the existing manual `import_service` path with no behaviour change

