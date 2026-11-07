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

The SPA maps a rule to its usage row through `rule_ids`. A rule that is not active shows no bar.

### D2. Cap bar

One `CapBar` component is used on the overview, in account settings and in the rule sheet's header.

- **Track:** `--app-surface-soft`.
- **Posted part:** `--app-primary`.
- **Estimated part:** `--app-primary` at 30 % opacity.
- **At (used + estimated) / cap ≥ 0.9,** both parts and the text switch to the warning banner ink (`--app-banner-warn` from the notification change; until that lands, the current warning ink).
- **Text:** 「$300 / $500 · 剩 $200」, or 「… · 快到上限」 at ≥ 90 %.
- **Without a cap:** no bar, only 「無上限 · 本期 $X 已入帳 + $Y 預估」.
- **Accessible name:** 「本期已入帳 $236，預估 $64，上限 $500」.

### D3. Overview layout

- **Header.** The title 回饋規則 with a summary line: N 張卡 · M 條規則 · 本期回饋合計 $X（含預估）. The state chips carry counts (全部 7, 生效中 5, …), followed by 含已封存帳戶.
- **Grid.** Cards are in a grid, `repeat(auto-fill, minmax(min(100%, 520px), 1fr))`.
- **Card header.** The account's logo or icon, its name, and the window line: 「結帳日 25 號 · 本期 10/26–11/25」, or 「每月 1 日起算 · 本月 11/1–11/30」 for `calendar_month` rules. On the right sits the period total, the sum of `used + estimated` over the account's usage rows.
- **Rule rows** open the account's settings at that rule (`?rule=<id>`), as today. They are dimmed to 70 % when the rule is not active, with 「停用中，不估算回饋」 or 「已結束，不再估算」 in place of the bar.
- **Card footer.** Stats from `stats` (「近 12 個月 214 筆 · 回饋 $3,862」) and 「＋ 新增規則」.
- **Accounts without rules** (credit accounts first, then any account that ever had rules) are chips in one dashed row: 「沒有規則的卡片」.
- **The expired callout** becomes the warning banner, with 只看已過期.

### D4. Account settings sections

The page is split into section components. Each section keeps the fields' existing bindings and validation; this is a layout change, not a form-model change.

| Section | Rows |
|---|---|
| 基本 | 名稱, 主幣種 (read-only note when locked), 帳戶分組, 初始金額, 納入總餘額 |
| 外觀 | preview, plus a switch 上傳圖片 / 內建 logo / 文字＋顏色 (D5) |
| 信用卡 | 信用帳戶 switch in the section header. When on: 結帳日 (stepper), 繳款期限 (結帳後 / 每月固定日 switch, stepper, preview), 信用額度, 額度共用, 主帳戶, 自動扣繳 |
| 回饋規則 | the period in the header and 「所有卡片的規則 ›」; rule rows (D6); ＋ 新增規則 |
| 國外交易 | switch in the header; 費率 (with a % suffix), 計算方式 switch, 退款時退回 |
| 其他 | 備註; 還原 MOZE 設定 (when applicable); 封存帳戶 (danger outline); 刪除帳戶 (only without entries) |

**Section chips at ≥ 760 px.** A sticky chip row under the account header scrolls to each section. The current chip follows the scroll position (IntersectionObserver), and 回饋規則 shows the rule count.

**Below 760 px.** Each section is a grouped list of rows: label, value and ›. Tapping a value row opens the same control in a bottom sheet (stepper, picker, amount keypad). Switches stay inline.

### D5. 外觀

A preview (the `<app-account-icon>` at 64 px) sits beside a three-way switch. The initial position comes from the current data: an uploaded image selects 上傳圖片, a `logo` slug selects 內建 logo, and otherwise 文字＋顏色.

- **上傳圖片**
  - A drop zone with 選擇檔案 and the limits line (PNG / JPG / WebP · 5 MB 以內).
  - When an image exists, a 移除圖片 button.
  - Uploading and removing keep their existing immediate-write behaviour.
- **內建 logo:** the bundled logo grid inline, with 無 first.
- **文字＋顏色:** the `icon` text field (up to 4 characters) and a row of swatches from the category palette, plus a custom colour.

Switching tabs does not clear the other sources. The precedence stays upload > logo > icon, as `account-logo-upload` specifies. A 「目前顯示：…」 line under the preview says which source wins.

### D6. Rule rows in settings

Each rule row has:

- a drag handle;
- the name, a rate chip, and a state tag only when the rule is not active (已停用 / 已過期 / 未開始) or is 基本;
- 「· 次期 15 號入帳」;
- the `CapBar` underneath;
- an enable switch;
- ⋯ with 編輯 and 刪除.

Tapping the row opens the rule sheet. Drag reorders the rules and calls the existing order endpoint once on drop. Keyboard users get 上移 / 下移 in ⋯.

### D7. 回饋 section in entry detail

One section, 回饋, sits where 紅利回饋 and 預估回饋 are now: after tags and the debt state, before the e-invoice card. It holds one card per accrual, ordered by rule order:

- **Line 1:** the rule name and the `RewardStateTag`.
- **Line 2:** the amount: `ledger_amount` when present, else `estimate`. It is struck through and muted for 未獲得. When the bank's figure or a cap changed it, the original estimate is struck through beside it.
- **Line 3, depending on the state:**
  - 預估: 「預計 12/15 入帳到 玉山 Pi 卡」 plus 「· 本期 $300 / $500」 when the rule has a cap, or 「算出 $64，本期上限只剩 $20」 when capped.
  - 已入帳: 「12/15 已入帳到 …」 and 「看回饋記錄 ›」, linking to `ledger_entry_id`.
  - 銀行確認: 「與 12 月帳單相符」, or 「銀行實際給 $12」 when it differs.
  - 未獲得: the reason when known (refund, rule disabled, cap reached), else 「不會獲得這筆回饋」.

Reward rows in `d.rewards` that no accrual points to (imported, or entered by hand) render as 已入帳 cards with their posting date and link. The 紅利回饋 heading goes away.

### D8. RewardStateTag

The tag is a pill with an icon and a label.

| State | Style | Icon |
|---|---|---|
| `estimated` 預估 | dashed 1.5 px border in `--app-primary`, transparent fill | clock |
| `posted` 已入帳 | `--app-primary-soft` fill, primary text | check |
| `bank_confirmed` 銀行確認 | success-soft fill, success ink | double check |
| `forfeited` 未獲得 | neutral-soft fill, muted text | ✕ |

The four states never differ by colour alone: border style and icon differ too. The tag is used in the entry detail, in the calendar drawer (the same component) and in the rule sheet's recent-accruals list.

## Risks

- **Drag reorder on touch.** Below 760 px the handle needs a long-press. 上移 / 下移 in ⋯ stay as the reliable path.
- **Sections and validation.** An error in a collapsed or scrolled-away section must stay visible. On save, the page scrolls to the first invalid section and its chip shows a red dot.
