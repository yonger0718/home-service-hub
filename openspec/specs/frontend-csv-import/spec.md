# frontend-csv-import Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Two-step import card with auto-detected format

The CSV import screen SHALL render a single `.imp-card` containing two stepped sections: (1) dropzone that on file drop or selection renders a `FileChip` (filename + parsed row count) plus a detected-format chip (`國泰證券` or `通用 CSV`) sourced from the backend response, (2) a `.preview-table` of parsed rows including buy/sell tags. The card footer MUST contain 取消 and 確認匯入 N 筆 buttons. A broker-format selector MUST NOT be rendered, because the backend (`detect_csv_format`) determines format from file content and no other broker parsers exist.

#### Scenario: Selecting a file advances to preview
- **WHEN** the user selects a CSV file
- **THEN** a `FileChip` appears with the file name and parsed row count
- **AND** a detected-format chip shows `國泰證券` (for Cathay preamble) or `通用 CSV`
- **AND** the `.preview-table` lists the parsed rows with buy/sell tags

#### Scenario: Confirm button shows parsed count
- **WHEN** N rows are parsed
- **THEN** the confirm button label is `確認匯入 N 筆`

#### Scenario: Cancel resets the card
- **WHEN** the user clicks 取消 after selecting a file
- **THEN** the file chip, detected-format chip, and preview table clear

### Requirement: Backend returns detected CSV format

The `/api/portfolio/imports/transactions` response payload SHALL include a `csv_format` field whose value is `cathay` or `generic`, reflecting `detect_csv_format()` result. The frontend uses this field to render the detected-format chip.

#### Scenario: Cathay CSV detection surfaces in response
- **WHEN** a CSV with `根據您篩選的結果` preamble is uploaded
- **THEN** the response includes `"csv_format": "cathay"`

#### Scenario: Generic CSV detection surfaces in response
- **WHEN** a CSV without Cathay preamble is uploaded
- **THEN** the response includes `"csv_format": "generic"`

### Requirement: Wired to existing importer

The import flow SHALL call the existing portfolio import service on confirm and display a success or error toast.

#### Scenario: Import success
- **WHEN** the importer succeeds with N rows
- **THEN** the card resets and a success toast displays the count
