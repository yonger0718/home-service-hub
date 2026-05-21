## Why

`_recompute_day_trade_flags` (portfolio_service.py:216) flags any same-symbol same-day BUY+SELL pair as `is_day_trade=true`, regardless of instrument type. Per TW FSC rules, **認購(售)權證 / 牛熊證** are NOT day-trade eligible — they trade T+2 settled but cannot be 現股當沖. The current logic mis-flags warrant round-trips (observed on symbol `045378`), polluting the 已實現損益 page's `當沖` badge and any future tax-form aggregations.

## What Changes

- Add `type` column to `symbol_map` table (e.g., "股票", "認購權證", "認售權證", "牛證", "熊證") populated from `twstock.codes[<symbol>].type` during refresh.
- New helper `is_day_trade_eligible(db, symbol) -> bool` in `symbol_map_service` — returns False for warrants/牛熊證, True otherwise (including unmapped symbols — fail-open to preserve legacy behavior on missing data).
- `_recompute_day_trade_flags` gates the `True` assignment on eligibility — ineligible symbols always get `is_day_trade=False`.
- One-shot Alembic data migration: scan every transaction grouped by `(symbol, calendar_date)`, recompute the flag with the eligibility gate in-place. Fixes existing wrong flags on warrant rows.
- Unit + integration tests covering: warrant pair stays False, equity pair stays True, unmapped symbol stays True, migration rewrite.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `stock-portfolio-realized-pnl`: day-trade flag semantics narrowed — warrant rows can never be flagged. Affects `Day-trade flag is propagated` scenario context; add new requirement for instrument eligibility.
- `stock-portfolio-symbol-resolver`: symbol_map schema gains `type` column and the resolver exposes eligibility lookup.

## Impact

- **DB schema**: `symbol_map.type VARCHAR(32) NULL` (Alembic migration).
- **Backend**: `app/models/symbol_map.py`, `app/services/symbol_map_service.py` (refresh + new helper), `app/services/portfolio_service.py` (`_recompute_day_trade_flags`).
- **Data migration**: One-shot rewrite of `transactions.is_day_trade` for currently-flagged warrant rows.
- **Tests**: new unit tests in `tests/unit/test_day_trade_eligibility.py`, extend `tests/unit/test_symbol_map_service.py`.
- **Frontend**: none (badge already gated on `is_day_trade`; backend correction is sufficient).
- **External**: relies on bundled `twstock==1.5.1` codes DB; offline-safe (no API call).
