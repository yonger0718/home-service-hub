## MODIFIED Requirements

### Requirement: Service persists daily portfolio totals

The service SHALL maintain a `portfolio_snapshot` table keyed by `date PRIMARY KEY` storing five totals derived from `PortfolioSummary`. The `total_realized_pnl` column SHALL be sourced from `realized_pnl_service.iter_realized_events` and MUST equal the aggregate returned by `GET /api/portfolio/realized-pnl` for the same `[earliest, date]` range.

#### Scenario: One row per TW calendar date
- **WHEN** `write_today_snapshot(db)` is called twice on the same TW calendar date
- **THEN** the table SHALL hold a single row for that date and its totals SHALL reflect the most recent call

#### Scenario: Totals mirror PortfolioSummary fields
- **WHEN** a snapshot is written
- **THEN** the row SHALL contain non-null `total_market_value`, `total_cost`, `total_unrealized_pnl`, `total_dividends` Decimal columns matching the same-named fields of `PortfolioSummary`, plus a nullable `portfolio_xirr` mirroring its optional analog

#### Scenario: created_at is server-defaulted
- **WHEN** a row is inserted
- **THEN** the row SHALL receive a `created_at` timestamp without explicit client input

#### Scenario: Realized PnL matches realized-pnl endpoint
- **WHEN** any consumer reads `portfolio_snapshot.total_realized_pnl` for date `D`
- **THEN** the value MUST equal `sum(event.net_pnl)` from `GET /api/portfolio/realized-pnl?to=D` (no `from` filter, or `from=` earliest trade date)
- **AND** the equality MUST hold across portfolios containing LONG-only, SHORT-only (融券), mixed LONG+SHORT, and day-trade (沖賣) histories

## ADDED Requirements

### Requirement: Snapshot-vs-realized-pnl parity is testable
The system SHALL include an integration test that seeds a mixed LONG+SHORT+day-trade transaction set, runs `_replay_snapshots` over the range, and asserts per-date equality between `portfolio_snapshot.total_realized_pnl` and the realized-PnL endpoint aggregate.

#### Scenario: Parity test covers mixed history
- **WHEN** the parity test runs with fixtures containing at minimum: one LONG round-trip, one SHORT round-trip (融券 open + close), one day-trade pair (沖賣), and one oversell
- **THEN** for every date `D` in the test range, `portfolio_snapshot.total_realized_pnl[D]` SHALL equal `sum(iter_realized_events).net_pnl where trade_date <= D`
- **AND** the test SHALL fail loudly if the two engines diverge by more than `Decimal('0.01')`
