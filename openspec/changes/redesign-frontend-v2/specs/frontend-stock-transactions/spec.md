## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Period summary cards

Above the filters the screen SHALL render four cards for the current filter: 買進金額 (with count), 賣出金額 (with count), 手續費＋交易稅 (with its share of turnover), and 已實現損益 (trend-coloured, linking to `/portfolio/realized-pnl`). The page header SHALL show the title, the period and total count, and the actions 匯入 CSV (to `/portfolio/import`) and 新增交易 (soft-primary). At < 760px only 買進 and 賣出 SHALL be shown, side by side. The card figures SHALL be TWD all-in totals computed client-side over every row of the active filter, read 100 rows per request and capped at 50 requests, with the note `⋯ 僅載入前 5,000 筆` when the cap is reached; foreign rows without a finite positive `fx_rate_to_twd` SHALL be left out of the figures and counted in the note `不含外幣 n 筆`. 已實現損益 SHALL be the realized-P&L summary (`filter_scope_total`) for the same filters without `side`. The page has no market filter, so the scenario below is met through the symbol, side, broker and date filters, not a 台股 switch.

#### Scenario: Summary follows the filter
- **WHEN** the owner filters to 台股 only
- **THEN** the four cards SHALL recompute for 台股 trades only

#### Scenario: Realized card links
- **WHEN** the owner clicks the 已實現損益 card
- **THEN** the router navigates to `/portfolio/realized-pnl`
