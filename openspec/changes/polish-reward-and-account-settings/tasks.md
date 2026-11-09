## 0. Spec

- [x] 0.1 Design canvas (RewardRulesDesktop, AccountSettingsDesktop, AccountSettingsPhone, RewardStatesSpec) and `design_handoff_v3/design_refs/`
- [x] 0.2 Proposal, design (D1–D8), spec deltas
- [x] 0.3 Archive `account-logo-upload` first, then run `openspec validate polish-reward-and-account-settings --strict`
- [x] 0.4 Self-check against the code (2026-11-08): reward units (D1a), 結帳日 stays in 基本, forfeit lines from accrual fields, reprice card placement, overview header fields

## 1. API (PR A)

- [x] 1.1 `usage` on each overview account (D1)
  - plus `icon`, `logo`, `logo_hash`, effective `closing_day` (`card_closing_day`)
- [x] 1.1a Usage unit on every usage row: `reward_account_id`, `reward_account_name`, `reward_is_money` (D1a)
  - same computation as `/reward-usage`
  - unaffected by the filters
  - `[]` without active rules
- [x] 1.2 Tests: parity with `/accounts/{id}/reward-usage`; shared cap row; `state=expired` keeps usage; combined card inherits its parent's `closing_day`; `icon`/`logo`/`logo_hash` equal `/accounts`; usage unit for a points account, archived, and for the card itself
- [x] 1.3 `cash_equivalent` on `account` (1 點 = 1 元): column, account reads, `POST`/`PUT` (omitted on `PUT` keeps it), untouched by MOZE imports; usage unit treats it as money (PR A2)
- [x] 1.4 Tests (PR A2): default false; PUT sets / omitted keeps; MOZE imports keep it; usage unit for a cash-equivalent account, also archived; overview usage row

## 2. Shared pieces (PR B)

- [ ] 2.1 `RewardStateTag` (D8) and `CapBar` (D2) in `components/ui`, light and dark, with accessible names
  - the `RewardStateTag` half shipped in PR D (#323); `CapBar` stays with PR B
- [ ] 2.2 Stepper and amount field; picker (sheet below 760 px), if `components/ui` lacks them
- [ ] 2.3 Tests: tag styles per state; bar at 89 % and 90 %; no-cap text; points unit text (no `$`)
  - the tag-style tests shipped in PR D; the bar tests stay with PR B

## 3. 回饋規則總覽 (PR B)

- [ ] 3.1 Navigation: 記帳設定 current, back link
- [ ] 3.2 Header summary and chips with counts
- [ ] 3.3 Card grid, window line, period total, rule rows with chip and bar
- [ ] 3.4 Rules-less accounts as chips; warning banner for expired-but-enabled
- [ ] 3.5 Tests
  - label without a repeated rate
  - near-cap row
  - chips for rules-less accounts
  - one request for the page
  - points rule's bar and card total in points; header money total excludes points; unit kept under `?account_id=`
  - expired banner and 只看已過期
  - mixed windows on one card: per-row 本期 line

## 4. 帳戶設定 (PR C)

- [ ] 4.1 Split into section components; sticky chips with scroll-spy (≥ 760 px)
- [ ] 4.2 外觀: preview, three-way switch, upload / grid / text and colour, 「目前顯示」 line (D5)
- [ ] 4.3 信用卡: steppers and previews, pickers; 帳單週期 (結帳日 stepper, empty = 月底) stays in 基本 for every account; `1 點 = 1 元` switch in 基本 on non-credit accounts
- [ ] 4.4 Rule rows (D6): one line, bar, switch, ⋯ menu, drag reorder with one order call
- [ ] 4.5 Phone: grouped list, sheet controls, 放棄 / 儲存 bar replacing the floating button
- [ ] 4.6 Save with errors scrolls to the first invalid section and marks its chip
- [ ] 4.7 Tests: the scenarios of "Account settings page" and "Reward rule rows"; 繳款期限 未設定 saves `due_rule` null; icon accepts 16 characters; 「來自 MOZE」 tag kept

## 5. Entry detail (PR D)

- [x] 5.1 回饋 section replacing 紅利回饋 and 預估回饋 (D7)
  - cards per accrual
  - imported reward rows as posted cards
- [x] 5.2 Per-state lines; struck-through estimate when capped or changed by the bank; link to the reward row
- [x] 5.3 Tests: the four states; the three 未獲得 lines; capped estimate; points reward unit; imported reward without accrual; reprice card inside the section; section absent without either
- [x] 5.4 `rewardAmount` treats a `cash_equivalent` reward account as money (PR D, #343)

## 6. Wrap-up

- [ ] 6.1 Screens at 1440 / 820 / 390, light and dark, against the artboards
