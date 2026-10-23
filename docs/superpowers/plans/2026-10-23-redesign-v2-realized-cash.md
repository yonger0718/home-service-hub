# Redesign v2 — PR 5 (已實現損益 + 現金帳戶) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/portfolio/realized-pnl` (four summary cards, 每月已實現 bars, expandable sell rows, filter card with market / 僅當沖) and `/portfolio/accounts` + `/portfolio/accounts/:id` (master-detail at ≥ 1024 px, account cards, cash-transaction rows with a running 餘額 column) per the OpenSpec change, frontend only.

**Architecture:** Same shape as PR 4: pure helpers next to each page (`realized-math.ts`, `cash-rows.ts`), filter-wide loading via the shared `allPages`, signals + `computed` for everything derived, OnPush, `LayoutModeService` for phone / sheet / panes. The accounts master-detail is a thin wrapper page that hosts the existing list and detail components side by side in `panes` mode and falls back to the existing two-page flow below it. No API, backend, worker or migration change.

**Tech Stack:** Angular 21 standalone / signals / OnPush, Vitest (`cd frontend && npx ng test --watch=false --include <spec>`), PrimeNG only where it already is (datepicker, select, paginator, dialogs), the `ui/` kit (`app-bento`, `app-btn`, `app-seg-toggle`, `app-switch`, `app-tag`, `app-pct-badge`, `app-page-header`), shared portfolio helpers (`paging.ts`, `money.ts`, `square.ts`, `period.ts`).

**Spec:** `openspec/changes/redesign-frontend-v2/specs/frontend-realized-pnl/spec.md`, `specs/frontend-portfolio-accounts/spec.md`, `design.md` D9 (已實現損益, 現金帳戶) + D10, `design_handoff_v2/design_refs/PfRealized.dc.html`, `PfCash.dc.html` (copy + sample data), tasks 5.1–5.2.

## Decisions this plan makes

