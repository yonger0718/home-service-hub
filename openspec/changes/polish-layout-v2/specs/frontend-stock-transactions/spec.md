## ADDED Requirements

### Requirement: Transactions use the shared filter bar

The stock transactions screen SHALL use `<app-filter-bar>` with search and the 全部 / 買進 / 賣出 toggle inline; date range, broker and sort SHALL sit behind 篩選.

#### Scenario: One filter row
- **WHEN** `/portfolio/transactions` renders at 1440 px with no filters active
- **THEN** the filters SHALL occupy one row above the list

## MODIFIED Requirements

### Requirement: Period summary cards

Above the filters the screen SHALL render four cards for the current filter: 買進金額 (with count), 賣出金額 (with count), 手續費＋交易稅 (with its share of turnover), and 已實現損益 (trend-coloured, linking to `/portfolio/realized-pnl`). The page header SHALL show the title, the period and total count, and the actions 匯入 CSV (to `/portfolio/import`) and 新增交易 (soft-primary); below 760 px 新增交易 stays as the one primary action and 匯入 CSV moves into a ⋯ menu button. At < 760px only 買進 and 賣出 SHALL be shown, side by side. The card figures SHALL be TWD all-in totals computed client-side over every row of the active filter, read 100 rows per request and capped at 50 requests, with the note `⋯ 僅載入前 5,000 筆` when the cap is reached; foreign rows without a finite positive `fx_rate_to_twd` SHALL be left out of the figures and counted in the note `不含外幣 n 筆`. 已實現損益 SHALL be the realized-P&L summary (`filter_scope_total`) for the same filters without `side`. The page has no market filter, so the scenario below is met through the symbol, side, broker and date filters, not a 台股 switch.

#### Scenario: Summary follows the filter
- **WHEN** the owner filters to 台股 only
- **THEN** the four cards SHALL recompute for 台股 trades only

#### Scenario: Realized card links
- **WHEN** the owner clicks the 已實現損益 card
- **THEN** the router navigates to `/portfolio/realized-pnl`

#### Scenario: Phone header
- **WHEN** `/portfolio/transactions` renders at 390 px
- **THEN** the header SHALL show the title, 新增交易 and a ⋯ menu holding 匯入 CSV, on one row
