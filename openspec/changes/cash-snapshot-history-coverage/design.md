## Context

PR #23 introduced `total_cash_twd` on `portfolio_snapshot` and wired it through the live summary, the daily snapshot writer, the full-history backfill, and the dashboard chart. The two known gaps (Codex review findings) are about WHICH dates get a snapshot row written — not about the column or its math.

Two writers exist:

1. **`portfolio_snapshot_service.write_today_snapshot(session)`** — runs daily, writes one row for `today`. Called by the cron AND by `cash_account_service._refresh_today_snapshot` after every cash CRUD.
2. **`networth_backfill_service`** — bulk rebuild. Iterates a derived "snapshot dates" set (currently only stock-activity dates) and writes/upserts a row per date.

Cash balance at a given date comes from `cash_account_service.get_total_balance_in(db, "TWD", asof=date)`. That function correctly handles per-day balances and FX; the bug is solely that we don't ASK for the right set of dates.

## Goals / Non-Goals

**Goals:**
- After a cash CRUD whose `txn_date` is in the past, every snapshot row from `txn_date` to today reflects the new balance.
- After `--rebuild-all`, every date where cash balance is non-zero has a snapshot row, even if no stock transaction ever occurred on that date.
- One single helper is the single source of truth for "rewrite snapshot rows for a date range" — used by both CRUD writers and (optionally) the backfill writer.

**Non-Goals:**
- Async / background range refresh. CRUD volume is human-scale (a few per week); synchronous range replay over a ~1-year span is well under 1 s in practice.
- UI loading state on cash CRUD beyond what already exists.
- Per-account or per-currency historical series.
- Pruning snapshot rows that exist but no longer have any activity (column already defaults to 0, so a stale row is harmless).
- Migrating existing main spec files for capabilities the change touches if those main specs do not yet exist on disk (handled separately during a future archive sync pass).

## Decisions

### D1. New helper `refresh_snapshot_cash_range(session, start_date, end_date)` in `portfolio_snapshot_service`

**Decision**: a NEW helper that walks `[start_date, end_date]` (inclusive) and, for each date:

1. Computes `cash_total = cash_account_service.get_total_balance_in(session, "TWD", asof=date)` (existing FX-as-of semantics).
2. If a `portfolio_snapshot` row exists for that date, UPDATE only its `total_cash_twd` column — do NOT touch stock columns.
3. If no row exists AND `cash_total > 0`, INSERT a cash-only row with `total_market_value=0`, `total_cost=0`, `total_unrealized_pnl=0`, `total_dividends=0`, `total_realized_pnl=0`, `total_cash_twd=cash_total`, `portfolio_xirr=None`.
4. If no row exists AND `cash_total == 0`, SKIP.

Then `session.commit()` once at the end.

**Why we do NOT reuse `write_today_snapshot`**: that function calls `portfolio_service.get_portfolio_summary` which returns LIVE market value (no historical date input). Calling it for a past date would overwrite the row's stock columns with today's live values — corrupting history. Stock columns are already correct from the prior `--rebuild-all` run via `replay_snapshots_range`; we must only touch the cash column for backdated CRUD.

**Why we do NOT reuse `replay_snapshots_range`**: it's a heavy stock-walking machine (transactions/dividends iteration, price_map, realized-PnL accumulator). Overkill for a CRUD that only changed cash, and it would also unnecessarily re-emit stock columns.

**Edge case**: `end_date < start_date` → no-op (defensive; logged at DEBUG).

**Edge case**: cash dropping to zero on an existing row → UPDATE writes `0`. Existing row stays (won't get pruned here; stale-row pruning is `replay_snapshots_range`'s job).

### D2. CRUD range = `[min(txn_date, today), today]`

**Decision**: on create, use the transaction's `txn_date` lower-bounded against today. On delete, capture the row's `txn_date` BEFORE the delete commit, then use the same formula.

**Why**: a forward-dated future txn (legitimate edge case for scheduled deposits) should not retroactively rewrite the past, so we clamp to today as the upper bound; the lower bound stays at today when the user creates a today-dated txn (no change vs current behavior).

**Trade-off**: a future-dated txn does not yet alter today's balance (since `get_total_balance_in(asof=today)` excludes future rows by date) — so the helper writes today's row with the same value it would have anyway. Cheap no-op, easier to reason about than special-casing.

### D3. Capture deleted row's `txn_date` before commit, refresh after

