## MODIFIED Requirements

### Requirement: Service stores daily OHLC for TWSE and TPEx symbols

The service SHALL maintain a `price_history` table keyed by `(symbol, market, date)` capturing daily open, high, low, close, volume, turnover, native `currency`, and `source` for each trading day. The `market` column SHALL hold a top-level market code (`TW`, `US`, `LSE`) and default to `'TW'` for backwards compatibility. The `currency` column SHALL hold the native trading currency reported by the source feed (`TWD` for TW rows; yfinance `meta.currency` such as `USD`, `GBP`, `GBp` for foreign rows). The `source` column SHALL record the data feed (`TWSE` / `TPEx` / `yfinance`).

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
- **THEN** the `source` column SHALL be one of `TWSE`, `TPEx`, or `yfinance` for the markets enumerated above

#### Scenario: Existing TW rows backfill to `market='TW'` and `currency='TWD'`
- **WHEN** the migration adds the new `currency` column to an existing `price_history` table
- **THEN** every pre-existing row SHALL have `market='TW'` and `currency='TWD'` after upgrade and the new PK `(symbol, market, date)` SHALL hold without duplicate-key conflicts

#### Scenario: Foreign row records yfinance native currency
- **GIVEN** yfinance returns `regularMarketPrice=190.50, meta.currency='USD'` for `AAPL`
- **WHEN** the yfinance fetcher upserts the row
- **THEN** the persisted row SHALL have `symbol='AAPL'`, `market='US'`, `currency='USD'`, `source='yfinance'`

#### Scenario: LSE GBp tickers store pence units with currency `'GBp'`
- **GIVEN** yfinance returns `regularMarketPrice=8050.0, meta.currency='GBp'` for `VOD.L`
- **WHEN** the fetcher upserts the row
- **THEN** the persisted row SHALL have `currency='GBp'` and `close=Decimal('8050.0')` — no divide-by-100 normalization SHALL be applied at write time
