# GBp dividends ×100 + market labels (#115) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop LSE (GBp) dividends from being valued 100× in TWD on the backend totals/XIRR and on the 股利 page, and label non-TW markets by market code instead of calling everything 美股.

**Architecture:** Read-path normalisation, the same convention the price path already uses (`CLAUDE.md`: "LSE GBp prices are stored as pence and divided by 100 on the read path"): the stored dividend row keeps `amount` / `cash_dividend_per_share` in pence with `currency = 'GBp'` and `fx_rate_to_twd` in TWD per pound; every TWD valuation divides GBp by 100. No migration, no write-path change. Market labels become a shared `marketLabel(code)` map in the frontend.

**Tech Stack:** FastAPI (stock-portfolio-service; SQLite unit tests with dummy `POSTGRES_*` env + the pinned `.venv`), Angular 21 + Vitest.

**Spec:** GitHub issue #115. Code facts (survey 2026-10-23): pence→pounds for prices at `portfolio_service.py:720-721, :748`, `quotes/fx_rate_service.py:160-162` (`divisor=100`), `routers/fx_rates.py:35-43`; LSE dividends fetched by `foreign_dividend_service.refresh_today` (`:106-165`) from yfinance in pence with `currency='GBp'` (`:67-74`, `:132-133`, `:144-163`), FX looked up as GBP (`:77-91`); valuation `_dividend_amount_twd` (`portfolio_service.py:651-663`) multiplies pence × GBP rate (callers `:957`, `:964` native bucket); cash legs skipped for non-TWD (`:801-812`); frontend `money.ts:37-45` `twdAmount` (used by `dividend-math.ts:34, :86-90`); labels `realized-math.ts:5-11`, `realized-rows.ts:156`, `realized-pnl.ts:119-123`.

**Rev 2 (AGENT-238, dev-astra, CHANGES REQUIRED at 69477bb):** (P1) the API schema uppercased `GBp` → `GBP` (`app/schemas/portfolio.py:142-147` `normalize_symbol`), so the client never saw the pence marker — the validator now preserves the exact `GBp` token (everything else still upper-cased) and an ORM → schema → page-math fixture with a true-`GBP` control is added; (P1) `networth_backfill_service.py:741-743` multiplies pence × GBP rate into persisted `PortfolioSnapshot.total_dividends` — routed through the shared divisor, with a snapshot fixture, and the stored-history limitation is documented (derived snapshots stay stale until an operator-run, separately authorised rebuild — follow-up issue, not this PR); (P2) the native dividend bucket (`portfolio_service.py:960-964`, read at `:1231-1236`) upper-cased the key, merging pence into `GBP` and breaking the lookup by the holding's `GBp` — the key keeps the exact currency token. Also: dashboard 待收 is TW/TWD-only (`:834-878`, no divisor needed); the Step 0 audit grep is case-insensitive; the realized UI tests are `realized-pnl.component.spec.ts:718,733` and `realized-pnl-list.foreign-columns.spec.ts`; deploy = API (incl. the schema fix) first, then SPA, with the old SPA's dividend page still ×100 in the interim (minutes, same session).

**Branch:** `fix/gbp-dividends` from `origin/main` eca0f57 (worktree `~/workspace/home-hub-batch2`).

## Global Constraints

- Stored dividend rows untouched (pence stays pence); no migration; `currency` compared exactly as `'GBp'` (`'GBP'` pounds must not be divided). The API schema and every native-currency bucket preserve the exact `GBp` token (never upper-cased); all other currency codes keep being upper-cased.
- Derived history: `PortfolioSnapshot.total_dividends` rows persisted before this fix were computed with the ×100 value (`portfolio_snapshot_service.py:60` stores live totals; `:181-186` reads stored history). This PR does not rewrite them; it documents the limitation and files a follow-up for an operator-run, separately authorised snapshot rebuild.
- Native displays stay in pence with 4 decimals (`formatNativeAmount`, `native-amount.pipe`); only TWD valuations change.
- Cash legs: unchanged (non-TWD dividends have none); XIRR / summary / dashboard totals follow `_dividend_amount_twd`.
- Market copy: TW → 台股, US → 美股, LSE → 英股, anything else → the raw code; the realized filter keeps three buttons: 全部 / 台股 / 海外 (海外 = `market !== 'TW'`).
- API additive only if needed (none expected); deploy API first, then SPA (each half is independently correct).

## Decisions

