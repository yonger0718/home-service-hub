## Context

Today, `transactions` stores `name` (Chinese display name) captured at import time, but **does not** store the instrument's type (warrant / ETF / common stock). Day-trade eligibility is resolved at recompute time by a live lookup against `symbol_map.type` keyed by `symbol`. `symbol_map` is refreshed periodically from `twstock`, which itself mirrors the **current** TWSE/TPEx code table.

TWSE recycles warrant codes (6-digit symbols beginning with `0` for 認購, etc.) after expiry. The same numeric code can later host an unrelated warrant or even a non-warrant ETF/common stock. When this happens:

1. `symbol_map.type` for the recycled code switches from `上市認購(售)權證` to whatever the new instrument is.
2. `is_day_trade_eligible(symbol)` for the old warrant row begins returning `True` (eligible) because the live lookup now sees the recycled type.
3. Next time `_recompute_day_trade_flags` runs over the bucket containing the old warrant transaction (e.g. on next CSV re-import or backfill), the flag retroactively flips from False → True, contaminating realized-P&L tax cost estimates and badges.
4. UI shows the new instrument's name for historical rows (cosmetic), but `transactions.name` is already captured per-row at import so this is only an issue for any future read-side that derefs `symbol_map` instead of `transactions.name`.

No recycled code has hit the production DB yet (verified: `045378` still matches `symbol_map`). Closing the gap pre-emptively is cheap; recovering after a silent recycle is not.

## Goals / Non-Goals

**Goals:**

- Make day-trade eligibility for warrant rows immune to future warrant-code recycle by stamping `instrument_type` at insert time.
- Backfill the new column on existing warrant rows using the current `symbol_map.type` as the best-available historical proxy (no twstock archive exists).
- Preserve today's behavior for non-warrant rows (column stays NULL, live lookup remains the eligibility source).
- Keep the change additive: no breaking schema, no API/contract change, no frontend change.

**Non-Goals:**

- Recover pre-import historical warrant names from external sources (impossible — twstock only exposes current).
- Snapshot for non-warrant ETF/stock renames (separate concern; ETF rename does not change tax-eligibility class, so the realized-P&L blast radius is zero).
- Add a UI toggle between snapshot name and live name.
- Auto-detect recycle events or alert on them.
- Change the warrant detection vocabulary used elsewhere — reuse the existing `_INELIGIBLE_TYPE_SUBSTRINGS` tuple (`認購`, `認售`, `牛證`, `熊證`) verbatim.

## Decisions

### D1 — Column shape: `instrument_type VARCHAR(64) NULL`

