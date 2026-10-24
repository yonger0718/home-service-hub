## ADDED Requirements

### Requirement: Uniform amount formatting

Every money amount on screen SHALL be rendered by one formatter (`formatMoney`): TWD and JPY in whole units, every other currency with two decimals, rounding half away from zero. The same value SHALL render identically on every page that shows it.

#### Scenario: Scope of the formatter
- **WHEN** a screen shows totals, KPI or summary values, row amounts, profit and loss, fees and taxes, cash balances or TWD ledger totals
- **THEN** each SHALL come from `formatMoney`, while prices, per-share values, the `nativeAmount` pipe output and the ledger's foreign-currency row display (`¥5,390` with its rate) keep their own precision

#### Scenario: Large TWD total
- **WHEN** the portfolio market value is 3,021,869.34 TWD
- **THEN** the 總市值 card and the holdings group header SHALL show `$3,021,869`, and the hero total and its composition legend (under the label 總資產 · TWD) SHALL show `3,021,869`

#### Scenario: Profit keeps sign, loses decimals
- **WHEN** a holding's estimated PnL is 292,227.2 TWD
- **THEN** the holdings row SHALL show `+$292,227`

#### Scenario: Foreign amount keeps decimals
- **WHEN** a US holding's native PnL is 1,326 USD
- **THEN** the row SHALL show `1,326.00 USD`

#### Scenario: Yen is whole units
- **WHEN** the formatter receives 5,390.4 JPY
- **THEN** it SHALL return `5,390 JPY`

#### Scenario: Same figure on two pages
- **WHEN** realized PnL for all periods is 35,205 TWD
- **THEN** the 交易紀錄 summary card and the 已實現損益 summary card SHALL both show `+$35,205`

#### Scenario: Rounding half away from zero
- **WHEN** the formatter receives 448.5 TWD and −4,076.5 TWD as changes
- **THEN** it SHALL return `+$449` and `−$4,077`

### Requirement: Currency marker and sign

A TWD amount SHALL carry the `$` prefix unless the element's own label or column header already names TWD; other currencies SHALL render as value, space, ISO code. A leading sign (`+` or U+2212 `−`) SHALL appear only on changes, profit and loss and ledger entries; other negative amounts keep `−` alone.

#### Scenario: Label already names TWD
- **GIVEN** the hero label reads 總資產 · TWD
- **WHEN** the hero total is 3,021,869.34
- **THEN** it SHALL show `3,021,869` while the 總市值 card shows `$3,021,869`

#### Scenario: Plain negative amount
- **WHEN** a cash balance is −4,076 TWD outside a change context
- **THEN** it SHALL show `−$4,076` with no `+` counterpart on positive balances

### Requirement: Uniform percent formatting

Percentages SHALL be rendered by one function (`formatPct`): one decimal below 100 in magnitude, none at or above, rounded half away from zero, with a `%` suffix; a leading `+` / `−` (U+2212) only with `sign: 'change'`. A ratio-type share (fees as a share of turnover, 佔成交額) SHALL pass `digits: 2` and keep two decimals.

#### Scenario: Percent precision
- **WHEN** `formatPct` receives 12.345 and 123.4
- **THEN** it SHALL return `12.3%` and `123%`

#### Scenario: Fee share keeps two decimals
- **WHEN** the 手續費＋交易稅 card footnote renders a fee share of 0.27 % of turnover
- **THEN** it SHALL read `佔成交額 0.27%`

#### Scenario: Percent change keeps its sign
- **WHEN** `formatPct` receives −2.35 as a change
- **THEN** it SHALL return `−2.4%`

### Requirement: Gain and loss colours only for gains and losses

`--app-trend-*` colours SHALL be used only for values that are a gain or a loss. The 賣出成本費用 summary value SHALL use `--app-text`; row-level fees and taxes (手續費, 交易稅) SHALL use `--app-text-muted`.

#### Scenario: Selling costs are neutral
- **WHEN** the 已實現損益 summary shows 賣出成本費用 of 1,249
- **THEN** the value SHALL use `--app-text`, not the trend colour

#### Scenario: Transaction fees
- **WHEN** a 交易紀錄 row shows 手續費 31 and 交易稅 110
- **THEN** both SHALL use `--app-text-muted`, not the trend colour

### Requirement: Logo legible in both themes

The sidebar logo lockup SHALL keep its wordmark at 4.5:1 contrast or more against the sidebar background in light and dark mode.

#### Scenario: Dark sidebar
- **GIVEN** dark mode is on
- **WHEN** the expanded sidebar renders
- **THEN** the words Home Hub and 家庭服務中樞 SHALL be visible against the sidebar background