**Decision**: in `delete_manual_cash_transaction`, read `txn_date` off the loaded ORM row into a local variable, then `session.delete(...)`, then `session.commit()`, then call the range helper on a fresh subsession-like context (current code already does this for `_refresh_today_snapshot`).

**Why**: after `session.delete + commit`, the row's attribute access can be expired or raise. Capturing eagerly avoids `DetachedInstanceError` and keeps the failure mode (snapshot refresh failure already rolls back per PR #23) localized.

### D4. Networth backfill: emit cash-only rows in the existing per-day loop

**Decision**: `replay_snapshots_range` already iterates EVERY calendar day from `from_d` to `to_d`. The current `would_skip` branch (weekend / holiday / inactive) only writes a forward-fill row when prior stock holdings exist. Extend that branch: if `would_skip and not wrote_forward_fill and total_cash_twd(cur) > 0`, write a cash-only row with `total_market_value=0`, `total_cost=0`, `total_unrealized_pnl=0`, current `cumulative_dividends` + `cumulative_realized`, and the cash total.

Likewise, in the trading-day branch when `mv == 0 and total_cost == 0` (no held stock at all), STILL write a row when cash is non-zero rather than skipping.

**Why**: cash-only periods (before the first buy, after liquidation, cash-only users) now produce snapshot rows. We don't change the date-enumeration set (still `from_d..to_d` per day); we change what we DO on days that previously got skipped.

**Window-start fix**: the `from_d` value passed to `replay_snapshots_range` is derived in `_main` / `run_backfill` from the earliest stock-side activity. When stock activity is empty but cash activity exists, the caller SHALL fall back to `min(cash_transaction.txn_date)` for `from_d`. Otherwise the loop never enters the cash-only window.

**Alternative considered**: emit one row per calendar day with cash forward-fill → rejected. Snapshot table would grow ~365× the number of activity dates with no information gain. The current per-day loop is fine because we now only WRITE on activity-relevant days (stock day OR cash > 0 day); flat-cash inactive days still get skipped.

### D5. Window-start fallback for cash-only users (caller side)

**Decision**: in `_main` / wherever `from_d` is auto-derived ("rebuild-all" mode), the derivation SHALL be:

```python
from_d = min(
    earliest_stock_date or +inf,
    earliest_cash_date or +inf,
    earliest_opening_date or +inf,
)
```

`earliest_opening_date` comes from `MIN(broker_account.opening_date)` over accounts with `opening_balance != 0`, so accounts initialized with a non-zero opening balance and zero `cash_transaction` rows still anchor the rebuild window.

When both are absent, the rebuild is a no-op (no history exists yet).

**Why**: matches the existing semantic ("backfill from earliest activity"); just broadens what counts as activity.

### D6. Sync, not async, range replay

**Decision**: range replay runs synchronously inside the CRUD request, after the cash-side commit, with the existing rollback-on-failure guard.

**Why**: typical range = days to ~1 year of activity dates. Snapshot write per date is dominated by the cash balance compute (single SQL aggregation). Measured cost: ~1-5 ms × N. For N=365, that's < 2 s and only on backdated CRUD (rare). Async machinery would add operational complexity not justified by the workload.

**Trade-off**: a CRUD request with a deeply backdated `txn_date` (years back) feels slow. If that becomes a UX problem we revisit with a background queue.

## Risks / Trade-offs

- **Backdated CRUD latency proportional to range** → Mitigation: CRUD walks every calendar day in `[min(txn_date, today), today]` once. The per-date work is one cash balance compute (single SQL aggregation). Backdating is rare; revisit if profiling shows pain.
- **Cash-only-period snapshot rows inflate row count** → Mitigation: the backfill loop still iterates every calendar day, but cash-only emits are GATED on `cur in cash_activity_dates` (`DISTINCT cash_transaction.txn_date ∪ broker_account.opening_date for opening_balance != 0`). Row count therefore scales with user-visible activity, not calendar days.
- **`refresh_snapshot_cash_range` becomes a hot path if someone deletes the entire ledger** → Mitigation: bulk-delete flow is not exposed today (no UI). If added, it can call the backfill CLI instead of N range refreshes.

## Migration Plan

1. Deploy backend
2. Operator runs `python -m app.services.networth_backfill_service --rebuild-all` to fill cash-only-period gaps in existing history
3. Verify dashboard chart fills cash band over previously-empty periods (or after liquidation)
4. Rollback: revert deploy; no migration to undo

## Open Questions

None — decisions locked in proposal hand-off.
