## ADDED Requirements

### Requirement: Delete control on manual cash rows

The account detail cash transactions list SHALL render a trash-icon button on every row whose `source === 'manual'`. The button SHALL be absent (not disabled, not present) on rows with any other `source` value.

Clicking the trash icon SHALL open a confirmation dialog with body text `{type label} {amount with sign} {currency} on {txn_date}{note ? " — " + note : ""}` and buttons `刪除` (severity danger) + `取消`.

Confirming the dialog SHALL call `DELETE /api/portfolio/accounts/{id}/cash-transactions/{txn_id}`. On HTTP 200, the page SHALL refetch the cash transactions list, balance history (current window), and the parent account summary in parallel. On any non-2xx response, a toast SHALL surface the error and the row SHALL remain in the list.

#### Scenario: Manual row shows trash icon

- **GIVEN** a row with `source=manual` in the rendered list
- **THEN** a trash icon button is visible on the row

#### Scenario: Non-manual row hides trash icon

- **GIVEN** a row with `source=auto_derive` (or `csv_import`, `backfill`) in the rendered list
- **THEN** no trash icon is rendered on the row

#### Scenario: Confirmation dialog shows row context

- **GIVEN** a manual deposit row with `amount=+10000`, `currency=TWD`, `txn_date=2026-06-03`, `note="testing"`
- **WHEN** the user clicks the trash icon
- **THEN** a confirmation dialog opens
- **AND** the dialog body contains `入金 +10,000 TWD on 2026-06-03 — testing`

#### Scenario: Confirm fires DELETE and refreshes

- **WHEN** the user clicks `刪除` in the confirmation dialog
- **THEN** the page fires `DELETE /api/portfolio/accounts/1/cash-transactions/42`
- **AND** on 200, the page re-fires the cash transactions list query, balance history query, and account summary query
- **AND** the deleted row no longer appears in the list

#### Scenario: Cancel closes dialog without DELETE

- **WHEN** the user clicks `取消` in the confirmation dialog
- **THEN** no DELETE request is fired
- **AND** the row remains in the list

#### Scenario: Server error surfaces toast

- **GIVEN** the backend returns 403 or 500 to the DELETE call
- **THEN** a toast appears with severity `error` and message including `刪除失敗`
- **AND** the row remains in the list
