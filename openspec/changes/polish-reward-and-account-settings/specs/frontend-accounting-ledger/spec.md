## MODIFIED Requirements

### Requirement: Account settings page

The SPA SHALL provide `/accounting/accounts/:id/settings` and `/accounting/accounts/new`.

**Header.** At the top: the account's logo (uploaded image, else bundled brand logo, else its icon), its name and a status line (kind, and for cards the amount due and its debit date).

**Sections.** The form SHALL be in sections, in this order:

- **基本:** 名稱, 主幣種, 帳戶分組, 初始金額, 納入總餘額.
- **外觀:** a preview and a three-way switch 上傳圖片 / 內建 logo / 文字＋顏色.
  - 上傳圖片: 選擇檔案 (and drop) with the limits line; 「移除圖片」 only when an uploaded image exists. On `/accounting/accounts/new`, upload is disabled with 「先建立帳戶，再上傳 logo」.
  - 內建 logo: the bundled logo grid with `無` first.
  - 文字＋顏色: the icon text and a colour swatch row.
  - A line saying which source is shown. The upload > logo > icon precedence is unchanged.
- **信用卡:** a 信用帳戶 switch in the section header. When on, it reveals:
  - 結帳日, a stepper with the computed current cycle;
  - 繳款期限, fixed day or days after closing, with a stepper and the computed due date;
  - 信用額度;
  - 額度共用, a multi-select of credit accounts;
  - 主帳戶 and 自動扣繳, pickers.
- **回饋規則:** the current window in the header, the account's rules as rows (requirement "Reward rule rows"), and ＋ 新增規則.
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
- **THEN** the 結帳日, 繳款期限, 信用額度, 額度共用, 主帳戶 and 自動扣繳 rows SHALL appear

#### Scenario: Due date preview
- **GIVEN** closing day 15 and 繳款期限 = 20 days after closing
- **WHEN** the section renders on 2026-10-02
- **THEN** it SHALL show the current cycle `09/16 – 10/15` and the due date `11/04`

#### Scenario: Closing day stepper
- **GIVEN** closing day 25 on 2026-11-07
- **WHEN** ＋ is pressed once
- **THEN** the stepper SHALL read 26 號 and the cycle line SHALL read 本期 10/27–11/26

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

**Header.** A summary line: cards, rules, and the period's reward total with estimates. State chips with counts (全部, 生效中, 已停用, 已過期, 未開始), and 含已封存帳戶.

**Cards.** Accounts with rules SHALL be cards, two columns from 1024 px. Each card shows:

- the account's icon, name, and its window line (closing day and the current period, or the calendar month);
- the period's reward total, the sum of used and estimated over the account's usage;
- one row per rule:
  - the name and a rate chip, never the rate repeated in the label;
  - a state tag only when the rule is not active, or is 基本;
  - the posting summary;
  - for an active rule with a cap, the cap bar;
  - for any other rule, one muted line instead of the bar;
- the 12-month stats and 「＋ 新增規則」.

**Without rules.** Accounts without rules SHALL be chips in one row, 「沒有規則的卡片」, each opening the account's settings at 新增規則.

**Expired callout.** Enabled-but-expired rules SHALL raise a warning banner with 只看已過期.

**Cap bar** (shared with the account settings rows):

- posted and estimated parts in the primary colour (the estimate lighter);
- text 「$used+est / $cap · 剩 $N」;
- at 90 % or more of the cap, the warning ink and 「快到上限」;
- an accessible name with the posted, estimated and cap amounts.

#### Scenario: Card with a near cap
- **GIVEN** 玉山 Pi 卡's 全聯・家樂福加碼 5% has a $200 cap, $180 posted and $13 estimated this period
- **WHEN** the overview renders at 1440 px
- **THEN** its row SHALL read 「$193 / $200 · 快到上限」 in the warning ink, the label SHALL read 全聯・家樂福加碼 with a separate 5% chip, and the card SHALL be in a two-column grid

#### Scenario: Accounts without rules
- **GIVEN** 匯豐 現金回饋卡 has no rules
- **THEN** it SHALL appear as a chip under 沒有規則的卡片, not as a card

#### Scenario: Current navigation item
- **WHEN** the overview is open
- **THEN** the 記帳設定 navigation item SHALL be current and 記帳記錄 SHALL NOT be

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

The entry detail SHALL show one 回饋 section, after the tags and debt state and before the e-invoice card, with one card per reward accrual in rule order. Each card shows:

- the rule name and the reward state tag;
- the amount: the posted reward row's amount when present, else the estimate. It is struck through for 未獲得. When a cap or the bank changed it, the original estimate is struck through beside it;
- a line by state:
  - 預估: the expected posting date and account, plus 「本期 $used / $cap」 for capped rules, or 「算出 $X，本期上限只剩 $Y」 when capped down;
  - 已入帳: the posting date and account, with a link 「看回饋記錄 ›」 to the reward row;
  - 銀行確認: whether it matched the statement;
  - 未獲得: the reason when known.

Reward rows generated from the entry without an accrual SHALL show as 已入帳 cards. The section SHALL be absent when the entry has neither.

#### Scenario: Estimated with a cap
- **GIVEN** 全聯 採買 −1,286 on 玉山 Pi 卡 with 一般消費 1% ($13 estimated, $300 of $500 used) and 全聯・家樂福加碼 5% ($64 computed, $20 left in the cap)
- **WHEN** its detail opens on 11/21
- **THEN** the 回饋 section SHALL show two 預估 cards: +$13 with 「預計 12/15 入帳到 玉山 Pi 卡 · 本期 $300 / $500」, and +$20 with $64 struck through and 「算出 $64，本期上限只剩 $20」

#### Scenario: Posted
- **WHEN** the same entry is opened after 12/15
- **THEN** both cards SHALL show 已入帳 and link to their reward rows, and no separate 紅利回饋 heading SHALL appear

#### Scenario: Imported reward without an accrual
- **GIVEN** an entry with a reward row imported from MOZE and no accrual
- **THEN** the 回饋 section SHALL show one 已入帳 card with that row's amount and date

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
