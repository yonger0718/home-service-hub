# stock-portfolio-realized-pnl Specification

## Purpose
TBD - created by archiving change add-realized-pnl-events-page. Update Purpose after archive.
## Requirements
### Requirement: Per-SELL realized event listing

The system SHALL expose `GET /api/portfolio/realized-pnl` returning one realized P&L event per SELL transaction, computed against the corporate-action-adjusted transaction view using moving-average cost basis. Each event SHALL include `trade_date`, `symbol`, `name`, `quantity`, `sell_price`, `avg_cost_at_sale`, `fee`, `tax`, `proceeds_gross`, `proceeds_net`, `cost_out`, `realized_pnl`, `is_day_trade`, and an optional `note` field.

#### Scenario: SELL after one BUY uses that BUY's price as average cost

- **WHEN** a portfolio contains a single BUY of 1000 shares at 100 NT$ followed by a SELL of 400 shares at 120 NT$ with zero fees and zero tax
- **THEN** the endpoint returns exactly one event with `quantity=400`, `avg_cost_at_sale=100`, `cost_out=40000`, `proceeds_net=48000`, and `realized_pnl=8000`

#### Scenario: SELL after multiple BUYs uses moving average

- **WHEN** a portfolio contains BUYs of 1000 @ 100 and 500 @ 130 (both zero fee) followed by a SELL of 600 @ 140 (zero fee, zero tax)
- **THEN** the endpoint returns one event whose `avg_cost_at_sale` equals the weighted average `110` and whose `realized_pnl` equals `(600 * 140) - (600 * 110) = 18000`

#### Scenario: Fees and taxes are included in net proceeds

- **WHEN** a SELL records `fee=85` and `tax=255`
- **THEN** the event's `proceeds_net` equals `proceeds_gross - 85 - 255` and `realized_pnl` is computed from `proceeds_net`

#### Scenario: Day-trade flag is propagated

- **WHEN** a SELL transaction has `is_day_trade=true`
- **THEN** the corresponding event has `is_day_trade=true`

### Requirement: Aggregate equals dashboard cumulative

The system SHALL guarantee that the sum of `realized_pnl` across every event returned by the endpoint with no filters applied equals `PortfolioSummary.total_realized_pnl` for the same portfolio state.

#### Scenario: Sum of unfiltered events matches summary

- **WHEN** the endpoint is called with no filters
- **AND** `get_portfolio_summary()` is called for the same portfolio
- **THEN** `sum(event.realized_pnl for event in response.items_across_all_pages)` equals `summary.total_realized_pnl`

### Requirement: Filter by symbol, date range, year, and day-trade

The endpoint SHALL accept the optional query parameters `symbol`, `date_from`, `date_to`, `year`, and `day_trade_only` and return only events that match all supplied filters. When `year` is supplied alongside `date_from` or `date_to`, the explicit date range SHALL take precedence.

#### Scenario: Symbol filter narrows results

- **WHEN** a portfolio contains SELL events for both `2330` and `6488`
- **AND** the endpoint is called with `symbol=2330`
- **THEN** only events with `symbol=2330` are returned

#### Scenario: Year preset maps to calendar year boundaries

- **WHEN** the endpoint is called with `year=2025` and no `date_from` / `date_to`
- **THEN** the response contains only events whose `trade_date` falls within `2025-01-01` to `2025-12-31` inclusive

#### Scenario: Day-trade toggle filters non-day-trade rows out

- **WHEN** the endpoint is called with `day_trade_only=true`
- **THEN** the response contains only events with `is_day_trade=true`

### Requirement: Pagination and sort

The endpoint SHALL return a paged response of the shape `{items, total, summary}` and accept `offset`, `limit`, and `sort` query parameters. Default `limit` SHALL be 25; default `sort` SHALL be `trade_date:desc`. `total` SHALL reflect the total event count after filters and before pagination.

#### Scenario: Pagination boundary returns expected slice

- **WHEN** a filtered query produces 60 events and the endpoint is called with `offset=50&limit=25`
- **THEN** `items` contains 10 events, `total` equals 60

#### Scenario: Sort by realized PnL descending

- **WHEN** the endpoint is called with `sort=realized_pnl:desc`
- **THEN** `items` is ordered by `realized_pnl` from highest to lowest

### Requirement: Filter-scope and YTD aggregates in response

The endpoint SHALL include a `summary` object containing `filter_scope_total` (sum of `realized_pnl` across all events matching the active filter, regardless of pagination) and `ytd_total` (sum of `realized_pnl` across all events whose `trade_date` falls in the current calendar year, ignoring `symbol`, `date_from`, `date_to`, `year`, and `day_trade_only` filters).

#### Scenario: Filter-scope total reflects active filter

- **WHEN** the endpoint is called with `symbol=2330`
- **THEN** `summary.filter_scope_total` equals the sum of `realized_pnl` across every `2330` event, ignoring `offset` / `limit`

#### Scenario: YTD total ignores filters

- **WHEN** the endpoint is called with `symbol=6488`
- **AND** the portfolio also has `2330` SELL events in the current calendar year
- **THEN** `summary.ytd_total` includes the realized PnL of every event in the current calendar year for every symbol

### Requirement: No-inventory SELL is flagged

When a SELL transaction occurs against zero prior inventory of the same symbol (e.g., broker 融券 short or data anomaly), the system SHALL still emit an event with `cost_out=0`, `realized_pnl=proceeds_net`, and `note="no_inventory"`.

#### Scenario: SELL with no prior BUY is flagged

- **WHEN** a portfolio contains a SELL of 100 shares of `9999` at 50 NT$ (zero fees, zero tax) with no prior BUY of `9999`
- **THEN** the response contains an event with `cost_out=0`, `realized_pnl=5000`, and `note="no_inventory"`

### Requirement: Frontend page and navigation

The frontend SHALL provide a route `/portfolio/realized-pnl` rendering a page that displays two aggregate cards (filter-scope total and YTD total), a filter bar (symbol autocomplete, date-from, date-to, year preset chips, day-trade toggle, sort dropdown), an event list using the existing hub-modern-list card layout, and a paginator with selectable page sizes. The top-level navigation SHALL include a link "已實現損益" to this route.

#### Scenario: Page renders with required regions

- **WHEN** a user navigates to `/portfolio/realized-pnl`
- **THEN** the page renders the aggregate cards, the filter bar, the event list, and the paginator

#### Scenario: Year preset clears manual date range

- **WHEN** a user has typed a manual date range and then clicks a year preset chip
- **THEN** the manual date range inputs are cleared and the query uses the year preset

