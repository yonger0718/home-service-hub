# frontend-accounting-ledger Specification

## Purpose
TBD - created by archiving change rebuild-accounting-moze-ledger. Update Purpose after archive.

## Requirements

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

It SHALL load more entries on demand using the paginated endpoint, SHALL filter by kind, date range and text, SHALL mark entries with `needs_review = true`, SHALL open the entry detail on tap, and SHALL link to the account settings page. Its header avatar SHALL be a button (aria-label 「更換 logo」) that opens a file picker; while uploading it SHALL show a busy state.

#### Scenario: Unpaired transfer is flagged
- **GIVEN** an account has a transfer leg with `needs_review = true`
- **WHEN** its history is shown
- **THEN** that row SHALL carry a visible review marker

#### Scenario: Running balance on a phone
- **WHEN** the history is viewed at 390 px
- **THEN** every row SHALL show its running balance above the amount

#### Scenario: Upload from the account page
- **WHEN** the owner taps the header avatar and picks a valid image
- **THEN** the avatar SHALL show a busy state, then the new image (no toast)

#### Scenario: Upload rejected
- **WHEN** the upload fails
- **THEN** a toast SHALL show 「圖片太大（上限 5 MB）」 for 413, 「只接受 PNG、JPEG、WebP」 for `logo_format`, 「圖片尺寸不符（每邊 16–8192 px）」 for `logo_dimensions`, 「這個檔案無法當作圖片使用」 for `logo_unreadable` or `logo_empty`, 「帳戶不存在」 for a 404 `account not found`, and 「伺服器尚未支援上傳」 for any other 404
- **AND** the previous logo SHALL remain

#### Scenario: Oversized file refused client-side
- **WHEN** the owner picks a file over 5 MiB
- **THEN** no request SHALL be sent and a toast SHALL show 「圖片太大（上限 5 MB）」

### Requirement: Mobile-usable layout

Every accounting page SHALL be usable at a 390 px viewport width without horizontal page scrolling, with the shell's bottom tab bar visible. Below 760 px each route is one full-width screen. From 760 px to 1023 px the list pages are one pane and the entry detail, entry form and account settings open as a sheet sliding in from the right. At 1024 px and above the list pages render two persistent panes inside the dock shell: the list on the left and the detail, entry form or settings on the right; selecting a row updates the right pane and the URL without a page navigation.

#### Scenario: Viewing on iPhone
- **WHEN** the accounts page is opened on a 390 px wide viewport
- **THEN** no horizontal page scroll SHALL occur and every balance SHALL be readable

#### Scenario: Two panes on desktop
- **WHEN** the timeline is opened at 1280 px and a row is tapped
- **THEN** the detail SHALL render in the right pane, the list SHALL stay in place, and the URL SHALL be `/accounting/entries/{id}`

#### Scenario: Sheet on iPad portrait
- **WHEN** the timeline is opened at 820 px and a row is tapped
- **THEN** the detail SHALL slide in as a sheet over the list and close with ✕ or a swipe

### Requirement: Timeline home page

The SPA SHALL provide `/accounting` as the accounting home, replacing the redirect to the accounts list. It SHALL show a month navigator with the month's expense, income and net totals from the summary endpoint, a category distribution bar under the totals (the month's expense split by top-level category: the five largest plus 其他, each segment in the category's colour, with a legend of name and amount), a filter bar (`<app-filter-bar>`: text search and the 帳戶 control inline; 類型, 類別, 專案, 標籤, 商家 and 金額範圍 behind 篩選 at every width, with active filters as removable chips), and entries from `GET /api/accounting/entries` grouped by day with a per-day net. Each row SHALL show category icon and colour, name or category, merchant and tags, amount (red for outflow, green for inflow per the preference), the original-currency amount inline when converted, and project and account pills. Reward entries SHALL be hidden when the preference `hide_rewards_on_timeline` is true. A split group SHALL render as one row with the group's name, the sum and a count badge. Tapping a row opens the entry detail. The page SHALL load more on scroll.

#### Scenario: Foreign-currency amount visible without hover
- **GIVEN** an expense converted from `¥5,390` at `0.2163`
- **WHEN** the timeline renders at 390 px
- **THEN** the row SHALL show `¥5,390` and the rate beneath the TWD amount

#### Scenario: Month totals
- **WHEN** the owner moves the navigator to 2026-09
- **THEN** the header SHALL show September's expense, income and net from the summary endpoint and the list SHALL show September's entries

#### Scenario: Category distribution bar
- **GIVEN** the month's expenses span eight top-level categories
- **WHEN** the summary renders
- **THEN** the bar SHALL show the five largest categories plus 其他 at their real spending amounts, and the legend SHALL reconcile to the month's expense: Σ segments − 退款 = |expense| (up to FX rounding), where 退款 is a legend-only line for categories whose refunds exceeded their spending this month (absent when there are none)

#### Scenario: Net-refund month
- **GIVEN** the month's refunds exceed its spending (the summary expense is ≥ 0)
- **WHEN** the summary renders
- **THEN** no distribution bar SHALL be shown

#### Scenario: Phone filters
- **WHEN** the timeline renders at 390 px with a 類別 and a 商家 filter active
- **THEN** the first entry row SHALL start within 200 px of the filter bar, the 篩選 button SHALL read `篩選 2` and two chips SHALL appear under the bar

### Requirement: Entry page

The SPA SHALL provide `/accounting/entry` (new) and `/accounting/entries/:id/edit`. The form SHALL follow MOZE's layout top to bottom: ✕ and ✓ in the header; kind tabs 支出 / 收入 / 轉帳 / 應收款項 / 應付款項 / 系統 (系統 offers 餘額調整 only in 2a); a category grid of icon bubbles (main categories; choosing one with sub-categories shows them; the grid collapses into a strip showing the chosen category and amount, with a "+" that adds a split line); the amount tile with a currency pill (opens the FX sheet) and a "+" (opens the fee / discount sheet); the name tile with a ☆ reserved for templates (disabled in 2a); tiles for 帳戶 | 專案, 商家 (對象 for receivables and payables) | 日期, 時間 | 發票號碼, 隨機碼; a chips field for #tags and reward rules (rules offered are the account's enabled rules covering the date; `is_basic` rules pre-selected); a 備註 box; an 進階 section with 入帳日 (posting date); and a footer line for context (fee total, FX conversion, 2b's expected reward).

Choosing a category SHALL pre-fill account and project from the category's defaults (server values, overridden by the device's last use). Switching to 轉帳 SHALL show from / to account tiles with ⇄, two amount tiles (轉出 / 轉入), each with its own fee / discount "+", and a rate tile for cross-currency transfers. Saving SHALL call the matching write endpoint and return to the previous page; ✓ long-press or ⇧⏎ SHALL save and start a new record with the same kind, account and date (連續記帳). Editing a locked (imported) entry SHALL show the fields read-only with a banner explaining the lock. Copying (複製) SHALL open the form pre-filled with today's date.

#### Scenario: Expense in three taps
- **WHEN** the owner opens the entry page, taps 飲食, taps 午餐, types 170 and taps ✓
- **THEN** an expense of `-170` in `飲食/午餐` SHALL be created on the category's default account with its default project, dated today

#### Scenario: Split line with another kind
- **GIVEN** an expense 午餐 `230` is entered
- **WHEN** the owner taps "+" on the category strip and adds 應收款項 代付 `180` for Alan
- **THEN** saving SHALL call the split endpoint with both members and the detail SHALL show both

#### Scenario: Locked entry
- **GIVEN** an entry with `source = 'moze_backup'` and the import not locked
- **WHEN** the owner opens its edit page
- **THEN** every field SHALL be read-only and a banner SHALL say the record becomes editable after cutover

### Requirement: Amount input

