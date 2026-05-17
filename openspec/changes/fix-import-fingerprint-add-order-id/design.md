## Context

Today's fingerprint:

```
SOURCE_TRANSACTIONS | symbol | type | quantity | price(.4f) | trade_date.iso | fee(.4f) | tax(.4f)
```

`UNIQUE(import_fingerprint)` on `transactions` enforces dedupe at INSERT. Re-uploading the same CSV is a no-op — which is the right behavior for accidental re-uploads, but the wrong behavior for two genuinely-distinct fills that happen to share every column.

Concrete failure (real-world, reported by the user):

> Two identical BUY orders: 1000 sh `0050` @ NTD 50, same `trade_date`, same fee/tax. Both legitimate. SHA256 collides. Second silently dropped.

Most Taiwan brokers expose a per-order identifier (委託書號 / 訂單編號) on their CSV exports, which is unique per fill. When present, including it in the hash trivially disambiguates.

## Goals / Non-Goals

**Goals:**
- Identical same-day fills with distinct `order_id` values produce distinct fingerprints.
- Re-uploading the same CSV (rows that already have `order_id` set) still dedupes cleanly.
- Re-uploading an *existing* (pre-feature) CSV with no `order_id` column produces the same hashes as today — no regressions on already-imported data.
- Lenient: CSVs without `order_id` continue to work; the limitation is documented, not enforced.

**Non-Goals:**
- Persisting `order_id` as its own DB column (deferred — would need migration; not required to fix the bug).
- Backfilling `order_id` onto historical transactions.
- Validating `order_id` shape (broker formats vary; we treat it as an opaque string).
- Dividend fingerprints — out of scope; identical same-day dividends are rare and the column set there is already narrower in practice.

## Decisions

### 1. Hash-only, no persistence

Store nothing about `order_id` in the database. Only fold it into the SHA256 input when present.

**Why:** Smallest possible diff. Solves the stated bug without a migration. The `import_fingerprint` UNIQUE constraint already gives us the safety we need — distinct hashes ⇒ distinct rows.

**Alternative considered:** Add `transactions.order_id VARCHAR(64)` column. Rejected for v1: requires Alembic migration, partial-unique constraint design, and we have no immediate read use case. If a future feature wants to display order IDs in the transaction list, add the column then.

### 2. Optional column, lenient parsing

`order_id` is **not** required. Missing column or empty cell → fingerprint computed exactly as today.

**Why:** Existing CSVs continue to import. Pre-feature DB rows continue to dedupe correctly against any re-uploaded pre-feature CSV (same input bytes ⇒ same hash).

### 3. Empty string canonicalised to `""` in hash input

When `order_id` is absent or empty, the canonical hash string appends `""` — but only if we change the format. To preserve byte-for-byte hash compatibility with existing rows, we **conditionally** append the order-id segment:

```
canonical = "|".join([
    SOURCE_TRANSACTIONS, symbol, type_, str(quantity),
    f"{price:.4f}", trade_date.iso, f"{fee:.4f}", f"{tax:.4f}",
])
if order_id:
    canonical += f"|order_id={order_id}"
```

**Why the `order_id=` prefix:** Defence against future field additions colliding by position. If a later change appends another optional field (e.g. `broker_ref`), prefixing the segment ensures hash uniqueness across schemas.

**Alternative considered:** Always append `|<order_id_or_empty>`. Rejected: would invalidate all existing hashes when `order_id` is absent (every row in the DB today), causing every re-upload to look brand new.

### 4. Chinese header synonyms

Extend `TRANSACTION_HEADER_SYNONYMS`:
```
"委託書號": "order_id", "訂單編號": "order_id", "委託編號": "order_id",
```

And canonical English key `order_id`. Existing `_normalize_header` is already lenient about unknown columns, so existing CSVs without these headers continue to work.

### 5. Whitespace handling

`order_id` is `.strip()`-ed. Whitespace-only cells are treated as empty.

## Risks / Trade-offs

- **Residual collision when no `order_id` is supplied** → documented in user-facing copy (the import page hint). User accepts this for non-broker CSVs.
- **Mixed CSVs (some rows with `order_id`, some without)** → each row computes its own hash. Two rows in the same CSV where row A has `order_id` and row B is identical but has no `order_id` will have *distinct* hashes (one includes the `|order_id=...` segment, the other doesn't). This is the intended behavior — they should be treated as distinct rows.
- **Broker-format drift** → if a broker uses a non-string `order_id` (unlikely; Taiwan brokers use alphanumeric strings), `.strip()` on the raw cell value handles it. No format assumed.
- **Forward compat with persistence** → if we later add an `order_id` column, the hash logic is already correct; only the persist step changes.

## Migration Plan

- No DB migration required.
- No data backfill required.
- Existing rows in `transactions` keep their existing `import_fingerprint`. Re-uploading their source CSV (which has no `order_id`) computes the same hash ⇒ correctly skipped as duplicate.
- Rollback: revert the code change; hashes for rows imported under the new code that had `order_id` populated will, on re-upload, recompute without it and miss the dedupe — they'd be re-imported under a new hash. Mitigation: clean up by `DELETE FROM transactions WHERE import_fingerprint IN (...)` if rollback is needed within the same broker-CSV cycle. (Low likelihood; standard forward-only release.)

## Open Questions

- None blocking. If frontend wants to surface `order_id` in the transaction list later, that's a follow-up (requires the persistence decision above).
