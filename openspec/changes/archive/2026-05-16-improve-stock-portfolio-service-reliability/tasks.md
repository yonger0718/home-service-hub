## 1. Baseline and Preflight

- [x] 1.1 Run baseline tests: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q`; record pass/warning count in implementation notes
- [x] 1.2 Run read-only invalid data preflight SQL against the target DB for blank symbols, non-positive quantities/prices/amounts, and negative fees/taxes
- [x] 1.3 Inspect existing transaction ledger for any symbol that already goes negative under `(trade_date, id)` ordering; document findings before enabling SELL validation
- [x] 1.4 Confirm frontend behavior for DELETE response bodies; keep DELETE 200/body unchanged unless explicitly confirmed safe to change

## 2. Low-Risk Cleanup

- [x] 2.1 Convert portfolio response schemas from class-based Pydantic `Config` to `ConfigDict(from_attributes=True)`
- [x] 2.2 Delete unused `services/stock-portfolio-service/app/routers/health.py`
- [x] 2.3 Keep `tests/unit/test_health.py` passing and assert shared-lib health routes are still registered exactly once
- [x] 2.4 Rename or clarify mocked TWSE test file naming so it is not mistaken for a live e2e test; do not mark mocked tests as excluded e2e tests

## 3. Input Validation and Database Constraints

- [x] 3.1 Add Pydantic validation for transaction inputs: trimmed non-empty symbol, `quantity > 0`, `price > 0`, `fee >= 0`, `tax >= 0`
- [x] 3.2 Add Pydantic validation for dividend inputs: trimmed non-empty symbol and `amount > 0`
- [x] 3.3 Add SQLAlchemy `CheckConstraint`s matching the API invariants on `transactions` and `dividends`
- [x] 3.4 Create an Alembic migration for the new constraints, with reversible downgrade
- [x] 3.5 Add API/service tests proving invalid transaction and dividend payloads are rejected without writes
- [x] 3.6 Run Alembic upgrade/downgrade/upgrade locally in the service environment

## 4. Update Semantics and SELL Protection

- [x] 4.1 Fix `update_transaction` and `update_dividend` so omitted optional fields do not overwrite stored values with `None`
- [x] 4.2 Add tests that omitting `trade_date` on transaction update preserves existing `trade_date`
- [x] 4.3 Add tests that omitting `received_date` on dividend update preserves existing `received_date`
- [x] 4.4 Implement shared ledger availability validation for SELL create/update using normalized symbol and deterministic `(trade_date, id)` ordering
- [x] 4.5 Reject SELL without holdings and SELL greater than available shares with HTTP 400
- [x] 4.6 On update, exclude the original transaction, validate the proposed replacement ledger, and preserve the previous row if validation fails
- [x] 4.7 Add tests for SELL without holdings, oversell, valid partial SELL, update causing oversell, and same-day ordering

## 5. Shared Holdings Aggregation

- [x] 5.1 Extract a reusable active holdings aggregation helper from current summary/ex-dividend logic
- [x] 5.2 Update `get_portfolio_summary` to use the shared holdings helper without changing existing numeric output semantics
- [x] 5.3 Update upcoming ex-dividend route/service path to use the same active holdings helper
- [x] 5.4 Add tests proving summary and ex-dividend filtering agree on active symbols after BUY/SELL sequences
- [x] 5.5 Add tracing attributes for transaction count, dividend count, active symbol count, quote count, and quote status where available

## 6. TWSE TLS, Truststore, Retry, and Cache

- [x] 6.1 Add `truststore` to `services/stock-portfolio-service/requirements.txt`
- [x] 6.2 Create a stock-service-scoped TWSE client/helper module; inject `truststore` there idempotently, not in `shared_lib.create_app`
- [x] 6.3 Implement `TWSE_TLS_MODE` with default `fallback`, plus `verify` and `insecure` modes
- [x] 6.4 In fallback mode, attempt `verify=True` first and retry once with `verify=False` only for `requests.exceptions.SSLError`
- [x] 6.5 Ensure timeout, limited retry/backoff, warning logs, and tracing metadata are applied consistently by the shared TWSE client
- [x] 6.6 Update quote fetching to use the shared TWSE client while preserving quote parsing behavior
- [x] 6.7 Update ex-dividend fetching to use the shared TWSE client while preserving ex-dividend parsing behavior
- [x] 6.8 Add in-process TTL cache for quotes keyed by normalized symbol set; use a configurable TTL in the 15-60 second range
- [x] 6.9 Add in-process TTL cache for the TWSE ex-dividend source table; use a configurable TTL in the 15-60 minute range
- [x] 6.10 Add tests for default fallback mode, verify mode, insecure mode, SSLError-only fallback, non-TLS failure behavior, and cache hit/expiry behavior

## 7. Quote Status and Observability

- [x] 7.1 Add an additive `PortfolioSummary` quote status field, choosing a final name such as `quotes_status`
- [x] 7.2 Report quote status `ok` when every active holding has quote data
- [x] 7.3 Report quote status `partial` when only some active holdings have quote data
- [x] 7.4 Report quote status `unavailable` when active holdings exist but no quote data is available, while preserving safe numeric fallback behavior
- [x] 7.5 Demote per-symbol TWSE quote parse logs from INFO to DEBUG
- [x] 7.6 Add or keep aggregate quote parse/fetch logging at INFO where useful
- [x] 7.7 Add tests for quote status values and fallback summary behavior

## 8. List Endpoint Maintainability

- [x] 8.1 Move transaction listing query from router into `portfolio_service.list_transactions`
- [x] 8.2 Move dividend listing query from router into `portfolio_service.list_dividends`
- [x] 8.3 Add bounded `limit` and `offset` query parameters to both list endpoints, with documented default and max values
- [x] 8.4 Add optional normalized `symbol` filter to both list endpoints
- [x] 8.5 Add tests for default list behavior, pagination, bounds handling, ordering, and symbol filtering

## 9. Dividend Semantics Documentation

- [x] 9.1 Decide whether `total_dividends` means lifetime dividends or active-holdings dividends for the current API
- [x] 9.2 Update `services/stock-portfolio-service/SPEC.md` to document the chosen `total_dividends` semantics
- [x] 9.3 If both lifetime and active dividends are needed, add new response fields additively and preserve `total_dividends` until frontend migration
- [x] 9.4 Add tests covering dividends from closed positions according to the documented semantics

## 10. Optional Index Migration

- [x] 10.1 Add date/list-query indexes only if implementation confirms they help the new list/filter query paths
- [x] 10.2 If added, create a reversible Alembic migration for `transactions(trade_date)`, `dividends(ex_dividend_date)`, and optional `transactions(symbol, trade_date)`
- [x] 10.3 Run Alembic upgrade/downgrade/upgrade for the index migration

## 11. Final Verification

- [x] 11.1 Run `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q`
- [x] 11.2 Run Alembic upgrade/downgrade/upgrade after all migrations
- [x] 11.3 Smoke `/health`, `/health/ready`, `/api/portfolio/summary`, and `/api/portfolio/ex-dividends/upcoming`
- [x] 11.4 Verify `TWSE_TLS_MODE=fallback` emits observable metadata on fallback without breaking successful verified requests
- [x] 11.5 Update implementation notes with any deferred decisions: DELETE 204, dividend semantics, cache TTL values, and fallback-rate follow-up
