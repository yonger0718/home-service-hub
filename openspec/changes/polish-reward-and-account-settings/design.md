## Context

- **`/accounting/reward-rules`** reads `GET /reward-rules`, which returns `accounts[].rules[]` with `state` and `stats`, plus `counts`. Usage per window exists only per account: `GET /accounts/{id}/reward-usage` returns `[{rule_ids, shared_cap_id, name, window_start, window_end, used, estimated, cap, remaining}]`.
- **Account settings** (`account-settings.html`, 276 lines) is one list of rows. Native `type="number"` is used for the closing day and the due value, and native `<select>` for the group, due rule, master and auto-pay pickers. The Logo block (from `account-logo-upload`) sits above a separate 圖示 row with an emoji field and a colour input. Each rule row shows its title, badges, posting, period, window and usage on separate lines, with a switch, 編輯, ↑ and ↓.
- **Entry detail** has a 紅利回饋 section (`d.rewards`, the reward rows generated from the entry) and a 預估回饋 section (`d.accruals`, one line each: rule name, amount, and the state word from `accrualStateLabels`: 預估 / 已入帳 / 銀行確認 / 未獲得).

## Decisions

### D1. Usage in the overview response

Each account in `GET /reward-rules` gets `usage: RewardUsageOut[]`, computed exactly like `/accounts/{id}/reward-usage` for the window containing today.

- Rules sharing a cap appear in one usage row, as today.
- Accounts without active rules get `[]`.
- The overview filters (`state`, `account_id`) do not change `usage`. It always describes the account's active rules, because a cap is shared regardless of what is listed.

The SPA maps a rule to its usage row through `rule_ids`. A rule that is not active shows no bar (a shared-cap row's `rule_ids` can include a disabled member, so the SPA checks the rule's `state`, not only the mapping).

Each account also gains the fields the card header needs, so the page stays one request (the overview does not load `/accounts` today):

- `icon`, `logo`, `logo_hash` — the `<app-account-icon>` inputs;
- `closing_day` — the effective closing day the reward windows use (`reward_engine.card_closing_day`: the card's own, else its combined parent's, else `null` for calendar months).

### D1a. Reward units

Rewards are not always money. A rule whose reward account is neither the card itself nor another credit account (points, miles) pays in that account's units; the entry detail already shows those as the number followed by the account name (#294, `EntryDetail.rewardAmount`). Every reward figure in this change follows the same rule.

The server decides the unit, so no filter can hide it: each usage row (`RewardUsageOut`, so both `/reward-rules` and `/accounts/{id}/reward-usage`) gains `reward_account_id`, `reward_account_name` and `reward_is_money` — the reward account of the row's first rule in rule order (a shared-cap group pays into one account in practice); `reward_is_money` is true when that account is the card itself (or unset) or a credit account, archived or not. The SPA never resolves units through the overview's `accounts` (a `?account_id=` filter or an archived points account would drop it and count points as money).

- the cap bar text and accessible name and the card's period total use the usage row's unit;
- a card whose usage rows mix units shows one total per unit;
- the header's 「本期回饋合計」 sums money only, followed by one 「＋ N 點數帳戶名」 part per non-money unit;
- the entry reward cards use `rewardAmount` unchanged.

The `$` in the examples below stands for money; read it as 「N 帳戶名」 for a points rule.

### D2. Cap bar

One `CapBar` component is used on the overview and in account settings. The rule sheet is unchanged (see Non-goals): it has no usage today and gains none here.

- **Track:** `--app-surface-soft`.
- **Posted part:** `--app-primary`.
- **Estimated part:** `--app-primary` at 30 % opacity.
- **At (used + estimated) / cap ≥ 0.9,** both parts and the text switch to the warning banner ink (`--app-banner-warn` from the notification change; until that lands, the current warning ink).
- **Text:** 「$300 / $500 · 剩 $200」, or 「… · 快到上限」 at ≥ 90 %, in the rule's reward unit (D1a).
- **Without a cap:** no bar, only 「無上限 · 本期 $X 已入帳 + $Y 預估」 (the overview's and the settings rows' muted line for an active uncapped rule).
- **Accessible name:** 「本期已入帳 $236，預估 $64，上限 $500」.

