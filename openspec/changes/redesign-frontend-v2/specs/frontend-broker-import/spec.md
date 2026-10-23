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

## Implementation notes

- The separate broker page is removed; 券商對帳單 is a type on the unified import page and hides the header checkbox and the symbol-confirmation card.
- The preview shows a transactions table, each row with a 結果 column from its `status` (新增 / 重複 · 略過 / 錯誤), and a 現金流 table (日期 / 類型 / 金額 / 幣別) with no 結果 column: a `duplicate` cash-flow row is dimmed only.
- On non-Cathay brokers `counts.skipped` equals the number of `duplicate` rows. On Cathay, rehash rows (existing trade re-fingerprinted in place, no insert) carry their own `rehash` status: they are labelled 更新, not dimmed, excluded from `確認匯入 N` and counted in a 更新 tag, so #duplicate again equals `would_skip_duplicate` (dry run) / `skipped_duplicates` (commit) and #rehash equals `would_rehash` / `rehashed`.
- A row-level FX error (for example a missing FX rate) is listed under 需要修正的列 and the file is kept.
