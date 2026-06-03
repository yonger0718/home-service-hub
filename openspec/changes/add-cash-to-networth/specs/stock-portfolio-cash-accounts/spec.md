## ADDED Requirements

### Requirement: Live summary exposes cash and total assets

`GET /api/portfolio/summary` SHALL include two derived fields:

- `total_cash_twd`: live sum of all active broker accounts' balances converted to TWD via `cash_account_service.get_total_balance_in("TWD")`
- `total_assets_twd`: `total_market_value + total_cash_twd`

Both fields SHALL be present in every response (default to "0" if no accounts exist).

#### Scenario: Summary with one TWD account

- **GIVEN** account 1 (broker=cathay, currency=TWD) has balance 100000.00
- **AND** `total_market_value` from holdings is 500000.00
- **WHEN** the client calls `GET /api/portfolio/summary`
- **THEN** the response contains `total_cash_twd = "100000.0000"` and `total_assets_twd = "600000.0000"`

#### Scenario: Summary with mixed-currency accounts

- **GIVEN** account 1 (TWD) balance 100000, account 2 (USD) balance 1000, account 3 (GBP) balance 500
- **AND** FX rates: USD/TWD=31, GBP/TWD=39
- **WHEN** the client calls `GET /api/portfolio/summary`
- **THEN** `total_cash_twd = "100000 + 31000 + 19500 = 150500.0000"`

#### Scenario: Summary when no accounts exist

- **GIVEN** zero rows in `broker_account`
- **WHEN** the client calls `GET /api/portfolio/summary`
- **THEN** `total_cash_twd = "0"` and `total_assets_twd = total_market_value`

### Requirement: Daily snapshot persists cash totals

`portfolio_snapshot.write_today_snapshot` SHALL compute `total_cash_twd` for the snapshot date via `cash_account_service.get_total_balance_in(db, "TWD", asof=target)` and persist it in the new `total_cash_twd` column of `portfolio_snapshot`.

#### Scenario: Snapshot row carries cash total

- **GIVEN** the cash ledger has rows producing a TWD-equivalent balance of 150500 as of today
- **WHEN** the daily snapshot job runs
- **THEN** the resulting `portfolio_snapshot` row has `total_cash_twd = 150500`

#### Scenario: Missing FX rate skips account in snapshot

- **GIVEN** account 4 (currency=JPY) balance 1000000 and no JPY/TWD rate available for today
- **AND** account 1 (TWD) balance 100000
- **WHEN** the snapshot job runs
- **THEN** the row `total_cash_twd` includes the TWD account but excludes JPY
- **AND** a WARN log line records the skipped currency

### Requirement: Backfill recomputes historical cash for snapshot rows

`networth_backfill_service.run_backfill` (and the `--rebuild-all` CLI flag per `reference_realized_pnl_canonical_engine` pattern) SHALL recompute `total_cash_twd` for every snapshot date in the backfill window using `get_total_balance_in("TWD", asof=date)`.

#### Scenario: Rebuild updates historical cash on existing snapshot rows

- **GIVEN** snapshot rows exist for 2023-01-01 through 2026-06-03 with `total_cash_twd = 0` (pre-migration default)
- **WHEN** the operator runs `python -m app.services.networth_backfill_service --rebuild-all`
- **THEN** every snapshot row's `total_cash_twd` reflects the cash balance as-of that snapshot date

### Requirement: History endpoint exposes cash and total assets per snapshot

`GET /api/portfolio/history?from=&to=` response items SHALL include:

- `total_cash_twd`: the value stored on the snapshot row
- `total_assets_twd`: `total_market_value + total_cash_twd` (derived per item, no extra storage)

#### Scenario: History item carries cash and total

- **GIVEN** a snapshot row with `total_market_value=500000`, `total_cash_twd=100000`
- **WHEN** the client calls `GET /api/portfolio/history?from=2026-01-01&to=2026-06-03`
- **THEN** the matching response item contains `total_cash_twd = "100000.0000"` and `total_assets_twd = "600000.0000"`
