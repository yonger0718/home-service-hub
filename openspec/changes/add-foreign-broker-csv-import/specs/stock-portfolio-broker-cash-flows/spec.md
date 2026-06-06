## ADDED Requirements

### Requirement: broker_cash_flows table records per-broker cash events

The service SHALL persist every broker cash event in a dedicated `broker_cash_flows` table with columns `id, broker, date, type, amount, currency, fx_rate_to_twd, note, import_fingerprint, created_at`. The `type` column SHALL accept the values `deposit, withdrawal, interest, dividend_cash, fee`. The `import_fingerprint` column SHALL be `UNIQUE` to enforce idempotency.

#### Scenario: Cash flow row persists with all required columns
- **WHEN** an importer emits a cash flow `(broker='IB', date=2026-06-01, type='deposit', amount=3000.0, currency='USD')`
- **THEN** the database SHALL contain a `broker_cash_flows` row with those exact values plus a non-null `import_fingerprint` and `created_at`

#### Scenario: Duplicate fingerprint upload skips silently
- **GIVEN** the same broker CSV is uploaded twice
- **WHEN** the second upload runs
- **THEN** the second upload SHALL create zero new `broker_cash_flows` rows and the import response SHALL report the duplicate count

### Requirement: Per-broker cash balance is computed from broker_cash_flows

The networth backfill service SHALL expose a helper `get_broker_cash_balance(broker, as_of_date)` that returns the sum of all `broker_cash_flows.amount` for that broker on or before `as_of_date`. No materialised snapshot SHALL be stored — the balance is derived on read.

#### Scenario: Single deposit yields balance
- **GIVEN** one `broker_cash_flows` row `(broker='SCHWAB', date=2026-06-04, type='deposit', amount=1500.00)`
- **WHEN** `get_broker_cash_balance('SCHWAB', 2026-06-05)` is called
- **THEN** the result SHALL be `Decimal('1500.00')`

#### Scenario: Multiple events sum chronologically up to as_of_date
- **GIVEN** rows `(SCHWAB, 2026-06-04, deposit, 1500), (SCHWAB, 2026-06-05, withdrawal, -500), (SCHWAB, 2026-06-10, deposit, 200)`
- **WHEN** `get_broker_cash_balance('SCHWAB', 2026-06-06)` is called
- **THEN** the result SHALL be `Decimal('1000.00')` (the 2026-06-10 row is after as_of_date and excluded)

### Requirement: Read API exposes per-broker cash balance

The service SHALL expose `GET /api/portfolio/broker-cash-flows` that returns the current per-broker balance for every broker that has at least one row, in the shape `[{broker, currency, balance, as_of_date}]`.

#### Scenario: Endpoint returns one row per active broker
- **GIVEN** cash flow rows exist for IB, FIRSTRADE, and SCHWAB
- **WHEN** a client calls `GET /api/portfolio/broker-cash-flows`
- **THEN** the response SHALL contain exactly three rows, one per broker, each with a non-null `balance`
