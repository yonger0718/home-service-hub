## ADDED Requirements

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
