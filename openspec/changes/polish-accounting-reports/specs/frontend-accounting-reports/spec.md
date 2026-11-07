## ADDED Requirements

### Requirement: Running period comparisons

A report period (`to` being its inclusive last day) SHALL be *running* while `from ≤ today < to`. For a running period:

- **The amounts, the charts and the comparisons** SHALL all use the range from the period's start through today. Changes SHALL be marked 「截至 M/D」.
- **The current column** SHALL read 「本月至今（M/D–M/D）」 (「本期至今」 for other periods), and the comparison columns 上月同期 (上期同期) and 去年同期.

#### Scenario: Header on the 7th
- **GIVEN** today is 2026-11-07, November's expense so far is 4,233, October 1–7 is 6,910 and November 1–7 2025 is 5,120
- **WHEN** 2026-11 is selected
- **THEN** 支出 SHALL read `$4,233` with 「比 10 月同期 ▼ 2,677 · 比去年同期 ▼ 887 · 截至 11/7」

#### Scenario: A month that has not started
- **WHEN** 2026-12 is selected on 2026-11-07
- **THEN** the period SHALL not be running, and its comparisons SHALL cover the whole of December without 「截至」

#### Scenario: A range ending today
- **WHEN** the custom range 2026-11-01 to 2026-11-07 is selected on 2026-11-07
- **THEN** the period SHALL not be running, its to-date range being the whole range, and its comparisons SHALL read without 「截至」

#### Scenario: A finished month
- **WHEN** 2026-10 is selected on 2026-11-07
- **THEN** the comparisons SHALL cover the whole of October against the whole of September and October 2025, without 「截至」

### Requirement: Rows after today in a running period

When a running period holds rows dated after today, a muted line under the table SHALL read 「另有 M/D 之後已記的 N 筆 $X 未計入」, counting those rows in the table's metric. Pressing it SHALL switch to the whole-period view, which hides every comparison column and change; pressing it again SHALL return to the to-date view. Without such rows the line SHALL be absent.

#### Scenario: Scheduled rows later in the month
- **GIVEN** today is 2026-11-07 and November holds 2 expense rows totalling 13,420 dated after 11/7
- **WHEN** 2026-11 is opened
- **THEN** the figures SHALL leave those rows out, a muted line SHALL read 「另有 11/7 之後已記的 2 筆 $13,420 未計入」, and pressing it SHALL show the whole month without comparison columns

#### Scenario: No rows after today
- **GIVEN** today is 2026-11-07 and November holds no rows after 11/7
- **WHEN** 2026-11 is opened
- **THEN** no 「另有」 line SHALL appear

### Requirement: Running bucket in multi-bucket reports

In a report over time buckets, only the last bucket SHALL be running. Its row SHALL read 「M 月 進行中（至 M/D）」 with its to-date amount, and its change SHALL compare with the same number of days of the bucket before it. The total row SHALL hold the to-date total and compare with the comparison window of the to-date range.

#### Scenario: November in 近 6 個月
- **GIVEN** today is 2026-11-07 and a saved report 近 6 個月 by month with 和上期比較
- **WHEN** it is opened
- **THEN** November's row SHALL read 「11 月 進行中（至 11/7）」 with November 1–7's amount and a change against October 1–7, June to October SHALL show whole months, and the total row SHALL compare 2026-06-01..2026-11-07 with 2025-12-01..2026-05-07

### Requirement: Comparisons without a base

A comparison window SHALL be *empty* when its totals count no rows. A change against an empty window SHALL read 「上期無資料」 or 「去年無資料」 in the muted text colour, without an arrow. A table comparison column whose window is empty SHALL be left out, with a muted note under the table naming the hidden column.

#### Scenario: Ledger younger than a year
- **GIVEN** the ledger starts in 2026-08
- **WHEN** 週末外食 (近 6 個月, 和去年同期比較) is opened
- **THEN** the table SHALL have no 去年同期 or 增減 column, a note SHALL read 「去年同期沒有記錄，已隱藏比較欄」, and no row SHALL read 新增

