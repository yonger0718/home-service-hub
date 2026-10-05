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

The web manifest SHALL declare a shortcut `記一筆` to `/accounting/entry`. On viewports of 760 px and above every accounting page SHALL show a floating "+" bottom-right that opens the entry form (in the right pane at ≥ 1024 px). On the entry page ⏎ saves, ⇧⏎ saves and continues, Esc cancels; on list pages N opens the entry form and E edits the selected entry.

#### Scenario: Home-screen shortcut
- **WHEN** the installed app's shortcut `記一筆` is launched
- **THEN** the app SHALL open directly on `/accounting/entry`

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