1. **Realized filter-wide set.** `GET /realized-pnl` has no market filter and the summary (`filter_scope_total`, `filter_scope_count`, `ytd_*`) has no gain/loss counts, no month split, no 當沖 or fee totals. The page therefore loads every event of the active filter with `allPages(offset => getRealizedPnl({ ...filters, sort, limit: 200, offset }), 200, 50)` (`services/portfolio.service.ts:120`, router `limit ≤ 200` at `routers/realized_pnl.py:25`; cap note `⋯ 僅載入前 10,000 筆`), with the same generation guard / unsubscribe pattern as `transaction-list.ts` `loadSummary()`. The cards, the bars and the market filter work on that set. Server paging of the list stays for market 全部; with 台股 / 美股 selected the list pages the loaded set client-side honouring `limit` (the dividends status-mode pattern in `dividend-list.ts`); a load failure hides the cards and bars silently and keeps the server page.
2. **Four cards** (copy verbatim from the canvas): `{year} 年已實現` (range label: `{year} 年` when `query.year` is set, else `periodLabel(query)`; value = server `filter_scope_total` on market 全部, else Σ `realized_pnl` of the client set; footnote `{n} 筆 · {g} 筆獲利、{l} 筆虧損` from the client set), `本月（{M} 月）` (Σ `realized_pnl` of events whose `trade_date` is in the current calendar month, footnote `{n} 筆`), `當沖` (Σ over `is_day_trade`, footnote `{n} 筆 · 交易稅減半`), `賣出成本費用` (Σ `fee + tax`, footnote `賣出手續費＋交易稅`). Values use `--app-trend-positive` / `--app-trend-negative` by sign (`.up` / `.down` as on transactions), `−` U+2212 for negatives, whole TWD. Phone: only the first two cards.
3. **Monthly bars** are plain DOM (no Chart.js): a `.bars` grid of 12 columns for `barsYear = query.year ?? year(query.date_to) ?? currentYear`; each column has a `.bar` whose height is `|total| / max|total| × 100 %` above or below a `.zero` line, colour by sign, the amount printed at the bar end (`groupedInteger`, signed), the label `{M}月` beneath; months after the current month (only when `barsYear === currentYear`) get `.future` (muted); months with no events draw no bar. Card title `每月已實現`, caption `柱上數字為當月合計`, and a visually-hidden paragraph `{year} 年每月已實現損益：{M} 月 {±amount}、…` listing the non-zero months in order.
4. **Rows** are `<button class="rz-row" aria-expanded>` (one expanded at a time, `expandedKey`): `stockSquare(symbol, name)` square, `{name}` + `{symbol}` (+ `· 美股` for non-TW), `app-tag variant="warning"` 當沖 when `is_day_trade`, meta `{MM/DD} · {qty} 股 · 均價 {avg} → 賣出 {price} · {broker}` (TW: 2 dp; foreign: `formatNativeAmount(native_avg?, currency)` — the event has no native avg cost, so foreign avg = `native_cost / quantity` and price = `native_sell_price`), right side the realized amount (TWD, signed, trend colour) and a percentage pill `realized_pnl / cost_out × 100` (1 dp, signed; `app-pct-badge` if it takes a number, else `.pct-pill`), hidden when `cost_out` is 0. Expanded panel rows: 成交總額, 手續費, 交易稅 (label 交易稅（當沖）when `is_day_trade`), 淨收入, 賣出成本, 已實現損益; foreign sells show the native figure as the main value with the TWD beneath and one extra row `匯率 {proceeds_gross / native_proceeds_gross, 2–4 dp}`. Keep the existing 融券 / `no_long_inventory` / `no_short_inventory` markers inside the row (they are data warnings, not design).
5. **Filter card** (`app-bento`, title 篩選): search (existing debounce + `resolveSymbol`), market `app-seg-toggle` 全部 / 台股 / 美股 (`market = signal<'ALL'|'TW'|'US'>('ALL')`, client-side; 美股 = every non-TW market), 僅當沖 `app-switch` (`ariaLabel="僅當沖"`, drives `day_trade_only`), date range (existing `p-datepicker` + year presets select), broker `app-seg-toggle` (existing). 清除篩選 text button shown only while a filter is active (`hasActiveFilters` includes market and 僅當沖). Sort select and rows-per-page stay as today.
6. **Realized header:** `<app-page-header [subtitle]="…" [actionsBelowOnPhone]="true">` with subtitle `{range label} · {n} 筆賣出` (`n` = client set length when loaded, else server `total`); no actions.
7. **Accounts master-detail** is a new wrapper `PortfolioAccountsPageComponent` (`components/portfolio/accounts/accounts-page.{ts,html,scss}`) routed for both `portfolio/accounts` and `portfolio/accounts/:id` (`app.routes.ts:75-76`). In `panes` mode it renders one `app-page-header` (title 現金帳戶, subtitle `券商交割戶與複委託帳戶的現金`, actions 記錄入出金 soft → the existing `app-cash-flow-form` toggle, 新增帳戶 soft → the list's create dialog) and two columns: left `<app-portfolio-accounts-list [master]="true" [selectedId]="…">` (≤ 360 px), right `<app-portfolio-account-detail [accountId]="selectedId()" [embedded]="true">`. `selectedId` = the route `:id`, else the first active account once the list has loaded (`(loaded)` output; the wrapper then `router.navigate(['/portfolio/accounts', id], { replaceUrl: true })`). Below `panes` the wrapper renders only the component the route names today (list at `/portfolio/accounts`, detail at `/:id`), unchanged. The wrapper does not touch `PaneStateService` (the page is always two columns; D2's auto-collapse is for pages whose pane opens and closes).
8. **List component in master mode:** cards become `<button class="acct-card" aria-pressed>` with the `--app-primary` ring on the selected one; click still navigates to `/portfolio/accounts/:id` (the wrapper reacts to the route). Card content: broker icon square, nickname, `{brokerLabel} · {currency}` (+ `· 期初 {opening_balance}（{opening_date}）` on the selected card as on the canvas), native balance (`--app-trend-negative` when negative), tags 啟用中 / 已停用 and 未記錄入金 (existing `isOverdraft`). The 現金帳戶總額 · TWD card uses the accent background (`--app-accent` fill, `--app-on-accent` text if that token exists in `styles.scss`, else `--app-bg`) and the footnotes `{n} 個帳戶待補入金紀錄，未計入` (count of overdraft accounts — their native balance is negative; the server total already includes/excludes per `cash_account_service.list_accounts`, which this plan does not change, so the footnote names them without altering the number) and the existing `未換算: …` / `以 TWD 換算`. The page header (非 master) keeps today's 新增帳戶 action; in master mode the list renders no header.
9. **Detail component inputs:** `accountId = input<number | null>(null)` (used instead of `route.paramMap` when set, re-loading on change) and `embedded = input(false)` (no `app-page-header`, no back button; the account header card stays). The balance chart, window toggle, edit dialog, 新增交易 dialog, merge toggle, filters, paginator and delete flow are unchanged.
10. **Cash-transaction rows** (`cash-rows.ts`): columns 日期 (`YYYY/MM/DD`), 項目 (type icon in a soft square + title + subtitle), 來源 tag, 金額 (signed, `--app-trend-*`, `−` U+2212), 餘額. Title by type: deposit 入金, withdraw 出金, buy_settle `交割 · 買進`, sell_settle `交割 · 賣出`, trade (merged) `交割` with subtitle `已合併買賣 {n} 筆` (`child_legs.length`), dividend_cash 股利, fee 手續費, tax 交易稅, interest_in 利息收入, margin_interest 融資利息, wire_fee 匯費, fx_convert 換匯. Subtitle = `note` when present, else the merged-legs sentence, else none. **The API carries no related-transaction shares / trade date and no dividend per-share data** (`CashTransaction` has only `related_transaction_id` / `related_dividend_id` / `note`, `models/portfolio.model.ts:453-467`; auto-derived legs are created without a note, `cash_backfill_service.py:308` copies the flow note only), so the spec's row-subtitle scenario is met only when `note` carries that text; the spec gets an implementation note and the owner a follow-up option (embed `related_symbol / related_quantity / related_trade_date / per_share` in `CashTransactionOut`, backend). Source tag: `manual` → 手動, `csv_import` → 匯入, `auto_derive` → 股利 when `related_dividend_id`, else 交易. Expand (child legs) stays as today.
11. **Running balance:** `runningBalances(rows, currentBalance)` returns `balanceAfter[i] = current − Σ amount[0..i-1]` (row 0 = current balance), only when the page is contiguous to the present: `offset === 0`, `sort === 'txn_date:desc'`, no `date_from` / `date_to` / `type` filter (merge toggle allowed); otherwise `null` and the 餘額 column is omitted (header cell too). Spec scenario: current 438,360, newest row +20,000 → rows show 438,360 then 418,360.
12. **Phone (< 760 px):** realized — two cards, bars card scrolls horizontally inside its card (12 fixed-width columns), rows collapse to square · name + tag · meta (two lines) · amount with the pill beneath; accounts — the existing two-page flow; cash rows collapse to icon+title/subtitle · amount with the date beneath (source tag inline after the title, 餘額 beneath the amount when present).
13. **Spec notes** (Task 7): realized market filter is client-side; bars use `barsYear`; running-balance condition; subtitle data limits; both pages' cap notes.

