## 0. Spec

- [x] 0.1 Design canvas (RewardRulesDesktop, AccountSettingsDesktop, AccountSettingsPhone, RewardStatesSpec) and `design_handoff_v3/design_refs/`
- [x] 0.2 Proposal, design (D1–D8), spec deltas
- [ ] 0.3 Archive `account-logo-upload` first, then run `openspec validate polish-reward-and-account-settings --strict` (not run in the authoring session)

## 1. API (PR A)

- [ ] 1.1 `usage` on each overview account (D1)
  - same computation as `/reward-usage`
  - unaffected by the filters
  - `[]` without active rules
- [ ] 1.2 Tests: parity with `/accounts/{id}/reward-usage`; shared cap row; `state=expired` keeps usage

## 2. Shared pieces (PR B)

- [ ] 2.1 `RewardStateTag` (D8) and `CapBar` (D2) in `components/ui`, light and dark, with accessible names
- [ ] 2.2 Stepper and amount field; picker (sheet below 760 px), if `components/ui` lacks them
- [ ] 2.3 Tests: tag styles per state; bar at 89 % and 90 %; no-cap text

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

## 4. 帳戶設定 (PR C)

- [ ] 4.1 Split into section components; sticky chips with scroll-spy (≥ 760 px)
- [ ] 4.2 外觀: preview, three-way switch, upload / grid / text and colour, 「目前顯示」 line (D5)
- [ ] 4.3 信用卡: steppers and previews, pickers
- [ ] 4.4 Rule rows (D6): one line, bar, switch, ⋯ menu, drag reorder with one order call
- [ ] 4.5 Phone: grouped list, sheet controls, 放棄 / 儲存 bar replacing the floating button
- [ ] 4.6 Save with errors scrolls to the first invalid section and marks its chip
- [ ] 4.7 Tests: the scenarios of "Account settings page" and "Reward rule rows"

## 5. Entry detail (PR D)

- [ ] 5.1 回饋 section replacing 紅利回饋 and 預估回饋 (D7)
  - cards per accrual
  - imported reward rows as posted cards
- [ ] 5.2 Per-state lines; struck-through estimate when capped or changed by the bank; link to the reward row
- [ ] 5.3 Tests: the four states; capped estimate; imported reward without accrual; section absent without either

## 6. Wrap-up

- [ ] 6.1 Screens at 1440 / 820 / 390, light and dark, against the artboards
