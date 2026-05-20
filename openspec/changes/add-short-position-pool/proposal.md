## Why

Realized P&L compute (`iter_realized_events`) tracks only a long-side pool. SELL with no inventory is reported as full-proceeds gain — wrong for `券賣` (short open: no realized gain, opens a short position) and for `券買` (short cover: realizes gain vs short cost basis, not a "buy"). Cathay CSV parser already reads `買賣別` (8 sides incl. 資/券) but discards the subtype, so the database cannot distinguish short rows. CodeRabbit flagged this oversell behavior in PR #12; correct accounting needs long+short pool semantics and a `position_side` field on transactions.

## What Changes

- Add `position_side` column (`LONG` | `SHORT`, default `LONG`) to `transactions` via Alembic migration; legacy rows backfill `LONG`.
- Cathay CSV parser writes `position_side` from `買賣別`: 現/資/沖 → `LONG`; 券 → `SHORT`.
- Fold 利息 + 券手續費/標借費 into existing `fee` at parse — no new columns.
- `iter_realized_events` maintains two pools per symbol (long + short); routes by `position_side`. `SHORT SELL` opens, `SHORT BUY` covers; gain math inverts.
- `RealizedPnlEvent` and `RealizedPnlEventOut` gain `position_side` field.
- Edge cases: `LONG SELL` with empty long pool → `note="no_long_inventory"` (preserves current oversell flag); `SHORT BUY` with empty short pool → `note="no_short_inventory"`.
- UI: 融券 badge on `position_side=SHORT` rows in `realized-pnl` and `transaction-list` cards.
- **BREAKING (internal API only)**: `RealizedPnlEvent` dataclass + `RealizedPnlEventOut` schema add required `position_side` field. No public REST contract removed.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stock-portfolio-realized-pnl`: dual-pool semantics; new `position_side` event field; revised no-inventory note taxonomy.
- `stock-portfolio-broker-cathay-import`: writes `position_side` from `買賣別`; folds 利息 / 券手續費 into `fee`.
- `stock-portfolio-data-integrity`: `transactions.position_side` column added with NOT NULL + default `LONG`.

## Impact

- **Backend**: `app/models/portfolio.py` (column + enum), `app/services/realized_pnl_service.py` (dual pool), `app/services/broker_cathay_service.py` (subtype routing + fee folding), `app/services/portfolio_service.py` (oversell guard in `_step_transactions` mirror), `app/schemas/realized_pnl.py` (+ `position_side`), Alembic new migration.
- **Frontend**: `models/portfolio.model.ts` (+ `position_side`), `components/portfolio/realized-pnl/*`, `components/portfolio/transaction-list/*`.
- **Data**: ~2150 legacy rows backfill `LONG`. Sample CSV (2026-05-08) contains 4 真實 短 rows (技嘉, 漢磊). User re-imports CSV after migration to recover correct `SHORT` classification.
- **Tests**: new `tests/unit/test_realized_pnl_short_pool.py`, new `tests/unit/test_cathay_position_side.py`, extend invariant test, extend Cathay integration test.
- **Risk**: low — short-row population is 0.2% of data; long-only paths regression-tested via existing invariant.
