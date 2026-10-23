## ADDED Requirements

### Requirement: Realized PnL summary

The page at `/portfolio/realized-pnl` SHALL render a header (已實現損益, the range and number of sells) and four summary cards for the current filter: the range total (with counts of profitable and losing sells), the current month, 當沖 (count and total), and 賣出成本費用 (sell-side fees plus transaction tax). Totals SHALL use `--app-trend-positive` / `--app-trend-negative` by sign. The card figures SHALL be computed client-side over every sell of the active filter, read 200 per request and capped at 50 requests, with the note `⋯ 僅載入前 10,000 筆；筆數與本月／當沖／費用為已載入部分` when the cap is reached; under 全部 the range total SHALL be the server `filter_scope_total` of the filter, under 台股 / 美股 the sum of that market's loaded sells. At < 760px only the first two cards (range total and current month) SHALL be shown.

#### Scenario: Range total and counts
- **GIVEN** five sells in 2026 with four gains and one loss totalling +21,046
- **WHEN** the page renders for 2026
- **THEN** the first card SHALL show +21,046 and 4 筆獲利、1 筆虧損

### Requirement: Monthly realized bars

The page SHALL render a 每月已實現 card with one column per month of the selected year: a bar above a zero line for a gain or below it for a loss, coloured by trend sign, with the month's total printed at the bar end and the month label beneath. Months after the current month SHALL be greyed. The bars SHALL be computed client-side from the realized events already loaded; the card SHALL have an accessible text listing the non-zero months. The bars SHALL draw the year filter's year (YTD = the current year), else the year of the range's end date, else the current year, and the card title SHALL read `每月已實現 · {year}`; under a custom range that spans years the bars SHALL show only that year while the range card SHALL still sum every year of the range.

#### Scenario: Loss month draws below the line
- **GIVEN** April's realized total is −4,897
- **WHEN** the card renders
- **THEN** April's bar SHALL extend below the zero line in `--app-trend-negative` with −4,897 beneath it

### Requirement: Expandable sell rows

Each sell SHALL render as a row button (with `aria-expanded`) showing the stock square, name, code, a 當沖 tag when applicable, `date · qty 股 · 均價 avg → 賣出 price · broker`, the realized amount and a percentage pill in trend colour. Expanding a row SHALL show 成交總額, 手續費, 交易稅 (labelled 交易稅（當沖）for day trades), 淨收入, 賣出成本 and 已實現損益; foreign sells show native amounts and the FX rate. The percentage pill SHALL be `realized_pnl / cost_out` at 1 dp and SHALL be omitted when the cost is 0 or missing. For a foreign sell, 均價 SHALL be `native_cost / quantity` (`—` when either is missing or 0) and 匯率 SHALL be the rate implied by `proceeds_gross / native_proceeds_gross` at 4 significant digits, omitted when the native gross is not positive.

#### Scenario: Expanding a row
- **WHEN** the owner expands the 鴻海 sell
- **THEN** the row SHALL show 成交總額 175,000, 手續費 249, 交易稅 525, 淨收入 174,226, 賣出成本 160,728 and 已實現損益 +13,498

### Requirement: Realized filters

A filter card SHALL offer search by code or name, a market `SegToggle` (全部 / 台股 / 美股), a 僅當沖 switch (`role="switch"`), a date range and a broker selector. Filters SHALL apply to the summary, the bars and the list together. The market filter SHALL apply client-side to the filter-wide set of sells (the API has no market filter; 美股 is every market other than TW): under 台股 / 美股 the list SHALL page that set client-side; a market or page change SHALL not reload the set, and a page change under 台股 / 美股 SHALL send no request.

#### Scenario: Day trades only
- **WHEN** the owner turns 僅當沖 on
- **THEN** only day-trade sells SHALL be listed and the summary and bars SHALL recompute
