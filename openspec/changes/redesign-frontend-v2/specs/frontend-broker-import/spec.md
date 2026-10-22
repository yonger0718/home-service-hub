## MODIFIED Requirements

### Requirement: Broker CSV import page surfaces dispatcher routing and dry-run preview

The broker statement import SHALL live on the unified import screen (`/portfolio/import`) as the 匯入類型 券商對帳單; `/portfolio/import-broker` SHALL redirect to `/portfolio/import?type=broker`. With that type selected, choosing a file SHALL send it to `POST /api/portfolio/imports/csv` with `dry_run=true` and render the parser response (detected broker, parsed transactions, parsed cash flows, row-indexed errors) in the unified preview. 確認匯入 SHALL re-POST the same payload with `dry_run=false` and SHALL show created / skipped (duplicate) / rejected counts.

#### Scenario: Old route lands on the broker type
- **WHEN** the owner opens `/portfolio/import-broker`
- **THEN** the unified import screen opens with 券商對帳單 selected

#### Scenario: Dry-run renders parsed rows without writing
- **WHEN** the user picks an FT CSV with type 券商對帳單
- **THEN** the page SHALL display the detected broker as `FIRSTRADE`, the parsed BUY/SELL rows and the parsed cash flows, and SHALL NOT show a "rows created" count

#### Scenario: Commit creates rows and shows counts
- **WHEN** the user confirms after a successful preview
- **THEN** the page SHALL POST `dry_run=false`, render `{created, skipped, rejected}` counts, and SHALL clear the file on success

#### Scenario: Row-indexed errors surface from the parser
- **WHEN** the parser returns `{"errors": [{"row_index": 3, "reason": "missing FX rate for 2026-06-02 USD"}]}`
- **THEN** the page SHALL list the error with row index and reason under 需要修正的列, and SHALL NOT clear the file

#### Scenario: Unknown header falls through to manual path with warning
- **WHEN** the upload's first row does not match any broker signature
- **THEN** the detected format SHALL read `manual` (or none) and the page SHALL warn that the file will be parsed as a generic manual CSV
