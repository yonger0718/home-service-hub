## Why

The current CSV transaction fingerprint is a SHA256 over `(symbol, type, quantity, price, trade_date, fee, tax)`. Two real-world identical fills on the same day — e.g. two BUY orders of 1000 shares of `0050` at NTD 50 on the same date — produce the **same** hash, so the second is silently dropped as a "duplicate" on import. This is a correctness bug: legitimate trades disappear.

Taiwan broker exports (Cathay, SinoPac, Yuanta, etc.) include a per-order identifier (委託書號 / 訂單編號). When present, it uniquely disambiguates fills even when every other column matches. We should fold it into the fingerprint when it is supplied.

## What Changes

- Add `order_id` as an **optional** CSV column for transaction imports (canonical English key + Chinese synonyms `委託書號`, `訂單編號`).
- Extend `_transaction_fingerprint` to include `order_id` in the canonical hash input when supplied.
- When `order_id` is absent (column missing or cell empty), the fingerprint MUST match today's hash so existing imported rows continue to dedupe correctly — no DB rewrite, no false re-imports.
- Document the residual limitation in user-facing copy: without `order_id`, identical same-day fills will still collide and the second is dropped.
- No DB schema change in this iteration. `order_id` is hash-only — not persisted as its own column. (Persistence can be added later if audit needs arise.)

## Capabilities

### New Capabilities
- _none_

### Modified Capabilities
- `stock-portfolio-data-integrity`: extend the import-idempotency requirement so that an optional per-row identifier disambiguates otherwise-identical rows.

## Impact

- `services/stock-portfolio-service/app/services/import_service.py` — fingerprint signature, header synonyms, parse loop.
- `services/stock-portfolio-service/tests/unit/test_import_service.py` — new test cases.
- Frontend hint copy in `frontend/src/app/components/portfolio/import/import.ts` (kindOptions) — surface the optional `order_id` column.
- No migration, no API surface change, no breaking change for callers that don't supply `order_id`.
