# Redesign v2 — PR 4 (交易紀錄 + 股利) Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/portfolio/transactions` (period summary cards, month-grouped rows with 買 / 賣 totals, 當沖 tag, fees beneath, phone rows) and `/portfolio/dividends` (four-figure summary in the CONTEXT.md vocabulary, status filter, table-like rows, inline 確認收款 form replacing the dialog), from data the services already return.

**Architecture:** Both pages stay single standalone components. Filter-wide figures (summary cards) come from client-side paging of the existing list endpoints — the list itself stays paged at 25/50/100 — using the `allDividendPages`-style helper generalised to `allPages(fetch, pageSize)` in a shared `components/portfolio/paging.ts`; pure aggregation helpers live in `transaction-math.ts` / `dividend-math.ts` next to each page. The shared `TimelineComponent` gains an optional month grouping with a heading line and richer row fields for the transactions page; the dividends page drops `app-timeline` for its own grid rows (the ≥ 760 px table layout and the expandable inline form do not fit the timeline shape).

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), PrimeNG (dialogs/menus kept), Vitest/jsdom, SCSS with existing `--app-*` tokens only.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D9 交易紀錄 / 股利) and `specs/frontend-stock-transactions/spec.md`, `specs/frontend-dividend-records/spec.md`; vocabulary in `CONTEXT.md` (已記錄股利, 預估含息損益, 實際收款). Visual reference: `design_handoff_v2/design_refs/PfTransactions.dc.html`, `PfTransactionsPhone.dc.html`, `PfDividends.dc.html`, `PfDividendsPhone.dc.html`.

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-transactions-dividends` from `origin/main` 4173312 (PR 3 merged). Dummy root `.env` present. Single spec: `cd frontend && npx ng test --watch=false --include <spec>`; full suite 114 files / 1650 tests at 4173312. The runner shares globals across spec files — restore anything a spec touches.

**Rev 2 (AGENT-213, dev-grok, CHANGES REQUIRED at b6c401c):** Musts fixed — foreign dividends are converted to TWD with `fx_rate_to_twd` (model field added, type only) for cards and 淨額, rows without fx excluded with the `不含外幣 n 筆` footnote (T4/T5); the receipt form closes on success **and** error, keeps the toast detail fallback, sends `revision ?? 1`, reloads both sets, and the 待收 filter drops a confirmed row (T6). Shoulds folded — TWD all-in definitions and US×fx fixture, `feeLine` prefixes, `monthHeading` vs `groupHeading` types, `allDividendPages` wrapper shape, calendar `monthKey` (T1); `hasActiveFilters` includes broker, realized card `—` on its own failure, decimal string (T2); broker field, currency-prefixed price, native note, square via `holdingAbbr`/series/`abbrInk`, day column on every month-mode row (T3); dividend cap note, page change doesn't reload, keep server page on load error (T4); status-mode total/paginator, cap note on the list, `YYYY/MM/DD` for a different-year payment date, per-share 2–4 digits, keep `review_reason` / source-correction (T5); keep the accounts toast, spec warning copy (T6); phone via `LayoutModeService.set('phone')` + `set(null)`, `max-width: 759.98px` (T7).

## Not in this PR

- 已實現損益 / 現金帳戶 pages (PR 5), 匯入 (PR 6), timeline category bar (PR 7).
- No backend, API, schema or migration change (design Non-goals). No new tokens. The create/edit dialogs and the row-action menu (編輯 / 刪除) of both pages stay as they are.

## Decisions this plan makes (code facts checked at 4173312)

1. **Filter-wide totals are computed client-side.** `GET /api/portfolio/transactions` and `/dividends` return only `{items, total}` (`services/stock-portfolio-service/app/schemas/portfolio.py:273-281`; `limit` capped at 100, `routers/portfolio.py:64`). The summary cards page through every row of the **active filter** (`symbol`, `date_from`, `date_to`, `side`, `broker` for transactions; `symbol`, dates, `source` for dividends) with `allPages`, independent of the list page; cap 50 pages (5 000 rows) with a `⋯` note when hit. The cards show `—` while loading and are omitted on error (list still renders).
2. **已實現損益 card** = `getRealizedPnl({ symbol, date_from, date_to, broker, limit: 1 }).summary.filter_scope_total` (`services/portfolio.service.ts:120`, `RealizedPnlSummary.filter_scope_total` `portfolio.model.ts:384`) — server-side, same filters minus `side`; trend colour by sign; the whole card links to `/portfolio/realized-pnl`. `手續費＋交易稅` share = `(Σ fee + tax) / (Σ buy gross + Σ sell gross)` as `佔成交額 x.xx%`.
3. **Amounts** reuse the page's `allInTotal` / `formatTransactionAmount` (`transaction-list.ts:334-362`): buys `−` total paid, sells `+` net received; foreign trades show the TWD amount (`allInTotal × fx_rate_to_twd`) with the native amount beneath; when `fx_rate_to_twd` is missing the native amount is the main line and the TWD line is `—`. Fees line keeps the prefixes on every row: `手續費 {fee}` + ` · 交易稅 {tax}` when tax > 0, amounts in the trade's currency (`手續費 US$1.00`), foreign rows append ` · 匯率 {fx}`. Card figures are **TWD all-in**: `buyTotal` / `sellTotal` = Σ `allInTotal × fx_rate_to_twd` (fx = 1 for TWD), `turnover` = Σ converted `price × quantity`, `fees` = Σ converted `fee + tax`, `feeShare = fees / turnover` (null when turnover is 0); rows with a missing or non-finite fx are excluded from all three and counted in `foreignWithoutFx` (footnote `不含外幣 n 筆`). Month headings use the same all-in TWD totals and skip those rows.
4. **當沖** = `Transaction.is_day_trade === true` (`portfolio.model.ts:57`), a `Tag` with text 當沖; no client-side pairing.
5. **Month grouping** is a `TimelineComponent` option: `groupBy = input<'day'|'month'>('day')`; in month mode the timeline renders one `.tl-month` heading per `YYYY-MM` key with `monthLabel(key)` (`2026 年 10 月`, built in the timeline from the key) and a right-aligned text from `groupHeading = input<(key: string) => string>()` — the page's callback closes over its own `Transaction[]` and returns `monthHeading(key, rows)` (the timeline never sums formatted strings); every row keeps its day column (`10/15 週四`) in month mode (today the day cell is only on the group's first row). `TimelineRow` gains optional `sub` (fees line), `tags: string[]`, `broker`, `amountNote` (總付出 / 淨收入 on TWD rows; the native amount string on foreign rows), `square: { text, token, ink: 'light'|'dark' }`. Day mode markup is unchanged (new DOM gated on the new fields / month mode), so `timeline.spec.ts` stays green; `app-timeline` is used only by the two portfolio pages (the accounting ledger is its own component).
6. **Dividend summary** (client-side over all rows of the active filter, Decision 1), **all in TWD**: a dividend's `amount` is in its own currency with `fx_rate_to_twd` beside it (`services/stock-portfolio-service/app/services/foreign_dividend_service.py:133-149`, `schemas/portfolio.py:131-132`); add `fx_rate_to_twd?: number | string | null` and `market?: MarketCode` to the frontend `Dividend` interface (type only, no API change) and convert like transactions (`amount × fx`, fx = 1 for TWD; missing/non-finite fx → excluded from every card, counted in a `不含外幣 n 筆` footnote). 已確認收款 = Σ converted `amount` of `receipt_status === 'confirmed'` (count, footnote `n 筆 · 已寫入現金帳`); 有效待收 = Σ `amount` of `'pending'` (value `--app-primary`, footnote `n 筆 · 納入預估含息損益`) — the owner's option-1 rule (pending rows stand in for the server-only 有效待收; the dashboard's `total_pending_dividends_net` may differ); 舊資料帳面 = Σ of `legacy_unknown` or missing status (footnote `n 筆 · 收款未核實`, dashed swatch); 已記錄股利 = 已確認收款 + 舊資料帳面 (value `--app-dividend`, footnote `已確認 ＋ 舊資料`). `unresolved` rows count in none of the four and are listed with tag 待人工釐清.
7. **Status filter**: the API has no status filter and the list is paged, so the status `SegToggle` (全部 / 待收 / 已收款 / 舊資料) switches the list to the filter-wide row set already loaded for the summary (Decision 1), paged client-side honouring the current `limit` (25/50/100); the subtitle `共 {n} 筆` and the paginator use that set's length in status mode and the server `total` on 全部; `全部` returns to server paging at offset 0. When the 50-page cap tripped, the status list shows the same `⋯ 僅載入前 5,000 筆` note. If the filter-wide load fails while a status is selected, keep the server page and show the list error — never an empty "no pending rows". 舊資料 = legacy or missing status; `unresolved` rows appear only under 全部. Sort / search / date / source filters apply to both; a page change never reloads the filter-wide set.
8. **Row columns** (≥ 760 px grid): 股票 (square + name + `symbol`), 除息日 (`ex_dividend_date` `YYYY/MM/DD`), 發放 / 收款 (`預定 {payment_date}` + `{payment_date_source}` beneath, or `收款 {receipt_date}` + receiving account `nickname` beneath, else `—`), 每股 × 股數 (`per_share × qty` with `手續費 {fee} · 補充保費 {tax}` beneath when > 0), 淨額 (`+{TWD amount}` main line; `--app-dividend` for confirmed/legacy, `--app-primary` for pending; non-TWD rows show the TWD figure as the main line with the native amount beneath (`US$7.80 · 匯率 30.80`), or the native amount as the main line and `—` beneath when fx is missing — canvas row AAPL `+168` with `US$0.26 × 30`; formatting via a shared `formatNativeAmount` helper in `components/portfolio/money.ts` (moved from the dashboard's `formatNative` method, which stays as a thin delegate)), 狀態 tag (待收 / 已收款 / 舊資料 · 未核實 / 待人工釐清). Account names come from `getAccounts()` (`BrokerAccount.nickname`, loaded once). No "period" (Q3 / 上半年) — the model has no such field; omitted. Keep the existing `review_reason` line and the source-correction sentence (`來源資料已變更；已確認金額及現金帳不變，需人工釐清。`) under the row (`dividend-list.html:84-85`); the fourth tag 待人工釐清 is an implementation note for the spec. 發放 / 收款 dates render `MM/DD` when the year equals the ex-date's year, else `YYYY/MM/DD` (canvas `預定 2027/01/08`). Per-share keeps the current 2–4 fraction digits (`0.042`, `0.0421`, `0.0001`).
9. **Inline receipt form** replaces the `p-dialog`: one open row at a time (`openReceiptId` signal); form title `確認實際收款 · {name} {amount}`; warning text verbatim from the spec; `實際收款日` `<input type="date">`; `收款帳戶` select of `getAccounts().items.filter(is_active && currency === 'TWD')` (as today, `dividend-list.ts:111-118`); buttons 取消 / 確認已收到並入帳 (disabled until both fields set or while busy); submit calls `confirmDividendReceipt(id, { receipt_date, account_id, revision })` exactly as today; payload `revision: row.revision ?? 1`; on success **and** on error the form closes (`openReceiptId` → null), `receiptBusy` resets, and both the page and the filter-wide set reload; on error the toast is summary `收款確認未完成` with detail `err.error?.detail ?? '請重新載入後核對'` (the server puts the reason there: non-TW, bad date, stale revision, account). The accounts-load failure keeps its toast `無法取得收款帳戶`. With 待收 selected, a row that comes back confirmed (local success, or a refresh after a 409) leaves the list and 有效待收 drops by its amount. The warning sentence is the spec's, not the current dialog's.
10. **Phone (< 760 px)**: transactions rows collapse to square · name + `SideTag` · `qty 股 @ price · broker` · amount with the date beneath; only 買進 / 賣出 cards side by side. Dividends: only 已確認收款 / 有效待收 cards; rows collapse to square · name + tag · 淨額 · 除息/發放 line; the inline form keeps the same fields stacked.
11. **Headers**: transactions `title` default (股票交易紀錄) with `subtitle` `{period} · 共 {total} 筆` (server `total`; on dividends the status-mode count per Decision 7) (period = the date filter range `YYYY/MM/DD – YYYY/MM/DD`, else `全部期間`), actions 匯入 CSV (link `/portfolio/import`, secondary) + 新增交易 (soft); dividends `subtitle` `{period} · 共 {total} 筆`, action 新增股利 (soft). 清除篩選 moves into the filter bar as a text button shown only while a filter is active (`hasActiveFilters` must include `broker` — today it ignores it, `transaction-list.ts:246-248`).

## Global Constraints

- Copy (verbatim): transactions cards 買進金額 / 賣出金額 / 手續費＋交易稅 / 已實現損益, card footnotes `{n} 筆`, `佔成交額 {x}%`, `查看明細 →`; amount notes 總付出 / 淨收入; tag 當沖; month heading `YYYY 年 M 月` + `買 {x} · 賣 {y}`; fees `手續費 {x}` / `交易稅 {x}` / `匯率 {x}`; header actions 匯入 CSV, 新增交易; empty `無股票交易`. Dividends cards 已確認收款 / 有效待收 / 舊資料帳面 / 已記錄股利 with the footnotes in Decision 6; note text `有效待收股利會納入「預估含息損益」，但不計入可用現金；待釐清的金額不納入預估。舊資料的收款未經核實。`; column heads 股票 / 除息日 / 發放 / 收款 / 每股 × 股數 / 淨額 / 狀態; tags 待收 / 已收款 / 舊資料 · 未核實 / 待人工釐清; status toggle 全部 / 待收 / 已收款 / 舊資料; button 確認收款; form title `確認實際收款 · {name} {amount}`, warning `只在核對過實際入帳後確認。預定發放日不是收款證明；確認後會寫入現金帳，無法直接編輯撤銷。`, fields 實際收款日 / 收款帳戶, buttons 取消 / 確認已收到並入帳; header action 新增股利.
- `SideTag`: buy indigo 買, sell slate 賣 (existing `components/ui/side-tag`). Dividend amounts `--app-dividend` (received/legacy) / `--app-primary` (pending). 已實現損益 trend colour via the existing `.up/.down` convention classes. Cards `--app-surface` + `--app-card-shadow`. Colours from existing tokens only.
- Breakpoint: phone = `LayoutModeService.mode() === 'phone'` where behaviour differs; CSS `@media (max-width: 759.98px)` (the service's `PHONE_QUERY`; 760 itself is `sheet`). Tests force phone with `TestBed.inject(LayoutModeService).set('phone')` and reset `set(null)` in `afterEach` (the service is root-provided and specs share globals).
- Numbers `tabular-nums`; TWD via `formatCurrency`; native amounts via the shared `formatNativeAmount` (`components/portfolio/money.ts`, GBp 4 dp with `GBp`, else 2 dp + currency) — `formatTransactionAmount` keeps its behaviour and delegates. Existing empty/loading/error strings stay (`無股票交易`, `無股利紀錄`, `查詢中…`, `查詢失敗，請檢查篩選條件`).
- 已記錄股利 MUST equal 已確認收款 + 舊資料帳面 and MUST NOT include 有效待收 (spec scenario: 37,578 + 21,342 = 58,920 with 14,600 pending).
- No API change; failures of the summary loads hide the cards silently (list errors keep today's toast).

**Order:** Task 1 first (Tasks 2–4 import `paging.ts` / `money.ts`); Task 2 before Task 3 (same spec file); Task 4 → 5 → 6 sequential (same files). Never run two tasks in parallel.

## Review Focus

1. **Filter-wide paging vs the paged list** — changing a filter must reload both; a stale summary from a previous filter must never overwrite a newer one (generation guard like the dashboard's), and the 50-page cap shows the `⋯` note instead of wrong totals. → Task 1 and Task 4 tests.
2. **Foreign trades without an FX rate** — amount main/sub lines, fee line and the card sums must not produce NaN or mix currencies silently (foreign gross excluded from TWD sums with a footnote `不含外幣 n 筆` when any foreign row lacks `fx_rate_to_twd`). → Task 1/2 tests.
3. **Receipt confirm edge cases** — a second 確認收款 closes the first form; 409 (revision conflict) and other errors keep the row pending, show the existing toast and refresh; a row confirmed elsewhere disappears from the 待收 filter after refresh. → Task 6 tests.
4. **Status filter + pagination** — switching to 待收 shows all pending rows of the filter (not just the current page), paged 25 client-side; switching back restores server paging at offset 0. → Task 5 tests.
5. **Phone layouts** — only two cards, collapsed rows, form fields stacked, no horizontal scroll at 390 px. → Task 7 screenshots + structure tests in Tasks 3/5.

---

### Task 1: Shared paging helper + transaction math

**Files:**
- Create: `frontend/src/app/components/portfolio/paging.ts` (+ `paging.spec.ts`) — generic `allPages<T>(fetch: (offset: number) => Observable<Paged<T>>, pageSize = 100, maxPages = 50): Observable<{ items: T[]; capped: boolean }>`.
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard-math.ts` — `allDividendPages` becomes a thin wrapper over `allPages` that still returns `Observable<Dividend[]>` (`dashboard.ts:573-580` and `dashboard-math.spec.ts` unchanged).
- Create: `frontend/src/app/components/portfolio/money.ts` (+ spec) — `formatNativeAmount(value, currency)` moved from `dashboard.ts` `formatNative` (which delegates) and `twdAmount(value, fx): number | null` (null when fx missing/non-finite; 1 for TWD).
- Create: `frontend/src/app/components/portfolio/transaction-list/transaction-math.ts` (+ spec)

