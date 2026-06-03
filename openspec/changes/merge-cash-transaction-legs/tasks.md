## 1. Backend — schema + service

- [x] 1.1 Extend `CashTransactionOut` in `services/stock-portfolio-service/app/schemas/cash_account.py` with `child_legs: list[CashTransactionOut] | None = None`; add `"trade"` to the schema-level type enum (NOT the DB enum)
- [x] 1.2 In `app/services/cash_account_service.py`, add private helper `_merge_legs_into_groups(rows: list[CashTransaction]) -> list[CashTransactionOut]` that:
      - partitions rows into `with_rt = [r for r in rows if r.related_transaction_id]` and `standalone = rows - with_rt`
      - groups `with_rt` by `related_transaction_id`
      - for each group builds a synthetic `CashTransactionOut` per design D2 (id = -rt_id, type="trade", amount=sum, txn_date=settle.txn_date, currency/source from legs[0], note=None, related_transaction_id=rt_id, child_legs=ordered settle/fee/tax)
      - returns groups + standalone (un-sorted; caller sorts)
- [x] 1.3 Extend the existing list query function with a `merge_related: bool = False` kwarg. When True: fetch ALL filtered rows (no LIMIT/OFFSET at the SQL layer), run `_merge_legs_into_groups`, apply sort, then slice `[offset:offset+limit]`. When False: keep current LIMIT/OFFSET SQL path
- [x] 1.4 When merge is on, derive `total` from the merged virtual list length, NOT the raw row count
- [x] 1.5 Type filter with merge on: a group is included if ANY leg matches the filter; standalone rows match by their own type. Synthetic `"trade"` is NOT a selectable filter value at the SQL level
- [x] 1.6 Sort with merge on: group sort keys use settle leg's `txn_date` / `created_at` and the summed `amount`; standalone rows use their own values

## 2. Backend — router

- [x] 2.1 In `app/routers/accounts.py`, add `merge_related: bool = Query(False)` to the `GET /{account_id}/cash-transactions` endpoint and forward to the service call

## 3. Backend — tests

- [x] 3.1 Extend `tests/integration/test_accounts_endpoints.py` with: merge-off baseline (existing test untouched), merge-on groups BUY+SELL legs, merge-on dividend rows stay individual, merge-on manual rows stay individual, total reflects merged count, pagination offset slices the merged list, type filter "fee" surfaces trade groups containing fee legs
- [x] 3.2 Add unit test `tests/unit/test_cash_account_merge.py` covering `_merge_legs_into_groups`: settle/fee/tax leg ordering inside `child_legs`, summed amount, settle-date as group date, synthetic id sentinel
- [x] 3.3 `cd services/stock-portfolio-service && pytest tests/unit/ tests/integration/` clean

## 4. Frontend — model + service

- [x] 4.1 In `frontend/src/app/models/portfolio.model.ts`, add `child_legs?: CashTransaction[] | null` to `CashTransaction` and `"trade"` to the `CashTransactionType` union
- [x] 4.2 In `frontend/src/app/services/portfolio.service.ts`, extend the `getCashTransactions` query parameter type with `merge_related?: boolean`

## 5. Frontend — detail page toggle + render

- [x] 5.1 In `components/portfolio/accounts/account-detail.ts`, add `mergeRelated` signal initialized from `localStorage["accounts.merge." + accountId]` (default OFF), persist on toggle, include in query, reset paginator offset to 0 on toggle change
- [x] 5.2 In `account-detail.html`, add a `p-toggleSwitch` (or `p-checkbox` styled as toggle) labeled `合併同筆交易` in the transactions section header alongside `新增交易`
- [x] 5.3 In the list `@for`, when a row has `child_legs` populated render: summed amount + leg-count badge + chevron control. Track expand state per row id in a `Set<number>` signal. Expanded state renders `child_legs` as inline sub-items
- [x] 5.4 Show synthetic `"trade"` type label as `交易` in `typeLabel()` mapping

## 6. Frontend — tests

- [x] 6.1 Extend `account-detail.component.spec.ts` with: toggle defaults OFF, toggle ON triggers refetch with `merge_related=true`, expand chevron reveals child legs, localStorage persists per account id, paginator resets on toggle change
- [x] 6.2 `cd frontend && npm test` clean
- [x] 6.3 `npm run build` clean

## 7. Manual verification

- [ ] 7.1 With `CASH_LEG_ENABLED=true` and backfill done: visit `/portfolio/accounts/1`, default toggle OFF shows existing 5173 rows over many pages
- [ ] 7.2 Flip toggle ON: list collapses to ~2266 virtual rows (2196 trades + 70 dividends), each trade shows summed amount
- [ ] 7.3 Click chevron on a trade row: settle / fee / tax legs appear inline with original amounts
- [ ] 7.4 Reload page: toggle initializes ON (localStorage persisted)
- [ ] 7.5 Open `/portfolio/accounts/2` (different account): toggle initializes OFF (per-account state)
