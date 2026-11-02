## MODIFIED Requirements

### Requirement: Accounts list page

The SPA SHALL provide the route `/accounting/accounts`. It SHALL list every non-archived account returned by `GET /api/accounting/accounts`, grouped by account group in group order, each group with a collapsible header showing its subtotal in the main currency. Each row SHALL show:

- its logo, and name: the uploaded image when `logo_hash` is set, else the bundled brand logo when `logo` is set, else its icon
- a second line: currency when not the main currency, and for credit accounts the available credit (`credit_limit + balance`, shared across a shared-limit set) and, when rules exist, the names and rates of enabled rules
- balance, formatted in the account's currency with grouping separators: TWD and JPY rounded to whole units (half away from zero), other currencies with up to 2 decimals; rounding is display-only
- for accounts whose `combined_account_id` points at a 主帳戶, nesting under that master's row, which shows a count badge and the sum of its own and its children's balances

Negative balances SHALL be visually distinguished. A header SHALL show the total of accounts with `include_in_total` in the main currency, plus assets and liabilities (the sums of positive and negative balances). A collapsed 封存 section SHALL list archived accounts. A "+" in the page header SHALL open the new-account form.

The page SHALL show the latest import's timestamp, status and the number of entries needing review, taken from `GET /api/accounting/imports/latest`.

#### Scenario: Owner compares balances with MOZE
- **WHEN** the owner opens `/accounting/accounts` after an import
- **THEN** every account SHALL appear under its group with the balance computed by the backend, in its own currency

#### Scenario: No import yet
- **GIVEN** no import has run and no account exists
- **WHEN** the page loads
- **THEN** it SHALL show an empty state explaining that a MOZE backup import is required, with a link to the settings page

#### Scenario: Master account tree
- **GIVEN** three cards whose `combined_account_id` is `玉山信用卡`
- **WHEN** the page renders
- **THEN** `玉山信用卡` SHALL show a badge `3` and the three cards SHALL be indented beneath it

