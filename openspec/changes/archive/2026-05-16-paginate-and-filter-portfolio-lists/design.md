## Context

Both list endpoints (`GET /api/portfolio/transactions`, `GET /api/portfolio/dividends`) currently return a flat `List[T]`. The router accepts `offset`/`limit`/`symbol` but the frontend never passes them — `PortfolioService.getTransactions()` calls `BaseApiService.getAll()` (no params) and `getDividends()` issues a bare `GET`. The Angular components store everything in `signal<T[]>()` and render via the shared `hub-modern-list` card component (not `p-table`).

After recent imports the transactions table has ~2000 rows and the dividends table 70+. The single payload is still small enough to ship, but the UI lists are unscrollable in practice — the user can't find a symbol or restrict to a date range.

A previous OpenSpec change (`add-symbol-name-resolver`) added the `/symbol-map/names` endpoint that returns `{symbol: display_name}` for every traded symbol; this is the natural source for the symbol autocomplete in the filter bar.

## Goals / Non-Goals

**Goals:**
- Server-side pagination on both endpoints with a total count for paginator UX.
- Filter by symbol, date range, and side/source.
- Stable, predictable sort options for each list.
- Keep the existing `hub-modern-list` visual style.
- Backend response shape mirrors what other paged endpoints in this codebase will want (single canonical wrapper).

**Non-Goals:**
- Cursor / keyset pagination — offset/limit is fine at current row counts.
- `p-table` migration with column-header sorting.
- Multi-column sort.
- Persisting filter state in the URL.
- Full-text search across counterparty / memo fields.
- Pagination on other portfolio endpoints.

## Decisions

### 1. Response wrapper: `{ items, total }`

Return `{ items: list[T], total: int }`. The Pydantic schema lives in `app/schemas/portfolio.py` as two concrete classes — `PagedTransactions` and `PagedDividends` — rather than a generic. FastAPI's OpenAPI generator handles concrete classes more cleanly, and we only have two callers.

**Alternative considered:** Headers (`X-Total-Count`). Rejected — extra round-trip to read headers from `HttpClient`, and the response body already needs a wrapper if we add things like `page`/`page_size` echo later.

**Alternative considered:** Cursor pagination. Rejected — offset/limit suffices for 2K rows; cursor adds complexity (next-cursor bookkeeping, opaque tokens) with no current benefit.

### 2. Sort param syntax: `field:direction`

A single `sort` query param of the form `<field>:<asc|desc>`. Server validates `field` against an allowlist (per endpoint) and rejects anything else with HTTP 422. Default sorts retain today's behaviour: transactions = `trade_date:desc`, dividends = `ex_dividend_date:desc`. Tie-breaker: `id:desc` is always appended server-side so pages are deterministic across equal sort keys.

**Allowlists:**
- `transactions`: `trade_date`, `symbol`, `type`, `price`, `quantity`
- `dividends`: `ex_dividend_date`, `symbol`, `amount`, `source`

**Alternative considered:** `order_by` + `order_dir`. Rejected — two coupled params duplicate the same info, and `field:direction` matches the convention already used in stonk and several public APIs (Stripe, GitHub).

### 3. Filter semantics

- `symbol`: exact match (already exists).
- `date_from`, `date_to`: inclusive bounds on `trade_date` / `ex_dividend_date`. Either may be omitted. Server parses `YYYY-MM-DD` and rejects bad input with 422.
- `side` (transactions only): `BUY` or `SELL`, exact match on `type`.
- `source` (dividends only): exact match on `source` string. Allowlist limited to values the codebase actually writes (`manual`, `auto:TWT49U`, `csv`); pass-through for forward-compat.

### 4. Count query

Issue a second `SELECT COUNT(*)` over the same WHERE clause within the same DB session. Two queries per request is acceptable at this scale; consolidating into `(rows, count) = OVER(...)` would need a window function and breaks the simple SQLAlchemy `.order_by().offset().limit()` chain.

### 5. Frontend types

```ts
export interface Paged<T> { items: T[]; total: number; }
export interface TransactionQuery {
  offset?: number; limit?: number;
  symbol?: string; date_from?: string; date_to?: string;
  side?: 'BUY' | 'SELL';
  sort?: string;
}
export interface DividendQuery { /* analogous, with source instead of side */ }
```

`PortfolioService.getTransactions` / `getDividends` build `HttpParams` from a query object. `BaseApiService.getAll()` is no longer used by these two methods — they call `http.get` directly.

### 6. Filter bar UX

- Symbol field: `p-autoComplete` backed by the existing `getSymbolNames()` map (key = ticker, label = "ticker · 中文名"). Free-text fallback so users can still type a ticker that isn't in the map yet.
- Date range: `p-datepicker` in `selectionMode="range"` with TW locale; emits two ISO strings.
- Side / source: `p-select` (dropdown), nullable.
- Sort: `p-select` driven by a static option list per page.
- All filters live in a single `signal<Query>()`; an `effect()` debounces 300 ms via `rxjs.timer` and triggers re-fetch.

### 7. Paginator UX

`p-paginator` below the list. Bound to `page` (computed from offset) and `rowsPerPageOptions = [25, 50, 100]`, default 25. `onPageChange` updates the query signal which triggers the same debounced fetch path.

### 8. Loading state

A `loading` signal flips true during fetch. Render a single PrimeNG spinner overlay above the list rather than per-card skeletons — list contents stay visible (faster perceived nav) but become non-interactive.

## Risks / Trade-offs

- **Breaking response shape** → Mitigation: ship backend + frontend in one PR; no external consumers exist. Add a short note to the release commit message.
- **Two queries (rows + count) on every list call** → Mitigation: same WHERE, both indexes already exist (`(symbol, trade_date)`, `ex_dividend_date`); cost is negligible at current row counts.
- **Symbol autocomplete loads the full `getSymbolNames()` map on mount** → Mitigation: that endpoint already returns a small dict (~180 entries today); revisit if it grows past ~5K.
- **Debounce hides slow servers** → Mitigation: surface `loading` signal so the UI still indicates inflight requests; debounce only delays the kick-off, not the visibility.
- **Sort field allowlist drifts vs. UI options** → Mitigation: unit test asserts the UI's sort-option keys are a subset of the server allowlist.

## Migration Plan

- No DB migration. Indexes are already in place.
- Backend and frontend ship together — the API contract changes on the same commit so the in-repo frontend never sees a mismatched shape.
- No feature flag. The change is fully forward-compatible from the user's perspective: same data, better controls.

## Open Questions

- None at proposal time. (Page size, render style, OpenSpec usage all confirmed up front.)
