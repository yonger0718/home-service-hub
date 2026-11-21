## ADDED Requirements

### Requirement: Reports page and navigation

The SPA SHALL provide `/accounting/reports` with tabs 總覽, 趨勢與比較, 排行 and 專案與預算 (`?tab=overview|trend|rank|plan`), followed by the pinned saved reports and 「＋ 自訂報表」. The 財務 group SHALL list 報表 between 帳戶 and 記帳設定. The header SHALL show the period control (月 / 年 / 自訂 with previous and next), a line stating the statistics rules and the excluded projects, and four figures: 支出 with its change against the previous period and the same period last year, 收入, 結餘 with the savings rate, and 日均支出.

#### Scenario: October header
- **WHEN** 2026-10 is selected with expense 41,890, September 39,608 and October 2025 44,050
- **THEN** 支出 SHALL read `$41,890` with 「比 9 月 ▲ 2,282 · 比去年 10 月 ▼ 2,160」

### Requirement: Overview tab

總覽 SHALL show a donut and a category list (amount, count, share) for 支出 or 收入, a 退款 line when refunds exist, and for the selected category a detail card with its subcategories, change against the previous period, average per entry, share, and a link 在記帳記錄看這 N 筆 that opens the timeline with the same range and filters.

#### Scenario: Drill into 餐飲
- **WHEN** the owner selects 餐飲
- **THEN** the detail card SHALL list 晚餐, 午餐, 咖啡, 早餐 and 宵夜 with amounts and counts

### Requirement: Trend and comparison tab

趨勢與比較 SHALL show twelve monthly bars ending at the selected month with a dashed average line, switchable between all expense and single categories, and a table per category with this period, the previous period and the same period last year, each change as ▲ / ▼ amount and percentage, and a total row.

#### Scenario: Unchanged category
- **WHEN** 訂閱 is 1,078 in both September and October
- **THEN** its month change SHALL read —

### Requirement: Ranking tab

排行 SHALL rank by 商家, 標籤, 對象 or 帳戶 with a bar, count and average per row; rows without the dimension SHALL be labelled （無商家） and so on and drawn muted; a note SHALL explain overlapping tag totals. Selecting a row SHALL show its total, count, average, median, maximum, minimum, average interval and six-month bars.

#### Scenario: Merchant statistics
- **WHEN** the owner selects 路易莎
- **THEN** the panel SHALL show 12 筆, 平均 86, 中位數 85, 最大 120, 最小 65, 平均間隔 2.5 天

### Requirement: Projects and budgets tab

專案與預算 SHALL show each budget for the month as a bar with a marker at the month's time progress, the used and limit amounts, and 剩 N or 超出 N (in `--c-red-ink`); and each project as a card with its total, dates, budget use and category split, marking projects excluded from reports. 編輯預算 SHALL edit amounts from the selected month on.

#### Scenario: Over budget bar
- **WHEN** 購物 used 3,490 of 3,000
- **THEN** its bar SHALL be full, red, and read 超出 490（116.3%）

### Requirement: Spend comparison colours

Comparisons of spending SHALL use ▲ / ▼ with `--app-spend-more` and `--app-spend-less` (new tokens in light and dark) and SHALL NOT use the gain/loss tokens, so the 紅漲綠跌 setting never changes them.

#### Scenario: Convention switched
- **GIVEN** the owner uses 綠漲紅跌
- **WHEN** a category spent more than last month
- **THEN** its change SHALL still render in `--app-spend-more`

### Requirement: Saved report editor

At ≥ 760 px `/accounting/reports/custom/new` and `/accounting/reports/custom/:id` SHALL offer a name, a period (presets or custom range), 看什麼, 依什麼分組, 再細分, filters (all must match), chart type, 比較, and 釘選到報表分頁, with a live preview of figures, chart and table, and 儲存 / 另存新報表 / 刪除. A new report SHALL start from an empty state with templates. Chart types that do not fit the grouping SHALL be disabled with a reason.

#### Scenario: Donut disabled with a split
- **WHEN** the report splits by subcategory
- **THEN** 圓環 SHALL be disabled with the reason 有細分時不能用圓環

#### Scenario: Relative period rolls forward
- **GIVEN** 週末外食 is saved with 近 6 個月 on 2026-10-31
- **WHEN** it is opened on 2026-12-02
- **THEN** it SHALL cover July to December 2026

### Requirement: Saved reports on phones

Below 760 px the reports page SHALL list saved reports as chips under the tabs; opening one SHALL show its figures, chart and table, with 編輯條件 opening a bottom sheet of its conditions and 套用. Creating a report SHALL be desktop only.

#### Scenario: Edit conditions on a phone
- **WHEN** the owner taps 編輯條件 on 週末外食
- **THEN** a sheet SHALL list 期間, 看什麼, 依什麼分組, 再細分, 篩選 and 比較
