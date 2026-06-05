## ADDED Requirements

### Requirement: Foreign holdings revalue at live FX in `get_portfolio_summary`

`portfolio_service.get_portfolio_summary` SHALL compute TWD market value for each foreign holding (any holding whose `market != 'TW'`) as `qty × native_close_in_base × live_fx_rate_to_twd`, where:

- `native_close` is the most recent `price_history.close` for `(symbol, market)`.
- `native_close_in_base` equals `native_close` when the row's persisted `currency` is an ISO base code (`USD`, `GBP`, `TWD`, ...), and equals `native_close / 100` when the persisted `currency` is `'GBp'`.
- `live_fx_rate_to_twd` is the most recent `fx_rates.rate_to_twd` for the base currency on-or-before today (`fx_rate_service.get_rate(db, base, today)`).

TW holdings (`market='TW'`) SHALL continue to be valued by the pre-Phase-2 TWSE quote path; their summary math SHALL NOT change.

Cost basis SHALL continue to be computed by the Phase 1 frozen-FX realized-PnL engine. The difference between live-FX market value and frozen-FX cost basis is the embedded FX P&L for foreign holdings, exposed via `unrealized_pnl` by design.

#### Scenario: USD holding revalues using latest USD/TWD rate

- **GIVEN** an open `AAPL` position with `market='US'`, `quantity=10`, frozen cost `100` USD/share at `fx_rate_to_twd=30.0`
- **AND** `price_history` row for `('AAPL', 'US', today)` with `close=Decimal('110.0')`, `currency='USD'`
- **AND** `fx_rates` row for `('USD', today)` with `rate_to_twd=Decimal('32.0')`
- **WHEN** `get_portfolio_summary(db)` runs
- **THEN** the `AAPL` holding's `market_value_twd` SHALL equal `10 * 110.0 * 32.0 = 35200`
- **AND** its `cost_basis_twd` SHALL equal `10 * 100 * 30.0 = 30000` (frozen)
- **AND** its `unrealized_pnl_twd` SHALL equal `5200`

#### Scenario: LSE GBp holding divides by 100 before applying GBP rate

- **GIVEN** an open `VOD` position with `market='LSE'`, `quantity=100`
- **AND** `price_history` row with `close=Decimal('8050.0')`, `currency='GBp'`
- **AND** `fx_rates` row for `('GBP', today)` with `rate_to_twd=Decimal('40.0')`
- **WHEN** `get_portfolio_summary(db)` runs
- **THEN** the holding's `market_value_twd` SHALL equal `100 * (8050.0 / 100) * 40.0 = 322000`

#### Scenario: Missing FX rate degrades to partial status

- **GIVEN** an open US holding with `price_history` close populated
- **AND** no `fx_rates` row exists for `USD` on-or-before today
- **WHEN** `get_portfolio_summary(db)` runs
- **THEN** the holding's `market_value_twd` SHALL be `None`
- **AND** `summary.quotes_status` SHALL be `'partial'` or `'unavailable'` (consistent with the existing missing-quote behavior)

#### Scenario: TW holding math is unchanged
- **GIVEN** a portfolio containing only TW positions
- **WHEN** `get_portfolio_summary(db)` runs before and after this feature deploys
- **THEN** every numeric field on `PortfolioSummary` and each `StockHolding` SHALL be byte-equal across the two runs

### Requirement: `StockHolding` exposes native price, currency, and live FX rate

The `StockHolding` schema SHALL expose `native_close: Decimal | None`, `native_currency: str | None`, and `live_fx_rate_to_twd: Decimal | None` for each holding. For TW rows these fields MAY be `None` or set to the TW close + `'TWD'` + `1.0` — implementations SHALL pick one and document it; consumers MUST NOT rely on TW rows carrying either shape.

#### Scenario: Foreign holding response carries native price + FX

- **GIVEN** a US holding revalued with `native_close=110.0`, `currency='USD'`, FX `32.0`
- **WHEN** `get_portfolio_summary(db)` returns
- **THEN** the holding entry SHALL include `native_close=Decimal('110.0')`, `native_currency='USD'`, `live_fx_rate_to_twd=Decimal('32.0')`

#### Scenario: GBp holding response preserves native pence + GBP rate

- **GIVEN** an LSE GBp holding revalued from `native_close=8050.0` and GBP rate `40.0`
- **WHEN** `get_portfolio_summary(db)` returns
- **THEN** the holding entry SHALL include `native_close=Decimal('8050.0')`, `native_currency='GBp'`, `live_fx_rate_to_twd=Decimal('40.0')` (the GBP base rate; the divide-by-100 happens internally for `market_value_twd`)
