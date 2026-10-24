## ADDED Requirements

### Requirement: Uniform amount formatting

Every money value on screen SHALL go through one formatter. TWD amounts SHALL display without decimals (rounded to whole dollars); foreign-currency amounts SHALL keep two decimals with their currency; a leading sign (`+` or U+2212 `−`) SHALL appear only on changes, profit and loss, and ledger entries. The same value SHALL render identically on every page that shows it.

#### Scenario: Large TWD total
- **WHEN** the portfolio market value is 3,021,869.34 TWD
- **THEN** the hero legend, the 總市值 card and the holdings group header SHALL all show `3,021,869` (with `$` where the element shows currency)

#### Scenario: Profit keeps sign, loses decimals
- **WHEN** a holding's estimated PnL is 292,227.2 TWD
- **THEN** the holdings row SHALL show `+$292,227`

#### Scenario: Foreign amount keeps decimals
- **WHEN** a US holding's native PnL is 1,326 USD
- **THEN** the row SHALL show `1,326.00 USD`

#### Scenario: Same figure on two pages
- **WHEN** realized PnL for all periods is 35,205 TWD
- **THEN** the 交易紀錄 summary card and the 已實現損益 summary card SHALL both show `+$35,205`

### Requirement: Gain and loss colours only for gains and losses

`--app-trend-*` colours SHALL be used only for values that are a gain or a loss. Costs, fees and taxes SHALL use `--app-text`.

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