Below 760 px the amount tile SHALL open a custom keypad instead of the system keyboard, with the layout chosen by the preference: calculator (`÷ × − +` row, then `7 8 9 ⌫`, `4 5 6 C`, `1 2 3 ↵`, `. 0 00 ✓`) or phone (`1 2 3` on top). `↵` moves focus to the next field, `C` clears, `✓` saves. A strip of quick amounts (the six most frequent amounts of the chosen category) SHALL sit above the keypad. At 760 px and above the amount is a text field that accepts an arithmetic expression (`+ − × ÷`, decimals, parentheses) evaluated on blur or ⏎ with standard precedence; an invalid expression SHALL keep the field in an error state and block saving. Both paths SHALL share one evaluator that rounds to the currency's decimals (TWD and JPY 0, others 2).

#### Scenario: Expression on desktop
- **WHEN** `1200+35` is typed and the field blurs
- **THEN** the amount SHALL read `1,235`

#### Scenario: Keypad arithmetic
- **WHEN** the keypad receives `3`, `×`, `4`, `0`, `✓`
- **THEN** the entry SHALL be saved with amount `120`

### Requirement: Entry detail view

The SPA SHALL provide `/accounting/entries/:id` showing: a header in the category colour with the icon and category path; name and total (with "內含手續費 $N" when children exist); the FX line (original amount × rate = converted, source) when converted; a grid of account, merchant or counterparty, date | project, kind, time; tags and attached rule chips; a 回饋 section (requirement "Entry reward section") in place of separate 紅利回饋 and 預估回饋 sections; a 關聯記錄 section with fee and discount children, the transfer counterpart, group siblings, the entry it settles or refunds and the entries settling or refunding it; for receivables and payables the open amount and a 新增收款 / 新增還款 action; the note; and an action bar 編輯 / 複製 / 退款 / 收款 / 刪除. Delete SHALL confirm inside the page and explain what else is deleted (children, the other transfer leg). Actions SHALL be disabled with the lock banner on locked entries.

#### Scenario: Receivable detail
- **GIVEN** a receivable of `-420` for Alan with `200` collected
- **WHEN** its detail opens
- **THEN** it SHALL show `剩餘 $220`, the collection under 關聯記錄, and an enabled 新增收款 action

#### Scenario: Settle from detail
- **WHEN** 新增收款 is tapped, account `錢包` and amount `220` entered, and saved
- **THEN** the settle endpoint SHALL be called and the detail SHALL show `已結清`

### Requirement: Account settings page

The SPA SHALL provide `/accounting/accounts/:id/settings` and `/accounting/accounts/new`.

**Header.** At the top: the account's logo (uploaded image, else bundled brand logo, else its icon), its name and a status line (kind and current balance, and for cards the amount due and its debit date).

**Sections.** The form SHALL be in sections, in this order:

- **基本:** 名稱, 主幣種, 帳戶分組, 初始金額, 帳單週期 (a 結帳日 stepper with an empty 月底 state and the current cycle, for every account), 納入總餘額, and on a non-credit account a `1 點 = 1 元` switch with the helper 回饋入這個帳戶時當成金額計算 (`cash_equivalent`).
- **外觀:** a preview and a three-way switch 上傳圖片 / 內建 logo / 文字＋顏色.
  - 上傳圖片: 選擇檔案 (and drop) with the limits line; 「移除圖片」 only when an uploaded image exists. On `/accounting/accounts/new`, upload is disabled with 「先建立帳戶，再上傳 logo」.
  - 內建 logo: the bundled logo grid with `無` first.
  - 文字＋顏色: the icon text (up to 16 characters, as today) and a colour swatch row.
  - A line saying which source is shown. The upload > logo > icon precedence is unchanged.
- **信用卡:** a 信用帳戶 switch in the section header. When on, it reveals:
  - 繳款期限: 未設定, fixed day or days after closing, with a stepper and the computed due date when set;
  - 信用額度;
  - 額度共用, a multi-select of credit accounts;
  - 主帳戶 and 自動扣繳, pickers.
- **回饋規則:** the current window and 「所有卡片的規則 ›」 in the header, the account's rules as rows (requirement "Reward rule rows"), and ＋ 新增規則.
- **國外交易:** a switch in the header revealing 費率 (%), 計算方式 and 退款時退回.
- **其他:** 備註, 還原 MOZE 設定, 封存帳戶, and 刪除帳戶 (enabled only with no entries).

**From 760 px:**

- A sticky row of section chips SHALL scroll to each section and mark the one in view. The 回饋規則 chip shows the rule count.
- Save SHALL be in the header with 「有 N 項未儲存」 while dirty.

**Below 760 px:**

- Each section SHALL be a grouped list of label, value and › rows whose controls open in a bottom sheet. Switches stay inline.
- A bottom bar 放棄 / 儲存 SHALL replace the floating 記一筆 button while the form is dirty.

**Controls.** Day and amount fields SHALL use the app's stepper and amount field, and pickers the app's picker, not native number inputs or selects.

**Behaviour kept from the current page:**

- 主幣種 SHALL be read-only once the account has entries.
- Saving calls the account write endpoints. Validation errors SHALL be shown beside the field, and saving with errors SHALL scroll to the first invalid section and mark its chip.
- An imported account whose settings were edited locally SHALL show a note 「已自訂，匯入不再覆寫」 with a 還原 MOZE 設定 action that clears the flag.

#### Scenario: Credit toggle reveals card fields
- **WHEN** 信用帳戶 is switched on
- **THEN** the 繳款期限, 信用額度, 額度共用, 主帳戶 and 自動扣繳 rows SHALL appear

#### Scenario: Due date preview
- **GIVEN** closing day 15 and 繳款期限 = 20 days after closing
- **WHEN** the section renders on 2026-10-02
- **THEN** it SHALL show the current cycle `09/16 – 10/15` and the due date `11/04`

#### Scenario: Closing day stepper
- **GIVEN** closing day 25 on 2026-11-07
- **WHEN** ＋ is pressed once
- **THEN** the stepper SHALL read 26 號 and the cycle line SHALL read 本期 10/27–11/26

#### Scenario: Closing day on a bank account
- **GIVEN** an account with 信用帳戶 off
- **THEN** 帳單週期 SHALL still be shown under 基本 and keep its value on save

#### Scenario: Pick a logo
- **WHEN** the owner selects 內建 logo in 外觀 and taps a logo
- **THEN** the preview and the header SHALL show that logo's image, and saving SHALL send its slug as `logo`

#### Scenario: Clear the logo
- **GIVEN** an account with a logo and the emoji `💳`
- **WHEN** the owner taps `無` in the 內建 logo grid and saves
- **THEN** `logo` SHALL be null, the header SHALL show the emoji, and `icon` SHALL be unchanged

#### Scenario: Remove button only with an uploaded image
- **GIVEN** an account without an uploaded image
- **THEN** 「移除圖片」 SHALL NOT be shown

#### Scenario: Image write keeps the dirty form
- **GIVEN** the owner has edited fields but not saved
- **WHEN** they upload or remove an image
- **THEN** the image SHALL change immediately and the unsaved edits SHALL be kept

#### Scenario: Upload on a new account
- **WHEN** the form is `/accounting/accounts/new`
- **THEN** 選擇檔案 SHALL be disabled and the hint 「先建立帳戶，再上傳 logo」 SHALL show

#### Scenario: Phone save bar
- **GIVEN** the page at 390 px
- **WHEN** 名稱 is changed
- **THEN** a bottom bar with 「有 1 項未儲存」, 放棄 and 儲存 SHALL appear and the floating 記一筆 button SHALL be hidden

### Requirement: Accounting settings page

The SPA SHALL provide `/accounting/settings` (replacing the redirect) with sections: 資料 (帳戶分組, 類別, 專案, 對象: each a list with add, rename, reorder by drag, hide or archive, and delete when unused; categories show icon and colour pickers and the two-level tree per kind), 顯示 (支出收入顏色, 數字鍵盤順序, 月曆起始星期, 首頁隱藏紅利回饋, 總額縮寫), and 匯入 (upload a MOZE backup zip with a dry-run first, showing the report; the latest import; the import lock state; the import report's `schedules` block after an import — including a count and one line per differing item (definition name, 第 k 期, date, HomeHub amount against MOZE's) for both `past_records_amount_differs` and `amount_differs`, and the count of `past_records_owner_pending`; and a 週期／分期 row linking to the 週期／分期 section of 提醒中心, replacing the read-only list of phase 2a). The settings sub-nav item of the accounting group SHALL point here instead of the global `/settings`.

