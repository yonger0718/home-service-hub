# Polish Layout v2 — PR B (numbers) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One formatter for every money amount and percentage on the portfolio pages (D2), and KPI / summary values that scale to their card instead of clipping or wrapping (D3).

**Architecture:** (D2) Add `formatMoney` / `formatPct` in `components/ui/format.ts` and route the dashboard hero, KPI cards, legend, group headers and holdings rows, plus the 交易紀錄 / 股利 / 已實現損益 summary cards and the 現金帳戶 total and header figures through them; the four duplicated `Intl.NumberFormat('zh-TW', currency)` helpers go. (D3) One shared stylesheet `components/portfolio/kpi-cards.scss` replaces the four copies of the `.kpi-row / .label / .value / .meta` rules; each `app-bento` in a KPI row becomes an inline-size query container and the value uses `clamp(18px, 12cqi, var(--fs-value))`; the dashboard KPI grid becomes 2 × 2 below 760 px like the other three pages.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest via `cd frontend && npx ng test --watch=false --include <spec>` (zsh: quote globs), SCSS with the `--app-*` token set, Playwright-in-Docker for the browser check (stub models in `~/reports/home-hub-playwright-stubs/`).

**Spec:** `openspec/changes/polish-layout-v2/` (on main since #137, rev 8 `9a1e0a6`): `design.md` D2 and D3, `tasks.md` §2, deltas `specs/frontend-design-system/spec.md` ("Uniform amount formatting", "Currency marker and sign", "Uniform percent formatting"), `specs/frontend-ui-primitives/spec.md` ("Values fit their container"), `specs/frontend-portfolio-dashboard/spec.md` (MODIFIED "Bento KPI grid", "總資產 hero"), `specs/frontend-stock-transactions/spec.md` ("Period summary cards"), `specs/frontend-dividend-records/spec.md` ("Summary row"), `specs/frontend-realized-pnl/spec.md` ("Realized PnL summary"), `specs/frontend-portfolio-accounts/spec.md` ("Accounts list page", `$150,000`). Evidence: `design_handoff_v2/reviews/2026-10-24-layout-review.md` defect 2 (KPI clipping) and the D2 examples.

## Global Constraints

- Frontend only; no API, model or copy change. Branch `fix/polish-layout-pr-b` from `origin/main` (a6acc99).
- D2 rule table (verbatim values): TWD and JPY whole units; every other currency two decimals; rounding half away from zero; TWD marker `$` (`$3,021,869`), `symbol: false` only where the element's own label or column header already names TWD (`3,021,869`); other currencies value, space, ISO code (`1,326.00 USD`, `5,390 JPY`); `sign: 'change'` → `+$4,921` / `−$4,076` (U+2212), zero unsigned; otherwise no `+`, negatives still `−`; `formatPct` one decimal when |v| < 100, none at or above (`12.3%`, `123%`), half away from zero, `+` / `−` only with `sign: 'change'`; ratio-type shares (佔成交額) pass `digits: 2` → `0.27%` (owner ruling 2026-10-24, spec rev 9).
- Out of D2 scope (unchanged): prices and per-share values, quantities, the `nativeAmount` pipe, `formatNativeAmount` (foreign row display), the ledger (`components/accounting/format.ts`, which keeps its own `formatMoney`), the compact `…M` chart axis, `confirmDelete` dialog text (`+10,000 TWD`, live accounts spec).
- Ruling (list rows): the 交易紀錄 timeline rows, 股利 rows, 已實現損益 rows and monthly bars, and the 現金帳戶 transaction rows / running balances stay as they are (`groupedInteger` / `signedInteger` / `formatCash`: whole TWD, U+2212, no `$`). The live specs pin them bare (realized "已實現損益 +13,498", bars "−4,897", accounts rows "438,360") and the deltas do not modify those requirements; their output already matches the D2 precision and sign rules. Routing them through `formatMoney` is a follow-up issue, not PR B.
- D3 values: `container-type: inline-size` on each KPI card, `font-size: clamp(18px, 12cqi, var(--fs-value))`, `white-space: nowrap`, `font-variant-numeric: tabular-nums`; below 760 px the KPI grid is `repeat(2, minmax(0, 1fr))`, gap 12px, card padding `--space-4`. The hero total (`.hero-total`, 44px / 2.35rem phone) is untouched — the spec keeps it "in `--fs-value` or larger" and it already fits.
- Colours only through `--app-*` tokens; trend colours only on gains / losses (the realized 賣出成本費用 colour change is PR E 5.6, not here).
- Every task: `cd frontend && npx ng test --watch=false --include <touched specs>` green before commit; the whole suite green before the PR.

## Review Focus

1. **A value that rounds to zero must never show a sign** (`−$0`, `+0.0%`): −0.4 TWD as a change → `$0`; −0.04% as a change → `0.0%`. Pinned in Task 1.
2. **Negative TWD with `symbol: false`** (hero total of a liability-heavy day, legend cash below zero): `−3,021,869`, the minus before the digits with no `$`. Pinned in Task 1.
3. **A market-filtered dashboard** shows KPI totals in the market's currency: `1,234.50 USD` with the ISO suffix, meta lines too; a TWD-filtered view keeps `$`. Pinned in Task 2 (the existing filtered-market dashboard tests).
4. **Nine-digit TWD in a 360 px 2 × 2 cell** (`$123,456,789`) overflows at the 18 px floor by design (spec Risks). Task 5 records the measured width at `$12,345,678` so the margin is known; no code.
5. **`clamp()` with `cqi` needs a query container above the value**: if a page nests `app-bento` inside another element that is not the container (the 交易紀錄 `a.kpi-link` wrapper), `cqi` must still resolve to the card, not the viewport. Task 5 asserts the computed font size of every `.kpi-row .value` is ≤ 32 px and ≥ 18 px at 360 / 1100 / 1440.

---

### Task 1: `formatMoney` / `formatPct` with the D2 oracle (2.1)

**Files:**
- Create: `frontend/src/app/components/ui/format.ts`
- Test: `frontend/src/app/components/ui/format.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  export type Amount = number | string | null | undefined;
  export interface MoneyOptions { currency?: string | null; sign?: 'change'; symbol?: boolean }
  export function formatMoney(value: Amount, options?: MoneyOptions): string
  export function formatPct(value: Amount, options?: { sign?: 'change'; digits?: number }): string
  export const MINUS = '−';            // U+2212
  ```
  `currency` missing / empty / any case of `TWD` = TWD (reuse `isTwdCurrency` from `components/portfolio/money.ts`). `value` null / `''` / non-finite → `—`. Body: `Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })` over `Math.abs(value)` with `d = 0` for TWD / JPY, else `2` (Intl's default rounding is half-expand = half away from zero). Zero body (`0`, `0.00`) → no sign. Sign: negative → `−`; positive and `sign === 'change'` → `+`. TWD: `${sign}$${body}` or `${sign}${body}` when `symbol === false`. Other: `${sign}${body} ${CODE}` with the currency code as given (`GBp` stays `GBp`). `formatPct`: with `digits` given, use it as is (`100` → `100.00%`); otherwise format `|v|` with 1 dp and, when that rounded magnitude is ≥ 100, format again with 0 dp (`99.95` → `100%`, `99.94` → `99.9%`, `123.4` → `123%`); suffix `%`, same sign rule, `—` for non-numeric.

- [ ] **Step 1: Write the failing spec** — `components/ui/format.spec.ts`, one `it.each` table per function:

```ts
// formatMoney — [value, options, expected]
[3021869.34, {}, '$3,021,869'],
[3021869.34, { symbol: false }, '3,021,869'],
[292227.2, { sign: 'change' }, '+$292,227'],
[1326, { currency: 'USD' }, '1,326.00 USD'],
[1326, { currency: 'USD', sign: 'change' }, '+1,326.00 USD'],
[5390.4, { currency: 'JPY' }, '5,390 JPY'],
[35205, { sign: 'change' }, '+$35,205'],
[448.5, { sign: 'change' }, '+$449'],
[-4076.5, { sign: 'change' }, '−$4,077'],
[-4076, {}, '−$4,076'],
[4076, {}, '$4,076'],
[-3021869, { symbol: false }, '−3,021,869'],
[-0.4, { sign: 'change' }, '$0'],
[0, { currency: 'USD', sign: 'change' }, '0.00 USD'],
['150000', {}, '$150,000'],
[1234.5, { currency: 'twd' }, '$1,235'],
[12.345, { currency: 'GBp' }, '12.35 GBp'],
[null, {}, '—'], ['', {}, '—'], ['abc', {}, '—'],
// formatPct — [value, options, expected]
[12.345, {}, '12.3%'], [123.4, {}, '123%'], [-2.35, { sign: 'change' }, '−2.4%'],
[2.35, { sign: 'change' }, '+2.4%'], [2.35, {}, '2.4%'], [-0.04, { sign: 'change' }, '0.0%'],
[99.95, {}, '100%'], [99.94, {}, '99.9%'], [100, { digits: 2 }, '100.00%'], [0, {}, '0.0%'], [0.27, { digits: 2 }, '0.27%'], [0.274, { digits: 2 }, '0.27%'], [null, {}, '—'],
```

- [ ] **Step 2: Run it to see it fail** — `cd frontend && npx ng test --watch=false --include src/app/components/ui/format.spec.ts` → fails: module not found.
- [ ] **Step 3: Implement** `format.ts` per the Interfaces block (≤ 40 lines; export `MINUS`). Doc comment names the D2 rule table and says the ledger's `components/accounting/format.ts#formatMoney` is a different function.
- [ ] **Step 4: Run** the spec → all cases pass.
- [ ] **Step 5: Commit** — `feat(ui): formatMoney / formatPct — the D2 amount and percent rules (polish-layout-v2)`.

---

### Task 2: Dashboard adopts the formatter; `PctBadge` uses `formatPct` (2.2 dashboard)

**Files:**
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.ts` (hero :175-188, `groupHeader` :428-431, `marketValueDisplay` :443-446, `formatAmount` :470-472, `formatCurrency` :474-480, `formatNative` :500-502, `formatXirr` :504-507, chart text :313, :753, :756)
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.html` (:36 hero total, :39 today, :48/:53/:58 legend, :72/:74/:87/:94/:109/:119 KPI cards, :151/:155/:160/:182 chart legend, :202 allocation pct, :223 pending rows, :288 day pct, :290-294 holdings value / PnL / pct, :317/:321-329 detail panel)
- Modify: `frontend/src/app/components/ui/pct-badge/pct-badge.ts:22-27`
- Test: `dashboard/dashboard.spec.ts` (assertions listed below), `ui/pct-badge/pct-badge.spec.ts:20,30`

**Interfaces:**
- Consumes: Task 1 `formatMoney`, `formatPct`.
- Produces: `dashboard.ts` keeps three template helpers and drops the rest — `money(value: Amount, currency?: string | null, sign?: 'change'): string` = `formatMoney(value, { currency, sign })`; `twdPlain(value: Amount): string` = `formatMoney(value, { symbol: false })` (hero total, hero legend — label 總資產 · TWD names the currency); `pct(value: Amount, sign?: 'change'): string` = `formatPct(value, { sign })`. `formatAmount`, `formatCurrency`, `formatNative`, `formatXirr` are deleted (specs call `f.formatNative(...)` / `f.formatCurrency(...)` to build expected strings — rewrite those to literals).

Mapping (value → call → example):
| Site | Call | Example |
|---|---|---|
| hero total `:36` | `twdPlain(summary.total_assets_twd)` | `3,021,869` |
| hero today `:39` (`todayText`) | `money(today.amount, 'TWD', 'change')` — drop the manual `+`/`-` | `+$10` / `−$10` |
| hero legend `:48/:53/:58` | `twdPlain(...)` | `2,612,900`, `−200` |
| `barLabel` `:186` | `pct(parts.stocksPct)` / `pct(parts.cashPct)` | `股票 80.0%，現金 20.0%` |
| KPI 總市值 / 總成本 / 已記錄股利 `:72/:74/:119` | `money(x, totals.currency)` | `$2,612,900` / `1,234.50 USD` |
| KPI 預估含息損益 `:87` and meta 價差 · 股利 · 待收 `:94` | `money(x, totals.currency, 'change')` for the PnL and 價差; `money(x, totals.currency)` for 股利 / 待收 | `+$4,690` |
| XIRR `:109` (and the holding detail call `:338`) | `pct(portfolioXirr * 100)` / `pct(holdingXirr * 100)` — the template's existing `@if` null branch keeps the `—` placeholder (`:104-106`); `formatXirr` and its `'N/A'` go | `12.3%` |
| chart legend / caption `:151/:155/:160/:182`, tooltip `:313/:753/:756` | `money(x)` | `$1,250` |
| allocation legend `:202` | `pct(slice.pct)` | `45.2%` |
| pending ex-dividend rows `:223` | `money(row.amount, row.currency, 'change')` → TWD `+$9,600`; foreign `+7.80 USD`; delete the template's literal `'+'` (the call carries the sign) | |
| holdings `stock-value` (`marketValueDisplay`) | `money(holding.market_value)` | `$650,000` |
| holdings `pnl-amt` `:292` | `money(holdingEstimate(stock), holdingCurrency(stock), 'change')` | `+$292,227` / `+1,326.00 USD` |
| holdings `pnl-pct` `:294` | `pct(holdingEstimatePercent(stock), 'change')` | `(+93.8%)` |
| `stock-day` pct `:288` | `pct(stock.day_change_percent, 'change')`, deleting the template's literal `'+'` (the per-share amount `:287` stays `number:'1.2-2'` with its own `+`) | `(+0.8%)` |
| `groupHeader` `:430` | `money(total)` | `台股 · 4 檔 · $2,612,900` |
| detail panel `:317/:324/:326` TWD amounts | `money(x)`; `:321-329` PnL / pending via `money(x, currency, 'change')` for PnL, `money(x, currency)` otherwise | |
| `PctBadge.formatted` | `formatPct(value, { sign: 'change' })` (`—` when no value stays) | `+4.2%`, `−1.2%` |

- [ ] **Step 1: Rewrite the assertions** in `dashboard.spec.ts` to the new strings (`:405/427/438` `+$10`, `−$10`; `:407/429` `+0.8%`, `−0.8%`; `:411` unchanged, `:448` → `資產組成：股票 100%，現金 0.0%` (`formatPct(100)` drops the decimal); `:450` `−200`; `:240/247` `12.3%`, `3.2%`; `:401/480` literals `3,021,869`-style for the fixture values; `:550-552/559` `+$4,690`, `+93.8%`, `$3,090`; `:555/583-584/596-597/624/633/647-648/667/708-709` footnote literals; `:785-788/825/941/945/975` unchanged `$` strings; `:835` `+25.0%`; `:1067` `+$9,600`; `:1239` unchanged; `:1253` `+5.00 (+0.8%)`) and `pct-badge.spec.ts:20/30` (`+4.2%`, `−1.2%`). Run `--include 'src/app/components/portfolio/dashboard/*.spec.ts' --include src/app/components/ui/pct-badge/pct-badge.spec.ts` → the rewritten cases fail.
- [ ] **Step 2: Implement** the mapping table; delete the four old helpers; `PctBadge` imports `formatPct`.
- [ ] **Step 3: Run** the two spec globs → green. Also `--include 'src/app/components/portfolio/dashboard/*.spec.ts'` covers `holdings-table.grouping.spec.ts` (native strings unchanged).
- [ ] **Step 4: Commit** — `fix(spa): dashboard amounts and percentages through formatMoney / formatPct (polish-layout-v2 D2)`.

---

### Task 3: 交易紀錄 / 股利 / 已實現損益 / 現金帳戶 summary figures (2.2 pages)

**Files:**
- Modify: `components/portfolio/transaction-list/transaction-list.ts` (`wholeTwd` :33-35, `formatCurrency` :403-408, `formatCardAmount` :412-414, `cardSign` :417), `transaction-list/transaction-math.ts:59-62` (`feeShareLabel`)
- Modify: `components/portfolio/dividend-list/dividend-list.ts` (`grossHint` :377-385, `formatCardAmount` :571-573, `formatCurrency` :575-581)
- Modify: `components/portfolio/realized-pnl/realized-math.ts:135-148` (`realizedCards`), `realized-pnl/realized-rows.ts:83-92` (`pct`)
- Modify: `components/portfolio/accounts/accounts-list.ts:124-128` (`totalText`), `:245-247` (`cardBalance`), `accounts/account-detail.ts:606-616` (`formatCurrency`)
- Test: `transaction-list/transaction-list.spec.ts` (:231/285/335/358 cards unchanged `$…`; :256 `−$1,234`; :234/378 unchanged — `佔成交額 0.27%`, `—`), `transaction-list/transaction-math.spec.ts` (`feeShareLabel` cases), `dividend-list/dividend-list.spec.ts` (:62/295/499/1038/1055/1121 unchanged; :482 `配息 $1,000 (匯費 −$10)`), `realized-pnl/realized-math.spec.ts` (:181-184 `+$21,046`, `+$13,498`, `+$2,030`, `$1,764`; :170-172/205/207 bars unchanged), `realized-pnl/realized-pnl.component.spec.ts` (card strings only; rows / bars / pct unchanged), `realized-pnl/realized-rows.spec.ts` (pct unchanged: `+8.4%`, `−7.3%`, `0.0%`), `accounts/accounts-list.component.spec.ts` (:176 `$150,000` unchanged; :165 `['$100,000', '−1,500.50 USD', '−300 JPY']` — the fixture accounts are TWD / USD / JPY), `accounts/account-detail.component.spec.ts` (:1133-1137 `−$12,345`, `期初 −$5,000`, `帳戶餘額為負（−$12,345），可能漏記入金。`; :1144-1147 `formatCurrency` cases in call order → `−$1,501` (`'-1500.5','TWD'`), `$438,360`, `−1,500.50 USD` (`'-1500.5','USD'`), `$0` (`'-0','TWD'`); the `not.toContain('NT$')` lines stay)

**Interfaces:**
- Consumes: Task 1.
- Produces: no new exports. `transaction-list.ts`: `formatCardAmount(v) = formatMoney(v)` for the three plain cards and `formatMoney(v, { sign: 'change' })` for the 已實現損益 card (add a second helper `formatSignedCard(v)`); `cardSign` keeps `Math.sign(Math.round(v) || 0)`; `formatCurrency` and `wholeTwd` deleted. `feeShareLabel(share)`: `佔成交額 ${share == null ? '—' : formatPct(share * 100, { digits: 2 })}` (same strings as today; keep the null guard — `formatPct(0, { digits: 2 })` would print `0.00%`). `dividend-list.ts`: `formatCardAmount(v) = formatMoney(v)`, `grossHint` uses `formatMoney`, `formatCurrency` deleted. `realizedCards`: default card formatter `(v) => formatMoney(v, { sign: 'change' })`, costs card `(v) => formatMoney(v)`; `signedInteger` stays for bars and rows. `realized-rows.ts#pct`: `formatPct(ratio, { sign: 'change' })` (same strings; `null` when no cost stays). `accounts-list.ts`: `totalText = formatMoney(total)`; `cardBalance(a) = formatMoney(a.native_balance, { currency: a.currency })` (`cardMeta` 期初 keeps `formatCash`). `account-detail.ts`: `formatCurrency(value, currency)` body becomes `return formatMoney(value, { currency });` (header 目前餘額, 期初, overdraft banner, chart tooltip and ticks all flow through it; a `null` value now renders `—` instead of `$0` — the header only renders inside `@if (account())`, so no visible change).

- [ ] **Step 1: Rewrite the listed assertions**; run the five spec globs (`transaction-list/*.spec.ts`, `dividend-list/*.spec.ts`, `realized-pnl/*.spec.ts`, `accounts/*.spec.ts`, plus `transaction-list/transaction-math.spec.ts`) → the rewritten cases fail.
- [ ] **Step 2: Implement** per the Interfaces block.
- [ ] **Step 3: Run** the globs → green.
- [ ] **Step 4: Commit** — `fix(spa): summary cards, cash totals and header figures through formatMoney; percent shares through formatPct (polish-layout-v2 D2)`.

---

### Task 4: Values fit their card — shared KPI stylesheet, 2 × 2 phone grid (2.3)

**Files:**
- Create: `frontend/src/app/components/portfolio/kpi-cards.scss`
- Modify: `components/ui/bento/bento.scss:7` (`padding: var(--bento-pad, var(--space-6));`)
- Modify: `dashboard/dashboard.scss` (delete `.kpi-row` grid :12-20, `.label` :166-173, `.value-row` :175-180, `.value` :182-188, `.meta` :190-195; keep `.bento-grid` gap, `.up/.down/.dividend`), - Modify: `transaction-list/transaction-list.scss:125-192` (delete `.kpi-row`, `.label`, `.value`, `.meta`; keep `.kpi-link`), `dividend-list/dividend-list.scss:1-60` (delete `.kpi-row`, `.label`, `.value`, `.meta`; keep `.swatch`), `realized-pnl/realized-pnl.scss:1-56` (delete the `.kpi-row` block); each component's `styleUrl: './x.scss'` becomes `styleUrls: ['./x.scss', '../kpi-cards.scss']` (dashboard :86, transaction-list :67, dividend-list :93, realized-pnl :68); each page keeps a one-line `.kpi-row { margin-bottom: 1.25rem; }` (the dashboard has none — its `.bento-grid` gap spaces it)
- Modify: `accounts/accounts-list.scss:40-65` (`.acct-total { container-type: inline-size; }`, `.acct-total-value { font-size: clamp(18px, 12cqi, var(--fs-value)); white-space: nowrap; }`), `accounts/account-detail.scss:7-18,47-52` (`.account-header { container-type: inline-size; }`, `.balance-value { font-size: clamp(18px, 12cqi, var(--fs-value)); white-space: nowrap; font-variant-numeric: tabular-nums; }`)
- Test: `frontend/src/app/theme/kpi-values.spec.ts` (new; reads the stylesheets like `theme/contrast.spec.ts` reads `styles.scss`)

**Interfaces:**
- Produces `kpi-cards.scss` (everything nested under `.kpi-row` so nothing leaks):
  ```scss
  .kpi-row {
    display: grid; gap: 1.25rem; grid-auto-rows: minmax(140px, auto);
    grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
    app-bento { container-type: inline-size; display: grid; height: 100%; }
    .label { /* the shared label rule: --app-text-muted, 0.75rem, 700, 0.08em, margin-bottom 0.5rem, uppercase */ }
    .value-row { align-items: baseline; display: flex; flex-wrap: wrap; gap: 0.5rem; }
    .value { color: var(--app-text); font-size: clamp(18px, 12cqi, var(--fs-value)); font-variant-numeric: tabular-nums;
             font-weight: 800; line-height: 1.1; min-height: 1.1em; white-space: nowrap;
             &.up { color: var(--app-trend-positive); } &.down { color: var(--app-trend-negative); }
             &.dividend { color: var(--app-dividend); } &.primary { color: var(--app-primary); } }  // `.kpi-row .value` (0,2,0) would otherwise beat the pages' `.dividend` / `.primary` (0,1,0)
    .meta { color: var(--app-text-muted); font-size: 0.8rem; font-variant-numeric: tabular-nums; font-weight: 700; margin-top: 0.75rem; }
    @media (max-width: 759.98px) { --bento-pad: var(--space-4); gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  ```
  The page-level colour utilities `.dividend` (dashboard.scss:667, dividend-list.scss:66) and `.primary` (dividend-list.scss:62) stay for non-KPI text; the `.value.*` variants above keep the KPI colours. The dividends page keeps its own `.kpi-row .label { display: flex; align-items: center; gap: 0.4rem; }` override (dividend-list.scss:20-28) so the dashed `.swatch` box survives.

- [ ] **Step 1: Write the failing spec** `theme/kpi-values.spec.ts`: reads `src/app/components/portfolio/kpi-cards.scss` and asserts it contains `clamp(18px, 12cqi, var(--fs-value))`, `white-space: nowrap`, `container-type: inline-size`, `repeat(2, minmax(0, 1fr))`, `--bento-pad: var(--space-4)`; reads `ui/bento/bento.scss` and asserts `var(--bento-pad, var(--space-6))`; asserts none of the four page stylesheets still contains `font-size: 2rem` or `font-size: 1.35rem` inside a `.value` rule (regex `\.value\s*\{[^}]*font-size`); asserts each of the four components' `styleUrls` lists `kpi-cards.scss` (read the `.ts` files). Run → fails.
- [ ] **Step 2: Implement** the stylesheet, the `styleUrls`, the deletions, the bento padding variable and the 現金帳戶 rules.
- [ ] **Step 3: Run** `--include src/app/theme/kpi-values.spec.ts` and the four pages' spec globs (class-based assertions such as `.value.up` must still pass) → green.
- [ ] **Step 4: Commit** — `fix(spa): KPI and summary values scale to their card (clamp + inline-size container); shared kpi-cards.scss; 2×2 phone KPI grid (polish-layout-v2 D3)`.

---

### Task 5: Whole suite, build and browser verification (2.4)

**Files:**
- No source change. Report dir `~/reports/home-hub-polish-pr-b-<UTC stamp>/` (screenshots, `probe.json`, `NOTES.md`, the script). Script modelled on `~/reports/home-hub-playwright-stubs/pr_a_shots.py` (imports `feeshots` / `pr7sweep` from that dir; stubbed APIs, fictional data).

- [ ] **Step 1: Full suite + build** — `cd frontend && npx ng test --watch=false && npx ng build --output-path <scratchpad>/pr-b-dist`. Expected: all green; warnings limited to the pre-existing `dashboard.scss` budget notice and protobufjs.
- [ ] **Step 2: Serve** — copy `pr-b-dist/browser/*` flat into `~/workspace/home-hub-demo-preview/redesign-v2-pr101/dist/`, `python3 -I redesign_server.py` on 127.0.0.1:18081 (stop it at the end).
- [ ] **Step 3: Measure** (Playwright 1.49.1 in Docker, light + dark). Summary stub: `total_market_value` 12,345,678.34, `total_assets_twd` 3,021,869.34, `estimated_pnl_with_dividends` 292,227.2, `total_dividends` 58,920, holdings with one TW and one US row (US native PnL 1,326):
  - `/portfolio` at 360, 390, 1100, 1440: every `.kpi-row .value` has `scrollWidth ≤ clientWidth` and computed `font-size` between 18 px and 32 px; at 360 and 390 the four KPI cards form two rows of two (two distinct `top` values); text of 總市值 == `$12,345,678`, hero == `3,021,869`, holdings PnL `+$292,227` and `+1,326.00 USD`; record the 總市值 text width vs its cell width (Review Focus 4).
  - `/portfolio/transactions` at 390: 買進金額 card with 2,871,432 → `$2,871,432` on one line (no clip); fee share meta keeps two decimals (`佔成交額 0.27%`-style).
  - `/portfolio/realized-pnl` at 390 and 1100: first card `+$…`, 賣出成本費用 `$…`.
  - `/portfolio/dividends` at 390: 已記錄股利 `$58,920`.
  - `/portfolio/accounts` at 390 and 1440: `.acct-total-value` `$150,000`-style, no clip; `/portfolio/accounts/<id>` header `.balance-value` `$438,360`, `scrollWidth ≤ clientWidth`.
  - Phone KPI card padding: `getComputedStyle(.kpi-row .bento).padding` == 16px at 390.
- [ ] **Step 4: Write `NOTES.md`** with the measured numbers and before / after screenshot pairs (stubbed fixtures only; nothing from the owner's data). Stop the preview server.

---

## Not in PR B (follow-ups to file after merge)

- Route the list rows (交易紀錄 / 股利 / 已實現 rows and bars / 現金 rows) through `formatMoney` once the live row requirements are modified to say so — note two shapes it must keep: `formatCash` prints foreign cash with no ISO suffix (`−2,379.54`) and `formatNativeAmount` keeps 4 decimals for `GBp`.
- `components/portfolio/money.ts#formatNativeAmount` still defaults TWD to the zh-TW currency style with decimals; only foreign callers remain, but the default could become `formatMoney`.
- Phone collapsed holdings rows (two lines) are PR D 4.3.
