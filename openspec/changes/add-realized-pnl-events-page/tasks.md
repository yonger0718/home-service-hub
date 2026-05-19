## 1. Backend — extract shared cost-basis helper

- [x] 1.1 Introduce `RealizedPnlEvent` dataclass (or Pydantic model) used as the internal value object emitted by the MA loop, with fields: `trade_date`, `symbol`, `name`, `quantity`, `sell_price`, `avg_cost_at_sale`, `fee`, `tax`, `proceeds_gross`, `proceeds_net`, `cost_out`, `realized_pnl`, `is_day_trade`, `note`
- [x] 1.2 Add `iter_realized_events(transactions)` pure helper that walks the (already corporate-action-adjusted) iterable in order, maintains a per-symbol pool of `(quantity, total_cost)`, and yields one `RealizedPnlEvent` per SELL using moving-average cost basis
- [x] 1.3 Refactor the existing SELL branch inside `portfolio_service._step_transactions` to consume `iter_realized_events` output and accumulate `realized_pnl` into `holdings_map[symbol]["realized_pnl"]` — keep `total_quantity`, `total_cost`, `total_cost_ex_fee` updates intact so the dashboard summary numbers do not change
- [x] 1.4 Run `pytest` and confirm no existing test fails (covers the no-behavior-change claim)

## 2. Backend — realized P&L service + endpoint

- [x] 2.1 Add `app/services/realized_pnl_service.py` exporting `compute_events(session, *, symbol=None, date_from=None, date_to=None, year=None, day_trade_only=False, sort="trade_date:desc")` which calls into the same adjusted-transactions producer used by `portfolio_service` and runs `iter_realized_events`, then filters / sorts the result
- [x] 2.2 Add `compute_summary(session, filter_query)` returning `(filter_scope_total, ytd_total)` where `filter_scope_total` reflects the filter and `ytd_total` always covers the current calendar year across all symbols
- [x] 2.3 Add `app/schemas/realized_pnl.py` with `RealizedPnlEventOut`, `RealizedPnlSummaryOut`, `RealizedPnlPagedOut`
- [x] 2.4 Add `app/routers/realized_pnl.py` defining `GET /api/portfolio/realized-pnl` with query params `symbol`, `date_from`, `date_to`, `year`, `day_trade_only`, `sort`, `offset` (default 0), `limit` (default 25) returning `{items, total, summary}`
- [x] 2.5 Register the new router in `app/main.py`

## 3. Backend — tests

- [x] 3.1 Add `tests/unit/test_realized_pnl_service.py` covering: single SELL after multi-BUY, SELL spanning a corporate-action split (use existing corp-action fixture), no-inventory SELL flagged with `note="no_inventory"`, each filter (symbol, date range, year, day-trade-only), each sort order
- [x] 3.2 Add `tests/unit/test_realized_pnl_invariant.py` building a multi-symbol fixture portfolio and asserting `sum(events.realized_pnl) == portfolio_service.get_portfolio_summary().total_realized_pnl`
- [x] 3.3 Add `tests/integration/test_realized_pnl_endpoint.py` covering pagination boundaries, filter accuracy, summary `filter_scope_total` versus `ytd_total`, and a 200-status happy path
- [x] 3.4 Run `pytest tests/unit/ tests/integration/` from `services/stock-portfolio-service` and ensure all tests pass

## 4. Frontend — models + service

- [x] 4.1 Add `RealizedPnlEvent`, `RealizedPnlQuery`, `RealizedPnlSummary`, and a `RealizedPnlPaged` type alias to `frontend/src/app/models/portfolio.model.ts`
- [x] 4.2 Add `getRealizedPnl(query: RealizedPnlQuery)` to `frontend/src/app/services/portfolio.service.ts` returning an Observable of the paged response with summary

## 5. Frontend — page component

- [x] 5.1 Create `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.component.{ts,html,scss}` as a standalone component matching the structure of `transaction-list` (signal-based filter state, debounced re-fetch on change, expandable row toggle, paginator)
- [x] 5.2 Render two aggregate cards (filter-scope total and YTD total) above the filter bar using the existing dashboard summary-card pattern, labelled with `已實現損益 (篩選範圍)` and `今年累計 (YTD)` and showing the realized P&L value plus the trade count, using `筆交易`
- [x] 5.3 Render the filter bar: symbol autocomplete (using `getSymbolNames`), date-from / date-to pickers, year preset chips (`YTD`, `2026`, `2025`, `2024`, `All`, mutually exclusive with manual range), `僅當沖` toggle, sort dropdown
- [x] 5.4 Render the event list as hub-modern-list cards showing `trade_date`, `symbol` + `name`, `quantity`, `sell_price`, `avg_cost_at_sale`, `realized_pnl`, a `當沖` badge on `is_day_trade=true` rows, and a warning icon on `note="no_inventory"` rows
- [x] 5.5 On row click, expand to show `proceeds_gross`, `fee`, `tax`, `proceeds_net`, and the `cost_out` breakdown using fields already on the loaded row (no extra request)
- [x] 5.6 Render the paginator below the list with selectable page sizes 25 / 50 / 100, defaulting to 25, persisting the choice to `localStorage` under the same key namespace as the other portfolio lists
- [x] 5.7 Register the route `/portfolio/realized-pnl` in `frontend/src/app/app.routes.ts` using `loadComponent`
- [x] 5.8 Add a top-level nav entry `已實現損益` linking to the new route, placed adjacent to the existing 交易紀錄 / 股息 entries (locate and update whichever nav definition the dashboard header uses)

## 6. Frontend — tests

- [x] 6.1 Add `realized-pnl.component.spec.ts` covering: filter debounce, year preset clears manual date range, expand toggle reveals breakdown, paginator emits offset / limit changes that trigger re-fetch
- [x] 6.2 Run `npm test` from `frontend/` and ensure the new tests pass

## 7. Manual verification

- [ ] 7.1 Start the stock-portfolio-service and the Angular dev server
- [ ] 7.2 Navigate to `/portfolio/realized-pnl`, exercise each filter, change page size, expand a row
- [ ] 7.3 Compare the unfiltered `summary.filter_scope_total` displayed on the page against `total_realized_pnl` on the dashboard for the same portfolio and confirm they match exactly

## 8. OpenSpec hygiene

- [x] 8.1 Run `openspec validate add-realized-pnl-events-page --strict` and address any findings before opening a PR
