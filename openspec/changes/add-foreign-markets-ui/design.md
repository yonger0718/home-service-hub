## Context

Phase 1 (PR #25) widened `transactions` / `dividends` / `price_history` to hold `market`, `currency`, `fx_rate_to_twd`. Phase 2 (PR #26) shipped the yfinance fetcher, daily FX cron, and a market-aware quote dispatcher behind `app/services/quotes/`. The portfolio summary endpoint now returns `market`, `native_close`, `native_currency`, and `live_fx_rate_to_twd` on every `StockHolding`, and the realized-PnL feed returns per-event market + native amounts. The Angular dashboard still renders the pre-Phase-1 shape — `StockHolding` in `frontend/src/app/models/portfolio.model.ts:131` has no `market` field, `portfolio.service.ts` keys cached holdings by `symbol`, and the transaction form has no market picker. The result is that backend foreign rows exist but the user cannot see them or create them through the UI.

Existing frontend layout: `components/portfolio/{dashboard,transaction-list,realized-pnl,dividend-list,networth-chart,corporate-actions-panel,accounts,import}`. Holding rows render in `dashboard` via `PortfolioService.getSummary()`. Transaction CRUD goes through `transaction-list` + a form modal. Existing main specs to touch: `frontend-portfolio-dashboard`, `frontend-stock-transactions`.

## Goals / Non-Goals

**Goals:**
- Surface every Phase 1+2 backend field that already ships on `StockHolding` and on realized-PnL events.
- Allow the user to record a US or LSE trade from the existing transaction form without a second flow.
- Keep TW behavior bit-identical: TW-only users see no new columns, no new picker friction, no layout shift.
- Make `(symbol, market)` the only holding identity in the frontend so a same-named symbol cannot collide across markets.
- Render native units verbatim (matches storage) and let the TWD column do the conversion math.

**Non-Goals:**
- Foreign broker CSV import (Phase 4+).
- Historical foreign-price backfill UI (admin-only, deferred).
- Multi-account UI.
- Cross-market consolidated tax view.
- Editing FX rates from the UI — they come from the daily cron.

## Decisions

### D1 — Market picker on the transaction form: dropdown with derived currency, manual override allowed

The transaction form gets a `market` dropdown `{TW, US, LSE}` (default `TW`). When `market === 'TW'`, the form looks and behaves exactly as today — no FX inputs. When `market !== 'TW'`, two fields appear: `currency` (auto-filled from market: `US`→`USD`, `LSE`→`GBP`, but editable so LSE `GBp` tickers can be recorded) and `fx_rate_to_twd` (required, `Decimal > 0`).

**Alternative considered:** Auto-derive currency without override. Rejected — LSE tickers can be `GBP`, `GBp`, or even `USD` per yfinance metadata; locking the user out would block real LSE workflows.

### D2 — Holdings table: flat table with `Market` column and collapsible per-market grouping

Holdings render in a single PrimeNG table with a new `Market` column (badge / pill). The table groups by `market` using PrimeNG's row-group feature: TW first and expanded, then US, then LSE; empty groups are not rendered. The flat-with-grouping shape keeps existing sort / filter wiring (a flat row array under the hood) but visually separates markets so totals per section read clearly.

**Alternative considered:** Three separate tables (one per market). Rejected — duplicates header markup, breaks "sort by P&L across markets" use case, and forces three subscriptions to the same summary stream.

### D3 — Native units rendered verbatim; TWD column does the conversion

For every holding row, the new `Native Price` column shows `native_close` exactly as stored, followed by `native_currency` as a suffix — `8050.00 GBp` for LSE pence tickers, `190.50 USD` for US, `590.00 TWD` for TW (no suffix needed for TW; suffix shown only when `market !== 'TW'`). The existing TWD columns (`market_value`, `unrealized_pnl`) continue to drive totals and percentages. **GBp `/100` happens only on the backend revaluation path** — the frontend never divides by 100 itself.

**Alternative considered:** Show LSE prices as `80.50 GBP`. Rejected — diverges from storage, requires double-bookkeeping in tests, and obscures what yfinance actually returned.

### D4 — Live FX rate is a tooltip / info badge on the foreign TWD cell

On rows where `market !== 'TW'` and `live_fx_rate_to_twd != null`, the `market_value` TWD cell renders an info icon whose tooltip shows `Revalued at 1 ${native_currency} = ${live_fx_rate_to_twd} TWD`. For TW rows or rows missing a live rate, no icon renders. Tooltip wiring uses the existing PrimeNG `pTooltip` directive — no new dependency.

**Alternative considered:** Dedicated FX-rate column. Rejected — sparse for TW-heavy portfolios and duplicates the same value across every same-currency row.

### D5 — Composite holding key everywhere: `${symbol}|${market}`

`PortfolioService` and every consumer (chart selection, row-click handlers, `trackBy` functions, any cached lookup) switches from `symbol` to a `holdingKey(holding)` helper that returns `\`${symbol}|${market}\``. A single helper exported from `portfolio.model.ts` is the only place the format lives.

**Alternative considered:** Use a tuple `[symbol, market]` everywhere. Rejected — JavaScript `Map` keys + Angular `trackBy` are friendlier with stable string keys; the helper is one line.

### D6 — Realized PnL list gets `Market` + `Native Amount` columns

The realized-PnL component adds a `Market` badge column and a `Native Amount` column (`native_proceeds` / `native_cost` with currency suffix). Existing TWD columns stay. When every event in the dataset is `market === 'TW'`, both new columns hide automatically so TW-only users see no layout change.

### D7 — Currency formatting via a single `nativeAmount` pipe

A new pure pipe `nativeAmountPipe(value: number | string | null, currency: string | null)` produces strings like `8050.00 GBp`, `190.50 USD`, `590.00 TWD`. Decimals: 4 places for `GBp`, 2 places otherwise (matches backend storage precision). Pipe lives in `frontend/src/app/pipes/` and is exported from a tiny standalone module so dashboard, holdings table, and realized-PnL list all consume the same formatting.

**Alternative considered:** Use Angular's built-in `currency` pipe. Rejected — Angular's pipe rejects ISO 4217 unknown codes like `GBp`, doesn't accept `TWD` without locale data, and the unit suffix style we want differs from `currency` pipe output.

### D8 — Conditional FX inputs validated client-side using the Phase 1 backend rule

When the form submits with `market !== 'TW'`, the client checks `fx_rate_to_twd > 0`. The backend `eligibility` validator already rejects `<= 0`; we mirror it to short-circuit a server round-trip. No other client-side currency validation — backend is source of truth.

### D9 — Tests: Vitest unit for new pieces; existing dashboard tests updated

New: `holding-key.spec.ts`, `native-amount.pipe.spec.ts`, `transaction-form.market-picker.spec.ts`, `holdings-table.grouping.spec.ts`, `realized-pnl-list.foreign-columns.spec.ts`. Updated: existing dashboard summary test learns about the new `market` field via a fixture builder so old tests do not break. No new E2E coverage in this change — Phase 3 is component-level only; a follow-up can extend the existing E2E suite once the backend is exercised in CI.

## Risks / Trade-offs

- **Composite-key cutover is a silent invariant change** → grep every callsite of `holding.symbol`-as-key in `frontend/src/app/components/portfolio/**` and `services/portfolio.service.ts` before merge; add a Vitest case that asserts two holdings with the same `symbol` but different `market` do not collide in `PortfolioService` cache.
- **Per-market grouping changes the row order users currently see** → for TW-only portfolios the visual result is identical (one TW group, no other groups rendered), so impact is limited to portfolios that actually hold foreign positions; document in PR description.
- **`GBp` ambiguity if user overrides currency by hand** → backend `eligibility` validator already rejects mismatched currency / market pairs; client-side just trusts whatever backend accepts.
- **`live_fx_rate_to_twd` may be `null` if the FX cron has not run yet** → tooltip + foreign TWD cells fall back to a dash with a `Live FX unavailable` tooltip; no console errors.
- **Vitest fixtures duplicate backend shape** → use a single `buildHolding(overrides)` helper in `tests/__fixtures__/` so a future backend field addition is a one-line change.

## Migration Plan

Frontend-only, no DB migration. Deploy steps:
1. Merge once unit tests pass and dashboard rendered against a dev backend with one TW + one US + one LSE holding.
2. No feature flag — composite-key change is internal; rendered fields are additive on the table and conditional on `market !== 'TW'`.
3. Rollback = revert the frontend commit; backend already supports both shapes.

## Open Questions

- Do we want a settings toggle to hide foreign sections when empty (today) versus always render them as zero-state placeholders (cleaner for users mid-onboarding to a new market)? **Default: hide-when-empty**; revisit after first real-world feedback.
- Should the dashboard total card break TWD totals down by source-currency contribution (e.g. `TWD: 1.2M | USD-derived: 380K | GBP-derived: 95K`)? **Deferred** — single TWD total stays for now; a per-currency breakdown is a follow-up.
