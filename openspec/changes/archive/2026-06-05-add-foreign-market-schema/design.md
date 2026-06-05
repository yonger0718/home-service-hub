## Context

Stock portfolio service was built TWSE-only. Symbol columns are bare strings (no exchange disambiguation), money columns are `Numeric(12,2)` (TWD-shaped — no sub-cent), and quantity is `Integer` (TW odd-lot is 1 share — no fractional). Production has multiple years of TW data + a working `fx_rate` table + a multi-currency cash subsystem.

User wants to track US (NASDAQ/NYSE) and LSE holdings without rewriting the portfolio data model. Phase 1 (this change) ships schema + minimal code branches so Phase 2 (yfinance quote fetcher, scheduler) and Phase 3 (UI for adding foreign trades, multi-currency display) layer on without further migrations.

Constraints:
- Cannot break existing TW reporting (realized P&L, dashboard chart, broker import path) during migration
- Cathay broker import is the only automated trade source today; foreign trade entry will be manual at first
- `iter_realized_events` is the single source of truth for realized P&L per the canonical-engine rule — must remain so

## Goals / Non-Goals

**Goals:**
- Schema supports `(symbol, market)` uniqueness across transactions, dividends, price_history, symbol_map, corporate_actions
- Transactions and dividends carry frozen FX rate for hybrid cost-basis (frozen cost / live market value)
- Money precision wide enough for USD cents + LSE GBp + fractional shares
- Existing TW data path unchanged in behavior; defaults preserve current results
- Realized P&L engine handles both TWD-native and FX-frozen rows through one branch

**Non-Goals:**
- No yfinance integration (Phase 2)
- No new cron jobs or quote dispatcher (Phase 2)
- No frontend changes (Phase 3)
- No foreign broker importers (deferred — IBKR / Schwab not in scope)
- No retroactive FX rate fetching (`fx_rate` table stays empty for new currencies until Phase 2)
- No `asset` table abstraction (per locked brainstorm — composite key sufficient without unified ledger)

## Decisions

### D1. Composite key `(symbol, market)` over `asset` table FK

Rejected stonk-style `asset` table because: (a) we don't have unified ledger / multi-account types that would share `asset_id`, (b) refactor would touch every query for ~5% gain over composite key, (c) ticker rename history not a current concern.

Composite key approach: add `market VARCHAR(8)` to 5 tables, default `'TW'`. Symbol uniqueness is now per-market.

Alternative considered: suffix encoding (`AAPL.US`, `BARC.L`). Rejected — breaks on tickers with native dots (`BRK.B` US, `00878.TW`).

### D2. Frozen FX rate column on transactions and dividends

`fx_rate_to_twd NUMERIC(20,8) NULL`. NULL when `currency='TWD'` (no FX involved). Populated at trade / ex-date for foreign currencies.

Hybrid model rationale:
- Cost basis must be stable → freeze at trade time
- Market value must reflect today's FX → live multiply at display time
- Frozen cost vs live market value diff = unrealized FX gain (renderable separately)

Alternative considered: always live-revalue. Rejected — realized P&L would drift daily even after position closes.

Alternative considered: always frozen. Rejected — total assets chart would not move with FX swings, misrepresenting reality.

### D3. Precision widening: `Numeric(18,4)` for price / amount / quantity

`price Numeric(12,2)` cannot hold LSE GBp values like `6414.0` losslessly above 9,999,999.99, and cannot hold sub-cent USD prices. Bumping to `(18,4)` covers:
- USD with 4dp (matches IBKR fill prices)
- LSE GBp with 1dp natively
- TWD existing data fits trivially

`quantity Integer → Numeric(18,4)` enables fractional shares (US DRIP, Robinhood). TW data backfills cleanly (integer → numeric is widening, no precision loss).

PostgreSQL behavior:
- `ALTER TYPE numeric(a,b) → numeric(c,d)` widening = metadata-only, no rewrite
- `ALTER TYPE integer → numeric(18,4)` = table rewrite (different storage). Acceptable at ~50k rows.

### D4. Realized P&L engine adds one branch, not a rewrite

`iter_realized_events` becomes:
```python
def _to_twd_per_share(row):
    if row.fx_rate_to_twd is None:
        return row.price          # TWD-native: behavior unchanged
    return row.price * row.fx_rate_to_twd
```
All downstream FIFO / proceeds / cost math stays in TWD-equivalent. TW rows hit the `is None` branch — bit-for-bit identical output.