### D3. Overview layout

- **Header.** The title 回饋規則 with a summary line: N 張卡 · M 條規則 · 本期回饋合計 $X（含預估）. The state chips carry counts (全部 7, 生效中 5, …), followed by 含已封存帳戶.
- **Grid.** Cards are in a grid, `repeat(auto-fill, minmax(min(100%, 520px), 1fr))`.
- **Card header.** The account's logo or icon (D1 fields), its name, and the window line: 「結帳日 25 號 · 本期 10/26–11/25」 (from `closing_day` and the usage rows' `window_start`/`window_end`), or 「每月 1 日起算 · 本月 11/1–11/30」 when every usage row is a calendar month. When the account's usage rows have different windows, the header shows only 「結帳日 25 號」 and each rule row carries its own 「本期 …」. On the right sits the period total, the sum of `used + estimated` over the account's usage rows, per unit (D1a).
- **Rule rows** open the account's settings at that rule (`?rule=<id>`), as today. They are dimmed to 70 % when the rule is not active, with 「停用中，不估算回饋」 or 「已結束，不再估算」 in place of the bar.
- **Card footer.** Stats from `stats` (「近 12 個月 214 筆 · 回饋 $3,862」) and 「＋ 新增規則」.
- **Accounts without rules** are the credit accounts listed with an empty `rules` under 全部. They are chips in one dashed row: 「沒有規則的卡片」. Under any other state chip, accounts with no listed rule are omitted and the chip row is not shown (the response cannot tell "no rules" from "none in this state").
- **The expired callout** becomes the warning banner, with 只看已過期.

### D4. Account settings sections

The page is split into section components. Each section keeps the fields' existing bindings and validation; this is a layout change, not a form-model change.

