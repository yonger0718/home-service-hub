## Why

After `detect-partial-phase1-fetch` shipped, the partial-fetch gate now blocks future poisoning — but the database still holds 6 pre-existing sentinel rows (close=10.0000) on three real TW market holidays (2026-04-03 Children's Day makeup, 2026-04-06 Tomb Sweeping makeup, 2026-05-01 Labour Day). Those rows were inserted before the gate existed and will never be refreshed (`_existing_price_dates()` treats their presence as "already fetched"), so they permanently distort any backfill or chart that touches those dates.

## What Changes

- One-shot deletion of the 6 known poisoned `price_history` rows: `(date, source)` ∈ {2026-04-03, 2026-04-06, 2026-05-01} × {TWSE, TPEx}.
- Pure DELETE, no refetch — these are real TW market holidays with no upstream OHLC data.
- Operational script under `services/stock-portfolio-service/scripts/` so the action is reviewable, repeatable in non-prod, and ignored by SQLAlchemy/Alembic.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `stock-portfolio-data-integrity`: add requirement that holiday-only `price_history` rows (no real upstream data) must be absent so the partial-fetch gate's downstream consumers see a clean baseline.

## Impact

- Affected data: `price_history` table, 6 rows removed (2 sources × 3 dates).
- Affected code: new `scripts/cleanup_historical_partial_dates.py` (or `.sql`); no application code changes.
- No downstream cascade: `portfolio_snapshot` has zero rows on those dates, no FK from other tables to `(symbol, date)`.
- Reversible: rows can be recreated from a DB backup if needed (they have no business meaning to lose).
