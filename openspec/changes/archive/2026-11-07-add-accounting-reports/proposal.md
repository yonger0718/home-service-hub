## Why

The ledger has a month summary (`GET /entries/summary`: expense, income, net, expense by top-level category) and the timeline's distribution bar, but no way to look across months, compare periods, rank payees, follow a project or keep a budget. In MOZE the owner uses the overview, trend and comparison, ranking and project / budget reports, and the notifications design deferred reports as "the next one" (`docs/superpowers/specs/2026-10-27-accounting-notifications-design.md`, non-goals). The owner also wants reports of their own, saved and pinned beside the built-in ones.

The owner reviewed the design on the canvas (`design_handoff_v3/`: ReportsDesktop, ReportsPhone, ReportBuilder, ReportSavedPhone) and asked for this handoff on 2026-10-31. Scope is accounting data only; portfolio figures (net worth, dividends) are a later change. **This PR contains no code.**

## What Changes

- **API (accounting-service)**
  - `GET /reports/breakdown`: totals and rows for a date range, grouped by one dimension and optionally split by a second, with the ledger's filters, per-row statistics and an optional comparison period.
  - `GET /reports/trend`: one or more series bucketed by month, week or day with their average.
  - Budgets: a `budget` table and `/reports/budgets` CRUD plus `/reports/budgets/status?month=`.
  - Saved reports: a `saved_report` table and `/reports/saved` CRUD (name, config, chart, pinned, order).
  - Projects gain `exclude_from_reports`.
  - One set of inclusion rules shared with the month summary (refunds reduce expense, transfers and debts excluded, main currency at the cached rate).
- **SPA**
  - 財務 › 報表 at `/accounting/reports` with tabs 總覽, 趨勢與比較, 排行, 專案與預算, then the pinned saved reports and 「＋ 自訂報表」.
  - Saved report editor at `/accounting/reports/custom/new` and `/accounting/reports/custom/:id` (≥ 760 px); on phones a saved report opens read-only with an 編輯條件 sheet.
  - Phone 財務 sub-nav becomes 記錄 / 帳戶 / 報表 / 記帳設定.
  - Spend comparisons get their own colour tokens (`--app-spend-more`, `--app-spend-less`).

## Capabilities

### New Capabilities

- `accounting-reports`: report endpoints, inclusion rules, budgets, saved reports, project exclusion.
- `frontend-accounting-reports`: the 報表 page, its tabs, the saved report editor and viewer.

### Modified Capabilities

- `frontend-app-shell`: the 財務 sub-nav gains 報表.

## Non-goals

- Portfolio data in reports (net worth, dividend income, realized PnL).
- Historical FX: conversion stays at the latest cached rate, as the month summary does today.
- Exports (CSV / PDF) and scheduled report delivery; Discord digests are unchanged.
- Budget rollover between months.

## Impact

- accounting-service: new router `app/routers/reports.py`, service `app/services/report_service.py`, two tables and one column (one Alembic revision), tests under `tests/unit/reports` and `tests/integration/reports`.
- SPA: `components/accounting/reports/*`, `services/reports.service.ts`, shell navigation, `styles.scss` (two tokens).
- Design reference: `design_handoff_v3/`.