#### Scenario: Rename a counterparty
- **WHEN** `Alan` is renamed to `Alan Chen`
- **THEN** every entry's counterparty name SHALL update

#### Scenario: Backup dry run from the page
- **WHEN** a zip is chosen and 試算 tapped
- **THEN** the page SHALL show the report (per-type counts, skipped future rows, balance differences) without changing data, and offer 匯入 as a second step

#### Scenario: Pending amount differences shown
- **GIVEN** an import report whose `schedules` block has only `amount_differs` non-empty (Netflix seq 3, 2026-10-05, HomeHub `120`, MOZE `100`)
- **WHEN** the report is shown
- **THEN** the page SHALL show a warning with the count 1 and the line `Netflix 第 3 期 2026-10-05：HomeHub $120 / MOZE $100`

#### Scenario: Schedule list moved to 提醒中心
- **WHEN** the owner taps 週期／分期 in 匯入
- **THEN** the app SHALL open `/accounting/reminders?tab=debts` scrolled to the 週期／分期 section, and no request to `/api/accounting/imports/schedules` SHALL be made

### Requirement: Category icons and colours

Category bubbles, timeline rows, detail headers and the category picker SHALL use `category.icon` (emoji) on a circle filled with `category.color`; sub-categories inherit from their parent. The design's default icon table SHALL be applied client-side when a category has no icon.

#### Scenario: Imported colours used
- **GIVEN** `飲食` imported with colour `#f0cd92`
- **WHEN** an expense in `飲食/午餐` renders on the timeline
- **THEN** its icon circle SHALL be `#f0cd92`

### Requirement: Entry shortcuts

The web manifest SHALL declare a shortcut `記一筆` to `/accounting/entry`. The floating "+" and the `N` shortcut SHALL be provided app-wide by the shell (`frontend-app-shell`, App-wide 記一筆 floating button); on accounting pages at ≥ 1024 px the entry form opens in the right pane. On the entry page ⏎ saves, ⇧⏎ saves and continues, Esc cancels; on list pages E edits the selected entry.

#### Scenario: Home-screen shortcut
- **WHEN** the installed app's shortcut `記一筆` is launched
- **THEN** the app SHALL open directly on `/accounting/entry`

#### Scenario: Floating button opens the form in the pane
- **WHEN** the owner presses the floating "+" on `/accounting` at 1280 px
- **THEN** the entry form SHALL open in the right pane

### Requirement: Entry form schedule tabs

The entry form's 進階 section SHALL offer the tabs 單次 / 週期 / 分期 (MOZE 進階設定), 單次 selected by default:

