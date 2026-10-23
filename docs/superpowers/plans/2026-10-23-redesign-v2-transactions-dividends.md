# Redesign v2 — PR 4 (交易紀錄 + 股利) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/portfolio/transactions` (period summary cards, month-grouped rows with 買 / 賣 totals, 當沖 tag, fees beneath, phone rows) and `/portfolio/dividends` (four-figure summary in the CONTEXT.md vocabulary, status filter, table-like rows, inline 確認收款 form replacing the dialog), from data the services already return.

**Architecture:** Both pages stay single standalone components. Filter-wide figures (summary cards) come from client-side paging of the existing list endpoints — the list itself stays paged at 25/50/100 — using the `allDividendPages`-style helper generalised to `allPages(fetch, pageSize)` in a shared `components/portfolio/paging.ts`; pure aggregation helpers live in `transaction-math.ts` / `dividend-math.ts` next to each page. The shared `TimelineComponent` gains an optional month grouping with a heading line and richer row fields for the transactions page; the dividends page drops `app-timeline` for its own grid rows (the ≥ 760 px table layout and the expandable inline form do not fit the timeline shape).

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), PrimeNG (dialogs/menus kept), Vitest/jsdom, SCSS with existing `--app-*` tokens only.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D9 交易紀錄 / 股利) and `specs/frontend-stock-transactions/spec.md`, `specs/frontend-dividend-records/spec.md`; vocabulary in `CONTEXT.md` (已記錄股利, 預估含息損益, 實際收款). Visual reference: `design_handoff_v2/design_refs/PfTransactions.dc.html`, `PfTransactionsPhone.dc.html`, `PfDividends.dc.html`, `PfDividendsPhone.dc.html`.

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-transactions-dividends` from `origin/main` 4173312 (PR 3 merged). Dummy root `.env` present. Single spec: `cd frontend && npx ng test --watch=false --include <spec>`; full suite 114 files / 1650 tests at 4173312. The runner shares globals across spec files — restore anything a spec touches.

## Not in this PR

- 已實現損益 / 現金帳戶 pages (PR 5), 匯入 (PR 6), timeline category bar (PR 7).
- No backend, API, schema or migration change (design Non-goals). No new tokens. The create/edit dialogs and the row-action menu (編輯 / 刪除) of both pages stay as they are.

## Decisions this plan makes (code facts checked at 4173312)

1. **Filter-wide totals are computed client-side.** `GET /api/portfolio/transactions` and `/dividends` return only `{items, total}` (`services/stock-portfolio-service/app/schemas/portfolio.py:273-281`; `limit` capped at 100, `routers/portfolio.py:64`). The summary cards page through every row of the **active filter** (`symbol`, `date_from`, `date_to`, `side`, `broker` for transactions; `symbol`, dates, `source` for dividends) with `allPages`, independent of the list page; cap 50 pages (5 000 rows) with a `⋯` note when hit. The cards show `—` while loading and are omitted on error (list still renders).
2. **已實現損益 card** = `getRealizedPnl({ symbol, date_from, date_to, broker, limit: 1 }).summary.filter_scope_total` (`services/portfolio.service.ts:120`, `RealizedPnlSummary.filter_scope_total` `portfolio.model.ts:384`) — server-side, same filters minus `side`; trend colour by sign; the whole card links to `/portfolio/realized-pnl`. `手續費＋交易稅` share = `(Σ fee + tax) / (Σ buy gross + Σ sell gross)` as `佔成交額 x.xx%`.
3. **Amounts** reuse the page's `allInTotal` / `formatTransactionAmount` (`transaction-list.ts:334-362`): buys `−` total paid, sells `+` net received; foreign trades show the TWD amount (`allInTotal × fx_rate_to_twd`) with the native amount beneath; when `fx_rate_to_twd` is missing the native amount is the main line and the TWD line is `—`. Fees line: `手續費 {fee}` + ` · 交易稅 {tax}` when tax > 0, in the trade's currency; foreign rows append ` · 匯率 {fx}`.
4. **當沖** = `Transaction.is_day_trade === true` (`portfolio.model.ts:57`), a `Tag` with text 當沖; no client-side pairing.
5. **Month grouping** is a `TimelineComponent` option: `groupBy = input<'day'|'month'>('day')`, `groupHeading = input<(key: string, rows: TimelineRow[]) => string>()` → heading `2026 年 10 月` + right-aligned `買 110,407 · 賣 174,226` (omit a zero side); `TimelineRow` gains optional `sub` (fees line), `tags: string[]`, `amountNote` (總付出 / 淨收入), `nativeAmount`, `square: { text, token }`. The accounting timeline and the dividends page do not use these; existing timeline specs stay green.
6. **Dividend summary** (client-side over all rows of the active filter, Decision 1): 已確認收款 = Σ `amount` of `receipt_status === 'confirmed'` (count, footnote `n 筆 · 已寫入現金帳`); 有效待收 = Σ `amount` of `'pending'` (value `--app-primary`, footnote `n 筆 · 納入預估含息損益`) — the owner's option-1 rule (pending rows stand in for the server-only 有效待收; the dashboard's `total_pending_dividends_net` may differ); 舊資料帳面 = Σ of `legacy_unknown` or missing status (footnote `n 筆 · 收款未核實`, dashed swatch); 已記錄股利 = 已確認收款 + 舊資料帳面 (value `--app-dividend`, footnote `已確認 ＋ 舊資料`). `unresolved` rows count in none of the four and are listed with tag 待人工釐清.
7. **Status filter** is client-side over the loaded page? No — the API has no status filter and the list is paged, so the status `SegToggle` (全部 / 待收 / 已收款 / 舊資料) switches the list to the filter-wide row set already loaded for the summary (Decision 1), paged client-side 25 at a time; `全部` returns to server paging. Sort / search / date / source filters apply to both.
8. **Row columns** (≥ 760 px grid): 股票 (square + name + `symbol`), 除息日 (`ex_dividend_date` `YYYY/MM/DD`), 發放 / 收款 (`預定 {payment_date}` + `{payment_date_source}` beneath, or `收款 {receipt_date}` + receiving account `nickname` beneath, else `—`), 每股 × 股數 (`per_share × qty` with `手續費 {fee} · 補充保費 {tax}` beneath when > 0), 淨額 (`+amount`; `--app-dividend` for confirmed/legacy, `--app-primary` for pending; non-TWD via `formatNative`), 狀態 tag (待收 / 已收款 / 舊資料 · 未核實 / 待人工釐清). Account names come from `getAccounts()` (`BrokerAccount.nickname`, loaded once). No "period" (Q3 / 上半年) — the model has no such field; omitted.
9. **Inline receipt form** replaces the `p-dialog`: one open row at a time (`openReceiptId` signal); form title `確認實際收款 · {name} {amount}`; warning text verbatim from the spec; `實際收款日` `<input type="date">`; `收款帳戶` select of `getAccounts().items.filter(is_active && currency === 'TWD')` (as today, `dividend-list.ts:111-118`); buttons 取消 / 確認已收到並入帳 (disabled until both fields set or while busy); submit calls `confirmDividendReceipt(id, { receipt_date, account_id, revision })` exactly as today; on success the row's status/tag/amount colour update and the summary recomputes (reload both the page and the filter-wide set); on error the existing toast text.
10. **Phone (< 760 px)**: transactions rows collapse to square · name + `SideTag` · `qty 股 @ price · broker` · amount with the date beneath; only 買進 / 賣出 cards side by side. Dividends: only 已確認收款 / 有效待收 cards; rows collapse to square · name + tag · 淨額 · 除息/發放 line; the inline form keeps the same fields stacked.
11. **Headers**: transactions `title` default (股票交易紀錄) with `subtitle` `{period} · 共 {total} 筆` (period = the date filter range `YYYY/MM/DD – YYYY/MM/DD`, else `全部期間`), actions 匯入 CSV (link `/portfolio/import`, secondary) + 新增交易 (soft); dividends `subtitle` `{period} · 共 {total} 筆`, action 新增股利 (soft). 清除篩選 moves into the filter bar as a text button shown only while a filter is active.

## Global Constraints

- Copy (verbatim): transactions cards 買進金額 / 賣出金額 / 手續費＋交易稅 / 已實現損益, card footnotes `{n} 筆`, `佔成交額 {x}%`, `查看明細 →`; amount notes 總付出 / 淨收入; tag 當沖; month heading `YYYY 年 M 月` + `買 {x} · 賣 {y}`; fees `手續費 {x}` / `交易稅 {x}` / `匯率 {x}`; header actions 匯入 CSV, 新增交易; empty `無股票交易`. Dividends cards 已確認收款 / 有效待收 / 舊資料帳面 / 已記錄股利 with the footnotes in Decision 6; note text `有效待收股利會納入「預估含息損益」，但不計入可用現金；待釐清的金額不納入預估。舊資料的收款未經核實。`; column heads 股票 / 除息日 / 發放 / 收款 / 每股 × 股數 / 淨額 / 狀態; tags 待收 / 已收款 / 舊資料 · 未核實 / 待人工釐清; status toggle 全部 / 待收 / 已收款 / 舊資料; button 確認收款; form title `確認實際收款 · {name} {amount}`, warning `只在核對過實際入帳後確認。預定發放日不是收款證明；確認後會寫入現金帳，無法直接編輯撤銷。`, fields 實際收款日 / 收款帳戶, buttons 取消 / 確認已收到並入帳; header action 新增股利.
- `SideTag`: buy indigo 買, sell slate 賣 (existing `components/ui/side-tag`). Dividend amounts `--app-dividend` (received/legacy) / `--app-primary` (pending). 已實現損益 trend colour via the existing `.up/.down` convention classes. Cards `--app-surface` + `--app-card-shadow`. Colours from existing tokens only.
- Breakpoint `< 760px` phone (`LayoutModeService.mode() === 'phone'` where behaviour differs; CSS `max-width: 760px` as the project does).
- Numbers `tabular-nums`; TWD via `formatCurrency`, native via `formatNative` / `formatTransactionAmount`.
- 已記錄股利 MUST equal 已確認收款 + 舊資料帳面 and MUST NOT include 有效待收 (spec scenario: 37,578 + 21,342 = 58,920 with 14,600 pending).
- No API change; failures of the summary loads hide the cards silently (list errors keep today's toast).

## Review Focus

1. **Filter-wide paging vs the paged list** — changing a filter must reload both; a stale summary from a previous filter must never overwrite a newer one (generation guard like the dashboard's), and the 50-page cap shows the `⋯` note instead of wrong totals. → Task 1 and Task 4 tests.
2. **Foreign trades without an FX rate** — amount main/sub lines, fee line and the card sums must not produce NaN or mix currencies silently (foreign gross excluded from TWD sums with a footnote `不含外幣 n 筆` when any foreign row lacks `fx_rate_to_twd`). → Task 1/2 tests.
3. **Receipt confirm edge cases** — a second 確認收款 closes the first form; 409 (revision conflict) and other errors keep the row pending, show the existing toast and refresh; a row confirmed elsewhere disappears from the 待收 filter after refresh. → Task 6 tests.
4. **Status filter + pagination** — switching to 待收 shows all pending rows of the filter (not just the current page), paged 25 client-side; switching back restores server paging at offset 0. → Task 5 tests.
5. **Phone layouts** — only two cards, collapsed rows, form fields stacked, no horizontal scroll at 390 px. → Task 7 screenshots + structure tests in Tasks 3/5.

---

### Task 1: Shared paging helper + transaction math

**Files:**
- Create: `frontend/src/app/components/portfolio/paging.ts` (+ `paging.spec.ts`) — move `allDividendPages` from `dashboard/dashboard-math.ts` into a generic `allPages<T>(fetch: (offset: number) => Observable<Paged<T>>, pageSize = 100, maxPages = 50): Observable<{ items: T[]; capped: boolean }>`; keep `allDividendPages` as a thin wrapper so the dashboard and its specs are unchanged.
- Create: `frontend/src/app/components/portfolio/transaction-list/transaction-math.ts` (+ spec)

**Interfaces:**
```ts
export interface TradeTotals { buyTotal: number; buyCount: number; sellTotal: number; sellCount: number; fees: number; turnover: number; feeShare: number | null; foreignWithoutFx: number }
export function tradeTotals(rows: readonly Transaction[]): TradeTotals;            // TWD; foreign rows converted by fx_rate_to_twd, counted in foreignWithoutFx when missing
export function monthKey(date: string | Date): string;                             // 'YYYY-MM'
export function monthHeading(key: string, rows: readonly Transaction[]): string;  // '2026 年 10 月' is the label; returns the right-side text '買 110,407 · 賣 174,226' (zero side omitted)
export function feeLine(t: Transaction): string;                                   // '手續費 157' | '手續費 249 · 交易稅 525' | foreign: 'US$1.00' style via formatNative + ' · 匯率 30.70'
export function periodLabel(q: { date_from?: string | null; date_to?: string | null }): string; // '全部期間' | 'YYYY/MM/DD – YYYY/MM/DD'
```

- [ ] **Step 1: Failing tests** — `paging.spec`: total 250 → offsets 0/100/200, `capped: false`; empty page stops; cap 2 pages with total 500 → `capped: true`; error propagates. `transaction-math.spec`: fixture 2 buys (one TW, one US with fx 30.7) + 1 sell → buy/sell totals and counts, fees, `feeShare` = fees / turnover; a US buy without fx → excluded, `foreignWithoutFx 1`; no rows → zeros and `feeShare null`; `monthHeading` with only buys omits `賣`; `feeLine` cases incl. tax 0 and foreign; `periodLabel` both branches.
- [ ] **Step 2–4:** run → FAIL; implement; run both specs + `dashboard-math.spec` → PASS.
- [ ] **Step 5: Commit** `feat(portfolio): shared allPages helper and transaction summary math`

---

### Task 2: Transactions — summary cards, header, filter bar

**Files:**
- Modify: `components/portfolio/transaction-list/transaction-list.{ts,html,scss}`, `transaction-list.header.spec.ts` → rename to `transaction-list.spec.ts` and extend.

**Interfaces:**
- `readonly summary = signal<TradeTotals | null>(null)`, `readonly realized = signal<number | null>(null)`, `readonly summaryCapped = signal(false)`; `loadSummary()` runs on init and after every filter change (not on page change), with a generation guard; uses `allPages(offset => getTransactions({ ...filters, limit: 100, offset, sort }))` and `getRealizedPnl({ symbol, date_from, date_to, broker, limit: 1 })`, each `catchError → null`.

- [ ] **Step 1: Failing tests** (stub `PortfolioService` with `Subject`s): four cards with labels in order and values from `tradeTotals`; `手續費＋交易稅` footnote `佔成交額 0.27%`; 已實現損益 card is a link to `/portfolio/realized-pnl` with `.up`/`.down`; filter change (side → SELL) triggers one new summary load with the filter and a realized reload; page change does not; an older summary response landing after a newer is dropped; summary error → no cards, list intact; capped → `⋯` note; header subtitle `全部期間 · 共 24 筆` and `2026/01/01 – 2026/10/22 · 共 7 筆` after a date filter; actions 匯入 CSV (`href="/portfolio/import"`) and 新增交易 soft; 清除篩選 appears only with an active filter; phone mode → only 買進/賣出 cards.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(transactions): period summary cards and header`

