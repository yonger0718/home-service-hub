## Context

PR #5 fixed CSV fingerprint collisions by folding optional `order_id` into the SHA256. That solves the *forward* path: any new CSV upload with `order_id` correctly disambiguates same-day fills. It does **not** solve the *backward* path: rows already in the database were inserted under the legacy hash (no `order_id` segment). Re-uploading a broker CSV with `order_id` now treats those existing rows as new (different hash) and inserts duplicates.

The user has a real dataset: 國泰證券's `證券對帳單` export, 1997 rows, with `委託書號` per row. Columns: `股名, 日期, 成交股數, 淨收付金額, 買賣別, 成交價, 成本, 手續費, 交易稅, 融資金額/券擔保品, 資自備款/券保證金, 利息, 稅款, 券手續費/標借費, 委託書號`. Notable:
- First file line is a Chinese preamble (`根據您篩選的結果，總計有1997筆資料...`), header is line 2.
- No `symbol` column — only `股名` (Chinese name).
- `買賣別` has 8 values: `現買/現賣/資買/資賣/券買/券賣/沖買/沖賣`.

Home-hub's `transactions` table models BUY/SELL only (no margin/short/day-trade discriminator). Adding those types is out of scope per the merge plan.

## Goals / Non-Goals

**Goals:**
- Import the 國泰 CSV without manual pre-processing.
- Provide a one-shot **smart rehash backfill** that updates existing rows' fingerprints to fold in `order_id`, rather than inserting duplicates.
- Surface name→symbol collisions loudly (no silent guesses).
- Keep the generic CSV path (English headers, English types) byte-for-byte unchanged.
- No DB migration. No new columns. Hash-only stays hash-only.

**Non-Goals:**
- Persisting margin/short/day-trade discriminator (would require schema change + spec for new transaction types — separate change).
- Persisting `order_id` as a column (deferred; current spec is hash-only).
- Generic broker auto-detection across other brokers (永豐, 元大, etc.). 國泰-specific only.
- Migrating already-imported manual rows that *don't* match any CSV row (they stay as-is with legacy hash).
- Bidirectional sync / live broker API. CSV-upload one-shot only.

## Decisions

### D1 — Format dispatcher, not generic parser extension

`parse_transactions_csv` stays as today's generic English/Chinese-synonym parser. A new thin dispatcher (`detect_csv_format`) sniffs the first non-empty line: if it matches `^根據您篩選的結果` → route to `parse_cathay_transactions_csv`. Otherwise → existing path. Keeps each parser readable and testable in isolation.

**Alternative considered:** extend the generic parser with more synonyms (e.g. `股名→name`, `現買→BUY`). Rejected: collapses two distinct CSV dialects into one path, makes `_normalize_header` behavior order-dependent (does `現買` get expanded before or after `BUY` lookup?), and the preamble row is a parser-state concern, not a synonym concern.

### D2 — Name-to-symbol via bundled JSON asset

Ship `app/data/name_to_symbol.json` derived from stonk's `code_name_map.json`. Build the reverse map at module-load:

```python
# reverse: name -> [symbol, ...]
NAME_TO_SYMBOL: dict[str, list[str]] = _build_reverse(load_json("data/name_to_symbol.json"))
```

A given Chinese name may map to multiple tickers (rare for stocks, common for derivatives like 元大23購15). The reverse map is `name -> list[symbol]`. Lookup behavior:
- 0 matches → row error: `cannot resolve symbol for 股名='XXX'`.
- 1 match → use it.
- 2+ matches → row error: `ambiguous symbol for 股名='XXX': [2330, 2330B, ...]`.

**Alternative considered:** call out to a live TWSE/TPEx lookup. Rejected: adds network dep + latency + flakiness to import path. Static map is sufficient for a one-shot backfill; users can amend the JSON if a name is missing.

### D3 — 國泰 type vocabulary → BUY/SELL collapse

8 values collapse to 2:

```python
CATHAY_SIDE_MAP = {
    "現買": "BUY",  "資買": "BUY",  "券買": "BUY",  "沖買": "BUY",
    "現賣": "SELL", "資賣": "SELL", "券賣": "SELL", "沖賣": "SELL",
}
```

Sub-type prefix (`現` / `資` / `券` / `沖`) is preserved in the parsed payload under key `broker_subtype` for frontend display only — not persisted, not in fingerprint. Frontend can show a small chip ("資" badge) if desired.

**Trade-off:** lossy — round-tripping CSV → DB → CSV won't recover `資/券/沖`. Acceptable because home-hub's schema doesn't model these; treating them all as plain BUY/SELL matches the current data model.

### D4 — Smart rehash backfill: match-by-legacy-hash, not field-by-field

For each CSV row in rehash mode:

