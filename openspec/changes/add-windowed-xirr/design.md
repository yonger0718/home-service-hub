## Context

`portfolio_service.get_portfolio_summary` already builds a `cashflows_map: Dict[str, List[(date, Decimal)]]` while walking transactions and dividends, and feeds it to `_calculate_xirr` (a thin wrapper over `pyxirr`) twice — once per held symbol (`stock_xirr`) and once for the aggregate portfolio (`portfolio_xirr`). The wrapper already handles every edge case we care about: fewer than two flows, all-same-date flows, non-positive terminal, NaN/Inf, and pyxirr exceptions all collapse to `None`.

The existing `portfolio_snapshot` table records daily total market value (written by the snapshot job and rebuildable through `networth_backfill_service`), and the existing `price_history` table records per-symbol daily OHLC for the entire TWSE+TPEx universe (written by the `tw_daily_prices` scheduler job). Both tables are the natural source for the opening market value at any given `window_start`. No new tables, migrations, or external services are required.

The current `portfolio_xirr` field, when computed for a recent and small loss, prints a heavily annualized number (e.g., `-60.22%` for a `-5%` loss over 30 days). That number is mathematically correct but useless as a headline — every fund product instead shows windowed returns (1M/3M/1Y/YTD) computed against the opening market value of the window, which keeps the denominator stable and the rate interpretable.

## Goals / Non-Goals

**Goals:**
- Surface four additional windowed XIRR values (`1M`, `3M`, `1Y`, `YTD`) at the portfolio level and at each per-stock holding level in a single `GET /api/portfolio/summary` response.
- Reuse the existing `_calculate_xirr` helper and the existing `cashflows_map` data so the math stays in one place.
- Fail gracefully (return `null`) when the supporting `portfolio_snapshot` or `price_history` row is missing, so the API never errors on first install or thin history.
- Give the dashboard a chip selector so the user picks the window that makes sense for their holding age.

**Non-Goals:**
- Alternative return methodologies (Modified Dietz, TWR). XIRR only.
- Custom date-range pickers. Fixed windows only.
- Automated snapshot/price backfill. The user runs the existing CLI when they want to fill a gap.
- Changing or deprecating the existing lifetime `portfolio_xirr` / per-stock `xirr` fields. They stay exactly as they are for backwards compatibility.
- New persistent tables. Computation is on-read inside the summary endpoint.

## Decisions

### 1. Reuse existing `cashflows_map` rather than re-querying
`get_portfolio_summary` already walks `transactions` and `dividends` once and builds `cashflows_map[symbol]` with the canonical sign convention. For windowed XIRR, we filter that same list per window rather than re-issuing DB queries. This keeps the math definition in one place and avoids the risk of two slightly different cashflow definitions diverging.

**Alternatives considered:** running a fresh `db.query` per window. Rejected — duplicates the sign/fee/tax logic and doubles I/O on every summary call.

### 2. Opening market value sourced from `portfolio_snapshot` (portfolio) and `price_history` (per-stock)
At the portfolio level, the canonical "what was my portfolio worth on date D" comes from `portfolio_snapshot.total_market_value`. We pick the row with the largest `date <= window_start`. At the per-stock level, the canonical opening value is `qty_at_window_start * price_history.close[window_start]`, where `qty_at_window_start` is replayed from transactions (sum of BUY minus SELL for `trade_date < window_start`). The replay is cheap because the transaction list is already in memory.

**Alternatives considered:**
- Use the lifetime cashflow series unchanged and let pyxirr handle it. Rejected — that is exactly what `portfolio_xirr` already does, and it is the source of the -60% UX problem.
- Reconstruct opening market value by replaying every transaction and looking up `price_history` for every held symbol on `window_start`. Rejected at the portfolio level — `portfolio_snapshot` already aggregates this; replaying duplicates work. Accepted at the per-stock level — there is no per-stock snapshot table and the per-symbol price lookup is a single keyed read.

