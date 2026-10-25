# frontend-portfolio-dashboard Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.

## Requirements

### Requirement: Bento KPI grid

Below the 總資產 hero the Portfolio dashboard SHALL render a responsive row of four KPI cards: at ≥ 760 px auto-fit with a 220 px minimum; below 760 px two columns (`repeat(2, minmax(0, 1fr))`, card padding `--space-4`, gap 12 px) so the four cards occupy two rows of two. The cards are 總市值 (footnote 總成本), 預估含息損益 (value and `PctBadge` in trend colour; footnote 價差 · 股利 · 待收 with the three components), 年化報酬率 XIRR (footnote: range · market scope), and 已記錄股利 (value in `var(--app-dividend)`; footnote 含舊資料 · 不代表已核實收款). Values follow the "Values fit their container" requirement (`frontend-ui-primitives`). Cards MUST use the surface colour with `--app-card-shadow` (no gradient fill, no coloured border); colour appears only in values. When a market filter is active, labels carry ` · <market>` as today.

#### Scenario: Tiles render with correct labels
- **WHEN** the dashboard loads
- **THEN** four KPI cards render with labels 總市值 / 預估含息損益 / 年化報酬率 XIRR / 已記錄股利

#### Scenario: Phone grid
- **WHEN** the dashboard renders at 390 px
- **THEN** the four KPI cards SHALL occupy two rows of two

#### Scenario: 未實現損益 value uses trend colour
- **WHEN** the estimated PnL is positive
- **THEN** its value and badge are rendered in `var(--app-trend-positive)`

#### Scenario: 本年度股利 value uses violet
- **WHEN** the dividend KPI renders
- **THEN** its value uses `var(--app-dividend)` and its footnote reads 含舊資料 · 不代表已核實收款

### Requirement: Net-worth chart with range selector

The dashboard SHALL render a Chart.js line chart titled 淨值走勢 with two series: **帳戶總值** (total assets = holdings market value + broker cash) and **持股淨值** (holdings market value). The area between the two lines SHALL be shaded and the area under 持股淨值 lightly filled. Above the chart a legend SHALL show each series' swatch, name and latest value, plus a third entry 兩線差距 = 交割戶現金 with the latest cash value. Hovering (or focusing) the chart SHALL show a dashed vertical guide, a marker on both lines and a tooltip with the date, 帳戶總值, 持股淨值 and 現金; the tooltip MUST stay inside the card. The header MUST contain the title, a `PctBadge` showing the period change of 帳戶總值 labelled 帳戶總值, and a `SegToggle` with options **1M / 3M / YTD / 1Y / 5Y** (default **1Y**). At < 760px the legend SHALL be a two-column grid above the chart and the hover tooltip MAY be omitted. While a market filter is active, the chart title and the XIRR card footnote SHALL name 全市場 (portfolio-wide), since both remain portfolio-wide. Keyboard focus on the chart SHALL show the latest point, and ← / → SHALL move the focused point.

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

### Requirement: 總資產 hero

The dashboard SHALL open with a hero card: label 總資產 · TWD, the total in `--fs-value` or larger, a 今日 change pill in trend colour, and a composition bar 股票市值 / 交割戶現金 with a legend that also lists 待收股利 (dashed swatch, not part of the bar). The page header SHALL show the title, the quote time line, the market `SegToggle`, 匯入券商 and 刷新行情; below 760 px the header SHALL keep one row — 匯入券商 and 刷新行情 move into a ⋯ menu button and the market `SegToggle` renders under the header at full width. When a market filter is active the hero SHALL be hidden as today. The quote time line SHALL read `行情更新於 HH:mm · 盤中|台股收盤` when the last quote refresh (`quote_refresh.finished_at` from `GET /api/portfolio/imports/recalc/status`) finished today in Asia/Taipei, and `行情更新於 MM/DD HH:mm · 盤中|台股收盤` when it finished on an earlier day. The session tag is computed client-side as 盤中 for Mon–Fri 09:00 ≤ t < 13:30 Asia/Taipei (13:30 itself is 台股收盤) and 台股收盤 otherwise. The market `SegToggle` SHALL be hidden when only one market is present.

