# stock-portfolio-cash-accounts Specification

## Purpose
TBD - created by archiving change add-broker-cash-accounts. Update Purpose after archive.
## Requirements
### Requirement: Broker account model

The system SHALL persist broker accounts in a new `broker_account` table with columns: `id` (PK), `broker` (enum: `cathay`, `sinopac`, `firstrade`, `ib`, `cs`, `other`), `nickname` (VARCHAR(64)), `currency` (ISO-4217 3-letter), `opening_balance` (NUMERIC(20,4), default 0), `opening_date` (DATE, default `CURRENT_DATE`), `is_active` (BOOLEAN, default TRUE), `created_at` (TIMESTAMPTZ, default `now()`). Account identity is `(broker, nickname)` — that pair SHALL be UNIQUE — so a single broker MAY hold multiple accounts in different currencies (each with a distinct nickname).

#### Scenario: Creating an account persists all required fields
- **WHEN** a client POSTs `{"broker":"firstrade","nickname":"Firstrade Main","currency":"USD","opening_balance":"12345.67","opening_date":"2026-01-01"}` to `/api/portfolio/accounts`
- **THEN** a new `broker_account` row SHALL exist with those exact values, `is_active=true`, and `created_at` set to the current timestamp
- **AND** the response SHALL echo the persisted row including its generated `id`

#### Scenario: Duplicate (broker, nickname) is rejected
- **GIVEN** a `broker_account` row with `(broker='cathay', nickname='Cathay Main')` already exists
- **WHEN** a second create request reuses the same `(broker, nickname)` pair
- **THEN** the second request SHALL fail with HTTP 409 and the existing row SHALL be unchanged

#### Scenario: Soft-deactivating an account preserves history
- **WHEN** a client PATCHes `is_active=false` on an existing account
- **THEN** the row SHALL remain in the table with `is_active=false`
- **AND** GET `/api/portfolio/accounts` by default SHALL omit it
- **AND** all linked `cash_transaction` rows SHALL remain queryable

### Requirement: Cash transaction ledger

