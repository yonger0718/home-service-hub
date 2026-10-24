## ADDED Requirements

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

## MODIFIED Requirements

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
