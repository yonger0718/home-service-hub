## MODIFIED Requirements

### Requirement: Realized PnL summary

The page at `/portfolio/realized-pnl` SHALL render a header (已實現損益, the range and number of sells) and four summary cards for the current filter: the range total (with counts of profitable and losing sells), the current month, 當沖 (count and total), and 賣出成本費用 (sell-side fees plus transaction tax). Gain/loss totals SHALL use `--app-trend-positive` / `--app-trend-negative` by sign; the 賣出成本費用 value SHALL use `--app-text` because it is a cost. Amounts follow the uniform formatter (`frontend-design-system`). The card figures SHALL be computed client-side over every sell of the active filter, read 200 per request and capped at 50 requests, with the note `⋯ 僅載入前 10,000 筆；筆數與本月／當沖／費用為已載入部分` when the cap is reached; under 全部 the range total SHALL be the server `filter_scope_total` of the filter, under 台股 / 海外 the sum of that market's loaded sells. At < 760px only the first two cards (range total and current month) SHALL be shown.

#### Scenario: Range total and counts
- **GIVEN** five sells in 2026 with four gains and one loss totalling +21,046
- **WHEN** the page renders for 2026
- **THEN** the first card SHALL show `+$21,046` and 4 筆獲利、1 筆虧損

#### Scenario: Cost value colour
- **WHEN** the summary renders with costs of 1,249
- **THEN** the 賣出成本費用 value SHALL read `$1,249` in `--app-text` under both gain/loss conventions

### Requirement: Realized filters

The page SHALL use `<app-filter-bar>`: search by code or name and the 年度 selector (YTD / 年份 / 全部) inline; behind 篩選 a date range, the market `SegToggle` (全部 / 台股 / 海外), a broker selector, a 僅當沖 switch (`role="switch"`) and sort. Filters SHALL apply to the summary, the bars and the list together. The market filter SHALL apply client-side to the filter-wide set of sells (the API has no market filter; 海外 is every market other than TW): under 台股 / 海外 the list SHALL page that set client-side; a market or page change SHALL not reload the set, and a page change under 台股 / 海外 SHALL send no request.

#### Scenario: Day trades only
- **WHEN** the owner turns 僅當沖 on (behind 篩選)
- **THEN** only day-trade sells SHALL be listed, the summary and bars SHALL recompute, and the 篩選 button SHALL read `篩選 1`

#### Scenario: Date range kept
- **WHEN** the owner sets a date range behind 篩選
- **THEN** the list, bars and summary SHALL follow it and a removable chip SHALL show the range under the bar
