## ADDED Requirements

### Requirement: IB CSV importer parses multi-section statements

The service SHALL accept Interactive Brokers multi-section CSVs at `POST /api/portfolio/imports/csv`. The importer SHALL read the `總結` section to anchor the account's base currency (`基礎貨幣`) and SHALL parse the `轉賬歷史` section with columns `日期, 賬戶, 說明, 交易類型, 代碼, 交易量, 價格, Price Currency, 總額, 佣金, 淨金額`. Rows with `交易類型 in {買, 賣}` SHALL be written as `transactions` with `broker='IB'`. Rows with `交易類型='存款'` SHALL be written as `broker_cash_flows` rows with `type='deposit'`. The `Statement` section header SHALL be ignored.

#### Scenario: Equity buy row imports with per-row currency
- **WHEN** the uploaded CSV contains `轉賬歷史,Data,2026-06-02,U***86396,SS SPD MS AL CO WO UC ET-USD,買,ACWD,1.0,325.05,USD,-325.05,-1.78,-326.83`
- **THEN** the importer SHALL create one `transactions` row with `broker='IB'`, `symbol='ACWD'`, `quantity=1`, `price=325.05`, `currency='USD'`, `fee=1.78`, `type='BUY'`, `trade_date=2026-06-02`

#### Scenario: Equity row currency comes from Price Currency column
- **GIVEN** an IB row with `Price Currency='GBP'`
- **WHEN** the row is imported
- **THEN** the resulting `transactions` row SHALL have `currency='GBP'` regardless of the account's `基礎貨幣`

#### Scenario: Deposit row imports as cash flow
- **WHEN** the uploaded CSV contains `轉賬歷史,Data,2026-06-01,U***86396,電子資金轉帳,存款,-,-,-,-,3000.0,-,3000.0`
- **THEN** the importer SHALL create one `broker_cash_flows` row with `broker='IB'`, `type='deposit'`, `amount=3000.0`, `currency=<base currency from 總結>`, `date=2026-06-01`

#### Scenario: Commission column populates transaction fee
- **WHEN** an IB equity row carries `佣金=-1.78`
- **THEN** the resulting `transactions` row SHALL have `fee=1.78` (absolute value, stored positive)

### Requirement: IB importer infers market from `Price Currency`

The importer SHALL set `transactions.market='LSE'` when the row's `Price Currency == 'GBP'`, and `transactions.market='US'` otherwise. The inference SHALL be deterministic from the CSV row alone and SHALL NOT make a yfinance network call during import. (An optional `market_resolver` hook MAY override the heuristic via the symbol-map table, applied later in `import_service`.)

#### Scenario: USD-denominated ticker resolves as US market
- **GIVEN** an IB row `(代碼='AAPL', Price Currency='USD')`
- **WHEN** the row is imported
- **THEN** the resulting `transactions.market` SHALL be `'US'`

#### Scenario: GBP-denominated ticker resolves as LSE market
- **GIVEN** an IB row `(代碼='VOD', Price Currency='GBP')`
- **WHEN** the row is imported
- **THEN** the resulting `transactions.market` SHALL be `'LSE'`

### Requirement: IB importer rejects rows with missing FX rate

For every IB equity row whose `Price Currency` is not `TWD`, the importer SHALL resolve `fx_rates.rate_to_twd` for that currency at `trade_date`. Missing entries SHALL reject the row with `{"row_index": N, "reason": "missing FX rate for <date> <currency>"}` and SHALL NOT block other rows in the same upload.

#### Scenario: USD row with no fx_rates entry rejects
- **GIVEN** no `fx_rates` row exists for `(currency='USD', date=2026-06-02)`
- **WHEN** the corresponding IB BUY row is imported
- **THEN** the importer SHALL emit a row-indexed error and SHALL NOT persist the transaction
