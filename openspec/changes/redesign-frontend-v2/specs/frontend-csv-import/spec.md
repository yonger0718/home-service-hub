## MODIFIED Requirements

### Requirement: Two-step import card with auto-detected format

The import screen at `/portfolio/import` SHALL render a three-step indicator (選擇檔案 → 預覽與確認 → 匯入並重算) and, below it, cards in this order: **檔案** (a 匯入類型 `SegToggle` 交易 / 股利 / 券商對帳單, the 首列為欄位名稱 checkbox, and a dropzone that on selection becomes a `FileChip` with filename, row count, the detected format sourced from the backend response, 更換檔案 and remove), **預覽** (count tags 解析 N, 重複 N, 錯誤 N and a preview grid of date, stock, buy/sell tag, quantity, price and result — 新增, 重複 · 略過 (dimmed) or 代號待確認), **需要修正的列** (one line per row error: row index, message, and that the row will be skipped), **確認股票代號** (one block per unmatched name: occurrences and dates, code input, 驗證代號 result, 確認使用此代號 checkbox), and a footer with 取消, 重新預覽 and 確認匯入 N 筆. The type 券商對帳單 SHALL route to the broker dispatcher (`frontend-broker-import`) and its preview SHALL add the cash-flow table. A broker-format selector MUST NOT be rendered, because format is detected from file content.

#### Scenario: Selecting a file advances to preview
- **WHEN** the user selects a CSV file with type 交易
- **THEN** a `FileChip` appears with the file name and row count
- **AND** the detected format shows `國泰證券` or `通用 CSV`
- **AND** the preview lists the parsed rows with buy/sell tags and per-row results

#### Scenario: Confirm button shows parsed count
- **GIVEN** 41 rows parsed of which 3 are duplicates and 1 row has an error
- **WHEN** the preview renders
- **THEN** the confirm button label is `確認匯入 38 筆`

#### Scenario: Cancel resets the card
- **WHEN** the user clicks 取消 after selecting a file
- **THEN** the file chip, detected format, preview, errors and symbol blocks clear

## ADDED Requirements

### Requirement: Last failed import banner

When the latest import chain reported by `GET /api/portfolio/imports/recalc/status` failed, or the last import attempt made on this device failed, the import screen SHALL show an alert banner at the top: 最近一次匯入失敗 with time, file name and the reason, and, for a failed attempt, state that the whole batch was not written. A failed recalculation after a commit SHALL show its own banner (最近一次匯入的重算失敗) without the nothing-written statement, because rows may have been written. The banner SHALL disappear after the next successful import. The same condition drives the 匯入 navigation dot.

#### Scenario: Banner after a failed attempt
- **GIVEN** the last attempt on this device failed with one row error
- **WHEN** the owner opens `/portfolio/import`
- **THEN** the banner SHALL name the file and the error count
- **AND** the 匯入 navigation item SHALL show a dot

### Requirement: Post-import panel and recent imports

At ≥ 1024px a side column SHALL explain what runs after an import (重算持股與損益, 抓取除權息事件 — recorded as pending dividends to confirm on the dividends page, 更新報價 without dividends) and list the most recent import attempts made on this device (file name, time, result, counts) with a green or red status dot. Below 1024px the column SHALL move under the footer.

#### Scenario: Recent attempts listed
- **GIVEN** three import attempts were made on this device
- **WHEN** the side column renders
- **THEN** the three attempts SHALL be listed newest first with their result

## Implementation notes

- One page serves `/portfolio/import?type=transactions|dividends|broker`; the old `/portfolio/import-broker` redirects to `type=broker`.
- The preview rows carry a per-row `status` from the API (`new | duplicate | rehash | unresolved | error`) on both dry-run and commit responses. 新增 / 重複 · 略過 / 更新 / 代號待確認 / 錯誤 render from it; `重複 · 略過` rows are dimmed and excluded from `確認匯入 N`. When `status` is absent (older response) the UI falls back to 新增 / 代號待確認 / 錯誤 and never invents 重複 or 更新.
- `rehash` is Cathay-only: an existing trade re-fingerprinted in place (fingerprint, side, day-trade marker and cash legs refreshed, no insert). Such rows show a neutral 更新 tag with the hint 既有交易原地更新：指紋、多空、當沖標記與現金腳, are not dimmed, are excluded from `確認匯入 N`, and are counted in an extra 更新 count tag shown only when it is above 0.
- Count tags follow the statuses: 解析 − 重複 − 更新 = N. On a Cathay statement #duplicate equals `would_skip_duplicate` (dry run) / `skipped_duplicates` (commit) and #rehash equals `would_rehash` / `rehashed`; without statuses 更新 reads those counters.
- Dry-run statuses inherit three gaps: the generic dry run skips the ledger guard, the broker dry run does not catch an in-file repeated cash flow, and the Cathay dry run marks rows even when parser errors will reject the batch. The 需要修正的列 list is shown alongside.
- Banner split: the failed-attempt banner says the batch was not written; the recalc-failed banner does not. A partial commit (`created > 0` with row errors) counts as a success.
- Recent imports are device-local (`localStorage` key `hh-import-history`, 10 entries) and drive the 匯入 navigation dot together with the recalc state; dry-runs are not recorded.
- The side column renders only at ≥ 1024 px (`panes`); otherwise both cards sit under the footer.
- After a clean commit the file is cleared but the result and step 3 stay visible. The recalc panel is the step-3 card.
- 確認匯入 stays enabled with pending symbol overrides; the `尚有代號未驗證` dialog still submits.
