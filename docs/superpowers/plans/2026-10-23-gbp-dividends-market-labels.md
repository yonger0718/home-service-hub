# GBp dividends ×100 + market labels (#115) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop LSE (GBp) dividends from being valued 100× in TWD on the backend totals/XIRR and on the 股利 page, and label non-TW markets by market code instead of calling everything 美股.

**Architecture:** Read-path normalisation, the same convention the price path already uses (`CLAUDE.md`: "LSE GBp prices are stored as pence and divided by 100 on the read path"): the stored dividend row keeps `amount` / `cash_dividend_per_share` in pence with `currency = 'GBp'` and `fx_rate_to_twd` in TWD per pound; every TWD valuation divides GBp by 100. No migration, no write-path change. Market labels become a shared `marketLabel(code)` map in the frontend.

**Tech Stack:** FastAPI (stock-portfolio-service; SQLite unit tests with dummy `POSTGRES_*` env + the pinned `.venv`), Angular 21 + Vitest.

**Spec:** GitHub issue #115. Code facts (survey 2026-10-23): pence→pounds for prices at `portfolio_service.py:720-721, :748`, `quotes/fx_rate_service.py:160-162` (`divisor=100`), `routers/fx_rates.py:35-43`; LSE dividends fetched by `foreign_dividend_service.refresh_today` (`:106-165`) from yfinance in pence with `currency='GBp'` (`:67-74`, `:132-133`, `:144-163`), FX looked up as GBP (`:77-91`); valuation `_dividend_amount_twd` (`portfolio_service.py:651-663`) multiplies pence × GBP rate (callers `:957`, `:964` native bucket); cash legs skipped for non-TWD (`:801-812`); frontend `money.ts:37-45` `twdAmount` (used by `dividend-math.ts:34, :86-90`); labels `realized-math.ts:5-11`, `realized-rows.ts:156`, `realized-pnl.ts:119-123`.

**Branch:** `fix/gbp-dividends` from `origin/main` eca0f57 (worktree `~/workspace/home-hub-batch2`).

## Global Constraints

- Stored data untouched (pence stays pence); no migration; `currency` compared exactly as `'GBp'` **before** any upper-casing (`'GBP'` pounds must not be divided).
- Native displays stay in pence with 4 decimals (`formatNativeAmount`, `native-amount.pipe`); only TWD valuations change.
- Cash legs: unchanged (non-TWD dividends have none); XIRR / summary / dashboard totals follow `_dividend_amount_twd`.
- Market copy: TW → 台股, US → 美股, LSE → 英股, anything else → the raw code; the realized filter keeps three buttons: 全部 / 台股 / 海外 (海外 = `market !== 'TW'`).
- API additive only if needed (none expected); deploy API first, then SPA (each half is independently correct).

## Decisions

1. **Backend divisor.** `_dividend_amount_twd`: `raw_currency = (row.currency or 'TWD').strip()`; `pence = raw_currency == 'GBp'`; TWD check uses `raw_currency.upper()`; `amount = Decimal(row.amount) / 100` when `pence`, then `× fx`. The native bucket at `:964` keeps pence (it is labelled with the native currency).
2. **Any other TWD valuation of a dividend** found by `grep -n "Dividend" app/services/*.py | grep -i "amount"` (Task 1 Step 0 lists them) must route through `_dividend_amount_twd`; if a consumer computes `per_share × quantity × fx` itself, it gets the same divisor via a shared `_pence_divisor(currency)` helper.
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
- Test: `services/stock-portfolio-service/tests/unit/test_dividend_gbp_valuation.py` (new; reuse the fixtures style of `test_portfolio_summary_foreign_revalue.py`)