---

### Task 3: Transactions — month-grouped rows

**Files:**
- Modify: `components/ui/timeline/timeline.{ts,scss,spec.ts}` (groupBy/month heading, new optional row fields; existing day behaviour unchanged), `components/ui/tag/*` if a `variant` is needed for 當沖, `transaction-list.{ts,html,scss,spec.ts}`.

- [ ] **Step 1: Failing tests** — `timeline.spec`: `groupBy="month"` renders one `.tl-month` heading per month with the label and the `groupHeading` text, rows keep day + weekday (`10/15 週四`); day mode unchanged (existing tests). `transaction-list.spec`: rows show square, `name symbol`, `SideTag` 買/賣, 當沖 tag only when `is_day_trade`, `5,000 股 @ 22.05`, fee line beneath, broker, amount `−110,407` with note 總付出 / `+174,226` 淨收入, foreign row TWD main + native beneath, missing fx → native main + `—`; phone mode → collapsed row structure (`.tl-compact`) with the date beneath the amount; row action menu still works (existing `onRowAction`).
- [ ] **Step 2–4:** run → FAIL; implement; run timeline + transaction specs + the accounting timeline consumer specs that render `app-timeline` → PASS.
- [ ] **Step 5: Commit** `feat(transactions): month-grouped rows with 當沖, fees and amount notes`