The system SHALL persist every cash inflow and outflow in a new `cash_transaction` table with columns: `id` (PK), `account_id` (FK → `broker_account.id`, `ON DELETE RESTRICT`), `txn_date` (DATE, NOT NULL), `type` (enum: `deposit`, `withdraw`, `buy_settle`, `sell_settle`, `fee`, `tax`, `dividend_cash`, `interest_in`, `margin_interest`, `wire_fee`, `fx_convert`), `amount` (NUMERIC(20,4), NOT NULL — positive = inflow, negative = outflow), `currency` (ISO-4217, must match account's currency for non-`fx_convert` types), `related_transaction_id` (FK → `transactions.id`, `ON DELETE SET NULL`, nullable), `related_dividend_id` (FK → `dividends.id`, `ON DELETE SET NULL`, nullable), `note` (VARCHAR(255), nullable), `source` (enum: `manual`, `csv_import`, `auto_derive`), `import_fingerprint` (VARCHAR(128), UNIQUE, NOT NULL). Indexes SHALL exist on `account_id`, `txn_date`, and `(related_transaction_id)`.

The `currency` column SHALL be denormalized from `broker_account.currency` at insert time so that account currency changes (which are non-goal in v1 anyway) do not retroactively reinterpret history.

#### Scenario: Posting a manual deposit creates an inflow row
- **GIVEN** an active `broker_account` with `currency=USD`
- **WHEN** a client POSTs `{"type":"deposit","txn_date":"2026-06-01","amount":"5000","note":"Wire from bank"}` to `/api/portfolio/accounts/{id}/cash-transactions`
- **THEN** a `cash_transaction` row SHALL be created with `amount=5000`, `currency=USD`, `source=manual`, `import_fingerprint=sha256("manual|{account_id}|{txn_date}|deposit|5000|Wire from bank")` (canonical string)
- **AND** the account's computed balance as-of `2026-06-01` SHALL increase by 5000

#### Scenario: Posting a withdrawal stores negative amount
- **WHEN** a client POSTs `{"type":"withdraw","txn_date":"2026-06-02","amount":"-200"}` (or the equivalent positive value with type indicating outflow per UI contract)
- **THEN** the persisted `amount` SHALL be `-200` (the service SHALL normalize to signed-by-type)
- **AND** the account's balance SHALL decrease by 200

#### Scenario: Duplicate idempotency fingerprint is rejected
- **GIVEN** a `cash_transaction` already exists with a given `import_fingerprint`
- **WHEN** another POST with the same canonical inputs would produce the same fingerprint
- **THEN** the INSERT SHALL be rejected by the UNIQUE constraint and the API SHALL return HTTP 409
- **AND** no second row SHALL be inserted

#### Scenario: Currency mismatch on non-fx_convert row is rejected
- **GIVEN** an account with `currency=TWD`
- **WHEN** a client posts a `deposit` row with `currency=USD`
- **THEN** the API SHALL return HTTP 400 with message indicating currency must match account

### Requirement: Compute-on-read balance service

The system SHALL provide a service function `get_balance(account_id: int, asof: date | None = None) -> Decimal` that returns `broker_account.opening_balance + SUM(cash_transaction.amount WHERE account_id=? AND txn_date <= asof)`. When `asof` is None, SHALL use the current date.

The system SHALL also provide `get_balance_history(account_id: int, date_from: date, date_to: date) -> list[BalancePoint]` returning one point per day between bounds (inclusive), where `BalancePoint = {date, balance}` and missing days inherit the previous day's balance (step function, not interpolation).

#### Scenario: Balance is opening + sum of dated rows
- **GIVEN** an account with `opening_balance=1000`, `opening_date=2026-01-01`, and three cash rows: `+500` on `2026-02-01`, `-200` on `2026-03-01`, `+100` on `2026-04-01`
- **WHEN** `get_balance(account_id, asof=2026-03-15)` is called
- **THEN** the result SHALL be `1300` (1000 + 500 - 200, excluding the `+100` row dated after `asof`)

#### Scenario: Future-dated rows are excluded
- **GIVEN** an account with no rows and `opening_balance=0`
- **WHEN** `get_balance(account_id, asof=2026-01-01)` is called against a DB containing a single row dated `2026-06-01` with `amount=+999`
- **THEN** the result SHALL be `0`

#### Scenario: Balance history step-fills missing days
- **GIVEN** an account with `opening_balance=0` and a single row `+1000` on `2026-06-02`
- **WHEN** `get_balance_history(account_id, date_from=2026-06-01, date_to=2026-06-03)` is called
- **THEN** the result SHALL be `[{2026-06-01: 0}, {2026-06-02: 1000}, {2026-06-03: 1000}]`

### Requirement: Multi-currency aggregate to reporting currency

The system SHALL provide `get_total_balance_in(target_currency: str, asof: date | None = None) -> Decimal` returning the sum of per-account balances converted to `target_currency` via `fx_rate_service.get_rate(asof_or_today, src_ccy, target_ccy)`. Accounts where `is_active=false` SHALL be excluded by default; a flag `include_inactive=true` SHALL include them.

When a rate is missing for `asof`, the most recent rate dated `<= asof` SHALL be used. When no rate exists at all for a currency pair, the aggregate SHALL exclude that account and report it in a `skipped_currencies` array alongside the total.

#### Scenario: Two USD accounts and one TWD account aggregate correctly
- **GIVEN** an active USD account with balance `1000` USD, an active USD account with balance `500` USD, an active TWD account with balance `30000` TWD, and an `fx_rate` row `(date=2026-06-01, base=USD, quote=TWD, rate=32.0)`
- **WHEN** `get_total_balance_in("TWD", asof=2026-06-01)` is called
- **THEN** the result SHALL be `30000 + (1500 * 32) = 78000` TWD

#### Scenario: Missing rate skips the account but does not raise
- **GIVEN** a JPY account with balance `100000` JPY and no `fx_rate` row for `(JPY, TWD, *)`
- **WHEN** `get_total_balance_in("TWD")` is called
- **THEN** the response SHALL exclude the JPY balance from the total
- **AND** `skipped_currencies` SHALL contain `"JPY"`

### Requirement: Backfill CLI replays existing transactions and dividends

The system SHALL provide a CLI invokable as `python -m app.services.cash_backfill_service --all [--dry-run]` that iterates every `transactions` row and every `dividends` row in `trade_date` ascending order, and for each emits one or more `cash_transaction` rows tagged `source=csv_import` (if the originating row's importer was a broker CSV) or `source=auto_derive` (otherwise).

For each `transactions` row, the backfill SHALL emit:
- one `buy_settle` (BUY) or `sell_settle` (SELL) row with `amount = ±(quantity * price)` (sign per side),
- one `fee` row with `amount = -fee` if `fee > 0`,
- one `tax` row with `amount = -tax` if `tax > 0`.

For each `dividends` row, the backfill SHALL emit one `dividend_cash` row with `amount = +cash_amount` (or per the existing dividends schema's amount column).

Each emitted row SHALL have a deterministic `import_fingerprint = sha256("backfill|{source_table}|{source_id}|{leg}")` so re-running the CLI is a no-op via the UNIQUE constraint.

The `--dry-run` mode SHALL compute and print row counts per account and per type without writing anything.

If no `broker_account` exists for the Cathay-imported rows, the CLI SHALL exit non-zero with a message instructing the operator to create one first.

#### Scenario: First run emits expected counts
- **GIVEN** a `broker_account` exists with `(broker=cathay, currency=TWD)`, 100 Cathay-imported `transactions` (50 BUY, 50 SELL) all with `fee>0` and `tax>0` (SELL only), and 10 `dividends` rows
- **WHEN** `--all` runs (not dry-run)
- **THEN** 100 settlement rows + 100 fee rows + 50 tax rows + 10 dividend rows = 260 `cash_transaction` rows SHALL be inserted
- **AND** all rows SHALL have `account_id` pointing at the Cathay account

#### Scenario: Re-running is idempotent
- **GIVEN** a backfill has already completed successfully
- **WHEN** `--all` runs again
- **THEN** every INSERT SHALL be a no-op (caught by `import_fingerprint` UNIQUE)
- **AND** no `cash_transaction` row SHALL be modified or duplicated

#### Scenario: Dry-run writes nothing
- **WHEN** `--all --dry-run` runs against a clean DB
- **THEN** the CLI SHALL print expected per-account / per-type counts
- **AND** zero `cash_transaction` rows SHALL exist after the command exits

#### Scenario: Missing default account is a hard error
- **GIVEN** Cathay-imported transactions exist but no `broker_account` with `broker=cathay` exists
- **WHEN** `--all` runs
- **THEN** the CLI SHALL exit with non-zero status and a message telling the operator to create the Cathay account first
- **AND** zero rows SHALL be inserted

### Requirement: Transaction CRUD syncs the linked cash leg

`portfolio_service.create_transaction`, `update_transaction`, and `delete_transaction` SHALL, inside the same DB transaction, create / update / delete the linked `cash_transaction` row(s) (settlement + fee + tax legs) tagged `source=auto_derive`. The link SHALL be by `cash_transaction.related_transaction_id`. If any cash-leg write fails, the parent transaction write SHALL roll back.

Manual transactions created before this feature shipped (no linked cash row) SHALL on first UPDATE create the missing cash rows. On DELETE of a transaction whose cash rows are already present, the cash rows SHALL be removed via explicit DELETE inside the same DB transaction (not via FK cascade, since FK is `ON DELETE SET NULL` to preserve manually-edited rows).

#### Scenario: Creating a SELL transaction emits settlement + fee + tax rows
- **WHEN** a client POSTs a manual SELL of 1000 shares at price 50 with fee 22 and tax 50 to `/api/portfolio/transactions`
- **THEN** three `cash_transaction` rows SHALL be created with `source=auto_derive` and `related_transaction_id` pointing at the new transaction
- **AND** their amounts SHALL be `+50000`, `-22`, `-50`

#### Scenario: Updating fee on an existing transaction syncs the linked fee row
- **GIVEN** an existing transaction with linked `cash_transaction` fee=−22
- **WHEN** the transaction is PATCHed to `fee=33`
- **THEN** the linked fee `cash_transaction` row SHALL be updated to `amount=-33`
- **AND** no additional fee row SHALL be inserted

#### Scenario: Deleting a transaction removes its cash legs
- **GIVEN** a transaction with three linked `cash_transaction` rows
- **WHEN** the transaction is DELETEd
- **THEN** the three linked cash rows SHALL be removed in the same DB transaction
- **AND** the account balance SHALL revert

### Requirement: Accounts REST endpoints

The system SHALL expose:

- `GET /api/portfolio/accounts?include_inactive=false` → list of accounts with computed `native_balance` and (optionally, via `?in_currency=TWD`) `target_balance` and `skipped_currencies`.
- `POST /api/portfolio/accounts` → create a new account (body matches the model in the broker-account requirement).
- `PATCH /api/portfolio/accounts/{id}` → update `nickname`, `is_active`, `opening_balance`, `opening_date`. Disallow `broker` and `currency` updates.
- `GET /api/portfolio/accounts/{id}/cash-transactions?date_from=&date_to=&type=&offset=&limit=&sort=` → paginated list, default sort `txn_date desc`.
- `POST /api/portfolio/accounts/{id}/cash-transactions` → manual entry (only `source=manual` allowed via this endpoint).
- `GET /api/portfolio/accounts/{id}/balance-history?date_from=&date_to=` → list of `BalancePoint`.
- `POST /api/portfolio/fx/refresh` → manually trigger an FX fetch (operator escape hatch when the scheduler job missed).

#### Scenario: List endpoint returns balances in TWD on request
- **GIVEN** two accounts in USD and TWD with known balances and a valid USD→TWD fx_rate
- **WHEN** `GET /api/portfolio/accounts?in_currency=TWD` is called
- **THEN** each account row SHALL include `native_balance` (in its native currency) and `target_balance` (in TWD)

#### Scenario: Cash-transactions endpoint paginates
- **GIVEN** an account with 200 cash rows
- **WHEN** `GET /api/portfolio/accounts/{id}/cash-transactions?limit=50&offset=50` is called
- **THEN** the response SHALL contain exactly 50 rows
- **AND** the total count SHALL be 200

#### Scenario: Manual-entry endpoint rejects non-manual source
- **WHEN** a client POSTs a body with `source=csv_import` to the manual-entry endpoint
- **THEN** the API SHALL ignore the field and persist with `source=manual` (the source enum is server-controlled, not client-controlled)

### Requirement: Delete manual cash transaction

The system SHALL provide `DELETE /api/portfolio/accounts/{account_id}/cash-transactions/{txn_id}` that permanently removes ONE cash transaction row when its `source` is `manual` AND it belongs to the given account.

The system SHALL reject deletion of rows whose `source` is `auto_derive` or `csv_import` (the only non-manual values in the `CashTxnSource` enum) with HTTP 403 and body `{"detail": "only manual cash transactions can be deleted"}`. Backfilled rows are tagged `auto_derive` or `csv_import` depending on origin and are covered by the same guard.

The system SHALL return HTTP 404 if the row does not exist OR exists but belongs to a different account.

The system SHALL return HTTP 200 with body `{"deleted_id": <txn_id>}` on success.

#### Scenario: Delete manual deposit row succeeds

- **GIVEN** a cash transaction id=42 exists on account 1 with `source=manual`, `type=deposit`, `amount=10000`
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/42`
- **THEN** the response is 200 with body `{"deleted_id": 42}`
- **AND** the row no longer exists in the database
- **AND** a subsequent `GET /api/portfolio/accounts/1/cash-transactions` does not include id=42

#### Scenario: Reject delete on auto_derive row

- **GIVEN** a cash transaction id=100 exists with `source=auto_derive` and a non-null `related_transaction_id`
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/100`
- **THEN** the response is 403 with body `{"detail": "only manual cash transactions can be deleted"}`
- **AND** the row still exists

#### Scenario: Reject delete on csv_import row

- **GIVEN** a cash transaction id=200 exists with `source=csv_import`
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/200`
- **THEN** the response is 403
- **AND** the row still exists

#### Scenario: Reject delete on backfilled csv_import row

- **GIVEN** a cash transaction id=300 exists with `source=csv_import` and an `import_fingerprint` starting with the backfill marker
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/300`
- **THEN** the response is 403
- **AND** the row still exists

#### Scenario: Missing row returns 404

- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/99999`
- **AND** no row with id=99999 exists
- **THEN** the response is 404

#### Scenario: Wrong-account row returns 404 (no existence leak)

- **GIVEN** a cash transaction id=42 exists on account 2
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/42` (note: account 1)
- **THEN** the response is 404 (not 403, not 200)
- **AND** the row still exists on account 2

#### Scenario: Balance reflects deletion immediately

- **GIVEN** account 1's `native_balance` is 100000 and includes a manual deposit row id=42 of amount 30000
- **WHEN** the client calls `DELETE /api/portfolio/accounts/1/cash-transactions/42` then `GET /api/portfolio/accounts/`
- **THEN** account 1's `native_balance` is 70000

### Requirement: Merge related cash transaction legs

The system SHALL support an optional `merge_related` query parameter on `GET /api/portfolio/accounts/{account_id}/cash-transactions` that collapses cash rows sharing a `related_transaction_id` into one synthetic group row per trade. When `merge_related=true`, pagination, sorting, and filtering operate on the merged virtual list.

The response item schema (`CashTransactionOut`) SHALL include an optional `child_legs: list[CashTransactionOut] | None` field populated only on synthetic group rows when merge is on.

#### Scenario: Merge off returns rows unchanged

- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions` without `merge_related` or with `merge_related=false`
- **THEN** the response contains each `cash_transaction` row as a discrete item with `child_legs` omitted or null
- **AND** `total` equals the count of underlying rows matching the filter

#### Scenario: Merge on groups settle + fee + tax legs of a BUY

- **GIVEN** a BUY transaction with id 42 emitted 3 cash legs: settle (-100000), fee (-285), tax (-300), all sharing `related_transaction_id=42`
- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions?merge_related=true`
- **THEN** the items list contains one synthetic group row with:
  - `id` = -42 (negative sentinel)
  - `type` = "trade"
  - `amount` = -100585
  - `txn_date` = settle leg's txn_date
  - `related_transaction_id` = 42
  - `child_legs` = the 3 original leg rows ordered settle → fee → tax
- **AND** the 3 underlying leg rows do NOT appear as separate items

#### Scenario: Dividend and manual rows stay individual when merge is on

- **GIVEN** the account has 2 dividend cash rows (`related_dividend_id` set) and 3 manual deposit rows (both relation FKs null)
- **WHEN** the client calls `GET /api/portfolio/accounts/1/cash-transactions?merge_related=true`
- **THEN** the dividend and manual rows appear individually with `child_legs` omitted or null

#### Scenario: Pagination total reflects merged count

- **GIVEN** the filtered set contains 60 BUY/SELL leg rows across 20 trades, 10 dividend rows, 5 manual rows
- **WHEN** the client calls the endpoint with `merge_related=true&limit=25&offset=0`
- **THEN** `total` = 35 (20 trade groups + 10 dividend + 5 manual)
- **AND** `items.length` = 25
- **AND** calling again with `offset=25` returns the remaining 10 items

#### Scenario: Type filter "fee" with merge on includes trade groups containing fee legs

- **GIVEN** 10 BUY trades each with a fee leg, plus 5 standalone fee rows from corrections
- **WHEN** the client calls the endpoint with `merge_related=true&type=fee`
- **THEN** the response includes 10 synthetic trade groups (each exposing its fee leg in `child_legs`) plus the 5 standalone fee rows
- **AND** `total` = 15

#### Scenario: Sort by amount uses summed group amount

- **WHEN** the client calls the endpoint with `merge_related=true&sort=amount:asc`
- **THEN** synthetic group rows are sorted by the sum of their legs' amounts, and individual rows by their own amount, intermixed

