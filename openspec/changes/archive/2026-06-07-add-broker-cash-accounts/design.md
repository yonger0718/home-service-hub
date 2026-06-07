## Context

Today `stock-portfolio-service` models only equity holdings (`transactions`, `dividends`, `corporate_actions`, snapshots) and treats every cash flow as an implicit side-effect of a trade. That worked when the only broker was 國泰證券 and the only question was "what's my P&L." It breaks now that:

- the user has open relationships with five brokers (Cathay, SinoPac, Firstrade, IB, CS) in three currencies (TWD, USD, GBP, with JPY on the horizon),
- the operational question has shifted from "P&L of held equities" to "where is my idle cash and how much,"
- 1,900+ Cathay CSV rows already imported encode the exact cash movements (settlement net, fees, tax, interest, dividends) the new feature wants to surface — discarding them now would create a hard-to-rebuild blind spot.

Existing infra to reuse: SQLAlchemy + Alembic, FastAPI router pattern, `twse_client`-style outbound HTTP wrapper (retry + cache), the existing `import_fingerprint` idempotency pattern, the existing `iter_realized_events` "compute-on-read with cached intermediate" pattern, and the `networth_backfill_service` CLI pattern for replay-from-source backfills. Angular standalone components + PrimeNG cards + ECharts already in use across `portfolio/transactions`, `portfolio/dividends`, `portfolio/realized-pnl`.

## Goals / Non-Goals

**Goals:**

- Single ledger row per cash movement, signed amount, typed (11 types), linked back to its source `transaction` / `dividend` row when auto-derived.
- Per-account native-currency balance correct at any historical point in time (replay-able).
- Multi-currency aggregate to TWD via daily-snapshot FX rates (no real-time conversion required).
- Backfill every existing Cathay-imported `transaction` and `dividend` to cash rows in a single CLI invocation, idempotent on re-run.
- Manual cash entry for non-Cathay accounts (Firstrade, IB, CS, SinoPac).
- New `/portfolio/accounts` page surfacing account list + per-account detail (txn list, manual entry form, balance-over-time chart).
- Nav entry "現金帳戶" alongside existing portfolio sub-pages.

**Non-Goals:**

- Tracking US / LSE / JP **holdings** (only cash). Foreign equity positions, foreign quote feeds, foreign cost-basis, and foreign-currency unrealized P&L are explicit TODOs for a later change.
- CSV importers for SinoPac / Firstrade / IB / CS. Manual cash entry only.
- Reconciliation diff between Home Hub balance and broker-reported balance.
- Merging cash balances into the existing `networth_snapshot` materialization. The accounts page surfaces its own totals; networth integration is a separate change.
- Real-time intraday FX (single daily snapshot is enough for "idle cash" framing).
- Bank-account import to accounting-service. Scoped to brokerage cash only.
- Capital movement between accounts (transfers) modelled as a first-class double-entry: for v1 each side is a separate manual `deposit` + `withdraw` row with a shared note. First-class transfers are a follow-up.

## Decisions

### D1: One signed-amount ledger, not double-entry

**Chosen:** `cash_transaction` carries a single signed `amount` (+ inflow, − outflow) per row, typed by `CashTxnType` enum, optionally linked back to the originating `transaction_id` or `dividend_id`.

