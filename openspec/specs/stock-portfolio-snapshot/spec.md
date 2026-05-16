# stock-portfolio-snapshot Specification

## Purpose
TBD - created by archiving change add-portfolio-snapshot-and-history. Update Purpose after archive.
## Requirements
### Requirement: Service persists daily portfolio totals

The service SHALL maintain a `portfolio_snapshot` table keyed by `date PRIMARY KEY` storing five totals derived from `PortfolioSummary`.

#### Scenario: One row per TW calendar date
- **WHEN** `write_today_snapshot(db)` is called twice on the same TW calendar date
- **THEN** the table SHALL hold a single row for that date and its totals SHALL reflect the most recent call

#### Scenario: Totals mirror PortfolioSummary fields
- **WHEN** a snapshot is written
- **THEN** the row SHALL contain non-null `total_market_value`, `total_cost`, `total_unrealized_pnl`, `total_dividends` Decimal columns matching the same-named fields of `PortfolioSummary`, plus a nullable `portfolio_xirr` mirroring its optional analog

#### Scenario: created_at is server-defaulted
- **WHEN** a row is inserted
- **THEN** the row SHALL receive a `created_at` timestamp without explicit client input

### Requirement: History endpoint returns ordered ranges

The service SHALL expose `GET /api/portfolio/history?from=&to=` returning snapshots in ascending date order.

#### Scenario: Inclusive range
- **WHEN** the client passes `from=A&to=B`
- **THEN** the response SHALL include rows where `A <= date <= B`

#### Scenario: Default window when range omitted
- **WHEN** the client omits both `from` and `to`
- **THEN** the service SHALL return the last 90 days of snapshots ending today (TW)

#### Scenario: Empty range
- **WHEN** no snapshots exist in the requested range
- **THEN** the response SHALL be an empty array with HTTP 200

### Requirement: Manual snapshot trigger

The service SHALL expose `POST /api/portfolio/history/snapshot` to force a snapshot outside the cron schedule.

#### Scenario: Manual trigger writes today's row
- **WHEN** the endpoint is called
- **THEN** the service SHALL invoke `write_today_snapshot` and SHALL return the persisted row as JSON