### Requirement: New and stopped keys in comparisons

Against a non-empty comparison window:

- a key absent from the comparison window SHALL read 新增 in the muted text colour;
- a key absent from the current window SHALL read 「— 停止」 in the muted text colour;
- neither case SHALL use the spend-more or spend-less colours.

#### Scenario: New category this month
- **GIVEN** September holds rows but none in 寵物, and October holds 寵物 1,200
- **WHEN** 2026-10 is compared with the previous month
- **THEN** 寵物's change SHALL read 新增 in the muted colour

### Requirement: Month trend average

The 趨勢與比較 chart's average line SHALL be the mean of the monthly values:

- from the first month with a non-zero value in the series;
- through the last completed month, leaving out a running month.

It SHALL be labelled 「N 個月平均」. With no completed month holding data, the chart SHALL show no average line.

#### Scenario: Ledger started in August
- **GIVEN** today is 2026-11-07, the series holds 0 from 2025-12 to 2026-07, then 31,200, 39,608 and 41,890 for August to October, and November so far
- **THEN** the average SHALL be 37,566 and its label SHALL read 「3 個月平均 37,566」

### Requirement: Category detail without subcategories

When the selected category in 總覽 has no subcategory amounts, the detail card SHALL list 常去商家 in place of subcategories:

- the category's top five merchants for the period, with amount and count;
- a 「（無商家）」 row for rows without a merchant.

If they cannot be read, the card SHALL show its figures and the drill-through link only.

#### Scenario: 居家 without subcategories
- **GIVEN** 居家 has no subcategories and October holds 房租 12,000 to 房東 and 電費 1,420 to 台電
- **WHEN** 居家 is selected
- **THEN** the card SHALL list 常去商家 with 房東 12,000 and 台電 1,420, and SHALL NOT list 居家 under itself

### Requirement: Single-entry statistics

When a ranking row counts one entry, the 排行 statistics panel SHALL show 只有 1 筆 with that entry's amount and, when the API returns it, its date, in place of 平均, 中位數, 最大, 最小 and 平均間隔. For more entries, 平均間隔 SHALL be hidden when the first and last dates are the same day.

#### Scenario: One visit
- **GIVEN** 饗食天堂 has one 1,250 entry on 2026-10-03
- **WHEN** it is selected in 排行
- **THEN** the panel SHALL read 「只有 1 筆 · 10/3 · $1,250」 and show no median, maximum, minimum or interval

### Requirement: Bucket labels

Wherever a time-dimension key is shown as text (figures, table rows, chart axes, accessible names), it SHALL be formatted as follows:

- **month:** 9 月;
- **week:** 10/5 週;
- **day:** 10/5.

The year SHALL be prefixed (2025 年 9 月, 2025/10/5) when the report spans more than one calendar year. A running bucket SHALL be suffixed 「進行中（至 M/D）」, and excluded from 平均每月 and 平均每組.

#### Scenario: Highest month in a saved report
- **GIVEN** 週末外食 by month, September 2026 being the highest at 367
- **THEN** the 最高 figure SHALL read 「9 月」 with `$367` beneath, not 「2026-09」

#### Scenario: Running bucket
- **GIVEN** a saved report 近 6 個月 by month opened on 2026-11-07
- **THEN** November's row SHALL read 「11 月 進行中（至 11/7）」 and 平均每月 SHALL average June to October

### Requirement: Reports header and button clearance

From 1024 px the reports page header SHALL be one row: the title with the statistics rules line on the left, and the period control on the right. Below 1024 px the period control SHALL stay under the title.

The reports page and the saved-report page SHALL keep ending with the shell's floating-button clearance, so the 記一筆 button covers no figure once the page is scrolled to its end.

#### Scenario: Desktop header
- **WHEN** `/accounting/reports` opens at 1440 px
- **THEN** the period stepper SHALL be on the title's row and no separate period row SHALL appear

#### Scenario: Last column visible
- **WHEN** a saved report's table is scrolled to its end at 1440 px
- **THEN** the 增減 column of the total row SHALL not be covered by the 記一筆 button