## Global Constraints

- Frontend only; no change under `services/`, no API shape change; `Dividend`/`Transaction`/`CashTransaction` models unchanged (type-only additions allowed).
- Tokens only (`--app-*`); numbers `tabular-nums`; negatives `−` U+2212 on rows and cards; TWD whole numbers via `groupedInteger` / `formatCurrency`; native via `formatNativeAmount` (suffix style, grouped); fx via `formatFxRate`.
- Copy (verbatim): realized cards `{year} 年已實現` / `本月（{M} 月）` / `當沖` / `賣出成本費用`, footnotes `{n} 筆 · {g} 筆獲利、{l} 筆虧損`, `{n} 筆`, `{n} 筆 · 交易稅減半`, `賣出手續費＋交易稅`; card 每月已實現 + `柱上數字為當月合計`; filter 篩選 with 全部 / 台股 / 美股, 僅當沖, 券商; row meta `{date} · {qty} 股 · 均價 {avg} → 賣出 {price} · {broker}`; detail labels 成交總額 / 手續費 / 交易稅 / 交易稅（當沖） / 淨收入 / 賣出成本 / 已實現損益 / 匯率; empty `無已實現損益`. Accounts: header 現金帳戶 + `券商交割戶與複委託帳戶的現金`, actions 記錄入出金 / 新增帳戶, total card `現金帳戶總額 · TWD`, footnote `{n} 個帳戶待補入金紀錄，未計入`, tags 啟用中 / 已停用 / 未記錄入金, detail 目前餘額 / 餘額走勢 / 現金交易 / 合併同筆交易 / 新增交易, column heads 日期 / 項目 / 來源 / 金額 / 餘額, sources 手動 / 交易 / 股利 / 匯入.
- Breakpoints: phone `LayoutModeService.mode() === 'phone'` (`max-width: 759.98px`), sheet 760–1023, panes ≥ 1024 (`'panes'`); tests force modes with `TestBed.inject(LayoutModeService).set('phone' | 'sheet' | 'panes')` and `set(null)` in `afterEach`.
- Specs restore every global they touch (`vi.useRealTimers()`, `localStorage`, spies); no `.only`.
- Existing strings kept: `查詢失敗，請檢查篩選條件`, `現金交易讀取失敗`, `無已實現損益`, loading indicators.
- Style budget 10 kB per component scss (trim, never raise); `npm run build` must add no warning beyond the pre-existing `dashboard.scss` 62 B and protobufjs notices.