**Interfaces:**
```ts
export interface TradeTotals { buyTotal: number; buyCount: number; sellTotal: number; sellCount: number; fees: number; turnover: number; feeShare: number | null; foreignWithoutFx: number }
export function tradeTotals(rows: readonly Transaction[]): TradeTotals;            // TWD; foreign rows converted by fx_rate_to_twd, counted in foreignWithoutFx when missing
export function monthKey(date: string | Date): string;                             // 'YYYY-MM'
export function monthLabel(key: string): string;                                  // '2026 年 10 月'
export function monthHeading(key: string, rows: readonly Transaction[]): string;  // right-side text '買 110,407 · 賣 174,226' (TWD all-in, zero side omitted, no-fx rows skipped)
export function feeLine(t: Transaction): string;                                   // '手續費 157' | '手續費 249 · 交易稅 525' | foreign: '手續費 US$1.00 · 匯率 30.70' (prefixes kept, native formatting)
export function periodLabel(q: { date_from?: string | null; date_to?: string | null }): string; // '全部期間' | 'YYYY/MM/DD – YYYY/MM/DD'
```

- [ ] **Step 1: Failing tests** — `paging.spec`: total 250 → offsets 0/100/200, `capped: false`; empty page stops; cap 2 pages with total 500 → `capped: true`; error propagates. `transaction-math.spec`: fixture TW buy 5,000 × 22.05 fee 157 (all-in 110,407), US buy 10 × 238.40 fee 1.00 fx 30.70 (all-in 2,385 USD → 73,219.5 TWD), TW sell 1,000 × 175 fee 249 tax 525 (net 174,226) → `buyTotal 183,626.5`, `buyCount 2`, `sellTotal 174,226`, `fees` = (157 + 30.7 + 774) and `turnover` = (110,250 + 73,188.8 + 175,000), `feeShare = fees / turnover`; a US buy without fx (or fx `'abc'`) → excluded from all three, `foreignWithoutFx 1`; `monthKey('2026-10-15')` → `'2026-10'` from the string parts (no `Date`/`toISOString`); no rows → zeros and `feeShare null`; `monthHeading` with only buys omits `賣`; `feeLine` cases incl. tax 0 and foreign; `periodLabel` both branches.
- [ ] **Step 2–4:** run → FAIL; implement; run both specs + `dashboard-math.spec` → PASS.
- [ ] **Step 5: Commit** `feat(portfolio): shared allPages helper and transaction summary math`

