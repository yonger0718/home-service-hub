## Context

`_recompute_day_trade_flags` (portfolio_service.py:216-241) treats any same-symbol same-day BUY+SELL pair as a day-trade. Real-world observation: warrant `045378` had a BUY+SELL pair flagged `is_day_trade=true`, which is incorrect under TW FSC rules — 認購(售)權證 and 牛熊證 are not eligible for 現股當沖.

The bundled `twstock==1.5.1` package exposes `twstock.codes[symbol].type` carrying the instrument classification (`股票`, `ETF`, `認購權證`, `認售權證`, `牛證`, `熊證`, etc.). Per inventory check (40,200 warrant entries in TWSE+TPEx bundles), this is the authoritative offline classifier.

`symbol_map` already caches `(name, symbol, market)` per refresh. Adding `type` here is the natural extension — one column, refreshed on the same weekly cron, no new external dependency.

## Goals / Non-Goals

**Goals:**

- Prevent `is_day_trade=true` on warrant / 牛熊證 transactions.
- Backfill existing wrongly-flagged warrant rows in a single migration.
- Keep behavior conservative on unknown symbols (fail-open: assume eligible).
- Zero frontend change — UI badge already gated on `is_day_trade`.

**Non-Goals:**

- Block warrant ledger / short / margin handling — out of scope; only the flag changes.
- Auto-fetch from TWSE/TPEx live; bundled `twstock` codes DB is sufficient.
- Generalize to other settlement-rule differences (e.g., 興櫃, 全額交割股) — separate proposal if needed.
- Update existing `當沖` realized-pnl event aggregation logic — values flow naturally from the corrected flag.

## Decisions

### D1: Persist `type` in `symbol_map`, do not query twstock at runtime

- **Choice**: Add `type VARCHAR(32) NULL` column to `symbol_map`, populate during `refresh_all_from_twstock`. Helper queries the column.
- **Alternative**: Call `twstock.codes[symbol].type` inline inside `_recompute_day_trade_flags`.
- **Why**: DB lookup is single SELECT (joins existing query path), offline-safe, and avoids importing `twstock` in the hot transaction path. Refresh cron already keeps the map current. Inline lookup would force `import twstock` (lazy module) on every transaction create/update.

### D2: Fail-open on unknown / unmapped symbols

- **Choice**: `is_day_trade_eligible(db, symbol)` returns `True` when `symbol_map` has no row OR `type` is NULL.
- **Alternative**: Fail-closed (assume ineligible until proven otherwise).
- **Why**: Preserve current behavior for the long tail of unmapped tickers (foreign symbols, OTC, stale data). Day-trade flag is informational, not a trading gate — false negatives cost more (silently missing a day-trade) than false positives (already the status quo). User can re-run `symbol_map_refresh` then a backfill if precision matters.

### D3: Ineligibility predicate matches substring `{認購, 認售, 牛證, 熊證}`

- **Choice**: Type is ineligible iff it CONTAINS any of `{認購, 認售, 牛證, 熊證}` as a substring.
- **Alternative**: `startswith` prefix match.
- **Why substring**: `twstock` 1.5.1 emits combined types like `上市認購(售)權證` (30,615 rows) and `上櫃認購(售)權證` (9,585 rows) — call+put are bundled together with a market-prefix. Prefix match would miss everything; substring covers all current and likely future variants. Confirmed against a fresh `refresh_all_from_twstock` run: only 3 distinct ineligible type strings exist today; substring catches every one and stays robust against new variants like `國外指數股票型基金牛證`.
- **Known gap**: ~12,432 entries (≈22% of bundled codes) have empty `type` in twstock data despite being warrants by name pattern (`購NN`/`售NN` suffix). These fail-open as eligible per D2 — manual fix required if observed. Documented as out-of-scope for v1; future change can add name-pattern fallback.

### D4: Migration scope — narrow (warrant-only clear, no positive recompute)