---

### Task 4: Dividend math + filter-wide loading

**Files:**
- Create: `components/portfolio/dividend-list/dividend-math.ts` (+ spec)
- Modify: `dividend-list.ts` (summary signals + `loadAll()` with generation guard; reload on filter change and after a receipt confirm)

**Interfaces:**
```ts
export type DividendStatus = 'pending' | 'confirmed' | 'legacy' | 'unresolved';
export function dividendStatus(d: Dividend): DividendStatus;   // missing/legacy_unknown → 'legacy'
export interface DividendTotals { confirmed: number; confirmedCount: number; pending: number; pendingCount: number; legacy: number; legacyCount: number; recorded: number }
export function dividendTotals(rows: readonly Dividend[]): DividendTotals; // recorded = confirmed + legacy
export function statusTag(s: DividendStatus): string;  // 待收 | 已收款 | 舊資料 · 未核實 | 待人工釐清
export function payLine(d: Dividend, accountName: (id: number) => string | null): { main: string; sub: string | null }; // '預定 11/18' + source | '收款 08/14' + account | '—'
```

- [ ] **Step 1: Failing tests** — spec scenario: confirmed 37,578 + legacy 21,342 + pending 14,600 → `recorded 58,920`, pending excluded; `unresolved` counted nowhere; `payLine` three branches with `MM/DD` from the string parts; status mapping incl. missing status; component: filter change reloads the filter-wide set once (generation guard), error → cards hidden.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): CONTEXT.md totals and filter-wide loading`

---

### Task 5: Dividends — summary cards, status filter, grid rows

**Files:**
- Modify: `dividend-list.{ts,html,scss,spec.ts}` (drop `app-timeline`; own `.div-row` grid; `SegToggle` status filter; header subtitle + 新增股利; remove the `upcoming-card` bento? — **keep** 即將除權息 as a compact strip above the list (spec does not remove it; design shows 即將除息 on phone).

- [ ] **Step 1: Failing tests** — four cards in order with values/footnotes from `dividendTotals`, 有效待收 value class `primary`, 舊資料帳面 swatch `.dashed`, 已記錄股利 value class `dividend`; note text verbatim; status toggle options; selecting 待收 lists only pending rows from the filter-wide set (3 rows when the server page had 1), client paging 25; 全部 → server paging offset 0; rows: square, `name code`, 除息日 `2026/10/17`, 發放/收款 lines, `0.48 × 20,000` + deductions beneath, `+9,600` with `.primary` for pending / `.dividend` otherwise, tag text; USD row via `formatNative`; phone mode → two cards, collapsed rows; existing tests in `dividend-list.spec.ts` updated to the new markup (keep their intent: precision, same-day-different-year dates, loading/empty/error feedback, query/sort/pagination preserved).
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): four-figure summary, status filter and grid rows`

