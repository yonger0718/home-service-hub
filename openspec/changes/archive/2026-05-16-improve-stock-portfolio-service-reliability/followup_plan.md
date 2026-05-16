# Plan: Follow-up stock-portfolio-service improvements (2026-05-05)

## Objective
Implement follow-up tasks from `docs/stock-portfolio-service-followups.md` for the stock-portfolio-service.

## Scope
- services/stock-portfolio-service/ (only)

## Tasks
1. **Migration (DB schema)**
   - Generate Alembic migration to change `price`, `fee`, `tax` (transactions) and `amount` (dividends) from DOUBLE PRECISION to Numeric(12, 2) with USING casts.
   - Run verification.
2. **Cleanup Decimal wrappers**
   - After migration, remove `Decimal(str(...))` calls in `portfolio_service.py` where data is now properly typed.
3. **Resolve trade_date semantics (Option B)**
   - Modify `create_transaction` to resolve `trade_date` before validation.
   - Refactor `_resolve_sort_trade_date` to be a pure normalizer.
4. **Code Hygiene**
   - Expose `bootstrap_truststore` in `twse_client.py` and call it from `main.py`.
   - Update exdividend cache key in `twse_client.py` to include the URL.
   - Add warning for unknown `TWSE_SSL_VERIFY` values.
5. **Documentation**
   - Update `SPEC.md` for truststore scope and mocked-test deviation.
   - Add implementation notes for plan deviations.

## Verification
- `alembic check` should pass.
- `pytest` for existing and new regression tests.
- Manual verify schema state.

## Implementation Steps
1. Create Migration File.
2. Apply Migration.
3. Update code logic (hygiene, trade_date, Decimal cleanup).
4. Update docs and comments.
5. Verification tests.
