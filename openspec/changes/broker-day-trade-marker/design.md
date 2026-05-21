## Context

Cathay 證券對帳單 CSV labels every 現股當沖 row with `買賣別='沖買'` or `沖賣` (the only two 現股當沖 vocabulary entries; 資/券 sides are margin, not 現沖). `broker_cathay_service.CATHAY_SIDE_MAP` currently maps `沖買→("BUY","LONG")` and `沖賣→("SELL","LONG")`, indistinguishable from `現買`/`現賣`. The `broker_subtype` payload field (`side[0]`) was added by an earlier change but is never persisted — `_insert_transaction` drops it.

Downstream, `_recompute_day_trade_flags` (services/stock-portfolio-service/app/services/portfolio_service.py:217) uses bucket heuristic: `is_day_trade = has_buy AND has_sell` for the (symbol, calendar_date) tuple, AND-gated by `is_day_trade_eligible` (warrant rejection). The heuristic over-classifies any equity that happened to have an unrelated BUY and SELL on the same date — the warrant-day-trade-eligibility backfill iteration surfaced 20 such equity rows on dev DB.

TW FSC 現股當沖 rule: only 現股 (cash account) round-trip on eligible instruments. 資/券 (margin) does not qualify. So preserving 沖買/沖賣 is sufficient — those are the only Cathay markers that imply 現沖.

## Goals / Non-Goals

**Goals:**
- Persist authoritative day-trade signal from Cathay CSV on `transactions` rows.
- Priority chain in `_recompute_day_trade_flags`: explicit broker marker → bucket heuristic fallback → eligibility gate (unchanged).
- One-shot Alembic data migration flips wrongly-True equity rows to False on dev DB without re-running the full broad-scan iteration.

**Non-Goals:**
- SinoPac and other broker CSVs (no equivalent marker column today).
- UI surfacing of the marker column (read-only persistence for now; future change can surface).
- Removing the bucket heuristic entirely — kept as fallback for manual entries and non-Cathay sources.
- Backfilling marker on legacy Cathay rows from re-parsed CSVs — original CSVs not retained per user choice; backfill is symbol+date based, marker stays NULL on legacy.

## Decisions

### D1: Column shape — single nullable VARCHAR(8) `broker_day_trade_marker` storing `沖買`/`沖賣`/NULL

**Why over alternatives:**
- **Alt A: boolean `is_explicit_day_trade`** — loses BUY-vs-SELL semantic on the marker side; can't reconstruct what the broker actually said.
- **Alt B: enum** — overkill; only two valid string values today, and migrations would couple to broker vocabulary.
- **Alt C: reuse `broker_subtype` (`沖`/`現`/`資`/`券`)** — would require persisting `broker_subtype` first AND coupling marker semantics to first-character heuristic. Cleaner to store the full marker.

Storing the literal CSV value is debuggable: a future operator can `SELECT broker_day_trade_marker, COUNT(*)` and immediately see the distribution.

### D2: Priority chain semantics

In `_recompute_day_trade_flags(db, symbol, calendar_date)`:

```python
bucket = [...]  # rows for (symbol, calendar_date)
marker_present = any(row.broker_day_trade_marker in {"沖買", "沖賣"} for row in bucket)
has_buy = any(row.type == BUY for row in bucket)
has_sell = any(row.type == SELL for row in bucket)
if marker_present:
    new_flag = is_day_trade_eligible(db, normalized)  # marker still gated by warrant rule
elif has_buy and has_sell:
    new_flag = is_day_trade_eligible(db, normalized)  # legacy heuristic, same gate
else:
    new_flag = False
```

Marker on warrant still returns False (the eligibility gate is the safety net — broker may erroneously emit 沖買 on an ineligible instrument; we trust FSC vocabulary over broker tagging).

The whole bucket gets one flag (matches existing semantics). A single 沖買 row in the bucket without a paired 沖賣 still flips the bucket True — this matches the broker's intent (the trade *was* a day-trade transaction even if its pair landed in a different bucket due to data lag), and intentionally errs on the side of believing the broker.

### D3: Migration scope — only flip True→False where marker absent AND legacy heuristic was the sole reason

**Why:** the warrant-day-trade-eligibility iteration's broad-scan migration over-classified by enforcing the heuristic in the True direction. This change must NOT repeat that. The migration:

1. Find all rows with `is_day_trade=true` AND `broker_day_trade_marker IS NULL`.
2. For each such row, recompute the bucket using the new priority chain (treating marker as NULL since none persists).
3. If the new flag is False (which it will be only when the bucket has no marker AND no longer satisfies has_buy+has_sell+eligible — i.e., the warrant gate already handles this; the marker-absent legacy case stays True), flip to False.

