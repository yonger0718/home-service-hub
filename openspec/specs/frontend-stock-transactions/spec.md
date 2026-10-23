# frontend-stock-transactions Specification

## Purpose
TBD - created by archiving change redesign-dashboard-handoff. Update Purpose after archive.
## Requirements
### Requirement: Date-grouped buy/sell timeline

The stock transactions screen SHALL render trades in descending order grouped by month; with a non-date sort (代碼) the months appear in first-seen order. Each month heading SHALL show the month (2026 年 10 月) and the month's 買 / 賣 totals. Each row MUST display: the trade day and weekday, a stock square, stock name + code, a `SideTag` (`buy` indigo 買 / `sell` slate 賣), a 當沖 tag when the trade is part of a day trade, `qty 股 @ price` with fees and tax beneath, the broker, and the amount (buys: total paid with a leading −; sells: net received with a leading +; foreign trades: TWD amount with the native amount beneath) and a row-actions button. At < 760px rows SHALL collapse to stock square, name + `SideTag`, `qty 股 @ price · broker`, and amount with the date beneath. The month 買 / 賣 totals SHALL use the same TWD all-in rule and row set as the summary cards (the list page's rows only while that set loads or after it failed). Native amounts SHALL show the currency as a suffix (`238.40 USD`). The stock square's fill SHALL be a stable hash of the symbol over the dashboard's five-colour series, so it MAY differ from the allocation-rank colour the dashboard gives the same stock.

#### Scenario: Buy row uses buy styling
- **WHEN** a buy trade renders
- **THEN** the row shows an indigo `買` `SideTag` and its amount as total paid with a leading −

#### Scenario: Trades on same date share a heading
- **WHEN** two trades in October are rendered
- **THEN** they appear under a single 2026 年 10 月 heading with the month's 買 / 賣 totals

#### Scenario: Day trade is tagged
- **WHEN** a buy and a sell of the same symbol on the same day are rendered
- **THEN** both rows show a 當沖 tag

### Requirement: Transaction form accepts market plus conditional FX inputs

The transaction create/edit form SHALL include a `market` dropdown (options `TW`, `US`, `LSE`; default `TW`). Selecting a non-TW market SHALL reveal a `currency` input pre-filled from the market choice (`US`→`USD`, `LSE`→`GBP`, user-editable so `GBp` can be entered) and a required `fx_rate_to_twd` Decimal input. TW selections SHALL render the form identically to the pre-Phase-3 layout (no FX inputs, no extra friction).

#### Scenario: Submitting a TW trade sends no fx fields
- **WHEN** the user submits the form with `market === 'TW'`
- **THEN** the request body SHALL NOT include `fx_rate_to_twd` and the `currency` field, if sent, SHALL be `TWD`

#### Scenario: Submitting a US trade includes USD currency and fx_rate_to_twd
- **WHEN** the user selects `US`, leaves `currency` as `USD`, enters a valid `fx_rate_to_twd`, and submits
- **THEN** the request body SHALL include `market: 'US'`, `currency: 'USD'`, and the entered `fx_rate_to_twd`

#### Scenario: Submitting an LSE GBp trade preserves the override currency
- **WHEN** the user selects `LSE`, changes `currency` to `GBp`, enters a valid `fx_rate_to_twd`, and submits
- **THEN** the request body SHALL include `market: 'LSE'`, `currency: 'GBp'`, and the entered `fx_rate_to_twd`

#### Scenario: Non-positive fx_rate_to_twd blocks submission
- **WHEN** the user selects `US` and enters `fx_rate_to_twd <= 0`
- **THEN** client-side validation SHALL block submission and surface an inline error

### Requirement: Transaction list renders market column for non-TW rows

The transaction list (timeline view per existing `frontend-stock-transactions` requirements) SHALL render a `market` badge next to each non-TW row. TW rows SHALL render with no market badge so existing visuals stay unchanged.

#### Scenario: US trade renders market badge
- **GIVEN** a transaction with `market === 'US'`
- **WHEN** the row renders
- **THEN** a `US` badge SHALL appear in the row meta area

#### Scenario: TW trade has no market badge
- **GIVEN** a transaction with `market === 'TW'`
- **WHEN** the row renders
- **THEN** no market badge SHALL appear and the row layout SHALL match the pre-Phase-3 layout pixel-for-pixel

### Requirement: Transaction list renders broker badge for non-default brokers

The transaction list SHALL render a broker badge next to the existing market badge for every row whose `broker` is non-null and not `TW_MANUAL`. TW_MANUAL and null rows SHALL render without a broker badge so the existing TW workflow is visually unchanged.

#### Scenario: IB row shows the IB badge
- **GIVEN** a transaction with `broker='IB'`
- **WHEN** the transaction list renders
- **THEN** the row SHALL display a badge reading `IB` next to the existing market badge

#### Scenario: TW_MANUAL row hides the broker badge
- **GIVEN** a transaction with `broker='TW_MANUAL'`
- **WHEN** the transaction list renders
- **THEN** the row SHALL NOT display a broker badge

#### Scenario: Null broker hides the broker badge
- **GIVEN** a transaction with `broker=null` (pre-backfill row)
- **WHEN** the transaction list renders
- **THEN** the row SHALL NOT display a broker badge

### Requirement: Period summary cards

Above the filters the screen SHALL render four cards for the current filter: 買進金額 (with count), 賣出金額 (with count), 手續費＋交易稅 (with its share of turnover), and 已實現損益 (trend-coloured, linking to `/portfolio/realized-pnl`). The page header SHALL show the title, the period and total count, and the actions 匯入 CSV (to `/portfolio/import`) and 新增交易 (soft-primary). At < 760px only 買進 and 賣出 SHALL be shown, side by side. The card figures SHALL be TWD all-in totals computed client-side over every row of the active filter, read 100 rows per request and capped at 50 requests, with the note `⋯ 僅載入前 5,000 筆` when the cap is reached; foreign rows without a finite positive `fx_rate_to_twd` SHALL be left out of the figures and counted in the note `不含外幣 n 筆`. 已實現損益 SHALL be the realized-P&L summary (`filter_scope_total`) for the same filters without `side`. The page has no market filter, so the scenario below is met through the symbol, side, broker and date filters, not a 台股 switch.

#### Scenario: Summary follows the filter
- **WHEN** the owner filters to 台股 only
- **THEN** the four cards SHALL recompute for 台股 trades only

#### Scenario: Realized card links
- **WHEN** the owner clicks the 已實現損益 card
- **THEN** the router navigates to `/portfolio/realized-pnl`

