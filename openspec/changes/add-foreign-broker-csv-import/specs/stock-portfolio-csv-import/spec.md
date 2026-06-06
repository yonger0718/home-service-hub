## ADDED Requirements

### Requirement: CSV import dispatcher sniffs broker format from header

The `POST /api/portfolio/imports/csv` endpoint SHALL sniff the first lines of the upload and route to the matching broker parser without requiring the client to specify the broker. The dispatcher SHALL detect:

- IB: first line begins with `Statement,Header,域名稱,域值`
- Firstrade: header row contains both `交易類別` and `代號`
- Schwab: header is `"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"`

Any upload whose header does not match a broker signature SHALL fall through to the existing manual CSV path with no behaviour change. The dispatcher SHALL stamp `transactions.broker` per row with the matching broker enum value (`IB`, `FIRSTRADE`, `SCHWAB`); the manual path SHALL stamp `TW_MANUAL` by default and SHALL accept a client-supplied broker override.

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

## MODIFIED Requirements

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
