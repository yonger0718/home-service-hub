## Context

The relevant parts as they stand:

- **`report-math.ts`**
  - `periodFromParams` resolves `?month=` to the whole month.
  - `compareRange` mirrors the API's `compare_window`.
  - `elapsedDays` already knows whether a period is running; it is used for 日均支出 only.
  - `change(current, base)` returns ▲ or ▼ and the difference, adding a percentage only when the base is non-zero.
- **`reports.ts`**
  - Header figures come from three breakdowns: expense with `compare: 'previous'`, expense with `'last_year'`, and income with `'previous'`.
- **`trend-tab.ts`**
  - The average line is the API's `average`: the sum over every bucket ÷ the bucket count.
- **`overview-tab.ts`**
  - The detail card lists `splits` (subcategories). When the category has no subcategories, the API returns one split whose label is the category's own name.
- **`rank-tab.ts`**
  - The panel pushes median, max, min and `avg_interval_days`, which the API returns as `0.0` for one entry.
- **`custom-report.ts`**
  - The 最高 figure uses `rowLabel`, which prints the raw key for time dimensions.
  - The table marks `base === 0 && total > 0` as 新增 in the spend-more direction.

Measured on seeded data on 2026-11-07:

- The header reads 「比 10 月 ▼ 37,649」 for 7 days against 31.
- 週末外食 with 和去年同期比較 reads 新增 on every row.
- The trend average is about a third of a real month because nine of the twelve buckets predate the ledger.

## Decisions

### D1. "Running" is decided by today, not by the period type

A period is *running* when `from ≤ today < to`. The rule covers every unit: the current month, the current year, a custom range ending in the future, and a saved report's relative period.

For a running period, the comparisons use the *to-date range* `from..today`.

- The figures themselves keep the whole period: the KPI amounts, the charts and the tables' current column. The future days of the month hold only scheduled rows, and those show as they do today.
- What changes is the comparison.

| Figure | Running period | Otherwise |
|---|---|---|
| Header 支出 change (two lines) | to-date range with `previous` and `last_year`, plus 「截至 11/7」 | unchanged |
| Header 結餘 rate, 日均支出 | unchanged (日均 already divides by elapsed days) | unchanged |
| 總覽 detail card 「比上期」 | the category's to-date amount against its to-date base | unchanged |
| 趨勢與比較 table | columns read 本月至今 / 上月同期 / 去年同期, with to-date amounts | unchanged |
| Saved report 比較 | to-date for the whole report, with the period label suffixed 「（截至 11/7）」 | unchanged |

This takes one extra breakdown in total: the to-date expense with `compare=previous`, made only while running. The `last_year` breakdown simply uses the to-date range. Income takes the to-date range as well.

### D2. API: "previous" for ranges that start on the 1st

`compare_window(date_from, date_to, 'previous')`:

- **Whole months** (from the 1st through a month's last day): unchanged.
- **Ranges that start on the 1st and end mid-month.** These now move back by the number of calendar months they touch. The end day is clipped to the target month's length.
  - `2026-11-01..2026-11-07` → `2026-10-01..2026-10-07`
  - `2026-03-01..2026-03-30` → `2026-02-01..2026-02-28`
  - `2026-06-01..2026-11-07` → `2025-12-01..2026-05-07`
- **Every other range:** unchanged (same day count, ending the day before `date_from`).

`report-math.ts` `compareRange` mirrors the change, keeping its existing parity test.

The alternative was a `compare_from` / `compare_to` pair on the API. It is more general, but it moves the window rule into every client. The SPA is the only client, and "same days of the previous month" is the rule MOZE users expect.

### D3. Empty comparisons

A comparison window is *empty* when its `totals.count` is 0. In that case:

- **Header and detail-card changes** read 「上期無資料」 or 「去年無資料」 in `--app-text-muted`, without an arrow.
- **Table columns** (趨勢與比較, saved reports): a comparison column whose window is empty is left out. A muted line under the table explains it: 「去年同期沒有記錄，已隱藏比較欄」.
- **A key absent only from a non-empty window** reads 新增 in `--app-text-muted`. This is new spending, not more spending. A key absent only from the current window reads 「— 停止」 muted.

`change()` gains a `kind: 'none' | 'new' | 'gone' | 'delta'` field, so the templates can style these cases without re-deriving them.

### D4. Averages

- **Trend, month granularity.** The average is computed in the SPA:
  - it covers the buckets from the first bucket with a non-zero value in the series through the last completed month;
  - the running month is excluded;
  - the label reads 「N 個月平均」;
  - when N is 0 (a ledger younger than one complete month), there is no line and no label.

  The API's `average` stays in the contract for other clients.
- **Saved reports, 平均每月 and 平均每組 over time dimensions.** These exclude the running bucket. The running bucket's bar is drawn hatched, and its table row label is suffixed 「進行中」.

### D5. Category detail without subcategories

When every split of the selected category has a null subcategory key, or the only split's label equals the category's label, the card replaces the subcategory list with 「常去商家」:

- the top five merchants by amount, from `GET /reports/breakdown?group_by=merchant&category_id=<id>` over the same period, each with its amount and count;
- one 「（無商家）」 row when rows lack a merchant.

The result is cached per period and category. If the read fails, the card shows only the figures and the drill-through link.

### D6. Single-entry statistics

When a ranking row's `count` is 1, the panel shows 只有 1 筆, the date (`first_date`) and the amount in place of 平均, 中位數, 最大, 最小 and 平均間隔. The six-month bars stay. For two or more entries, 平均間隔 hides when `first_date == last_date`, which means several entries on the same day.

### D7. Bucket labels

One helper, `bucketText(key, granularity, span)`, formats bucket keys wherever a key is shown as text: figures, table rows, chart axes and `aria-label`s.

| Granularity | Within one year | Across years |
|---|---|---|
| Month | 9 月 | 2025 年 9 月 |
| Week | 10/5 週 | 2025/10/5 週 |
| Day | 10/5 | 2025/10/5 |

`rowLabel` uses it for `month`, `week` and `day` keys.

### D8. Header and FAB

- **≥ 1024 px:** the header is one row. On the left, the title with the rules line under it. On the right, the period control (月 / 年 / 自訂 and the stepper).
- **< 1024 px:** the period control stays under the title, unchanged.
- **FAB clearance.** The reports page and the saved-report page add the shell's `--fab-clear` as bottom padding on their last card. This is the same clearance the accounting panes already take, so the floating 記一筆 never covers the right-most table column at rest.

## Risks

- **Changing `previous` for 1st-anchored partial ranges changes what an existing saved report shows.** It applies to a custom range like 11/1–11/15. The new window is the one the owner would expect, and saved reports store presets, not windows, so no data migration is needed.
- **The SPA's own average differs from the API's `average`.** A test pins both on the same series, so the difference is deliberate and documented.
