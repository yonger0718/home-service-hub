# Redesign v2 — PR 3 (Portfolio dashboard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/portfolio` per the spec: 總資產 hero with composition bar, relabelled KPI row, the two-series net-worth chart with legend/tooltip/guide, a 持股配置 + 待收股利 card, and holdings restyled as list rows — all from data the services already return.

**Architecture:** `PortfolioDashboardComponent` stays one standalone component; its template is reorganised into hero / KPI / chart+allocation / holdings sections. Pure helpers (today change, composition, allocation, pending-dividend rows, quote-time label) move to `components/portfolio/dashboard/dashboard-math.ts` so they are unit-testable without the chart stub. The market `SegToggle` and quote-time line move into the page header via a new optional `subtitle` input on `PageHeaderComponent`. Chart stays PrimeNG/Chart.js; the hover guide is a small inline Chart.js plugin.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), PrimeNG chart (Chart.js), Vitest, SCSS with existing `--app-*` tokens only.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D9 Dashboard, D10 colours) and `specs/frontend-portfolio-dashboard/spec.md`. Visual reference: `design_handoff_v2/design_refs/Main.dc.html` (lines 95–300), `PortfolioPhone.dc.html` (lines 51–157).

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-dashboard` from `origin/main` a17476c (PR 2 merged). Dummy root `.env` present. Single spec: `cd frontend && npx ng test --watch=false --include <spec>`; full suite 112 files / 1548 tests at a17476c. The runner shares globals across spec files — restore anything global a spec touches.

## Not in this PR

- 交易紀錄 / 股利 / 已實現 / 現金 / 匯入 pages (PRs 4–6); accounting timeline bar (PR 7).
- No backend, API, schema or migration change (design Non-goals). No new tokens.

## Decisions this plan makes (code facts checked at a17476c)

1. **Quote time line** `行情更新於 HH:mm · 盤中|台股收盤`: time = `RecalcStatus.quote_refresh.finished_at` (`models/portfolio.model.ts:174-179`, `PortfolioService.getRecalcStatus()` `services/portfolio.service.ts:222`), formatted `HH:mm` Asia/Taipei — the same source Settings uses. Session tag is client-side: 盤中 when Taipei local time is Mon–Fri 09:00–13:30 (`scheduler.py:92-96` gates quotes to that session), else 台股收盤. Line hidden when the status is unavailable. The dashboard already polls this endpoint in `pollRecalcStatus()` (`dashboard.ts:~370`); one extra read after `reloadSummary()` feeds the line.
2. **今日 pill**: amount = `summary.total_day_pnl` (`portfolio.model.ts:288`); percent = `day_pnl / (total_assets_twd − day_pnl) × 100`, `—` when the denominator ≤ 0.
3. **Composition bar**: 股票市值 = `total_market_value`, 交割戶現金 = `total_cash_twd`; 待收股利 = `total_pending_dividends_net` legend-only (spec scenario "not added to cash").
4. **待收股利 list** = `getDividends({ limit: 100, sort: 'ex_dividend_date:desc' })` (`portfolio.service.ts:114`; sort fields `portfolio_service.py:1464`) filtered `receipt_status === 'pending'`, ordered by `payment_date ?? ex_dividend_date` ascending, first three. Date tile from `payment_date` (month `M月`, day `DD`); when `payment_date` is null use `ex_dividend_date` and append `· 除息` to the note. Note `每股 {cash_dividend_per_share} · {quantity_at_record_date} 股` (omit a missing half). Amount = `amount` (the dividend page sums `amount` as the received figure, `dividend-list.ts:77`), shown `+{amount}` in `--app-dividend`. Name: `portfolioService.getCachedHolding({symbol, market})?.name ?? symbol` (same resolution the dividend page uses via `nameFor`). Request failure → the 待收股利 block is omitted, no error (same policy as badges).
5. **Allocation colours** (D10 says a five-colour series starting with `--app-primary`; no new tokens allowed): `ALLOCATION_SERIES = ['--app-primary', '--app-accent', '--app-dividend', '--app-success', '--app-warning']` read at draw time; holdings beyond five are merged into 其他 in `--app-text-muted`. Percent = `market_value / Σ market_value` over `flatHoldings()` (TWD values), sorted desc.
6. **Holdings order**: within each market group sort by `market_value` desc (canvas `5 檔 · 依市值排序`); data per row unchanged (D9). Group header shows `{n} 檔 · {Σ market_value}`.
7. **Market toggle** moves to the page header with labels 全部 / 台股 / 美股 / 英股 (`TW` / `US` / `LSE`), hidden when a single market is held; the hero is hidden while filtered (as the 總資產 tile is today).
8. **KPI 預估含息損益 footnote** becomes the one-line `價差 {x} · 股利 {y} · 待收 {z}` (spec) replacing the `<details>` breakdown; the "估算・非可用現金" hint stays as the card's `title` attribute (tooltip), not a visible line.
9. **PageHeaderComponent** gains `subtitle = input<string | null>(null)` rendered as a muted line under the `h1` on every width; no other page uses it yet.

## Global Constraints

- Copy (verbatim): hero `總資產 · TWD`, pill `今日`, legend `股票市值` / `交割戶現金` / `待收股利`; KPI labels `總市值` (footnote `總成本 {x}`), `預估含息損益` (footnote `價差 … · 股利 … · 待收 …`), `年化報酬率 XIRR` (footnote `{range} · 全市場` or `{range} · {market}`), `已記錄股利` (footnote `含舊資料 · 不代表已核實收款`); chart title `淨值走勢`, series `帳戶總值` / `持股淨值`, legend third entry `兩線差距 = 交割戶現金`, phone caption `兩線之間是交割戶現金 {cash}`, badge label `帳戶總值`; card `持股配置`, block `待收股利` with link `全部` → `/portfolio/dividends`; holdings `持股明細`, `{n} 檔 · 依市值排序`, columns 名稱 / 股數 / 現價 / 今日 / 市值 (TWD) / 預估含息損益; header `投資概覽`, `行情更新於 HH:mm · 盤中|台股收盤`, buttons `匯入券商`, `刷新行情`; market toggle 全部 / 台股 / 美股 / 英股. When a market filter is active KPI labels carry ` · {market}` as today.
- Range options **1M / 3M / YTD / 1Y / 5Y**, default **1Y** (unchanged).
- Colours: 帳戶總值 line `--app-accent`, band `--app-accent-soft`; 持股淨值 line `--app-trend-positive` / `--app-trend-negative` (+ `-soft` fill) by the sign of the period change; dividend values `--app-dividend`; cards `--app-surface` + `--app-card-shadow`, no gradients, no coloured borders. Colours read via `getComputedStyle` at draw time; chart refreshes on `dark$` / `gainLoss$` (existing `refreshChartTheme`).
- Breakpoints: `< 760px` phone; allocation card stacks below the chart `< 1024px`; KPI row auto-fit min 220px.
- Numbers `tabular-nums`; TWD via the existing `formatCurrency`; percentages via `PctBadge`.
- No new tokens, no API change. Match file style (signals, `computed`, comments only for non-obvious why).

## Review Focus

1. **Empty / partial data** — no holdings but cash > 0; holdings but no history points; `total_assets_twd` `'0'` with cash (existing fallback in `totalAssetsValue`); summary with `market_totals` missing → hero, KPI and allocation must render without NaN/Infinity or an empty bar. → Task 1 and Task 4 tests.
2. **Market filter on** — hero hidden, KPI labels suffixed, allocation card shows the filtered holdings only, chart title gets ` · 全市場`. → Task 1/4 tests.
3. **Pending dividends with missing fields** — null `payment_date`, null `cash_dividend_per_share`, unknown symbol name, request error → tile falls back to 除息 date, note trimmed, name = symbol, block omitted on error. → Task 4 tests.
4. **Chart legend latest values and cash when the last point lacks cash** — `total_cash_twd` `'0'`/absent → cash shows `0`, band degenerates (lines coincide) without errors. → Task 3 tests.
5. **Theme flip while hovered** — `refreshChartTheme` rebuilds options; the guide plugin must not keep a stale colour/canvas reference. → Task 3 test that the plugin reads colours at draw time (asserts via options rebuild), plus the controller's screenshot pass.

---

### Task 1: Page header subtitle, quote-time line, market toggle, 總資產 hero

**Files:**
- Modify: `frontend/src/app/components/shell/page-header/page-header.{ts,html,scss,spec.ts}` (subtitle input)
- Create: `frontend/src/app/components/portfolio/dashboard/dashboard-math.ts` (+ `dashboard-math.spec.ts`)
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.{ts,html,scss,spec.ts}`

