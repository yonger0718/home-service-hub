## 1. Holding-interval helper

- [x] 1.1 Add `compute_active_dates(db, from_d, to_d) -> set[date]` helper in `services/stock-portfolio-service/app/services/networth_backfill_service.py` (or a new `holding_intervals.py` if it grows beyond ~80 LoC). Walks `transactions` (signed quantity) + `dividends.stock_dividend_shares` chronologically per symbol. Returns union of per-symbol holding intervals clipped to `[from_d, to_d]` and restricted to weekdays.
- [x] 1.2 Unit test: closed position yields exact `[buy_date, sell_date]` weekday set, no later dates.
- [x] 1.3 Unit test: open position extends to `to_d`.
- [x] 1.4 Unit test: same-day BUY+SELL yields single date.
- [x] 1.5 Unit test: multi-symbol overlap unions without double-counting.
- [x] 1.6 Unit test: stock-dividend share grant on the ex-date is included.
- [x] 1.7 Unit test: weekend dates inside an interval are excluded.
- [x] 1.8 Unit test: empty portfolio returns empty set.

## 2. Phase 1 filter

- [x] 2.1 Add optional `active_dates: set[date] | None = None` parameter to `backfill_prices_range` in `networth_backfill_service.py`. Inside the per-date loop, before the throttle sleep, `continue` when `active_dates is not None and date not in active_dates`.
- [x] 2.2 Add `dates_inactive: int = 0` counter to `PriceBackfillResult`. Increment on every inactive-date skip. Do NOT increment `dates_skipped`.
- [x] 2.3 Unit test: with `active_dates={d1}` over a 5-weekday range, only `d1` triggers HTTP fetch; the other four increment `dates_inactive` and produce no `price_history` writes.
- [x] 2.4 Unit test: with `active_dates=None`, behaviour is byte-identical to today (cache-skip via `_existing_price_dates` still applies; `dates_inactive == 0`).

## 3. Phase 2 filter

- [x] 3.1 Add optional `active_dates: set[date] | None = None` parameter to `replay_snapshots_range` in `networth_backfill_service.py`. Inside the per-date loop, `continue` when `active_dates is not None and date not in active_dates`. Do NOT write a `portfolio_snapshot` row on skipped dates.
- [x] 3.2 Add `dates_inactive: int = 0` counter to `SnapshotReplayResult`. Increment on every inactive-date skip.
- [x] 3.3 Unit test: with `active_dates={d1}` over a 5-date range, only `d1` produces a `portfolio_snapshot` row; the other four increment `dates_inactive`.
- [x] 3.4 Unit test: pre-existing `portfolio_snapshot` rows on inactive dates are NOT deleted by replay.
- [x] 3.5 Unit test: with `active_dates=None`, replay writes a row for every weekday in range exactly as today.

## 4. Wire active-date filter through `run_backfill`

- [x] 4.1 Extend `run_backfill(db, from_d, to_d, phase, *, active_dates=None)` in `networth_backfill_service.py` to forward `active_dates` into both phase functions.
- [x] 4.2 Aggregate the per-phase `dates_inactive` into the `BackfillResult` returned to the caller.

## 5. Orchestrator integration

- [x] 5.1 In `post_import_orchestrator._step_networth_backfill`, compute `active_dates = compute_active_dates(db, recalc_from, recalc_to)` once at the top of the step. Pass into `networth_backfill_service.run_backfill(..., active_dates=active_dates)`.
- [x] 5.2 Include `dates_inactive` in the `StepResult.detail` dict so `latest_status` surfaces the optimization win to the UI.
- [x] 5.3 If `active_dates` is empty, short-circuit: return `StepResult(name="networth_backfill", status="ok", detail={"dates_processed": 0, "dates_inactive": <weekday count in range>, "snapshots_written": 0, "errors": []})` without invoking either phase.
- [x] 5.4 Unit test: orchestrator passes the computed `active_dates` set into `run_backfill` (mock the service, assert kwargs).
- [x] 5.5 Unit test: empty `active_dates` causes the networth step to short-circuit without calling Phase 1 or Phase 2.

## 6. Integration test (real DB)

- [x] 6.1 Add integration test in `tests/integration/test_post_import_recalc_chain.py` that seeds a 2022 BUY + 2022 SELL fully closing the position, triggers the chain with `recalc_from=2022-01-03, recalc_to=2026-05-18`, and asserts only the 3 in-interval weekdays produce snapshot rows. Mock TWSE/TPEx fetchers to assert they were called exactly 3 times.
- [x] 6.2 Add integration test for an open position: 2024 BUY, no SELL — asserts every weekday from BUY to today is in the active set and produces snapshot rows.

## 7. Docs

- [x] 7.1 Update `services/stock-portfolio-service/README.md` (or the post-import-recalc design notes section) with a one-paragraph note on the active-date optimization: what it does, when it kicks in, how to interpret `dates_inactive` in recalc status.

## 8. Verification

- [x] 8.1 `cd services/stock-portfolio-service && pytest tests/unit/test_networth_backfill_service.py tests/unit/test_post_import_orchestrator.py tests/integration/test_post_import_recalc_chain.py` — all green.
- [x] 8.2 Manual smoke: import a CSV containing a single 2022 BUY+SELL that fully closes the position. Watch `/api/portfolio/imports/recalc/status` — `dates_inactive` should equal weekdays-in-range minus 3 (or 2 if same-week BUY+SELL). Wall time under 10s.
- [x] 8.3 Manual smoke: import a CSV containing an open 2024 position. Watch the same endpoint — active dates should equal weekdays from BUY to today; wall time matches today's behaviour over that smaller window.
- [x] 8.4 `openspec validate optimize-recalc-active-dates-only --strict` passes.
