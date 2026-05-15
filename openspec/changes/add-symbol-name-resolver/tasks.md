## 1. Model + Migration

- [x] 1.1 New `app/models/symbol_map.py`: `SymbolMap(name VARCHAR PK, symbol VARCHAR NOT NULL, market VARCHAR(8) NOT NULL, updated_at TIMESTAMP WITH TIME ZONE default now())`. Index on `symbol`. Use case-insensitive PK (PostgreSQL `citext` or `LOWER(name)` unique index — pick whichever the rest of the codebase already uses; default to `LOWER(name)` unique index for portability with SQLite tests).
- [x] 1.2 Register the model in `app/main.py` and `alembic/env.py`.
- [x] 1.3 Alembic revision `k8f9a0b1c2d3_add_symbol_map_table` with reversible downgrade.

## 2. Service

- [x] 2.1 New `app/services/symbol_map_service.py`:
  - `refresh_all_from_twstock(db: Session) -> dict` — call `twstock.__update_codes()`, iterate `twstock.codes.items()`, upsert one row per `(name, symbol, market)` via `Session.merge`. Skip rows with empty/None name. Return `{"refreshed_count": int}`.
  - `resolve_name(db: Session, name: str) -> Optional[str]` — query by lowercase name match.
  - `backfill_transactions(db: Session, *, dry_run: bool = False) -> dict` — scan `transactions` where `symbol` is non-numeric (regex check) and resolvable; rewrite `symbol` to ticker. `import_fingerprint` is NOT recomputed so future re-imports of the original CSV still dedupe. Return `{"updated": int, "unresolved": list[str], "collisions": list[int], "dry_run": bool}` (`collisions` is reserved for future use and currently always empty).

## 3. Scheduler

- [x] 3.1 In `app/services/scheduler.py`, register `symbol_map_refresh` cron job: `day_of_week='mon'`, `hour=6`, `minute=0`, timezone `Asia/Taipei`. Calls `symbol_map_service.refresh_all_from_twstock`. Catches all exceptions and logs `scheduler.symbol_map_refresh.failed` with the source name.
- [x] 3.2 Add the job to the startup log line (`event=scheduler.started`).

## 4. Endpoints

- [x] 4.1 New `app/routers/symbol_map.py`:
  - `POST /api/portfolio/symbol-map/refresh` — invokes `refresh_all_from_twstock`. Returns the service result.
  - `POST /api/portfolio/symbol-map/backfill?dry_run=true|false` — invokes `backfill_transactions`. Returns the service result.
- [x] 4.2 Register the router in `app/main.py`.

## 5. Dependency

- [x] 5.1 Add `twstock>=1.3` to `requirements.txt`.
- [x] 5.2 Install into the existing virtualenv (`./.venv/bin/pip install -r requirements.txt`).

## 6. Tests

- [x] 6.1 `tests/unit/test_symbol_map_service.py`:
  - `refresh_all_from_twstock` upserts and is idempotent across two runs (patch `twstock.codes` with a 3-entry dict).
  - `resolve_name` returns ticker for known name, `None` for unknown.
  - `backfill_transactions` rewrites a Chinese-named row to its ticker and updates `import_fingerprint`; leaves untouched a row whose name has no map entry; surfaces `collisions` when the rewritten fingerprint already exists.
  - `backfill_transactions(dry_run=True)` makes no DB writes.
- [x] 6.2 Endpoint test: `POST /symbol-map/backfill?dry_run=true` returns counts without persisting.
- [x] 6.3 Mock `twstock.__update_codes` in scheduler test — verify the job is registered and runs without raising.

## 7. Verification

- [x] 7.1 Full `pytest` passes.
- [x] 7.2 Manual: hit `POST /api/portfolio/symbol-map/refresh` and verify `symbol_map` table populated (`SELECT COUNT(*) FROM symbol_map` > 1500).
- [x] 7.3 Manual: `POST /api/portfolio/symbol-map/backfill?dry_run=true` reports the number of resolvable Chinese-named rows for the current dataset, then `dry_run=false` rewrites them. Confirm `transactions.symbol` no longer contains Chinese names for resolvable cases.