- **Choice**: Migration only flips rows currently `is_day_trade=true` AND symbol is ineligible per `symbol_map.type`. It does NOT recompute eligible buckets in either direction.
- **Alternative considered + rejected**: Full bucket rescan of every `(symbol, calendar_date)` that recomputes both directions.
- **Why narrow**: The legacy bucket heuristic (`has_buy AND has_sell` on same calendar date) over-classifies in the positive direction — it catches cases like "BUY to open new position + SELL to close old position on same day" that are NOT real day-trades. The broker CSV's authoritative `沖買 / 沖賣` markers are folded into `LONG` at import time (`broker_cathay_service.CATHAY_SIDE_MAP`), so the heuristic is currently the only signal. A full positive-direction rescan would propagate the heuristic's false positives across historical data on rows that have managed to stay False until now. A separate follow-up change (`broker-day-trade-marker`) will preserve the explicit broker marker and re-derive day-trade flags from it — until then, the narrow migration only does the unambiguous half (clear flags on instruments that can never be day-trades).
- **Trade-off**: Eligible symbols whose buckets are wrongly False stay wrongly False until the follow-up change ships. Acceptable because the affected rows are already at False (no user-visible regression).

### D5: Migration imports application service code

- **Choice**: Migration imports `symbol_map_service.is_day_trade_eligible` and reuses the gated bucket logic from `portfolio_service`.
- **Risk**: Alembic migrations importing app code can break on future model refactors.
- **Mitigation**: Inline the predicate (small) inside the migration so it stays self-contained. Reuse symbol_map raw SQL query (no ORM) inside the migration.

### D6: `type` column in `symbol_map` is nullable

- **Choice**: `type VARCHAR(32) NULL` (not `NOT NULL`).
- **Why**: First refresh after migration may not yet have run; existing rows lacking `type` should resolve as `eligible=True` (D2). No backfill of `type` in the column-add migration — defer to the next scheduled `symbol_map_refresh`.

## Risks / Trade-offs

- **Stale `symbol_map.type` after a code reclassification** → twstock package update + weekly refresh. Risk window ≤ 7 days; acceptable for informational flag.
- **`twstock.codes` missing newly-listed warrants** → fail-open keeps the row flagged as day-trade. User can manually fix via direct DB update; not severe.
- **Migration touches many rows** → full table scan grouped by `(symbol, date)`. Estimate: <10K rows in current home-hub portfolio service, sub-second. If grown, can switch to chunked execution; defer until measured.
- **Lazy `twstock` import side effect** → `refresh_all_from_twstock` already imports lazily; `type` field is plain `getattr(entry, "type", None)`. No new side effects.

## Migration Plan

1. Alembic migration `add_symbol_map_type_column.py`:
   - `op.add_column("symbol_map", sa.Column("type", sa.String(32), nullable=True))`
   - Downgrade drops the column.
2. Code change to `refresh_all_from_twstock`: pull `type` from `twstock.codes[code]`, include in `db.merge(SymbolMap(...))`.
3. Code change to `symbol_map_service`: add `is_day_trade_eligible(db, symbol) -> bool`.
4. Code change to `_recompute_day_trade_flags`: gate the final `new_flag = has_buy and has_sell` on eligibility.
5. Alembic data migration `backfill_day_trade_flags.py`:
   - Pre-condition: must run AFTER `symbol_map.type` is populated; the migration first triggers `refresh_all_from_twstock` (or warns if `symbol_map.type` is entirely NULL).
   - Iterate distinct `(symbol, calendar_date)` from `transactions`, recompute flag in-place.
6. Tests pass: warrant pair flag stays False; equity pair stays True; unmapped stays True; migration rewrite test.

**Rollback**: Drop the `type` column; revert code. Existing transactions retain whatever `is_day_trade` value was last computed; manually patch via SQL if needed.

## Open Questions

- None blocking implementation.
