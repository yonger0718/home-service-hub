## Context

The `add-broker-cash-accounts` change ships a 3-leg cash model: every BUY/SELL emits `settle + fee + tax` rows linked by `related_transaction_id`. This is correct for audit + tax reporting but creates visual noise on the account-detail cash list (2196 trades → 5173 rows in production). Users want one row per trade by default while still being able to drill into the legs.

A pure client-side group fails because pagination is server-driven: a page-25 boundary that lands mid-group produces partial groups; merged totals never agree with paginator counts. The fix has to be in the same query that drives pagination.

## Goals / Non-Goals

**Goals:**
- Single coherent paginator: when merge is on, `total` and `offset/limit` operate on the merged virtual list
- Lossless drill-down: every merged group exposes its original legs for inspection
- Opt-in: behavior unchanged when `merge_related` absent or false
- No schema change, no migration, no DB enum addition

**Non-Goals:**
- Merging dividend cash rows (single-leg, no value)
- Merging across `related_transaction_id` (e.g., grouping by trade_date)
- Edit / delete on the synthetic group row (users still edit the underlying transaction via the transactions page)
- Server-side caching of merged results

## Decisions

### D1. Query-time grouping over client-side

**Decision**: backend computes the merged list inside `cash_account_service.get_cash_transactions(..., merge_related=True)`; pagination operates on the merged result.

**Why**: pagination + filtering + sorting must align with the row count the user sees. Splitting that across server and client guarantees off-by-one bugs at page boundaries.

**Alternative considered**: client-side grouping on each page → rejected (paginator desync).

### D2. Synthetic row representation in response

**Decision**: merged group is one `CashTransactionOut` with:
- `id = -1 * related_transaction_id` (negative sentinel; PK is always positive serial — no clash risk)
- `type = "trade"` (new value added to the schema-level enum only, NOT the DB enum)
- `amount = sum(legs.amount)`
- `txn_date = settle.txn_date` (settle is always present per current emission contract)
- `currency = legs[0].currency` (all legs share currency by invariant)
- `source = legs[0].source` (all legs share source by invariant — `auto_derive` or `csv_import`)
- `note = None`
- `related_transaction_id = original`
- `child_legs = [leg, leg, leg]` ordered by type rank (settle → fee → tax)

**Why**: same shape as a real row so the frontend list renderer needs no branching; `child_legs` opt-in carries audit detail; negative id is unambiguous to the client.

**Alternative considered**: separate `CashGroupOut` response type → rejected (forces type unions in TS, duplicates renderer code).

### D3. Pagination math

**Decision**: when `merge_related=true`, compute the full filtered list of "virtual rows" first, then slice.

```
virtual_rows =
  [group(rt_id) for rt_id in distinct(filtered.related_transaction_id WHERE NOT NULL)] +
  [row for row in filtered WHERE row.related_transaction_id IS NULL]
sort virtual_rows by query.sort
total = len(virtual_rows)
items = virtual_rows[offset:offset+limit]
```

**Why**: pagination correctness requires `total` to match the virtual list, not the underlying row count.

**Cost analysis**: a single account today has ≤5173 rows; even at 10x growth the eager fetch + group is sub-50ms. If this becomes a bottleneck, the next iteration adds a materialized count query.

**Alternative considered**: SQL window function to compute groups + paginate in one query → rejected for now (PostgreSQL-specific, harder to read, premature optimization at current scale).

### D4. Filter + sort semantics with merge on

**Type filter**: applies to the original `cash_transaction.type` values. A group is included if ANY leg matches the filter (so filtering "fee" still surfaces the trade group containing a fee leg). Synthetic `"trade"` is not selectable in the type filter (it's a render label, not a real type).

**Date range filter**: applies to `txn_date` of the underlying rows; if any leg matches, the group is included.

**Sort**: group's sort key uses settle leg's value (`txn_date`, `created_at`). Sort by `amount` uses summed amount.

**Why**: matches user intent — they ask "show me fee impact" and the trade groups containing fees stay visible, expandable.

### D5. Frontend toggle state

**Decision**: `localStorage["accounts.merge." + account_id]` stores `"1"` or `"0"`; default OFF on first session per account; toggle sends `merge_related=true|false` as the query param and re-fetches.

**Why**: per-account because users may have one account with hundreds of trades and another with mostly deposits — the right default differs.

### D6. No DB enum change

The synthetic `"trade"` value lives only in the response schema's enum and the frontend `CashTransactionType` union. The database `cash_txn_type` enum stays as defined in `add-broker-cash-accounts`. Synthetic rows are never persisted.

## Risks / Trade-offs

- **Eager fetch + group cost** → Mitigation: bounded by single account size; current production peak ~5173 rows. Re-evaluate if growth >50k.
- **Page count changes when toggle flips** → Mitigation: documented in the UI tooltip; paginator naturally resets to page 1 on toggle.
- **Synthetic id collides with real id if someone treats it as a PK** → Mitigation: negative-id contract documented in schema docstring; frontend list uses `id` only for `trackBy`, never for API calls.
- **Type filter "fee" with merge on shows trade groups, possibly confusing** → Mitigation: filter chip in UI explicitly says `含合併同筆交易` when merge is on.

## Migration Plan

No DB changes. Deploy backend + frontend together; toggle defaults to OFF so existing UX is unchanged. Rollback: revert both deploys; localStorage key is harmless.

## Open Questions

None — all 6 decisions locked in the brainstorm.
