## Why

Redesign v2 (`2026-10-23-redesign-frontend-v2`) shipped in seven PRs. A layout review on 2026-10-24 ran the latest `main` (#130) against seeded data at 1440, 1100 and 390 px, in light and dark, and compared each screen with the code. It found a handful of real defects (Bootstrap's global `.row` shifting accounting rows 12 px left, KPI numbers overflowing their cards, the logo wordmark vanishing in dark mode, the 現金帳戶 page borrowing the accounting pane geometry) and a set of layout rough edges (three different filter-bar patterns, the 記一筆 button covering row actions, empty right panes, tall phone headers, inconsistent number formatting). This change records the fixes as specs so they can be implemented in small PRs. **This PR contains no code.**

The notification surfaces (提醒中心, 🔔 count, toasts, reconciliation inbox cards) are out of scope here; they get their own change once the owner approves the notification-center design.

## What Changes

- **Defects**
  - Accounting rows stop inheriting Bootstrap's `.row` grid margins; every row keeps the page gutter (timeline list mode, account entry history).
  - KPI and summary values always fit their card at every width: TWD values drop decimals; the type scales down by container width (18 px floor, `--fs-value` ceiling) instead of clipping or wrapping a digit; phone KPI cards sit in a 2 × 2 grid.
  - The sidebar logo lockup is legible in dark mode.
  - `/portfolio/accounts` no longer drives the shell's pane state: the 記一筆 button stays at the page's bottom-right and the sidebar does not auto-collapse on that page.
  - 現金帳戶 balance chart fills its card, uses short date ticks, no point markers and theme tokens.
  - The net-worth chart carries 持股淨值 across cash-only snapshot rows (non-trading days with cash activity) instead of dropping to 0, and plots 帳戶總值 there as the carried value plus that day's cash.
- **Layout**
  - One shared filter bar for 交易紀錄, 股利, 已實現損益, 現金帳戶 and the accounting timeline: search plus at most two primary controls inline, everything else behind 篩選 (a sheet on phones), active filters shown as removable chips.
  - The 記一筆 floating button hides while scrolling down and returns on scroll up or at the end of the page.
  - Accounting two-pane pages at ≥ 1280 px show a month overview in the empty pane; 帳戶 marks the selected account and its empty state says 選擇帳戶.
  - Phone page headers keep one row: secondary actions go into a ⋯ menu.
  - Phone KPI cards use a 2 × 2 grid; phone holdings rows use two lines.
- **Consistency**
  - One amount formatter: TWD and JPY in whole units everywhere (cards, legends, group headers, rows); other currencies keep two decimals; a sign only on changes, PnL and ledger entries; `$` on TWD amounts except where the element's own label or column header already names TWD. Prices, per-share values and the `nativeAmount` pipe are out of its scope.
  - Costs and fees render in neutral text (`--app-text` for the 賣出成本費用 card, `--app-text-muted` for row-level fees and taxes), never in the gain/loss colours.
  - UI chrome uses the line icon set; emoji remain only for owner-chosen category and account icons.
  - Accounting pages render their title on the page background like the other groups.
  - 股利: the 舊資料帳面 card and the 即將除權息 row hide when empty.
  - 匯入: the footer appears once a file is chosen; the native checkbox becomes a switch.
  - The shared filter bar also covers 股利 (MODIFIED Violet dividend timeline: search and status inline; date range, 股利來源 and sort behind 篩選) and the 現金帳戶 detail page (MODIFIED Account detail page lives at `/portfolio/accounts/:id`: 類型 inline; date range and sort behind 篩選).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-design-system`: amount formatting, semantic colour use, dark-mode logo.
- `frontend-ui-primitives`: filter bar, value fitting, icon set, class-name hygiene.
- `frontend-app-shell`: floating button behaviour, pane-state ownership, phone header actions.
- `frontend-accounting-ledger`: row gutter, empty pane overview, selected account, page title placement.
- `frontend-portfolio-accounts`: balance chart, account detail filter bar.
- `frontend-portfolio-dashboard`: chart gaps, phone KPI grid and holdings rows.
- `frontend-dividend-records`: empty summary card and reminder row, filter bar.
- `frontend-realized-pnl`: neutral cost card.
- `frontend-csv-import`: footer visibility, switch control.
- `frontend-stock-transactions`: shared filter bar.

## Non-goals

- No notification-center redesign (separate change).
- No backend change. The chart fix is client-side; the cash-only snapshot rows stay as the backend writes them.
- No new pages or routes.

## Impact

- Frontend only: `frontend/src/styles.scss`, `components/ui/*`, `components/shell/*`, `components/sidebar/*`, the accounting entry row and account-entries styles, the portfolio pages listed above, a shared money formatter.
- Visual baselines for the affected pages change; unit tests that assert formatted strings (`$3,021,869.34`) need updating.
- Live requirements this change MODIFIES (so archive keeps one truth): Bento KPI grid and 總資產 hero (dashboard), Period summary cards (transactions), Summary row and Upcoming ex-dividend grid (dividends), Realized PnL summary and Realized filters, Two-step import card, Timeline home page (filter row), Accounts list page (`$150,000`), App-wide 記一筆 floating button.
- Evidence: `design_handoff_v2/reviews/2026-10-24-layout-review.md`.
