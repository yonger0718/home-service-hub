# stock-portfolio-list-paging Specification

## Purpose
TBD - created by archiving change paginate-and-filter-portfolio-lists. Update Purpose after archive.
## Requirements
### Requirement: Paged response wrapper on list endpoints

The service SHALL return paged responses of shape `{ items: list[T], total: int }` from `GET /api/portfolio/transactions` and `GET /api/portfolio/dividends`. `total` SHALL reflect the count of rows matching the active filters, independent of `offset` and `limit`.

#### Scenario: Empty result still includes total
- **GIVEN** the database holds 12 transactions, none matching the active filter
- **WHEN** a client `GET`s `/api/portfolio/transactions?symbol=NOMATCH`
- **THEN** the response SHALL be `{ "items": [], "total": 0 }` with HTTP 200

#### Scenario: Total reflects filters, not page
- **GIVEN** the database holds 50 transactions for symbol `2330`
- **WHEN** a client `GET`s `/api/portfolio/transactions?symbol=2330&limit=10&offset=0`
- **THEN** `items` SHALL contain 10 rows and `total` SHALL be 50

#### Scenario: Dividends use the same shape
- **WHEN** a client `GET`s `/api/portfolio/dividends`
- **THEN** the response SHALL be `{ "items": [...], "total": <int> }`

### Requirement: Pagination via offset and limit

The endpoints SHALL accept `offset` (default `0`, minimum `0`) and `limit` (default `25`, minimum `1`, maximum `100`) query parameters. Out-of-range values SHALL return HTTP 422.

#### Scenario: Default page size is 25
- **WHEN** a client `GET`s `/api/portfolio/transactions` with no `limit`
- **THEN** the response SHALL contain at most 25 rows in `items`

#### Scenario: Offset skips earlier rows
- **GIVEN** 30 transactions exist, sorted by the default `trade_date:desc, id:desc`
- **WHEN** a client `GET`s `/api/portfolio/transactions?limit=10&offset=20`
- **THEN** `items` SHALL contain the 21st through 30th rows of that ordering

#### Scenario: Limit above max is rejected
- **WHEN** a client `GET`s `/api/portfolio/transactions?limit=500`
- **THEN** the response SHALL be HTTP 422

### Requirement: Deterministic ordering with tie-breaker

Every list response SHALL be ordered by the requested `sort` field plus `id:desc` as a tie-breaker, so two adjacent pages never overlap or skip rows when the sort field has duplicate values.

#### Scenario: Equal trade_date rows are stably ordered
- **GIVEN** three transactions share `trade_date='2026-03-01'` with ids `7`, `9`, `12`
- **WHEN** the default sort is applied
- **THEN** those three rows SHALL appear in the order `id=12, id=9, id=7`

### Requirement: Sort param with allowlisted fields

The endpoints SHALL accept a single `sort` query parameter of the form `<field>:<asc|desc>`. The service SHALL reject any field outside the per-endpoint allowlist with HTTP 422.

The transactions allowlist SHALL be: `trade_date`, `symbol`, `type`, `price`, `quantity`.
The dividends allowlist SHALL be: `ex_dividend_date`, `symbol`, `amount`, `source`.
The default sort SHALL be `trade_date:desc` for transactions and `ex_dividend_date:desc` for dividends.

#### Scenario: Sort by amount descending for dividends
- **WHEN** a client `GET`s `/api/portfolio/dividends?sort=amount:desc`
- **THEN** `items` SHALL be ordered by `amount` descending, then `id` descending

#### Scenario: Unknown sort field is rejected
- **WHEN** a client `GET`s `/api/portfolio/transactions?sort=memo:asc`
- **THEN** the response SHALL be HTTP 422

#### Scenario: Malformed sort syntax is rejected
- **WHEN** a client `GET`s `/api/portfolio/transactions?sort=trade_date`
- **THEN** the response SHALL be HTTP 422

### Requirement: Symbol filter on both endpoints

Both endpoints SHALL accept an optional `symbol` query parameter that filters rows by exact match. When omitted, all symbols SHALL be included.

#### Scenario: Symbol filter narrows result
- **GIVEN** 100 transactions across 30 symbols, 12 of them for `0050`
- **WHEN** a client `GET`s `/api/portfolio/transactions?symbol=0050`
- **THEN** `total` SHALL be 12 and every row in `items` SHALL have `symbol='0050'`

### Requirement: Inclusive date range filter

