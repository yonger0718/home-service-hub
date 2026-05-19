## Context

`portfolio_service._step_transactions` already computes per-SELL realized P&L using a moving-average cost basis and aggregates the total into `PortfolioSummary.total_realized_pnl`. The per-event records are thrown away — only the running sum survives.

The dashboard surfaces that sum, but users cannot inspect the individual events that built it, filter by year for tax filing, or audit a surprising aggregate.

Adjacent state worth knowing:

- Transactions are corporate-action-adjusted before they reach the MA loop (`adjusted_transactions` in `_compute_portfolio_summary`). Reads must use the same view so events stay consistent with summary.
- Daily snapshots persist `total_realized_pnl` (`portfolio_snapshot.total_realized_pnl`), built by the same MA pipeline. Any event-level numbers we emit must sum to those snapshot values to stay credible.
- `transactions.is_day_trade` already exists and is populated by the import / portfolio service. TW half-rate securities tax is already baked into `transactions.tax` upstream of the MA loop.
- Other list endpoints (`transactions`, `dividends`) follow a paged response shape `{items, total}` (see `stock-portfolio-list-paging`). The new endpoint mirrors that shape and adds a `summary` field for aggregates.

## Goals / Non-Goals

**Goals:**
- One read-only endpoint that lists realized P&L events with filtering, sorting, pagination, and an aggregate summary.
- Single source of truth for cost-basis math shared between the existing summary path and the new events path.
- An Angular page that matches the look and behavior of the existing transactions / dividends list pages.

**Non-Goals:**
- Persisting events to a new table or maintaining materialized aggregates.
- Switching cost-basis method (FIFO, LIFO, specific-lot).
- Isolating day-trade pairs into separate event rows (TW tax aesthetic only; cumulative total is identical to the non-isolated view).
- Year-grouped bar chart, day-trade split aggregate card, CSV export, top winners/losers, FIFO drill-down (all deferred follow-ups).
- Backfilling historical snapshots; they already carry the correct cumulative number.

## Decisions

### D1: Compute-on-read, no materialized event table.

**Why:** Moving-average cost basis is sequentially dependent — any edit to a historical BUY reshuffles every later event for that symbol. A materialized table would need invalidation hooks on transaction CRUD, corporate-action retro adjustment, dividend recalc, and broker CSV bulk import. Each hook is a place for the event table to silently drift from `transactions`. The cheap recompute path (nuke and rebuild per symbol) is effectively compute-on-read anyway.

**Alternatives:**
- Materialized `realized_pnl_events` table — rejected; see above.
- Materialized only with denormalized cumulative running PnL — rejected; same invalidation cost, marginal read win.

**Trade-off:** Page request is O(N transactions). Retail scale (<10k lifetime rows) is sub-100ms. Filter/pagination happen in Python after compute.

### D2: Extract `iter_realized_events` as the single source of cost-basis math.

**Why:** The existing MA loop already produces the per-SELL `realized_pnl` we need. Duplicating the loop in a new service is the obvious way to introduce drift between the summary number and the event list. Extract the math into a pure helper that both paths call. The summary path keeps aggregating; the event path collects.

**Shape:**

```python
def iter_realized_events(transactions: Iterable[Transaction]) -> Iterator[RealizedPnlEvent]:
    """Stream per-SELL events; caller picks aggregation or collection."""
```

The helper takes the already corporate-action-adjusted transaction iterable, walks it in order, maintains the symbol → (qty, total_cost) pool, and yields one event per SELL with `avg_cost_at_sale`, `proceeds_net`, `cost_out`, `realized_pnl`, and metadata.

**Alternatives:**
- Have the new service re-read transactions and re-derive everything independently — rejected; drift risk.
- Have the new service call `get_portfolio_summary` and parse internals — rejected; couples to a wide interface for a narrow need.

### D3: Server-side filter, sort, paginate over a recomputed list.

**Why:** The compute step produces the full event list anyway; slicing in Python is trivial and avoids a SQL layer that would have to reproduce the MA math. Year preset is a server-side convenience that maps to `date_from` / `date_to`.

**Shape:**

```
GET /api/portfolio/realized-pnl
  ?symbol=&date_from=&date_to=&year=&day_trade_only=&sort=&offset=&limit=
→ {items: RealizedPnlEvent[], total: int, summary: {filter_scope_total, ytd_total}}
```

`summary.filter_scope_total` reflects the active filter. `summary.ytd_total` always ignores symbol/date filters — it is the dashboard YTD card delivered alongside the list to save a round-trip.

### D4: No-inventory SELL is flagged, not dropped.

A SELL with zero prior inventory can come from broker 融券 short positions or from data anomalies (e.g., historical partial-fetch artifacts before the gate shipped). The event is emitted with `cost_out=0`, `realized_pnl=proceeds_net`, `note="no_inventory"`. The UI shows a warning icon so the user can investigate rather than the row silently inflating their realized total.

### D5: Single-page server-rendered slicing instead of an infinite-scroll cursor.

**Why:** Matches existing `transactions` / `dividends` pagination. Users know how to page; users do not need infinite scroll for an audit view.

### D6: Aggregate header is a flex row of two cards.

**Why:** Cards 1 and 2 (filter-scope total, YTD total) are the only locked aggregates. The layout is a flex row so future cards (day-trade split, by-year chart) drop in without a redesign. Card text uses "筆交易", not "SELL", to match the user-facing language of the rest of the dashboard.

## Risks / Trade-offs

- **MA recompute on every request** → For retail scale this is fine. If a portfolio grows past ~50k transactions or page response time exceeds 500ms, revisit with a per-(symbol, request) in-process memo or a snapshot cache keyed on `max(transactions.id)`.
- **Event list and dashboard `total_realized_pnl` drift** → Invariant test (`SUM(events) == get_portfolio_summary().total_realized_pnl`) runs in CI on fixture portfolios. Catches any refactor that breaks parity.
- **Corporate-action retroactive split changes historical avg-cost** → Events automatically reflect the adjusted view because they read from `adjusted_transactions`. Same behavior as the dashboard summary, so no separate handling needed.
- **`no_inventory` flag may surprise users on imports** → UI surfaces a tooltip explaining the cause (融券 short or pre-gate partial-fetch artifact). Cumulative total stays meaningful because dashboard summary already treats this case identically.
- **Year preset vs manual date range can confuse** → Treat them as mutually exclusive in the UI. Picking a year preset clears the manual range; touching the manual range clears the preset.

## Open Questions

None — all scope locked in brainstorm.
