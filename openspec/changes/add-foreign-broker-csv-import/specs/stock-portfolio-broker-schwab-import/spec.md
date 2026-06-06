## ADDED Requirements

### Requirement: Schwab CSV importer parses equity and cash rows

The service SHALL accept Charles Schwab CSVs at `POST /api/portfolio/imports/csv` with columns `Date, Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount`. Rows with `Action in {Buy, Sell}` SHALL be written as `transactions` rows with `broker='SCHWAB'`, `market='US'`, `currency='USD'`. Rows with `Action='Wire Received'` SHALL be written as `broker_cash_flows` rows with `type='deposit'`. Rows with `Action='Wire Sent'` SHALL be written as `broker_cash_flows` rows with `type='withdrawal'`. The Amount field's `$` prefix SHALL be stripped at parse time.

#### Scenario: Wire Received imports as deposit cash flow
- **WHEN** the uploaded CSV contains `"06/04/2026","Wire Received","","WIRED FUNDS RECEIVED","","","","$1500.00"`
- **THEN** the importer SHALL create one `broker_cash_flows` row with `broker='SCHWAB'`, `type='deposit'`, `amount=1500.00`, `currency='USD'`, `date=2026-06-04`

#### Scenario: Buy row imports as transaction with SCHWAB broker
- **WHEN** the uploaded CSV contains `"06/04/2026","Buy","AAPL","APPLE INC","10","190.50","0.00","-$1905.00"`
- **THEN** the importer SHALL create one `transactions` row with `broker='SCHWAB'`, `market='US'`, `currency='USD'`, `symbol='AAPL'`, `quantity=10`, `price=190.50`, `type='BUY'`, `fee=0`

#### Scenario: Sell row imports as SELL transaction
- **WHEN** the uploaded CSV contains `"06/04/2026","Sell","AAPL","APPLE INC","5","195.00","0.10","$974.90"`
- **THEN** the importer SHALL create one `transactions` row with `broker='SCHWAB'`, `type='SELL'`, `quantity=5`, `price=195.00`, `fee=0.10`

#### Scenario: Dollar-sign prefix on Amount is stripped
- **WHEN** any Schwab row's `Amount` column carries a `$` prefix
- **THEN** the parser SHALL strip the `$` before converting to `Decimal`

### Requirement: Schwab importer rejects rows with missing FX rate

The Schwab importer SHALL look up `fx_rates.rate_to_twd` for `currency='USD'` at `trade_date` for every parsed transaction and SHALL reject any row whose FX rate is missing with a row-indexed error.

#### Scenario: USD row with no fx_rates entry rejects
- **GIVEN** no `fx_rates` row exists for `(currency='USD', date=2026-06-04)`
- **WHEN** the corresponding Schwab BUY row is imported
- **THEN** the importer SHALL emit `{"row_index": N, "reason": "missing FX rate for 2026-06-04 USD"}` and SHALL NOT persist the row
