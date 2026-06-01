# frontend-portfolio-dashboard Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements
### Requirement: Bento KPI grid

The Portfolio dashboard SHALL render a responsive bento grid (6-col → 4-col → 1-col at handoff breakpoints) whose top row holds four white KPI tiles: 總市值, 未實現損益, 年化報酬率 XIRR, 本年度股利. Tiles MUST stay white (no gradient fill, no coloured left border); colour appears only in numeric values.

#### Scenario: Tiles render with correct labels
- **WHEN** the dashboard loads
- **THEN** four KPI tiles render with labels 總市值 / 未實現損益 / 年化報酬率 / 本年度股利

#### Scenario: 未實現損益 value uses trend colour
- **WHEN** the unrealized PnL is positive
- **THEN** its `.value` is rendered in `var(--app-trend-positive)`

#### Scenario: 本年度股利 value uses violet
- **WHEN** the dividend KPI renders
- **THEN** its `.value.dividend` is rendered in `var(--app-dividend)`

### Requirement: Net-worth chart with range selector

The dashboard SHALL render a Chart.js line chart of net worth inside a `.b-full` bento tile. The header MUST contain the title, a `.pct-badge` showing the period return, and a `.seg-toggle.range-tabs` with options **1M / 3M / YTD / 1Y / 5Y**. Default selected range is **1Y**.

#### Scenario: Default range is 1Y
- **WHEN** the dashboard first renders
- **THEN** the `1Y` segment is active in the range selector

#### Scenario: Selecting a range updates chart, badge, and XIRR
- **WHEN** the user clicks `3M` in the range selector
- **THEN** the chart line dataset switches to the 3M series
- **AND** the `.pct-badge` recomputes to the 3M period return
- **AND** the XIRR tile updates to the annualised rate for the 3M window

### Requirement: Chart colour respects theme and convention

The line and area-fill colour of the net-worth chart SHALL be `--app-trend-positive` when the selected period ended with a gain, and `--app-trend-negative` when it ended with a loss. Colours MUST be read via `getComputedStyle` at draw time and the chart MUST refresh on `dark$` or `gainLoss$` changes.

#### Scenario: Convention flip repaints chart
- **WHEN** the chart is showing a gaining period in asian convention (red line)
- **AND** the user flips convention to western
- **THEN** the chart line repaints green within the same session

### Requirement: Chart animation disabled

The dashboard SHALL set `options.animation = false` on the net-worth Chart.js instance.

#### Scenario: Chart options exclude animation
- **WHEN** the chart is instantiated
- **THEN** its `options.animation` evaluates to `false`

### Requirement: Expandable holdings rows

The dashboard SHALL render a holdings list whose rows expand on click to reveal cost basis, dividend total, and XIRR detail per stock.

#### Scenario: Click expands a holding
- **WHEN** the user clicks a holdings row
- **THEN** that row expands inline to show cost / dividend / XIRR detail
- **AND** other rows collapse if previously expanded
