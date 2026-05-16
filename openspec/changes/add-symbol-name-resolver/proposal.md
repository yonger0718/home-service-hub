## Why

Imported transactions from broker CSVs sometimes use Chinese stock names as the `symbol` field instead of the ticker (e.g. `鴻海` rather than `2317`). When that happens the rest of the system — quote fetcher, corporate-action lookup, networth chart — has no way to look up the position, leaving holdings displayed without prices, PnL, or dividends.

We have one shipped artifact: the recent stonk reimport surfaced 172 unique Chinese-name "symbols" across 1958 transaction rows. Going forward, any new broker CSV that ships names instead of tickers will repeat the problem.

## What Changes

- **`symbol_map` table** — `name` (PK, citext), `symbol`, `market`, `updated_at`. One row per Chinese name → ticker mapping.
- **`symbol_map_service`** — wraps the `twstock` Python library:
  - `refresh_all_from_twstock(db)` — call `twstock.__update_codes()` then iterate `twstock.codes`, upsert one row per `(name, symbol, market)`. Idempotent across re-runs via `Session.merge`.
  - `resolve_name(db, name) -> Optional[str]` — return ticker or `None`.
  - `backfill_transactions(db, *, dry_run) -> dict` — iterate `transactions` rows whose `symbol` matches a known name, rewrite `symbol` to the resolved ticker and recompute `import_fingerprint` over the new value. Returns counts (`updated`, `unresolved`, `collisions`). Skips writes when `dry_run=True`.
- **Weekly scheduler job** — `symbol_map_refresh` runs Monday 06:00 Asia/Taipei (before market open). Reuses the existing APScheduler from `scheduler.py`.
- **Endpoints** — `POST /api/portfolio/symbol-map/refresh` (manual refresh) and `POST /api/portfolio/symbol-map/backfill?dry_run=` (manual rewrite).
- **`requirements.txt`** — add `twstock>=1.3`.

### Out of scope

- Frontend UI for the map (admin-only feature; CLI/API is enough).
- Warrants, futures, and structured products — `twstock.codes` covers TWSE/TPEx equities + ETFs only; those names will remain unresolved and be reported in `unresolved`.
- Live name-resolution during CSV upload — the importer keeps its current behaviour; backfill is run separately.
- Reverse direction (ticker → name) — not needed; `transactions.name` already stores the display name.

## Capabilities

### New Capabilities

- `stock-portfolio-symbol-resolver`: maintain a cached Chinese-name → ticker map sourced from twstock with weekly refresh, used to backfill phantom symbols on imported transactions.

### Modified Capabilities

- None.
