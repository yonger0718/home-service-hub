## MODIFIED Requirements

### Requirement: Two-step import card with auto-detected format

The import screen at `/portfolio/import` SHALL render a three-step indicator (選擇檔案 → 預覽與確認 → 匯入並重算) and, below it, cards in this order: **檔案** (a 匯入類型 `SegToggle` 交易 / 股利 / 券商對帳單, the 首列為欄位名稱 switch (`role="switch"`), and a dropzone that on selection becomes a `FileChip` with filename, row count, the detected format sourced from the backend response, 更換檔案 and remove), **預覽** (count tags 解析 N, 重複 N, 錯誤 N and a preview grid of date, stock, buy/sell tag, quantity, price and result — 新增, 重複 · 略過 (dimmed) or 代號待確認), **需要修正的列** (one line per row error: row index, message, and that the row will be skipped), **確認股票代號** (one block per unmatched name: occurrences and dates, code input, 驗證代號 result, 確認使用此代號 checkbox), and a footer with 取消, 重新預覽 and 確認匯入 N 筆, rendered only once a file is chosen. The type 券商對帳單 SHALL route to the broker dispatcher (`frontend-broker-import`) and its preview SHALL add the cash-flow table. A broker-format selector MUST NOT be rendered, because format is detected from file content.

#### Scenario: Fresh import screen
- **WHEN** `/portfolio/import` opens with no file chosen
- **THEN** no 確認匯入 button SHALL render

#### Scenario: Selecting a file advances to preview
- **WHEN** the user selects a CSV file with type 交易
- **THEN** a `FileChip` appears with the file name and row count
- **AND** the detected format shows `國泰證券` or `通用 CSV`
- **AND** the preview lists the parsed rows with buy/sell tags and per-row results
- **AND** the footer renders with 確認匯入 and the parsed count

#### Scenario: Confirm button shows parsed count
- **GIVEN** 41 rows parsed of which 3 are duplicates and 1 row has an error
- **WHEN** the preview renders
- **THEN** the confirm button label is `確認匯入 38 筆`

#### Scenario: Cancel resets the card
- **WHEN** the user clicks 取消 after selecting a file
- **THEN** the file chip, detected format, preview, errors and symbol blocks clear
