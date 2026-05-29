# stock-portfolio-xirr Specification

## Purpose
TBD - created by archiving change add-windowed-xirr. Update Purpose after archive.
## Requirements
### Requirement: Portfolio-level windowed annualized return
The system SHALL compute portfolio-level annualized return (XIRR) for four fixed windows in addition to the existing lifetime XIRR: trailing 1 month (`portfolio_xirr_1m`), trailing 3 months (`portfolio_xirr_3m`), trailing 1 year (`portfolio_xirr_1y`), and year-to-date from January 1 of the current calendar year (`portfolio_xirr_ytd`). Each value SHALL be exposed as an optional `Decimal` on the `PortfolioSummary` response of `GET /api/portfolio/summary` and SHALL represent the annualized internal rate of return over the chosen window.

For every window, the cashflow series passed to XIRR SHALL consist of:
1. An opening outflow at `window_start` equal to the negation of the portfolio total market value at `window_start`, sourced from the most recent `portfolio_snapshot` with `date <= window_start`.
2. Every BUY/SELL transaction whose `trade_date` falls within `[window_start, today]`, recorded with the same sign convention as the lifetime XIRR (BUY → outflow, SELL → inflow, net of fee and tax).
3. Every dividend whose `ex_dividend_date` falls within `[window_start, today]`, recorded as an inflow.
4. A terminal inflow at today equal to the current portfolio total market value.

`window_start` for each window SHALL be derived from today (TW calendar) as:
- `1m`: today minus 1 month (calendar-month arithmetic; fall back to last day of the prior month when the target day does not exist).
- `3m`: today minus 3 months.
- `1y`: today minus 1 year.
- `ytd`: January 1 of the current calendar year.

#### Scenario: All four windows return a value when snapshots exist
- **WHEN** the caller invokes `GET /api/portfolio/summary` and a `portfolio_snapshot` row exists with `date <= window_start` for every one of `1m`, `3m`, `1y`, `ytd`
- **THEN** `portfolio_xirr_1m`, `portfolio_xirr_3m`, `portfolio_xirr_1y`, and `portfolio_xirr_ytd` SHALL each be populated with a non-null `Decimal` annualized return
- **AND** the existing `portfolio_xirr` field SHALL remain unchanged in value and meaning

#### Scenario: Windowed XIRR returns null when no snapshot at or before window_start
- **WHEN** no `portfolio_snapshot` row exists with `date <= window_start` for a given window
- **THEN** the corresponding `portfolio_xirr_*` field SHALL be `null`
- **AND** no error SHALL be raised; other windows that do have snapshots SHALL still be returned

#### Scenario: Cashflow inclusion is inclusive on the window edges
- **WHEN** a BUY, SELL, or dividend event has its date equal to `window_start` or equal to today
- **THEN** that event SHALL be included in the windowed cashflow series

### Requirement: Per-stock windowed annualized return
The system SHALL compute per-stock annualized return for the same four windows (`xirr_1m`, `xirr_3m`, `xirr_1y`, `xirr_ytd`) and SHALL expose each as an optional `Decimal` on every `StockHolding` entry of the `GET /api/portfolio/summary` response. The existing per-stock lifetime `xirr` field SHALL remain unchanged.

For every per-stock window, the cashflow series SHALL consist of:
1. An opening outflow at `window_start` equal to `-(quantity_at_window_start * opening_close_price)` where `quantity_at_window_start` is the net (BUY minus SELL) quantity replayed from all transactions with `trade_date < window_start`, and `opening_close_price` is `price_history.close` for that symbol on `window_start` (or the nearest previous trading-day row when `window_start` is not a trading day).
2. Every BUY/SELL/dividend event for that symbol whose date falls within `[window_start, today]`, with the same sign convention as the portfolio-level series.
3. A terminal inflow at today equal to the holding's current `market_value` from the summary computation.

When `quantity_at_window_start <= 0` the position did not exist at the window start; in that case the per-stock windowed XIRR SHALL fall back to a cashflow series that omits the opening outflow and uses only the in-window events plus the terminal inflow, mirroring the lifetime per-stock XIRR shape.

#### Scenario: Per-stock windowed XIRR populated when price history exists
- **WHEN** a holding has at least one BUY before `window_start`, has `price_history.close` available for `window_start` (or the nearest previous trading day), and currently has a non-zero `market_value`
- **THEN** the corresponding `xirr_1m`/`xirr_3m`/`xirr_1y`/`xirr_ytd` field on the holding SHALL be a non-null `Decimal`

#### Scenario: Per-stock windowed XIRR returns null when price history is missing
- **WHEN** no `price_history` row exists for the symbol on `window_start` or any trading day in the preceding seven calendar days
- **THEN** the corresponding per-stock `xirr_*` field SHALL be `null`
- **AND** other windows that do have price history SHALL still be returned

#### Scenario: Holding opened entirely within the window
- **WHEN** the holding's first BUY occurs on or after `window_start`
- **THEN** the opening outflow SHALL be omitted from the series
- **AND** the per-stock windowed XIRR SHALL be computed from in-window cashflows plus the terminal market value
- **AND** the result MAY be `null` per the existing `_calculate_xirr` rules (fewer than two distinct dates, non-positive terminal, etc.)

### Requirement: Gap remediation guidance
When any windowed XIRR field is `null` because the required `portfolio_snapshot` or `price_history` row is absent, the system SHALL document `python -m app.services.networth_backfill_service --rebuild-all` as the supported way to backfill the missing rows. The frontend SHALL render `—` for any `null` windowed XIRR value and SHALL surface a tooltip pointing the user at the backfill command.

#### Scenario: Frontend renders gap placeholder with tooltip
- **WHEN** the API returns `null` for a selected windowed XIRR field on the dashboard
- **THEN** the dashboard SHALL render `—` in place of a percentage
- **AND** the rendered element SHALL expose a tooltip containing the backfill CLI guidance

### Requirement: XIRR window selection in dashboard UI
The dashboard SHALL replace the single annualized-return display with a chip selector offering `1M`, `3M`, `1Y`, `YTD`, and `全部` options. The selected chip SHALL drive which XIRR field (portfolio-level on the headline card; per-stock on each expanded holding detail row) is rendered. The default selection SHALL be `1Y`.

#### Scenario: Switching the chip updates the rendered field
- **WHEN** the user selects a different chip
- **THEN** the headline XIRR card SHALL render the corresponding `portfolio_xirr*` field
- **AND** every expanded per-stock detail row SHALL render the corresponding per-stock `xirr*` field
- **AND** no additional API call SHALL be made (all values come from the single summary response)

#### Scenario: Default window on first load
- **WHEN** the dashboard is loaded for the first time in a session and the user has not yet chosen a chip
- **THEN** the `1Y` chip SHALL be selected
- **AND** `portfolio_xirr_1y` SHALL drive the headline card