| Section | Rows |
|---|---|
| 基本 | 名稱, 主幣種 (read-only note when locked), 帳戶分組, 初始金額, 帳單週期 (結帳日 stepper with an empty 月底 state and the cycle preview, for every account as today), 納入總餘額 |
| 外觀 | preview, plus a switch 上傳圖片 / 內建 logo / 文字＋顏色 (D5) |
| 信用卡 | 信用帳戶 switch in the section header. When on: 繳款期限 (未設定 / 結帳後 / 每月固定日, a three-way switch keeping today's 未設定 → `due_rule` null; stepper and preview when set), 信用額度, 額度共用, 主帳戶, 自動扣繳 |
| 回饋規則 | the period in the header and 「所有卡片的規則 ›」; rule rows (D6); ＋ 新增規則 |
| 國外交易 | switch in the header; 費率 (with a % suffix), 計算方式 switch, 退款時退回 |
| 其他 | 備註; 還原 MOZE 設定 (when applicable); 封存帳戶 (switch, as today); 刪除帳戶 (danger outline, only without entries) |

**Section chips at ≥ 760 px.** A sticky chip row under the account header scrolls to each section. The current chip follows the scroll position (IntersectionObserver), and 回饋規則 shows the rule count.

**Below 760 px.** Each section is a grouped list of rows: label, value and ›. Tapping a value row opens the same control in a bottom sheet (stepper, picker, amount keypad). Switches stay inline.

### D5. 外觀

A preview (the `<app-account-icon>` at 64 px) sits beside a three-way switch. The initial position comes from the current data: an uploaded image selects 上傳圖片, a `logo` slug selects 內建 logo, and otherwise 文字＋顏色.

- **上傳圖片**
  - A drop zone with 選擇檔案 and the limits line (PNG / JPG / WebP · 5 MB 以內).
  - When an image exists, a 移除圖片 button.
  - Uploading and removing keep their existing immediate-write behaviour.
- **內建 logo:** the bundled logo grid inline, with 無 first.
- **文字＋顏色:** the `icon` text field (its current 16-character limit) and a row of swatches from the category palette, plus a custom colour.

Switching tabs does not clear the other sources. The precedence stays upload > logo > icon, as `account-logo-upload` specifies. A 「目前顯示：…」 line under the preview says which source wins.

### D6. Rule rows in settings

Each rule row has:

- a drag handle;
- the name, a rate chip, a state tag only when the rule is not active (已停用 / 已過期 / 未開始) or is 基本, and the 「來自 MOZE」 tag as today;
- 「· 次期 15 號入帳」;
- the `CapBar` underneath;
- an enable switch;
- ⋯ with 編輯 and 刪除.

Tapping the row opens the rule sheet. Drag reorders the rules and calls the existing order endpoint once on drop. Keyboard users get 上移 / 下移 in ⋯.

### D7. 回饋 section in entry detail

One section, 回饋, sits where 紅利回饋 and 預估回饋 are now: after tags and the debt state, before the e-invoice card. It holds one card per accrual, ordered by rule order:

- **Line 1:** the rule name and the `RewardStateTag`.
- **Line 2:** the amount, in the reward's unit (`rewardAmount`, D1a):
  - 預估 / 已入帳 / 銀行確認: `ledger_amount` when present, else `amount` (the capped allotment). When `estimate` differs from it (a cap cut it, or the bank paid another figure), `estimate` is struck through beside it.
  - 未獲得: the expected figure struck through and muted — `amount` when it is above 0 (a bank forfeit keeps it while the row goes to 0; a deleted row keeps it), else `estimate` (posted past the cap).
- **Line 3, depending on the state** (every case is derivable from today's `RewardAccrualOut` fields; no API change):
  - 預估: 「預計 12/15 入帳到 玉山 Pi 卡」 (`post_on`, `reward_account_name`; 「手動入帳」 when `post_on` is null), or 「算出 $64，本期上限只剩 $20」 when `amount` < `estimate`. The entry detail does not load cap usage, so no 「本期 $x / $y」 here.
  - 已入帳: 「12/15 已入帳到 …」 and 「看回饋記錄 ›」, linking to `ledger_entry_id`.
  - 銀行確認: 「與帳單相符」 when `ledger_amount` equals `amount`, else 「銀行實際給 $12」 (`ledger_amount`).
  - 未獲得, by how the engine forfeits:
    - `ledger_entry_id` set (the statement printed a reward line without this one; the row is kept at 0): 「帳單上沒有這筆回饋」;
    - no row and `amount` 0 (posted past the cap): 「本期上限已用完」;
    - no row and `amount` > 0 (the owner deleted the reward row): 「回饋記錄已刪除，不會入帳」.

The #306 reward-reprice card (`app-entry-reward-reprice-card`, today just above 紅利回饋) moves into the 回饋 section as its last item, with its own visibility rules unchanged.

Reward rows in `d.rewards` that no accrual points to (imported, or entered by hand) render as 已入帳 cards with their posting date and link. The 紅利回饋 heading goes away.

### D8. RewardStateTag

The tag is a pill with an icon and a label.

| State | Style | Icon |
|---|---|---|
| `estimated` 預估 | dashed 1.5 px border in `--app-primary`, transparent fill | clock |
| `posted` 已入帳 | `--app-primary-soft` fill, primary text | check |
| `bank_confirmed` 銀行確認 | success-soft fill, success ink | double check |
| `forfeited` 未獲得 | neutral-soft fill, muted text | ✕ |

The four states never differ by colour alone: border style and icon differ too. The tag is used in the entry detail and is the component any later accrual view reuses (the calendar drawer of `add-ledger-calendar-view` when it lands). The rule sheet has no accrual list today and gains none here.

## Risks

- **Drag reorder on touch.** Below 760 px the handle needs a long-press. 上移 / 下移 in ⋯ stay as the reliable path.
- **Sections and validation.** An error in a collapsed or scrolled-away section must stay visible. On save, the page scrolls to the first invalid section and its chip shows a red dot.