In practice for legacy rows the priority chain falls through to the heuristic and yields True for any same-day BUY+SELL on an eligible symbol. So the migration flips **nothing** for legacy rows — those wrongly-True equity rows on dev DB stay wrongly-True until the user re-imports the original Cathay CSV (which they no longer have) OR manually patches them.

**Operational conclusion:** the migration is mostly a no-op for legacy data, and existence is justified mainly to:
- Future-proof: any *new* Cathay re-import after this change will persist markers, and the recompute will then correctly clear flags on equity rows where the marker is absent. This already works through the live recompute on insert, no migration needed for new imports.
- Document the intent explicitly so a future operator reading the alembic chain understands what changed.

The 20 wrongly-True equity rows on dev DB will be cleared by manual SQL after re-importing the user's most recent Cathay CSV (Cathay's portal still serves last-30-days history). Migration body itself defers to that workflow — no destructive auto-flip without marker evidence.

### D5: Odd-lot rows never flagged `is_day_trade=true`

User does not 沖 零股 — their odd-lot rows are fractional-share accumulation, never day-trades. The 49 wrongly-True 零股 rows currently on dev DB confirm the heuristic over-classifies them (e.g. `6491 BUY 25 + SELL 25` same day, unrelated accumulation order paired with a sell of a different lot).

**Rule:** a row is **odd-lot** iff `quantity < 1000 OR quantity % 1000 != 0`. Odd-lot rows SHALL always have `is_day_trade=false`, regardless of any broker marker, any bucket pair, or any eligibility outcome.

**Why over alternatives:**
- **Alt: FSC-accurate same-lot-category rule** (board-lot↔board-lot OR 零股↔零股 pair within bucket flips True) — technically correct per Taiwan 盤中零股 沖 rule (allowed since 2022-12), but user reports zero day-trade activity on 零股 in their full history. Tighter rule reduces false positives to zero for them and adds no false negatives.
- **Alt: enforce only when no marker present** — wouldn't clear the 49 legacy wrongly-True rows because they have NULL marker; would need separate backfill anyway. Simpler to make the rule absolute.

The bucket is therefore split into two sub-views in `_recompute_day_trade_flags`:
- **Odd-lot rows** → always False.
- **Board-lot rows** → priority chain (D2) computes one shared flag for the board-lot subset; marker/heuristic counts only consider board-lot rows.

A one-shot data migration SHALL clear `is_day_trade=true` on every existing row matching the odd-lot predicate. Safe (deterministic, no marker dependency), independent of D3's no-op stance because the predicate is purely on `quantity`, not on imported marker evidence.

### D4: Trade-off — bucket heuristic kept for manual entries

Keeping the heuristic fallback means same-day BUY+SELL on a manually-entered equity still flips True. This is *correct* for the manual-entry case (the user typed both trades, they know if it's a day-trade), but means manually-entered rows can't distinguish "true 現沖" from "two coincident orders." This is acceptable: the user can edit the row and set `broker_day_trade_marker` manually if needed (future UI work, out of scope here).

## Risks / Trade-offs

- **[Risk]** Marker collision with old `broker_subtype` payload field → **Mitigation**: `broker_subtype` was never persisted; this change makes the marker the single source of truth and leaves `broker_subtype` as a parser-internal payload entry. No DB column rename, no data clash.
- **[Risk]** Re-import of an old Cathay CSV after this change goes live could re-tag rows with markers, and the live `_recompute_day_trade_flags` on the rehash path would then flip equity rows correctly — but the rehash path in `_commit_rehash` only updates `import_fingerprint` and `position_side`, not the marker. → **Mitigation**: extend rehash to also write `broker_day_trade_marker` (covered in tasks).
- **[Risk]** Wrong tagging by Cathay (e.g., 沖買 on an ineligible warrant) → **Mitigation**: eligibility gate still applies; marker is overridden by eligibility check.
- **[Trade-off]** Migration intentionally no-op for legacy data → operator must re-import or SQL-patch to clean dev DB. Documented in tasks.md and migration docstring.

## Migration Plan

1. Schema-only migration: `op.add_column("transactions", sa.Column("broker_day_trade_marker", sa.String(length=8), nullable=True))`. Downgrade drops the column.
2. Data migration (odd-lot backfill): `UPDATE transactions SET is_day_trade=false WHERE is_day_trade=true AND (quantity < 1000 OR quantity % 1000 != 0)`. Safe per D5. No downgrade.
3. No board-lot data migration — see D3.
4. Live flow: next Cathay re-import populates the column on rehashed rows; live `_recompute_day_trade_flags` then converges board-lot flags.
5. Dev cleanup: user re-imports last 30 days of Cathay CSV → rehash path tags markers → recompute clears wrong board-lot flags. Out of scope: write a one-shot script for it; just document the steps.

## Open Questions

- Should the marker also be persisted on the rehash path? **Answer (locked):** yes, tasks include rehash patch. Otherwise re-importing the same CSV would not propagate markers to pre-existing rows.
