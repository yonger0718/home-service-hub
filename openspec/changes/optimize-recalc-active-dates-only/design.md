## Context

Post-import recalc chain runs three steps: `symbol_map_backfill`, `dividend_auto_record`, `networth_backfill` (`post_import_orchestrator.py:300-314`). The networth step is two phases:

1. **Phase 1 — `backfill_prices_range`** (`networth_backfill_service.py:124`): iterates `_iter_trading_days(from, to)`, fetches whole-market TWSE + TPEx for each weekday not already cached in `price_history`, throttles ~1.5s between dates.
2. **Phase 2 — `replay_snapshots_range`** (`networth_backfill_service.py:255`): walks the same date range in memory, recomputes holdings + market value per date from `transactions` + `dividends` + `price_history`, upserts `portfolio_snapshot`.

`recalc_from` is set by the importer to `min(new tx.trade_date)` of the just-imported batch. For a CSV containing a single 2022 transaction, this expands the range to ~1100 weekdays even when 99% of those weekdays carry zero holdings. Cold first-run wall time ≈ 30min, dominated entirely by Phase 1 HTTP fetches.

Constraints:
- `price_history` is shared across all users / historical snapshots — must NOT drop cached rows.
- `portfolio_snapshot` rows on non-active dates would be all-zero, never read in the current UI.
- Existing scenarios for the spec (`stock-portfolio-networth-backfill/spec.md`) cover idempotent re-run, weekend skip, throttle gap, per-date failure isolation — all must keep passing.

## Goals / Non-Goals

**Goals:**
- Skip Phase 1 HTTP fetch on weekdays where the user held zero shares of every symbol.
- Skip Phase 2 snapshot writes on weekdays where the user held zero shares of every symbol.
- Keep SELL dates active so the SELL-day snapshot + realized P&L math (future feature) are unaffected.
- Single-pass active-date computation at chain entry; both phases consume the same set.
- Zero schema change. Zero API surface change.

**Non-Goals:**
- Per-symbol price fetch (would change whole-market endpoint to per-symbol — separate refactor, evaluated as option C/F earlier and rejected).
- Holiday calendar caching (option A, separate spec).
- Realized P&L event table (separate spec, future).
- Throttle tuning (option D, NO-GO).
- Networth chart UX changes — frontend forward-fills missing dates already, unchanged here.

## Decisions

### D1: Active-date set computed once per chain run

Compute `active_dates` at the top of `run_chain` (`post_import_orchestrator.py:280`), pass into both `_step_networth_backfill` → `networth_backfill_service.run_backfill` → both phase functions.

**Rationale:** Both phases consume the same set; a single computation avoids drift and lets the orchestrator log the active-date count once.

**Alternative:** compute inside `backfill_prices_range` / `replay_snapshots_range` independently. Rejected — duplicated SQL, two log lines, easier to drift.

### D2: Algorithm — per-symbol holding-interval union

For every symbol the user ever traded, walk `transactions` (signed `quantity`: BUY positive, SELL negative) + `dividends.stock_dividend_shares` in chronological order. Maintain running qty. An interval `[open_date, ...]` opens when qty crosses 0→positive. An interval closes at `[..., close_date]` when qty returns to exactly 0 (close_date = SELL trade_date inclusive). If qty never returns to 0, the interval stays open and closes at `recalc_to`.

Final `active_dates = ⋃ intervals ∩ trading_weekdays ∩ [recalc_from, recalc_to]`.

**Rationale:** Captures every day the user held ≥1 share. SELL-closing dates remain in the set (per-interval close_date is inclusive). Multi-buy-multi-sell sequences naturally union. Stock dividends (share grants) flow through the same signed-qty walk.

**Edge cases handled:**
- BUY+SELL same day → 1 active date.
- Multiple symbols overlap → union, no double-count.
- Currently-open position → interval extends to `recalc_to`.
- Stock-dividend share grant on a no-trading-day → qty change date is included as active (matches Phase 2's existing dividend-aware holdings math).
- Negative qty from short sell (融券) — treated as held (interval stays open) since the position has market value exposure.

### D3: Phase 1 skip semantics

Inside `backfill_prices_range` loop, before the throttle sleep, check `if active_dates is not None and date not in active_dates: continue` (does NOT increment `dates_skipped` — that counter means "holiday", which is a different signal).

Add a new counter `dates_inactive` to `PriceBackfillResult` so the orchestrator can report the optimization win in `latest_status`.

**Rationale:** Preserves holiday-skip semantics (`dates_skipped` still means TWSE+TPEx both returned empty after a real fetch). Adds an orthogonal signal users can read.

### D4: Phase 2 skip semantics

Inside `replay_snapshots_range` loop, before the per-date compute, check `if active_dates is not None and date not in active_dates: continue`. Do NOT write a `portfolio_snapshot` row on skipped dates.

`SnapshotReplayResult` gains `dates_inactive` counter symmetrically.

**Rationale:** A snapshot row with `total_market_value = 0` provides no information; absence is equivalent. Frontend net-worth chart already forward-fills gaps, so visual continuity is preserved.

### D5: Re-runs do not orphan old zero-rows

If a user previously ran the chain without this optimization, `portfolio_snapshot` may contain all-zero rows for past non-active dates. We do NOT delete those rows. Frontend treats `total_market_value = 0` as a real value, and removing them is a separate cleanup concern (out of scope).

**Alternative:** on each chain run, DELETE snapshot rows that fall outside `active_dates` in the range. Rejected — destructive, mixes optimization with cleanup, risk of nuking legitimate zero-net-worth snapshots (e.g., user genuinely had no holdings on day X but wants the row).

### D6: Feature flag NOT needed

The feature-flag gate already exists at `post_import_orchestrator.is_enabled()` for the chain itself. The active-date filter is a pure perf optimization with no behaviour change for active dates, so a second flag would be overhead.

**Alternative:** ship behind `RECALC_ACTIVE_DATES_FILTER=true`. Rejected — adds env var sprawl, no scenario where you'd want chain on but filter off.

## Risks / Trade-offs

- **Risk: interval computation undercounts active dates** → user's currently-held symbol skipped on legitimate trading days → stale snapshot or missing price row. **Mitigation:** unit tests for open positions, closed positions, multi-symbol overlap, same-day BUY+SELL, stock-dividend grants, short (negative qty) positions. Integration test in `tests/integration/test_post_import_recalc_chain.py` exercises real DB fixtures.

- **Risk: interval computation overcounts** → days inside the original range stay active that needn't → no correctness issue, just less speedup. Acceptable.

- **Risk: skipped dates that LATER become active (user back-imports a missing 2022 SELL)** → Phase 1 / Phase 2 must re-fetch / re-write on next chain run. **Mitigation:** the active-date set is recomputed every chain run from current DB state, so back-imports flow through automatically on the next trigger.

- **Risk: `portfolio_snapshot` rows missing on dates the UI explicitly queries** → chart shows gaps. **Mitigation:** existing frontend handles missing dates (the snapshot table is sparse already on holidays). Verified before merge.

- **Trade-off: complexity vs. speedup.** ~150 LoC for the helper + plumbing, in exchange for 10–100× cold-import wall-time reduction on closed-position scenarios. Worth it.

## Migration Plan

No data migration. Behaviour change is purely additive — `active_dates=None` (default) preserves current full-range behaviour, so existing call sites that don't opt in continue to work. The orchestrator opts in unconditionally.

Rollback: revert the PR. No data cleanup needed (no rows added/removed by this change itself).

## Open Questions

- _(none — algorithm + integration points are nailed down by D1–D6.)_
