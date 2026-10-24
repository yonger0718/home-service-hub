## ADDED Requirements

### Requirement: Chart bridges cash-only snapshot rows

The net-worth chart SHALL NOT draw a drop to 0 for a snapshot row that holds cash only (`total_market_value` and `total_cost` both 0) when the previous point has holdings: 持股淨值 SHALL carry the previous value for that date and 帳戶總值 SHALL be that value plus the row's cash.

#### Scenario: Dividend received on a Saturday
- **GIVEN** a cash-only snapshot on Saturday 2026-01-10 between two trading days with market value about 2.9 M
- **WHEN** the 1Y range renders
- **THEN** neither series SHALL dip toward 0 on 2026-01-10

### Requirement: Phone summary density

Below 760 px the four KPI cards SHALL render as a 2 × 2 grid, and each holdings row SHALL use two lines: name and market value, then `quantity 股 · price · day change` and the PnL with its percentage.

#### Scenario: Phone dashboard
- **WHEN** `/portfolio` renders at 390 px
- **THEN** the four KPI cards SHALL occupy two rows of two
- **AND** each holdings row SHALL be at most two text lines tall