---

### Task 2: Transactions — summary cards, header, filter bar

**Files:**
- Modify: `components/portfolio/transaction-list/transaction-list.{ts,html,scss}`, `transaction-list.header.spec.ts` → rename to `transaction-list.spec.ts` and extend (keep its "no 記一筆 button" assertion).

**Interfaces:**
- `readonly summary = signal<TradeTotals | null>(null)`, `readonly realized = signal<number | null>(null)`, `readonly summaryCapped = signal(false)`; `loadSummary()` runs on init and after every filter change (not on page change), with a generation guard; uses `allPages(offset => getTransactions({ ...filters, limit: 100, offset, sort }))` and `getRealizedPnl({ symbol, date_from, date_to, broker, limit: 1 })`, each `catchError → null`.

- [ ] **Step 1: Failing tests** (stub `PortfolioService` with `Subject`s): four cards with labels in order and values from `tradeTotals`; `手續費＋交易稅` footnote `佔成交額 0.27%`; 已實現損益 card is a link to `/portfolio/realized-pnl` with `.up`/`.down`; filter change (side → SELL) triggers one new summary load with the filter and a realized reload; page change does not; an older summary response landing after a newer is dropped; summary (`allPages`) error → no cards, list intact; `getRealizedPnl` error alone (e.g. 422 when a row lacks fx) → three trade cards stay, 已實現損益 shows `—`; `filter_scope_total` is a decimal string → parsed to a number; capped → `⋯ 僅載入前 5,000 筆` note; 清除篩選 appears with a broker-only filter too; header subtitle `全部期間 · 共 24 筆` and `2026/01/01 – 2026/10/22 · 共 7 筆` after a date filter; actions 匯入 CSV (`href="/portfolio/import"`) and 新增交易 soft; 清除篩選 appears only with an active filter; phone mode → only 買進/賣出 cards.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(transactions): period summary cards and header`

---

### Task 3: Transactions — month-grouped rows

**Files:**
- Modify: `components/ui/timeline/timeline.{ts,scss,spec.ts}` (groupBy/month heading, new optional row fields; existing day behaviour unchanged), `components/ui/tag/*` if a `variant` is needed for 當沖, `transaction-list.{ts,html,scss,spec.ts}`.

- [ ] **Step 1: Failing tests** — `timeline.spec`: `groupBy="month"` renders one `.tl-month` heading per month with `monthLabel` and the `groupHeading(key)` text, **every** row shows its day + weekday (`10/15 週四`), rows carry `square` (text/token/ink), `broker`, `tags`, `sub`, `amountNote`; day mode markup unchanged (existing tests, snapshot of a day-mode row's class list). `transaction-list.spec`: rows show the square (`holdingAbbr` text, `ALLOCATION_SERIES` token stable per symbol, `abbrInk`), `name symbol`, `SideTag` 買/賣, 當沖 tag only when `is_day_trade`, `5,000 股 @ 22.05` (foreign: `10 股 @ US$238.40`), fee line beneath, broker label, amount `−110,407` with note 總付出 / `+174,226` 淨收入, foreign row TWD main (`−73,220`) with the native amount as the note, missing fx → native main + `—`; phone mode → collapsed row structure (`.tl-compact`) with the date beneath the amount; row action menu still works (existing `onRowAction`).
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
export function dividendTotals(rows: readonly Dividend[]): DividendTotals & { foreignWithoutFx: number }; // TWD via twdAmount(amount, fx); rows without fx excluded + counted; recorded = confirmed + legacy
export function statusTag(s: DividendStatus): string;  // 待收 | 已收款 | 舊資料 · 未核實 | 待人工釐清
export function payLine(d: Dividend, accountName: (id: number) => string | null): { main: string; sub: string | null }; // '預定 11/18' + source | '收款 08/14' + account | '—'; 'YYYY/MM/DD' when the year differs from the ex-date year
export function netAmountLines(d: Dividend): { main: string; sub: string | null; twd: number | null }; // TWD main + native sub, or native main + '—'
```

- [ ] **Step 1: Failing tests** — spec scenario: confirmed 37,578 + legacy 21,342 + pending 14,600 → `recorded 58,920`, pending excluded; numeric currency fixture: TWD confirmed 9,600 + USD confirmed 7.80 fx 30.80 (→ 240.24) + USD pending 5.00 without fx → `confirmed 9,840.24`, `pending 0`, `foreignWithoutFx 1`; `unresolved` counted nowhere; `payLine` three branches, `MM/DD` same year and `2027/01/08` different year, from the string parts; `netAmountLines` TWD / USD-with-fx / USD-without-fx; status mapping incl. missing status; component: filter change reloads the filter-wide set once (`fetchSeq`-style guard: an older response landing later is dropped), page change does not, error → cards hidden (server page kept), capped → `⋯` note.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): CONTEXT.md totals and filter-wide loading`

---

### Task 5: Dividends — summary cards, status filter, grid rows

**Files:**
- Modify: `dividend-list.{ts,html,scss,spec.ts}` (drop `app-timeline`; own `.div-row` grid; `SegToggle` status filter; header subtitle + 新增股利; remove the `upcoming-card` bento? — **keep** 即將除權息 as a compact strip above the list (spec does not remove it; design shows 即將除息 on phone).

- [ ] **Step 1: Failing tests** — four cards in order with values/footnotes from `dividendTotals`, 有效待收 value class `primary`, 舊資料帳面 swatch `.dashed`, 已記錄股利 value class `dividend`; note text verbatim; status toggle options; selecting 待收 lists only pending rows from the filter-wide set (3 rows when the server page had 1), client paging honouring `limit`, subtitle `共 3 筆` and paginator from that set, capped → `⋯` note on the list, load error → server page + list error; 全部 → server paging offset 0 and server `total`; rows: square, `name code`, 除息日 `2026/10/17`, 發放/收款 lines, `0.48 × 20,000` (per-share 2–4 fraction digits: `0.042`, `0.0421`, `0.0001`) + deductions beneath, `+9,600` with `.primary` for pending / `.dividend` otherwise, tag text incl. 待人工釐清, `review_reason` and the source-correction sentence still rendered; USD row `+240` main with `US$7.80 · 匯率 30.80` beneath, no-fx USD row native main + `—`; phone mode → two cards, collapsed rows; existing tests in `dividend-list.spec.ts` updated to the new markup (keep their intent: per-share precision, same-day-different-year ex dates (extend to payment dates), loading/empty/error feedback with the existing strings, query/sort/pagination preserved, `review_reason` + source-correction lines).
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): four-figure summary, status filter and grid rows`

---

### Task 6: Inline 確認收款 form

**Files:**
- Modify: `dividend-list.{ts,html,scss,spec.ts}` (remove the receipt `p-dialog`; `openReceiptId` signal; form row)

- [ ] **Step 1: Failing tests** — pending row shows 確認收款; press → form row under it with the title `確認實際收款 · 國泰永續高股息 +9,600`, the warning verbatim, date input, account select (active TWD accounts), 取消 / 確認已收到並入帳 (disabled until both set); pressing 確認收款 on another row closes the first; submit calls `confirmDividendReceipt(id, { receipt_date: '2026-11-18', account_id: 7, revision: row.revision ?? 1 })` (a row without `revision` sends 1); success → form closes, row tag 已收款, 有效待收 drops by 9,600 (both the page and the filter-wide set reload); error → form closes, toast summary `收款確認未完成` with detail from `err.error.detail` (fallback `請重新載入後核對`), both sets reload, row stays pending; with 待收 selected a row that comes back confirmed after the reload leaves the list; accounts load error → toast `無法取得收款帳戶`; duplicate submit guarded (`receiptBusy`); legacy/confirmed rows have no button. Keep the existing receipt tests' intents (`requires explicit date and account, sends revision, guards duplicate submits`, `does not offer confirmation for legacy or confirmed rows`).
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(dividends): inline receipt confirmation replaces the dialog`

---

### Task 7: Verification and bookkeeping

- [ ] **Step 1:** full suite (twice) + `npm run build` (no budget warning); `npx -y @fission-ai/openspec validate redesign-frontend-v2 --strict` valid.
- [ ] **Step 2 (controller):** Playwright screenshots on a preview build (stop it before any SPA publish — the publisher probe uses 18081) with stubbed `/api/portfolio/transactions`, `/dividends`, `/realized-pnl`, `/symbol-map/names`, `/transactions/brokers`, `/accounts`, `/ex-dividends/upcoming` fixtures: both pages at 390 / 820 / 1440, light / dark; the inline form open; status filter 待收; a month with buys only.
- [ ] **Step 3:** tick tasks 4.1–4.4 in `openspec/changes/redesign-frontend-v2/tasks.md`; add implementation notes to the two specs only where the implementation refines them (client-side TWD totals with the 50-page cap and the `不含外幣` footnote, 有效待收 = pending rows per the owner's decision, the fourth tag 待人工釐清, no period label, 即將除權息 strip kept); commit `docs(openspec): transactions/dividends implementation notes; tick tasks 4.x`.