```python
legacy_fp = _transaction_fingerprint(symbol, type_, qty, price, trade_date, fee, tax)  # no order_id
new_fp    = _transaction_fingerprint(symbol, type_, qty, price, trade_date, fee, tax, order_id=order_id)
existing  = db.query(Transaction).filter_by(import_fingerprint=legacy_fp).one_or_none()
if existing:
    existing.import_fingerprint = new_fp  # in-place hash rewrite
else:
    # row not yet in DB — fall through to normal insert path with new_fp
```

**Why hash-match, not field-match:** the legacy fingerprint *is* the canonical join key. It already encodes `(symbol, type, qty, price, trade_date, fee, tax)` exactly. Re-deriving an `AND` filter on each field re-implements the same join (and risks normalization drift — e.g. float comparison on `price`).

**Edge case — same-day collision pre-fix:** when two CSV rows produce the same `legacy_fp`, only one DB row exists (the bug). First CSV row rewrites it to `new_fp_A`. Second CSV row again computes the same `legacy_fp`, finds nothing (was just rewritten), falls through to insert with `new_fp_B`. Result: the silently-dropped twin is recovered. **This is the whole point.**

**Edge case — already-rehashed CSV uploaded twice:** second pass computes `legacy_fp`, finds nothing (rows now keyed by `new_fp`), tries to insert with `new_fp`, hits `UNIQUE` constraint, reported as `skipped_duplicates`. Safe.

**Alternative considered:** add a transient `legacy_fingerprint` column or audit table. Rejected: no recovery from migration error needed (idempotent above) and persists a one-shot migration concern in the schema forever.

### D5 — Endpoint shape: auto-dispatch, no new query param

Reuse the existing `POST /api/portfolio/imports/transactions` endpoint unchanged. The router calls `detect_csv_format(raw)`; if the result is `cathay`, dispatches to the rehash-capable cathay parser. Otherwise: existing generic path.

**No `rehash` query param.** Rehash logic is always-safe for cathay CSVs (no DB matches → plain insert; matches → in-place rewrite; second pass → all `skipped_duplicates` per D4). Exposing a toggle would be: confusing UI vocabulary (`重算指紋` is an implementation detail), permanent UI clutter for a one-shot migration, and a footgun (toggle-off behavior would re-insert 1997 dupes — never the desired path). Auto-apply collapses the matrix.

Dry-run still works (`dry_run=true`) and returns `{would_rehash, would_insert, would_skip_duplicate, errors}` so users can preview before commit.

**Alternative considered:** separate `/imports/cathay-rehash` endpoint or a `rehash=true` query flag. Both rejected for the reasons above.

### D6 — Atomicity

Whole rehash runs in a single transaction. If any row errors out (name collision, parse failure), entire batch rolls back. Forces user to fix the input CSV before any DB state changes. Partial-success would leave the user in an undetermined "some rows rehashed, others still on legacy hash" state.

## Risks / Trade-offs

- **Stale `name_to_symbol.json`** → resolution fails for newly-listed stocks. Mitigation: ship a refresh script (`scripts/refresh_name_to_symbol.py`) that re-pulls from stonk's source; document in README.
- **Same Chinese name on multiple tickers** (derivatives, dual listings) → row error. Mitigation: error message lists all candidates; user supplies an override JSON (`name_to_symbol_overrides.json`, loaded on top). Out of scope for v1 — manually edit JSON for now.
- **Whole-batch rollback on single bad row** could frustrate users with a 1997-row CSV. Mitigation: dry-run mode surfaces all errors before commit; user fixes once.
- **Rehash applied to a non-國泰 CSV** (operator error) → impossible by construction. Auto-dispatch runs rehash *only* when `detect_csv_format` returns `cathay` (preamble sniff). Generic CSVs take the unchanged generic path. No user-facing flag to misuse.
- **Lossy collapse of 資/券/沖**: documented limitation; preserved in parsed payload for UI display but not in DB.
- **Hash rewrite is irreversible** without a fresh broker CSV. Mitigation: dry-run is encouraged; rehash mode is documented as "run once after PR #5 lands."

## Migration Plan

This change is itself the migration mechanism. Rollout order:

1. Merge PR #5 (fingerprint fix). New imports correctly handle `order_id`.
2. Merge this change. Backfill capability available.
3. User uploads broker CSV with `dry_run=true`. Reviews report (incl. `would_rehash`, `would_insert` counts).
4. User uploads same CSV with `dry_run=false`. Existing rows rehashed in-place; previously-dropped twins recovered as new rows.
5. Future broker CSV uploads take the same path: cathay → auto-rehash (idempotent — first upload migrates, subsequent uploads no-op via `skipped_duplicates`).

**Rollback:** if rehash produces wrong state, restore from the daily DB backup (infra/backup script, when shipped). No code rollback needed — generic CSVs are routed to the unchanged generic path; only 國泰 CSVs are affected, and re-running is idempotent.