#### Scenario: Header contents
- **GIVEN** the owner holds Taiwan and US stocks and the viewport is at least 760 px
- **WHEN** the page renders during market hours
- **THEN** the header SHALL show 投資概覽, 行情更新於 HH:mm · 盤中, a `SegToggle` 全部 / 台股 / 美股, 匯入券商 and 刷新行情

#### Scenario: Phone header
- **GIVEN** the owner holds Taiwan and US stocks
- **WHEN** `/portfolio` renders at 390 px
- **THEN** the header SHALL be one row with 投資概覽, a ⋯ menu holding 匯入券商 and 刷新行情, and the gear (only when the tab bar omits 設定, per the live Hideable groups and tab bar parity rule), and the 全部 / 台股 / 美股 toggle SHALL render under the header

#### Scenario: Single market hides the toggle
- **GIVEN** only Taiwan stocks are held
- **WHEN** the page renders
- **THEN** no market `SegToggle` SHALL be shown

#### Scenario: Pending dividends are not added to cash
- **GIVEN** cash 438,360 and pending dividends 14,600
- **WHEN** the hero renders
- **THEN** the composition bar SHALL use only 股票市值 and 438,360, and 14,600 SHALL appear only in the 待收股利 legend entry

### Requirement: Allocation and pending dividends card

Beside the chart (stacked below it at < 1024px) the dashboard SHALL render a card 持股配置 with a horizontal allocation bar of holdings by market value and a list of name, symbol and percentage, followed by 待收股利: up to three pending dividends with a date tile (month, day of payment), name, per-share × quantity note and net amount in `var(--app-dividend)`, and a 全部 link to `/portfolio/dividends`. The 待收股利 list SHALL show dividends with `receipt_status = pending` (reading every page of `GET /api/portfolio/dividends`), dated rows first ordered by payment date, with amounts as returned (net) and non-TWD amounts shown with their currency; the hero's 待收股利 figure remains the server's `total_pending_dividends_net` and MAY differ from this list. Allocation colours SHALL be a five-token series starting with `--app-primary`, and holdings beyond the fifth SHALL be grouped as 其他.

#### Scenario: Allocation sums to 100%
- **WHEN** the card renders five holdings
- **THEN** the five percentages SHALL sum to 100% (± 0.1 rounding)

#### Scenario: Link to dividends
- **WHEN** the owner clicks 全部 in 待收股利
- **THEN** the router navigates to `/portfolio/dividends`

### Requirement: Chart carries holdings across cash-only snapshot rows

A point is **cash-only** when its five stock columns (`total_market_value`, `total_cost`, `total_unrealized_pnl`, `total_dividends`, `total_realized_pnl`) are 0 and `portfolio_xirr` is null. For every run of cash-only points after a point with holdings, the chart SHALL carry that point's five values onto the run (dates kept) and plot 帳戶總值 as carried market value + each point's own `total_cash_twd`, ignoring `total_assets_twd`. Other points plot as written; backend rows unchanged.

#### Scenario: Leading cash-only rows
- **GIVEN** cash-only snapshots before the first point with holdings
- **WHEN** the chart renders
- **THEN** those points SHALL plot as written

#### Scenario: Dividend received on a Saturday
- **GIVEN** a cash-only snapshot on Saturday 2026-01-10 between two trading days with market value about 2.9 M
- **WHEN** the 1Y range renders
- **THEN** 持股淨值 on 2026-01-10 SHALL equal Friday's value and 帳戶總值 SHALL equal that value plus Saturday's `total_cash_twd`

#### Scenario: Two cash-only days in a row
- **GIVEN** cash-only snapshots on Saturday and Sunday after a Friday with holdings
- **WHEN** the chart renders
- **THEN** both days SHALL carry Friday's holdings values

#### Scenario: Liquidation is not bridged
- **GIVEN** a snapshot with market value 0, cost 0 and `total_realized_pnl` 35,205 after a day with holdings
- **WHEN** the chart renders
- **THEN** 持股淨值 SHALL plot 0 on that date

### Requirement: Phone summary density

Below 760 px each collapsed holdings row SHALL use two lines: name and market value, then `quantity 股 · price · day change` and the PnL with its percentage. (The 2 × 2 KPI grid is part of the modified Bento KPI grid requirement.)

#### Scenario: Phone dashboard
- **WHEN** `/portfolio` renders at 390 px
- **THEN** each collapsed holdings row SHALL be at most two text lines tall
