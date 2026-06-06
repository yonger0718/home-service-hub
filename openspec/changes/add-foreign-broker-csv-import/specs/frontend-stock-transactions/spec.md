## ADDED Requirements

### Requirement: Transaction list renders broker badge for non-default brokers

The transaction list SHALL render a broker badge next to the existing market badge for every row whose `broker` is non-null and not `TW_MANUAL`. TW_MANUAL and null rows SHALL render without a broker badge so the existing TW workflow is visually unchanged.

#### Scenario: IB row shows the IB badge
- **GIVEN** a transaction with `broker='IB'`
- **WHEN** the transaction list renders
- **THEN** the row SHALL display a badge reading `IB` next to the existing market badge

#### Scenario: TW_MANUAL row hides the broker badge
- **GIVEN** a transaction with `broker='TW_MANUAL'`
- **WHEN** the transaction list renders
- **THEN** the row SHALL NOT display a broker badge

#### Scenario: Null broker hides the broker badge
- **GIVEN** a transaction with `broker=null` (pre-backfill row)
- **WHEN** the transaction list renders
- **THEN** the row SHALL NOT display a broker badge