#### Scenario: Valuation balance
- **GIVEN** an account with `balance_kind: "valuation"` (a `valuation_source` is set, so `balance` is the portfolio's market total)
- **WHEN** the list, the account-settings hero, the account picker or the entry form's 當前餘額 shows its balance
- **THEN** a value SHALL be formatted like any balance and labelled `市值` (`市值（資料較舊）` when `stale` is true), a null `balance` SHALL read `市值暫不可得` in muted text (never red, never `0`) and that account SHALL be left out of the totals, and a 主帳戶 family containing it SHALL show no family total

#### Scenario: Brand logo in the list
- **GIVEN** an account with `logo` `cathay-united` and one with none
- **THEN** the first row SHALL render the bundled image (`assets/logos/…`) and no emoji; the second SHALL render its emoji
- **AND** an unknown slug or a failed image load SHALL render the emoji

#### Scenario: Uploaded logo wins over the bundled slug
- **GIVEN** an account with both `logo` `cathay-united` and a `logo_hash`
- **THEN** its row SHALL render the uploaded image (fetched with the scoped token, shown as a `blob:` URL) and not the bundled image
- **AND** a failed fetch SHALL fall back to the bundled image, then to the emoji

### Requirement: Account entry history page

The SPA SHALL provide the route `/accounting/accounts/:id`. It SHALL show a period navigator following the account's statement cycle (`closing_day`; calendar month when NULL), a summary of the period (new spend, income or rewards, period-end balance), and the account's entries newest first, showing for each entry:

- category icon and colour
- name (or category when empty), with merchant and tags on the second line
- amount, with the original-currency amount and rate inline beneath it when the entry was converted
- running balance, shown as a small line above the amount on every viewport (passbook style)
- project and kind pills (應收, 回饋, 轉帳, 退款)

It SHALL load more entries on demand using the paginated endpoint, SHALL filter by kind, date range and text, SHALL mark entries with `needs_review = true`, SHALL open the entry detail on tap, and SHALL link to the account settings page. Its header avatar SHALL be a button (aria-label 「更換 logo」) that opens a file picker; while uploading it SHALL show a busy state, and on success a toast.

#### Scenario: Unpaired transfer is flagged
- **GIVEN** an account has a transfer leg with `needs_review = true`
- **WHEN** its history is shown
- **THEN** that row SHALL carry a visible review marker

#### Scenario: Running balance on a phone
- **WHEN** the history is viewed at 390 px
- **THEN** every row SHALL show its running balance above the amount

#### Scenario: Upload from the account page
- **WHEN** the owner taps the header avatar and picks a valid image
- **THEN** the avatar SHALL show a busy state, then the new image, and a success toast SHALL appear

#### Scenario: Upload rejected
- **WHEN** the upload fails
- **THEN** the page SHALL show 「圖片太大（上限 5 MB）」 for 413, 「只接受 PNG、JPEG、WebP」 for `logo_format`, 「圖片尺寸不符（每邊 16–8192 px）」 for `logo_dimensions`, 「這個檔案無法當作圖片使用」 for `logo_unreadable` or `logo_empty`, and 「伺服器尚未支援上傳」 for a 404 on the PUT
- **AND** the previous logo SHALL remain

### Requirement: Account settings page

The SPA SHALL provide `/accounting/accounts/:id/settings` and `/accounting/accounts/new` as a settings-style list: the account's logo (uploaded image, else bundled brand logo, else its icon), and live balance at the top; rows 名稱, 主幣種, 帳戶分組, Logo (a block with a state line 「已上傳的圖片」, 「內建：<label>」 or 「無」, buttons 「上傳圖片」, 「移除圖片」 and 「選擇內建 logo」 that opens a grid of the bundled logo set with `無` first, and the hint 「上傳的圖片優先於內建 logo，上傳後立即生效」; on `/accounting/accounts/new` the upload is disabled with 「先建立帳戶，再上傳 logo」), 圖示, 初始金額, 帳單週期 (closing day with the computed current cycle), 信用帳戶 toggle revealing 繳款期限 (fixed day or days after closing, showing the computed due date), 信用額度, 額度共用 (multi-select of credit accounts), 主帳戶 (picker), 自動扣繳 (account picker); 紅利回饋 (read-only list of the account's rules in 2a); 國外交易手續費 toggle revealing %, rounding, refund flag; 納入總餘額; 封存帳戶; 刪除帳戶 (enabled only with no entries). 主幣種 SHALL be read-only once the account has entries. Saving calls the account write endpoints and validation errors SHALL be shown beside the field. An imported account whose settings were edited locally SHALL show a note "已自訂，匯入不再覆寫" with a 還原 MOZE 設定 action that clears the flag.

#### Scenario: Credit toggle reveals card fields
- **WHEN** 信用帳戶 is switched on
- **THEN** the 繳款期限, 信用額度, 額度共用, 主帳戶 and 自動扣繳 rows SHALL appear

#### Scenario: Due date preview
- **GIVEN** closing day 15 and 繳款期限 = 20 days after closing
- **WHEN** the row renders on 2026-10-02
- **THEN** it SHALL show the current cycle `09/16 – 10/15` and the due date `11/04`

#### Scenario: Pick a logo
- **WHEN** the owner opens the 品牌 logo grid and taps a logo
- **THEN** the grid SHALL close, the header SHALL show that logo's image, and saving SHALL send its slug as `logo`

#### Scenario: Clear the logo
- **GIVEN** an account with a logo and the emoji `💳`
- **WHEN** the owner taps `無` in the 品牌 logo grid and saves
- **THEN** `logo` SHALL be null, the header SHALL show the emoji, and `icon` SHALL be unchanged

#### Scenario: Image write keeps the dirty form
- **GIVEN** the owner has edited fields but not saved
- **WHEN** they upload or remove an image
- **THEN** the image SHALL change immediately and the unsaved edits SHALL be kept

#### Scenario: Upload on a new account
- **WHEN** the form is `/accounting/accounts/new`
- **THEN** 「上傳圖片」 SHALL be disabled and the hint 「先建立帳戶，再上傳 logo」 SHALL show
