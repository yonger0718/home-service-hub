## ADDED Requirements

### Requirement: Realized P&L converts foreign-currency events to TWD using frozen FX rate

The realized-PnL engine `iter_realized_events` SHALL convert each transaction's native price to TWD using the transaction's frozen `fx_rate_to_twd` column when present, before applying FIFO / moving-average cost-basis math. The same rule SHALL apply to dividend events: native `amount` × `fx_rate_to_twd` when present. When `fx_rate_to_twd IS NULL` (TWD-native rows), the engine SHALL treat native price as already-TWD and produce bit-for-bit identical results to the pre-migration behavior.

#### Scenario: TWD-native transaction yields unchanged realized P&L

- **GIVEN** a BUY for symbol `2330` with `currency='TWD'`, `fx_rate_to_twd=NULL`, price `100`
- **AND** a SELL for `2330` with `currency='TWD'`, `fx_rate_to_twd=NULL`, price `120`, quantity `1000`
- **WHEN** `iter_realized_events` runs
- **THEN** the emitted event SHALL have `avg_cost_at_sale=100`, `proceeds_gross=120000`, `realized_pnl=20000`

#### Scenario: Foreign BUY + SELL uses frozen FX on each leg

- **GIVEN** a BUY for symbol `AAPL` with `market='US'`, `currency='USD'`, `fx_rate_to_twd=32.0`, price `100` (USD), quantity `10`
- **AND** a SELL for `AAPL` with `market='US'`, `currency='USD'`, `fx_rate_to_twd=33.0`, price `110` (USD), quantity `10`
- **WHEN** `iter_realized_events` runs
- **THEN** the event's `cost_out` SHALL equal `10 * 100 * 32.0 = 32000` TWD
- **AND** `proceeds_gross` SHALL equal `10 * 110 * 33.0 = 36300` TWD
- **AND** `realized_pnl` SHALL equal `4300` TWD

#### Scenario: Dividend in foreign currency converts via frozen rate

- **GIVEN** a Dividend row with `currency='USD'`, `fx_rate_to_twd=32.5`, amount `50.0` (USD)
- **WHEN** the dividend contributes to `PortfolioSummary.total_dividends`
- **THEN** it SHALL contribute `50.0 * 32.5 = 1625.0` TWD

### Requirement: Transactions and dividends persist `market`, `currency`, and `fx_rate_to_twd`

The `transactions` and `dividends` schemas SHALL include `market VARCHAR(8) NOT NULL DEFAULT 'TW'`, `currency CHAR(3) NOT NULL DEFAULT 'TWD'`, and `fx_rate_to_twd NUMERIC(20,8) NULLABLE`. The schema layer (Pydantic models, ORM classes) SHALL expose these fields as optional inputs with the same defaults so existing TW callers compile and run unchanged.

#### Scenario: Existing TW caller passes no new fields

- **WHEN** a caller creates a transaction without specifying `market`, `currency`, or `fx_rate_to_twd`
- **THEN** the row SHALL persist with `market='TW'`, `currency='TWD'`, `fx_rate_to_twd=NULL`

#### Scenario: Foreign currency requires non-null frozen rate

- **WHEN** a transaction is created with `currency='USD'` and `fx_rate_to_twd=NULL`
- **THEN** the realized-PnL engine SHALL raise a `ValueError` on first iteration referencing the offending row, signaling missing FX freeze

#### Scenario: `transactions.price` accepts four decimal places

- **WHEN** a transaction is created with `price=234.5678`
- **THEN** the value SHALL round-trip without precision loss through the database

#### Scenario: `transactions.quantity` accepts fractional shares

- **WHEN** a transaction is created with `quantity=0.5`
- **THEN** the value SHALL persist as `0.5` and downstream cost-basis math SHALL treat it as a valid share count