Alternative considered: separate `iter_realized_events_foreign`. Rejected — violates single-source-of-truth rule; doubles maintenance.

### D5. Day-trade detection gates on `market == 'TW'`

`沖買/沖賣` is a TWSE-specific concept (tied to TW half-tax). US "pattern day trader" semantics are unrelated and out of scope.

Detector returns `is_day_trade=False` for any non-TW row regardless of same-day buy+sell pattern. Avoids leaking TW tax logic into foreign data.

### D6. Cathay importer explicitly stamps TW/TWD

Existing path implicitly creates TW/TWD rows. After schema change, columns have defaults, but importer should set them explicitly so future foreign importers can be added by symmetry without relying on column defaults.

```python
Transaction(
    symbol=..., market='TW', currency='TWD', fx_rate_to_twd=None, ...
)
```

### D7. Resolve `symbol_map.market` name collision

Existing `symbol_map.market` stores TWSE/TPEx (Taiwan sub-exchange) — collides with the new TW/US/LSE meaning we want to add. Rename existing column to `exchange` (more accurate for TWSE/TPEx anyway), then add fresh `market` column with TW/US/LSE semantics. All existing rows backfill `market='TW'` (twstock data is TW-only).

`market_data_service.backfill_date(market=...)` and the `/api/portfolio/price-history/backfill?market=` query param continue to accept `TWSE|TPEX|BOTH` — those represent the TW sub-exchange filter, not the new top-level market. Document the distinction; do not rename the API param in this phase. Phase 2 introduces a separate dispatcher param for top-level market routing.

### D8. Migration safety: single revision, all-or-nothing

One alembic revision applies all column adds + index/PK refresh + type widening atomically. If revision fails mid-way, transaction rolls back (PostgreSQL DDL is transactional). Downgrade reverses cleanly: drop new columns, restore old PK/index, narrow types back. Type narrowing risks data loss only if foreign rows have been inserted — at Phase 1 they haven't, so safe.

Order within the revision:
1. Add `market`, `currency`, `fx_rate_to_twd` columns with defaults (cheap)
2. Backfill is automatic from defaults — no explicit UPDATE needed
3. Drop old indexes / PKs
4. Widen types (`quantity Integer → Numeric` rewrites table)
5. Create new indexes / PKs

## Risks / Trade-offs

- **`quantity` type rewrite locks `transactions` briefly during migration** → Run during low-traffic window; ~50k rows takes seconds on PG 16; if larger, consider `pg_repack` or split into add-shadow-column + backfill + swap. Mitigation: confirm row count pre-migration, run off-hours.
- **Default-based backfill means schema is fine but no `fx_rate` rows exist for USD/GBP** → Phase 2 must backfill `fx_rate` before any foreign trade can be inserted with valid frozen rate. Mitigation: Phase 2 includes FX backfill CLI as Task 1.
- **Realized P&L test surface grows** → New unit tests for both branches required. Mitigation: parametrize existing tests with `fx_rate_to_twd` variants.
- **Downgrade path narrows `Numeric → Integer` on `quantity`** → If anyone inserts a fractional row between upgrade and downgrade, downgrade truncates silently. Mitigation: downgrade in Phase 1 only — once Phase 2/3 lands, downgrade is one-way (document in revision).
- **Composite key default `'TW'` makes accidental cross-market insert silent** → If foreign trade inserted without passing `market` arg, defaults to TW and may collide. Mitigation: Pydantic schemas require explicit `market` field (no Python-side default once Phase 2 lands); Phase 1 keeps default permissive to preserve back-compat for existing callers.

## Migration Plan

1. Apply alembic revision (atomic, single transaction)
2. Verify defaults populated by querying `SELECT DISTINCT market FROM transactions` → expect `{'TW'}`
3. Run existing test suite end-to-end (unit + integration) — must pass unchanged
4. Run realized P&L recompute via `python -m app.services.networth_backfill_service --rebuild-all --dry-run` — output must match pre-migration snapshot
5. Live deploy

Rollback (Phase 1 only):
- `alembic downgrade -1` reverses cleanly while no foreign data exists
- Once any foreign trade inserted, downgrade becomes one-way (truncation risk on `quantity`)

## Open Questions

None — all locked in brainstorm.
