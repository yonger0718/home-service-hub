## Why

PR #23 (`add-cash-to-networth`) wired cash balances into the networth snapshot/chart, but Codex review flagged two coverage gaps that ship stale or missing cash history:

1. `cash_account_service._refresh_today_snapshot` only rewrites today's snapshot row after a cash CRUD. A backdated create/delete (e.g. correcting a 2025 deposit) leaves every snapshot row in `[txn_date, yesterday]` with the old `total_cash_twd`. The chart shows stale historical cash for that range until the operator runs `--rebuild-all`.
2. `networth_backfill_service` only writes snapshot rows for dates where stock activity already produced one. Pure cash periods — before the first stock buy, after a full liquidation, or accounts that hold only cash — get no snapshot row at all, so `total_cash_twd` history is permanently incomplete for those windows.

Both issues silently understate (or misstate) historical net worth without the operator noticing.

## What Changes

- Extract a helper `refresh_snapshot_range(session, start_date, end_date)` in `portfolio_snapshot_service` that walks the inclusive date range and upserts each `portfolio_snapshot` row using existing `write_snapshot_for_date` semantics
- Replace the `_refresh_today_snapshot` call in `cash_account_service.create_manual_cash_transaction` with `refresh_snapshot_range(session, min(txn.txn_date, today), today)`
- Replace the equivalent call in `cash_account_service.delete_manual_cash_transaction` with `refresh_snapshot_range(session, min(deleted_row.txn_date, today), today)` (captured BEFORE delete commit)
- Extend `networth_backfill_service` snapshot-date enumeration to UNION (a) existing stock-activity dates and (b) distinct dates from `cash_transaction.txn_date` for any account; when no stock activity exists at all, use the earliest cash txn date as window start
- Keep the existing FX-as-of + USD-pivot + skipped-currencies semantics unchanged
- Unit tests covering both range-replay and cash-only enumeration paths

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-cash-accounts`: CRUD writes now replay snapshots across the affected date range, not just today
- `stock-portfolio-networth-backfill`: snapshot-date enumeration now covers cash-only periods (no stock activity required)

## Impact

**Backend** (`services/stock-portfolio-service/`):
- `app/services/portfolio_snapshot_service.py`: new `refresh_snapshot_range(session, start_date, end_date)` helper
- `app/services/cash_account_service.py`: `create_manual_cash_transaction` + `delete_manual_cash_transaction` switch from `_refresh_today_snapshot` to `refresh_snapshot_range`; the lazy-import circular-import workaround already in place stays
- `app/services/networth_backfill_service.py`: snapshot-date set widens to include `cash_transaction.txn_date` distinct values; window-start fallback uses earliest cash txn when no stock activity exists
- Tests: extend `tests/unit/test_cash_account_service.py` (backdated CRUD replays range), `tests/unit/test_networth_backfill_service.py` (cash-only periods get snapshot rows)

**Frontend**: none.

**Migration**: none (column added in PR #23).

**Rollout**:
- Deploy backend
- Operator re-runs `python -m app.services.networth_backfill_service --rebuild-all` to fill cash-only-period gaps in existing history
- No feature flag (behaviour is strictly more correct; bad case = slightly slower CRUD on backdated edits)