## Review Focus

1. **Stale filter-wide realized load** — a newer filter's set must never be overwritten by an older response; a market change must not refetch. → Task 1 race test; Task 2 market-change test (no API call).
2. **Bars with no events or one month only** — `max|total| = 0` must not divide by zero; a single month renders one full-height bar; future months grey only in the current year. → Task 1 `monthlyBars` tests.
3. **Foreign sell with `native_proceeds_gross` 0 / null** — the 匯率 row must be omitted, not `Infinity`/`NaN`; pct pill hidden when `cost_out` is 0. → Task 3 tests.
4. **Running balance on a merged page** — with 合併同筆交易 on, the merged row's `amount` is the net of its legs; `balanceAfter` must use the row amount, not the legs. → Task 4 test with `child_legs`.
5. **Master-detail route churn** — opening `/portfolio/accounts` in panes mode must navigate once (replaceUrl) to the first active account and not loop when the list reloads; narrowing the window below 1024 must leave the current URL working. → Task 5 tests.

---

### Task 1: Realized math + filter-wide loading

**Files:**
- Create: `frontend/src/app/components/portfolio/realized-pnl/realized-math.ts` (+ `realized-math.spec.ts`)
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.ts` (signals `allEvents`, `allCapped`, `allFailed`, `market`; `loadAll()`; `visibleEvents`), `realized-pnl.component.spec.ts` (component tests for the loading rules)

**Interfaces (produces):**
```ts
export type MarketFilter = 'ALL' | 'TW' | 'US';
export function matchesMarket(e: RealizedPnlEvent, m: MarketFilter): boolean;           // 'US' = every market !== 'TW'
export interface RealizedTotals { total: number; count: number; gains: number; losses: number; month: number; monthCount: number; dayTrade: number; dayTradeCount: number; costs: number }
export function realizedTotals(rows: readonly RealizedPnlEvent[], today: Date): RealizedTotals; // TWD; month = current calendar month of `today`
export interface MonthBar { month: number; total: number; count: number; ratio: number; future: boolean } // ratio = |total| / max|total| (0 when max is 0)
export function monthlyBars(rows: readonly RealizedPnlEvent[], year: number, today: Date): MonthBar[]; // always 12 entries
export function barsYear(q: RealizedPnlQuery, today: Date): number;                      // q.year ?? year(q.date_to) ?? today's year
export function barsAriaText(bars: readonly MonthBar[], year: number): string;          // '2026 年每月已實現損益：1 月 +4,714、4 月 −4,897' (non-zero months only; '無' when none)
export function rangeLabel(q: RealizedPnlQuery): string;                                 // '2026 年' | periodLabel(q)
```
Component: `readonly allEvents = signal<RealizedPnlEvent[] | null>(null)`, `allCapped`, `allFailed`, `market = signal<MarketFilter>('ALL')`, `readonly marketEvents = computed(() => allEvents()?.filter(e => matchesMarket(e, market())) ?? null)`, `loadAll()` called on init and after every filter change (search / date / year / 僅當沖 / broker / sort — sort matters for the client-paged list), never on page change or market change; generation guard + `allSub` unsubscribed on restart and in `ngOnDestroy`; `fetch()`'s request kept in `listSub` the same way.

- [ ] **Step 1: Failing tests** — `realized-math.spec`: fixture of the canvas' five 2026 sells (鴻海 10/02 +13,498 day-trade=false; 台積電 08/21 +2,030 day-trade; AAPL 06/12 +5,701 US; 00878 04/08 −4,897; 0050 03/03 +4,714) with `fee`/`tax` so `costs` = Σ fee + tax → `realizedTotals(rows, 2026-10-22)`: `total 21046`, `count 5`, `gains 4`, `losses 1`, `month 13498`, `monthCount 1`, `dayTrade 2030`, `dayTradeCount 1`; `monthlyBars(rows, 2026, 2026-10-22)`: 12 entries, April `total -4897`, `ratio 4897/13498`, October `ratio 1`, November/December `future true`, February `total 0 ratio 0 future false`; empty rows → all ratios 0, no NaN; `barsYear`: year set → it; date_to `2025-06-30` → 2025; neither → today's year; `barsAriaText` string exactly as above; `rangeLabel` both branches; `matchesMarket` TW / US / LSE under 'US'. `realized-pnl.component.spec`: filter change (僅當沖 on) → one `allPages` load with `day_trade_only: true, limit: 200, offset: 0` plus one list fetch; page change → list only; market change → no API call; older `allPages` response after a newer → dropped and unobserved; load error → `allFailed` true, `allEvents` null, list intact, no toast; capped (50 pages × 200, total 10 001) → `allCapped` true; destroy → both subscriptions unobserved.
- [ ] **Step 2–4:** run → FAIL; implement; run both specs + `realized-pnl.broker-filter.spec.ts` + `realized-pnl-list.foreign-columns.spec.ts` → PASS (adapt their stubs for the new `allPages` calls: a `getRealizedPnl` stub split by `limit === 200`).
- [ ] **Step 5: Commit** `feat(realized): filter-wide event set, totals and monthly bars math`

---

### Task 2: Realized page — header, cards, bars, filter card

**Files:**
- Modify: `realized-pnl.{ts,html,scss}`, `realized-pnl.component.spec.ts`.

**Interfaces (consumes Task 1):** `realizedTotals`, `monthlyBars`, `barsYear`, `barsAriaText`, `rangeLabel`, `marketEvents()`, `allCapped()`, `allFailed()`.

- [ ] **Step 1: Failing tests** — header subtitle `2026 年 · 5 筆賣出` with `year: 2026` (server `total` 5 before the set lands, set length after); four `app-bento.kpi-card` in order with the labels/footnotes of Decision 2 and values `+21,046` (server `filter_scope_total` on 全部) / `+13,498` / `+2,030` / `$1,023` (fixture Σ fee+tax), `.up`/`.down` by sign; 台股 selected → the first card uses the client Σ (AAPL excluded → `+15,345`) and footnote counts drop to `4 筆 · 3 筆獲利、1 筆虧損`; `allFailed` → no cards, no bars, list intact; capped → `⋯ 僅載入前 10,000 筆`; bars card: 12 `.bar-col`, April's `.bar.down` with `--h: 36.3%` (or the equivalent style), label `4月`, amount `−4,897`, `.future` on 11/12 only, `.visually-hidden` text equals `barsAriaText`; filter card contains the search input, the market toggle (`aria-label="市場"`), the 僅當沖 switch (`role="switch"`, `aria-checked`), the datepicker, the broker toggle; 清除篩選 appears with only 僅當沖 on or only 台股 selected and resets both; phone → two cards only, header actions below.
- [ ] **Step 2–4:** run → FAIL; implement (replace the two-card `.realized-summary-row` and `.hub-filter-bar` markup; KPI card markup/scss copied from `transaction-list` as PR 4 did; bars as plain CSS grid; `.visually-hidden` utility already in `styles.scss`); run → PASS; `npm run build` budget check.
- [ ] **Step 5: Commit** `feat(realized): summary cards, monthly bars and filter card`

---

### Task 3: Realized rows — expandable sells

**Files:**
- Create: `realized-pnl/realized-rows.ts` (+ spec) — pure row model.
- Modify: `realized-pnl.{ts,html,scss}`, `realized-pnl-list.foreign-columns.spec.ts` (keep its intent: foreign sells show native figures), `realized-pnl.component.spec.ts`.

**Interfaces:**
```ts
export interface RealizedRow { key: string; square: StockSquare; name: string; symbol: string; marketNote: string | null; dayTrade: boolean; meta: string; amount: string; amountClass: 'up' | 'down' | ''; pct: string | null; detail: { label: string; main: string; sub: string | null }[]; warnings: string[] }
export function realizedRow(e: RealizedPnlEvent, name: string | null, brokerLabel: string): RealizedRow;
```
Detail rows in order: 成交總額, 手續費, 交易稅 / 交易稅（當沖）, 淨收入, 賣出成本, 已實現損益, then 匯率 only for foreign sells with a positive `native_proceeds_gross` (`proceeds_gross / native_proceeds_gross`, `formatFxRate`); foreign main = native (`formatNativeAmount`), sub = TWD; TW main = TWD, sub null.

- [ ] **Step 1: Failing tests** — `realized-rows.spec`: 鴻海 fixture → `meta '10/02 · 1,000 股 · 均價 160.73 → 賣出 175.00 · 元大'`, `amount '+13,498'`, `pct '+8.4%'` (13,498 / 160,728), detail `成交總額 175,000`, `手續費 249`, `交易稅 525`, `淨收入 174,226`, `賣出成本 160,728`, `已實現損益 +13,498`; 台積電 day trade → `交易稅（當沖）` label and `dayTrade true`; AAPL → `marketNote '美股'`, meta avg `175.20 USD`, price `212.30 USD`, detail mains in USD with TWD subs, 匯率 row; `cost_out 0` → `pct null`; `native_proceeds_gross` null → no 匯率 row; `-0.4` realized → `0`, class `''`. Component: rows are `button[aria-expanded]`, one expanded at a time, 當沖 `app-tag`, the 融券 / inventory warnings still rendered, keyboard Enter toggles, phone → `.compact` rows with the pill beneath the amount, list in 台股 mode paged client-side (3 rows when the server page had 1, paginator from the set, no API call on page change), empty state `無已實現損益`.
- [ ] **Step 2–4:** run → FAIL; implement (drop `app-list-item` for the row button; delete `displayAmount`/`displayPnl`/`displaySellPrice`/`displayAvgCost` once unused; keep `NativeAmountPipe` only if still referenced); run the four realized specs → PASS.
- [ ] **Step 5: Commit** `feat(realized): expandable sell rows with square, pill and detail grid`

---

### Task 4: Cash-transaction row model + running balance

**Files:**
- Create: `frontend/src/app/components/portfolio/accounts/cash-rows.ts` (+ `cash-rows.spec.ts`)

**Interfaces:**
```ts
export interface CashRow { id: number; date: string; icon: string; title: string; subtitle: string | null; source: '手動' | '交易' | '股利' | '匯入'; amount: string; amountClass: 'up' | 'down' | ''; hasLegs: boolean }
export function cashRow(t: CashTransaction): CashRow;                                     // Decision 10
export function runningBalances(rows: readonly CashTransaction[], current: string | number, q: CashTransactionQuery): (number | null)[] | null; // Decision 11
export function balanceColumnShown(q: CashTransactionQuery): boolean;
```

- [ ] **Step 1: Failing tests** — `cashRow`: deposit with note `從台新 Richart 轉入` → title 入金, subtitle the note, source 手動, amount `+20,000` `.up`; buy_settle auto_derive with `related_transaction_id` → `交割 · 買進`, subtitle null, source 交易, `−110,407` `.down`; merged `trade` with two `child_legs` → title `交割`, subtitle `已合併買賣 2 筆`, `hasLegs true`; dividend_cash auto_derive with `related_dividend_id` → 股利, source 股利; csv_import → 匯入; icon per type (a `Record<CashTransactionType, string>` of `pi-*` names). `runningBalances`: current 438,360, rows [+20,000, −110,407, +174,226] (newest first), `{ offset: 0, sort: 'txn_date:desc' }` → `[438360, 418360, 528767]`; `offset: 25` → null; `date_from` set → null; `type` set → null; `sort: 'txn_date:asc'` → null; `merge_related: true` → still computed with the row amounts; `balanceColumnShown` mirrors.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(accounts): cash row model and running balance math`

