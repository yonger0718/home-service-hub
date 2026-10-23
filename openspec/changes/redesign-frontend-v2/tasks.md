## 0. Design record (this PR)

- [x] 0.1 Canvas reviewed and approved by the owner (2026-10-22)
- [x] 0.2 Canvas source files copied to `design_handoff_v2/design_refs/`
- [x] 0.3 Proposal, design and spec deltas written

## 1. Shell and navigation

- [x] 1.1 Add `NavigationPreferencesService` (`hiddenGroups`, `sidebarDefault`, `autoCollapse`, `showBadges`, `foldedGroups`; `hh-nav-*` keys) with Vitest specs
- [x] 1.2 Extend `NavItem` with optional `badge`; add a badge source that fetches reminder count, 待確認 count, pending dividends and import status once on start and on window focus — PR 1: reminder, 待確認 and recalc-status sources; 股利 count and the device-local import flag are owner exceptions (plan Decisions 2, 5)
- [x] 1.3 Replace `components/dock` with the labelled sidebar (expanded ≥ 1280 px, rail 760–1279 px), foldable groups, collapse button, tooltips in rail mode
- [x] 1.4 Remove the frosted top header from `components/shell`; give every routed page its own `<h1>` and header actions
- [x] 1.5 Auto-collapse to the rail while the accounting right pane is open (≥ 1024 px), honouring a manual expand until the pane closes
- [x] 1.6 Move the floating 記一筆 button from `accounting-layout` to the shell (every route ≥ 760 px, tooltip `記一筆 N`, hidden while the entry form is open, anchored to the list column when a pane is open)
- [x] 1.7 Make `N` open the entry form from any route; keep `E`, arrows, ⏎, ⇧⏎, Esc as today
- [x] 1.8 Phone: hide groups per preferences; dots on group tabs; 設定 becomes a header gear when the tab count is odd; sub-nav chips for 投資 with counts
- [x] 1.9 Remove 記一筆 buttons from page headers
- [x] 1.10 Vitest specs: active highlight, folded group sum, rail tooltip, auto-collapse, tab bar parity rule

## 2. Appearance and Settings

- [x] 2.1 `AppearanceService.theme$` (`light` | `dark` | `system`), `hh-theme` with one-time `hh-dark` migration, live `prefers-color-scheme` listener
- [x] 2.2 Update the pre-paint script in `index.html` to read `hh-theme` first
- [x] 2.3 Settings: three-way theme control, 漲跌顏色 with preview (kept), 導覽 section, 記帳設定 link, shortcut reference, scheduler times
- [x] 2.4 Specs for theme migration and the 導覽 switches

## 3. Portfolio dashboard

- [x] 3.1 總資產 hero with 今日 change and 股票 / 現金 bar; 待收股利 shown separately
- [x] 3.2 KPI row labels and footnotes per spec
- [x] 3.3 Net-worth chart: series names, `--app-accent` total line, trend-coloured holdings line, shaded gap, legend with latest values, hover tooltip (date, 帳戶總值, 持股淨值, 現金)
- [x] 3.4 持股配置 bar and 待收股利 list beside the chart
- [x] 3.5 Holdings list restyle (data unchanged)

## 4. 交易紀錄 and 股利

- [x] 4.1 Transactions: period summary cards for the active filter, month grouping with 買 / 賣 totals
- [x] 4.2 Dividends: four-figure summary (已確認收款, 有效待收, 舊資料帳面, 已記錄股利), status filter
- [x] 4.3 Dividends: inline 確認收款 form replacing the dialog; same endpoint and warning text
- [x] 4.4 Phone layouts for both pages (sub-nav chips, compact rows)

## 5. 已實現損益 and 現金帳戶

- [x] 5.1 Realized: KPI row (range total, current month, 當沖, sell-side costs), monthly bar chart, expandable rows, 僅當沖 switch
- [x] 5.2 Cash accounts: master-detail at ≥ 1024 px, first account selected on `/portfolio/accounts`, running balance column

## 6. 匯入

- [ ] 6.1 Unified page with 交易 / 股利 / 券商對帳單 switch mapped to the three existing endpoints
- [ ] 6.2 Last-failure banner, error list, symbol confirmation block, post-import panel, recent imports (device-local)
- [ ] 6.3 Redirect `/portfolio/import-broker` → `/portfolio/import?type=broker`

## 7. Accounting

- [ ] 7.1 Timeline summary category distribution bar (top five categories + 其他, colours from category settings)

## 8. Verification

- [ ] 8.1 `npm test` and `npm run build` pass
- [ ] 8.2 Every route checked at 390, 820, 1280 and 1440 px in light and dark
- [ ] 8.3 Contrast check of small trend-coloured text (see design notes in `design_handoff_v2/README.md`)
