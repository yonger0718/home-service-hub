# frontend-accounting-ledger Specification

## Purpose
TBD - created by archiving change rebuild-accounting-moze-ledger. Update Purpose after archive.
## Requirements
### Requirement: Accounts list page

The SPA SHALL provide the route `/accounting/accounts`. It SHALL list every non-archived account returned by `GET /api/accounting/accounts`, grouped by account group in group order, each group with a collapsible header showing its subtotal in the main currency. Each row SHALL show:

- icon and name
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

### Requirement: Account entry history page

The SPA SHALL provide the route `/accounting/accounts/:id`. It SHALL show a period navigator following the account's statement cycle (`closing_day`; calendar month when NULL), a summary of the period (new spend, income or rewards, period-end balance), and the account's entries newest first, showing for each entry:

- category icon and colour
- name (or category when empty), with merchant and tags on the second line
- amount, with the original-currency amount and rate inline beneath it when the entry was converted
- running balance, shown as a small line above the amount on every viewport (passbook style)
- project and kind pills (應收, 回饋, 轉帳, 退款)

It SHALL load more entries on demand using the paginated endpoint, SHALL filter by kind, date range and text, SHALL mark entries with `needs_review = true`, SHALL open the entry detail on tap, and SHALL link to the account settings page.

#### Scenario: Unpaired transfer is flagged
- **GIVEN** an account has a transfer leg with `needs_review = true`
- **WHEN** its history is shown
- **THEN** that row SHALL carry a visible review marker

#### Scenario: Running balance on a phone
- **WHEN** the history is viewed at 390 px
- **THEN** every row SHALL show its running balance above the amount

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

The SPA SHALL provide `/accounting` as the accounting home, replacing the redirect to the accounts list. It SHALL show a month navigator with the month's expense, income and net totals from the summary endpoint, a filter row (accounts, kinds, text search; behind a ⌕ button below 760 px), and entries from `GET /api/accounting/entries` grouped by day with a per-day net. Each row SHALL show category icon and colour, name or category, merchant and tags, amount (red for outflow, green for inflow per the preference), the original-currency amount inline when converted, and project and account pills. Reward entries SHALL be hidden when the preference `hide_rewards_on_timeline` is true. A split group SHALL render as one row with the group's name, the sum and a count badge. Tapping a row opens the entry detail. The page SHALL load more on scroll.

#### Scenario: Foreign-currency amount visible without hover
- **GIVEN** an expense converted from `¥5,390` at `0.2163`
- **WHEN** the timeline renders at 390 px
- **THEN** the row SHALL show `¥5,390` and the rate beneath the TWD amount

#### Scenario: Month totals
- **WHEN** the owner moves the navigator to 2026-09
- **THEN** the header SHALL show September's expense, income and net from the summary endpoint and the list SHALL show September's entries

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

The SPA SHALL provide `/accounting/entries/:id` showing: a header in the category colour with the icon and category path; name and total (with "內含手續費 $N" when children exist); the FX line (original amount × rate = converted, source) when converted; a grid of account, merchant or counterparty, date | project, kind, time; tags and attached rule chips; a 紅利回饋 section listing reward entries generated from it with their posting dates; a 關聯記錄 section with fee and discount children, the transfer counterpart, group siblings, the entry it settles or refunds and the entries settling or refunding it; for receivables and payables the open amount and a 新增收款 / 新增還款 action; the note; and an action bar 編輯 / 複製 / 退款 / 收款 / 刪除. Delete SHALL confirm inside the page and explain what else is deleted (children, the other transfer leg). Actions SHALL be disabled with the lock banner on locked entries.

#### Scenario: Receivable detail
- **GIVEN** a receivable of `-420` for Alan with `200` collected
- **WHEN** its detail opens
- **THEN** it SHALL show `剩餘 $220`, the collection under 關聯記錄, and an enabled 新增收款 action

