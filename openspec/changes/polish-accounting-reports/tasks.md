## 0. Spec

- [x] 0.1 Proposal, design (D1–D8), spec deltas
- [ ] 0.2 Archive `add-accounting-reports` first, then run `openspec validate polish-accounting-reports --strict` (not run in the authoring session)

## 1. API (PR A)

- [ ] 1.1 `compare_window`: ranges starting on the 1st that end mid-month move back by the number of months they touch, with the end day clipped (D2)
- [ ] 1.2 Tests: the three new scenarios; the whole-month and other-range scenarios unchanged

## 2. Math and labels (PR B)

- [ ] 2.1 `report-math.ts`
  - `isRunning(period, today)` and `toDateRange(period, today)`
  - `compareRange` follows D2; the new cases in both `report-math.spec.ts` and the API's `test_report_rules.py`
- [ ] 2.2 `change()` returns `kind` (`none` / `new` / `gone` / `delta`); empty window detection from `totals.count`
- [ ] 2.3 `monthAverage(buckets, today)` over months with data, through the last completed month (D4)
- [ ] 2.4 `bucketText(key, granularity, spansYears)` replacing `bucketLabel`; use it in `rowLabel`, axes and `aria-label`s (D7)
- [ ] 2.5 Tests
  - 11/7 header case
  - empty window
  - 新增 and 停止
  - average 37,566 over 3 months
  - 9 月 and 2025 年 9 月

## 3. Screens (PR B)

- [ ] 3.1 Header: to-date comparisons with 「截至 M/D」; one-row layout at ≥ 1024 px
- [ ] 3.2 總覽 detail: running-period change; 常去商家 for categories without subcategories, with a per period and category cache (D5)
- [ ] 3.3 趨勢與比較
  - N 個月平均
  - running month hatched
  - table columns 本月至今 / 上月同期 / 去年同期
  - hidden empty columns with the note
- [ ] 3.4 排行: single-entry panel; interval hidden for same-day ranges (D6)
- [ ] 3.5 自訂報表
  - 最高 label
  - running bucket 進行中, excluded from averages
  - empty compare column hidden
  - muted 新增
- [ ] 3.6 Header and saved-report requests while running (D1 table): two extra header breakdowns; a saved report with a comparison makes a second, to-date request
- [ ] 3.7 Screens at 1440 / 1100 / 390, light and dark, on the seeded ledger (11/7 and October)
