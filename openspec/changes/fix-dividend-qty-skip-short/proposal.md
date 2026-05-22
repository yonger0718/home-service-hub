## Why

`dividend_auto_record_service._qty_held_on` predates the `position_side` (LONG/SHORT) feature. It sums all `Transaction.quantity` rows by `type=BUY` minus `type=SELL` with no `position_side` filter. Cathay 融券 SELL legs subtract from the LONG count; 融資/沖買 close BUY legs inflate it. Both pollute the qty used to compute dividend amount.

Concretely:

| Real holdings | `_qty_held_on` returns | Effect |
|---|---|---|
| LONG BUY 1000 + 融券 SELL 500 | 1000 − 500 = 500 | Dividend amount halved |
| LONG BUY 1000 + 融券 BUY 500 (cover) | 1000 + 500 = 1500 | Dividend amount inflated 50% |
| SHORT-only (融券 SELL 1000, no LONG) | −1000 | Skipped `no_holding` (correct outcome by coincidence) |

This is the same LONG/SHORT-blindness pattern fixed in `2026-05-21-unify-realized-pnl-snapshot` for `networth_backfill_service`. Same root cause, different consumer.

Wrong qty propagates to:
- Cash leg `gross = qty * cash_per_share` → wrong `amount` in `dividends.amount`
- Stock leg `extra_shares = floor(qty * stock_per_thousand / 1000)` → wrong gifted shares in `transactions`
- Both written with idempotent fingerprint, so a later fix without re-record produces stale rows.

Triggered every time:
- 18:00 TW cron `run_dividend_auto_record` runs while user holds both LONG and SHORT same symbol
- CSV import chain `_step_dividends` runs for touched symbols within recalc range
- Manual `POST /api/portfolio/dividends/backfill` walks history with mixed positions

## What Changes

- Add `Transaction.position_side == PositionSide.LONG` filter to both `buy_total` and `sell_total` queries in `_qty_held_on`
- Update existing tests; add regression cases: mixed LONG+SHORT, SHORT-only, SHORT-close inflation scenario
- One-shot rebuild path: delete `auto:*` `Dividend` rows + `auto-stk-div:*` `Transaction` rows for affected symbols, then re-run `POST /api/portfolio/dividends/backfill`. Documented in tasks.md.
- After re-record, run `python -m app.services.networth_backfill_service --rebuild-all` to refresh snapshots that consumed the wrong dividend rows.

## Capabilities

### Modified Capabilities
- `stock-portfolio-auto-record-dividends`: `_qty_held_on` MUST count only `position_side='LONG'` rows; SHORT 融券/融資 transactions MUST NOT shift the LONG qty used for dividend amount.

### New Capabilities
(none)

## Impact

- Code: `services/stock-portfolio-service/app/services/dividend_auto_record_service.py` (`_qty_held_on`)
- Data: existing `dividends.amount` rows written by the buggy path are stale until re-record migration runs. Affects only users with both LONG + SHORT positions on the same symbol on or before any ex-date in their history.
- API: no shape change. `/api/portfolio/dividends` and `/api/portfolio/upcoming-events` values may change post-rebuild.
- Tests: `tests/unit/test_dividend_auto_record_service.py`, `tests/unit/test_dividends_backfill_router.py`, `tests/unit/test_post_import_orchestrator.py`
- Downstream: `portfolio_snapshot.total_dividends` consumes `dividends.amount`; needs snapshot rebuild after dividend re-record. Chart 累積總損益 line auto-corrects after snapshot rebuild.
- No schema change; no breaking API change (response shape unchanged, values corrected).
