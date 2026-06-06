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

### Requirement: Holdings table groups rows by market

The dashboard holdings list SHALL group rows by `market` using a PrimeNG row-group (or equivalent grouping primitive). Groups SHALL render in the order `TW`, `US`, `LSE`. The `TW` group SHALL be expanded by default. Groups with zero rows SHALL NOT be rendered. Sort and filter behaviour SHALL continue to operate over the flat row set; grouping is presentational only.

#### Scenario: TW-only portfolio renders a single group, expanded
- **GIVEN** every holding has `market === 'TW'`
- **WHEN** the dashboard renders
- **THEN** a single `TW` group SHALL be rendered, expanded, and no other group headers SHALL appear

#### Scenario: Mixed portfolio renders three groups in fixed order
- **GIVEN** holdings include `market` values `TW`, `US`, and `LSE`
- **WHEN** the dashboard renders
- **THEN** group headers SHALL appear in the order `TW`, `US`, `LSE`

#### Scenario: Sort across groups uses the flat row set
- **WHEN** the user sorts by `unrealized_pnl` descending
- **THEN** rows SHALL be re-ordered by `unrealized_pnl` within their respective group and group order SHALL remain fixed

### Requirement: Holdings table renders Market, Native Price, and FX-tooltip columns

The dashboard holdings table SHALL include a `Market` badge column and a `Native Price` column (per the `frontend-foreign-markets-display` formatting rules). The TWD `market_value` cell SHALL surface the live FX rate as an info-icon tooltip for foreign rows per the same capability. Existing TWD columns SHALL remain unchanged.

#### Scenario: Market badge column renders the market code
- **WHEN** a holding row renders
- **THEN** the `Market` cell SHALL display the holding's `market` value as a badge

#### Scenario: Existing TWD columns are unchanged for TW rows
- **WHEN** a `market === 'TW'` row renders
- **THEN** the existing `總市值`, `未實現損益`, `當日損益`, `XIRR`, and dividend cells SHALL display the same values they did before this change

### Requirement: PortfolioService caches and looks up holdings by composite key

`PortfolioService` SHALL key its in-memory holdings cache (and any selection / lookup state derived from `getSummary()`) by `holdingKey(holding)` rather than by `symbol`. Any public method that accepts a holding identifier SHALL accept either the composite key string or a `{ symbol, market }` object and SHALL NOT accept a bare `symbol`.

#### Scenario: Cache disambiguates same-symbol across markets
- **GIVEN** the latest summary response contains both `{ symbol: 'AAPL', market: 'TW' }` and `{ symbol: 'AAPL', market: 'US' }`
- **WHEN** the service stores them in its cache
- **THEN** both entries SHALL be retrievable as distinct holdings

#### Scenario: Lookup by bare symbol is rejected
- **WHEN** a caller invokes a `PortfolioService` lookup with only a bare `symbol` string
- **THEN** the call SHALL throw at compile time (TypeScript) or return `undefined` if the surface is `any`-typed at the boundary

### Requirement: StockHolding model includes foreign-market fields

The frontend `StockHolding` interface in `frontend/src/app/models/portfolio.model.ts` SHALL declare `market: 'TW' | 'US' | 'LSE'`, `native_close: number | string | null`, `native_currency: string | null`, and `live_fx_rate_to_twd: number | string | null` fields, matching the Phase 2 backend response shape.

#### Scenario: Type allows market enum
- **WHEN** code assigns `holding.market = 'US'`
- **THEN** the TypeScript compiler SHALL accept the assignment

#### Scenario: Null live FX rate is allowed
- **WHEN** the backend returns `live_fx_rate_to_twd: null` for a foreign holding
- **THEN** the frontend SHALL parse the row without error and downstream renderers SHALL fall back to the dash placeholder

### Requirement: Dashboard renders per-broker cash tile from broker_cash_flows

The portfolio dashboard SHALL render a per-broker cash tile that reads from `GET /api/portfolio/broker-cash-flows`. The tile SHALL show one row per broker returned by the API, each with `broker`, `currency`, and `balance`. The existing aggregate cash tile SHALL remain for the ALL-broker total in TWD.

#### Scenario: Tile renders one row per active broker
- **GIVEN** `GET /api/portfolio/broker-cash-flows` returns three rows for IB, FIRSTRADE, SCHWAB
- **WHEN** the dashboard mounts
- **THEN** the per-broker cash tile SHALL display three rows with the matching broker labels and balances

#### Scenario: Empty broker cash flows hides the tile
- **GIVEN** the endpoint returns an empty array
- **WHEN** the dashboard mounts
- **THEN** the per-broker cash tile SHALL NOT render

#### Scenario: Aggregate cash tile still shows ALL-broker TWD total
- **GIVEN** non-empty broker cash flows
- **WHEN** the dashboard mounts
- **THEN** the existing aggregate cash tile SHALL continue to render in TWD with no behaviour change