Single nullable text column on `transactions`. NULL means "fall back to live `symbol_map` lookup" (today's behavior). Non-NULL means "trust this stamped value; never re-derive from `symbol_map`."

Alternatives considered:

- `is_warrant BOOLEAN`: cheaper but loses the actual TWSE type string, which is useful for forensic queries ("which 認購 / 認售 rows do I hold?") and matches what `symbol_map.type` carries today.
- Stamp `name` + `instrument_type` + `market`: rejected — `market` doesn't affect any current behavior, and ETF rename is out of scope, so the marginal column buys nothing.
- Encode as enum: rejected — `symbol_map.type` already carries free-text strings from twstock (e.g. `上市認購(售)權證`, `上市ETF`), and forcing them through an enum requires a maintenance burden every time twstock adds a category.

`VARCHAR(64)` accommodates the longest observed twstock type string (`上市認購(售)權證` = 9 CJK chars ≈ 27 bytes UTF-8) with comfortable headroom.

### D2 — Snapshot only on warrant detection

At insert time, the code checks whether the symbol's current `symbol_map.type` contains any of `{認購, 認售, 牛證, 熊證}`. If yes, the value is stamped onto `transactions.instrument_type`. If no (including unmapped / NULL type), the column is left NULL.

Rationale: snapshotting every row would couple a benign feature (warrant-recycle defense) to every insert path and grow the table by one column-worth of redundant strings for ~99% of rows. Stamping selectively keeps the change surgical and the column NULL on the cardinality-dominant majority.

The detection helper is centralized: `symbol_map_service.lookup_warrant_type(db, symbol) -> Optional[str]` returns the type string when warrant, `None` otherwise. Both broker-import and manual-entry paths call it; tests stub it once.

### D3 — Eligibility helper: stamped value wins, fail-open on NULL

`is_day_trade_eligible` signature changes from `(db, symbol) -> bool` to `(db, symbol, instrument_type=None) -> bool`. Behavior:

| `instrument_type` arg | Behavior |
|---|---|
| Provided non-empty | Bypass `symbol_map` lookup entirely. Return `False` if it matches any of `_INELIGIBLE_TYPE_SUBSTRINGS`, else `True`. |
| `None` or empty | Existing behavior: live `symbol_map` lookup, fail-open on unmapped / NULL. |

`_recompute_day_trade_flags` iterates the bucket and passes each row's `instrument_type` through. Bucket-wide flag still gates on `is_day_trade_eligible(... symbol)` once (the bucket key is the symbol), so the stamped value of any one warrant row in the bucket would suffice — but in practice an entire bucket of one warrant symbol shares the same stamped type, so the choice of representative row is immaterial. To avoid coupling correctness to row order, we resolve eligibility per-row inside the comprehension and require ALL rows in the bucket to agree before flipping `board_flag` true.

Alternative considered: store `instrument_type` on the bucket key and resolve once. Rejected — `models.Transaction` rows already carry the stamped value; the cost of resolving per-row is negligible (in-memory string compare on a list of ≤ ~10 rows per bucket) and avoids the failure mode where a recycle straddles a single bucket.

### D4 — Backfill migration: warrant-only UPDATE keyed off live `symbol_map`

Migration body (parametrized SQL):

```sql
UPDATE transactions t
SET instrument_type = sm.type
FROM symbol_map sm
WHERE t.symbol = sm.symbol
  AND t.instrument_type IS NULL
  AND (
       sm.type LIKE '%認購%'
    OR sm.type LIKE '%認售%'
    OR sm.type LIKE '%牛證%'
    OR sm.type LIKE '%熊證%'
  );
```

Idempotent: re-running on already-stamped rows is a no-op because of the `IS NULL` guard. Prints affected row count, mirroring the `r5g6h7i8j9k0` odd-lot backfill style.

The migration accepts the inherent risk that a code RECYCLED BEFORE THE BACKFILL RAN would stamp the NEW instrument's type onto OLD rows. Mitigation: dev DB has no known recycle yet (verified Sec. Context above). Going forward, the insert-time snapshot closes the window.

### D5 — No re-derivation of name

The existing `transactions.name` column is left untouched. Future recycle would never overwrite `name` because no code path today re-derives it from `symbol_map` after insert. The original snapshot intent (preserve display name across recycle) is therefore already satisfied as a side-effect of the existing insert behavior. This change focuses on the eligibility leak, which is the actual silent-correctness bug.

## Risks / Trade-offs

- **Backfill stamps current-`symbol_map.type` not historical type** → if a recycle already happened pre-backfill, old rows get tagged with the recycled type. Mitigation: confirmed no recycled codes in current production. Going forward, insert-time snapshot prevents new occurrences. Accepted residual risk: zero historical impact today.
- **`_recompute_day_trade_flags` per-row resolution adds tiny overhead** → ≤ ~10 in-memory rows per bucket × cheap string compare = sub-microsecond. Not measurable.
- **Stamped value can go stale if instrument type legitimately reclassifies (without recycle)** → e.g. TWSE relabels `上市ETF` → `上市指數股票型ETF`. Mitigation: pure-cosmetic; eligibility class doesn't change. Accepted.
- **Non-warrant rows still vulnerable to recycle** → in theory a common-stock code could be recycled too. In practice TWSE does not recycle 4-digit equity codes; this is a warrant-specific lifecycle. If TWSE ever changes that policy, this design naturally extends — broaden the warrant detection predicate to "snapshot every row" and the existing fallback logic continues to work.
- **Two new Alembic revisions tied to one feature** → operator must apply both during deploy. Mitigation: docstring on both revisions cross-references the other; migration chain runs in sequence under `alembic upgrade head` so operator sees both.

## Migration Plan

1. Apply column-add migration (additive, online-safe).
2. Apply backfill migration (idempotent UPDATE).
3. Roll out application code: insert paths and eligibility helper updates ship together with the migrations.
4. Verify on dev DB: query for distinct stamped types on warrant symbols, confirm non-warrant rows remain NULL.

Rollback strategy: drop the column (loses stamped values; eligibility reverts to today's live-lookup behavior — i.e. back to the original risk). No data loss in other columns.

## Open Questions

(none — D1 / D2 / D3 locked in user clarification before propose)