- [ ] **Step 0: Audit** `grep -n "Dividend" app/services/*.py app/routers/*.py | grep -iE "amount|per_share"` and list every TWD valuation of a dividend in the task report; route each through `_dividend_amount_twd` / `_pence_divisor`.
- [ ] **Step 1: Failing tests** — `_dividend_amount_twd` with `currency='GBp'`, `amount='1234.5000'`, `fx_rate_to_twd='40.5'` → `Decimal('499.9725')`; `currency='GBP'` → `1234.5 × 40.5`; `currency='TWD'` → `amount`; GBp without fx → `ValueError`; summary-level test: one GBp dividend in the DB → `total_dividends`/the dividend total in the summary equals the /100 value and the native bucket still shows `1234.5000 GBp`; XIRR cash-flow builder (if it uses dividends — Step 0) uses the divided value.
- [ ] **Step 2: Run** `cd services/stock-portfolio-service && POSTGRES_USER=t POSTGRES_PASSWORD=t POSTGRES_DB=t POSTGRES_HOST=localhost POSTGRES_PORT=5432 .venv/bin/python -m pytest tests/unit/test_dividend_gbp_valuation.py -q` → FAIL.
- [ ] **Step 3: Implement** Decisions 1–2.
- [ ] **Step 4: Run** the new file + `tests/unit -q` → green.
- [ ] **Step 5: Commit** `fix(api,dividends): value GBp dividends in pounds (÷100) on the read path (#115)`.

### Task 2: Frontend — `twdAmount` GBp divisor

**Files:**
- Modify: `frontend/src/app/components/portfolio/money.ts:37-45`
- Test: `money.spec.ts` (create next to it if absent), `dividend-list/dividend-math.spec.ts` (one GBp row through the page math)

- [ ] **Step 1: Failing tests** — Review Focus 4 cases; `dividend-math`: a GBp dividend row renders its TWD figure /100.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include src/app/components/portfolio/money.spec.ts --include src/app/components/portfolio/dividend-list/dividend-math.spec.ts` → FAIL.
- [ ] **Step 3: Implement** Decision 3.
- [ ] **Step 4: Run** → PASS; grep `twdAmount(` callers and confirm none pre-divides.
- [ ] **Step 5: Commit** `fix(portfolio): GBp dividends valued in pounds on the client (#115)`.

### Task 3: Frontend — market labels

**Files:**
- Create: `frontend/src/app/components/portfolio/market.ts` (+ `market.spec.ts`); Modify: `dashboard/dashboard-math.ts:6` (import from `market.ts`)
- Modify: `realized-pnl/realized-math.ts:5-11`, `realized-rows.ts:156`, `realized-pnl.ts:119-123` (+ `.html` toggle labels), and every `'美股'` literal found by `grep -rn "美股" frontend/src/app/components/portfolio --include='*.ts' --include='*.html'` (audit; transaction/dividend rows)
- Test: `realized-math.spec.ts`, `realized-rows.spec.ts`, `realized-pnl.spec.ts` (toggle copy 海外, LSE note 英股, filter still matches US + LSE)

- [ ] **Step 1: Failing tests** — `marketLabel('LSE') === '英股'`, `'XX'` → `'XX'`; `matchesMarket(LSE, 'FOREIGN')` true; `realized-rows` LSE → `marketNote '英股'`, US → `'美股'`; toggle renders 全部 / 台股 / 海外.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** Decision 4. **Step 4: Run** the three specs + any audited spec → PASS; `npx ng build --configuration production 2>&1 | grep -iE "error|budget"` unchanged.
- [ ] **Step 5: Commit** `fix(portfolio): market labels by code (英股 for LSE); 海外 filter (#115)`.

### Task 4: Verification

- [ ] **Step 1 (implementer):** `npx ng test --watch=false` (full) + `npm run build`; backend `tests/unit -q`.
- [ ] **Step 2 (controller):** on the live API after deploy, `GET /api/portfolio/summary` dividend total before/after for the owner's data is **not** quoted anywhere; only confirm the endpoint is 200 and that a GBp row (if any) appears with the native bucket unchanged. Screenshot the realized page toggle (海外) and an LSE row note on the preview with a stubbed fixture.
- [ ] **Step 3 (controller):** comment on #115 with the PR; the issue closes on merge (`Fixes #115` in the PR body).

**Order:** 1 ∥ 2 ∥ 3 (disjoint files) → 4.

## Deploy

API first from a new release checkout (`~/releases/home-hub-portfolio/<sha>`, pinned venv, `cd` into the release dir before `pm2 start`; no migration; smoke `/health` + `/api/portfolio/summary` 200), then the SPA release.

## Self-review

- Spec coverage: #115 item 2 (GBp) → Tasks 1–2; item 1 (labels) → Task 3.
- Type consistency: `MarketFilter` union changes from `'US'` to `'FOREIGN'` — every reference updated in Task 3 (grep `MarketFilter`).
- Review Focus 1–5 each have a test.
