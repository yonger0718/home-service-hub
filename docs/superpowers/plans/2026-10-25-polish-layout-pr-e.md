# Polish Layout v2 — PR E (pages) Implementation Plan

_Rev 2.1 — AGENT-285 re-check PASS-with-minor: Task 9 spec line numbers refreshed (+1 after Task 1's import; `1785` due-badge added). Rev 2 — folds Multica AGENT-285 (dev-grok): tick callback uses the label index (T6), `.dwrap` carries the negative FAB margin (T4), bill-line and import spec rewrites keep their assertions (T8/T9), `.tl-sub` fee rule pinned + R4 corrected (T7), `clear()` resets `month` (T1 fold in T3), liquidation-then-cash-only test (T5). Rev 1 — for Multica plan review._

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** D7 — at ≥ 1280 px the empty accounting pane shows the month overview (category distribution with amounts, next three due items), 帳戶 marks the selected account and says 選擇帳戶查看明細，或按 ＋ 新增; accounting titles move onto the page background; D8 — the net-worth chart carries holdings across cash-only snapshot rows; the 現金帳戶 balance chart fills its card with `MM/DD` ticks, no markers and token colours; 股利 hides the 舊資料帳面 card at 0 and the 即將除權息 row when empty; 已實現損益 costs stay neutral; 匯入 shows its footer only once a file is chosen and uses a switch; 🔔 / 💳 / entry-detail glyphs become line icons.

**Architecture:** The accounting layout owns the empty pane, so D7 is a presentational `<app-month-overview>` the layout renders instead of `.pane-empty` when `mode() === 'panes'`, `LayoutModeService.wideSidebar()` (the existing 1280 px query) and `listKey() === 'timeline'`. The timeline already loads the month summary, the card statements and the 待完成 queue; it publishes `month`, `summary` and `dueItems` through a small root service (`AccountingOverviewService`) so nothing is fetched twice and the overview follows the month navigator. The distribution bar + legend styles become a Sass mixin shared by the timeline and the overview. The page title moves by hoisting `<app-page-header>` from the two list components into the layout (above the `.dbody` grid, as 現金帳戶 renders its header above both columns); the list components take an `embedded` input (set through `ngComponentOutletInputs`) and skip their own header when embedded, so phone mode is unchanged. D8 is a pure `carryHoldings(points)` in `dashboard-math.ts` applied where `chartPoints` is set, so the datasets, legend, tooltip and keyboard read-out all see the carried values. The balance chart gets token colours through `getComputedStyle` at compute time and recomputes on the appearance `dark$` stream. The rest are template/SCSS edits with test updates.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest + jsdom via `cd frontend && npx ng test --watch=false --include '<spec>'` (zsh: quote globs), SCSS with the `--app-*` tokens, PrimeNG `p-chart` (Chart.js), PrimeIcons, Playwright-in-Docker for the browser check (stubs in `~/reports/home-hub-playwright-stubs/`, server `~/workspace/home-hub-demo-preview/redesign-v2-pr101/redesign_server.py` on 127.0.0.1:18081).

**Spec:** `openspec/changes/polish-layout-v2/` (rev 12 on main `ac31412`): `design.md` D7, D8; `tasks.md` §5 (5.1–5.8) and §6.1; deltas `specs/frontend-accounting-ledger/spec.md` (ADDED Empty pane shows the month, Accounts list marks the selection, Page title on the page background), `specs/frontend-portfolio-accounts/spec.md` (ADDED Balance chart fills its card), `specs/frontend-portfolio-dashboard/spec.md` (ADDED Chart carries holdings across cash-only snapshot rows — four scenarios), `specs/frontend-dividend-records/spec.md` (MODIFIED Summary row, Upcoming ex-dividend grid), `specs/frontend-realized-pnl/spec.md` + `specs/frontend-design-system/spec.md` (Gain and loss colours only for gains and losses), `specs/frontend-csv-import/spec.md` (MODIFIED Two-step import card), `specs/frontend-ui-primitives/spec.md` (Line icons for interface chrome). Code inventory with verified lines: `.superpowers/pr-e-inventory.md` (git-ignored; implementers get the relevant lines in their brief).

**Rulings baked into this plan (spec silent or ambiguous — reviewers please confirm):**
- R1 — The month overview renders only when the left list is the timeline (`listKey() === 'timeline'`). On `/accounting/accounts` the empty pane shows the 帳戶 copy at every panes width: the 帳戶 requirement names its empty copy without a width cut, and the D7 scenario is "Timeline without a selection".
- R2 — "Next three due items" = the three earliest of: card due days from `reminderDues` (45-day horizon) whose statement has a remaining balance, and `pending` schedule instances of the 待完成 queue (`queue: true`), sorted by date ascending (overdue first). Amounts: the card's statement total (as the timeline's bill lines show), the instance's first `totals` entry.
- R3 — 舊資料帳面 is omitted only once the totals are known and `legacy === 0`; while the summary is loading the four cards render as today (no 3 → 4 jump for a ledger that has legacy rows).
- R4 — 已實現損益 賣出成本費用 already renders in `--app-text` (`sign: 0`, no `.up`/`.down`), and 交易紀錄 row fees (`transaction-rows.ts:59` `feeLine` → `.tl-sub`) already render in `--app-text-muted` (`components/ui/timeline/timeline.scss:237-241`); 5.6 adds pinning tests only, no colour code changes.
- R5 — Icon scope is tasks.md 5.8: 🔔 (timeline), 💳 (calendar badge and the day's bill rows), the entry-detail actions, plus the same action glyphs repeated in `split-group.html`. ✕ ✓ ‹ › and the settings row glyphs stay (not in 5.8).
- R6 — Balance chart plot height 280 px (fixed, like the dashboard's 220 px pattern: absolute `p-chart` inside a positioned box; the loading / empty placeholders take the same height).

## Global Constraints

- Frontend only; no API, model or copy change beyond the surfaces named here. Branch `fix/polish-layout-pr-e` from `origin/main` (ac31412).
- Copy verbatim: 帳戶 empty pane 選擇帳戶查看明細，或按 ＋ 新增; timeline empty pane unchanged 選擇一筆記錄查看明細，或按 ＋ 新增; overview section titles 支出類別 and 近期到期; overview empty lines 本月尚無支出 and 沒有即將到期的項目; switch label 首列為欄位名稱; 🔔 aria-label unchanged (`提醒中心，N 項`).
- Breakpoints only through `LayoutModeService` (`mode()`; `wideSidebar()` for ≥ 1280 px — do not add a second 1280 px query). Colours only through tokens (`--app-primary`, `--app-primary-soft`, `--app-text`, `--app-text-muted`, `--app-surface`, `--app-border`, `--radius-sm`, `--radius-md`); chart colours read with `getComputedStyle(document.documentElement).getPropertyValue(token)`.
- D8 predicate verbatim: a point is cash-only when `Number(total_market_value) === 0 && Number(total_cost) === 0 && Number(total_unrealized_pnl) === 0 && Number(total_dividends) === 0 && Number(total_realized_pnl) === 0 && portfolio_xirr === null`. Carried fields: those five; 帳戶總值 for a carried point = carried `total_market_value` + the point's own `total_cash_twd` (write it into `total_assets_twd` of the copied point so `totalAssetsValue` needs no change). Backend rows, the `networthCache` contents and the API are untouched.
- Balance chart: x ticks `maxTicksLimit: 8`, `maxRotation: 0`, label `MM/DD`; `pointRadius: 0`, `pointHoverRadius: 4`; `borderColor` = `--app-primary`, `backgroundColor` = `--app-primary-soft`, `fill: true`; `y.beginAtZero: false`; plot box 280 px.
- Icons: PrimeIcons only — `pi-bell`, `pi-credit-card`, `pi-pencil`, `pi-copy`, `pi-undo`, `pi-check-circle`, `pi-trash` — every icon element `aria-hidden="true"`; button text labels stay.
- `dashboard.scss` is at its 10 kB `anyComponentStyle` budget: Task 5 adds no SCSS there.
- Every task: `cd frontend && npx ng test --watch=false --include '<touched specs>'` green before commit; the whole suite, `npx ng build` and `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` (repo root) green before the PR.

## Review Focus

1. **Carry must not swallow the existing "cash not populated" shape** — rows with market > 0 and cash 0 (`collapses total assets onto total market value` test) are not cash-only; a cash-only row with `total_cash_twd` 0 after holdings still carries (帳戶總值 = carried market). Pinned in Task 5 (`carryHoldings` tests: holdings-with-zero-cash untouched; cash-only with zero cash carries).
2. **Stale overview** — after a summary error, a month change or leaving `/accounting` for `/accounting/accounts`, the pane must not show the previous month's numbers. Pinned in Task 1 (timeline publishes `null` on load start / error, clears on destroy) and Task 2 (overview with `summary: null` renders 本月尚無支出).
3. **Header hoist and the page scroller** — `.list-pane` stays the `data-page-scroller` and the two panes still fit the viewport (no document scrollbar at 1280 × 800). Pinned in Task 4 (spec: `data-page-scroller` still on `.list-pane`, header outside it) and Task 11 (browser: `document.scrollingElement.scrollHeight <= innerHeight` at 1440 and 1280 on `/accounting`).
4. **The switch keeps the checkbox contract** — `disabled` while `busy()`, hidden for 券商對帳單, toggling re-previews with `has_header=false`, operable with Space (it is a `<button role="switch">`). Pinned in Task 8 (adapted test clicks `[role="switch"]`; new test: broker type renders no switch).
5. **Icon swap keeps names and tests honest** — `.due-badge` text becomes `2` / `` (icon aria-hidden), the bell keeps its aria-label, entry-detail buttons keep their visible labels. Pinned in Task 9 (updated assertions check `querySelector('.pi-credit-card')` + text, not the emoji).

---

### Task 1: `AccountingOverviewService` + `nextDueItems` — the timeline publishes its month data

**Files:**
- Create: `frontend/src/app/components/accounting/month-overview/due-items.ts`
- Create: `frontend/src/app/components/accounting/month-overview/due-items.spec.ts`
- Create: `frontend/src/app/components/accounting/month-overview/overview.service.ts`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (fields near lines 382–396, computeds near 481–536; `loadSummary` 761–781 unchanged)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  // due-items.ts
  export interface DueBill { key: string; name: string; date: string; amountText: string }   // prepared by the timeline from BillState
  export interface DueItem { key: string; kind: 'bill' | 'schedule'; name: string; date: string; amountText: string | null; dueText: string }
  export function nextDueItems(bills: readonly DueBill[], instances: readonly ScheduleInstance[], today: string, limit = 3): DueItem[];
  // overview.service.ts
  @Injectable({ providedIn: 'root' }) export class AccountingOverviewService {
    readonly month = signal<string>(currentMonth());
    readonly summary = signal<MonthSummary | null>(null);
    readonly dueItems = signal<DueItem[]>([]);
    clear(): void;   // summary null, dueItems []
  }
  ```
- `nextDueItems`: bills → `{ kind: 'bill', name, date, amountText }`; instances with `status === 'pending'` → `{ kind: 'schedule', key: 'schedule:' + id, name: queueTitle(item), date: due_date, amountText: totals[0] ? formatMoney(totals[0].amount, totals[0].currency) : null }`; `dueText = dueCountdown(date, today)` (`billing-math.ts:140`); sort by `date` ascending, ties bills first; `slice(0, limit)`. Bill `key` = the caller's `billKey(event)`.

- [ ] **Step 1: Write `due-items.spec.ts`**

```ts
describe('nextDueItems', () => {
  const inst = (id: number, due: string, status: ScheduleInstanceStatus = 'pending', amount = '-1200') =>
    ({ id, definition_name: '房租', seq: 3, times: null, due_date: due, status, totals: [{ currency: 'TWD', amount }] }) as ScheduleInstance;
  const bill: DueBill = { key: 'bill:7:2026-10', name: '玉山 UNI', date: '2026-10-28', amountText: '$12,345' };

  it('merges bills and pending instances, earliest first, three at most', () => {
    const items = nextDueItems([bill], [inst(1, '2026-10-26'), inst(2, '2026-11-05'), inst(3, '2026-11-20')], '2026-10-25');
    expect(items.map(i => [i.kind, i.date])).toEqual([['schedule', '2026-10-26'], ['bill', '2026-10-28'], ['schedule', '2026-11-05']]);
    expect(items[0]).toMatchObject({ name: '房租 #3', amountText: '−$1,200', dueText: '明天' });
    expect(items[1]).toMatchObject({ amountText: '$12,345', dueText: '還有 3 天' });
  });
  it('skips posted and skipped instances and lists overdue ones first', () => {
    const items = nextDueItems([], [inst(1, '2026-10-20'), inst(2, '2026-10-30', 'posted'), inst(3, '2026-10-29', 'skipped')], '2026-10-25');
    expect(items).toHaveLength(1);
    expect(items[0].dueText).toBe('已逾期 5 天');
  });
  it('puts a bill before an instance due the same day and tolerates an instance without totals', () => {
    const items = nextDueItems([bill], [{ ...inst(9, '2026-10-28'), totals: [] }], '2026-10-25');
    expect(items.map(i => i.kind)).toEqual(['bill', 'schedule']);
    expect(items[1].amountText).toBeNull();
  });
});
```
(Use the accounting `formatMoney` from `components/accounting/format.ts` — `−` is U+2212 as that formatter emits.)

- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include 'src/app/components/accounting/month-overview/due-items.spec.ts'` → FAIL (module not found).

- [ ] **Step 3: Implement `due-items.ts` and `overview.service.ts`** per Interfaces. `currentMonth` comes from `timeline.ts:171` — move it (and `monthLabel`, `timeline.ts:187`) into `components/accounting/dates.ts` so the service does not import the component; keep re-exports in `timeline.ts` (`export { currentMonth, monthLabel } from '../dates'`) so existing imports and specs compile.

- [ ] **Step 4: Run** the spec → PASS.

- [ ] **Step 5: Timeline publishes.** In `timeline.ts`: inject `AccountingOverviewService` as `overview`; add
  ```ts
  /** The overview pane's three due rows: dues with a remaining statement (45-day horizon) + the pending queue. */
  readonly dueItems = computed(() =>
    nextDueItems(
      this.reminderEvents().flatMap(event => { const bill = this.openBill(event); return bill ? [{ key: billKey(event), name: event.name, date: event.date, amountText: formatMoney(bill.statement, bill.currency) }] : []; }),
      this.queue(), this.today()));
  ```
  and in the constructor three `effect`s: `overview.month.set(this.month())`, `overview.summary.set(this.summary())`, `overview.dueItems.set(this.dueItems())`; `inject(DestroyRef).onDestroy(() => this.overview.clear())`. `loadSummary` already sets `summary(null)` at start and on error (lines 763, 775) — nothing else to publish.

- [ ] **Step 6: Timeline tests** (append to `timeline.spec.ts`, using the file's existing `setup`/fixtures for a month summary with `by_category` and a queue response):
  - `publishes month, summary and due items to AccountingOverviewService` — after init + flush: `overview.month()` equals the component's `month()`, `overview.summary()` is the flushed summary, `overview.dueItems()[0].kind` is `'schedule'` for a pending queue instance due within the horizon.
  - `clears the overview on destroy` — `fixture.destroy()` → `overview.summary()` null, `overview.dueItems()` `[]`.
  - `resets the published summary while a new month loads` — `moveMonth(1)` before flushing → `overview.summary()` null.

- [ ] **Step 7: Run** `--include 'src/app/components/accounting/timeline/*.spec.ts' --include 'src/app/components/accounting/month-overview/*.spec.ts'` → PASS (108 + 3 timeline, 3 due-items).

- [ ] **Step 8: Commit** — `feat(accounting): overview service — timeline publishes month summary and the next due items (D7)`.

_Rev 2 fold (done inside Task 3's dispatch, own commit): `clear()` also resets `month` to `currentMonth()` so a re-created overview never titles the previous month; the destroy test asserts `overview.month()` equals `currentMonth()`._

---

### Task 2: `<app-month-overview>` in the empty pane at ≥ 1280 px; 帳戶 empty copy

**Files:**
- Create: `frontend/src/app/components/accounting/timeline/_distribution.scss` (mixin `distribution`, the `.dist-bar / .dist-list / .dist-row / .dist-swatch / .dist-name` rules now at `timeline.scss:107-149`)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.scss` (`@use './distribution'; @include distribution;` in place of the moved rules — no visual change)
- Create: `frontend/src/app/components/accounting/month-overview/month-overview.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html:44-47`, `accounting-layout.ts` (new computeds), `accounting-layout.scss:56-61`
- Test: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Consumes: `AccountingOverviewService` (Task 1), `categoryDistribution`, `distributionAriaText` (`timeline/category-distribution.ts`), `monthLabel` (`dates.ts` after Task 1).
- Produces: `MonthOverviewComponent` (`selector: 'app-month-overview'`) with `month = input.required<string>()`, `summary = input<MonthSummary | null>(null)`, `dueItems = input<DueItem[]>([])`; layout computeds `showOverview = computed(() => this.mode() === 'panes' && this.layoutMode.wideSidebar() && this.listKey() === 'timeline' && !this.paneOpen())` and `emptyCopy = computed(() => this.listKey() === 'accounts' ? '選擇帳戶查看明細，或按 ＋ 新增' : '選擇一筆記錄查看明細，或按 ＋ 新增')`.

- [ ] **Step 1: Overview spec** (`month-overview.spec.ts`, plain `TestBed.createComponent` + `setInput`):
  - `titles the month and lists the distribution with amounts` — summary with three categories (`by_category` expenses `-3000`, `-1200`, `-500`, currency TWD) → `h3.ov-month` text `2026 年 10 月` (= `monthLabel('2026-10')`), `.dist-bar span` count 3, `.dist-row` texts contain the names and `3,000`, `1,200`, `500`; `.dist-bar` has `role="img"` and `aria-label` = `distributionAriaText(categoryDistribution(summary))`.
  - `renders the empty lines without data` — `summary: null`, `dueItems: []` → text contains 本月尚無支出 and 沒有即將到期的項目, no `.dist-bar`, no `.due-row`.
  - `lists at most the given due items with icon, name, countdown and amount` — two items (one bill, one schedule) → two `.due-row`; the first has `i.pi-credit-card[aria-hidden="true"]`, the second `i.pi-replay`; texts contain `玉山 UNI`, `還有 3 天`, `$12,345`; no `<a>` or `<button>` inside the component (D7: no links).

- [ ] **Step 2: Run** `--include 'src/app/components/accounting/month-overview/*.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** Template: `<h3 class="ov-month">{{ monthLabel(month()) }}</h3>`, `<h4 class="ov-title">支出類別</h4>`, then the timeline's bar + legend markup (`timeline.html:28-45` minus the phone branches; every segment row shows `seg.name` and `seg.label`), or `<p class="ov-empty">本月尚無支出</p>`; `<h4 class="ov-title">近期到期</h4>` then `<ul class="due-list">` of `<li class="due-row">` = `<i class="pi" [class.pi-credit-card]="item.kind === 'bill'" [class.pi-replay]="item.kind === 'schedule'" aria-hidden="true">`, `<span class="due-name">`, `<span class="due-when">{{ item.dueText }}</span>`, `<b class="due-amount">` (`—` when null), or `<p class="ov-empty">沒有即將到期的項目</p>`. SCSS: `@use '../timeline/distribution'; @include distribution.distribution;`, `:host { display: block; padding: 1rem }`, titles `font-size: var(--fs-sm); color: var(--app-text-muted); font-weight: 700`, rows `display: flex; gap: 8px; align-items: center; padding: 6px 0; border-top: 1px solid var(--app-border)`.

- [ ] **Step 4: Layout.** Replace `accounting-layout.html:44-47` with
  ```html
  @if (showOverview()) {
    <app-month-overview [month]="overview.month()" [summary]="overview.summary()" [dueItems]="overview.dueItems()" />
  } @else if (!paneOpen()) {
    <p class="pane-empty">{{ emptyCopy() }}</p>
  }
  ```
  (`overview = inject(AccountingOverviewService)` protected readonly; import `MonthOverviewComponent`).

- [ ] **Step 5: Layout tests** (extend the `start()` helper with an optional `wide` flag that provides `MATCH_MEDIA` through the `fakeMatchMedia` pattern of `services/layout-mode.service.spec.ts:18-45` at 1440 px, or `set('panes')` plus a 1440 px fake — either way `wideSidebar()` must be `true`):
  - `shows the month overview instead of the empty copy at 1440px on /accounting` — `app-month-overview` present, `.pane-empty` null.
  - `keeps the one-line empty copy at 1100px` — `.pane-empty` text 選擇一筆記錄查看明細，或按 ＋ 新增, no `app-month-overview`.
  - `says 選擇帳戶 on /accounting/accounts at 1440px` — `.pane-empty` text 選擇帳戶查看明細，或按 ＋ 新增, no overview.
  - `hides the overview once a pane page opens` — navigate to `/accounting/entries/1` (existing fixture) → no `app-month-overview`.
  The existing `keeps the list and updates the URL when a row is selected at 1280px` (spec line 70) keeps asserting `.pane-empty` because its `start('panes', …)` has `wideSidebar()` false in jsdom (1024 px); leave it.

- [ ] **Step 6: Run** `--include 'src/app/components/accounting/accounting-layout/*.spec.ts' --include 'src/app/components/accounting/month-overview/*.spec.ts' --include 'src/app/components/accounting/timeline/*.spec.ts'` → PASS.

- [ ] **Step 7: Commit** — `feat(accounting): month overview in the empty pane at ≥ 1280 px; 帳戶 empty copy (D7)`.

---

### Task 3: 帳戶 list marks the selected account

**Files:**
- Modify: `frontend/src/app/components/accounting/accounts/accounts.ts` (inject `Router`, already at line 128), `accounts.html:18`, `accounts.scss:38-50`
- Test: `frontend/src/app/components/accounting/accounts/accounts.spec.ts`

**Interfaces:** Produces `selectedAccountId = computed<number | null>` from the router URL: `/^\/accounting\/accounts\/(\d+)(?:\/|$)/` on `router.url` (tracked with `toSignal(router.events.pipe(filter(e => e instanceof NavigationEnd), map(() => router.url)), { initialValue: router.url })`) — `null` for `/accounting/accounts` and `/accounting/accounts/new`; `isSelected(account) = account.id === selectedAccountId()`.

- [ ] **Step 1: Tests** (in `accounts.spec.ts`, the file's `TestBed.createComponent` setup with `provideRouter`):
  - `marks the routed account with .sel and aria-current` — navigate the test router to `/accounting/accounts/7`, flush accounts 7 and 8 → row 7 has class `sel` and `aria-current="true"`; row 8 has neither attribute (`getAttribute('aria-current')` null).
  - `marks nothing on the list URL or /new` — at `/accounting/accounts` and `/accounting/accounts/new` → no `.acc-row.sel`.
  - stylesheet: `accounts.scss` contains `.acc-row.sel` with `box-shadow: inset 0 0 0 2px var(--app-primary)` and `background: var(--app-primary-soft)` (read the file as `theme/kpi-values.spec.ts` does).

- [ ] **Step 2: Run** `--include 'src/app/components/accounting/accounts/*.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** `accounts.html:18`: add `[class.sel]="isSelected(account)" [attr.aria-current]="isSelected(account) ? 'true' : null"`. `accounts.scss`: 
  ```scss
  .acc-row.sel {
    background: var(--app-primary-soft);
    border-radius: var(--radius-sm);
    box-shadow: inset 0 0 0 2px var(--app-primary);
    margin-inline: calc(-1 * var(--row-sel-bleed, 0px));
    padding-inline: var(--row-sel-bleed, 0px);
  }
  .acc-row.sel.sub { padding-left: calc(22px + var(--row-sel-bleed, 0px)); }
  ```
  (the `.pane` sets `--row-sel-bleed: 8px`; the ring is the indigo `--app-primary` inset ring the spec names, over the entry rows' soft fill).

- [ ] **Step 4: Run** → PASS (33 + 3).

- [ ] **Step 5: Commit** — `feat(accounting): 帳戶 list marks the routed account with the selection ring (D7)`.

---

### Task 4: Accounting page title on the page background

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html:4-9`, `accounting-layout.ts` (imports, `addAccount()`), `accounting-layout.scss:25-39`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html:2`, `timeline.ts` (`embedded` input)
- Modify: `frontend/src/app/components/accounting/accounts/accounts.html:2-4`, `accounts.ts` (`embedded` input)
- Test: `accounting-layout.spec.ts`, `timeline.spec.ts`, `accounts.spec.ts`

**Interfaces:**
- Produces: `embedded = input(false)` on `LedgerTimelineComponent` and `AccountingAccountsComponent` (template: `@if (!embedded()) { <app-page-header>…</app-page-header> }`); the layout renders `<ng-container *ngComponentOutlet="list; inputs: { embedded: true }" />` and, above `.dbody`:
  ```html
  <div class="dwrap" [class.dwrap--sheet]="mode() === 'sheet'">
    <app-page-header>
      @if (listKey() === 'accounts') {
        <app-btn class="add-account" variant="soft" icon="pi-plus" (click)="addAccount()">新增帳戶</app-btn>
      }
    </app-page-header>
    <div class="dbody" …>  …existing…  </div>
  </div>
  ```
  `addAccount()` = `void this.router.navigateByUrl('/accounting/accounts/new')` (moved verbatim from `accounts.ts:256-258`, which keeps its own copy for phone mode).
- SCSS: `.dwrap { display: flex; flex-direction: column; height: calc(100dvh - var(--page-pad-y) - var(--page-pad-b)); margin-bottom: calc(-1 * var(--fab-clear, 0px)); min-height: 360px; }`, `.dbody { flex: 1; min-height: 0; height: auto; margin-bottom: 0; }` (drop the `height`, `min-height` and negative `margin-bottom` now on `.dbody` — the shell pads the page bottom by `page-pad-b + fab-clear`, so the wrapper that owns the height must own the compensating margin or `/accounting` scrolls at 1280 × 800), `.dwrap--sheet { display: block; height: auto; margin-bottom: 0; }`. `.list-pane` keeps `padding-bottom: calc(14px + var(--fab-clear, 0px))`. `app-page-header` keeps its own `margin-bottom: 1rem`. Stylesheet test: `accounting-layout.scss` has `.dwrap {` containing `margin-bottom: calc(-1 * var(--fab-clear, 0px))` and `.dbody {` no longer containing `margin-bottom: calc(-1`.

- [ ] **Step 1: Tests.**
  - `accounting-layout.spec.ts`: `renders the page title above the list card` — at `start('panes', '/accounting')`: `harness.routeNativeElement.querySelector(':scope > .dwrap > app-page-header h1')` text 記帳記錄; `.list-pane app-page-header` null; `.list-pane` still has `data-page-scroller`. `renders 新增帳戶 in the hoisted header on /accounting/accounts` — `.dwrap > app-page-header app-btn.add-account button` exists; click → `Location.path()` is `/accounting/accounts/new`. `keeps the title inside the list in sheet mode too` is NOT a requirement — instead `hoists the header in sheet mode` — `start('sheet', '/accounting')` → `.dwrap--sheet > app-page-header` present.
  - `timeline.spec.ts`: `omits its page header when embedded` — `fixture.componentRef.setInput('embedded', true)` → no `app-page-header`; default still renders it.
  - `accounts.spec.ts`: same two assertions; the existing test at spec line 364 (`app-page-header .page-actions app-btn.add-account button`) stays valid for the non-embedded default.

- [ ] **Step 2: Run** the three specs → FAIL.

- [ ] **Step 3: Implement** per Interfaces (`NgComponentOutlet` inputs syntax: `*ngComponentOutlet="list; inputs: outletInputs"` with `protected readonly outletInputs = { embedded: true }`).

- [ ] **Step 4: Run** the three specs → PASS. Also `--include 'src/app/components/shell/*.spec.ts'` (FAB offset tests read the layout) → PASS.

- [ ] **Step 5: Commit** — `feat(accounting): page title and actions on the page background above the list card (5.2)`.

---

### Task 5: Net-worth chart carries holdings across cash-only rows (D8)

**Files:**
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard-math.ts` (append), `dashboard.ts:612-640` (`loadNetworthHistory`) and `:366-376` (`selectRange`)
- Test: `frontend/src/app/components/portfolio/dashboard/dashboard-math.spec.ts`, `dashboard.spec.ts`

**Interfaces:** Produces
```ts
export function isCashOnlyPoint(point: NetworthPoint): boolean;
/** D8: cash-only runs after a holdings point take that point's five stock columns; 帳戶總值 = carried market + own cash. */
export function carryHoldings(points: readonly NetworthPoint[]): NetworthPoint[];
```
`carryHoldings` returns new objects for carried points (`{ ...point, total_market_value, total_cost, total_unrealized_pnl, total_dividends, total_realized_pnl, total_assets_twd: String(Number(carried.total_market_value) + Number(point.total_cash_twd || 0)) }`) and the same references otherwise; `date` and `total_cash_twd` never change.

- [ ] **Step 1: `dashboard-math.spec.ts`** — helper `row(date, { market = '0', cost = '0', upnl = '0', div = '0', rpnl = '0', xirr = null, cash = '0' })` with `total_assets_twd = String(Number(market) + Number(cash))`:
  - `isCashOnlyPoint`: true for all-zero + null xirr; false when any of the five is non-zero; false when xirr is `'0'` or `'0.12'`.
  - `leading cash-only rows plot as written` — `[cashOnly(1/5, cash 50), holdings(1/6)]` → first point `toBe` the input reference.
  - `a Saturday between two trading days carries Friday` — Fri market 2.9 M cost 2.5 M upnl 0.4 M div 10 k rpnl 20 k xirr '0.1' cash 100 k; Sat cash-only cash 114.6 k; Mon holdings → Sat point `toMatchObject({ date: '2026-01-10', total_market_value: '2900000', total_cost: '2500000', total_unrealized_pnl: '400000', total_dividends: '10000', total_realized_pnl: '20000', total_cash_twd: '114600', total_assets_twd: '3014600' })`; `portfolio_xirr` stays `null` (not one of the five).
  - `Saturday and Sunday both carry Friday` — two consecutive cash-only rows → both carry; Monday unchanged.
  - `liquidation is not bridged` — `row(…, { rpnl: '35205' })` after holdings → same reference, market stays `'0'`.
  - `a cash-only day after a liquidation stays at 0` — `[holdings, liquidation (rpnl '35205'), cashOnly]` → the third point's `total_market_value` stays `'0'` (the liquidation row is the new "last non-cash-only point" and its market is 0).
  - `holdings with zero cash are not cash-only` — market '100', cash '0' → unchanged reference.

- [ ] **Step 2: Run** `--include 'src/app/components/portfolio/dashboard/dashboard-math.spec.ts'` → FAIL.

- [ ] **Step 3: Implement** in `dashboard-math.ts`; apply in `dashboard.ts`: every `this.chartPoints.set(x)` for history rows (`selectRange` cached branch 373, `loadNetworthHistory` 616 and 626; the error branch 634 sets `[]` and prefetch writes only the cache) becomes `this.chartPoints.set(carryHoldings(x))`; the cache keeps the raw rows.

- [ ] **Step 4: `dashboard.spec.ts`** — in `describe('net-worth chart')` (line 860, helper `point(date, market, cash, total)` extended with the five fields where needed): `carries Friday's holdings onto a cash-only Saturday` — history `[Fri(100, cash 40, total 140), Sat cash-only (cash 54, total 54), Mon(120, cash 60, total 180)]` → `chartData.datasets[1].data` `[100, 100, 120]` and `datasets[0].data` `[140, 154, 180]`; the tooltip `afterBody` for index 1 reads `現金 $54` (whatever the existing `money` format yields for 54); the existing `collapses total assets…` test (581) still passes untouched.

- [ ] **Step 5: Run** `--include 'src/app/components/portfolio/dashboard/*.spec.ts'` → PASS.

- [ ] **Step 6: Commit** — `fix(dashboard): net-worth chart carries holdings across cash-only snapshot rows (D8)`.

---

### Task 6: 現金帳戶 balance chart fills its card

**Files:**
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts:226-278`, `account-detail.scss:92-104`
- Test: `frontend/src/app/components/portfolio/accounts/account-detail.component.spec.ts`

**Interfaces:** Produces in `AccountDetailComponent`: `private readonly dark = toSignal(inject(AppearanceService).dark$, { initialValue: false })`; `chartData` computed reads `this.dark()` then `cssVar('--app-primary')` / `cssVar('--app-primary-soft')` (private `cssVar(name)` = `getComputedStyle(document.documentElement).getPropertyValue(name).trim()`); dataset gains `pointRadius: 0, pointHoverRadius: 4, fill: true, borderWidth: 2`; `chartOptions.scales.x.ticks = { maxTicksLimit: 8, maxRotation: 0, autoSkip: true, callback: (value: string | number) => this.tickLabel(Number(value)) }` with `tickLabel(labelIndex) = mmdd(this.chartData().labels[labelIndex] ?? '')` (`'2026-09-01' → '09/01'`, empty string for a missing label). Chart.js 4 calls a category tick callback as `callback(labelIndex, visibleTickIndex, ticks)` — the FIRST argument is the label index; using the second would place the first dates on later ticks once `autoSkip` drops ticks.; `chartOptions.scales.y.beginAtZero = false`; `animation: false`.

- [ ] **Step 1: Tests** (the spec stubs `p-chart` at line 22; `document.documentElement.style.setProperty('--app-primary', '#112233')` in the test, cleared in `afterEach`):
  - `colours the balance line from the theme tokens and recolours on a theme change` — `datasets[0].borderColor` `'#112233'`, `backgroundColor` = the `--app-primary-soft` value set; set `--app-primary` to `'#445566'` and emit `dark$.next(true)` → `borderColor` `'#445566'`.
  - `draws no point markers and fills under the step line` — `toMatchObject({ pointRadius: 0, pointHoverRadius: 4, fill: true, stepped: true })`.
  - `labels the x axis MM/DD with at most 8 ticks` — `chartOptions.scales.x.ticks.maxTicksLimit` 8, `maxRotation` 0; with labels `['2026-09-01', '2026-11-30']`, `callback(0, 0)` → `'09/01'`, `callback(1, 0)` → `'11/30'` (second label, first visible tick), `callback(5, 0)` → `''`; `scales.y.beginAtZero` false.
  - stylesheet: `account-detail.scss` contains `.balance-chart {` with `height: 280px` and `position: relative`, and `.chart-loading, .networth-empty` with `height: 280px`.

- [ ] **Step 2: Run** `--include 'src/app/components/portfolio/accounts/account-detail.component.spec.ts'` → FAIL.

- [ ] **Step 3: Implement** per Interfaces; SCSS `.balance-chart { height: 280px; margin-top: 1rem; position: relative; p-chart { inset: 0; position: absolute !important; } canvas { height: 100% !important; width: 100% !important; } }` (the dashboard's pattern, `dashboard.scss:268-288`); placeholders `height: 280px`.

- [ ] **Step 4: Run** → PASS (44 + 4).

- [ ] **Step 5: Commit** — `fix(accounts): balance chart fills its card — MM/DD ticks, no markers, token colours (5.4)`.

---

### Task 7: 股利 empty card and strip; 已實現損益 cost colour pinned

**Files:**
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.html:21-26` and `:41-51`
- Test: `dividend-list.spec.ts` (labels at 502 and 1882), `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.component.spec.ts`, `frontend/src/app/theme/kpi-values.spec.ts`

- [ ] **Step 1: Dividend tests.** Both label assertions (502, 1882) keep four labels because their fixtures include legacy rows (check; if a fixture has no legacy rows, the expectation becomes three labels). Add:
  - `omits 舊資料帳面 once the totals are known and legacy is 0` — fixture without legacy rows → labels `['已確認收款', '有效待收', '已記錄股利']`.
  - `renders the four cards while the summary is still loading` — before flushing the page requests → 4 `.kpi-card` (desktop).
  - `renders no 即將除權息提醒 row when the list is empty` — `getUpcomingExDividends` → `[]` → `.upcoming-strip` null; with one event (existing test at 981) the strip renders and shows no 目前沒有即將除權息提醒 text anywhere.

- [ ] **Step 2: Run** `--include 'src/app/components/portfolio/dividend-list/dividend-list.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** Card: the current `@if (!isPhone())` wraps both 舊資料帳面 and 已記錄股利 — split it so only the legacy card gains the condition: `@if (!isPhone() && (totals === null || totals.legacy !== 0)) { …舊資料帳面 card… }` (`totals` = the template's existing alias for `summary()`; 已記錄股利 keeps plain `@if (!isPhone())`). Strip: wrap the `<section class="upcoming-strip">` in `@if (upcomingExDividends().length > 0) { … }` and delete the `@empty` branch.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Cost colour tests (R4, no code change).** The gain/loss convention only swaps the `--app-trend-*` token values (`AppearanceService.apply`), so a value without `.up` / `.down` is convention-independent by construction; `realized-pnl.component.spec.ts:551-560` already asserts `up: false, down: false` for 賣出成本費用 — add the label to that assertion's message if missing, no new component test. `theme/kpi-values.spec.ts`: `kpi-cards.scss gives .value the neutral text colour and trend colours only to .up / .down` — file contains `color: var(--app-text);` inside `.value {` before `&.up`, and `--app-trend-positive` appears only inside `&.up`; plus `row fees and taxes render muted` — `components/ui/timeline/timeline.scss` contains a `.tl-sub` rule (lines ~237-241) with `color: var(--app-text-muted)` and no `--app-trend` token (the design-system "Transaction fees" scenario: `transaction-rows.ts:59` puts 手續費 / 交易稅 into `.tl-sub`). Run `--include 'src/app/components/portfolio/realized-pnl/*.spec.ts' --include 'src/app/theme/*.spec.ts'` → PASS.

- [ ] **Step 6: Commit** — `fix(portfolio): 股利 hides 舊資料帳面 at 0 and the empty 即將除權息 row; cost colour pinned (5.5, 5.6)`.

---

### Task 8: 匯入 footer after a file is chosen; 首列為欄位名稱 as a switch

**Files:**
- Modify: `frontend/src/app/components/portfolio/import/import.html:41-44` and `:205-211`, `import.ts` (imports: add `SwitchComponent`; drop `CheckboxModule` if no `p-checkbox` remains), `import.scss:73-79`
- Test: `frontend/src/app/components/portfolio/import/import.spec.ts` (`headerCheckbox` helper line 74, test at 498)

- [ ] **Step 1: Tests.**
  - Change the test at 498 to find `label.header-check [role="switch"]` and `click()` it (no `whenStable` needed: the switch is a button); expectation unchanged (`has_header=false`).
  - `renders 首列為欄位名稱 as a switch that is on by default and disabled while busy` — `[role="switch"][aria-checked="true"]` present; with `busy()` true (set through the component's busy signal or during an in-flight preview) the button is `disabled`.
  - `hides the switch for 券商對帳單` — select type `broker` → no `label.header-check`.
  - `renders no footer before a file is chosen` — fresh component → `.imp-footer` null (so no 確認匯入 button, the spec's Fresh import screen scenario); after `onSelect({ files: [file] })` → `.imp-footer` present.
  - Existing tests that read the footer on a fresh page must select a file first and keep their assertions: `import.spec.ts:831` and `:936` (重新預覽 / 確認匯入 disabled on a fresh page → call `onSelect` first, then assert disabled while no result), `:1156` and `:1166` (`.imp-footer` precedes the side cards on sheet and phone → after `onSelect`), `:844` (取消 → after `onClear()` assert `.imp-footer` null as well as the cleared state). The `commitButton` helper (555) already looks inside `.imp-footer`.

- [ ] **Step 2: Run** `--include 'src/app/components/portfolio/import/import.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** `import.html:41-44` →
  ```html
  @if (type() !== 'broker') {
    <label class="header-check">
      <app-switch ariaLabel="首列為欄位名稱" [checked]="hasHeader()" [disabled]="busy()" (checkedChange)="onHeaderChange($event)" />
      <span>首列為欄位名稱</span>
    </label>
  }
  ```
  Footer: wrap `<footer class="imp-footer">…</footer>` in `@if (file()) { … }`. SCSS `.header-check { gap: .6rem }` (keep the rest).

- [ ] **Step 4: Run** → PASS (82 ± adjusted).

- [ ] **Step 5: Commit** — `fix(import): footer only once a file is chosen; 首列為欄位名稱 is a switch (5.7)`.

---

### Task 9: Line icons for 🔔, 💳 and the entry-detail actions

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html:8` and `:197`, `timeline.scss:291-300` (`.bell` icon size)
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-month.html:26-28`, `calendar-month.scss:99-107`
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.html:236-246`, `entry-detail.scss:156-185` (`.ico` → `font-size: 1.1rem; line-height: 1`)
- Modify: `frontend/src/app/components/accounting/split-group/split-group.html:87-89`
- Test: `timeline.spec.ts:1498-1647` (💳 / 🔔 text assertions), `calendar-month.spec.ts:106,112`, `entry-detail.spec.ts`, `split-group.spec.ts`

**Interfaces:** bell → `<i class="pi pi-bell" aria-hidden="true"></i>` (aria-label on the button unchanged); calendar badge → `<span class="due-badge" aria-hidden="true"><i class="pi pi-credit-card"></i>{{ cell.dueCount > 1 ? cell.dueCount : '' }}</span>`; bill rows → `<i class="pi pi-credit-card" aria-hidden="true"></i> {{ bill.name }} 帳單 …`; actions → `<i class="ico pi pi-pencil" aria-hidden="true"></i>編輯`, `pi-copy` 複製, `pi-undo` 退款, `pi-check-circle` {{ settleLabel() }}, `pi-trash` 刪除 / 刪除整組 (the `.ico` class stays on the `<i>` so the existing sizing rules apply).

- [ ] **Step 1: Update tests.** `calendar-month.spec.ts:106` → badge has `i.pi-credit-card` and `text(badge)` is `'2'`; `:112` → `''`. `timeline.spec.ts` due-badge texts at `1499`, `1551` and `1785` likewise (`i.pi-credit-card`, text `''`; the `1785` test has no `.bill`); the bill-line assertions `1505`, `1555` (the next line still expects `.partial`), `1617`, `1639`, `1806` keep their FULL strings minus the leading emoji (`text()` trims): `玉山 UNI 帳單 $12,345 · 到期`, `玉山 UNI 帳單 $12,345 · 到期 已繳 $5,000 · 剩餘 $7,345`, `… $200 · 到期`, `… $1,234 · 到期`, and each `.bill` additionally contains `i.pi-credit-card[aria-hidden="true"]`; `calendar-month.spec.ts:107` (`aria-hidden`) and the cell `aria-label`s at `:109` / `:113` stay; `1648` → `bell(el).querySelector('i.pi-bell')` not null and `text(bell(el))` is `''`. (Line numbers as of 64accd3; grep the emoji before editing.) `entry-detail.spec.ts`: add `uses line icons on the actions` — `.action-edit i.pi-pencil`, `.action-copy i.pi-copy`, `.action-refund i.pi-undo`, `.action-settle i.pi-check-circle`, `.action-delete i.pi-trash`, each `aria-hidden="true"`, and `nav.actions` text contains no character ≥ U+1F300 and none of `✎ ⧉ ↺`. `split-group.spec.ts`: `.action-edit i.pi-pencil`, `.action-delete i.pi-trash`.

- [ ] **Step 2: Run** the four specs → FAIL.

- [ ] **Step 3: Implement** per Interfaces; `.bell { font-size: 1.05rem }` keeps the icon the emoji's size; `.due-badge { display: inline-flex; gap: 1px; align-items: center; i { font-size: 0.7rem } }`.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** — `fix(accounting): line icons for the bell, card markers and entry actions (5.8)`.

---

### Task 10: Whole suite, build, openspec

- [ ] **Step 1:** `cd frontend && npx ng test --watch=false` → all files green (expect ≈ 2516 + ~30 tests). `npx ng build` → exit 0, only the protobufjs warning (no `anyComponentStyle` budget warning — `dashboard.scss` untouched). Repo root: `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` → `valid`.
- [ ] **Step 2:** Fix anything red inside the owning task's files; commit `test: …` / `fix: …` as needed.

---

### Task 11: Browser evidence (§6.1) — three widths, light and dark

**Files:** `~/reports/home-hub-playwright-stubs/pr_e_shots.py` (new; `pr_d_lib.py` helpers `stub`, `ctx_for`, `goto`), output `~/reports/home-hub-polish-pr-e-<TS>/` (`NOTES.md`, `probe.json`, PNGs), preview server as in PR D (rsync the build into `redesign-v2-pr101/dist`, start `nohup python3 -I redesign_server.py &`, stop by PID from `ss -ltnp`). Stubbed APIs, fictional data only; stop the server before any SPA publish; never touch 18080.

- [ ] **Step 1: Stubs** — extend the stub responses: `GET /api/accounting/entries/summary?month=` with four `by_category` roots; `GET /api/accounting/schedules/instances?queue=true` (the service's path) with two pending instances (one tomorrow, one in 20 days); the accounts fixture with one card whose statement is due in 3 days; `GET /api/portfolio/networth-history` with the Fri / Sat / Mon rows of Task 5; `GET /api/portfolio/accounts/1/balance-history` with 40 daily points; `GET /api/portfolio/ex-dividends/upcoming` → `[]`; dividends fixture without legacy rows.
- [ ] **Step 2: Probe (light + dark, 1440 × 900, 1100 × 800, 390 × 844)**, writing `probe.json`:
  - `/accounting` 1440: `app-month-overview` present, 3 `.due-row`, `.dist-row` count 4, `document.scrollingElement.scrollHeight <= innerHeight`, `app-page-header h1` bounding rect top < `.list-pane` rect top and h1 is not a descendant of `.list-pane`; 1100: `.pane-empty` present, no overview. 1280 × 800 extra: overview present, no document scrollbar.
  - `/accounting/accounts/7` 1440: `.acc-row.sel[aria-current="true"]` present, computed `box-shadow` contains `2px`; `/accounting/accounts` → `.pane-empty` text 選擇帳戶查看明細，或按 ＋ 新增.
  - `/portfolio` 1440: read the chart datasets via `window.Chart?.instances` or the component (`ng.getComponent(document.querySelector('app-dashboard')).chartData`) → `datasets[1].data[1] === datasets[1].data[0]`.
  - `/portfolio/accounts/1` 1440: `.balance-chart` height 280, `canvas` height 280; x-axis tick count ≤ 8 (`chart.scales.x.ticks.length` via `ng.getComponent`/Chart registry); dataset `pointRadius` 0; `borderColor` equals `getComputedStyle(document.documentElement).getPropertyValue('--app-primary').trim()` in both themes.
  - `/portfolio/dividends` 1440: 3 `.kpi-card`, no `.upcoming-strip`; `/portfolio/import`: no `.imp-footer`, `[role="switch"]` present; after setting a file through `page.set_input_files` on the hidden input → `.imp-footer` present.
  - `/accounting` 390: `i.pi-bell` in `.bell`; calendar view → `.due-badge i.pi-credit-card`; `/accounting/entries/1` → `.actions i.pi-pencil`.
  - 0 `pageerror` / console errors on every page.
- [ ] **Step 3: Screenshots** before (main `ac31412` build, served first) and after, at the three widths, light and dark: `/accounting`, `/accounting/accounts/7`, `/portfolio`, `/portfolio/accounts/1`, `/portfolio/dividends`, `/portfolio/import`, `/accounting/entries/1`. Name `before-<page>-<w>-<theme>.png` / `after-…`.
- [ ] **Step 4: NOTES.md** — table of probe results vs. the spec scenarios; anything red → fix wave in the owning task's files, re-run the affected probes, append `recheck-N/`.
- [ ] **Step 5:** stop the preview server (PID), leave the report dir for the PR body; commit nothing from `~/reports`.

---

## Self-review notes (rev 1)

- Spec coverage: Empty pane → T1/T2; Accounts list marks selection → T3; Page title → T4; Chart carries holdings (4 scenarios) → T5; Balance chart (two scenarios) → T6 (dark-mode line = `dark$` recompute test); Summary row (No legacy receipts) + Upcoming grid (No upcoming events) → T7; Realized summary / design-system cost colour → T7 Step 5 (R4); Two-step import card (Fresh import screen, switch) → T8; Line icons (Entry detail actions) → T9; §6.1 → T11. "Rows keep the page gutter" is PR A (1.5) — not here.
- Type consistency: `DueItem` / `DueBill` / `nextDueItems` (T1) are what T2 consumes; `embedded` (T4) is a plain `input(false)` on both list components; `carryHoldings` (T5) returns `NetworthPoint[]`.
- Proportion: 11 tasks, mostly template + spec edits; the only new units are `due-items.ts`, `overview.service.ts`, `month-overview.*` and the two `dashboard-math.ts` functions.
