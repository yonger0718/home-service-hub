## 1. Pre-work — confirm divergence

- [x] 1.1 Write failing unit test `tests/unit/test_dividend_auto_record_service.py::test_qty_held_on_skips_short_positions` with fixture: same symbol, LONG BUY 1000 + SHORT SELL 500 trades before ex_date. Assert `_qty_held_on` returns 1000, NOT 500. Must FAIL on current code.
- [x] 1.2 Companion failing test: LONG BUY 1000 + SHORT BUY 500 (cover) → `_qty_held_on` returns 1000, NOT 1500.

## 2. Fix _qty_held_on

- [x] 2.1 In `app/services/dividend_auto_record_service.py`, import `PositionSide` from `..models.portfolio`.
- [x] 2.2 Add `Transaction.position_side == PositionSide.LONG` predicate to the `buy_total` `select(...).where(...)`.
- [x] 2.3 Add identical predicate to the `sell_total` query.
- [x] 2.4 Run failing tests from 1.1, 1.2 — must now PASS.
- [x] 2.5 Confirm docstring still matches behavior (mentions "BUY=+, SELL=−"); append "LONG side only" clarification.

## 3. Tests

- [x] 3.1 Unit test: SHORT-only history (no LONG transactions) → `auto_record_for_event` returns `skipped_reason="no_holding"`, no Dividend row written.
- [x] 3.2 Unit test: mixed LONG+SHORT, ex_date BETWEEN SHORT open and SHORT close → qty equals LONG net only; dividend amount unaffected by 融券 position.
- [x] 3.3 Unit test: stock-dividend leg with mixed positions — gifted shares computed from LONG qty only.
- [x] 3.4 Regression: existing tests in `test_dividend_auto_record_service.py` still pass (no LONG-only fixture changes intended).
- [x] 3.5 Integration test: `auto_record_for_event` with Cathay 融券 + LONG buy on same symbol before ex_date → recorded `dividends.amount` matches LONG-only qty * cash_per_share − fee − tax. (Scope: direct call; `_step_dividends` is a thin loop over `auto_record_for_event` and is covered by existing `test_post_import_orchestrator.py` cases that mock `auto_record_for_event` — no new chain-level test added.)

## 4. Re-record migration (manual operator step)

- [ ] 4.1 Document re-record SQL/CLI in `tasks.md` (this file, below): operator deletes affected `auto:*` Dividend rows + `auto-stk-div:*` Transaction rows for symbols where user had concurrent LONG+SHORT, then re-runs `POST /api/portfolio/dividends/backfill`.
- [ ] 4.2 After dividend re-record, operator runs `python -m app.services.networth_backfill_service --rebuild-all` to refresh `portfolio_snapshot.total_dividends`.

### Re-record SQL (dev DB, manual)

Note: `import_fingerprint` is a SHA256 hex digest, NOT the raw `auto-stk-div:...` string — `LIKE 'auto-stk-div:%'` matches zero rows. `dividends.source` IS stored verbatim (`auto:TWT49U` etc.), so `source LIKE 'auto:%'` is correct. For auto-recorded stock-dividend `Transaction` rows, filter by `price = 0` — `dividend_auto_record_service._insert_stock` is the only zero-price Transaction writer in the codebase.

```sql
-- Identify candidate symbols (those with both LONG and SHORT trades)
SELECT DISTINCT symbol
FROM transactions
WHERE position_side = 'SHORT'
  AND symbol IN (
    SELECT symbol FROM transactions WHERE position_side = 'LONG'
  );

-- Per-symbol cleanup (example for one symbol)
DELETE FROM dividends
WHERE symbol = :sym
  AND source LIKE 'auto:%';

DELETE FROM transactions
WHERE symbol = :sym
  AND price = 0
  AND type = 'BUY'
  AND import_fingerprint IS NOT NULL;
```

Then: `curl -X POST http://localhost:8001/api/portfolio/dividends/backfill` to repopulate, followed by snapshot rebuild.

## 5. Cleanup + verification

- [x] 5.1 Run `pytest tests/unit tests/integration` — all green.
- [x] 5.2 `openspec validate fix-dividend-qty-skip-short --strict`.
- [ ] 5.3 Dev DB: identify affected symbols via SQL in 4.1, run re-record + snapshot rebuild, eyeball `/api/portfolio/dividends?symbol=...` for one affected symbol, confirm amount changed in expected direction.
- [ ] 5.4 Commit; PR.
- [ ] 5.5 After merge: operator re-record + snapshot rebuild on prod in maintenance window.
