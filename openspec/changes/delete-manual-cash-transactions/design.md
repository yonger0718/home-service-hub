## Context

The cash ledger is a strict invariant: every row that has a `related_transaction_id` is derived from a stock transaction; every row with `related_dividend_id` is derived from a dividend. Deleting such a row directly would break the invariant — the next CRUD sync or rehash would not detect the missing leg and balance computations would drift.

Manual rows (`source=manual`) are different. They own themselves. No upstream entity will resync them. The user must be able to remove them.

The existing `cash_account_service` already implements `sync_transaction_cash_legs` / `delete_transaction_cash_legs` for derived rows; this change adds the symmetric capability for manual rows only.

## Goals / Non-Goals

**Goals:**
- Single, narrow DELETE endpoint that touches one row by id, gated by source
- Server-side enforcement of the manual-only guard (frontend protection is UX, not security)
- Hard delete, no soft-delete column
- Full balance / history / list refresh after delete so user sees the effect immediately

**Non-Goals:**
- Bulk delete (one row at a time keeps audit clear)
- Soft-delete with restore
- Delete on `auto_derive` / `csv_import` / `backfill` rows (use the upstream transaction's CRUD instead)
- Undo

## Decisions

### D1. HTTP method + return shape

**Decision**: `DELETE /api/portfolio/accounts/{account_id}/cash-transactions/{txn_id}` returns `{deleted_id: <int>}` with HTTP 200 (not 204) so the frontend has confirmation payload to log / display.

**Why**: 200 + body makes optimistic UI easier and aligns with the existing POST/PATCH style elsewhere in the codebase.

### D2. Source guard at service layer, not router

**Decision**: the `manual` check lives in `cash_account_service.delete_manual_cash_transaction` and raises `ValueError("not_manual")`. The router catches and maps to 403.

**Why**: keeps the rule with the business logic so the unit test for the service exercises the guard without going through HTTP, and any future caller (e.g., a CLI) gets the same protection.

### D3. Account ownership check

**Decision**: the lookup `SELECT ... WHERE id = txn_id AND account_id = account_id` — a row that exists but belongs to another account returns 404, not 403.

**Why**: avoids leaking the existence of rows on accounts the user does not control. Even though all accounts in this single-user system belong to the user, the pattern stays correct if multi-user lands later.

### D4. Confirmation dialog content

**Decision**: dialog body shows `{type label} {amount with sign} {currency} on {txn_date}{note ? " — " + note : ""}`. Confirm button label: `刪除`. Cancel: `取消`. Severity: `danger`.

**Why**: the visible amount + date is the minimum context for the user to confirm they targeted the right row.

### D5. Refresh after delete

**Decision**: on 200, fire 3 calls in parallel: re-fetch cash transactions (current page), balance history (current window), and parent account summary. UI debounce stays at 300ms for the regular query stream but the explicit refresh skips the debounce.

**Why**: the chart + the TWD total tile + the list must all reflect the new balance before the user looks away.

## Risks / Trade-offs

- **User accidentally deletes a manual deposit they DID make** → Mitigation: confirmation dialog with full row context; no undo, but the manual entry modal lets them re-add identical row (idempotency fingerprint of `(account, date, type, amount, note)` will then accept the re-add because the original is gone)
- **Manual row with the same fingerprint exists in the deleted history** — N/A; rows are not retained after hard delete
- **Concurrent edit / delete race** → Mitigation: standard FastAPI session; if two browser tabs both hit DELETE, second one returns 404 — acceptable

## Migration Plan

No DB changes. Deploy backend + frontend together. Rollback: revert both deploys. No persisted side effects.

## Open Questions

None.