1. **Backend divisor.** `_dividend_amount_twd`: `raw_currency = (row.currency or 'TWD').strip()`; `pence = raw_currency == 'GBp'`; TWD check uses `raw_currency.upper()`; `amount = Decimal(row.amount) / 100` when `pence`, then `× fx`. The native bucket at `:964` keeps pence (it is labelled with the native currency).
2. **Every other TWD valuation of a dividend** goes through `_dividend_amount_twd` or a shared `_pence_divisor(currency) -> Decimal` (1 or 100): known consumers are `networth_backfill_service.py:741-743` (cumulative dividends per snapshot day) and the XIRR inflow at `portfolio_service.py:957-967` (already via `_dividend_amount_twd`); Task 1 Step 0 runs a case-insensitive `grep -rniE "dividend.*amount|amount.*dividend" app/services app/routers` and lists each hit with its verdict. Dashboard 待收 (`:834-878`) is TW/TWD-only — untouched.
2b. **Schema.** `DividendBase.normalize_symbol` (`app/schemas/portfolio.py:142-147`): `currency = raw.strip(); currency = currency if currency == 'GBp' else currency.upper()`; applies to input (create/update) and output (`Dividend`), so a manual `GBp` row is no longer re-labelled `GBP`.
2c. **Native buckets.** `portfolio_service.py:960` keeps the exact token (`strip()` only, no `upper()`), so the lookup by `revalue.native_currency` (`:1233`) finds `GBp`; mixed `GBP` + `GBp` rows stay separate keys (existing mixed-currency behaviour: no native total).
3. **Frontend divisor.** `twdAmount(value, fx, currency)`: when `currency === 'GBp'`, `numeric / 100 × rate`. All callers (`dividend-math.ts`, any `transaction-rows`/`realized-rows` use) inherit it; `formatNativeAmount` unchanged.
4. **Market labels.** Reuse the existing `MARKET_LABELS: Record<MarketCode, string> = { TW: '台股', US: '美股', LSE: '英股' }` (`dashboard/dashboard-math.ts:6`): move it to a new `components/portfolio/market.ts` together with `marketLabel(code: string): string` (the code itself when unknown) and `isForeign(code) = code !== 'TW'`; `dashboard-math.ts` re-exports / imports it so the dashboard is unchanged. `realized-math.ts` `MARKET_FILTERS = ['ALL','TW','FOREIGN']`, toggle copy 全部 / 台股 / 海外, `matchesMarket` uses `isForeign`; `realized-rows.ts:156` `marketNote: isForeign(e.market) ? marketLabel(e.market) : null`; transaction/dividend rows that print a market note (Task 3 audit) use `marketLabel`.

## Review Focus

1. A `'GBP'` (pounds) dividend row — if any source writes pounds — must not be divided (Task 1 test with `currency='GBP'`).
2. `fx_rate_to_twd` missing on a GBp row still raises as today (Task 1 test).
3. The native-currency bucket in the summary (`:964`) stays in pence and still labels GBp (Task 1 test).
4. Frontend: `twdAmount('1234.5000', '40.5', 'GBp')` = 1234.5 / 100 × 40.5; `'GBP'` not divided; TWD untouched (Task 2 tests).
5. Realized filter under 海外 still includes US and LSE; row notes read 英股 for LSE (Task 3 tests).

---

### Task 1: Backend — GBp divisor in dividend valuation

**Files:**
- Modify: `services/stock-portfolio-service/app/services/portfolio_service.py:651-663` (+ any consumer from Step 0)
- Modify: `services/stock-portfolio-service/app/schemas/portfolio.py:142-147`, `app/services/networth_backfill_service.py:741-743`, `app/services/portfolio_service.py:960`
- Test: `services/stock-portfolio-service/tests/unit/test_dividend_gbp_valuation.py` (new; reuse the fixtures style of `test_portfolio_summary_foreign_revalue.py`)

