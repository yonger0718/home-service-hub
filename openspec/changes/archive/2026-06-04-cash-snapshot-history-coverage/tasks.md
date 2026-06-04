## 1. Backend — snapshot cash-range helper

- [x] 1.1 In `app/services/portfolio_snapshot_service.py`, add `refresh_snapshot_cash_range(db: Session, start_date: dt_date, end_date: dt_date) -> None`. For each date in `[start_date, end_date]` inclusive: compute `cash_total = cash_account_service.get_total_balance_in(db, "TWD", asof=date)`; if a `PortfolioSnapshot` row exists for that date, UPDATE only `total_cash_twd` (do NOT touch stock columns); if no row exists AND `cash_total > 0`, INSERT a cash-only row with stock columns zeroed (`total_market_value=0`, `total_cost=0`, `total_unrealized_pnl=0`, `total_dividends=0`, `total_realized_pnl=0`, `portfolio_xirr=None`); if no row exists AND `cash_total == 0`, skip. Single `db.commit()` at end.
- [x] 1.2 Defensive no-op when `end_date < start_date` (log DEBUG, return immediately).
- [x] 1.3 Use the existing skipped-currencies WARN log pattern (same key as `write_today_snapshot`) when `get_total_balance_in` returns non-empty skipped list.
- [x] 1.4 Unit test `tests/unit/test_portfolio_snapshot_service.py`: existing row gets `total_cash_twd` updated and stock columns untouched; missing row + cash > 0 inserts a cash-only row with stock columns = 0; missing row + cash == 0 skips; range of 3 dates produces 3 effects; `end_date < start_date` returns without writes.

## 2. Backend — cash CRUD wires the range helper

- [x] 2.1 In `app/services/cash_account_service.py`, replace the existing `_refresh_today_snapshot(session)` call in `create_manual_cash_transaction` with `refresh_snapshot_cash_range(session, min(txn.txn_date, today), today)`. Preserve the lazy-import pattern and the rollback-on-failure guard from PR #23 (commit 836ed3e).
- [x] 2.2 In `delete_manual_cash_transaction`, capture `deleted_txn_date = row.txn_date` BEFORE `session.delete(row)` and `session.commit()`; after commit, call `refresh_snapshot_cash_range(session, min(deleted_txn_date, today), today)`.
- [x] 2.3 Inline `_refresh_today_snapshot` (now a 1-line wrapper around the range helper for `start == end == today`) or delete it if no callers remain. Keep the rollback-on-failure semantics.
- [x] 2.4 Unit tests `tests/unit/test_cash_account_service.py`: create with today's date refreshes only today (single-date range); create with backdated date refreshes range from `txn_date` to today; delete with backdated date refreshes range using captured `txn_date`; future-dated create clamps end_date to today (so range = today..today); refresh raising rolls back snapshot writes but leaves the cash transaction intact (failure-isolation contract from PR #23 preserved).

## 3. Backend — networth backfill writes cash-only rows

- [x] 3.1 In `app/services/networth_backfill_service.replay_snapshots_range`, inside the `would_skip` branch where `wrote_forward_fill` is False, add: if `total_cash_twd(cur) > 0`, write a cash-only row (mv=0, cost=0, unrealized=0, cumulative_dividends, cumulative_realized, cash). Set `wrote_forward_fill = True` to avoid stale-candidate append.
- [x] 3.2 In the trading-day branch, when `mv == 0 and total_cost == 0` (no held stock at all), still emit a row when cash is non-zero rather than continuing.
- [x] 3.3 In `_main` (or the auto-derivation point), when computing the rebuild-all window: `from_d = min(earliest_stock_date or +inf, earliest_cash_date or +inf)`. `earliest_cash_date = SELECT MIN(txn_date) FROM cash_transaction`. If both are absent, the rebuild is a no-op.
- [x] 3.4 Unit tests `tests/unit/test_networth_backfill_service.py`: cash-only user (no stock) gets snapshot rows on cash txn dates; cash-only period after liquidation (stock_qty all zero, cash > 0) emits rows; union of overlapping stock+cash dates dedups to one row per date; empty ledger is a no-op; dry-run still logs but writes nothing.

## 4. Verification

- [x] 4.1 `cd services/stock-portfolio-service && pytest tests/unit/` clean
- [x] 4.2 `pytest tests/integration/` clean
- [x] 4.3 `cd frontend && npm test` clean (sanity — no frontend change expected, but the dashboard spec must stay green)

## 5. Operational rollout

- [x] 5.1 Deploy backend
- [x] 5.2 Operator runs `python -m app.services.networth_backfill_service --rebuild-all` to fill cash-only-period gaps and recompute the broadened window
- [x] 5.3 Operator verifies dashboard chart shows the cash band over previously-empty periods, and that backdated cash CRUD now updates the chart immediately for the historical range
