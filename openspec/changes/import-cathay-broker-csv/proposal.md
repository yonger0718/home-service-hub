## Why

Real broker CSV from 國泰證券 (1997 rows in user's dataset) cannot be imported as-is. It has no `symbol` column (only `股名`), uses Chinese type vocabulary (`現買`/`現賣`/`資買`/`資賣`/`券買`/`券賣`/`沖買`/`沖賣`), and shipping `委託書號` per row. Even after the fingerprint fix (PR #5), re-uploading this CSV against a populated database would insert 1997 duplicates because the new hash (with `order_id`) differs from any legacy hash for rows already entered manually under a stripped-down schema.

This change accepts the 國泰 broker CSV verbatim and provides a one-shot **smart rehash backfill** that matches CSV rows against existing transactions on `(symbol, trade_date, type, quantity, price, fee, tax)` and rewrites their `import_fingerprint` to fold in `order_id` — without inserting duplicates.

## What Changes

- Accept Chinese name (`股名`) and resolve to ticker via a bundled `name_to_symbol` reverse map derived from a code/name JSON asset.
- Map 國泰 type vocabulary to home-hub's `BUY` / `SELL` enum:
  - `現買` / `資買` / `券買` / `沖買` → `BUY`
  - `現賣` / `資賣` / `券賣` / `沖賣` → `SELL`
- Surface the 國泰 sub-type (`現` / `資` / `券` / `沖`) into the parsed `payload` for future use but do **not** persist it (no schema change).
- Recognise 國泰's preamble row (`根據您篩選的結果...`) and skip it before reading the header.
- Add smart-rehash backfill into the cathay parser path (no new endpoint, no query param — auto-applied whenever `detect_csv_format` returns `cathay`). Existing `POST /api/portfolio/imports/transactions` handles both generic CSVs and 國泰 CSVs transparently. For each cathay-CSV row:
  1. Computes the **legacy** fingerprint (no `order_id`).
  2. Looks up an existing `transactions` row by that fingerprint.
  3. If found: recomputes the **new** fingerprint (with `order_id`) and `UPDATE`s the row's `import_fingerprint`.
  4. If not found: inserts a new transaction the normal way (so genuinely-new rows still land).
- Surface name-collision errors loudly: if a Chinese name maps to multiple tickers, abort with a row-level error listing the candidates.

## Capabilities

### New Capabilities

- `stock-portfolio-broker-cathay-import`: 國泰證券-specific CSV ingestion — preamble handling, Chinese name → symbol resolution, Chinese type vocabulary mapping, smart-rehash backfill for existing rows.

### Modified Capabilities

- None. This capability is additive — generic CSV path remains intact and `stock-portfolio-data-integrity`'s fingerprint contract (sealed in PR #5) is reused unchanged.

## Impact

- **Backend** (`services/stock-portfolio-service/`): new `app/services/broker_cathay_service.py`, new `app/data/name_to_symbol.json` (asset). Extends the existing `POST /api/portfolio/imports/transactions` route with format auto-dispatch (preamble sniff in `detect_csv_format`) — no new endpoint; generic parsing stays behind the same path. One new endpoint `POST /api/portfolio/imports/verify-symbol` is added strictly for per-row override verification.
- **Frontend** (`frontend/src/app/components/portfolio/import/`): one new result-panel summary line `重算指紋 N 筆` when auto-rehash ran. No new toggles; existing upload UX unchanged.
- **No DB migration.** Hash-only design preserved.
- **No new dependencies.**
- **Limitations** (documented, not bugs): margin/short/day-trade sub-type not persisted — all collapsed to BUY/SELL. Single-name → multi-symbol collisions fail loudly rather than guess.
