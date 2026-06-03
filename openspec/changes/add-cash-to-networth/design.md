## Context

Two parallel ledgers exist:
- `portfolio_snapshot` — daily totals derived from `transactions` + `dividends` (stock holdings, market value, unrealized P&L). Written by `portfolio_snapshot_service.write_today_snapshot()` daily and rebuilt historically by `networth_backfill_service`.
- `cash_transaction` + `broker_account` — signed-amount cash ledger introduced by `add-broker-cash-accounts`. Balance is compute-on-read via `cash_account_service.get_total_balance_in(target_currency, asof=date)`.

The two never meet, so the dashboard shows partial net worth. This change wires the cash side into the snapshot pipeline and the dashboard.

## Goals / Non-Goals

**Goals:**
- Snapshot rows carry `total_cash_twd` so the chart can render historical net worth without per-render cash recomputation
- Live summary endpoint exposes `total_cash_twd` + `total_assets_twd` so the dashboard tile renders in one call
- Backfill recomputes historical cash for the entire snapshot range
- Stacked chart: stocks bottom, cash top, top edge = total assets

**Non-Goals:**
- Per-account or per-currency series (single TWD-converted line is enough)
- Real-time cash updates faster than the daily snapshot cron (cash balance changes between snapshots are reflected only in the live tile, not the chart)
- Backfilling with rate-as-of-each-date FX accuracy if the rate row is missing — fall back to nearest as-of rate per existing `fx_rate_service.get_rate` semantics
- Showing cash as separate cards on the dashboard (the existing `/portfolio/accounts` page already does this)

## Decisions

### D1. Store `total_cash_twd` as a snapshot column, not a join

**Decision**: add `total_cash_twd NUMERIC(20,4) NOT NULL DEFAULT 0` to `portfolio_snapshot`.

**Why**: snapshot reads are paginated time-series queries (1M to All windows can be hundreds of rows); joining `cash_transaction` per row at read time multiplies the cost. Snapshot table already absorbs the "write once daily, read forever" pattern. One more column is essentially free.

**Alternative considered**: compute on read in `_serialize_snapshot` → rejected for cost and consistency (snapshot date must match cash balance compute date exactly).

### D2. FX conversion at write time, not read time

**Decision**: `write_today_snapshot` calls `cash_account_service.get_total_balance_in(db, "TWD", asof=target)` which already handles per-account FX conversion + USD pivot + missing-rate skip. The TWD-converted total is what gets stored.

**Why**: historical FX rates may not be available for snapshots written years ago; storing the converted value freezes the rate as-of the snapshot date and prevents future rate drift from rewriting history.

**Trade-off**: if a missing rate is filled in later (FX backfill), the snapshot still uses the old conversion. Mitigation: re-run `networth_backfill_service --rebuild-all` to refresh historical conversions.

### D3. Skipped currencies logged, not persisted

**Decision**: if an account currency has no usable FX rate, the snapshot writer logs `skipped_currencies=[...]` at WARN level but the row is still written with whatever cash IS convertible. The skipped list is NOT stored in the snapshot table.

**Why**: snapshots are point-in-time facts. A skipped row is permanently incomplete; storing the skip list would force the chart to display a warning forever, which is noisier than the actual operational risk (missing FX rates are a known transient issue resolved by `POST /api/portfolio/fx/refresh`).

### D4. Live summary endpoint adds derived fields, no new endpoint

**Decision**: `GET /api/portfolio/summary` response gains `total_cash_twd` (live `get_total_balance_in("TWD")`) and `total_assets_twd` (`total_market_value + total_cash_twd`). No new endpoint, no new client query.

**Why**: the dashboard tile and the summary both load on dashboard mount; piggybacking on the existing call is cheaper than two round trips. Service-side cost is one extra cash balance compute (~5ms over 5000 rows).

### D5. Two overlaid area series (revised after first cut)

**Decision**: chart datasets become `[{label: "總資產", data: total_assets_twd}, {label: "總市值", data: total_market_value}]` with `scales.y.stacked = false`. The two series share the same absolute scale; the vertical gap between the lines visually represents cash.

**Why**: initial implementation used a stacked area (`現金` + `持股市值` bands). After viewing it, the user fed back that the more useful framing is the two headline metrics they already track (`總市值` and `總資產`), with cash as the gap. Stacked bands made cash feel like a separate category rather than part of total assets.

**Alternative considered**: stacked area (initial cut) — rejected after user review. Single combined line — rejected (loses the market-value reference; user can't tell at a glance how much of the chart is investment performance vs cash injection).

### D6. Backfill column default = 0, not NULL

**Decision**: column is `NOT NULL DEFAULT 0`. Existing snapshot rows get 0 until the rebuild runs.

**Why**: chart code stays simple (no null handling). The 0 is visibly wrong only for the day or two between deploy and backfill, which is acceptable.

**Trade-off**: chart may show a sharp jump on the backfill day if the user views before rebuild completes. Mitigation: ship the deploy and backfill close together; the operational doc calls it out.

### D7. Tile placement above existing row

**Decision**: 總資產 tile sits as a new row above the existing tile row (market value / unrealized PnL / dividends / realized PnL / xirr). It is the primary number.

**Why**: it's the headline figure the user wants. Existing tiles become breakdown.

## Risks / Trade-offs

- **Historical FX rate gaps cause backfilled cash to be incomplete** → Mitigation: `fx_rate_service.get_rate` already does as-of-fallback + USD-pivot. Operator monitors `skipped_currencies` warnings during rebuild.
- **Snapshot write becomes coupled to cash service** — small coupling cost. Already coupled to FX service indirectly via cash service.
- **Backfill cost** — N snapshot rows × M accounts × 1 SQL aggregation per (row, account). At current scale (~5 years × 1 account = ~1800 ops) this completes in <30s.

## Migration Plan

1. Deploy backend with migration → schema adds column with default 0
2. Frontend deploy can happen in same window; existing chart degrades gracefully (cash series = 0 until backfill)
3. Run `python -m app.services.networth_backfill_service --rebuild-all` (existing CLI; backfill writes cash column for every snapshot date)
4. Verify dashboard chart shows the cash band
5. Rollback: `alembic downgrade -1` drops the column; revert deploys

## Open Questions

None — 5 decisions confirmed by user.
