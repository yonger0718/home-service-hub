# Implementation Notes

## Baseline

- 2026-05-04: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q`
- Result: 21 passed, 2 warnings
- Warnings: `app/schemas/portfolio.py` emitted two `PydanticDeprecatedSince20` warnings for class-based `Config`

## Preflight

- 2026-05-04: read-only invalid data query against the configured stock PostgreSQL DB returned `0` invalid transaction rows and `0` invalid dividend rows
- 2026-05-04: read-only ledger scan under `(trade_date, id)` ordering returned no negative-holdings symbols
- 2026-05-04: frontend stock portfolio delete flows use `Observable<void>` and ignore DELETE response bodies, so moving to 204 is frontend-safe if chosen later

## Migrations

- 2026-05-04: `cd services/stock-portfolio-service && .venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head` passed after switching downgrade to `op.drop_constraint(..., type_="check")`
- 2026-05-04: final migration verification passed with `alembic upgrade head && alembic downgrade 570991c7b5b8 && alembic upgrade head`

## TWSE Runtime Defaults

- Added `truststore==0.10.4` and a stock-service-scoped `twse_client` helper with idempotent bootstrap
- `TWSE_TLS_MODE` default: `fallback`
- Quote cache TTL default: `30` seconds via `TWSE_QUOTE_CACHE_TTL_SEC`
- Ex-dividend cache TTL default: `900` seconds via `TWSE_EXDIVIDEND_CACHE_TTL_SEC`
- Verified fallback observability via `tests/unit/test_twse_client.py::test_fallback_mode_sets_observable_span_attributes`

## Final Verification

- 2026-05-04: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q` → `65 passed`
- 2026-05-04: TestClient smoke checks returned `200` for `/health`, `/health/ready`, `/api/portfolio/summary`, `/api/portfolio/ex-dividends/upcoming`

## Plan Deviations

- 2026-05-05: Replaced live e2e TWSE tests with mocked unit tests (`tests/unit/test_twse_service_mocked.py`) instead of marking them with `@pytest.mark.e2e`. Live verification is now only done via the post-deploy smoke checks.
- 2026-05-05: `truststore` is injected at stock-portfolio-service process startup. The injection is process-global by design — it affects all HTTPS clients in this service, not just TWSE.

## Numeric Migration Preflight (d1e2f3g4h5i6)

- 2026-05-05: After applying `d1e2f3g4h5i6_migrate_to_numeric_types`, truncation preflight on the configured DB returned 0 rows for all four columns:

      select count(*) from transactions where price is not null and price <> round(price::numeric, 2);  -- 0
      select count(*) from transactions where fee   is not null and fee   <> round(fee::numeric,   2);  -- 0
      select count(*) from transactions where tax   is not null and tax   <> round(tax::numeric,   2);  -- 0
      select count(*) from dividends    where amount is not null and amount <> round(amount::numeric, 2); -- 0

  Conclusion: the DOUBLE → NUMERIC(12,2) cast did not silently truncate any existing values. The migration body now carries an inline WARNING with the same preflight, which must be re-run by anyone replaying the migration against another environment.