- **單次**: today's behaviour (入帳日).
- **週期** (支出, 收入, 轉帳, 應收款項, 應付款項): 區間 `每 N {天|週|月|年}` (N ≥ 1, default 每 1 月), 起始日 (default the entry's 日期), 結束 {無限期 | N 次 | 日期} (default 無限期), 入帳方式 {自動入帳 | 提醒入帳} (default 自動入帳). The footer SHALL read `週期：#1 / 無限期（每月 / 22號）` or `週期：#1 / 12（每月 / 22號）` in MOZE's wording.
- **分期** (支出 and 應付款項 only): 總額 (default the amount tile), 期數 (≥ 2), 首次還款日 (default one month after the 日期), 每期金額 (auto `floor(總額 ÷ 期數)` in whole units for TWD and JPY and to 2 decimals otherwise, editable; the remainder goes to the last period, and for 應付款項 the last repayment is clamped to the loan's open amount), 利息 (optional, per period, flat), 入帳方式, and for 應付款項 還款帳戶 (default the entry's account). The footer SHALL read `分期：#1 / 期數（$總額） 首次還款日將從 YYYY/MM/DD 開始進行（期數 期）`.

Saving with 週期 or 分期 SHALL call `POST /api/accounting/schedules/definitions` instead of the entry endpoint: one template line built from the form (kind, account, category, project, counterparty, amount, name, merchant; for 轉帳 the to-account and in-amount; for 分期 on 應付款項 a `repayment` line and, with 利息, an `interest` line, with `loan` carrying the payable's account, counterparty, category, 總額, 日期 and name). When the definition is `auto` and its 起始日 or 首次還款日 is today, the form SHALL then call `catch-up` for it; with an earlier date it SHALL NOT, and the past periods wait in 待完成交易 for the owner. Creation SHALL be complete when the create response arrives: the form SHALL keep the created definition's id, and when the catch-up fails (an HTTP error, including 409 `import_running`) the form SHALL stay open with `排程已建立，入帳未完成；按 ✓ 重試入帳` and ✓ SHALL retry only the catch-up — it SHALL never send a second create. A catch-up answered with a `failed` period SHALL be surfaced in the toast `排程已建立；這一期入帳失敗，請到待完成交易處理`, not discarded. On the 週期 and 分期 tabs the currency (FX) pill, the split "+", the fee / discount "+", the reward chips, the photo tile and the invoice fields SHALL be hidden, with the note `排程不支援` in their place. The form SHALL show a validation message beside the field the server names. 連續記帳 SHALL keep the tab at 單次 for the next record.

#### Scenario: Monthly Netflix
- **GIVEN** today 2026-10-03
- **WHEN** the owner enters 支出 娛樂/Netflix `390` on 範例卡, picks 週期 每 1 月, 起始日 2026-10-22, 無限期, 自動入帳, and saves
- **THEN** a `recurring` definition SHALL be created with one `expense` line of `390`, no entry SHALL be created, and `catch-up` SHALL NOT be called

#### Scenario: Catch-up retried without a second create
- **GIVEN** a 週期 definition starting today with 自動入帳 whose create succeeded and whose catch-up answered HTTP 409 `import_running`
- **WHEN** the owner taps ✓ again
- **THEN** only `catch-up` SHALL be called again and exactly one create request SHALL have been sent

#### Scenario: Recurring transfer from the form
- **WHEN** the owner enters 轉帳 `15000` from 薪轉 to 交割, picks 週期 每 1 月 起始日 2026-11-05, and saves
- **THEN** a `recurring` definition with one `transfer` line (`to_account_id` = 交割) SHALL be created, and the 分期 tab SHALL not be offered for 轉帳

#### Scenario: Card installment split
- **WHEN** on 2026-10-03 the owner enters 支出 `10000` dated today and picks 分期 with 期數 3
- **THEN** 每期金額 SHALL show `3,333` and the footer SHALL read `分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）`

#### Scenario: New loan with interest
- **WHEN** the owner enters 應付款項 信貸 `300000` from 範例銀行 into 薪轉, picks 分期 36 期, 首次還款日 2026-11-09, keeps 每期金額 `8,333`, enters 利息 `620`, 還款帳戶 薪轉, and saves
- **THEN** one request SHALL create the payable and an `installment` definition with `total_amount = 300000`, a `repayment` line of `8333` and an `interest` line of `620`

### Requirement: Scheduled entry edit scope

Editing an entry whose `schedule` is set SHALL first ask 編輯這一筆 / 編輯整個排程. 編輯這一筆 SHALL open the normal edit for kinds the entry form edits, and for a settlement, interest or other entry the form refuses SHALL open a sheet with the period's line amounts that calls `POST /api/accounting/schedules/instances/{id}/repost`. 編輯整個排程 SHALL open the form in definition mode, pre-filled from the definition, saving with `PUT /api/accounting/schedules/definitions/{id}`; for an imported definition before cutover it SHALL show the lock banner instead. Deleting such an entry SHALL delete only that entry (and its transfer pair); the confirmation SHALL say so and name the period's other entries that stay (`同期的 利息 −$620 會保留，此期標示為部分入帳`), or, for the period's last entry, what happens to the period by its `acted_by`: `此期將回到待完成交易` for a period HomeHub posted, `此期將標示為略過` for one MOZE booked (`acted_by = import`). A partial period SHALL show a 部分 badge on its pill and offer 重新入帳 (`repost`) and 保留部分 (`accept-partial`).

#### Scenario: Correct one period's interest
- **GIVEN** the 2026-11-09 repayment of 信貸 每月還款 #1/36
- **WHEN** the owner chooses 編輯這一筆, changes 利息 to `598` and saves
- **THEN** `repost` SHALL be called with `["8333", "598"]` and the detail SHALL show the new interest

#### Scenario: Deleting a MOZE-booked period's last entry
- **GIVEN** an expense that is the only entry of a period with `acted_by = import`
- **WHEN** the owner taps 刪除
- **THEN** the confirmation SHALL say `此期將標示為略過`

### Requirement: 待完成交易 tab

提醒中心 (`/accounting/reminders`) SHALL have exactly the tabs 全部 / 信用卡帳單 / 借還款追蹤 / 待完成交易, in that order, selectable by `?tab=all|cards|debts|pending` (default 全部): 信用卡帳單 lists the cards whose current statement has a remaining balance, soonest due first; 借還款追蹤 lists the counterparties with an open receivable or payable and then the 週期／分期 section; 待完成交易 is described here; 全部 shows the card, counterparty and due 待完成交易 sections in that order. 待完成交易 SHALL list `GET /api/accounting/schedules/instances?queue=true` (until today + 30 days) in two groups, 已到期 (due today or earlier, oldest first) and 即將到來. Each item SHALL show the definition's icon (category), title (`name #k/N`), the lines (account, signed amount, to-account for transfers), the total, the due date, `已逾期 N 天` in the warning tone when overdue, `今天` when due today, the `last_error` text with 重試 when set, and the actions [入帳] and [略過]; a partial period (`is_partial`) SHALL instead show the badge 部分入帳 with [重新入帳] (`repost`) and [保留部分] (`accept-partial`), and SHALL be listed under 已到期 whatever its date until one of them is used. 略過 SHALL ask once inline (`略過這一期？剩餘不變`) before calling `skip`. Items of paused definitions are not in the queue. When a definition has more than one overdue item, its first item SHALL also offer [補入帳至今天], calling `catch-up`. 全部 SHALL show the 已到期 group as a section 待完成交易. After an action the list and the bell count SHALL refresh.

#### Scenario: Overdue confirm item
- **GIVEN** today 2026-10-03 and a 提醒入帳 instance 房租 #4/12 due 2026-09-30 of `−18000`
- **WHEN** the owner opens 待完成交易
- **THEN** it SHALL appear under 已到期 with `已逾期 3 天` in the warning tone and [入帳] [略過]

#### Scenario: Skip prompt keeps the remaining
- **WHEN** the owner taps [略過] on a loan period
- **THEN** the inline prompt SHALL read `略過這一期？剩餘不變` and only its confirmation SHALL call `skip`

#### Scenario: Back-dated start waits for the owner
- **GIVEN** today 2026-10-03
- **WHEN** the owner saves a 週期 自動入帳 with 起始日 2026-09-15
- **THEN** `catch-up` SHALL NOT be called and the 2026-09-15 period SHALL appear under 已到期 in 待完成交易

#### Scenario: Unsupported fields hidden
- **WHEN** the owner switches 進階 to 週期
- **THEN** the currency pill, split "+", fee / discount "+", reward chips, photo and invoice fields SHALL be hidden and `排程不支援` SHALL be shown

#### Scenario: Post from the queue
- **WHEN** the owner taps [入帳] on that item
- **THEN** `POST /api/accounting/schedules/instances/{id}/post` SHALL be called and the item SHALL leave the list

#### Scenario: Catch-up offered for a backlog
- **GIVEN** two overdue items of the same definition (2026-09-22 and 2026-10-01)
- **WHEN** the tab renders
- **THEN** the 2026-09-22 item SHALL offer [補入帳至今天]

### Requirement: 週期／分期 section

The 借還款追蹤 tab of 提醒中心 SHALL show, below the counterparties, a section 週期／分期 listing every definition that is not `ended` from `GET /api/accounting/schedules/definitions`, ordered by next due date: icon, name, next date (`下期 11/09`), progress `已入帳 k / N` (or `每月` / `每 2 週` … when `times` is NULL), `剩餘 −$275,001` for loans and `剩餘 $6,667` for installments with a total, a mode badge 自動 or 提醒, a 已暫停 badge when paused, a 需檢查 badge when `needs_check` is true (an ended loan definition whose loan is still open, or `review_reason` set), and, when `failing` is set, the error text with 重試 (calling `catch-up`). A collapsed 已結束 row SHALL list ended definitions, with 需檢查 where it applies. Tapping a row SHALL open a manage sheet with: the rule summary (`每月 9 號 · 36 期 · 自 2026/11/09`), the next three periods (each with 調整, which opens the period's amounts, one field per template line, with 儲存), 暫停 / 繼續 (繼續 asks 略過期間的 N 期 / 補入帳 when paused instances are overdue and sends `backlog`), 入帳方式 自動入帳 / 提醒入帳, 補入帳至今天 (when something is overdue), 編輯 (definition mode of the entry form), 結束 (confirm: `結束後未入帳的 N 期將刪除`), and 刪除 only when nothing was posted. When the owner saves a period with at least one changed amount, a sheet titled `套用範圍` SHALL ask with the buttons `僅這一期` / `這一期與之後` / `全部週期` (focus on `僅這一期`) and SHALL call `PUT /api/accounting/schedules/instances/{id}` with the amounts and `scope` `this` / `following` / `all` respectively; Esc SHALL close that sheet and save nothing; a save without a changed amount SHALL close the editor without asking or calling the server. The sheet follows the keyboard contract of every sheet (Esc closes and is handled; ⏎ in a field saves).

#### Scenario: Price change from this period on
- **GIVEN** the Netflix sheet whose next period is `−$390`
- **WHEN** the owner taps 調整 on it, enters `420`, taps 儲存 and chooses `這一期與之後`
- **THEN** `PUT /api/accounting/schedules/instances/{id}` SHALL be called with `{"amounts": ["420"], "scope": "following"}` and the sheet SHALL reload

#### Scenario: Scope question cancelled
- **WHEN** the `套用範圍` sheet is open and the owner presses Esc
- **THEN** the sheet SHALL close and no request SHALL be made

#### Scenario: Loan row
- **GIVEN** the 信貸 definition with 3 of 36 periods posted and the next on 2027-02-09
- **WHEN** the section renders
- **THEN** its row SHALL read `信貸 每月還款`, `下期 02/09`, `已入帳 3 / 36`, `剩餘 −$275,001` and the badge 自動

#### Scenario: Ended loan still open needs a check
- **GIVEN** an ended loan definition whose loan's open amount is `8333`
- **WHEN** 已結束 is expanded
- **THEN** its row SHALL carry 需檢查

#### Scenario: Pause from the sheet
- **WHEN** the owner taps 暫停 in the Netflix sheet
- **THEN** `pause` SHALL be called and the row SHALL show 已暫停

### Requirement: Reminder bell count

The timeline's 🔔 count SHALL be the sum of: the cards whose current statement has a remaining balance, the counterparties with a non-zero open amount in any currency, and the 待完成交易 queue items (`queue=true`) that are due today or earlier or are partial (until 保留部分 or 重新入帳) — which excludes paused and ended definitions. The aria-label SHALL keep the form `提醒中心，N 項`.

#### Scenario: Bell includes due confirm items
- **GIVEN** one unpaid bill, no open counterparty, and two queue items due 2026-10-01 and 2026-10-20, today 2026-10-03
- **WHEN** the timeline renders
- **THEN** the bell SHALL show `2`

#### Scenario: Partial period counted until accepted
- **GIVEN** a partial period due 2026-11-09, today 2026-10-03, and nothing else open
- **WHEN** the timeline renders, and again after 保留部分
- **THEN** the bell SHALL show `1` and then `0`

#### Scenario: Paused definition not counted
- **GIVEN** the only due queue item belongs to a definition the owner then pauses
- **WHEN** the timeline reloads
- **THEN** the bell SHALL not count it and the item SHALL leave 待完成交易

### Requirement: Loan breakdown in entry detail

The detail of a payable or receivable original with `loan_schedule` SHALL show, in its debt block, the line `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09` (下期 left out when nothing is pending) and a link `信貸 每月還款 · 已入帳 3 / 36` that opens the definition's manage sheet in 提醒中心.

#### Scenario: Loan detail line
- **GIVEN** the 信貸 payable with `loan_schedule` `remaining = -275001`, `repaid = 24999`, `next_due_date = 2027-02-09`
- **WHEN** its detail opens
- **THEN** it SHALL show `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09`

### Requirement: Import-running feedback

Any schedule action, and any entry delete that touches a scheduled period, answered with HTTP 409 `import_running` SHALL show the toast `匯入進行中，請稍後再試` and leave the page state unchanged.

#### Scenario: Posting during an import
- **GIVEN** a backup import is running
- **WHEN** the owner taps [入帳] in 待完成交易
- **THEN** the toast `匯入進行中，請稍後再試` SHALL appear and the item SHALL stay in the list

### Requirement: Schedule pills

Timeline rows, passbook rows, 提醒中心 rows and the entry detail SHALL show a pill for an entry whose `schedule` is set: `週期 #k/N` for a recurring definition (`週期 #k` when `times` is NULL) and `分期 #k/N` for an installment, in MOZE's wording. In the entry detail the pill SHALL link to the definition's manage sheet.

#### Scenario: Installment pill
- **GIVEN** an entry with `schedule = {kind: installment, seq: 5, times: 36}`
- **WHEN** it renders on the timeline
- **THEN** its row SHALL carry the pill `分期 #5/36`

#### Scenario: Unlimited recurring pill
- **GIVEN** an entry with `schedule = {kind: recurring, seq: 25, times: null}`
- **WHEN** it renders
- **THEN** its pill SHALL read `週期 #25`

#### Scenario: Partial period pill
- **GIVEN** an entry with `schedule = {kind: installment, seq: 1, times: 36, is_partial: true}`
- **WHEN** its detail opens
- **THEN** the pill SHALL read `分期 #1/36` with the badge 部分 and the detail SHALL offer 重新入帳

### Requirement: Rows keep the page gutter

Entry rows on the timeline (list mode) and in the account entry history SHALL align with the page's content gutter on every width; a selected row's highlight MAY bleed only by `--row-sel-bleed`.

#### Scenario: Phone timeline
- **WHEN** the timeline renders in list mode at 390 px
- **THEN** each row's category icon SHALL start at the same left edge as the day heading above it

### Requirement: Empty pane shows the month

At ≥ 1280 px, when no entry or account is selected, the accounting right pane SHALL show the month overview: the category distribution with amounts and the next three due items (card bills and schedule instances). Below 1280 px the one-line empty state SHALL remain. While the overview is shown, the list pane's summary strip SHALL hide its category distribution (the 支出 / 收入 / 結餘 figures stay); below 1280 px the strip keeps the distribution. In the timeline's 日曆 view the "Calendar layout on wide screens" requirements govern the right pane instead, from 1024 px.

#### Scenario: Timeline without a selection
- **GIVEN** the timeline month is 2026-10
- **WHEN** `/accounting` opens at 1440 px in the 清單 view with nothing selected
- **THEN** the right pane SHALL show October's category amounts and the next three due items

#### Scenario: Distribution shown once
- **GIVEN** `/accounting` at 1440 px in the 清單 view with nothing selected
- **THEN** the category distribution SHALL render in the overview only, not in the list pane's summary strip

#### Scenario: Calendar view at 1100 px
- **WHEN** `/accounting` opens at 1100 px in the 日曆 view with no day selected
- **THEN** the right pane SHALL show 本月摘要 rather than the one-line empty state

### Requirement: Accounts list marks the selection

On `/accounting/accounts` at ≥ 1024 px the selected account SHALL carry the indigo selection ring and `aria-current="true"`, and the empty pane copy SHALL read 選擇帳戶查看明細，或按 ＋ 新增.

#### Scenario: Account opened
- **WHEN** the owner opens 玉山銀行
- **THEN** its card in the left list SHALL show the selection ring

### Requirement: Page title on the page background

Accounting pages SHALL render their `<h1>` and page actions on the page background above the list card, as portfolio and settings pages do.

#### Scenario: Timeline header
- **WHEN** `/accounting` renders at 1440 px
- **THEN** 記帳記錄 SHALL sit above the list card, not inside it

### Requirement: Combined-card payments count toward the master's bill

A card's current statement (`paid`, `remaining`) SHALL sum the `transfer_in` entries of the master card and of every non-archived card combined into it, over the same payment window, so a payment recorded on a 副卡 clears the master's bill.

#### Scenario: Payment on a 副卡 clears the master

- **GIVEN** a master card with one combined card, period spend 1,000 on the master and 2,800 on the 副卡
- **AND** a `transfer_in` of 2,800 recorded on the 副卡 within the payment window
- **THEN** the statement SHALL show 應繳 $3,800, 已繳 $2,800, 剩餘 $1,000

### Requirement: Card bills count to the debit day

Each due event SHALL carry the debit day: the first business day on or after the printed due date, from `GET /api/accounting/calendar/business-days` (read once per session; until it lands or when it fails, only weekends shift). The accounts-list pill, the reminder countdown, the overview's due rows and the overdue tone SHALL count to the debit day; the printed due date stays the sort key and the calendar-grid position, and the reminder card SHALL read `MM/DD 截止 · MM/DD 扣款` when the days differ.

#### Scenario: Weekend due date over a holiday

- **GIVEN** a statement due Sat 2026-10-24, the calendar listing Mon 2026-10-26 as a holiday, and today 2026-10-26
- **THEN** the reminder SHALL read `10/24 截止 · 10/27 扣款` with the countdown `明天`, not `已逾期`
- **AND** the accounts-list pill SHALL read `明天繳費截止`

#### Scenario: Business-day due date is unchanged

- **GIVEN** a statement due Mon 2026-10-05
- **THEN** the reminder SHALL read `10/05 截止` and count to 2026-10-05

### Requirement: Account logo image store

The SPA SHALL fetch uploaded logos with the scoped token and keep one object URL per account. Each account icon SHALL hold a lease for its current (account, `logo_hash`), released when the pair changes or the icon is destroyed; the store SHALL fetch only for the newest non-null lease of an account, never because a consumer re-read its signal. The response `ETag` decides the hash shown. A superseded load SHALL never be published and a replaced object URL SHALL be revoked on the next macrotask.

#### Scenario: Cache hit
- **GIVEN** a logo already loaded for (account, `logo_hash`)
- **WHEN** another view acquires a lease for the same pair
- **THEN** the cached object URL SHALL be reused with no request

#### Scenario: ETag mismatch
- **WHEN** the response ETag differs from the requested `logo_hash`
- **THEN** the image SHALL be shown under the actual hash, the accounts list SHALL be refreshed once, and the leases already live for the requested hash SHALL make no further request; a new lease for that hash SHALL request it once more

#### Scenario: Failed load
- **WHEN** a logo request fails (any HTTP error, or an empty body)
- **THEN** the image already shown SHALL stay, that hash SHALL NOT be requested again for 30 s, and after the window the store SHALL request the newest lease's hash once if it is still unresolved

#### Scenario: Superseded load
- **GIVEN** a load is in flight
- **WHEN** a newer load for the same account starts
- **THEN** the older result SHALL NOT create an object URL, change the image or count as a failure

#### Scenario: Newest lease returns to the shown hash
- **GIVEN** an account showing hash B while a load for hash A is in flight
- **WHEN** a lease for B becomes the newest lease of the account
- **THEN** the A load SHALL be cancelled and B SHALL stay shown

#### Scenario: Earlier logo uploaded again
- **GIVEN** an account whose icons showed hash A, then hash B
- **WHEN** the account's `logo_hash` becomes A again and the icons acquire A
- **THEN** the store SHALL request A once more and show it

#### Scenario: Logo deleted
- **GIVEN** an account whose icons show an uploaded image
- **WHEN** every lease of the account is released or holds a null hash
- **THEN** the image SHALL be cleared on the next macrotask, its object URL revoked on the one after, and a response that arrives later SHALL NOT be shown

### Requirement: Calendar day selection in the URL

In the timeline's 日曆 view the selected day SHALL be the query parameter `day=YYYY-MM-DD` on `/accounting`, beside `month`. Without `day` nothing SHALL be selected, and opening 日曆 SHALL NOT select today by itself. A `day` outside `month` SHALL move `month` to the day's month. A URL with `day` SHALL open the 日曆 view for that visit without changing the stored view preference.

#### Scenario: Shared link
- **WHEN** `/accounting?month=2026-10&day=2026-10-21` is opened
- **THEN** the 日曆 view SHALL show October with 10/21 selected and 10/21's rows listed

### Requirement: Calendar day navigation

Picking a day SHALL set `day`. Picking the selected day again, 本月摘要 (≥ 1024 px) or 展開整月 (< 1024 px) SHALL remove it. ‹ and › beside the day label SHALL move one calendar day, crossing month edges and moving `month` with them. Moving the month with the navigator SHALL remove `day`.

#### Scenario: Crossing a month edge
- **GIVEN** 2026-10-31 is selected
- **WHEN** › is pressed
- **THEN** the URL SHALL become `?month=2026-11&day=2026-11-01` and the grid SHALL show November

#### Scenario: Month navigator clears the day
- **GIVEN** 10/21 is selected
- **WHEN** the month navigator moves to November
- **THEN** `day` SHALL be removed and no day SHALL be selected

### Requirement: Calendar view switch with a selected day

`day` SHALL force the 日曆 view only while it is in the URL. Choosing 清單 SHALL remove `day` and store 清單 as the view. 近 7 天 SHALL write `month` and `day` (the first due day) with the same history rules as picking a day, and SHALL store 日曆 as the view, as it does today.

#### Scenario: Choosing 清單 with a day selected
- **GIVEN** `/accounting?month=2026-10&day=2026-10-21`
- **WHEN** 清單 is chosen
- **THEN** the URL SHALL be `/accounting?month=2026-10`, the 清單 view SHALL show, and a reload SHALL keep 清單

### Requirement: Calendar selection history

Picking a day, ‹ and ›, the swipes and the month navigator SHALL replace the current browser history entry rather than add one, so that Back leaves the 日曆 view instead of stepping through the days picked. Every `month` / `day` write SHALL keep the URL's other query parameters (the 報表 drill-through filters). The 日曆 view SHALL follow `month` and `day` when they change in the URL (Back, Forward or a link), not only on first load.

#### Scenario: Drill-through filters kept
- **GIVEN** `/accounting?month=2026-10&category_id=12` was opened from 報表
- **WHEN** 10/21 is picked
- **THEN** the URL SHALL be `/accounting?month=2026-10&category_id=12&day=2026-10-21`

#### Scenario: Back after picking days
- **GIVEN** the owner opened `/accounting` from 報表 and picked 10/20, then 10/21
- **WHEN** Back is pressed
- **THEN** the browser SHALL return to 報表

### Requirement: Calendar selection survives opening an entry

An entry or entry form opened from the calendar SHALL keep `month` and `day` in its URL, so that closing it, going back, saving, cancelling or deleting restores the selection. This includes the form's fallback exit when there is no in-app previous page.

#### Scenario: Back from an entry
- **GIVEN** 10/21 is selected on a 390 px screen
- **WHEN** a row is opened and the owner goes back
- **THEN** the calendar SHALL show the week of 10/21 with 10/21 selected

### Requirement: Calendar day cell figures

Each day cell of the 日曆 view SHALL show:

- the date, with today on a primary-colour disc;
- the day's net, `income + expense` from `GET /entries/summary/daily` (expense is negative): with thousands separators at ≥ 1024 px, in compact form below 1024 px (`−3.1k`, `+71.7k`, `−250`);
- a bottom bar whose length is the day's |expense| over the month's largest daily |expense|, only on days whose expense is below 0.

#### Scenario: Heaviest day
- **GIVEN** the daily summary gives October's largest expense as −12,120 on 10/19 and 10/21's expense as −3,096
- **THEN** 10/19's bar SHALL span the full cell width and 10/21's about a quarter of it

### Requirement: Calendar day cell empty and future days

Future days SHALL be dimmed and show no figure. A past day without counted rows SHALL show — at ≥ 1024 px and no figure below 1024 px.

#### Scenario: Quiet past day
- **GIVEN** 10/05 has no counted rows and today is 10/22
- **THEN** 10/05's cell SHALL show — at 1440 px and no figure at 390 px, and 10/25's cell SHALL be dimmed with no figure

### Requirement: Calendar day cell category dots

Each day cell of the 日曆 view SHALL show up to three dots for the day's largest expense categories, coloured by joining each split's `key` to the owner's category list, then `+N` when there are more. They SHALL be read once per month from `GET /reports/breakdown?group_by=day&split_by=category&metric=expense&include_excluded_projects=true&date_from={month}-01&date_to={last day}`. If that read fails, the dots SHALL be left out and the cell SHALL render without an error.

#### Scenario: Breakdown unavailable
- **GIVEN** `GET /reports/breakdown` fails
- **WHEN** the 日曆 view renders
- **THEN** the cells SHALL show dates, nets, bars and marks without dots, and no error toast SHALL appear

### Requirement: Calendar day cell marks and accessible name

Each day cell of the 日曆 view SHALL show:

- the card mark on bill due days (帳單, or 已繳 once the statement is fully paid, at ≥ 1024 px; a muted dot below);
- a warning-ink marker when an inbox e-invoice awaiting a match has that day as its `issue_date` (發票待對帳 at ≥ 1024 px; a dot below).

The cell's accessible name SHALL include the date, the weekday, the row count, the day's expense and any bill or invoice mark; the bar and the dots SHALL be hidden from assistive technology.

#### Scenario: Invoice waiting on a day
- **GIVEN** the inbox holds an unmatched invoice from 路易莎 issued 2026-10-22
- **WHEN** October renders at 1440 px
- **THEN** 10/22's cell SHALL show 發票待對帳 and its accessible name SHALL end with 「發票待對帳」

### Requirement: Calendar layout on wide screens

From 1024 px, in the 日曆 view:

- **The list pane SHALL hold the calendar only.** No rows SHALL appear under the grid.
- **The month's 支出 / 收入 / 結餘 SHALL sit in the page header.**
- **The right pane SHALL follow the selection.**
- **The 清單 view SHALL keep its two-pane layout.**

#### Scenario: Opening the calendar on a desktop
- **WHEN** `/accounting?month=2026-10` opens at 1440 px in the 日曆 view
- **THEN** the left pane SHALL show the October grid alone

### Requirement: Calendar layout on wide screens: month summary

From 1024 px, in the 日曆 view with no day selected, the right pane SHALL show 本月摘要. In this order:

1. a search field;
2. a 今天 card (current month only: today's count, names and net) that selects today;
3. the category distribution, with a link to `/accounting/reports?month=…`;
4. 花最多的商家, as specified by 「Calendar top merchants」;
5. 這個月要處理: unmatched e-invoices and unpaid card bills in the month, plus near-term dues past month end in the current month (「Calendar items to handle」).

#### Scenario: Month summary on a desktop
- **WHEN** `/accounting?month=2026-10` opens at 1440 px in the 日曆 view
- **THEN** the right pane SHALL show 本月摘要 with 今天, the category distribution, 花最多的商家 and 這個月要處理

#### Scenario: Bill due early next month at month end
- **GIVEN** today is 2026-10-28 and a card's statement debits on 2026-11-03 with a remaining balance
- **WHEN** `/accounting?month=2026-10` opens in the 日曆 view at 1440 px
- **THEN** 這個月要處理 SHALL list that bill with 11/03 到期
- **AND** `/accounting?month=2026-09` SHALL not list it

### Requirement: Calendar items to handle

A 這個月要處理 row SHALL select its day. In the current month it SHALL also list unpaid card bills debiting within today + 6 after month end; that row selects its day next month. An e-invoice row whose matcher proposed exactly one candidate SHALL also open it with `month` and `day` kept: an entry candidate at `/accounting/entries/{id}`; a split-group candidate at `/accounting/entries/{memberId}/group`, where `memberId` is a member of that group found in the day's `GET /entries` rows.

#### Scenario: One entry candidate
- **GIVEN** an invoice issued 2026-10-22 whose only candidate is entry 501
- **WHEN** its row is clicked
- **THEN** 10/22 SHALL be selected and the drawer SHALL show `/accounting/entries/501?month=2026-10&day=2026-10-22`

### Requirement: Calendar top merchants

In 本月摘要, 花最多的商家 SHALL list the five merchants with the largest expense in the shown month, from `GET /reports/breakdown?group_by=merchant&metric=expense&include_excluded_projects=true` with `date_from` and `date_to` set to the month's first and last day, excluding rows without a merchant, each with its count and amount.

#### Scenario: Merchant ranking unavailable
- **GIVEN** the merchant breakdown fails
- **THEN** 本月摘要 SHALL show 「商家排行讀取失敗」 in place of 花最多的商家 and render its other parts

### Requirement: Calendar layout on wide screens: day panel

From 1024 px, in the 日曆 view with a day selected, the right pane SHALL show the day panel: ‹ day label › with the count and net, a 本月摘要 button, the bill line for that day, and the day's rows with their time. A day without rows SHALL show 這天沒有記錄 (還沒到這天 for a future day) and 「＋ 在這天記一筆」, which opens `/accounting/entry?date=…&month=…&day=…` with that date filled in.

#### Scenario: Picking a day
- **WHEN** 10/21 is clicked
- **THEN** the right pane SHALL list 全聯 採買, 電費 and Netflix under 「10/21 週三 · 3 筆 · −$3,096」 and the cell SHALL be marked selected

### Requirement: Calendar layout on wide screens: entry drawer

From 1024 px, in the 日曆 view, an entry opened from the day panel SHALL render as a drawer over the right pane. The day panel stays mounted underneath.

- ✕ or Esc SHALL close it back to the day, and focus SHALL return to the row that opened it. Focus SHALL stay inside the drawer while it is open.
- The 760–1023 px sheet SHALL keep its current behaviour; the drawer is a separate ≥ 1024 px overlay.

#### Scenario: Entry drawer
- **GIVEN** 10/21 is selected
- **WHEN** 全聯 採買 is clicked
- **THEN** the detail SHALL cover the right pane as a drawer, the calendar SHALL stay visible, and the URL SHALL be `/accounting/entries/{id}?month=2026-10&day=2026-10-21`

#### Scenario: Esc returns focus
- **GIVEN** the drawer was opened from 10/21's 電費 row
- **WHEN** Esc is pressed
- **THEN** the URL SHALL be `/accounting?month=2026-10&day=2026-10-21` and focus SHALL be on the 電費 row

### Requirement: Calendar entry drawer stepping

From 1024 px, with the calendar's entry drawer open, ↑ and ↓ SHALL move to the previous or next entry of the month in list order (newest first), keeping `month` and moving `day` with the entry. For that visit they SHALL replace the layout's document-level row stepping.

#### Scenario: Stepping across days in the drawer
- **GIVEN** the drawer shows the last entry of 10/21
- **WHEN** ↓ is pressed
- **THEN** the drawer SHALL show the newest entry of the previous day with rows, and the calendar's selection SHALL move to that day

### Requirement: Calendar grid keyboard

The 日曆 grid SHALL be one tab stop with a roving focus: ← and → move one day, ↑ and ↓ one week, Home and End go to the month's first and last day, and Enter or Space picks the focused day. With the day panel open and focus outside the drawer, `[` and `]` SHALL set `day` one day earlier or later, as ‹ › do. These handlers SHALL be bound to the grid and the panel, not the document; while the drawer is open, ↑ and ↓ belong to the drawer.

#### Scenario: Arrow keys in the grid
- **GIVEN** focus is on 10/21 in the grid
- **WHEN** ↓ and then Enter are pressed
- **THEN** focus SHALL be on 10/28 and 10/28 SHALL be selected

### Requirement: Calendar layout on narrow screens

Below 1024 px, in the 日曆 view, the month's 支出 / 收入 / 結餘 SHALL be one line under the month navigator.

- **No day selected.** The month grid SHALL be shown with category dots, followed by a 今天 bar showing today's row count, names and net, which selects today. In another month the bar SHALL read 回到今天 and set `month` and `day` to today.

#### Scenario: 回到今天
- **GIVEN** September 2026 is shown at 390 px and today is 2026-10-22
- **WHEN** 回到今天 is tapped
- **THEN** the URL SHALL be `?month=2026-10&day=2026-10-22` and the grid SHALL collapse to the week of 10/22

#### Scenario: Phone default
- **WHEN** `/accounting` opens at 390 px in the 日曆 view in October 2026 on 10/22
- **THEN** the October grid and a bar 「今天 · 3 筆 … −$250」 SHALL be shown, no day SHALL be selected, and no rows SHALL be listed

### Requirement: Calendar layout on narrow screens: week strip

Below 1024 px, in the 日曆 view with a day selected, the grid SHALL collapse to the week containing the day, without dots, with a 展開整月 handle under it. The day header (‹ label ›, count and net) and the day's rows SHALL follow.

A row SHALL open the entry detail as it does today: its own screen below 760 px, the right-hand sheet from 760 px.

#### Scenario: Picking a day on a phone
- **GIVEN** the week starts on Sunday
- **WHEN** 10/21 is tapped
- **THEN** the grid SHALL collapse to 10/18–10/24, and 10/21's three rows SHALL be listed under 「10/21 週三」

### Requirement: Calendar swipes on narrow screens

Below 1024 px with a day selected, a swipe on the day area SHALL move one day, and a swipe on the week strip seven days (same weekday); right to left moves later, left to right earlier, and `month` follows `day`. A gesture SHALL count as a swipe only when its horizontal movement dominates; vertical movement SHALL scroll the page.

#### Scenario: Swiping to the next day
- **GIVEN** 10/21 is selected at 390 px
- **WHEN** the day area is swiped from right to left
- **THEN** 10/22 SHALL be selected and its rows listed

### Requirement: Reward rules overview page

`/accounting/reward-rules` SHALL mark 記帳設定 as the current navigation item and offer 「‹ 記帳設定」.

**Header.** A summary line: cards, rules, and the period's money reward total with estimates, followed by 「＋ N 種點數」 counting the other units (omitted when there are none). State chips with counts (全部, 生效中, 已停用, 已過期, 未開始), and 含已封存帳戶.

#### Scenario: Current navigation item
- **WHEN** the overview is open
- **THEN** the 記帳設定 navigation item SHALL be current and 記帳記錄 SHALL NOT be

### Requirement: Reward overview cards

Accounts with rules SHALL be cards, two columns from 1024 px. Each card shows:

- the account's icon, name, and its window line (closing day and the current period, or the calendar month);
- the period's reward total, the sum of used and estimated over the account's usage;
- one row per rule, as specified by 「Reward overview rule lines」;
- the 12-month stats and 「＋ 新增規則」.

#### Scenario: Two-column card grid
- **GIVEN** 玉山 Pi 卡's 全聯・家樂福加碼 5% has a $200 cap, $180 posted and $13 estimated this period
- **WHEN** the overview renders at 1440 px
- **THEN** the card SHALL be in a two-column grid

### Requirement: Reward overview units and windows

Overview figures SHALL use the rule's reward unit: money, or the number and the points account's name. A card's total SHALL be given per unit, and the header's total SHALL sum money only, followed by 「＋ N 種點數」 counting the other units. A reward account marked `cash_equivalent` is money. When an account's rules have different windows, the card header SHALL show only the closing day and each rule row its own period.

#### Scenario: Points rule on the overview
- **GIVEN** a card with a money rule and a rule paying points into 亞洲萬里通
- **THEN** the card SHALL show a money total and a separate 亞洲萬里通 total, and the header's money total SHALL exclude the points and end with 「＋ 1 種點數」

#### Scenario: Points unit survives a filter
- **GIVEN** the overview opened with `?account_id=<that card>` only
- **THEN** the points rule's figures SHALL still read in 亞洲萬里通, taken from the usage row's unit

#### Scenario: Mixed windows on one card
- **GIVEN** a card with a statement-cycle rule and a calendar-month rule
- **THEN** the card header SHALL show only 「結帳日 N 號」 and each rule row its own 「本期 …」

### Requirement: Reward overview rule lines

Each rule row on an overview card SHALL show:

- the name and a rate chip, never the rate repeated in the label;
- a state tag only when the rule is not active, or is 基本;
- the posting summary;
- for an active rule with a cap, the cap bar;
- for an active rule without a cap, 「無上限 · 本期 … 已入帳 + … 預估」; for any other rule, one muted line instead of the bar: 「停用中，不估算回饋」 (disabled), 「已結束，不再估算」 (expired) or 「Y/M/D 起才估算」 (upcoming, the start date without zero padding).

#### Scenario: Upcoming rule
- **GIVEN** a rule starting 2027-01-01, viewed in 2026
- **THEN** its row SHALL be dimmed, show 「2027/1/1 起才估算」 and no bar

#### Scenario: Rate chip beside the label
- **GIVEN** 玉山 Pi 卡's 全聯・家樂福加碼 5% has a $200 cap, $180 posted and $13 estimated this period
- **WHEN** the overview renders at 1440 px
- **THEN** the label SHALL read 全聯・家樂福加碼 with a separate 5% chip

### Requirement: Reward overview empty and expired states

**Without rules.** Under 全部, credit accounts without rules SHALL be chips in one row, 「沒有規則的卡片」, each opening the account's settings at 新增規則. Under another state chip, accounts with no listed rule SHALL be omitted.

**Expired callout.** Enabled-but-expired rules SHALL raise a warning banner with 只看已過期.

#### Scenario: Accounts without rules
- **GIVEN** 匯豐 現金回饋卡 has no rules
- **THEN** it SHALL appear as a chip under 沒有規則的卡片, not as a card

#### Scenario: Expired rules banner
- **GIVEN** an enabled rule whose end date has passed
- **WHEN** the overview renders under 全部
- **THEN** a warning banner SHALL show with 只看已過期, which selects the 已過期 chip

### Requirement: Reward cap bar

The cap bar, shared by the overview cards and the account settings rows, SHALL show:

- posted and estimated parts in the primary colour (the estimate lighter);
- text 「$used+est / $cap · 剩 $N」, in the rule's reward unit;
- at 90 % or more of the cap, the warning ink and 「快到上限」;
- an accessible name with the posted, estimated and cap amounts.

#### Scenario: Card with a near cap
- **GIVEN** 玉山 Pi 卡's 全聯・家樂福加碼 5% has a $200 cap, $180 posted and $13 estimated this period
- **WHEN** the overview renders at 1440 px
- **THEN** its row SHALL read 「$193 / $200 · 快到上限」 in the warning ink

### Requirement: Reward rule rows

In account settings each rule SHALL be one row:

- a drag handle;
- the name and a rate chip, and a state tag only when the rule is not active or is 基本;
- the posting summary;
- the cap bar for an active capped rule;
- an enable switch;
- a ⋯ menu with 編輯, 刪除, 上移 and 下移.

Tapping the row SHALL open the rule sheet. Dropping a dragged row SHALL save the order once.

#### Scenario: Reorder by drag
- **GIVEN** rules A, B, C
- **WHEN** C is dragged above A and dropped
- **THEN** one order request SHALL be sent with C, A, B

#### Scenario: Disabled rule row
- **GIVEN** 海外消費 2.5% is disabled
- **THEN** its row SHALL be dimmed, show 已停用 and 「停用中，不估算回饋」, and show no cap bar

### Requirement: Entry reward section

The entry detail SHALL show one 回饋 section with one card per reward accrual in rule order. Reward rows generated from the entry without an accrual SHALL show as 已入帳 cards. The #306 reward-reprice card SHALL be the section's last item, visibility unchanged. The section SHALL be absent when the entry has neither accruals nor reward rows and is not awaiting a reward reprice (`review_reason` `reward_posted`); an entry awaiting a reprice with neither shows the section holding only the reprice card.

#### Scenario: Placement
- **GIVEN** an entry with an accrual, a debt state and an e-invoice
- **THEN** the 回饋 section SHALL sit after the tags and debt state and before the e-invoice card

#### Scenario: Imported reward without an accrual
- **GIVEN** an entry with a reward row imported from MOZE and no accrual
- **THEN** the 回饋 section SHALL show one 已入帳 card with that row's amount and date

### Requirement: Entry reward card

Each card of the 回饋 section SHALL show:

- the rule name and the reward state tag;
- the amount in the reward's unit (money — also for a `cash_equivalent` account — or the number and the points account's name): the reward row's amount when present, else the accrual's capped amount, with the raw estimate struck through when they differ. For 未獲得 the expected figure is struck through: the accrual's amount when above 0, else its estimate;
- a line by state, as specified by 「Entry reward state lines」.

#### Scenario: Points reward
- **GIVEN** a rule paying 120 points into the account 亞洲萬里通
- **THEN** its card SHALL show 120 亞洲萬里通, never $120

#### Scenario: Cash-equivalent reward
- **GIVEN** a rule paying 120 points into the account 測試現金點, marked `cash_equivalent`
- **THEN** its card SHALL show +$120

#### Scenario: Estimated with a cap
- **GIVEN** 全聯 採買 −1,286 on 玉山 Pi 卡 with 一般消費 1% ($13 estimated) and 全聯・家樂福加碼 5% ($64 computed, $20 left in the cap)
- **WHEN** its detail opens on 11/21
- **THEN** the 回饋 section SHALL show two 預估 cards: +$13 with 「預計 12/15 入帳到 玉山 Pi 卡」, and +$20 with $64 struck through and 「算出 $64，本期上限只剩 $20」

### Requirement: Entry reward state lines

The line on an entry reward card SHALL read by state:

- 預估: the expected posting date and account, or 「算出 $X，本期上限只剩 $Y」 when the cap cut the estimate;
- 已入帳: the posting date and account;
- 銀行確認: 「與帳單相符」, or 「銀行實際給 $N」 when the reward row differs from the accrual;
- 未獲得: 「帳單上沒有這筆回饋」 when a reward row was kept at 0 by the statement, 「本期上限已用完」 when there is no row and the amount is 0, else 「回饋記錄已刪除，不會入帳」.

Every card whose reward row exists SHALL link 「看回饋記錄 ›」 to it.

#### Scenario: Forfeited after the reward row was deleted
- **GIVEN** an accrual of $13 whose reward row the owner deleted
- **THEN** its card SHALL show 未獲得, $13 struck through and 「回饋記錄已刪除，不會入帳」

#### Scenario: Forfeited by the statement
- **GIVEN** an accrual of $10 whose reward row a statement set to 0
- **THEN** its card SHALL show 未獲得, $10 struck through and 「帳單上沒有這筆回饋」

#### Scenario: Forfeited past the cap
- **GIVEN** an accrual estimated at $8 that posted with amount 0 because the cap was used up
- **THEN** its card SHALL show 未獲得, $8 struck through and 「本期上限已用完」

#### Scenario: Posted
- **GIVEN** 全聯 採買 on 玉山 Pi 卡 with two accruals whose reward rows posted on 12/15
- **WHEN** the same entry is opened after 12/15
- **THEN** both cards SHALL show 已入帳 and link to their reward rows, and no separate 紅利回饋 heading SHALL appear

### Requirement: Reward state tag

Accrual states SHALL render with one tag component everywhere they are shown. Each state SHALL differ by border style and icon as well as colour:

| State | Label | Style | Icon |
|---|---|---|---|
| `estimated` | 預估 | dashed primary border, no fill | clock |
| `posted` | 已入帳 | primary-soft fill | check |
| `bank_confirmed` | 銀行確認 | success-soft fill | double check |
| `forfeited` | 未獲得 | neutral fill, muted text | ✕ |

#### Scenario: Not by colour alone
- **WHEN** the four tags are rendered in greyscale
- **THEN** 預估 SHALL still be distinguishable by its dashed border and every tag by its icon