**Rejected:** A proper double-entry `ledger_entries` table (stonk's pattern) where every cash flow has matched debit + credit legs.

**Why:** Double-entry shines when you have multiple kinds of accounts (asset / liability / equity / income / expense) that must net to zero. Home-hub's scope here is one account class (brokerage cash) and a single side of the trade leg — the equity / cost side already lives in `transactions`. A second ledger would force the schema to also model holdings as ledger entries to balance the books, which contradicts non-goal "track only cash." A single signed-amount row is the minimal model that answers every question in scope, and it composes cleanly with the existing `transactions` table (FK link).

### D2: Compute-on-read balance, no materialized balance column

**Chosen:** `balance(account, asof)` is computed as `opening_balance + SUM(cash_transaction.amount WHERE account_id=? AND txn_date <= asof)` at query time.

**Rejected:** Maintain a running-balance column on each row, or a separate `account_balance_snapshot` table.

**Why:** Same reasoning as realized-PnL: any edit to a historical row reshuffles all later balances; materialization would have to recompute the full tail anyway. Cash-flow row count per account is bounded by trade volume (~2K/year max), so an indexed `SUM(amount)` is sub-millisecond. The realized-PnL refactor already proved this pattern; reuse it.

### D3: Backfill is replay, not column-copy

**Chosen:** `cash_backfill_service` iterates every `transactions` row + every `dividends` row in `trade_date` order, emits a `cash_transaction` row tagged `source=auto_derive` (for manual transactions) or `source=csv_import` (for Cathay-imported transactions), keyed by a deterministic `import_fingerprint = sha256("auto|{source_table}|{source_id}|{leg}")` so re-runs are idempotent.

**Rejected:** Add a `cash_amount` column to `transactions` and `dividends` and read directly.

**Why:** Replay puts the cash-leg logic in exactly one place (the backfill service + the live hooks in `broker_cathay_service` / `portfolio_service`). A column-copy approach would have to handle every edge case (margin, day-trade, dividend split, fee folding) in two layers (the importer + the cash query). It also locks future schema changes to backwards-compatible column adds. The replay pattern lets the cash service iterate, while the source-of-truth tables stay clean.

### D4: FX as daily snapshot, not live conversion

**Chosen:** `fx_rate` table keyed on `(date, base_currency, quote_currency)`, populated once a day by a scheduled job calling the FX provider (see D5). Balance aggregation looks up the rate for `asof_date` (or the most recent earlier date if the snapshot is missing).

**Rejected:** Look up FX rate live on every balance query.

**Why:** Sub-millisecond reads, deterministic results, and we already plan to add APScheduler for other jobs (per the scheduler capability already in `openspec/specs/stock-portfolio-scheduling`). Daily granularity matches the actual question framing ("idle cash today") — no one cares about intra-day FX drift on idle cash.

### D5: `fawazahmed0/exchange-api` over `open.er-api.com` and Frankfurter

**Chosen:** `fawazahmed0/exchange-api` (CDN-hosted static JSON, MIT-licensed open-data project on GitHub). Primary URL: `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json`. Fallback URL: `https://latest.currency-api.pages.dev/v1/currencies/{base}.json`. Historical URL: substitute `@latest` with `@YYYY-MM-DD` to fetch the snapshot for any past date back to 2024-03-12.

**Rejected:** `open.er-api.com` free tier (1,500 req/month quota, no historical endpoint without paid upgrade), Frankfurter (no TWD as base — sourced from ECB reference set).

**Why:** Three concrete reasons.

1. **No rate limit risk.** CDN-fronted static JSON has effectively unlimited reads (jsdelivr serves billions of requests/day for npm packages). Our load (~3 requests/day) is rounding error. `open.er-api.com` free tier's 1,500/month is comfortable for current scope but creates a quota dependency to monitor and a per-IP failure mode we'd have to recover from.
2. **Free historical endpoint.** If the scheduler misses a day (host reboot, network outage), `@YYYY-MM-DD` URLs let the FX service backfill the gap with a single fetch — no need for stale-rate fallback for known-recoverable gaps. `open.er-api.com` requires a paid tier for historical data.
3. **TWD direct.** Both providers (unlike Frankfurter) publish direct TWD/USD/GBP/JPY pairs, so no triangulation needed for our common case.

**Trade-off accepted:** `fawazahmed0/exchange-api` is a community open-data project, not a commercial API. If the maintainer abandons it, the CDN URLs eventually go stale. Mitigations: (a) dual-URL fetch with fallback so a single mirror outage is invisible, (b) `fx_rate_service.get_rate` already falls back to the most-recent prior snapshot, so a multi-day outage leaves the system functional with last-known rates, (c) swapping providers is a contained service-internal change (one URL + one parser).

### D6: New `broker_account` row required before any cash_transaction

**Chosen:** `cash_transaction.account_id` is `NOT NULL` with FK to `broker_account`. The backfill service creates a default Cathay account on first run if none exists. Other brokers must be explicitly created via the new POST endpoint or seed CLI.

**Rejected:** Implicit per-broker account auto-creation on every txn insert.

**Why:** The broker × currency tuple is a first-class operational thing (each has a real-world account number, may eventually need credentials / metadata for statement import). Hiding it behind an auto-create makes future fields (nickname, opening balance for non-Cathay accounts) awkward. Explicit beats implicit.

### D7: Transaction CRUD writes a linked cash row, all under one DB transaction

**Chosen:** `portfolio_service.create_transaction` / `update_transaction` / `delete_transaction` extend their existing DB-transaction scope to also create / update / delete the linked `cash_transaction` row. Cathay importer does the same inside its existing bulk-commit transaction. Failure on either side rolls back both.

**Rejected:** Async write of the cash row via a queue or post-commit hook.

**Why:** Hard guarantee that `transaction` and its cash leg either both exist or both don't. Queues add complexity that buys nothing here (we already control both writes from the same Python process).

### D8: Backfill identifies the originating cash leg per `transaction.type`

| `transaction.type` (+ side) | Emitted `cash_transaction.type` | Sign convention |
|---|---|---|
| BUY (any position_side) | `buy_settle` | negative (cash out) |
| SELL (any position_side) | `sell_settle` | positive (cash in) |
| BUY/SELL fee column > 0 | `fee` | negative |
| BUY/SELL tax column > 0 | `tax` | negative |
| `dividends` row | `dividend_cash` | positive |

Margin interest, wire fees, interest income, and FX conversion are **not derivable** from existing rows — those exist only as manual entries in v1.

### D9: Frontend page uses standalone Angular components, ECharts for chart

**Chosen:** `/portfolio/accounts` (list) + `/portfolio/accounts/:id` (detail) as two standalone components. Account-cards: PrimeNG `<p-card>` grid. Cash transaction list: existing `hub-modern-list` card layout for parity with transactions / dividends / realized-PnL pages. Manual entry: PrimeNG inputs + reactive form. Balance-over-time chart: ECharts (already imported by existing networth chart on dashboard).

**Rejected:** Single mega-component with both list + detail in tabs.

**Why:** Matches the route shape of `portfolio/transactions/:id` / `portfolio/dividends`. Easier independent testing and code-splitting. Detail view will eventually grow (per-broker statement upload, reconciliation diff) and needs its own route.

## Risks / Trade-offs

- **Risk: backfill produces wrong cash leg for legacy 國泰 rows where the importer hadn't yet folded 利息 / 券手續費 into `fee`** → Mitigation: backfill reads only the `transactions` row as-stored (which already had its fee folded by a prior change). For rows imported before that fold change, operator must SQL-patch fees first (already documented in `stock-portfolio-broker-cathay-import` spec).
- **Risk: `fawazahmed0/exchange-api` CDN goes down or the project is abandoned** → Mitigation: dual-URL fetch (jsdelivr → `currency-api.pages.dev` fallback) handles single-mirror outages transparently; multi-day outages leave the system functional via the most-recent-prior-snapshot fallback in `get_rate`; provider swap is a contained service-internal change.
- **Risk: FX rate missing for an early date (e.g., snapshot job didn't run yet for today)** → Mitigation: `fx_rate_service.get_rate(date, base, quote)` returns the most recent rate dated `<= asof`. Documented in spec.
- **Risk: backfill double-emits if user re-runs CLI on already-backfilled DB** → Mitigation: deterministic `import_fingerprint` per source row makes the INSERT a no-op on the unique index.
- **Risk: deleting a `transaction` orphans/double-deletes the linked `cash_transaction`** → Mitigation: FK is `ON DELETE SET NULL` not CASCADE; `portfolio_service.delete_transaction` explicitly deletes the linked cash row inside the same DB transaction (matches D7). Spec'd as a scenario.
- **Trade-off: no double-entry means cross-account transfers (e.g., USD → TWD inside IB, or wire from Firstrade to Cathay) require two manual rows + matching note** → Acceptable for v1. Follow-up change can introduce `transfer_id` to pair them.
- **Trade-off: cash-only scope leaves "total assets" on the new page showing only cash + TWSE-held positions, not US/LSE positions** → Documented as a visible TODO badge on the page itself so users aren't misled.
- **Trade-off: Cathay rehash path already overwrites `import_fingerprint` and `position_side` on rehash; we now have to *also* keep the linked cash row in sync** → Inside the same DB transaction, look up the linked cash row by `(source_table='transactions', source_id=tx.id)` and rewrite its `import_fingerprint` + `amount` to match. Acceptable complexity since the rehash path is already the trickiest part of the importer.

## Migration Plan

1. **Phase 0 — code-only**: ship the three new tables + service code + endpoints + frontend page **disabled by default**. No backfill triggered yet. Existing Cathay imports continue to ignore the cash leg (the hook is feature-flagged off).
2. **Phase 1 — backfill**: operator runs `python -m app.services.cash_backfill_service --all --dry-run`, reviews summary (rows-per-account, balance-per-currency), then re-runs without `--dry-run`. Single transaction, all-or-nothing.
3. **Phase 2 — flip the hook**: enable the cash-leg emission in `broker_cathay_service` and `portfolio_service` CRUD. Subsequent imports and edits now write a live cash leg.
4. **Phase 3 — non-Cathay accounts**: operator creates Firstrade / IB / CS / SinoPac account rows via the new POST endpoint and enters opening balances + any manual cash movements through the UI.
5. **Rollback**: drop the three tables in reverse Alembic order, revert the importer + CRUD hooks. Existing `transactions` / `dividends` rows are untouched throughout, so rollback is safe.

## Open Questions

- None blocking. Optional later: should `fx_convert` rows model both legs (USD-out and TWD-in within IB) as paired rows, or a single row with a derived counter-amount field? Defer until the user actually does an in-account conversion.