### 3. Calendar-month subtraction with last-day-of-month fallback
"1 month ago" is ambiguous on edge dates (e.g., today is March 31 → 1 month ago has no March 31). We define `1m` as `today - relativedelta(months=1)` via `dateutil.relativedelta`, which automatically clamps to the last valid day of the prior month. Same rule for `3m` and `1y`. `ytd` is January 1 of the current calendar year. TW calendar is used for "today" (consistent with the existing scheduler `_today_tw` helper).

**Alternatives considered:** fixed 30-day / 90-day / 365-day windows. Rejected — gives surprising shifts across month boundaries.

### 4. Nearest-previous trading day for missing `price_history` on `window_start`
`window_start` is often a Saturday, Sunday, or TW holiday with no `price_history` row. We look back up to 7 calendar days for the nearest previous trading-day row. Beyond 7 days we treat the per-stock window as a gap and return `null` for that field. The 7-day cap protects against silently using a year-old price when the price-history backfill is broken.

**Alternatives considered:** roll forward to the next trading day after `window_start`. Rejected — would include the first day's cashflow events as "post-opening" which inflates the window slightly. Roll back is the convention used by mutual-fund NAV time-series.

### 5. Gap handling = return `null`, surface via tooltip
When the required snapshot or price row is absent, the corresponding field is `null`. The frontend renders `—` and a tooltip pointing the user at `python -m app.services.networth_backfill_service --rebuild-all`. We deliberately avoid auto-running the backfill from the summary endpoint — that is a heavy job and would make summary fetches unpredictably slow.

**Alternatives considered:**
- Silently fall back to "since-inception" XIRR. Rejected — that hides the real situation from the user and makes the displayed window label a lie.
- Fail the whole summary request when any window is gappy. Rejected — bricks the dashboard on first install.

### 6. New capability `stock-portfolio-xirr` rather than modifying an existing one
The four new windows are an additive concern with their own sourcing rules (snapshot vs price history) and their own UI selector. Folding them into an existing capability (e.g., `stock-portfolio-snapshot`) would obscure their contract. A dedicated capability keeps the requirement set focused and lets future XIRR work (alternate methods, custom ranges) extend a single spec.

## Risks / Trade-offs

- **Extra compute per summary call** → For a portfolio with N holdings, we now run XIRR `(N + 1) * 5` times instead of `N + 1`. `_calculate_xirr` is millisecond-class on typical Taiwan-equity histories (a few hundred cashflows max), so worst-case overhead is <100ms even for sizeable portfolios. Mitigation: if profiling shows a regression we can short-circuit windows where `len(filtered_cashflows) < 2`.
- **Stale snapshot at window_start** → If the user stopped running the snapshot scheduler for a while, the "closest snapshot ≤ window_start" might be days or weeks earlier than the requested window start, which subtly skews the opening market value. Mitigation: the windowed value is still useful directionally; the doc string for the new field calls out this limitation and the same backfill CLI fixes it permanently.
- **`relativedelta` adds a dependency surface** → `python-dateutil` is already a transitive dep via pandas/structlog and is on `requirements.txt`. No new pip add. Confirmed before merge.
- **Per-stock 7-day price lookback** → A symbol that delists or stops trading for more than 7 days before `window_start` will register as a gap, which is the right behavior (we cannot price it). Documented in the spec.
- **Chip selector adds a UI state that resets on reload** → The dashboard chip selection lives in component state, not localStorage. Acceptable for v1; if users complain we can persist it later.

## Migration Plan

No schema changes, no migrations, no env vars. Roll forward by deploying the service + frontend together; the new fields are optional so an older frontend talking to the new backend will simply ignore them, and a new frontend talking to the old backend will read `null` for every windowed field and render `—` for every window. The lifetime XIRR field is untouched, so the headline number remains valid throughout the rollout.

Rollback is a normal redeploy of the previous version — no data needs to be reverted.

## Open Questions

None at proposal time. The window list (`1M/3M/1Y/YTD`), the gap policy (`A + C`), and the snapshot/price sourcing have all been confirmed with the user.
