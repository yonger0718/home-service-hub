## Why

CSV import currently writes rows and stops. The portfolio summary, dividend events, and networth chart do not refresh until the next scheduled cron fires — so a user who imports two years of backdated history sees an empty chart and stale holdings for hours. The CSV upload page is also unreachable from the main navigation, so the flow is effectively hidden.

## What Changes

- Add a post-import orchestration step (`recalc_after_import`) that runs synchronously in a FastAPI `BackgroundTasks` slot right after a successful CSV commit.
- Chain steps:
  1. **Guard**: skip entirely if `inserted_count == 0` (re-upload of identical CSV).
  2. **Symbol-name backfill**: resolve any newly-imported Chinese-named symbols against `symbol_map`.
  3. **Dividend re-fetch**: call `dividend_event_service` for each newly-touched symbol (covers ex-dividend dates in the imported range).
  4. **Networth backfill**: fire `networth_backfill_service.backfill_range(start=min(new_tx.trade_date), end=today)`.
- Expose CSV upload in primary navigation (top-level link in `app.html`, route already at `/portfolio/import`).
- Add a `POST /api/portfolio/imports/recalc` endpoint to manually re-trigger the chain (no CSV needed) — used for retries when a chain step fails.
- Surface chain progress + result in the import page UI (toast on completion, error banner on partial failure).

## Capabilities

### New Capabilities
- `stock-portfolio-import-orchestration`: defines the post-import recalc chain, its guard conditions, ordering, failure semantics, and the manual re-trigger endpoint.

### Modified Capabilities
<!-- None. Existing networth-backfill / scheduling / symbol-resolver specs are reused as-is; orchestration just calls into them. -->

## Impact

- Backend (`services/stock-portfolio-service`):
  - NEW `app/services/post_import_orchestrator.py`
  - MODIFIED `app/routers/imports.py` (wire `BackgroundTasks`, add `/recalc` endpoint)
  - Reuses (no edits): `symbol_map_service`, `dividend_event_service`, `networth_backfill_service`.
- Frontend (`frontend/src/app`):
  - MODIFIED `app.html` (add nav link)
  - MODIFIED `components/portfolio/import/import.{ts,html}` (toast + recalc status)
- No schema changes. No new dependencies.
- Out of scope (separate change): fingerprint collision fix for identical same-day fills — tracked as `fix-import-fingerprint-add-order-id`.
