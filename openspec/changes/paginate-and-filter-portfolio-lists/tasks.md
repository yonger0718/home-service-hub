## 1. Backend schemas

- [x] 1.1 Add `PagedTransactions` and `PagedDividends` Pydantic classes to `services/stock-portfolio-service/app/schemas/portfolio.py` (`items: list[T]`, `total: int`).
- [x] 1.2 Add `TransactionSortField` and `DividendSortField` literal types (the allowlists from design.md) used by the router for param validation.

## 2. Backend service layer

- [x] 2.1 Change `portfolio_service.list_transactions(...)` signature to `(db, *, symbol, date_from, date_to, side, sort_field, sort_dir, offset, limit) -> tuple[list[Transaction], int]`. Build the WHERE clause once; emit a `SELECT COUNT(*)` and the paged `SELECT` against the same filters. Append `id desc` tie-breaker to every sort.
- [x] 2.2 Change `portfolio_service.list_dividends(...)` the same way with `source` instead of `side` and `ex_dividend_date` instead of `trade_date`.
- [x] 2.3 Add a private `_parse_sort(value, allowlist) -> tuple[str, str]` helper that raises `ValueError` on bad input; reuse for both lists.

## 3. Backend router

- [x] 3.1 Update `GET /api/portfolio/transactions` in `app/routers/portfolio.py`: declare `symbol`, `date_from`, `date_to`, `side`, `sort` query params; validate `limit` 1..100, `offset` >=0; map `ValueError` from `_parse_sort` to HTTP 422; swap `response_model` to `PagedTransactions`; return `{items, total}`.
- [x] 3.2 Update `GET /api/portfolio/dividends` analogously with `source` and `sort` allowlist for dividends; swap `response_model` to `PagedDividends`.
- [x] 3.3 Confirm no other router/service references the old return shape (`get_active_holdings`, `auto_record_recent_dividends`, etc.) before declaring the change non-breaking internally.

## 4. Backend tests

- [x] 4.1 In `tests/unit/test_portfolio_router.py` (create if absent): seed 30+ transactions across 3 symbols and 2 sides; assert default sort/limit, custom sort, symbol filter, side filter, date range filter, bad sort field 422, bad date format 422, bad side 422, bad limit 422, offset pagination correctness (no overlap, no skip).
- [x] 4.2 Analogous coverage for dividends in the same or sibling test file (source filter instead of side).
- [x] 4.3 Update any existing tests that asserted on the bare-array response shape — switch to `response.json()["items"]` / `["total"]`.

## 5. Frontend types & service

- [x] 5.1 Add `Paged<T>`, `TransactionQuery`, `DividendQuery` interfaces to `frontend/src/app/models/portfolio.model.ts`. Include `sort` as a free-form string (UI options come from a local constant).
- [x] 5.2 Rewrite `PortfolioService.getTransactions(query: TransactionQuery): Observable<Paged<Transaction>>` in `services/portfolio.service.ts`. Build `HttpParams` skipping null / undefined / empty-string values.
- [x] 5.3 Same rewrite for `PortfolioService.getDividends(query: DividendQuery): Observable<Paged<Dividend>>`.

## 6. Frontend — transaction-list

- [x] 6.1 Add `query`, `total`, `loading`, `symbolNames` signals to `transaction-list.ts`. Seed `query` with `{ offset: 0, limit: 25, sort: 'trade_date:desc' }`.
- [x] 6.2 Add `fetch()` method wired to a debounced effect (300 ms via `rxjs.timer`) on `query`; pagination changes call `fetch()` directly with no debounce.
- [x] 6.3 Add filter bar markup to `transaction-list.html` above `hub-modern-list`: symbol `p-autoComplete` (suggestions from `Object.entries(symbolNames())`), date-range `p-datepicker`, side `p-select` (BUY/SELL/All), sort `p-select` (`trade_date:desc/asc`, `symbol:asc`, `quantity:desc`, `price:desc`).
- [x] 6.4 Add `<p-paginator>` below the list bound to `total`, page sizes 25/50/100, default 25; `onPageChange` writes back to `query` and triggers `fetch()`.
- [x] 6.5 Changing any filter SHALL reset `offset` to 0 before re-fetching.
- [x] 6.6 Render an overlay/inline spinner using the `loading` signal.

## 7. Frontend — dividend-list

- [x] 7.1 Apply the same scaffolding as transaction-list, swapping side for source dropdown (manual / auto:TWT49U / csv / All) and the sort options (`ex_dividend_date:desc/asc`, `amount:desc`, `symbol:asc`).
- [x] 7.2 Keep the existing `nameFor()` helper and the "name as primary, symbol as secondary" card layout untouched.

## 8. Verify

- [x] 8.1 Backend: `cd services/stock-portfolio-service && pytest tests/unit/` all green; new tests cover every scenario in the spec.
- [x] 8.2 Frontend: `cd frontend && npm test` all green.
- [ ] 8.3 Manual smoke (`docker compose up -d && uvicorn app.main:app --port 8001` + `npm start`): on each list page, page 2 fetches only page 2 (Network tab); applying a symbol filter narrows `total`; symbol dropdown shows Chinese names; page-size selector switches between 25/50/100; debounce visibly batches keystrokes; bad inputs surface a server 422 via PrimeNG toast.
- [x] 8.4 Run `openspec validate paginate-and-filter-portfolio-lists --strict` and resolve any spec-format issues before opening the PR.