---

### Task 5: Accounts master-detail wrapper

**Files:**
- Create: `components/portfolio/accounts/accounts-page.{ts,html,scss,spec.ts}`
- Modify: `app.routes.ts:75-76` (both paths → `PortfolioAccountsPageComponent`), `accounts-list.{ts,html,scss}` (`master`, `selectedId` inputs, `loaded` output, button cards, header hidden in master mode), `account-detail.{ts,html,scss}` (`accountId`, `embedded` inputs), `accounts-list.component.spec.ts`, `accounts-list.header.spec.ts`, `account-detail.component.spec.ts` (existing tests stay green; add the new-input cases).

**Interfaces:**
- `PortfolioAccountsListComponent`: `master = input(false)`, `selectedId = input<number | null>(null)`, `loaded = output<BrokerAccount[]>()` (emits after each `fetchAccounts`).
- `PortfolioAccountDetailComponent`: `accountId = input<number | null>(null)` (an `effect`/`toObservable` reloads account, history and transactions when it changes; `route.paramMap` used only when null), `embedded = input(false)`.
- `PortfolioAccountsPageComponent`: `mode = layout.mode()`, `routeId = toSignal(route.paramMap → Number(id) || null)`, `selectedId = computed(() => routeId() ?? firstActiveId())`, `firstActiveId` set from `loaded` (first `is_active` account, else first); when `mode() === 'panes' && routeId() === null && firstActiveId()` → `router.navigate(['/portfolio/accounts', id], { replaceUrl: true })` once per list load.

