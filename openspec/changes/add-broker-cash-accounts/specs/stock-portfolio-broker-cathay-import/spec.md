## ADDED Requirements

### Requirement: Cathay importer emits linked cash transaction rows

When the 國泰 import path commits a `transactions` row (whether on the insert path, the legacy-fingerprint rehash path, or the business-key rehash path) AND the `add-broker-cash-accounts` feature is enabled, the importer SHALL, inside the same DB transaction, emit one or more linked `cash_transaction` rows tagged `source=csv_import`:

- one settlement row (`buy_settle` for BUY, `sell_settle` for SELL) with `amount = ±(quantity * price)` (sign per side),
- one `fee` row with `amount = -fee` when `fee > 0`,
- one `tax` row with `amount = -tax` when `tax > 0`.

Each emitted cash row SHALL link back via `related_transaction_id = transactions.id` and SHALL use the deterministic `import_fingerprint = sha256("csv|cathay|{transaction_import_fingerprint}|{leg}")` so re-imports are idempotent.

When the rehash path updates an existing `transactions` row's `import_fingerprint`, `position_side`, `fee`, or `tax`, the importer SHALL also rewrite the linked cash rows' `import_fingerprint` (to match the new transaction fingerprint) and `amount` (to match the new fee / tax / quantity * price). If a linked cash row is missing for a rehashed transaction (legacy rows imported before the feature shipped), the rehash path SHALL create the missing rows.

Similarly, when the Cathay importer commits a `dividends` row, it SHALL emit one `dividend_cash` row with `amount = +cash_amount`, `source=csv_import`, `related_dividend_id = dividends.id`, and the same deterministic-fingerprint scheme.

The default broker_account for Cathay-emitted rows SHALL be the active `broker_account` where `broker='cathay'` and `currency='TWD'`. If zero or more than one such row exists, the importer SHALL fail the batch with a clear error directing the operator to create or deduplicate the account.

#### Scenario: BUY row commits with linked settle + fee row
- **GIVEN** the feature is enabled and a Cathay account exists
- **WHEN** a CSV row with `買賣別='現買'`, `quantity=1000`, `price=50`, `fee=22` is committed via the insert path
- **THEN** a `cash_transaction` row SHALL exist with `type=buy_settle`, `amount=-50000`, `currency=TWD`, `source=csv_import`, `related_transaction_id` pointing at the new transaction
- **AND** a second `cash_transaction` row SHALL exist with `type=fee`, `amount=-22`, linked to the same transaction
- **AND** no tax row SHALL be inserted (BUY has tax=0 in 國泰 CSV)

#### Scenario: SELL row commits with settle + fee + tax rows
- **GIVEN** the feature is enabled
- **WHEN** a CSV row with `買賣別='現賣'`, `quantity=1000`, `price=50`, `fee=22`, `tax=150` is committed
- **THEN** three linked `cash_transaction` rows SHALL exist: `sell_settle=+50000`, `fee=-22`, `tax=-150`

#### Scenario: Rehash path updates linked cash rows in lockstep
- **GIVEN** an existing transaction with linked cash rows whose fees and quantity were imported by an older parser
- **WHEN** a fresh CSV upload matches the row via legacy fingerprint and rewrites `fee=141` (was 39) on the transaction
- **THEN** the linked `fee` `cash_transaction` row SHALL be updated to `amount=-141`
- **AND** its `import_fingerprint` SHALL be recomputed to match the transaction's new fingerprint

#### Scenario: Legacy transaction without a linked cash row gains one on rehash
- **GIVEN** an existing transaction predating the feature (no `cash_transaction` rows exist with `related_transaction_id` equal to its id)
- **WHEN** a re-import triggers the rehash path on this row
- **THEN** the missing settlement / fee / tax rows SHALL be inserted alongside the rehash
- **AND** all rows SHALL share the same DB transaction

#### Scenario: Re-importing the same CSV is idempotent for cash rows
- **GIVEN** a Cathay CSV has been successfully imported (transactions and cash rows both exist)
- **WHEN** the same CSV is uploaded again
- **THEN** every cash-row INSERT SHALL be a no-op via the UNIQUE `import_fingerprint`
- **AND** zero new cash rows SHALL be inserted

#### Scenario: Missing Cathay account aborts the batch
- **GIVEN** the feature is enabled but no `(broker=cathay, currency=TWD, is_active=true)` account exists
- **WHEN** any 國泰 CSV is uploaded with `dry_run=false`
- **THEN** the import SHALL fail with HTTP 412 (or equivalent) and a message identifying the missing account
- **AND** no `transactions` rows or `cash_transaction` rows SHALL be committed

#### Scenario: Dividend import emits a dividend_cash row
- **GIVEN** the feature is enabled
- **WHEN** the importer commits a `dividends` row for `(symbol=0050, ex_date=2026-06-01, cash_amount=4500)`
- **THEN** a linked `cash_transaction` row SHALL exist with `type=dividend_cash`, `amount=+4500`, `currency=TWD`, `source=csv_import`, `related_dividend_id` pointing at the new dividend row
