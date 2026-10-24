## 0. Spec

- [x] 0.1 Layout review against `main` #130 with seeded data (1440 / 1100 / 390, light and dark)
- [x] 0.2 Proposal, design (D1–D9), spec deltas; `openspec validate polish-layout-v2 --strict`

## 1. Defects (PR A)

- [ ] 1.1 Rename component `.row` classes in the six templates that use them (`entry-row` / `field-row`); the four legacy PrimeNG forms keep the Bootstrap grid for now
- [ ] 1.2 Gate: a Vitest spec that fails, naming file and line, on `row` / `col` / `col-*` / `container` class tokens outside the allowlist (`item-list`, `dividend-list`, `transaction-list`, `account-settings`)
- [ ] 1.3 Dark-mode logo: inline the lockup SVG with `currentColor`, or swap in a dark variant under `.app-dark-mode`, so the wordmark reads on `--app-surface`
- [ ] 1.4 Remove `paneOpen` writes from `PortfolioAccountsPageComponent`; FAB pane offset via `--fab-right` set by the accounting layout
- [ ] 1.5 Tests: timeline row left edge equals the page gutter at 390 px; accounts page leaves `paneOpen` false; FAB right offset on `/portfolio/accounts/1` equals the default

## 2. Numbers (PR B)

- [ ] 2.1 `formatMoney` / `formatPct` in `components/ui/format.ts` with the D2 table as unit tests
- [ ] 2.2 Replace ad-hoc formatting on dashboard (hero, KPI, legend, holdings, group headers), 交易, 股利, 已實現, 現金
- [ ] 2.3 KPI and hero values: container queries + `clamp()`, `nowrap`
- [ ] 2.4 Tests: no KPI value overflows its card at 1100 px and 390 px with an 8-digit TWD value

## 3. Filter bar (PR C)

- [ ] 3.1 `<app-filter-bar>` primitive: search, primary slots, 篩選 popover / sheet, active chips, 清除全部
- [ ] 3.2 Adopt on 交易紀錄, 股利, 已實現損益, 現金帳戶, 記帳時間軸 per the D6 table
- [ ] 3.3 Tests: secondary filter count on the 篩選 button; removing a chip clears that filter

## 4. Shell and phone (PR D)

- [ ] 4.1 FAB hide-on-scroll-down / show-on-scroll-up / end-of-page / idle, reduced-motion aware
- [ ] 4.2 Phone page headers: one row, ⋯ menu for secondary actions; 投資概覽 market toggle under the header
- [ ] 4.3 Phone 投資概覽: 2 × 2 KPI grid; two-line holdings rows
- [ ] 4.4 Tests: FAB hidden after a downward scroll and visible after an upward scroll; phone header height ≤ 64 px

## 5. Pages (PR E)

- [ ] 5.1 Accounting empty pane month overview at ≥ 1280 px; 帳戶 empty copy and selected ring
- [ ] 5.2 Accounting page titles on the page background
- [ ] 5.3 Net-worth chart bridges cash-only rows (D8)
- [ ] 5.4 現金帳戶 balance chart: fixed height, MM/DD ticks (max 8), no point markers, token colours
- [ ] 5.5 股利: hide 舊資料帳面 at 0 and the 即將除權息 row when empty
- [ ] 5.6 已實現損益: 賣出成本費用 in `--app-text`
- [ ] 5.7 匯入: footer only after a file is chosen; 首列為欄位名稱 as a switch
- [ ] 5.8 Icons: replace emoji in UI chrome (🔔, 💳 calendar marker, entry-detail actions) with the line set

## 6. Verify

- [ ] 6.1 Re-run the review screenshots at the three widths in light and dark; attach before / after to each PR