- [ ] **Step 1: Failing tests** — `accounts-page.spec` (router harness with both routes, `LayoutModeService.set('panes')`): `/portfolio/accounts` renders the header (title 現金帳戶, subtitle, actions 記錄入出金 + 新增帳戶), the list in master mode and the detail for the first active account (fixture: inactive id 3 first, active id 7 second → detail shows 7, URL becomes `/portfolio/accounts/7`, one navigation); `/portfolio/accounts/9` → detail 9, card 9 `aria-pressed="true"` with `.selected`; clicking card 7 → URL `/7`, detail reloads once; a list reload with the same accounts → no extra navigation; `set('sheet')` + `/portfolio/accounts` → list only (today's header with 新增帳戶), `/portfolio/accounts/7` → detail only with its own header; `set('phone')` same as sheet; `set(null)` in `afterEach`. `accounts-list.component.spec`: master mode renders `button.acct-card` with `aria-pressed`, the broker · currency line, negative balance class, tags; the total card footnote `1 個帳戶待補入金紀錄，未計入` with one overdraft account; non-master mode unchanged (existing tests). `account-detail.component.spec`: `accountId` input 7 → loads 7 without a route param; changing it to 9 → reloads; `embedded` → no `app-page-header`.
- [ ] **Step 2–4:** run → FAIL; implement; run the four accounts specs + `app.routes`-dependent shell specs (`shell.spec.ts`, `navigation-state.service.spec.ts`) → PASS.
- [ ] **Step 5: Commit** `feat(accounts): master-detail page at panes width with account cards`

---

### Task 6: Cash-transaction rows + 餘額 column

**Files:**
- Modify: `account-detail.{ts,html,scss}`, `account-detail.component.spec.ts`.

**Interfaces (consumes Task 4):** `cashRow`, `runningBalances`, `balanceColumnShown`; component `readonly rows = computed(() => transactions().map(cashRow))`, `readonly balances = computed(() => runningBalances(transactions(), account()?.native_balance ?? 0, query()))`.

- [ ] **Step 1: Failing tests** — the list renders column heads 日期 / 項目 / 來源 / 金額 / 餘額 and one `.cash-row` per transaction with date `2026/10/16`, icon, title/subtitle, source `app-tag`, signed amount, balance `438,360` / `418,360` for the spec scenario; with `offset 25` or a type filter the 餘額 head and cells are absent; merged row shows 已合併買賣 n 筆 and the expand control; manual rows keep the delete button; header row keeps 合併同筆交易 (`app-switch` replacing `p-toggleswitch`, same persistence), filters and 新增交易; phone → `.compact` rows (date under the amount, 餘額 beneath when present); existing tests' intents kept (filters, sort, paging, delete, merge persistence, chart window).
- [ ] **Step 2–4:** run → FAIL; implement (replace the `p-tag`/`type-badge` markup; keep `typeLabel`/`sourceLabel` only if still used); run → PASS; budget check (`account-detail.scss` is 334 lines — trim the replaced rules).
- [ ] **Step 5: Commit** `feat(accounts): cash-transaction rows with type subtitles, source tags and running balance`

---

### Task 7: Verification and bookkeeping

- [ ] **Step 1:** full suite (twice) + `npm run build` (no new warning); `npx -y @fission-ai/openspec validate redesign-frontend-v2 --strict` valid.
- [ ] **Step 2 (controller):** Playwright screenshots on a preview build (stop it before any SPA publish) with stubbed `/realized-pnl`, `/accounts/`, `/accounts/{id}/cash-transactions`, `/accounts/{id}/balance-history`, `/symbol-map/names`, `/transactions/brokers`: realized at 390 / 820 / 1440 light / dark with a row expanded and 台股 + 僅當沖 on; accounts at 1440 (master-detail, first account auto-selected, 餘額 column) and 390 (list, then detail).
- [ ] **Step 3:** tick 5.1–5.2 in `tasks.md`; implementation notes in the two specs (Decision 13); commit `docs(openspec): realized/accounts implementation notes; tick tasks 5.x`.

**Order:** Task 1 → 2 → 3 (same files); Task 4 → 5 → 6 (Task 6 needs Task 4 and Task 5's `embedded` detail); Tasks 1–3 and 4–6 touch different folders but share `app.routes.ts` only in Task 5 — still run sequentially (one worktree).
