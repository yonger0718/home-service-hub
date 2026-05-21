## ADDED Requirements

### Requirement: `iter_realized_events` is the canonical realized-PnL engine
The system SHALL treat `realized_pnl_service.iter_realized_events` as the single source of truth for realized-PnL across all consumers: `/api/portfolio/realized-pnl` endpoint, `portfolio_snapshot.total_realized_pnl` replay, dashboard cumulative aggregates, and any future report. No consumer SHALL implement its own inline realized-PnL accumulator.

#### Scenario: Snapshot replay calls iter_realized_events
- **WHEN** `networth_backfill_service._replay_snapshots` computes `total_realized_pnl` for any date
- **THEN** the value comes from `iter_realized_events` output, not from an inline BUY/SELL FIFO loop

#### Scenario: Endpoint and snapshot agree
- **WHEN** the same transaction set is processed by `/api/portfolio/realized-pnl?from=A&to=B` and by snapshot replay over `[A, B]`
- **THEN** `sum(events.net_pnl)` from the endpoint equals `portfolio_snapshot.total_realized_pnl[B] - portfolio_snapshot.total_realized_pnl[day_before(A)]`
- **AND** divergence greater than `Decimal('0.01')` SHALL be treated as a regression