#### Scenario: Settle from detail
- **WHEN** 新增收款 is tapped, account `錢包` and amount `220` entered, and saved
- **THEN** the settle endpoint SHALL be called and the detail SHALL show `已結清`

### Requirement: Account settings page

The SPA SHALL provide `/accounting/accounts/:id/settings` and `/accounting/accounts/new` as a settings-style list: icon and live balance at the top; rows 名稱, 主幣種, 帳戶分組, 圖示, 初始金額, 帳單週期 (closing day with the computed current cycle), 信用帳戶 toggle revealing 繳款期限 (fixed day or days after closing, showing the computed due date), 信用額度, 額度共用 (multi-select of credit accounts), 主帳戶 (picker), 自動扣繳 (account picker); 紅利回饋 (read-only list of the account's rules in 2a); 國外交易手續費 toggle revealing %, rounding, refund flag; 納入總餘額; 封存帳戶; 刪除帳戶 (enabled only with no entries). 主幣種 SHALL be read-only once the account has entries. Saving calls the account write endpoints and validation errors SHALL be shown beside the field. An imported account whose settings were edited locally SHALL show a note "已自訂，匯入不再覆寫" with a 還原 MOZE 設定 action that clears the flag.

#### Scenario: Credit toggle reveals card fields
- **WHEN** 信用帳戶 is switched on
- **THEN** the 繳款期限, 信用額度, 額度共用, 主帳戶 and 自動扣繳 rows SHALL appear

#### Scenario: Due date preview
- **GIVEN** closing day 15 and 繳款期限 = 20 days after closing
- **WHEN** the row renders on 2026-10-02
- **THEN** it SHALL show the current cycle `09/16 – 10/15` and the due date `11/04`

### Requirement: Accounting settings page

The SPA SHALL provide `/accounting/settings` (replacing the redirect) with sections: 資料 (帳戶分組, 類別, 專案, 對象: each a list with add, rename, reorder by drag, hide or archive, and delete when unused; categories show icon and colour pickers and the two-level tree per kind), 顯示 (支出收入顏色, 數字鍵盤順序, 月曆起始星期, 首頁隱藏紅利回饋, 總額縮寫), and 匯入 (upload a MOZE backup zip with a dry-run first, showing the report; the latest import; the import lock state; a 分期 / 週期 list of upcoming scheduled items from `GET /api/accounting/imports/schedules`, read-only until phase 4). The settings sub-nav item of the accounting group SHALL point here instead of the global `/settings`.

#### Scenario: Rename a counterparty
- **WHEN** `Alan` is renamed to `Alan Chen`
- **THEN** every entry's counterparty name SHALL update

#### Scenario: Backup dry run from the page
- **WHEN** a zip is chosen and 試算 tapped
- **THEN** the page SHALL show the report (per-type counts, skipped future rows, balance differences) without changing data, and offer 匯入 as a second step

### Requirement: Category icons and colours

Category bubbles, timeline rows, detail headers and the category picker SHALL use `category.icon` (emoji) on a circle filled with `category.color`; sub-categories inherit from their parent. The design's default icon table SHALL be applied client-side when a category has no icon.

#### Scenario: Imported colours used
- **GIVEN** `飲食` imported with colour `#f0cd92`
- **WHEN** an expense in `飲食/午餐` renders on the timeline
- **THEN** its icon circle SHALL be `#f0cd92`

### Requirement: Entry shortcuts

The web manifest SHALL declare a shortcut `記一筆` to `/accounting/entry`. On viewports of 760 px and above every accounting page SHALL show a floating "+" bottom-right that opens the entry form (in the right pane at ≥ 1024 px). On the entry page ⏎ saves, ⇧⏎ saves and continues, Esc cancels; on list pages N opens the entry form and E edits the selected entry.

#### Scenario: Home-screen shortcut
- **WHEN** the installed app's shortcut `記一筆` is launched
- **THEN** the app SHALL open directly on `/accounting/entry`

