## MODIFIED Requirements

### Requirement: Service stores daily OHLC for TWSE and TPEx symbols

The service SHALL maintain a `price_history` table keyed by `(symbol, market, date)` capturing daily open, high, low, close, volume, turnover, and source for each trading day. The `market` column SHALL hold a top-level market code (`TW`, `US`, `LSE`) and default to `'TW'` for backwards compatibility. The pre-existing `source` column continues to record the data feed (`TWSE` / `TPEx` / future `yfinance`).

#### Scenario: Composite primary key prevents duplicates within a market
- **WHEN** two rows with the same `(symbol, market, date)` are written
- **THEN** the table SHALL retain a single row representing the latest write for that key

#### Scenario: Same symbol may coexist across markets
- **WHEN** rows are written for `(symbol='X', market='TW', date=D)` and `(symbol='X', market='US', date=D)`
- **THEN** both rows SHALL persist independently

#### Scenario: Close must be positive
- **WHEN** a write attempts to set `close <= 0`
- **THEN** the database SHALL reject the write through a check constraint

#### Scenario: Source is recorded
- **WHEN** a row is persisted
- **THEN** the `source` column SHALL be either `TWSE` or `TPEx` for TW rows, and any other backfilled market SHALL record its appropriate source label

#### Scenario: Existing TW rows backfill to `market='TW'`
- **WHEN** the migration runs over an existing `price_history` table
- **THEN** every pre-existing row SHALL have `market='TW'` after upgrade and the new PK `(symbol, market, date)` SHALL hold without duplicate-key conflicts

## ADDED Requirements

### Requirement: Range query is scoped by market with `'TW'` default

The `GET /api/portfolio/price-history?symbol=&from=&to=` endpoint SHALL accept an optional `market` query parameter that defaults to `'TW'`. Results SHALL be filtered to the supplied market.

#### Scenario: Default market is TW
- **WHEN** a client queries with `symbol=2330` and omits `market`
- **THEN** the service SHALL return rows where `market='TW'`

#### Scenario: Explicit US market returns only US rows
- **WHEN** a client queries with `symbol=AAPL` and `market=US`
- **THEN** the service SHALL return only rows where `market='US'`

#### Scenario: Unknown market code returns empty
- **WHEN** a client queries with `market=XYZ` and no rows exist for that market
- **THEN** the response SHALL be an empty array with HTTP 200
