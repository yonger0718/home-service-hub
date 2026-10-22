## MODIFIED Requirements

### Requirement: Bento KPI grid

Below the 總資產 hero the Portfolio dashboard SHALL render a responsive row of four KPI cards (auto-fit, minimum 220px): 總市值 (footnote 總成本), 預估含息損益 (value and `PctBadge` in trend colour; footnote 價差 · 股利 · 待收 with the three components), 年化報酬率 XIRR (footnote: range · market scope), and 已記錄股利 (value in `var(--app-dividend)`; footnote 含舊資料 · 不代表已核實收款). Cards MUST use the surface colour with `--app-card-shadow` (no gradient fill, no coloured border); colour appears only in values. When a market filter is active, labels carry ` · <market>` as today.

#### Scenario: Tiles render with correct labels
- **WHEN** the dashboard loads
- **THEN** four KPI cards render with labels 總市值 / 預估含息損益 / 年化報酬率 XIRR / 已記錄股利

#### Scenario: 未實現損益 value uses trend colour
- **WHEN** the estimated PnL is positive
- **THEN** its value and badge are rendered in `var(--app-trend-positive)`

#### Scenario: 本年度股利 value uses violet
- **WHEN** the dividend KPI renders
- **THEN** its value uses `var(--app-dividend)` and its footnote reads 含舊資料 · 不代表已核實收款

### Requirement: Net-worth chart with range selector

The dashboard SHALL render a Chart.js line chart titled 淨值走勢 with two series: **帳戶總值** (total assets = holdings market value + broker cash) and **持股淨值** (holdings market value). The area between the two lines SHALL be shaded and the area under 持股淨值 lightly filled. Above the chart a legend SHALL show each series' swatch, name and latest value, plus a third entry 兩線差距 = 交割戶現金 with the latest cash value. Hovering (or focusing) the chart SHALL show a dashed vertical guide, a marker on both lines and a tooltip with the date, 帳戶總值, 持股淨值 and 現金; the tooltip MUST stay inside the card. The header MUST contain the title, a `PctBadge` showing the period change of 帳戶總值 labelled 帳戶總值, and a `SegToggle` with options **1M / 3M / YTD / 1Y / 5Y** (default **1Y**). At < 760px the legend SHALL be a two-column grid above the chart and the hover tooltip MAY be omitted.

#### Scenario: Default range is 1Y
- **WHEN** the dashboard first renders
- **THEN** the `1Y` segment is active in the range selector

#### Scenario: Selecting a range updates chart, badge, and XIRR
- **WHEN** the user clicks `3M` in the range selector
- **THEN** both series switch to the 3M window
- **AND** the badge recomputes to the 3M change of 帳戶總值
- **AND** the XIRR card updates to the annualised rate for the 3M window

#### Scenario: Tooltip shows both values and cash
- **WHEN** the pointer rests over a day where 帳戶總值 is 3,284,560 and 持股淨值 is 2,846,200
- **THEN** the tooltip SHALL list both values and 現金 438,360

### Requirement: Chart colour respects theme and convention

The 持股淨值 line and its fill SHALL use `--app-trend-positive` when the selected period ended with a gain and `--app-trend-negative` when it ended with a loss. The 帳戶總值 line SHALL use `--app-accent` and the band between the lines `--app-accent-soft`. Colours MUST be read via `getComputedStyle` at draw time and the chart MUST refresh on `dark$` or `gainLoss$` changes.

#### Scenario: Convention flip repaints chart
- **WHEN** the chart is showing a gaining period in asian convention (red holdings line)
- **AND** the user flips convention to western
- **THEN** the holdings line repaints green within the same session and the 帳戶總值 line keeps the accent colour

## ADDED Requirements

### Requirement: 總資產 hero

The dashboard SHALL open with a hero card: label 總資產 · TWD, the total in `--fs-value` or larger, a 今日 change pill in trend colour, and a composition bar 股票市值 / 交割戶現金 with a legend that also lists 待收股利 (dashed swatch, not part of the bar). The page header SHALL show the title, the quote time line, the market `SegToggle`, 匯入券商 and 刷新行情. When a market filter is active the hero SHALL be hidden as today.

#### Scenario: Header contents
- **GIVEN** the owner holds Taiwan and US stocks
- **WHEN** the page renders during market hours
- **THEN** the header SHALL show 投資概覽, 行情更新於 HH:mm · 盤中, a `SegToggle` 全部 / 台股 / 美股, 匯入券商 and 刷新行情

#### Scenario: Single market hides the toggle
- **GIVEN** only Taiwan stocks are held
- **WHEN** the page renders
- **THEN** no market `SegToggle` SHALL be shown

#### Scenario: Pending dividends are not added to cash
- **GIVEN** cash 438,360 and pending dividends 14,600
- **WHEN** the hero renders
- **THEN** the composition bar SHALL use only 股票市值 and 438,360, and 14,600 SHALL appear only in the 待收股利 legend entry

### Requirement: Allocation and pending dividends card

Beside the chart (stacked below it at < 1024px) the dashboard SHALL render a card 持股配置 with a horizontal allocation bar of holdings by market value and a list of name, symbol and percentage, followed by 待收股利: up to three pending dividends with a date tile (month, day of payment), name, per-share × quantity note and net amount in `var(--app-dividend)`, and a 全部 link to `/portfolio/dividends`.

#### Scenario: Allocation sums to 100%
- **WHEN** the card renders five holdings
- **THEN** the five percentages SHALL sum to 100% (± 0.1 rounding)

#### Scenario: Link to dividends
- **WHEN** the owner clicks 全部 in 待收股利
- **THEN** the router navigates to `/portfolio/dividends`
