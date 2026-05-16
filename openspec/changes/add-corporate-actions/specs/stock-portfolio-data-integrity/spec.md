## ADDED Requirements

### Requirement: Portfolio summary applies cumulative split factor at read time

When computing `PortfolioSummary`, the service SHALL adjust each historical transaction by the cumulative split factor for its `(symbol, trade_date)` so that `total_quantity` and `avg_cost` remain consistent with the current market reference price. Transaction rows SHALL NOT be mutated.

#### Scenario: No corporate actions leaves output unchanged
- **GIVEN** no `corporate_actions` rows exist
- **WHEN** `get_portfolio_summary` runs
- **THEN** every numeric field SHALL match the previous (pre-feature) output for the same transaction set

#### Scenario: Pre-event transactions are multiplied
- **GIVEN** a BUY of 1 share at price 100 on 2026-01-01
- **AND** a corporate action with `ratio=10` on 2026-02-01
- **WHEN** `get_portfolio_summary` runs after 2026-02-01
- **THEN** the holding for that symbol SHALL show `total_quantity=10` and an adjusted `avg_cost` consistent with 10 shares at price 10

#### Scenario: Post-event transactions are not adjusted
- **GIVEN** a BUY of 5 shares at price 12 on 2026-03-01
- **AND** a corporate action with `ratio=10` on 2026-02-01
- **WHEN** `get_portfolio_summary` runs
- **THEN** that BUY SHALL contribute `quantity=5` and `price=12` unchanged to the aggregation

#### Scenario: Adjustment is reversible
- **WHEN** the corp-action row is deleted
- **THEN** the next `get_portfolio_summary` call SHALL return the pre-adjustment values
