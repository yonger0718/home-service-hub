## 0. Spec

- [x] 0.1 Design canvas (ReportsDesktop, ReportsPhone, ReportBuilder, ReportSavedPhone) and `design_handoff_v3/`
- [x] 0.2 Proposal, design (D1–D8), spec deltas; `openspec validate add-accounting-reports --strict`

## 1. Report API (PR A)

- [x] 1.1 Extract `report_rules.py` from `_converted_totals` / `_category_expense`; month and daily summary unchanged (existing tests green)
- [x] 1.2 `GET /reports/breakdown`: dimensions, split, filters (incl. `weekday[]`, `project_id[]`), compare, null-key rows, `__other__`, tag overlap flag
- [x] 1.3 Row statistics (≤ 50 groups)
- [x] 1.4 `GET /reports/trend` with empty buckets and averages
- [x] 1.5 Tests: timeline vs report totals equal; refund line; merchant null row; split sums; compare windows; 422 on equal split; performance check on 20k entries

## 2. Reports page: overview and trend (PR B)

- [ ] 2.1 `ReportsService`, route `/accounting/reports`, tabs with `?tab=`, sidebar item, phone sub-nav (4 items)
- [ ] 2.2 Header: period control, rules line, four figures with comparisons
- [ ] 2.3 總覽: donut + list, 支出/收入, refund line, category detail, drill-through to the timeline
- [ ] 2.4 趨勢與比較: 12-month bars + average, category chips, comparison table
- [ ] 2.5 `--app-spend-more` / `--app-spend-less` tokens (light and dark, ≥ 4.5:1 on surfaces)

## 3. Ranking (PR C)

- [ ] 3.1 Dimension switch, ranked rows, null rows muted, tag overlap note
- [ ] 3.2 Statistics panel with six-month bars
- [ ] 3.3 Tests: dimension switch keeps the period; selection updates the panel

## 4. Budgets and project exclusion (PR D)

- [x] 4.1 Alembic: `budget`, `project.exclude_from_reports`
- [x] 4.2 Budget CRUD + `/reports/budgets/status`; exclusion honoured by report endpoints
- [ ] 4.3 專案與預算 tab: bars with time marker, over state, project cards, 編輯預算
- [ ] 4.4 記帳設定 › 專案: 不計入報表 switch
- [ ] 4.5 Tests: history kept on amount change; project budget used over its lifetime; excluded project left out unless flagged

## 5. Saved reports (PR E)

- [x] 5.1 Alembic: `saved_report`; CRUD, reorder, config validation, `stale_refs`
- [ ] 5.2 Editor (≥ 760 px): fields, filters, chart rules, compare, pin, empty state with templates, save / save as / delete
- [ ] 5.3 Pinned reports as tabs; phone chips, viewer and 編輯條件 sheet
- [ ] 5.4 Tests: preset resolution on open; disabled chart reasons; stale reference message

## 6. Verify

- [ ] 6.1 Screens at 1440 / 1100 / 390 in light and dark against `design_handoff_v3/`; before / after on each PR
