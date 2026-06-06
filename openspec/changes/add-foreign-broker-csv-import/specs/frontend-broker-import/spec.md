## ADDED Requirements

### Requirement: Broker CSV import page surfaces dispatcher routing and dry-run preview

The Angular app SHALL expose a route `/portfolio/import-broker` rendering a standalone component that lets the user pick a CSV file, sends it to `POST /api/portfolio/imports/csv` with `dry_run=true`, and renders the parser response (detected broker, parsed transactions table, parsed cash flows table, row-indexed errors). A `Commit` action SHALL re-POST the same payload with `dry_run=false` and SHALL show created / skipped (duplicate) / rejected counts.

#### Scenario: Dry-run renders parsed rows without writing
- **WHEN** the user picks an FT CSV and clicks Preview
- **THEN** the page SHALL display the detected broker as `FIRSTRADE`, a table of parsed BUY/SELL rows, a table of parsed cash flows (deposit/interest), and SHALL NOT show a "rows created" count

#### Scenario: Commit creates rows and shows counts
- **WHEN** the user clicks Commit after a successful preview
- **THEN** the page SHALL POST `dry_run=false`, render `{created, skipped, rejected}` counts, and SHALL clear the file input on success

#### Scenario: Row-indexed errors surface from the parser
- **WHEN** the parser returns `{"errors": [{"row_index": 3, "reason": "missing FX rate for 2026-06-02 USD"}]}`
- **THEN** the page SHALL render the error list with row index + reason, and SHALL NOT clear the file input

#### Scenario: Unknown header falls through to manual path with warning
- **WHEN** the upload's first row does not match any broker signature
- **THEN** the page SHALL surface the detected broker as `manual` (or `null`) and SHALL warn the user that the file will be parsed as a generic manual CSV
