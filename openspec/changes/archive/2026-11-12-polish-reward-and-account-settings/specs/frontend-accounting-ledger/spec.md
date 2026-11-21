## MODIFIED Requirements

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

### Requirement: Entry detail view

The SPA SHALL provide `/accounting/entries/:id` showing: a header in the category colour with the icon and category path; name and total (with "內含手續費 $N" when children exist); the FX line (original amount × rate = converted, source) when converted; a grid of account, merchant or counterparty, date | project, kind, time; tags and attached rule chips; a 回饋 section (requirement "Entry reward section") in place of separate 紅利回饋 and 預估回饋 sections; a 關聯記錄 section with fee and discount children, the transfer counterpart, group siblings, the entry it settles or refunds and the entries settling or refunding it; for receivables and payables the open amount and a 新增收款 / 新增還款 action; the note; and an action bar 編輯 / 複製 / 退款 / 收款 / 刪除. Delete SHALL confirm inside the page and explain what else is deleted (children, the other transfer leg). Actions SHALL be disabled with the lock banner on locked entries.

#### Scenario: Receivable detail
- **GIVEN** a receivable of `-420` for Alan with `200` collected
- **WHEN** its detail opens
- **THEN** it SHALL show `剩餘 $220`, the collection under 關聯記錄, and an enabled 新增收款 action

#### Scenario: Settle from detail
- **WHEN** 新增收款 is tapped, account `錢包` and amount `220` entered, and saved
- **THEN** the settle endpoint SHALL be called and the detail SHALL show `已結清`

## ADDED Requirements

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
