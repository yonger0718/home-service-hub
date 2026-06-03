## 1. Backend — service + router

- [x] 1.1 Add `delete_manual_cash_transaction(db, account_id: int, txn_id: int) -> int` to `app/services/cash_account_service.py`: SELECT row by id; if not found OR `row.account_id != account_id` raise `LookupError`; if `row.source != 'manual'` raise `ValueError("not_manual")`; else delete + commit; return the deleted id
- [x] 1.2 Add `DELETE /{account_id}/cash-transactions/{txn_id}` handler in `app/routers/accounts.py` that calls the service, returns `{"deleted_id": <id>}`, maps `LookupError` → HTTP 404 and `ValueError("not_manual")` → HTTP 403 with body `{"detail": "only manual cash transactions can be deleted"}`

## 2. Backend — tests

- [x] 2.1 Extend `tests/unit/test_cash_account_service.py` with: delete manual returns id and removes row, delete non-manual raises ValueError, delete missing raises LookupError, delete from wrong account raises LookupError
- [x] 2.2 Extend `tests/integration/test_accounts_endpoints.py` with: delete manual returns 200 + count drops + balance shifts, delete auto_derive returns 403, delete csv_import returns 403, delete backfill returns 403, delete missing returns 404, delete wrong-account returns 404
- [x] 2.3 `cd services/stock-portfolio-service && pytest tests/unit/ tests/integration/` clean

## 3. Frontend — service

- [x] 3.1 Add `deleteCashTransaction(accountId: number, txnId: number): Observable<{deleted_id: number}>` to `frontend/src/app/services/portfolio.service.ts` calling `this.http.delete<{deleted_id: number}>(`/api/portfolio/accounts/${accountId}/cash-transactions/${txnId}`)`

## 4. Frontend — detail page UI

- [x] 4.1 In `components/portfolio/accounts/account-detail.html` add a trash-icon button in the right slot of each list-item ONLY when `txn.source === 'manual'`; use existing PrimeNG icon button styling
- [x] 4.2 In `account-detail.ts` inject `ConfirmationService` and `MessageService` (already used); add `confirmDelete(txn: CashTransaction)` that calls `confirmationService.confirm({ message: <body>, header: '刪除交易', icon: 'pi pi-exclamation-triangle', acceptLabel: '刪除', rejectLabel: '取消', acceptButtonStyleClass: 'p-button-danger', accept: () => this.executeDelete(txn) })`
- [x] 4.3 `executeDelete(txn)`: call `portfolioService.deleteCashTransaction(accountId, txn.id)`; on success refetch cash transactions (current page), balance history (current window), and parent account summary in parallel; toast `success` with `已刪除`; on error toast `error` with `刪除失敗: <message>`
- [x] 4.4 Add `<p-confirmDialog>` element to the template if not already present; verify `MessageService` toast outlet exists in the layout
- [x] 4.5 Ensure register-provider in component decorator includes `ConfirmationService`

## 5. Frontend — tests

- [x] 5.1 Extend `account-detail.component.spec.ts` with: trash icon hidden on non-manual rows, shown on manual rows, click opens ConfirmationService.confirm(), accept callback fires DELETE, success refetches list + history + summary, error surfaces toast and keeps row visible
- [x] 5.2 `cd frontend && npm test` clean
- [x] 5.3 `npm run build` clean

## 6. Manual verification

- [ ] 6.1 With dev server up, navigate to `/portfolio/accounts/1`, locate a row created via 新增交易 (`source=manual`)
- [ ] 6.2 Click trash icon → confirmation dialog shows correct amount + date + note
- [ ] 6.3 Confirm → row disappears, balance + chart update
- [ ] 6.4 Verify a `source=auto_derive` row (any BUY/SELL leg) shows NO trash icon
- [ ] 6.5 Verify a backfilled row (`source=csv_import` or `source=auto_derive`, depending on origin) shows NO trash icon
