# stock-portfolio-broker-firstrade-import Specification

## Purpose
TBD - created by archiving change add-foreign-broker-csv-import. Update Purpose after archive.
## Requirements
### Requirement: Firstrade CSV importer parses equity and cash rows

The service SHALL accept Firstrade-format CSV uploads at `POST /api/portfolio/imports/csv` and parse the Chinese-headed columns `日期, 交易類別, 數量, 說明, 代號, 賬戶類別, 價格, 金額` into equity transactions and cash flows. Rows with `交易類別 in {買進, 賣出}` SHALL be written as `transactions` rows with `broker='FIRSTRADE'`, `market='US'`, `currency='USD'`. Rows with `交易類別='存款'` SHALL be written as `broker_cash_flows` rows with `type='deposit'`. Rows with `交易類別='利息收入'` SHALL be written as `broker_cash_flows` rows with `type='interest'`. The `賬戶類別` field SHALL be dropped from the parsed output.

#### Scenario: Buy row imports as transaction with FIRSTRADE broker
- **WHEN** the uploaded CSV contains `"2026/6/5","買進","10","Energy Fuels Inc","UUUU","融資","15.65","-156.50"`
- **THEN** the importer SHALL create one `transactions` row with `broker='FIRSTRADE'`, `market='US'`, `currency='USD'`, `symbol='UUUU'`, `quantity=10`, `price=15.65`, `type='BUY'`, `trade_date=2026-06-05`, and `fee=0`

#### Scenario: Sell row imports as SELL transaction
- **WHEN** the uploaded CSV contains `"2026/6/3","賣出","-27","...","UUUU","融資","18.71","505.15"`
- **THEN** the importer SHALL create one `transactions` row with `broker='FIRSTRADE'`, `type='SELL'`, `quantity=27`, `price=18.71`

#### Scenario: Deposit row imports as cash flow
- **WHEN** the uploaded CSV contains `"2026/6/5","存款","0","Wire Funds Received ...","","融資","0.00","2,500.00"`
- **THEN** the importer SHALL create one `broker_cash_flows` row with `broker='FIRSTRADE'`, `type='deposit'`, `amount=2500.00`, `currency='USD'`, `date=2026-06-05`

#### Scenario: Interest income imports as cash flow
- **WHEN** the uploaded CSV contains `"2026/5/18","利息收入","0","INTEREST ON CREDIT BALANCE AT 0.150% 04/16 THRU 05/15","","融資","0.00","0.05"`
- **THEN** the importer SHALL create one `broker_cash_flows` row with `broker='FIRSTRADE'`, `type='interest'`, `amount=0.05`, `currency='USD'`

#### Scenario: Margin marker is dropped
- **WHEN** any FT row carries `賬戶類別='融資'`
- **THEN** the parsed output SHALL NOT include any `margin` or `account_class` field on the resulting transaction or cash-flow row

### Requirement: Firstrade importer resolves FX rate from fx_rates table

For every transaction parsed from Firstrade CSV, the importer SHALL look up `fx_rates.rate_to_twd` for `currency='USD'` at `trade_date` and store the result in `transactions.fx_rate_to_twd`. If no `fx_rates` row exists for that date the importer SHALL reject the row with an explicit error containing the row index and the missing-FX reason.

#### Scenario: FX rate populated from fx_rates table
- **GIVEN** an `fx_rates` row `(currency='USD', date=2026-06-05, rate_to_twd=31.42)`
- **WHEN** a Firstrade BUY row dated `2026-06-05` is imported
- **THEN** the resulting `transactions` row SHALL have `fx_rate_to_twd=31.42`

#### Scenario: Missing FX rate rejects row with index
- **GIVEN** no `fx_rates` row exists for `(currency='USD', date=2026-06-05)`
- **WHEN** a Firstrade BUY row dated `2026-06-05` is imported
- **THEN** the importer SHALL return a row-indexed error `{"row_index": N, "reason": "missing FX rate for 2026-06-05 USD"}` and SHALL NOT write that row
- **AND** other valid rows in the same upload SHALL still be written

