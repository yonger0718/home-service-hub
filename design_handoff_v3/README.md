# Handoff v3: 通知中心 · 報表 · 自訂報表

Design reference for two OpenSpec changes:

- `openspec/changes/add-notification-center/` — one site-wide 通知 page, a quieter count, a one-case-at-a-time reconciliation flow, one toast and one banner style.
- `openspec/changes/add-accounting-reports/` — 財務 › 報表 (總覽, 趨勢與比較, 排行, 專案與預算) and saved custom reports.

Read each change's `design.md` for the decisions and `specs/` for the requirements. Drawn on the design canvas on 2026-10-24 (notifications) and 2026-10-30 (reports, custom reports); the owner asked for this handoff on 2026-10-31. **Nothing in this folder is production code.**

## What this is

`design_refs/*.dc.html` are the canvas artboards' source. They need the canvas runtime (`support.js`) to render, so they will not open as plain pages; read them for structure, spacing, copy and the sample data in each file's `renderVals()`. Every artboard takes a `dark` prop; the canvas also shows a dark copy of each. Ask the owner for the canvas link to see them rendered and to click through the interactive ones.

The visual system is the one in `frontend/src/styles.scss` (v2 handoff plus the polish-layout-v2 tokens). The artboards inline hex values; map them to tokens as listed under **Colour mapping** — do not copy the hexes.

## Artboards

| File | Screen | Route | Size | Interactive |
|---|---|---|---|---|
| `NotifDesktop.dc.html` | 通知中心 with the 對帳 review open in the right pane | `/notifications`, `/notifications/recon/:statementId` | 1440 fluid | 來源 chips; review: every answer advances, 以後都這樣 switch |
| `NotifPhone.dc.html` | 通知中心 | `/notifications` | 390 | 來源 chips |
| `ReconPhone.dc.html` | 對帳逐筆處理 | `/notifications/recon/:statementId` | 390 | answers advance; end state after four sample cases |
| `FeedbackSpec.dc.html` | Entry points and count, toast, banner, empty state | — | 1280 | — |
| `ReportsDesktop.dc.html` | 報表 · 總覽 / 趨勢與比較 / 排行 / 專案與預算 | `/accounting/reports` | 1440 fluid | tabs, 支出/收入, category drill-in, trend category, ranking dimension and row |
| `ReportsPhone.dc.html` | 報表 | `/accounting/reports` | 390 | tabs, category expand, ranking dimension |
| `ReportBuilder.dc.html` | 自訂報表 editor with three saved reports and the empty state | `/accounting/reports/custom/:id`, `/accounting/reports/custom/new` | 1440 fluid | saved report chips, 新報表, chart type, 比較, 釘選 |
| `ReportSavedPhone.dc.html` | One saved report, 編輯條件 sheet | `/accounting/reports/custom/:id` | 390 | 編輯條件 |

Not clickable on the canvas (drawn only): the period controls, the field pickers in the builder, ⋯ menus, 稍後提醒, every link into other pages.

## Global rules (in addition to v2)

- **Count.** The number on 通知 counts tasks the owner must act on, not rows: one card bill, one schedule definition, one statement to reconcile, one dividend batch, one system failure each count 1. 追蹤中 (debts, upcoming) never counts. The pill is red when any task is overdue or a system task exists, otherwise primary. Other navigation items show dots only.
- **Tiers and order.** 已逾期 → 今天 → 要你確認 → 系統 → 追蹤中 (folded). Within a tier, oldest first.
- **One primary action per row.** The row's main action is a soft-primary button (solid primary in 已逾期 on phones); secondary actions live in ⋯ (desktop) or the text button beside it (phone).
- **Review flow.** One case per screen. Bank line and ledger entry side by side, the differing field highlighted with `--app-primary-soft`. Answers are full-width buttons, the first one solid. Keys: 1–3 answer, S skip, ← → previous / next.
- **Spend comparisons are not gains.** 多花 / 少花 in reports use their own colours (see mapping) and ▲ / ▼, never `--app-trend-*`, so the 紅漲綠跌 setting does not flip them.
- **Report statistics rules** are printed under the 報表 title and apply everywhere: amounts in TWD, refunds reduce expense and are listed as their own line, transfers / receivables / payables / balance adjustments excluded, projects marked 不計入報表 excluded unless a saved report filters on them.
- **Charts** follow the dashboard: no animation, theme tokens, `role="img"` with an `aria-label` that reads the values. Every chart has a table: the reports page shows it under the chart; the builder always renders one.

## Colour mapping

| Canvas (light / dark) | Use |
|---|---|
| `#5f6673` / `#99a3b3` muted text | `--app-text-muted` |
| `#c9373c` / `#ff6369` overdue, failure text | `--c-red-ink` |
| red count pill | background `--c-red-ink`, white text (white on `--c-red` is 3.9:1, too low for 11 px) |
| `#6b6596` / `#aba6d0` dividend amount | `--app-dividend`; at ≤ 13 px add an ink variant (`#6b6596` is 5.2:1 on `--app-surface`; the token is 4.0:1) |
| `#b5323a` / `#ff8a8e` 多花 | new `--app-spend-more` |
| `#2f6f9f` / `#7fb5e0` 少花 | new `--app-spend-less` |
| `#8a6327` / `#d0a06a` warning banner | new `--app-banner-warn` (+ `-soft`); `--app-warning` is a blue-grey and reads as info |
| dark solid primary button with dark text | follow the existing rule: `--app-primary-ink-bg` with white text |
| category / series palette | the dashboard's five-colour stock series, then the owner's category colours where a category is shown |

## Sample data

All numbers are invented and internally consistent so reviewers can check sums:

- Notifications: 7 tasks (3 overdue), 10 open cases on a 17 / 28 matched statement, 2 pending dividends = 14,600.
- Reports, October 2026: gross expense 42,380, refund 490, expense 41,890; income 68,126; September 39,608; October 2025 44,050. Accounts, payees and categories each sum to the month's expense. The 東京旅行 project (24,860) is excluded from the month and appears only in 專案與預算 and the 日本旅行比較 saved report.
- Saved reports: 週末外食 17,960 over May–Oct (last year 17,880); 訂閱月費 10,780 for Jan–Oct; 日本旅行比較 72,860 across three projects.

Budgets, saved reports and the project 不計入報表 flag do not exist in the app yet; all three are specified in `add-accounting-reports`. The canvas's period controls are drawn, not wired.