**Interfaces:**
- Produces (`dashboard-math.ts`):
  ```ts
  export function todayChange(summary: Pick<PortfolioSummary,'total_day_pnl'|'total_assets_twd'>): { amount: number; percent: number | null };
  export function composition(summary: Pick<PortfolioSummary,'total_market_value'|'total_cash_twd'|'total_pending_dividends_net'>): { stocks: number; cash: number; pending: number; stocksPct: number; cashPct: number }; // pcts of stocks+cash, 0/0 → 0
  export function quoteTimeLabel(finishedAtIso: string | null | undefined, now: Date): string | null; // '行情更新於 13:30 · 盤中' | '… · 台股收盤' | null
  export const MARKET_LABELS: Record<MarketCode, string>; // TW 台股, US 美股, LSE 英股
  ```
- `PageHeaderComponent.subtitle = input<string | null>(null)`.

- [ ] **Step 1: Failing tests.**
  - `dashboard-math.spec.ts`: `todayChange` → `{10, 0.806…}` for day 10 / assets 1250; percent `null` when assets − day ≤ 0. `composition` → stocks 1000 / cash 250 → `80 / 20`, pending 14 600 not in the bar; all zero → pcts 0. `quoteTimeLabel('2026-10-22T05:30:00Z', Thu 2026-10-22 05:31Z)` → `行情更新於 13:30 · 盤中`; `'…T09:00:00Z'` (17:00 Taipei) → `· 台股收盤`; Saturday → 台股收盤; null → null.
  - `page-header.spec.ts`: `subtitle` renders `.page-subtitle` under the h1; absent → not rendered.
  - `dashboard.spec.ts` (existing stubs): header shows subtitle `行情更新於 13:30 · 盤中` when `getRecalcStatus` resolves `{ quote_refresh: { state:'completed', finished_at:'2026-10-22T05:30:00Z' } }` (fixed clock via a `DASHBOARD_CLOCK` token like Settings' `SETTINGS_CLOCK`), no subtitle on error; market toggle in the header with labels `全部, 台股, 美股` for TW+US holdings and absent for TW only; hero `.hero` shows `總資產 · TWD`, formatted total, 今日 pill `+$10` with badge, composition bar `role="img"` `aria-label="資產組成：股票 80.0%，現金 20.0%"`, legend 待收股利 `14,600` with `.swatch.dashed`; filtered (`selectMarketFilter('US')`) → no `.hero`. Replace the old `renders the total assets tile above the market value tile` test.
- [ ] **Step 2: Run** the three specs → FAIL.
- [ ] **Step 3: Implement** per Decisions 1–3, 7, 9. Header: `<app-page-header [subtitle]="quoteLine()">` with projected `app-seg-toggle` (market) + 匯入券商 + 刷新行情. Hero markup per `Main.dc.html:95-128` (number in `--fs-value` or larger, pill in trend colour via existing `.up/.down`, two-segment bar + three-entry legend). Load the status once after each `reloadSummary()` success (`getRecalcStatus().pipe(catchError(() => of(null)))`).
- [ ] **Step 4: Run** the three specs → PASS.
- [ ] **Step 5: Commit** `feat(dashboard): header quote line and market toggle, 總資產 hero`

---

### Task 2: KPI row

**Files:**
- Modify: `dashboard.{html,scss,spec.ts}`

- [ ] **Step 1: Failing tests** (`dashboard.spec.ts`): labels `總市值 / 預估含息損益 / 年化報酬率 XIRR / 已記錄股利` in order; 總市值 footnote `總成本 $900`; 預估含息損益 footnote `價差 $100 · 股利 $20 · 待收 $5` (fixture pending 5) and value `.up` with a PctBadge; XIRR footnote `1Y · 全市場`, after `selectRange('3M')` → `3M · 全市場`; filtered US → labels suffixed ` · US`, XIRR footnote `1Y · US`; 已記錄股利 value has class `dividend`, footnote `含舊資料 · 不代表已核實收款`. Update/replace the existing `renders estimated main/holding amounts and breakdown` and dash tests to the new footnote (`—` for an unavailable component).
- [ ] **Step 2–4:** run → FAIL, implement (remove the `<details>` breakdown; `title="估算・非可用現金"` on the card; cards `--app-surface` + shadow — drop `primary-tile` gradient styles), run → PASS.
- [ ] **Step 5: Commit** `feat(dashboard): KPI labels and footnotes per spec`

---

### Task 3: Net-worth chart — series, legend, tooltip, hover guide

**Files:**
- Modify: `dashboard.{ts,html,scss,spec.ts}`
- Create: `frontend/src/app/components/portfolio/dashboard/chart-guide.plugin.ts` (+ spec)

**Interfaces:**
- Produces: `export const hoverGuidePlugin: Plugin<'line'>` — `afterDraw` draws a dashed vertical line at the active tooltip x from the chart top to bottom using `getComputedStyle(document.documentElement).getPropertyValue('--app-border')` at draw time; no state kept between draws.
- `dashboard.ts`: `readonly chartLegend = computed(() => ({ total, holdings, cash }))` from the last `chartPoints()` element (`cash = total − holdings`, ≥ 0).

- [ ] **Step 1: Failing tests.**
  - `chart-guide.plugin.spec.ts`: with a fake chart `{ tooltip: { getActiveElements: () => [{ element: { x: 120 } }] }, chartArea: { top: 0, bottom: 200 }, ctx }` the plugin draws one dashed line at x = 120 (spy `moveTo`/`lineTo`/`setLineDash`); no active element → nothing drawn; colour read from the root at draw time (set `--app-border` on the root inside the test and restore it in `afterEach`).
  - `dashboard.spec.ts`: datasets labelled `帳戶總值` (border `--app-accent`, `fill: '+1'`, background `--app-accent-soft`) and `持股淨值`; legend shows the three entries with the latest values `$1,250`, `$1,000`, `$250` and swatches; tooltip `callbacks.title` → date `2026/10/22`, `callbacks.label` per dataset `帳戶總值 $1,250` / `持股淨值 $1,000`, `callbacks.afterBody` → `現金 $250`; `plugins` includes `hoverGuidePlugin`; last point with cash `'0'` → legend cash `$0`; PctBadge labelled `帳戶總值` beside the title; phone mode → legend has class `two-col`. Keep the existing range / convention / animation tests (update dataset labels).
- [ ] **Step 2–4:** run → FAIL, implement (`fill:'+1'` band stays; add `options.plugins.tooltip.callbacks.{title,label,afterBody}`, `interaction.mode='index'` kept; register the plugin via `[plugins]="chartPlugins"` on `p-chart`; legend markup per `Main.dc.html:171-190`, phone caption per `PortfolioPhone.dc.html:120`), run → PASS.
- [ ] **Step 5: Commit** `feat(dashboard): two-series chart legend, tooltip and hover guide`

---

### Task 4: 持股配置 + 待收股利 card

**Files:**
- Modify: `dashboard-math.ts` (+ spec), `dashboard.{ts,html,scss,spec.ts}`

**Interfaces:**
- Produces (`dashboard-math.ts`):
  ```ts
  export const ALLOCATION_SERIES: readonly string[]; // Decision 5 tokens
  export interface AllocationSlice { name: string; symbol: string; pct: number; token: string }
  export function allocation(holdings: readonly StockHolding[]): AllocationSlice[]; // desc, max 5 + 其他, pcts sum to 100 ± 0.1
  export interface PendingRow { id: number; month: string; day: string; name: string; note: string; amount: number }
  export function pendingRows(dividends: readonly Dividend[], nameFor: (d: Dividend) => string, limit = 3): PendingRow[];
  ```
- `dashboard.ts`: `readonly pendingDividends = signal<PendingRow[] | null>(null)` (null = unavailable → block omitted); loaded with the summary.

- [ ] **Step 1: Failing tests.**
  - `dashboard-math.spec.ts`: `allocation` over 6 holdings → 5 slices + 其他, sum 100 ± 0.1, desc order, tokens in series order; empty → `[]`; zero market values → `[]`. `pendingRows`: pending rows only, ordered by `payment_date ?? ex_dividend_date` asc, max 3; tile `11月` / `18`; note `每股 0.48 · 20,000 股`; null `payment_date` → ex date + ` · 除息`; null per-share → `20,000 股`; name via `nameFor`.
  - `dashboard.spec.ts`: card `持股配置` beside the chart (grid class), bar segments count = slices, list rows `name symbol pct%`; 待收股利 block with ≤ 3 rows, amounts `.dividend`, link `全部` `href="/portfolio/dividends"`; `getDividends` error → no 待收股利 block, card still renders; filtered US → allocation uses filtered holdings; `getDividends` called with `{ limit: 100, sort: 'ex_dividend_date:desc' }`.
- [ ] **Step 2–4:** run → FAIL, implement (layout: chart card `2fr` + allocation card `1fr` at ≥ 1024, stacked below; markup per `Main.dc.html:224-256`), run → PASS.
- [ ] **Step 5: Commit** `feat(dashboard): 持股配置 bar and 待收股利 list`

---

### Task 5: Holdings list restyle

**Files:**
- Modify: `dashboard.{ts,html,scss,spec.ts}`, `holdings-table.grouping.spec.ts` (ordering)

- [ ] **Step 1: Failing tests**: header `持股明細` + `{n} 檔 · 依市值排序`; column header row 名稱 / 股數 / 現價 / 今日 / 市值 (TWD) / 預估含息損益 (hidden `< 760px` via class, rows show the phone layout per `PortfolioPhone.dc.html:131-157`); rows ordered by `market_value` desc within a group; group header `台股 · 4 檔 · $2,612,900` style text; each row keeps `role="button"`, expand toggle, detail panel contents and the existing US/LSE native branches (assert one TW and one US row unchanged in data); 40px abbr square with the allocation token of that holding (first char / symbol tail per canvas `abbr`).
- [ ] **Step 2–4:** run → FAIL, implement (restyle only; `groupHoldingsByMarket` gets `sortBy='market_value', 'desc'` default for the dashboard call), run → PASS.
- [ ] **Step 5: Commit** `feat(dashboard): holdings as list rows sorted by market value`

---

### Task 6: Verification and bookkeeping

- [ ] **Step 1:** full suite + `npm run build` pass; `npx @fission-ai/openspec validate redesign-frontend-v2 --strict` valid.
- [ ] **Step 2 (controller):** screenshots at 390 / 820 / 1280 / 1440, light / dark, asian / western on the demo build (demo has no portfolio API → use a Playwright route stub serving a fixture summary / history / dividends / recalc status from `dashboard.spec.ts` fixtures scaled up) — hero, KPI, chart legend + tooltip on hover (desktop), allocation card beside/below, holdings rows; convention flip repaints the holdings line.
- [ ] **Step 3:** tick tasks 3.1–3.5 in `openspec/changes/redesign-frontend-v2/tasks.md`; amend `specs/frontend-portfolio-dashboard/spec.md` only if an implemented detail contradicts it (note Decisions 1, 4, 5 as implementation notes if the reviewer asks); commit `docs(openspec): tick redesign-frontend-v2 dashboard tasks`.
