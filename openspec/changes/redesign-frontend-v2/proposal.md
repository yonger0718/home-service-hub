## Why

The first redesign (`2026-06-01-redesign-dashboard-handoff`) gave the app its token system, dark mode and gain/loss convention, but the navigation and several portfolio screens still behave like a collection of separate tools: the dock is icon-only, the 記一筆 action exists only inside accounting, pending work (待收股利, a failed import, reminders) is invisible until the owner opens the right page, the 物資 group the owner no longer uses can't be hidden, and 已實現損益 / 現金帳戶 / 匯入 were never redesigned. The owner reviewed a MOZE-inspired redesign on a design canvas (see `design_handoff_v2/`) and approved it on 2026-10-22. This change records that design as specs so the implementation can follow in later PRs. **This PR contains no code.**

## What Changes

- **Shell / navigation**
  - Replace the icon-only dock with a labelled sidebar that has three widths: expanded (≥ 1280 px), icon rail (760–1279 px), hidden (< 760 px, bottom tab bar). The owner can collapse it manually; the choice is remembered.
  - On two-pane pages (accounting at ≥ 1024 px) the sidebar collapses to the rail automatically while the right pane is open.
  - Sidebar groups fold; the active group never folds; a folded group shows the sum of its badges.
  - **BREAKING (layout)**: remove the frosted top header. The logo moves into the sidebar; each page renders its own title.
  - Navigation items may carry a badge: a count (待處理件數) or a dot (needs attention). Phone tab bar shows dots only; counts appear on the in-page sub-nav.
  - Groups can be hidden from Settings (物資 hidden by default for this owner). The phone tab bar keeps 記一筆 centred: with an odd number of tabs, 設定 moves to a gear at the top-right of each page.
  - One 記一筆 entry point per device: the centre "+" on phones, a floating button bottom-right on every page at ≥ 760 px (tooltip `記一筆 N`), global `N` shortcut. The sidebar and page headers carry no 記一筆 button.
- **Appearance / Settings**
  - Theme gains `跟隨系統` (light / dark / system).
  - Settings adds a 導覽 section (visible groups, sidebar default, auto-collapse, badges), a keyboard shortcut reference and the portfolio scheduler times. Accounting preferences stay in 記帳設定 (linked).
- **Portfolio**
  - Dashboard: 總資產 hero with a 股票 / 現金 composition bar; KPI labels follow the current code (總市值, 預估含息損益, 年化報酬率 XIRR, 已記錄股利); net-worth chart names its two series 帳戶總值 and 持股淨值, shades the gap as cash, and gets a legend and a hover tooltip; a 持股配置 bar and a 待收股利 list sit beside the chart.
  - 交易紀錄: period summary cards (買進, 賣出, 手續費＋交易稅, link to 已實現); month-grouped rows.
  - 股利: four-figure summary using the CONTEXT.md vocabulary (已確認收款, 有效待收, 舊資料帳面, 已記錄股利); status filter; 確認實際收款 inline in the row instead of a dialog.
  - 已實現損益 (new spec): KPI row, monthly bar chart (gain above / loss below the axis), expandable sell rows, 僅當沖 switch.
  - 現金帳戶: accounts list and selected account detail side by side at ≥ 1024 px; running balance column in the cash transaction list.
  - 匯入: CSV import and broker statement import merge into one page with a type switch (交易 / 股利 / 券商對帳單), a banner for the last failed import, an error list, the symbol confirmation block and a 匯入後自動執行 panel with recent imports.
- **Accounting**: the timeline summary adds a category distribution bar.

## Capabilities

### New Capabilities

- `frontend-realized-pnl`: the 已實現損益 page (KPI row, monthly bars, expandable sell rows, 僅當沖 filter).

### Modified Capabilities

- `frontend-app-shell`: sidebar widths, collapse and auto-collapse, foldable groups, hideable groups, badges, global 記一筆, removal of the frosted top header.
- `frontend-appearance-service`: theme mode `system`; navigation preferences persisted alongside appearance.
- `frontend-settings`: three-way theme control, 導覽 section, shortcut reference, scheduler times, link to 記帳設定.
- `frontend-portfolio-dashboard`: KPI labels, 總資產 hero, two-series chart with legend and tooltip, allocation and 待收股利 cards.
- `frontend-stock-transactions`: period summary cards, month grouping.
- `frontend-dividend-records`: four-figure summary, status filter, inline receipt confirmation.
- `frontend-portfolio-accounts`: master-detail layout, running balance.
- `frontend-csv-import`: unified import page with type switch, failure banner and post-import panel.
- `frontend-broker-import`: the standalone route redirects into the unified import page.
- `frontend-accounting-ledger`: category distribution bar; the floating "+" and `N` become app-wide (moved to the shell).

`frontend-design-system` is NOT modified: every colour in the design comes from the existing `--app-*` / `--c-*` tokens (light and `.app-dark-mode`).

## Non-goals

- No code in this PR; no backend, API, schema or migration changes in the follow-up implementation either — every figure on the new screens comes from data the services already return.
- No 物資 (庫存 / 採買清單) redesign; the group is only hidden.
- No new design tokens, fonts or icon set.
- The sample numbers and the 6M range shown on the canvas are illustrative; range options stay 1M / 3M / YTD / 1Y / 5Y.

## Impact

- **Docs**: `openspec/changes/redesign-frontend-v2/**`, `design_handoff_v2/**` (canvas source files for reference).
- **Follow-up code (later PRs)**: `components/shell/*`, `components/dock/*`, `shell/navigation.ts` (badges, group visibility), `services/appearance.service.ts`, a new `services/navigation-preferences.service.ts`, `components/settings/*`, `components/portfolio/*` (dashboard, transaction-list, dividend-list, realized-pnl, accounts, import, broker-import), `components/accounting/accounting-layout/*` (FAB moves to the shell), `components/accounting/timeline/*`, `app.routes.ts` (broker-import redirect).
- **Dependencies**: none.
