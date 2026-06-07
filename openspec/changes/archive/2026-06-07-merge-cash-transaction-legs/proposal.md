## Why

Each BUY/SELL transaction with `CASH_LEG_ENABLED` emits 3 cash rows (settle + fee + tax). The cash list view on the account detail page becomes noisy — every trade triples the row count, hiding the user's actual cash movement. Users want to see one row per trade by default with the ability to expand into legs for audit. A client-side group at render time is unworkable: pagination counts and offsets diverge from the real DB row count, breaking page boundaries.

## What Changes

- Add optional `merge_related=true` query param to `GET /api/portfolio/accounts/{id}/cash-transactions` that collapses rows sharing a `related_transaction_id` into one synthetic group per trade
- Extend `CashTransactionOut` schema with optional `child_legs: list[CashTransaction]` (populated only when merged and the row is a group)
- Synthetic group row identity: `id = -1 * related_transaction_id` (negative sentinel, never clashes with real PK), `type = "trade"` (new enum value, frontend-only label — no DB enum addition required since synthetic rows are never persisted), `amount = sum(legs.amount)`, `txn_date = group.settle.txn_date`, `currency = legs[0].currency`, `source = legs[0].source`, `note = null`, `related_transaction_id = original`, `child_legs = [...]`
- Pagination total when merged = `count(DISTINCT related_transaction_id WHERE related_transaction_id IS NOT NULL) + count(WHERE related_transaction_id IS NULL)` over the filtered set; offset / limit slice the merged virtual list
- Dividend cash rows (`related_dividend_id IS NOT NULL`) and manual rows (both relation FKs NULL) remain individual regardless of `merge_related`
- Frontend account-detail page adds a toggle button `合併同筆交易` that persists per-account in `localStorage[accounts.merge.<id>]`, default OFF on first visit; sends `merge_related` as query param
- Frontend renders grouped rows with a chevron control that expands inline to show `child_legs` (no extra API call)

## Capabilities

### New Capabilities

(none — extends existing capabilities introduced by `add-broker-cash-accounts`)

### Modified Capabilities

- `stock-portfolio-cash-accounts`: GET cash-transactions endpoint adds `merge_related` query param + group rollup semantics + `child_legs` field on response items
- `frontend-portfolio-accounts`: account detail page adds merge toggle with localStorage persistence and inline expandable group rows

## Impact

**Backend** (`services/stock-portfolio-service/`):
- `app/schemas/cash_account.py`: add `child_legs: list[CashTransactionOut] | None` to `CashTransactionOut`; add `"trade"` synthetic to `CashTxnType` schema enum only (NOT the DB enum)
- `app/services/cash_account_service.py`: extend the cash-transactions list query with `merge_related` flag; new helper `_merge_legs_into_groups(rows) -> list[CashTransactionOut]` that groups by `related_transaction_id`
- `app/routers/accounts.py`: new `merge_related: bool = False` query param on `GET /{account_id}/cash-transactions`
- Tests: extend `tests/integration/test_accounts_endpoints.py` with merged-mode shape + pagination boundary cases

**Frontend** (`frontend/src/app/`):
- `models/portfolio.model.ts`: extend `CashTransaction` with optional `child_legs`; add `"trade"` to `CashTransactionType` union
- `services/portfolio.service.ts`: extend `getCashTransactions` query param type with `merge_related?: boolean`
- `components/portfolio/accounts/account-detail.{ts,html,scss}`: toggle button, localStorage persistence per account id, expand/collapse chevron per group row, summed amount + leg-count badge
- Tests: extend `account-detail.component.spec.ts` with toggle behavior + expand

**No DB migration. No schema change in `cash_transaction` table.**