---

### Task 6: Inline 確認收款 form

**Files:**
- Modify: `dividend-list.{ts,html,scss,spec.ts}` (remove the receipt `p-dialog`; `openReceiptId` signal; form row)

- [ ] **Step 1: Failing tests** — pending row shows 確認收款; press → form row under it with the title `確認實際收款 · 國泰永續高股息 +9,600`, the warning verbatim, date input, account select (active TWD accounts), 取消 / 確認已收到並入帳 (disabled until both set); pressing 確認收款 on another row closes the first; submit calls `confirmDividendReceipt(id, { receipt_date: '2026-11-18', account_id: 7, revision })`; success → row tag 已收款, 有效待收 drops by 9,600 (both the page and the filter-wide set reload); error → toast `收款確認未完成` + reload, row stays pending; duplicate submit guarded (`receiptBusy`); legacy/confirmed rows have no button. Keep the existing receipt tests' intents (`requires explicit date and account, sends revision, guards duplicate submits`, `does not offer confirmation for legacy or confirmed rows`).
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): inline receipt confirmation replaces the dialog`

---

### Task 7: Verification and bookkeeping

- [ ] **Step 1:** full suite (twice) + `npm run build` (no budget warning); `npx -y @fission-ai/openspec validate redesign-frontend-v2 --strict` valid.
- [ ] **Step 2 (controller):** Playwright screenshots on a preview build with stubbed `/api/portfolio/transactions`, `/dividends`, `/realized-pnl`, `/symbol-map/names`, `/transactions/brokers`, `/accounts`, `/ex-dividends/upcoming` fixtures: both pages at 390 / 820 / 1440, light / dark; the inline form open; status filter 待收; a month with buys only.
- [ ] **Step 3:** tick tasks 4.1–4.4 in `openspec/changes/redesign-frontend-v2/tasks.md`; add implementation notes to the two specs only where the implementation refines them (client-side totals with the 50-page cap, 有效待收 = pending rows per the owner's decision, no period label, 即將除權息 strip kept); commit `docs(openspec): transactions/dividends implementation notes; tick tasks 4.x`.
