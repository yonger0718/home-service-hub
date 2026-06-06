## ADDED Requirements

### Requirement: Realized P&L events expose the originating broker

The realized P&L API SHALL include an optional `broker` field on every event, copied verbatim from the originating SELL (long) or BUY-to-cover (short) `transactions.broker` row. The field SHALL be absent (or `null`) only when the originating transaction predates the broker column's backfill default; in that case the consumer SHALL treat it as `TW_MANUAL`.

#### Scenario: Foreign SELL event carries broker tag
- **GIVEN** an `IB`-stamped LONG SELL of 5 ACWD @ 350 USD on 2026-07-15
- **WHEN** the client calls `GET /api/portfolio/realized-pnl`
- **THEN** the resulting event SHALL contain `"broker": "IB"`

#### Scenario: TW SELL event carries broker tag
- **GIVEN** a `TW_CATHAY`-stamped LONG SELL of 1000 2330 @ 600 TWD
- **WHEN** the client calls `GET /api/portfolio/realized-pnl`
- **THEN** the resulting event SHALL contain `"broker": "TW_CATHAY"`

#### Scenario: Pre-backfill rows are treated as TW_MANUAL
- **GIVEN** a SELL row whose `transactions.broker` is NULL (predates the column backfill)
- **WHEN** the client calls `GET /api/portfolio/realized-pnl`
- **THEN** the resulting event's `broker` field SHALL be either absent or `"TW_MANUAL"`

### Requirement: Realized P&L page surfaces broker badge and broker filter

The Angular realized-PnL page SHALL render a broker badge per event when the event's `broker` is non-null and not `TW_MANUAL`. The page SHALL render a broker filter chip row above the table; chips SHALL be derived from the dataset (one chip per distinct broker present, plus an `ALL` chip default). When every event in the dataset has `broker='TW_MANUAL'` or `null`, the badge column and filter row SHALL both be hidden.

#### Scenario: Mixed dataset renders broker filter row
- **GIVEN** the realized-PnL response contains events with `broker in {IB, FIRSTRADE, TW_CATHAY}`
- **WHEN** the page renders
- **THEN** the filter row SHALL contain chips `[ALL, IB, FIRSTRADE, TW_CATHAY]` in that order

#### Scenario: Selecting a broker chip filters rows
- **WHEN** the user clicks the `IB` chip
- **THEN** only events with `broker='IB'` SHALL remain visible in the table

#### Scenario: TW-only dataset hides the badge column and filter
- **GIVEN** every event has `broker='TW_MANUAL'` or `null`
- **WHEN** the page renders
- **THEN** the broker badge column SHALL NOT render and the broker filter row SHALL NOT render