- [ ] **Step 0: Audit** `grep -rniE "dividend.*amount|amount.*dividend|per_share" app/services app/routers` (case-insensitive — the backfill uses lowercase `dividend.amount`) and list every TWD valuation of a dividend in the task report; route each through `_dividend_amount_twd` / `_pence_divisor`.
- [ ] **Step 1: Failing tests** — `_dividend_amount_twd` with `currency='GBp'`, `amount='1234.5000'`, `fx_rate_to_twd='40.5'` → `Decimal('499.9725')`; `currency='GBP'` → `1234.5 × 40.5`; `currency='TWD'` → `amount`; GBp without fx → `ValueError`; **schema**: `Dividend.model_validate(orm_row_with_GBp).currency == 'GBp'` and `'gbp'`/`'usd'` → upper-cased, `DividendCreate(currency='GBp')` keeps `GBp`; **summary** with a real LSE holding (`native_currency='GBp'`) and one GBp dividend → `total_dividends` /100, `total_dividends_native` = `1234.5000` (key found), XIRR inflow = the divided value, a `GBP` control row not divided; **backfill**: a snapshot day after a GBp dividend → `total_dividends` /100 (fixture in the backfill test module's style).
- [ ] **Step 2: Run** `cd services/stock-portfolio-service && POSTGRES_USER=t POSTGRES_PASSWORD=t POSTGRES_DB=t POSTGRES_HOST=localhost POSTGRES_PORT=5432 .venv/bin/python -m pytest tests/unit/test_dividend_gbp_valuation.py -q` → FAIL.
- [ ] **Step 3: Implement** Decisions 1, 2, 2b, 2c.
- [ ] **Step 4: Run** the new file + `tests/unit -q` → green.
- [ ] **Step 5: Commit** `fix(api,dividends): value GBp dividends in pounds (÷100) on the read path (#115)`.

### Task 2: Frontend — `twdAmount` GBp divisor

**Files:**
- Modify: `frontend/src/app/components/portfolio/money.ts:37-45`
- Test: `money.spec.ts` (create next to it if absent), `dividend-list/dividend-math.spec.ts` (one GBp row through the page math)

- [ ] **Step 1: Failing tests** — Review Focus 4 cases; `dividend-math`: a GBp dividend row (as the corrected API emits it, `currency: 'GBp'`) renders its TWD figure /100 at `:34, :86-90`; a `GBP` transaction through `transaction-math`/`transaction-rows` is NOT divided (regression).
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include src/app/components/portfolio/money.spec.ts --include src/app/components/portfolio/dividend-list/dividend-math.spec.ts` → FAIL.
- [ ] **Step 3: Implement** Decision 3.
- [ ] **Step 4: Run** → PASS; grep `twdAmount(` callers and confirm none pre-divides.
- [ ] **Step 5: Commit** `fix(portfolio): GBp dividends valued in pounds on the client (#115)`.

### Task 3: Frontend — market labels

**Files:**
- Create: `frontend/src/app/components/portfolio/market.ts` (+ `market.spec.ts`); Modify: `dashboard/dashboard-math.ts:6` (import from `market.ts`)
- Modify: `realized-pnl/realized-math.ts:5-11`, `realized-rows.ts:156`, `realized-pnl.ts:119-123` (+ `.html` toggle labels), and every `'美股'` literal found by `grep -rn "美股" frontend/src/app/components/portfolio --include='*.ts' --include='*.html'` (audit; transaction/dividend rows)
- Test: `realized-math.spec.ts`, `realized-rows.spec.ts`, `realized-pnl.component.spec.ts:718,733`, `realized-pnl-list.foreign-columns.spec.ts` (toggle copy 海外, LSE note 英股, filter still matches US + LSE; keep genuine `US` market codes in fixtures); the dashboard keeps its own US selection untouched

- [ ] **Step 1: Failing tests** — `marketLabel('LSE') === '英股'`, `'XX'` → `'XX'`; `matchesMarket(LSE, 'FOREIGN')` true; `realized-rows` LSE → `marketNote '英股'`, US → `'美股'`; toggle renders 全部 / 台股 / 海外.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** Decision 4. **Step 4: Run** the three specs + any audited spec → PASS; `npx ng build --configuration production 2>&1 | grep -iE "error|budget"` unchanged.
- [ ] **Step 5: Commit** `fix(portfolio): market labels by code (英股 for LSE); 海外 filter (#115)`.

### Task 4: Verification

- [ ] **Step 1 (implementer):** `npx ng test --watch=false` (full) + `npm run build`; backend `tests/unit -q`.
- [ ] **Step 2 (controller):** on the live API after deploy, `GET /api/portfolio/summary` dividend total before/after for the owner's data is **not** quoted anywhere; only confirm the endpoint is 200 and that a GBp row (if any) appears with the native bucket unchanged. Screenshot the realized page toggle (海外) and an LSE row note on the preview with a stubbed fixture.
- [ ] **Step 3 (controller):** comment on #115 with the PR; the issue closes on merge (`Fixes #115` in the PR body); open a follow-up issue "rebuild PortfolioSnapshot.total_dividends after the GBp fix (operator-run)".

**Order:** 1 ∥ 2 ∥ 3 (disjoint files) → 4.

## Deploy

API first from a new release checkout (`~/releases/home-hub-portfolio/<sha>`, pinned venv, `cd` into the release dir before `pm2 start`; no migration; smoke `/health` + `/api/portfolio/summary` 200 and a dividends list row keeps `currency: 'GBp'`), then the SPA release in the same session. Interim (minutes): the old SPA's dividend page still shows ×100 for GBp rows against the fixed API; the new SPA against the old API would see `GBP` and not divide — so never SPA-first. Stored `PortfolioSnapshot.total_dividends` history stays stale until the follow-up rebuild.

## Self-review

- Spec coverage: #115 item 2 (GBp) → Tasks 1–2; item 1 (labels) → Task 3.
- Type consistency: `MarketFilter` union changes from `'US'` to `'FOREIGN'` — every reference updated in Task 3 (grep `MarketFilter`).
- Review Focus 1–5 each have a test.
