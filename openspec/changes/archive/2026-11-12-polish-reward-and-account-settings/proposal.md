## Why

The reward-rules work (rules, posting engine, batch folding) and the uploaded logos (`account-logo-upload`) shipped with functional screens. Reviewed on seeded data on 2026-11-07, they read as unfinished:

- **回饋規則總覽 (`/accounting/reward-rules`)**
  - The sidebar marks 記帳記錄 as current.
  - Rule labels repeat the rate (玩數位 3% (3%)).
  - Accounts without rules take a full card each.
  - The page is one narrow column at 1440 px.
  - Nothing shows how much of a cap this statement period has used, so the overview cannot answer "which card should I use next".
  - The expired-rule callout is not the banner style.
- **帳戶設定**
  - One long mixed form.
  - Logo and 圖示 are two rows for one picture.
  - Day and amount fields are native number inputs; pickers are native selects.
  - Each reward rule takes five lines plus ↑ ↓ buttons.
  - On a phone the floating 記一筆 button sits over the form.
- **Entry detail**
  - Rewards are split between 紅利回饋 (posted reward rows) and 預估回饋 (one plain line per accrual). The four accrual states are a word at the end of the line.

The owner reviewed the redesign on the canvas (`design_handoff_v3/design_refs/RewardRulesDesktop.dc.html`, `AccountSettingsDesktop.dc.html`, `AccountSettingsPhone.dc.html`, `RewardStatesSpec.dc.html`) and asked for this handoff on 2026-11-07. **This PR contains no code.**

## What Changes

- **API (accounting-service):** `GET /reward-rules` adds to each account `usage` (the same rows as `GET /accounts/{id}/reward-usage` for the current window) and the header fields `icon`, `logo`, `logo_hash` and the effective `closing_day`; every usage row (also on `/reward-usage`) carries its reward unit (`reward_account_id`, `reward_account_name`, `reward_is_money`). The overview stays one request instead of one per account plus `/accounts`.
- **回饋規則總覽**
  - The 記帳設定 item is current, with a 「‹ 記帳設定」 back link.
  - Accounts show as a two-column card grid at ≥ 1024 px.
  - Each card shows the period, the period's reward total (with estimates) and one row per rule. A row has the name, a rate chip, the state only when it is not active, the posting summary, and a cap bar (posted, then estimated) with 「$300 / $500 · 剩 $200」. The bar and text turn to the warning ink at ≥ 90 %.
  - Accounts without rules collapse into one row of chips that open 新增規則.
  - The expired-but-enabled callout uses the warning banner.
- **帳戶設定**
  - Sections 基本 · 外觀 · 信用卡 · 回饋規則 · 國外交易 · 其他, with a chip row that scrolls to each.
  - One 外觀 control with a preview and a 上傳圖片 / 內建 logo / 文字＋顏色 switch. It replaces the Logo and 圖示 rows.
  - Steppers for 結帳日 (still under 基本, for every account) and the 繳款期限 value, and app-styled pickers and amount fields.
  - Rule rows in one line plus the cap bar, an enable switch and ⋯ (編輯, 刪除), reordered by drag.
  - Save in the header with 「有 N 項未儲存」 at ≥ 760 px.
  - Below 760 px, a grouped list and a bottom bar 放棄 / 儲存 that replaces the floating button on this page.
- **Entry detail:** one 回饋 section replaces 紅利回饋 and 預估回饋.
  - One card per accrual: rule, state tag, amount, and a line saying when and where it posts, or why it did not (derived from today's accrual fields).
  - The #306 reward-reprice card moves into the section.
  - Posted cards link to the reward row.
  - Reward rows without an accrual (imported) show as posted cards.
  - The state tag is one shared component: 預估 dashed, 已入帳 primary, 銀行確認 success with a check, 未獲得 muted with the amount struck through.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `accounting-ledger`: the reward-rules overview carries usage.
- `frontend-accounting-ledger`: account settings page; entry detail view; reward rules overview (new requirement); reward state tag (new requirement).

## Dependencies

`account-logo-upload` modified "Account settings page"; it is archived in this PR (first commit), and this change's delta builds on its text.

## Non-goals

- No change to reward computation, posting, caps or the rule editor sheet. The sheet keeps its content (no cap bar, no accrual list), and only the rows that open it change.
- Reward units stay as #294 shows them: points rewards are never shown as money.
- No reward marker on timeline rows. That can be a follow-up if the detail section proves too hidden.
- The image crop step (logo upload PR 2) is separate. 外觀 shows the uploaded image as stored.

## Impact

- **API:** `reward_rule_service.overview` and `RewardRuleOverviewAccountOut` (add `usage`, `icon`, `logo`, `logo_hash`, `closing_day`), `reward_engine.usage` / `RewardUsageOut` (unit fields), plus tests.
- **SPA:**
  - `components/accounting/reward-rules/*`;
  - `components/accounting/account-settings/*`, split into section components;
  - `components/accounting/entry-detail/*` (回饋 section);
  - a `RewardStateTag` and a `CapBar` in `components/ui`;
  - stepper and picker primitives if `components/ui` has none.
- **Design reference:** `design_handoff_v3/` (four artboards above).
