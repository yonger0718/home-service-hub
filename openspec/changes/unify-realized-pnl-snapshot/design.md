## Context

Two realized-PnL engines coexist:

1. `services/stock-portfolio-service/app/services/realized_pnl_service.py` — canonical. Maintains per-symbol `{LONG, SHORT}` FIFO pools, handles position_side, applies Taiwan day-trade 0.15% tax rule, emits `RealizedEvent` per SELL/SHORT-close. Powers `/api/portfolio/realized-pnl`.
2. `services/stock-portfolio-service/app/services/networth_backfill_service.py:547-576` — inline replay loop inside `_replay_snapshots`. Predates position_side. Treats every BUY as long-open, every SELL as long-close via running average cost. Powers `portfolio_snapshot.total_realized_pnl`.

CSV-import post-chain (`post_import_orchestrator` step C) calls the inline loop, so every import overwrites `portfolio_snapshot.total_realized_pnl` with wrong values whenever Cathay 融券 / 沖賣 SHORT rows are present. `/api/portfolio/realized-pnl` and `portfolio_snapshot` then disagree.

Replay loop also has secondary bugs unrelated to SHORT: oversell (`tx_qty > qty[sym]`) loses the excess and still subtracts full fee/tax; no day-trade tax rule; corporate-action splits not applied (acknowledged limitation, out of scope here).

## Goals / Non-Goals

**Goals:**
- One source of truth for realized PnL. Snapshot and `/realized-pnl` endpoint MUST agree on every range.
- `position_side` honored in `portfolio_snapshot` qty/cost/MV roll-up.
- Fix oversell fee/tax pro-rating + pick up day-trade 0.15% tax automatically.
- One-shot rebuild script overwrites existing stale snapshot rows.
- No schema change. No API contract change.

**Non-Goals:**
- Corporate-action split factor application during replay (separate change).
- New per-symbol detail table (`networth_detail` does not exist; not introducing one).
- Changing `RealizedEvent` schema or `/realized-pnl` response shape.
- Cathay CSV parsing or import-time guards.

## Decisions

### Decision 1 — Delegate realized PnL to `iter_realized_events`

Replace the inline realized accumulator (`networth_backfill_service.py:567-575`) with a call to `realized_pnl_service.iter_realized_events(transactions)` BEFORE the date walk. Bucket events by `event.trade_date.date()`. During the date walk, advance a `cumulative_realized` total by summing buckets where `bucket_date <= cur`.

**Why:** `iter_realized_events` already encodes the correct LONG/SHORT pool logic, day-trade tax rule, and oversell handling. Mirroring it inline duplicates 80 lines and guarantees future drift.

**Alternative considered:** extract a shared `_compute_realized_for_range` helper used by both. Rejected — `iter_realized_events` is the helper. Adding another wrapper is noise.

### Decision 2 — Skip SHORT rows in long qty/cost roll-up

In the transaction walk (`:559-576`), branch on `position_side`:

```python
side = getattr(t, "position_side", None) or PositionSide.LONG
if side is PositionSide.SHORT:
    # SHORT contributes nothing to long-side MV or cost.
    # Realized impact handled by iter_realized_events bucket.
    continue
# existing LONG BUY/SELL logic
```

**Why:** matches `portfolio_service.py:512-519` which already excludes SHORT from `holdings_map`. SHORT MV/PnL surfaces separately via `/realized-pnl` and the dedicated short-position views; mixing it into long MV would double-count.

**Alternative considered:** also roll up a separate short qty/cost track for MV. Rejected — `portfolio_snapshot` totals are long-side dashboard aggregates; SHORT exposure already shown elsewhere. Out of scope here.

### Decision 3 — Rebuild stale rows via CLI flag, not auto-migration

Add `python -m app.services.networth_backfill_service --rebuild-all` (or extend existing CLI entry). Operator runs it once post-deploy over `[earliest trade_date, today]`. Existing rows overwritten via the same `Session.merge` path used by replay.

**Why:** Alembic migrations should not invoke business-logic services. Operator wants explicit control over recompute window (the chain takes minutes on long histories).

**Alternative considered:** auto-rebuild on first request after deploy. Rejected — surprises operator; can collide with cron snapshot writer.

### Decision 4 — Parity test as canonical correctness gate

Add `tests/integration/test_snapshot_realized_pnl_parity.py`: seed mixed LONG+SHORT+day-trade transactions, run replay, assert `portfolio_snapshot.total_realized_pnl == sum(realized_pnl_service.iter_realized_events).net_pnl` for every date in range.

**Why:** parity is the contract. Test enforces it forever; any future refactor that drifts the two engines will fail loudly.

## Risks / Trade-offs

- **Stale snapshot rows post-deploy until rebuild runs** → CLI is idempotent + documented; status endpoint surfaces last rebuild timestamp. Mitigate by running rebuild in same maintenance window as deploy.
- **`iter_realized_events` cost on large histories** → already used by `/realized-pnl` over arbitrary ranges; perf measured at ~50ms per 1k transactions. Acceptable for backfill which is already O(dates × symbols).
- **Forward-fill on weekends carries `cumulative_realized` forward unchanged** → correct behavior; realized PnL only increments on trade dates.
- **SHORT exposure invisible in `portfolio_snapshot`** → known. Already true for `total_market_value`; explicit non-goal here.
- **Day-trade tax rule may shift historical values when rebuild runs** → expected; documented in rebuild script output.

## Migration Plan

1. Land code + tests; deploy normally (no schema change).
2. Operator runs `python -m app.services.networth_backfill_service --rebuild-all` over earliest-trade-date → today.
3. Sanity check: hit `/api/portfolio/realized-pnl?from=YYYY-01-01&to=YYYY-12-31` and `/api/portfolio/history?from=...&to=...`; latest snapshot `total_realized_pnl` MUST equal endpoint aggregate.
4. Rollback: revert code; existing snapshot rows remain (still wrong, but no worse than pre-fix state).

## Open Questions

- Does the CLI rebuild need a `--dry-run` flag that prints per-date diffs without writing? (Lean yes — cheap to add; useful for spot-check before overwrite.)
- Should `post_import_orchestrator` log a WARN when imported batch contains SHORT rows and snapshot rebuild is pending? (Lean no — chain auto-runs replay anyway; once code is fixed, no manual step needed for incremental imports.)