Both endpoints SHALL accept `date_from` and `date_to` query parameters in `YYYY-MM-DD` format, filtering on `trade_date` (transactions) or `ex_dividend_date` (dividends) inclusive of both bounds. Either bound MAY be omitted. Bad formats SHALL return HTTP 422.

#### Scenario: Date range filters both bounds
- **GIVEN** transactions on 2024-12-31, 2025-01-15, 2025-06-30, 2025-12-31
- **WHEN** a client `GET`s `/api/portfolio/transactions?date_from=2025-01-01&date_to=2025-12-31`
- **THEN** `total` SHALL be 3

#### Scenario: Only date_from supplied
- **WHEN** `?date_from=2025-01-01` is supplied with no `date_to`
- **THEN** every returned row SHALL have its date on or after 2025-01-01

#### Scenario: Bad date format is rejected
- **WHEN** a client `GET`s `/api/portfolio/transactions?date_from=2025/01/01`
- **THEN** the response SHALL be HTTP 422

### Requirement: Side filter on transactions

The transactions endpoint SHALL accept an optional `side` query parameter accepting only `BUY` or `SELL`, matched exactly against the `type` column. Other values SHALL return HTTP 422.

#### Scenario: Side filter returns only BUYs
- **GIVEN** 10 BUY and 5 SELL transactions
- **WHEN** a client `GET`s `/api/portfolio/transactions?side=BUY`
- **THEN** `total` SHALL be 10 and every row's `type` SHALL be `BUY`

#### Scenario: Invalid side is rejected
- **WHEN** a client `GET`s `/api/portfolio/transactions?side=HOLD`
- **THEN** the response SHALL be HTTP 422

### Requirement: Source filter on dividends

The dividends endpoint SHALL accept an optional `source` query parameter, matched exactly against the `source` column. The value SHALL be passed through unchanged so future source strings work without code edits.

#### Scenario: Source filter narrows to auto-recorded rows
- **GIVEN** dividends with `source` values `manual`, `auto:TWT49U`, `csv`
- **WHEN** a client `GET`s `/api/portfolio/dividends?source=auto:TWT49U`
- **THEN** every row in `items` SHALL have `source='auto:TWT49U'`

### Requirement: Frontend sends typed query objects

The Angular `PortfolioService` SHALL expose `getTransactions(query: TransactionQuery)` and `getDividends(query: DividendQuery)` that return `Observable<Paged<T>>`. Query objects SHALL be serialised to `HttpParams`, omitting null/undefined fields.

#### Scenario: Omitted query fields are not sent
- **GIVEN** a query `{ offset: 0, limit: 25 }`
- **WHEN** `getTransactions` issues the HTTP request
- **THEN** the URL SHALL be `/api/portfolio/transactions?offset=0&limit=25` with no `symbol`, `date_from`, `date_to`, `side`, or `sort` keys

#### Scenario: Empty-string filters are not sent
- **GIVEN** a query `{ symbol: '', date_from: null, limit: 25 }`
- **WHEN** `getTransactions` issues the HTTP request
- **THEN** the URL SHALL NOT contain `symbol=` or `date_from=` parameters

### Requirement: Filter bar + paginator on list pages

The `transaction-list` and `dividend-list` Angular components SHALL render, above the existing `hub-modern-list`, a filter bar and, below it, a `<p-paginator>`. Filter changes SHALL debounce 300 ms before triggering a re-fetch. Pagination changes SHALL re-fetch immediately.

The filter bar SHALL include:
- a symbol autocomplete sourced from `getSymbolNames()` with free-text fallback,
- a date-range picker,
- a side dropdown (transactions: BUY/SELL/All) or source dropdown (dividends: manual / auto:TWT49U / csv / All),
- a sort dropdown matching the server allowlist.

The paginator SHALL offer page sizes 25, 50, and 100, defaulting to 25.

#### Scenario: Default page load uses defaults
- **WHEN** the component first mounts with no user input
- **THEN** it SHALL issue `?offset=0&limit=25` and render the returned `items`

#### Scenario: Filter change re-fetches once after debounce
- **GIVEN** the user is typing in the symbol field
- **WHEN** three keystrokes land within 200 ms followed by 300 ms of idle
- **THEN** exactly one HTTP request SHALL fire with the final symbol value and `offset=0`

#### Scenario: Page change re-fetches immediately
- **WHEN** the user clicks the next-page button
- **THEN** an HTTP request SHALL fire within 50 ms with the updated `offset`

#### Scenario: Changing a filter resets offset to 0
- **GIVEN** the user is on page 3 (offset 50, limit 25)
- **WHEN** they apply a symbol filter
- **THEN** the next request SHALL include `offset=0`

