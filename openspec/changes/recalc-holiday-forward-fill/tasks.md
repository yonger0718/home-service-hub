## 1. Extend `compute_active_dates` with calendar-inclusive mode

- [x] 1.1 Add `include_non_trading: bool = False` parameter to `compute_active_dates(db, from_d, to_d)` in `networth_backfill_service.py`. When `True`, the per-symbol interval expansion uses all calendar dates in `[clipped_start, clipped_end]` instead of only weekdays via `_iter_trading_days`.
- [x] 1.2 Unit test: `include_non_trading=True` over a Fri→Mon held interval returns a 4-date set (Fri/Sat/Sun/Mon).
- [x] 1.3 Unit test: default call (no flag) byte-identical to today's behaviour.
- [x] 1.4 Unit test: closed-position calendar interval still terminates correctly (Sat after Fri SELL closing qty=0 is NOT included).

## 2. Phase 2 forward-fill core

- [x] 2.1 In `replay_snapshots_range`, build the calendar-inclusive active set at entry: `held_calendar = compute_active_dates(db, from_d, to_d, include_non_trading=True)` (when caller provided weekday-only `active_dates`, derive `held_calendar` from the same DB query — design D6).
- [x] 2.2 Add a local `last_trading_mv: Decimal | None = None` and `last_trading_cost: Decimal | None = None`. Seed at entry from the most-recent `portfolio_snapshot` row with `date < from_d AND total_market_value > 0` (design D3).
- [x] 2.3 Inside the per-date loop, when a date is skipped (weekend, holiday, or weekday-active-dates miss) but IS in `held_calendar`, write a forward-fill `portfolio_snapshot` row carrying `last_trading_mv`, `last_trading_cost`, and the current cumulative `dividends` / `realized_pnl` (design D2 + D5). Increment `result.snapshots_written`.
- [x] 2.4 On every real trading-day write, update `last_trading_mv` and `last_trading_cost` from the just-computed values so subsequent forward-fills carry the freshest snapshot.
- [x] 2.5 Forward-fill must NOT fire when `last_trading_mv is None` (no seed available and no real trading day processed yet inside the range) OR when `sum(qty.values()) <= 0` (user holds nothing).
- [x] 2.6 Unit test: Fri+Mon held → Sat/Sun rows carry Fri's MV+cost.
- [x] 2.7 Unit test: 春節 cluster (last trading day before LNY → first after) → every closed-market date inside the cluster gets a row with the pre-LNY MV+cost.
- [x] 2.8 Unit test: SELL closes position on Fri → Sat/Sun get NO forward-fill row.
- [x] 2.9 Unit test: replay starts mid-holiday-cluster (`from_d = Saturday`, prior snapshot in DB) → forward-fill seeds from the DB snapshot.
- [x] 2.10 Unit test: dividend posted on a Saturday inside a held interval → Saturday row's `total_dividends` advances accordingly.

## 3. Stale-row self-heal

- [x] 3.1 In `replay_snapshots_range`, collect every skipped date where the function does NOT write a row into a local `stale_candidates: list[date]`. At end-of-replay, issue ONE bulk `DELETE FROM portfolio_snapshot WHERE date IN (...) AND total_market_value = 0 AND total_cost > 0`.
- [x] 3.2 Add a counter `result.stale_rows_deleted: int = 0` on `SnapshotReplayResult`. Populate from the DELETE row count.
- [x] 3.3 Unit test: pre-seed a `MV=0 cost>0` row on a holiday inside a closed-position range → replay deletes it.
- [x] 3.4 Unit test: pre-seed a `MV=0 cost=0` row on a no-holding date → replay does NOT delete it.
- [x] 3.5 Unit test: 50 stale candidates in range → exactly ONE DELETE round-trip (assert via SQL log / mock).

## 4. Plumbing + status surface

- [x] 4.1 `run_backfill` aggregates `stale_rows_deleted` into `NetworthBackfillResult`.
- [x] 4.2 Orchestrator `_step_networth_backfill` includes `stale_rows_deleted` in `StepResult.detail`.
- [x] 4.3 Unit test: orchestrator passes `stale_rows_deleted` through to `latest_status` payload.

## 5. Integration test (real DB)

- [x] 5.1 In `tests/integration/test_post_import_recalc_chain.py`, add a test that seeds a 2022 long-held position spanning the 春節 2022 cluster + pre-seeds a stale `MV=0 cost=209065.625` row on `2022-01-27`. Triggers the chain. Asserts: stale row deleted, every weekend/holiday in `[2022-01-27, 2022-02-04]` now has a snapshot row carrying the pre-cluster MV, and the running trading-day rows still match the pre-existing computation.

## 6. Docs

- [x] 6.1 Update `services/stock-portfolio-service/README.md` (the active-date optimization paragraph) with a one-paragraph addendum on forward-fill behaviour and `stale_rows_deleted` counter.

## 7. Verification

- [x] 7.1 `cd services/stock-portfolio-service && pytest tests/unit/test_active_date_optimization.py tests/unit/test_networth_backfill_service.py tests/integration/test_post_import_recalc_chain.py -x` — all green (existing + new).
- [x] 7.2 Manual smoke: triggered `POST /api/portfolio/imports/recalc` with `{"start_date":"2022-01-01","end_date":"2026-05-18"}` against the real stock_portfolio_db. Result: `stale_rows_deleted=84`, `snapshots_written=1515`, and `SELECT COUNT(*) FROM portfolio_snapshot WHERE total_market_value=0 AND total_cost>0` returned 0 afterwards. The 春節 cluster dates (e.g. 2022-01-27 → 2022-02-04, 2023-01-18 → 2023-01-25) now carry forward-filled MV+cost matching the pre-cluster trading day.
- [x] 7.3 Manual smoke: `/hub/portfolio` chart on the running stack — confirmed 總市值 series is continuous across the previously broken holiday clusters; the only remaining same-session "drop" was traced to a partial Phase 1 fetch for 2026-05-18, fixed independently by clearing today's `price_history` rows and re-running the chain. Tracking the under-threshold detection improvement as a follow-up (see PR description).
- [x] 7.4 `openspec validate recalc-holiday-forward-fill --strict` passes.
