## Why

The transactions and dividends pages currently load every row into a single signal and render them all in one card list. After the recent auto-record-dividends backfill (70 new dividend rows) and the broker-CSV imports (~2000 transactions), the lists are long enough that finding a specific symbol or date range means scrolling through hundreds of cards. Both endpoints already accept `offset/limit/symbol` but the frontend never sends them, so the entire result set is shipped on every page visit.

We need first-class server-side pagination plus filter/sort controls on both pages so users can scope the view (one symbol, last 12 months, BUY only, etc.) without changing the existing card aesthetic.

## What Changes

- **Paged response wrapper** — `GET /api/portfolio/transactions` and `GET /api/portfolio/dividends` SHALL return `{ items: [...], total: <int> }` instead of a bare array. **BREAKING** for any external consumer; only the Angular frontend in this repo consumes them today.
- **New query params on `/transactions`**: `symbol`, `date_from`, `date_to`, `side` (`BUY`|`SELL`), `sort` (e.g. `trade_date:desc`, `trade_date:asc`, `symbol:asc`), plus existing `offset`/`limit`.
- **New query params on `/dividends`**: `symbol`, `date_from`, `date_to`, `source` (`manual`|`auto:TWT49U`|`csv`), `sort` (e.g. `ex_dividend_date:desc`, `amount:desc`), plus existing `offset`/`limit`.
- **Service layer** — `portfolio_service.list_transactions` and `list_dividends` SHALL return `(items, total)` tuples. The total query reuses the same WHERE clause as the items query.
- **Default page size** — 25 rows. Caller may request 25/50/100.
- **Frontend `PortfolioService`** — `getTransactions(query)` and `getDividends(query)` accept typed query objects and return `Paged<T>` observables.
- **Frontend components** — `transaction-list` and `dividend-list` keep the `hub-modern-list` card layout but gain a filter bar above (symbol autocomplete using `getSymbolNames()`, date range, side/source dropdown, sort dropdown) and a `p-paginator` below. Filter changes debounce 300 ms before re-fetch.

### Out of scope

- Switching the card layout to `p-table` with sortable headers — explicit user decision to keep cards.
- Cursor-based pagination — offset/limit is sufficient for current row counts.
- Persisting filter state in the URL — page-local state only for now.
- Pagination on holdings, networth chart, corporate actions, or upcoming events.

## Capabilities

### New Capabilities

- `stock-portfolio-list-paging`: server-side pagination, filtering, and sorting contracts for the transactions and dividends list endpoints.

### Modified Capabilities

- None. (No prior shipped spec covers these endpoints; this is the first formal contract.)

## Impact

- **Backend** — `app/schemas/portfolio.py` (new paged wrappers), `app/services/portfolio_service.py` (signature change, count query), `app/routers/portfolio.py` (new params, response model swap), router + service unit tests.
- **Frontend** — `models/portfolio.model.ts` (new types), `services/portfolio.service.ts` (signature change), `components/portfolio/transaction-list/*`, `components/portfolio/dividend-list/*`.
- **Consumers** — Only the in-repo Angular app calls these endpoints; no external API contract to coordinate.
- **DB** — No schema changes. Existing indexes on `(symbol, trade_date)` and `ex_dividend_date` already cover the new filter paths.
