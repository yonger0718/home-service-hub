## Why

Users add manual cash transactions (deposits, withdrawals, wire fees, etc.) via the account-detail manual entry modal but cannot remove a mistaken or test entry without database access. Auto-derive / csv_import / backfill rows are correctly locked (they are derived from another entity and must stay in sync), but `source=manual` rows have no upstream — they own themselves and the user MUST be able to delete them from the UI.

## What Changes

- Add `DELETE /api/portfolio/accounts/{account_id}/cash-transactions/{txn_id}` endpoint that permanently removes ONE cash transaction row
- Endpoint accepts only `source=manual` rows: returns 403 with body `{detail: "only manual cash transactions can be deleted"}` for `auto_derive` or `csv_import` (the only non-manual values in the `CashTxnSource` enum; backfilled rows are tagged as one of these and are covered by the same guard)
- Returns 404 if the row does not exist OR belongs to a different account
- Returns 200 with `{deleted_id: <id>}` on success
- No cascade — manual rows have no `related_transaction_id` / `related_dividend_id`, so deletion is a plain row delete
- Frontend account-detail cash list adds a trash icon button on each row where `source === 'manual'`; clicking opens a PrimeNG confirmation dialog (`PrimeNG ConfirmDialog`) with the amount + date + note as the body; on confirm, fires the DELETE call, refetches list + balance history + account summary
- For non-manual rows the trash slot stays empty (no icon, no disabled button — clean look)

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-cash-accounts`: adds DELETE endpoint with source=manual guard
- `frontend-portfolio-accounts`: account detail cash list adds delete control on manual rows with confirmation dialog

## Impact

**Backend** (`services/stock-portfolio-service/`):
- `app/routers/accounts.py`: new `delete_cash_transaction` handler under existing prefix
- `app/services/cash_account_service.py`: new `delete_manual_cash_transaction(db, account_id, txn_id)` that raises `ValueError("not_manual")` if source != manual; raises `LookupError` if row not found / belongs to other account
- Tests: extend `tests/integration/test_accounts_endpoints.py` with: delete manual returns 200 + row count drops, delete auto_derive returns 403, delete missing returns 404, delete from wrong account returns 404
- Extend `tests/unit/test_cash_account_service.py` with service-level coverage of the guard

**Frontend** (`frontend/src/app/`):
- `services/portfolio.service.ts`: add `deleteCashTransaction(accountId, txnId): Observable<{deleted_id: number}>` calling `http.delete`
- `components/portfolio/accounts/account-detail.{ts,html,scss}`: trash-icon button on manual rows; uses existing `ConfirmationService` (already in PrimeNG); after success refetches list + balance history + parent account summary in parallel
- `account-detail.component.spec.ts`: trash icon hidden on non-manual rows, shown on manual, confirm-dialog fires DELETE, refresh fires on success

**No DB migration. No schema change.**
