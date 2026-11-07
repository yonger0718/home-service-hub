## 0. Spec

- [x] 0.1 Proposal, design (D1–D8), spec deltas
- [x] 0.2 Archive `add-accounting-reports` first (#324), then run `openspec validate polish-accounting-reports --strict`

## 1. API (PR A)

- [x] 1.1 `compare_window`: ranges starting on the 1st that end mid-month move back by the number of months they touch, with the end day clipped (D2)
- [x] 1.2 Tests: the three new scenarios plus a leap-year clip (2028-03-01..2028-03-30 → 2028-02-01..2028-02-29); the whole-month and other-range scenarios unchanged

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
  - running bucket labels 「11 月 進行中（至 11/7）」 and 「11/2 週 進行中（至 11/7）」; a month or week ending today is not running
  - running bucket comparison clipped: 2028-03-01..03-30 against 2028-02-01..02-29

## 3. Screens (PR B)

- [ ] 3.1 Header: to-date amounts and comparisons with 「截至 M/D」; one-row layout at ≥ 1024 px
- [ ] 3.2 總覽 detail: running-period change; 常去商家 for categories without subcategories, with a per period and category cache (D5)
- [ ] 3.3 趨勢與比較
  - N 個月平均
  - running month hatched
  - table columns 本月至今（M/D–M/D） / 上月同期 / 去年同期
  - hidden empty columns with the note
- [ ] 3.4 排行: single-entry panel; interval hidden for same-day ranges (D6)
- [ ] 3.5 自訂報表
  - 最高 label
  - running bucket 「進行中（至 M/D）」 with its change against the previous bucket's same days; to-date total row; excluded from averages
  - empty compare column hidden
  - muted 新增
- [ ] 3.6 Requests while running (D1 table): the header's three breakdowns over the to-date range plus two whole-period ones; a saved report to-date plus whole period, and the previous bucket's same days for a time dimension
- [ ] 3.7 Rows after today: the muted 「另有 M/D 之後已記的 N 筆 $X 未計入」 line (「…N 筆未計入」 for the count metric) under the 總覽 list, the 趨勢與比較 table and the saved-report table; pressing it switches to the whole-period view without comparisons and back (「改回截至 M/D」); absent without such rows
- [ ] 3.8 Screens at 1440 / 1100 / 390, light and dark, on the seeded ledger (11/7 and October)
