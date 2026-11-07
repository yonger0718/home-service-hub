## Why

報表 (add-accounting-reports) shipped. Seeded data checked on 2026-11-07 shows figures that are correct by the rules but mislead on screen:

- **The running month compares against a whole month.** On 11/7 the header reads 「比 10 月 ▼ 37,649」: seven days against thirty-one.
- **A comparison with nothing to compare against reads as growth.** The header and the 趨勢與比較 table show ▲ and the whole amount. The saved-report table marks every row 新增 in red, even when the ledger simply has no data a year back.
- **The 12-month average counts months before the ledger began.** It also counts the running month, so it sits well below any real month.
- **A category without subcategories repeats itself** in the detail card (居家 → 居家).
- **A one-entry merchant** shows the same median, maximum and minimum, and 平均間隔 0 天.
- **The saved-report 最高 figure** reads a raw bucket key (2026-09). Relative periods include the running month in 平均每月 without saying so.
- **Layout.** The period control takes a row of its own under the title.

**This PR contains no code.**

## What Changes

- **API (accounting-service):** `compare=previous` for a range that starts on the 1st but does not end on a month's last day now compares with the same days of the earlier month(s). For example, `2026-11-01..2026-11-07` compares with `2026-10-01..2026-10-07`. Whole-month ranges and other ranges are unchanged.
- **Running periods.** While the selected period contains today, the comparisons cover only up to today: the header figures, the 總覽 detail card's change, the 趨勢與比較 table and saved reports. Each such comparison reads 「截至 11/7」. Charts keep the whole period.
- **No base, no arrow.**
  - When the comparison window has no counted rows at all, the change reads 「上期無資料」 or 「去年無資料」, muted.
  - A table column that is empty throughout is left out, with a note.
  - A key that is absent only in the comparison window reads 新增, muted, not in the spend-more colour.
- **Averages.** The trend's average covers the months from the first one with data through the last completed month, and is labelled 「N 個月平均」. In saved reports, 平均每月 leaves out the running month, which is marked 進行中 in the chart and the table.
- **Detail without subcategories.** The 總覽 detail card lists the category's top five merchants instead.
- **One entry.** 排行's statistics panel shows 只有 1 筆 with its date and amount instead of median, min, max and interval.
- **Labels.** Month buckets read 9 月 (or 2025 年 9 月 across years) wherever a bucket key is shown as a label.
- **Layout.**
  - From 1024 px the period control moves into the title row.
  - The 記一筆 clearance stays as it is: the shell already ends these pages with it, so scrolled to the end the button covers nothing (checked on the screens).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `accounting-reports`: Comparison windows (ranges starting on the 1st).
- `frontend-accounting-reports`: running periods, empty comparisons, averages, category detail, single-entry statistics, bucket labels, header layout.

## Dependencies

The deltas modify requirements that `add-accounting-reports` introduces. Archive that change first: its code has shipped, and its open tasks are unticked boxes, not missing work.

## Non-goals

- No change to the inclusion rules, budgets, saved-report config or the 專案與預算 tab.
- Week and day granularity trends keep their averages over every bucket. Only the month trend changes, since week and day trends are not drawn in the SPA today.
- Fixed costs (rent) are not separated from discretionary spending. That is a separate idea from the calendar change.

## Impact

- **API:** `services/accounting-service/app/services/report_rules.py` (`compare_window`), plus tests.
- **SPA:** `components/accounting/reports/*`:
  - `report-math.ts`: a running-period range, change with no base, average over months with data, bucket labels;
  - `reports.ts` and `.html`: header;
  - `overview-tab`, `trend-tab`, `rank-tab`, `custom-report`;
- **Extra requests:**
  - while the period is running, two more header breakdowns (to-date expense and income; design D1);
  - a running saved report with a comparison, one more (to-date);
  - a merchant breakdown filtered to the category, only when a category without subcategories is selected in 總覽.
