## ADDED Requirements

### Requirement: 總資產 tile on dashboard

The portfolio dashboard SHALL render a primary `總資產` tile above the existing tile row (market value / unrealized PnL / dividends / realized PnL / xirr). The tile displays `total_assets_twd` from `GET /api/portfolio/summary` formatted as TWD currency. When the user has zero accounts, the value equals `total_market_value` and the tile renders the same as before.

#### Scenario: Tile reflects live combined total

- **GIVEN** the API summary returns `total_market_value=500000` and `total_cash_twd=100000`
- **WHEN** the dashboard mounts
- **THEN** the 總資產 tile displays `NT$ 600,000`
- **AND** the existing market-value tile still displays `NT$ 500,000` (no overlap, no replacement)

#### Scenario: Tile with no cash accounts

- **GIVEN** `total_cash_twd = 0`
- **WHEN** the dashboard mounts
- **THEN** the 總資產 tile equals the market-value tile

### Requirement: Networth chart shows total market value and total assets

The networth chart on the dashboard SHALL render two overlaid line/area series: `總資產` (`total_assets_twd`) on top and `總市值` (`total_market_value`) below. The vertical gap between the two lines represents cash. The y-axis SHALL NOT be stacked; both series share the same absolute scale. The existing window selector (1M / 3M / 1Y / All) controls fetch range unchanged.

#### Scenario: Chart uses two non-stacked datasets

- **GIVEN** the history endpoint returns points each with `total_market_value` and `total_cash_twd`
- **WHEN** the chart renders
- **THEN** the chart configuration has exactly two datasets labeled `總資產` and `總市值`
- **AND** the y-axis configuration has `stacked: false`
- **AND** the `總資產` series equals `total_assets_twd` per point
- **AND** the `總市值` series equals `total_market_value` per point

#### Scenario: Backfill-pending collapses both lines

- **GIVEN** the migration deployed but backfill has not yet run, so all snapshot rows have `total_cash_twd = 0`
- **WHEN** the chart renders
- **THEN** `總資產` equals `總市值` at every point (the two lines visually coincide)

#### Scenario: Window switch preserves dataset shape

- **WHEN** the user switches from 1M to 1Y
- **THEN** the new range refetches and the two-dataset layout is preserved
