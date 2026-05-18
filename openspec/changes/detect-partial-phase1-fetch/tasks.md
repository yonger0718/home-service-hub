## 1. Baseline helper

- [x] 1.1 Add module-level constants to `services/stock-portfolio-service/app/services/networth_backfill_service.py` (near the existing `RETRY_DELAYS_SEC` constant ~line 38): `PARTIAL_FETCH_RATIO = 0.8`, `PARTIAL_FETCH_MIN_BASELINE_DAYS = 10`, `PARTIAL_FETCH_BASELINE_WINDOW_DAYS = 30`, `PARTIAL_FETCH_LOOKBACK_DAYS = 45`.
- [x] 1.2 Add `_recent_row_counts(session, *, source: str, today: dt_date) -> list[int]` that runs the SQL from design D5 and returns the per-date row counts as a plain `list[int]` (order does not matter — caller only computes `statistics.median`; length ≤ `PARTIAL_FETCH_BASELINE_WINDOW_DAYS`).
- [x] 1.3 Add `_is_partial_response(session, *, source: str, date: dt_date, fetched_rows: int) -> bool` that:
  - returns `False` immediately when `fetched_rows == 0` (empty fetches are handled by the existing holiday/empty-fetch branch at lines 289–308, not by this gate);
  - calls `_recent_row_counts`; if the returned list has length < `PARTIAL_FETCH_MIN_BASELINE_DAYS`, emits info log `phase1.partial_check_skipped_cold_start` (fields: `source`, `date`, `baseline_days`) and returns `False`;
  - otherwise computes `ratio = fetched_rows / statistics.median(baseline)`; if `ratio < PARTIAL_FETCH_RATIO`, emits warning log `phase1.partial_fetch_skipped` (fields: `source`, `date`, `fetched_rows`, `baseline_median`, `ratio`) and returns `True`;
  - else returns `False`.

## 2. Wire into Phase 1 upsert path

- [x] 2.1 In `networth_backfill_service.py`, after the existing empty-fetch handling block (the `if not twse_rows and not tpex_rows: ... continue` at lines 289–308) and **before** the combined `market_data_service.upsert_rows(db, [*twse_rows, *tpex_rows])` call at line 311, insert per-source partial guards: for each source where `rows` is non-empty, call `_is_partial_response(db, source=<lit>, date=date, fetched_rows=len(rows))`; if `True`, set that source's `rows` variable to `[]` and append a `BackfillError(date=date, reason=f"{source} partial response, skipped")` to `result.errors`. Use the exact existing source literals `"TWSE"` and `"TPEx"` (matches `price_history.source` column values per `app/models/price_history.py:39`).
- [x] 2.2 After the per-source guards, add `if not twse_rows and not tpex_rows: continue` so the upsert call is skipped when both sources were classified partial (avoids calling `upsert_rows` with an empty list).
- [x] 2.3 Do NOT modify the existing upsert call shape (`market_data_service.upsert_rows(db, [*twse_rows, *tpex_rows])`). The partial-side becomes an empty splat and is a no-op; the non-partial side still persists in the same single transaction.
- [x] 2.4 Confirm the new guards do NOT run when `_fetch_with_retry` returned an empty list — the early-return on `fetched_rows == 0` inside `_is_partial_response` (task 1.3) plus the empty-fetch handler at lines 289–308 (which `continue`s before reaching the upsert block) together guarantee this.

## 3. Unit tests

- [x] 3.1 In `services/stock-portfolio-service/tests/unit/`, create a NEW file `test_networth_backfill_partial_fetch.py` (do not append to existing `test_networth_backfill_service.py` — keep the new gate's tests isolated for easier review).
- [x] 3.2 Test `_recent_row_counts` returns the right list of counts given a seeded `price_history` with 30+ dates per source (verify length cap = `PARTIAL_FETCH_BASELINE_WINDOW_DAYS`, the `date < today` filter, and the per-`source` filter).
- [x] 3.3 Test `_is_partial_response` returns `False` when `fetched_rows == 0` (does not query baseline, does not log).
- [x] 3.4 Test `_is_partial_response` returns `False` when `fetched_rows / median >= 0.8` (e.g. baseline median 1000, fetched 850).
- [x] 3.5 Test `_is_partial_response` returns `True` when `fetched_rows / median < 0.8` (e.g. baseline median 1000, fetched 400) and emits the `phase1.partial_fetch_skipped` warning log (capture via `caplog`, assert fields `source`, `date`, `fetched_rows`, `baseline_median`, `ratio`).
- [x] 3.6 Test cold-start path: baseline length < 10 → returns `False` and emits the `phase1.partial_check_skipped_cold_start` info log.
- [x] 3.7 Test per-source independence: seed asymmetric baselines for TWSE (median 1000) and TPEx (median 5000), call `_is_partial_response` once per source with their own fetched counts, verify each source is evaluated against its own median (TPEx returning 1000 rows = partial even though it equals TWSE baseline).

## 4. Integration test

- [x] 4.1 In `services/stock-portfolio-service/tests/integration/`, create a NEW file `test_networth_backfill_partial_skip.py`.
- [x] 4.2 Seed `price_history` with 30 trading days of TWSE rows (count ~1300 each) and TPEx rows (count ~5300 each), all dated `today - N` for N in 1..30.
- [x] 4.3 Monkey-patch the TWSE and TPEx fetchers used by the Phase 1 driver so that for `today`, TWSE returns 400 rows (partial) and TPEx returns a normal ~5300 rows.
- [x] 4.4 Run the Phase 1 driver for `today`; assert that `price_history` has 0 new TWSE rows for `today`, has ~5300 new TPEx rows for `today`, that one `phase1.partial_fetch_skipped` warning was logged for `source=TWSE`, and that `result.errors` contains exactly one `BackfillError` whose `reason` mentions `TWSE partial`.

## 5. Verification

- [x] 5.1 Run `./.venv/bin/pytest tests/unit/test_networth_backfill_partial_fetch.py tests/integration/test_networth_backfill_partial_skip.py -x` — all pass.
- [x] 5.2 Run full suite `./.venv/bin/pytest` — no regressions, especially the existing `test_networth_backfill_service.py` and `tests/integration/test_post_import_recalc_chain.py`.
- [x] 5.3 `openspec validate detect-partial-phase1-fetch --strict` clean.
