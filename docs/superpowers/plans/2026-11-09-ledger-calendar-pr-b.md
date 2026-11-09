# Ledger Calendar PR B — Wide Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At ≥ 1024 px the 日曆 view fills the left pane with the grid alone, the monthbar carries the month's 支出 / 收入 / 結餘 between the navigator and the 清單 / 日曆 toggle, and the right pane follows the selection: 本月摘要 (search, 今天, distribution, 花最多的商家, 這個月要處理) with no day, the day panel with a day, a search result list while searching, and an entry drawer over it all — plus the PR A follow-ups (split-group and global ＋ exits keep `month`/`day`, merchants read only when wide).

**Architecture:** The timeline (left pane) stays the owner of every read; it publishes the calendar's state through a new `CalendarPaneService` (signals) and one boolean `AccountingOverviewService.calendar`, and hands two commands (`stepEntry`, `openInvoice`) to the layout. `AccountingLayoutComponent` gains a calendar mode (`panes` + `timeline` + 日曆) that swaps the column ratio to 2 : 1 (the timeline moves the figures into its monthbar), renders the right pane's base layer (`MonthOverviewComponent` in calendar mode / new `CalendarDayPanelComponent`) and wraps the `pane` outlet in a drawer. Day writes from the right pane go through one shared URL writer (`writeCalendarQuery`); the timeline already syncs from the URL on `NavigationEnd` (PR A), so no second channel is needed.

**Tech Stack:** Angular 21 standalone components, signals / computed / effect, Router query params (`replaceUrl`, `queryParamsHandling: 'merge'`), Vitest (`npx ng test --watch=false --include='**/<file>.spec.ts'` from `frontend/`), SCSS.

**Spec:** `openspec/changes/add-ledger-calendar-view/` — `design.md` D1, D3, D5, D7, D8; `specs/frontend-accounting-ledger/spec.md` requirements "Calendar layout on wide screens" (+ ": month summary", ": day panel", ": entry drawer"), "Calendar items to handle", "Calendar top merchants", "Calendar entry drawer stepping", "Calendar grid keyboard", "Calendar selection survives opening an entry", and the MODIFIED "Empty pane shows the month"; `tasks.md` §3. PR A (merged `edec94a1`, PR #346) delivered §1–§2; the artboard is `design_handoff_v3/design_refs/CalendarDesktop.dc.html`.

## Global Constraints

- Calendar mode = `LayoutModeService.mode() === 'panes'` (≥ 1024 px) AND the layout's `listKey() === 'timeline'` AND the timeline's view is 日曆. It does NOT require `wideSidebar()` (1280): spec scenario "Calendar view at 1100 px".
- The 清單 view keeps today's behaviour exactly (two panes 5fr / 6fr, month overview only from 1280 px, document-level ↑ ↓, sheet at 760–1023). Every new branch is gated on calendar mode.
- Column ratio in calendar mode: `grid-template-columns: minmax(0, 2fr) minmax(0, 1fr)`; the shared 5fr / 6fr default is untouched (design Risks).
- Every `month` / `day` write from the right pane: `router.navigate([], { queryParams: calendarWrite(month, day), queryParamsHandling: 'merge', replaceUrl: true })` — through `writeCalendarQuery` only.
- Copy, verbatim: `本月摘要`, `今天`, `看報表 ›`, `支出類別`, `花最多的商家`, `商家排行讀取失敗`, `這個月要處理`, `沒有要處理的項目`, `這天沒有記錄`, `還沒到這天`, `＋ 在這天記一筆`, `搜尋整月：名稱、商家、備註`, `沒有符合的記錄`, drawer `aria-label="記錄詳情"`, close `aria-label="關閉"`, day ‹ › `aria-label="前一天"` / `"後一天"`. Day header: `{dayLabel(date)} · {n} 筆 · {formatSigned(net)}` (e.g. `10/21 週三 · 3 筆 · −$3,096`).
- Links carry the calendar: entry `/accounting/entries/{id}?month&day`; group `/accounting/entries/{memberId}/group?month&day`; new entry `/accounting/entry?date=YYYY-MM-DD&month&day`; reports `/accounting/reports?month=YYYY-MM`.
- Merchant ranking: top five rows with a non-null key, read only when the calendar is shown at ≥ 1024 px (`CalendarDataService.wide`).
- Drawer: covers the right pane (`position: absolute; inset: 0` inside `.detail-pane`), `role="dialog" aria-modal="true" data-global-shortcuts` (N keeps working, as in the 760–1023 sheet), raised shadow (`var(--app-raised-shadow)`), the entry form variant is `width: min(560px, 90vw)` anchored right. The day panel / 本月摘要 stays mounted under it with `inert`. `sheetOpen` / `sheetClosed` keep their 760–1023 meaning; the grid is never made inert by the drawer (D5). **Ruling** on 「Focus SHALL stay inside the drawer」: it is the keyboard contract — Tab / Shift+Tab cycle inside the drawer and Esc closes it; a pointer click on the grid (an explicit act) may move focus out, the drawer stays open on its entry and the base under it follows the new day. Esc with focus on the grid does nothing.
- Keyboard: ← → ↑ ↓ Home End Enter Space stay on the grid (PR A); `[` `]` step the day from the grid and from the day panel (host `(keydown)`), never from the document; with the drawer open ↑ ↓ belong to the drawer (`stepEntry`), and the layout's `moveSelection` stands down.
- Fixtures are fictional; no owner data in tests, reviews or commits. Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never run `npm run config` / `set-env.js` / `npm run build` / `npm start` in the worktree; `npx ng build --configuration development`, `npx tsc -p tsconfig.app.json --noEmit` and `npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict` are the checks.

## Review Focus

1. Month navigator ‹ › while the drawer is open: `day` is dropped (spec) but the drawer must stay open on its entry, and the base under it falls back to 本月摘要 of the new month — pinned in Task 7.
2. Search typed with a day selected: the day panel wins while `day` is set; clearing the day returns to the result list, and the search text survives a day pick — pinned in Task 9.
3. An invoice row whose single candidate is a split group, but the day's rows no longer hold a member of that group (dissolved / moved): the day is selected and nothing else navigates, no error — pinned in Task 8.
4. ↓ in the drawer on the month's last entry (and ↑ on the first): no navigation, the URL is unchanged — pinned in Task 8.
5. 今天 card and 回到今天-style shortcuts belong to the current month only: in another month the card is absent and nothing selects today — pinned in Task 4.

---

## File map

| File | Responsibility |
|---|---|
| `frontend/src/app/components/accounting/timeline/calendar-url.ts` (+spec) | `writeCalendarQuery(router, month, day)` — the one URL writer |
| `frontend/src/app/components/accounting/calendar-month/calendar-pane.service.ts` (+spec, new) | `CalendarPaneService`: what the timeline publishes for the right pane, and the commands it hands over |
| `frontend/src/app/components/accounting/month-overview/overview.service.ts` | `calendar` boolean (spec D5) |
| `frontend/src/app/components/accounting/timeline/timeline.{ts,html,scss,spec.ts}` | publishes the pane state; `time` on rows; `stepEntry` / `openInvoice` / `setQuery`; month search; monthbar figures; hides `.sum` + `.day-entries` in calendar mode at panes; binds `(dayStep)` |
| `frontend/src/app/components/accounting/calendar-month/calendar-data.service.ts` (+spec) | keeps the month's `invoices`; merchants read gated on `wide` |
| `frontend/src/app/components/accounting/calendar-month/calendar-month.{ts,spec.ts}` | `[` `]` → `dayStep` output |
| `frontend/src/app/components/accounting/month-overview/month-overview.{ts,html,scss,spec.ts}` | calendar mode: 今天 card, 看報表 ›, 花最多的商家, 這個月要處理 |
| `frontend/src/app/components/accounting/calendar-day-panel/calendar-day-panel.{ts,html,scss,spec.ts}` (new) | day panel and search-result list |
| `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}` | calendar mode, lifted search box, right-pane base, drawer, focus, FAB offset |
| `frontend/src/app/components/accounting/split-group/{group-nav.ts,split-group.ts}` (+specs) | exits keep `month`/`day` |
| `frontend/src/app/components/shell/shell.ts`, `frontend/src/app/components/mobile-nav/mobile-nav.{ts,html}`, `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (+specs) | global ＋ / N, 複製, 編輯整個排程 carry `month`/`day` |
| `openspec/changes/add-ledger-calendar-view/tasks.md` | §3 ticks |

---

### Task 1: Shared URL writer, `CalendarPaneService`, timeline publishing

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/calendar-url.ts`, `calendar-url.spec.ts`
- Create: `frontend/src/app/components/accounting/calendar-month/calendar-pane.service.ts`, `calendar-pane.service.spec.ts`
- Modify: `frontend/src/app/components/accounting/month-overview/overview.service.ts`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (rows `time`, `DayNet` export, publishing effects, `writeCalendar` → helper), `timeline.spec.ts`

**Interfaces:**
- Consumes: PR A `calendarWrite(month, day)`, `readCalendarQuery`, `TimelineRow`, `TimelineDay`, `BillLine`, `OverviewState`, `CalendarPartState`, `BreakdownRow`, `InboxEinvoice`.
- Produces:
  ```ts
  // calendar-url.ts
  export function writeCalendarQuery(router: Router, month: string, day: string | null): Promise<boolean>;
  // timeline.ts
  export interface DayNet { net: number; text: string; excluded: boolean }
  export interface TimelineRow { …; /** `HH:MM` from `entry_time`, null without one. */ time: string | null }
  // calendar-pane.service.ts
  export interface TodayCard { date: string; count: number; names: string; netText: string; net: number }
  export interface MonthDue { key: string; accountId: number; name: string; date: string; amountText: string }
  export interface CalendarCommands { stepEntry(delta: 1 | -1): void; openInvoice(invoice: InboxEinvoice): void; setQuery(text: string): void }
  @Injectable({ providedIn: 'root' })
  export class CalendarPaneService {
    readonly month = signal(currentMonth());
    readonly today = signal(todayIso());
    readonly selectedDay = signal<string | null>(null);
    readonly dayRows = signal<TimelineRow[]>([]);
    readonly dayNet = signal<DayNet | null>(null);
    readonly dayState = signal<OverviewState>('ready');
    readonly billLines = signal<BillLine[]>([]);
    readonly todayCard = signal<TodayCard | null>(null);
    readonly merchants = signal<BreakdownRow[]>([]);
    readonly merchantsState = signal<CalendarPartState>('loading');
    readonly invoices = signal<InboxEinvoice[]>([]);
    readonly monthDues = signal<MonthDue[]>([]);
    /** Mirror of the timeline's `query` (the filter bar's text); the right pane changes it through `commands.setQuery`. */
    readonly search = signal('');
    readonly searchDays = signal<TimelineDay[]>([]);
    readonly searchState = signal<OverviewState>('ready');
    readonly commands = signal<CalendarCommands | null>(null);
    clear(): void; // every signal back to its initial value above
  }
  // overview.service.ts
  readonly calendar = signal(false); // the timeline is in 日曆 view (any width); clear() leaves it alone like `visible`
  ```

- [ ] **Step 1: Failing tests — `writeCalendarQuery`**

```ts
// calendar-url.spec.ts
it('writeCalendarQuery merges month and day into the URL and replaces history', async () => {
  const navigate = vi.fn().mockResolvedValue(true);
  await writeCalendarQuery({ navigate } as unknown as Router, '2026-10', '2026-10-21');
  expect(navigate).toHaveBeenCalledWith([], { queryParams: { month: '2026-10', day: '2026-10-21' }, queryParamsHandling: 'merge', replaceUrl: true });
});
it('writeCalendarQuery writes day: null to drop the day', async () => { /* expect queryParams { month: '2026-10', day: null } */ });
```

- [ ] **Step 2: Failing tests — `CalendarPaneService.clear()` resets every field** (one test: set each signal to a non-default value, call `clear()`, assert all defaults; `commands` back to null).

- [ ] **Step 3: Failing tests — timeline publishing** (in `timeline.spec.ts`, the calendar describe; inject `CalendarPaneService` and `AccountingOverviewService`):

```ts
it('publishes the 日曆 view and the month to the pane services', () => { /* render panes, click 日曆 → overview.calendar() true, pane.month() '2026-10', pane.today() set; click 清單 → calendar() false */ });
it('[ and ] on the grid step the day in the URL (through dayStep → stepDay)', () => { /* select 10/21, dispatch keydown ']' on the grid → URL day 2026-10-22; '[' → 2026-10-20 — binding added in Task 5, test belongs here with the other URL tests */ });
it('publishes the selected day, its rows with time, net and bill lines', () => { /* select 10/21, flush entries with entry_time '12:30' → pane.selectedDay() '2026-10-21', pane.dayRows()[0].time '12:30', pane.dayNet()!.text, pane.dayState() 'ready'; a failed day read → 'error' */ });
it('publishes the month dues', () => { /* a due with remaining > 0 → pane.monthDues() one item with amountText; a paid one → none */ });
it('adds near-term dues from next month in the current month only', () => { /* today 2026-10-28, a card debiting 2026-11-03 with remaining > 0: month 2026-10 → monthDues() contains it (date '2026-11-03'); moveMonth(-1) (2026-09) → not present; a bill debiting 2026-11-10 → never (outside today + 6) */ });
it('publishes a 今天 card for the current month only', () => { /* month = today's month: a GET /entries?date_from=today&date_to=today&limit=3 is answered → todayCard {count from daily, names 'A、B', netText}; moveMonth(-1) → todayCard null and no request */ });
it('clears the pane services when destroyed', () => { /* fixture.destroy() → pane.selectedDay() null, dayRows [], overview.calendar() false */ });
```

- [ ] **Step 4: Run them to verify they fail** — `cd frontend && npx ng test --watch=false --include='**/calendar-url.spec.ts' --include='**/calendar-pane.service.spec.ts' --include='**/timeline.spec.ts'`. Expected: FAIL (`writeCalendarQuery` not exported, `CalendarPaneService` missing, `time` undefined).

- [ ] **Step 5: Implement**
  - `writeCalendarQuery` in `calendar-url.ts`; `timeline.writeCalendar()` calls it and keeps its immediate `month`/`urlDay` signal sets.
  - `TimelineRow.time` set in `plainRow` from `entry.entry_time?.slice(0, 5) ?? null`; group / transfer / batch rows copy the first member's `time`.
  - Export `DayNet`; `dayNet` computed returns it.
  - `CalendarPaneService` as specified; `AccountingOverviewService.calendar`. `pane.commands` is registered in the constructor with `{ stepEntry, openInvoice, setQuery }` — `setQuery(text)` = `this.query.set(text.trim())` followed by the same reload `onQueryInput`'s debounce ends in (Tasks 8 / 6 / 9 fill the bodies of the first two; this task ships `setQuery` and stubs the others as no-ops with a `// Task 8` / `// Task 6` comment).
  - Timeline constructor effects: `overview.calendar.set(view() === 'calendar')`; `pane.month/today/selectedDay/dayRows/dayNet/dayState/billLines/merchants/merchantsState/invoices/search` from the existing signals and `calendarData.data()` (`search` mirrors `query()`); `monthDues` = `monthBilling()` due events with `openBill()`, plus — when `month() === today().slice(0, 7)` — the `upcomingBills()` events whose `date` is after the month's last day (deduped by `billKey`, sorted by `date`), each → `{ key: billKey(event), accountId, name, date, amountText: formatMoney(bill.statement, bill.currency) }` (spec scenario 「Bill due early next month at month end」); `todayCard`: a `todayEntries` signal filled by `loadToday()` (`getAllEntries({ limit: 3, offset: 0, date_from: today, date_to: today, hide_rewards })`, request-id guarded) when `view() === 'calendar' && month() === today().slice(0, 7)`, re-run on `entriesChanged`; names = row titles joined with `、`; count/net from `daily()` for today (`formatSigned`); null in another month. `onDestroy`: `pane.clear()`, `overview.calendar.set(false)`. `dayState` = dayError ? 'error' : dayLoading && dayEntries empty ? 'loading' : 'ready'.

- [ ] **Step 6: Run the three spec files — PASS; then `npx tsc -p tsconfig.app.json --noEmit`.**

- [ ] **Step 7: Commit** — `feat(spa): calendar pane service and shared calendar URL writer (#318 PR B)`

---

### Task 2: Layout calendar mode — columns, monthbar figures, day rows leave the list pane

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.{ts,html,scss}` (monthbar figures; hide `.sum` and `.day-entries` in calendar mode at panes), `timeline.spec.ts`

**Interfaces:**
- Consumes: `AccountingOverviewService.calendar`, `.summary`, `.summaryState`; `LayoutModeService.mode`.
- Produces (layout):
  ```ts
  readonly calendarMode = computed(() => this.mode() === 'panes' && this.listKey() === 'timeline' && this.overview.calendar());
  export const FAB_RIGHT_BESIDE_CALENDAR_PANE = 'calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) / 3 + 32px)';
  ```
  Template (layout): `.dbody.dbody--calendar` when `calendarMode()`. Template (timeline `monthbar`, per D5 / artboard): when `view() === 'calendar' && layoutMode.mode() === 'panes'`, a `<dl class="monthbar-figures" aria-label="本月摘要">` rendered **between `.month-next` (after the bell) and `.view-toggle`**, with `支出` / `收入` / `結餘` as `<dt>` + `<dd class="neg sum-expense | pos sum-income | sum-net">` using the existing `expenseText` / `incomeText` / `formatSigned` (`—` while `summary()` is null; `摘要讀取失敗` + the retry button when `summaryError()`). The `.sum` card is not rendered in that state; the `.day-entries` block under the grid is not rendered either (the day lives in the right pane). The page header is untouched.

- [ ] **Step 1: Failing layout tests** (helper: `localStorage.setItem('hh.accounting.timelineView', 'calendar')` before `start(...)` puts the timeline in 日曆 without a `day`; `afterEach` clears it):

```ts
describe('calendar mode', () => {
  it('uses the 2:1 columns and shows the figures in the monthbar between › and the view toggle at 1100px in 日曆', async () => {
    localStorage.setItem('hh.accounting.timelineView', 'calendar');
    const harness = await start('panes', '/accounting?month=2026-10', 1100);
    await find(harness, '.dbody--calendar');
    const bar = await find(harness, '.monthbar');
    const order = Array.from(bar.children).map(el => el.className.split(' ')[0]);
    expect(order.indexOf('monthbar-figures')).toBeGreaterThan(order.indexOf('month-next'));
    expect(order.indexOf('monthbar-figures')).toBeLessThan(order.indexOf('view-toggle'));
    expect(bar.querySelector('.monthbar-figures')!.textContent).toContain('結餘');
    expect(harness.routeNativeElement!.querySelector('.list-pane .sum')).toBeNull();
  });
  it('renders no day rows under the grid in 日曆 at 1100px with a day selected', async () => { /* storage calendar; start panes /accounting?month=2026-10&day=2026-10-21 1100 → .list-pane .day-entries null, .list-pane [data-entry-id] none */ });
  it('keeps the 清單 layout: no calendar columns, no monthbar figures, month card in the list', async () => { /* localStorage hh.accounting.timelineView = 'list' (panes default to 日曆 when nothing is stored); start panes /accounting 1440 → .dbody--calendar null, .monthbar-figures null, .list-pane .sum present */ });
  it('publishes the calendar FAB offset while a pane is open in calendar mode', async () => { /* storage calendar; start panes /accounting/entries/5?month=2026-10 1100 → PaneStateService.fabRight() === FAB_RIGHT_BESIDE_CALENDAR_PANE */ });
});
```

- [ ] **Step 2: Run — FAIL** (`.dbody--calendar` never appears).

- [ ] **Step 3: Implement** — layout: `calendarMode`, the template class, `.dbody--calendar { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) }`, `paneState.fabRight.set(beside ? (this.calendarMode() ? FAB_RIGHT_BESIDE_CALENDAR_PANE : FAB_RIGHT_BESIDE_PANE) : null)`. Timeline: `readonly wideCalendar = computed(() => this.view() === 'calendar' && this.layoutMode.mode() === 'panes')`; template `@if (wideCalendar()) { <dl class="monthbar-figures">… }` in the monthbar, `@if (!wideCalendar()) { .sum }`, `@if (selectedDay() && !wideCalendar()) { .day-entries }`; `.monthbar-figures` styles (inline `dt`/`dd` pairs, `--fs-sm`, gap 8px 20px, `margin-inline: auto 8px`). The 近 7 天 banner and the filter bar stay.

- [ ] **Step 4: Run layout + timeline specs — PASS** (update any timeline test that asserted `.sum` or `.day-entries` in calendar + panes; note each in the report). Add to `timeline.spec.ts`: `it('renders the monthbar figures and no .sum / .day-entries in 日曆 at panes', …)` and `it('keeps .sum and .day-entries in 日曆 at phone width', …)`.

- [ ] **Step 5: Commit** — `feat(spa): accounting layout calendar mode — 2:1 columns, monthbar figures, day rows move right (#318 D5)`

---

### Task 3: `CalendarDataService` keeps the month's invoices; merchants only when wide

**Files:**
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-data.service.ts`, `calendar-data.service.spec.ts`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (sets `calendarData.wide`)

**Interfaces:**
- Produces:
  ```ts
  export interface CalendarMonthData { …; /** The month's inbox e-invoices awaiting a match, issue-date order. */ invoices: InboxEinvoice[] }
  readonly wide = signal(false); // ≥ 1024 px: the merchant ranking is read; below, merchantsState stays 'loading' and no request is made
  ```
  `EMPTY_CALENDAR_DATA.invoices = []`. `readInvoices` fills `invoices` (filtered to the month, sorted by `issue_date`) beside `invoiceDays`. `readMerchants` runs only when `wide()`; when `wide` turns true for a cached month whose `merchantsState` is `'loading'` and has no in-flight read, it starts the read (same pattern as the inbox fall-through).

- [ ] **Step 1: Failing tests**

```ts
it("keeps the month's invoices beside the invoice days", () => { /* two invoices in month, one outside → data().invoices.length 2, sorted by issue_date, invoiceDays matches */ });
it('does not read merchants until wide is set, then reads them once', () => { /* month set, wide false → no /reports/breakdown?group_by=merchant request (expectNone), merchantsState 'loading'; wide.set(true) → one request → 'ready' */ });
it('a month cached while narrow gets its merchants when wide turns on', () => { /* same as above after a month switch and back */ });
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement** (timeline: `effect(() => this.calendarData.wide.set(this.layoutMode.mode() === 'panes'))`).  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): CalendarDataService keeps month invoices; merchant ranking only at ≥ 1024 px (#318 D3)`

---

### Task 4: 本月摘要 — `MonthOverviewComponent` calendar mode

**Files:**
- Modify: `frontend/src/app/components/accounting/month-overview/month-overview.{ts,html,scss,spec.ts}`

**Interfaces:**
- Consumes: `TodayCard`, `MonthDue`, `InboxEinvoice`, `BreakdownRow`, `CalendarPartState`, `formatMoney`, `slashDate`.
- Produces (new inputs / outputs; the existing inputs and the ≥ 1280 清單 rendering are unchanged when `calendar` is false):
  ```ts
  readonly calendar = input(false);
  readonly today = input<TodayCard | null>(null);
  readonly merchants = input<BreakdownRow[]>([]);
  readonly merchantsState = input<CalendarPartState>('loading');
  readonly invoices = input<InboxEinvoice[]>([]);
  readonly dues = input<MonthDue[]>([]);
  readonly pickDay = output<string>();           // 今天 card, a due row
  readonly openInvoice = output<InboxEinvoice>(); // an invoice row (the layout selects the day and resolves the target)
  ```
  The search box is **not** in this component — the layout renders it above the right-pane base (Task 6), so it stays mounted and focused when the base switches from 本月摘要 to the result list. Template in calendar mode, in order: `@if (today())` → `<button class="ov-today">` with date disc, `今天 · {slashDate(date)} {weekday}`, `{count} 筆`, names, net; `<h3 class="ov-title">支出類別</h3>` + existing distribution + `<a class="ov-reports" [routerLink]="'/accounting/reports'" [queryParams]="{ month: month() }">看報表 ›</a>`; `<h3 class="ov-title">花最多的商家</h3>` + `@switch (merchantsState())`: loading `讀取中`, error `<p class="ov-empty ov-merchants-error">商家排行讀取失敗</p>`, ready → `<ol class="ov-merchants">` rows `rank · name · {count} 筆 · formatMoney(|amount|)` or `本月尚無商家支出`; `<h3 class="ov-title">這個月要處理</h3>` + `<ul class="ov-todo">`: invoice rows `<button class="todo-invoice">發票待對帳 · {slashDate(issue_date)} {seller_name} {formatMoney(total_amount)}</button>` then due rows `<button class="todo-bill">{name}帳單 {amountText} · {slashDate(date)} 到期</button>` (invoices then dues, each by date; a due outside the shown month renders the same and `pickDay` emits its full date, so the layout writes the next month), or `沒有要處理的項目`. 近期到期 is NOT rendered in calendar mode.

- [ ] **Step 1: Failing tests** (`month-overview.spec.ts`, new describe `calendar mode`):

```ts
it('renders search, 今天, distribution with 看報表, merchants and 這個月要處理 in that order', () => { /* assert the sequence of .ov-today, h3 texts ['支出類別','花最多的商家','這個月要處理'], a[href*="/accounting/reports?month=2026-10"], no 近期到期 */ });
it('shows no 今天 card without one (another month)', () => { /* today null → .ov-today absent */ });
it("emits pickDay with today's date from the 今天 card and with the due date from a bill row", () => {});
it('emits openInvoice for an invoice row and lists invoices before dues', () => {});
it('replaces the merchant card with 商家排行讀取失敗 on error and lists five rows with count and amount when ready', () => {});
it('keeps the 清單 overview unchanged when calendar is false', () => { /* existing sections only; no .ov-today / .ov-merchants / .ov-todo */ });
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement** (import `RouterLink`).  - [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** — `feat(spa): 本月摘要 calendar mode — search, 今天, 看報表, 花最多的商家, 這個月要處理 (#318 D5)`

---

### Task 5: `CalendarDayPanelComponent` (day and search-result modes) + `[` `]` on the grid

**Files:**
- Create: `frontend/src/app/components/accounting/calendar-day-panel/calendar-day-panel.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-month.{ts,spec.ts}` (`dayStep` output for `[` `]`)

**Interfaces:**
- Consumes: `TimelineRow` (with `time`), `TimelineDay`, `BillLine`, `DayNet`, `OverviewState`, `dayLabel`, `formatSigned`, `SplitFolderComponent` (`app-split-folder`, as the timeline uses it).
- Produces:
  ```ts
  @Component({ selector: 'app-calendar-day-panel', host: { '(keydown)': 'onKeydown($event)' } })
  export class CalendarDayPanelComponent {
    readonly mode = input<'day' | 'results'>('day');
    readonly day = input<string | null>(null);           // day mode
    readonly today = input.required<string>();
    readonly rows = input<TimelineRow[]>([]);              // day mode
    readonly net = input<DayNet | null>(null);
    readonly state = input<OverviewState>('ready');
    readonly billLines = input<BillLine[]>([]);
    readonly days = input<TimelineDay[]>([]);              // results mode
    readonly query = input('');                            // results mode header
    readonly selectedId = input<number | null>(null);      // marks the open entry's row (aria-current)
    readonly step = output<1 | -1>();                      // ‹ › and [ ]
    readonly clear = output<void>();                       // 本月摘要 button / results ✕
    readonly open = output<TimelineRow>();
    readonly add = output<string>();                       // ＋ 在這天記一筆 with the date
    readonly openCard = output<number>();                  // bill line → account id
  }
  ```
  Day mode markup: `<header class="dp-head">` ‹ (`aria-label="前一天"`) `<h2 class="dp-label">{dayLabel(day)}</h2><p class="dp-sub">{n} 筆 · {net.text}</p>` › (`aria-label="後一天"`) `<button class="dp-summary">本月摘要</button>`; bill lines as `<button class="bill">` (same text as the timeline's); rows `<button class="dp-row" [attr.data-entry-id] [attr.data-member-ids] [attr.data-split] [attr.aria-current]>` with `.ico` / `app-split-folder`, `.name`, `.sub` = `[time, row.sub].filter(Boolean).join(' · ')`, `.amt`, `.pills`; states: `state === 'loading'` → `<app-skeleton rows="3">`, `'error'` → `記錄讀取失敗`; empty → `<p class="dp-empty">{day > today ? '還沒到這天' : '這天沒有記錄'}</p><button class="dp-add">＋ 在這天記一筆</button>` (the add button is also shown under a non-empty list). Results mode: `<header class="dp-head"><h2 class="dp-label">「{query}」</h2><button class="dp-summary" aria-label="清除搜尋">✕</button></header>`, then for each `TimelineDay`: `<div class="dp-day">{label} <b>{formatSigned(net)}</b></div>` + rows; empty → `沒有符合的記錄`. Rows render batches unfolded (the panel receives rows built with `foldBatches = false`; see Task 6). `onKeydown`: `[` → `step.emit(-1)`, `]` → `step.emit(1)` (day mode, no modifiers, target not an input), `preventDefault`.
  - `CalendarMonthComponent`: `readonly dayStep = output<1 | -1>()`; in `onGridKeydown`, `[` / `]` emit it (cancelled) before the arrow switch. `timeline.html` binds `(dayStep)="stepDay($event)"` on `<app-calendar-month>` (the existing `stepDay` already crosses month edges) — the URL test for it is listed in Task 1's timeline spec block; run it here.

- [ ] **Step 1: Failing tests** (`calendar-day-panel.spec.ts`; `calendar-month.spec.ts` one test `[ and ] emit dayStep`):

```ts
it('shows the day header with count and net, the bill line and rows with time', () => { /* 10/21, 3 rows, time '12:30' → .dp-label '10/21 週三', .dp-sub '3 筆 · −$3,096', .dp-row .sub starts '12:30 · ' */ });
it('shows 這天沒有記錄 and ＋ 在這天記一筆 for a past day, 還沒到這天 for a future one, and emits add with the date', () => {});
it('emits step from ‹ › and from [ ] keys, clear from 本月摘要, open from a row, openCard from a bill line', () => {});
it("marks the open entry's row aria-current and keeps data-entry-id / data-member-ids / data-split", () => {});
it('shows skeleton while loading and 記錄讀取失敗 on error', () => {});
it('results mode lists day groups with labels and nets and 沒有符合的記錄 when empty', () => {});
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement** (styles: borrow the timeline's `.entry-row` tokens — 58px min height, `--app-border` separators, `.neg`/`.pos` tones; keep the file self-contained).  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): CalendarDayPanelComponent — day panel and search results; [ ] step the day (#318 D5/D8)`

---

### Task 6: Right-pane base composition in the layout

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (`openInvoice` command, `foldBatches` false for `dayRows`), `timeline.spec.ts`

**Interfaces:**
- Consumes: Task 1 service, Task 4 inputs/outputs, Task 5 component, `writeCalendarQuery`, `calendarParams`, `groupCommands`, `listOpenState`, `addDays`.
- Produces (layout):
  ```ts
  protected readonly pane = inject(CalendarPaneService);
  /** Calendar mode's right-pane base: 'summary' | 'day' | 'results' (day wins over results; results need a search text). */
  readonly calendarBase = computed<'summary' | 'day' | 'results' | null>(() =>
    !this.calendarMode() ? null : this.pane.selectedDay() ? 'day' : this.pane.search() ? 'results' : 'summary');
  onPickDay(date: string): void      // writeCalendarQuery(router, date.slice(0, 7), date) — a next-month due row moves the month too
  onStepDay(delta: 1 | -1): void     // next = addDays(selectedDay, delta); writeCalendarQuery(router, next.slice(0, 7), next) — crosses month edges like timeline.stepDay
  onClearDay(): void                 // writeCalendarQuery(router, pane.month(), null)
  onOpenRow(row: TimelineRow): void  // split → groupCommands(row.entryId, null) with state = listOpenState(router.url, '/accounting') like timeline.open; else ['/accounting/entries', row.entryId]; both with { queryParams: this.calendarQuery() }
  onAddEntry(date: string): void     // ['/accounting/entry'], { queryParams: { date, ...this.calendarQuery() } }
  onSearch(text: string): void       // pane.commands()?.setQuery(text) — one search shared with the filter bar
  onSearchInput(event: Event): void  // the lifted box: debounce 300 ms (QUERY_DEBOUNCE_MS from the timeline module) → onSearch; Enter commits at once; Esc empties the box and calls onSearch('')
  onOpenInvoice(invoice: InboxEinvoice): void // pane.commands()?.openInvoice(invoice)
  openCard(accountId: number): void  // router.navigate(['/accounting/accounts', accountId]) — same target as the timeline's openCard
  ```
  Template (inside `.detail-pane`, before the outlet wrapper): `@if (calendarBase(); as base) { <div class="cal-base" #calendarBase> @if (base !== 'day') { <input type="search" class="cal-search" [value]="pane.search()" placeholder="搜尋整月：名稱、商家、備註" aria-label="搜尋名稱、商家或備註" (input)="onSearchInput($event)" (keydown)="onSearchInput($event)" /> } @switch (base) { @case ('summary') { <app-month-overview [calendar]="true" [month]="overview.month()" [summary]="overview.summary()" [summaryState]="overview.summaryState()" [today]="pane.todayCard()" [merchants]="pane.merchants()" [merchantsState]="pane.merchantsState()" [invoices]="pane.invoices()" [dues]="pane.monthDues()" (pickDay)="onPickDay($event)" (openInvoice)="onOpenInvoice($event)" /> } @case ('day') { <app-calendar-day-panel mode="day" [day]="pane.selectedDay()" [today]="pane.today()" [rows]="pane.dayRows()" [net]="pane.dayNet()" [state]="pane.dayState()" [billLines]="pane.billLines()" [selectedId]="selectedEntryId()" (step)="onStepDay($event)" (clear)="onClearDay()" (open)="onOpenRow($event)" (add)="onAddEntry($event)" (openCard)="openCard($event)" /> } @case ('results') { <app-calendar-day-panel mode="results" [today]="pane.today()" [days]="pane.searchDays()" [state]="pane.searchState()" [query]="pane.search()" [selectedId]="selectedEntryId()" (clear)="onSearch('')" (open)="onOpenRow($event)" /> } } </div> }`. The `.cal-search` box is rendered once, above the `@switch`, for `summary` and `results` (not `day`): typing flips the base to results without unmounting the input, so focus stays in it. The existing `showOverview` / `.pane-empty` branches are skipped when `calendarMode()` (`showOverview` gains `&& !this.overview.calendar()`); `overview.visible` stays owned by the layout's existing effect and is not touched. `.cal-base` has **no** `inert` binding in the template: Task 7's overlay effect is the only writer (it must capture `activeElement` first).
  - Timeline: `dayGroup` builds with `foldBatches = false` so the panel gets one row per entry (**ruling**: no batch folding in the day panel; the list keeps folding); `commands.openInvoice(invoice)`: writes `day = invoice.issue_date`, remembers `pendingInvoice = invoice`; when `dayEntries` for that day arrive, with exactly one candidate: `kind === 'entry'` → navigate `['/accounting/entries', id]`; `kind === 'group'` → the first entry whose `group?.id === id` → `groupCommands(entry.id, null)`; none found or ≠ 1 candidate → only the day stays selected. Both navigations carry `{ queryParams: calendarParams(snapshot) }`.

- [ ] **Step 1: Failing layout tests** (calendar describe; URLs with `?month=2026-10&day=…` select a day; storage key for no-day cases):

```ts
it('shows 本月摘要 in calendar mode at 1100px with no day', async () => { /* .cal-base .cal-search and app-month-overview present; .pane-empty absent */ });
it('shows the day panel when the URL carries a day', async () => { /* /accounting?month=2026-10&day=2026-10-21 → app-calendar-day-panel .dp-label '10/21 週三' */ });
it('‹ › and 本月摘要 write the URL with replaceUrl', async () => { /* click .dp-head [aria-label=後一天] → url ?month=2026-10&day=2026-10-22 ; click .dp-summary → ?month=2026-10 */ });
it('› on the last day of the month moves to the next month', async () => { /* day=2026-10-31 → click 後一天 → url ?month=2026-11&day=2026-11-01 */ });
it('a next-month due row in 這個月要處理 writes the next month and its day', async () => { /* today 2026-10-28 (fake today in the timeline), seed pane.monthDues with { date: '2026-11-03' } via the service at month 2026-10 → click .todo-bill → url ?month=2026-11&day=2026-11-03 */ });
it('shows exactly one 本月摘要 and no 近期到期 at 1440px in 日曆 (showOverview stands down)', async () => { /* storage calendar; start panes /accounting?month=2026-10 1440 → querySelectorAll('app-month-overview').length 1, .cal-search present, no text 近期到期 */ });
it('＋ 在這天記一筆 opens the form with date, month and day', async () => { /* url /accounting/entry?date=2026-10-21&month=2026-10&day=2026-10-21 */ });
it('a day-panel row opens the entry with month and day kept', async () => { /* set pane.dayRows via the service → click → /accounting/entries/7?month=2026-10&day=2026-10-21 */ });
```
and in `timeline.spec.ts`: `openInvoice selects the invoice day and opens its single entry candidate`, `…opens the group of a split candidate through a member row`, `…only selects the day when the group has no member row on that day (Review Focus 3)`.

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): calendar right pane — 本月摘要, day panel and results wired to the URL (#318 D5)`

---

### Task 7: Entry drawer over the right pane

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}`

**Interfaces:**
- Produces (layout):
  ```ts
  readonly drawerOpen = computed(() => this.calendarMode() && this.paneOpen());
  private readonly overlayOpen = computed(() => this.sheetOpen() || this.drawerOpen());
  private readonly drawer = viewChild<ElementRef<HTMLElement>>('drawer');
  onDrawerKeydown(event: KeyboardEvent): void  // Esc → close() (marked handled, not for IME / already-handled); Tab → trapFocus(drawer); ↑ ↓ → pane.commands()?.stepEntry(±1) when the target is not an input/textarea/select
  ```
  Template: wrap `<router-outlet name="pane" />` in `<div #drawer class="pane-host" data-global-shortcuts [class.drawer]="drawerOpen()" [class.drawer--form]="drawerOpen() && formOpen()" [attr.role]="drawerOpen() ? 'dialog' : null" [attr.aria-modal]="drawerOpen() ? 'true' : null" [attr.aria-label]="drawerOpen() ? '記錄詳情' : null" (keydown)="onDrawerKeydown($event)">` (`data-global-shortcuts` keeps the shell's N working, as on the sheet); inside, when `drawerOpen() && !formOpen()`, `<button class="drawer-close" aria-label="關閉" (click)="close()">✕</button>`. Styles: `.dbody--calendar .detail-pane { overflow: visible; padding: 0 }`, `.cal-base { height: 100%; overflow-y: auto; padding: var(--pane-pad-top) 14px 14px }`, `.drawer { position: absolute; inset: 0; z-index: 3; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: var(--radius-lg); box-shadow: var(--app-raised-shadow); overflow-y: auto; padding: var(--pane-pad-top) 14px 14px }`, `.drawer--form { left: auto; width: min(560px, 90vw) }`, `.drawer-close` like `.sheet-close`. Focus: the `sheetOpen` effect becomes an `overlayOpen` effect — on open it **first** remembers `document.activeElement` (`focusBeforeOverlay`, renamed from `focusBeforeSheet`), **then** sets `inert` on `.cal-base` imperatively (`calendarBase()?.nativeElement.toggleAttribute('inert', true)`; no template binding, so the day-panel row that opened the drawer is captured before the browser moves focus to `body`), then focuses the drawer's ✕ (or `[data-pane-close]`); on close it removes `inert` from `.cal-base` and `restoreFocus()` focuses `focusBeforeOverlay` when it is still connected, else the selected grid cell (`.list-pane .cell.sel`) in calendar mode, else the existing `.sel, [aria-current]` fallback (lead ruling: a grid click that changed `day` under the drawer removes the opener row; Esc then goes to `/accounting?month&day=<new day>` and focus lands on that cell, never `body`). The sheet keeps `setOutsideInert(open, list, header)`; the drawer never inerts the list pane, header or sidebar. `moveSelection` returns early when `drawerOpen()` (the drawer's own ↑ ↓ handle it). `onEscape` (document) still ignores the drawer: Esc inside the drawer is handled by `onDrawerKeydown`; Esc with focus outside the drawer (on the grid) does nothing.

- [ ] **Step 1: Failing layout tests**

```ts
it('renders the entry as a drawer over the day panel at 1100px, day panel still mounted and inert', async () => { /* /accounting/entries/5?month=2026-10&day=2026-10-21 → .pane-host.drawer[role=dialog][aria-label=記錄詳情]; app-calendar-day-panel present inside .cal-base[inert]; .sheet-grip--active absent; .list-pane has no inert */ });
it('Esc after a grid click changed the day goes to the new day and focuses the selected grid cell', async () => { /* open the drawer from a .dp-row on 10/21, click the 10/23 cell (URL day 2026-10-23, drawer still open, opener row gone); Esc on the drawer → url /accounting?month=2026-10&day=2026-10-23, document.activeElement === .list-pane .cell.sel (not body) */ });
it('✕ and Esc close the drawer back to the day and return focus to the day-panel row that opened it', async () => { /* seed pane.dayRows via the service, click the .dp-row (focus lands on it) → drawer opens, .cal-base has inert; Esc dispatched on the drawer → url /accounting?month=2026-10&day=2026-10-21, .cal-base no inert, document.activeElement === that .dp-row */ });
it('keeps N working inside the drawer (data-global-shortcuts) and does not trap a pointer click on the grid', async () => { /* .pane-host.drawer[data-global-shortcuts]; document.querySelector(OPEN_MODAL selector '[aria-modal="true"]:not([data-global-shortcuts])') null; clicking a grid cell with the drawer open writes ?day and the drawer stays */ });
it('wraps Tab inside the drawer', async () => {});
it('widens the drawer for the entry form', async () => { /* /accounting/entry?month=2026-10&day=2026-10-21 → .drawer--form present, no .drawer-close (the form owns ✕) */ });
it('↑ ↓ inside the drawer call stepEntry and the document-level row stepping stands down', async () => { /* commands fake with vi.fn(); press ArrowDown on the drawer → stepEntry(1), press on document body → no navigation */ });
it('keeps the 820px sheet behaviour untouched in 日曆', async () => { /* start sheet …?month&day → .detail-pane.open, .sheet-grip--active, no .drawer */ });
it('the month navigator ‹ › keeps the drawer open and drops the day (Review Focus 1)', async () => { /* click .month-prev → url /accounting/entries/5?month=2026-09, .drawer still present, base switches to app-month-overview */ });
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run the whole layout spec — PASS** (the pre-existing flaky 1280px row test may time out; report, do not fix).

- [ ] **Step 5: Commit** — `feat(spa): entry drawer over the calendar's right pane — focus trap, Esc, focus return (#318 D5/D8)`

---

### Task 8: Drawer stepping through the month (`stepEntry`)

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts` (one integration test)

**Interfaces:**
- Produces (timeline, registered in `pane.commands` at construction, cleared on destroy):
  ```ts
  /**
   * ↓ / ↑ with the drawer open: the next / previous entry of the month in list order (newest first). Inside the selected
   * day: the neighbouring row. At the day's edge: the nearest earlier (↓) / later (↑) day whose daily-summary count > 0;
   * that day is written to the URL and, once its rows arrive, its first (↓) / last (↑) row opens. At the month's edge: nothing.
   */
  stepEntry(delta: 1 | -1): void;
  ```
  Implementation: `pendingOpen: 'first' | 'last' | null` consumed in `loadDay`'s `next` (same request id) → `open(row)`. Index lookup uses `dayRows()` and `layout.selectedEntryId()` (a group row matches on `memberIds`). Days come from `daily()?.days` sorted descending; a day with `count === 0` is skipped. Rows open through the existing `open(row)` (group rows → group view), so `month`/`day` travel.

- [ ] **Step 1: Failing tests** (`timeline.spec.ts`, calendar describe, panes):

```ts
it('stepEntry(1) opens the next row of the day', () => {});
it("stepEntry(1) at the day's last row moves to the previous day with rows and opens its newest row", () => { /* daily: 10/21 count 3, 10/20 count 0, 10/19 count 2 → url ?month=2026-10&day=2026-10-19 written, GET /entries date 10/19 answered → navigate ['/accounting/entries', <first id>] with month/day */ });
it('stepEntry(-1) at the first row opens the last row of the next later day with rows', () => {});
it("stepEntry does nothing at the month's edge (Review Focus 4)", () => { /* no navigate call */ });
```
Layout: `it('↓ in the drawer crosses a day edge and the calendar selection follows', …)` — with the real timeline: seed daily via HTTP flushes is heavy; instead assert that pressing ↓ on the drawer calls the registered command (fake) — already in Task 7 — and keep the cross-day behaviour pinned in the timeline spec.

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): drawer ↑ ↓ step through the month's entries across days (#318 D5)`

---

### Task 9: Search from 本月摘要 → month result list

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Timeline: **one search, two boxes** — the filter bar's text field stays visible in calendar mode and the 本月摘要 box edits the same text. `pane.search` is a mirror of the timeline's `query` signal: an effect publishes `pane.search.set(this.query())`; the layout's `onSearch(text)` calls `pane.commands()!.setQuery(text)` (new `CalendarCommands.setQuery(text: string): void` → the timeline's existing debounced `query` path, i.e. `query.set(text.trim())` through the same code `onQueryInput` ends in). `load()` / `loadDay()` keep using `query()` unchanged (the day rows already honour `q`). The filter bar's `#queryInput` is uncontrolled today: add an effect — when `query()` changes and `queryInput.nativeElement.value !== query()`, write the value, **skipped while that input is `document.activeElement`** (an IME composition is left alone); the lifted `.cal-search` box is controlled via `[value]="pane.search()"` (Task 6), so both boxes show the same text whichever one was typed in. The list effect: in 日曆, `query()` non-empty → `load(true)` and publish `pane.searchDays` built with `foldBatches = false` (`buildDays(entries, currency, hideRewards, false)` — the panel has no batch expander, same ruling as the day rows), `pane.searchState` from `loading`/`loadError`; empty → `clearList()` and `searchDays []`. `clearFilters()` clears `query` as today, so both boxes empty together. Month change: `query` persists across months exactly as it does in 清單 today (no new rule).

- [ ] **Step 1: Failing tests**

```ts
// timeline.spec.ts
it('a 本月摘要 search loads the month list with q and publishes the result days', () => { /* type 全聯 in the filter bar (or commands.setQuery('全聯')) → pane.search() '全聯', GET /entries with q=全聯, month range; flush 2 entries → pane.searchDays().length 1 day, searchState 'ready' */ });
it('clearing the search drops the result list and empties both boxes', () => { /* setQuery('') → searchDays [], pane.search() '', filter-bar input value '' */ });
it('typing in the 本月摘要 box updates the filter-bar input (and not while the filter bar is focused)', () => { /* commands.setQuery('全聯') with focus elsewhere → #queryInput value '全聯'; focus #queryInput, setQuery('x') → value unchanged, query() 'x' */ });
it("a selected day's rows use the 本月摘要 search as q (Review Focus 2)", () => {});
// accounting-layout.spec.ts
it('typing in the lifted search box keeps focus in it while the base switches to results', async () => { /* storage calendar, 1100px, no day; focus .cal-search, type '全聯' + debounce → calendarBase 'results' (app-calendar-day-panel[mode results]), document.activeElement === .cal-search, its value '全聯' */ });
it('typing in the filter bar shows the text in the lifted box; clearing it returns the summary and empties the box', async () => { /* type '全聯' in .list-pane input[type=search] + debounce → .cal-search value '全聯' in results mode; clear the filter bar → app-month-overview back, .cal-search value '' */ });
it('typing in 本月摘要 switches the base to the result list; picking a day shows the day panel; clearing the day returns to the results (Review Focus 2)', async () => { /* type 'x' in the filter bar's search input → pane.search() 'x', app-calendar-day-panel[mode results]…; navigate ?day → day mode; navigate without day → results again; pane.search() is still 'x', the results header shows 「x」, the filter-bar input value is 'x' and .cal-search shows 'x' */ });
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): 本月摘要 search lists the month's matches in the right pane (#318 D5)`

---

### Task 10: PR A follow-ups — exits that still drop `month` / `day`

**Files:**
- Modify: `frontend/src/app/components/accounting/split-group/group-nav.ts` (+spec), `split-group.ts` (+spec)
- Modify: `frontend/src/app/components/shell/shell.ts` (+spec), `frontend/src/app/components/mobile-nav/mobile-nav.{ts,html}` (+spec)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (+spec)

**Interfaces:**
- `groupParentUrl(state, passbookId, query: ParamMap | null = null): string` — for the timeline parent, serialize `/accounting` with `calendarParams(query)` (`router.serializeUrl(router.createUrlTree(['/accounting'], { queryParams }))` → the helper takes a `Router` too: `groupParentUrl(router, state, passbookId, query)`); `split-group.close()` and the dissolved-split redirect pass `this.route.snapshot.queryParamMap` (the redirect adds `queryParams: calendarParams(...)`).
- Shell: both `onGlobalKey` (N → `navigate(action.commands, { queryParams: calendarParams(...) })`) and `openEntryForm` (the 記一筆 button) pass `calendarParams(this.router.routerState.snapshot.root.queryParamMap)` — `{}` off the calendar, so every other page is unchanged. `entryFormHref` becomes a `computed` over a `url` signal updated on `NavigationEnd` (`prepareExternalUrl(serializeUrl(createUrlTree(ENTRY_FORM, { queryParams })))`), so a modified click (new tab) also carries month/day.
- Mobile nav: `[routerLink]="'/accounting/entry'" [queryParams]="entryQuery()"` with `entryQuery = computed(() => calendarParams(...))` refreshed on `NavigationEnd` (a `url` signal like the layout's).
- Entry detail: `copy()` → `{ queryParams: { copy: detail.id, ...calendarParams(this.route.snapshot.queryParamMap) } }`; `editSchedule()` → `{ schedule: link.definition_id, ...calendarParams(...) }`.
- Split group: `edit()` → `{ queryParams: { select: 'parent', ...calendarParams(this.route.snapshot.queryParamMap) } }`.

- [ ] **Step 1: Failing tests** — `group-nav.spec`: parent URL carries `?month&day` from the query; `split-group.spec`: ✕ (deep link) → `/accounting?month=2026-10&day=2026-10-21`; dissolved split → `/accounting/entries/5?month=…&day=…`; `shell.spec`: N on `/accounting?month=2026-10&day=2026-10-21` → `/accounting/entry?month=2026-10&day=2026-10-21`, N on `/portfolio` → `/accounting/entry`, the 記一筆 click carries month/day, the href follows the current URL after navigation; `split-group.spec`: 編輯 keeps month/day; `mobile-nav.spec`: the ＋ link href carries month/day on the calendar; `entry-detail.spec`: 複製 and 編輯整個排程 carry month/day.

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `fix(spa): split-group, global ＋/N, 複製 and schedule edits keep the calendar month and day (#318 D2)`

---

### Task 11: openspec ticks and final checks

**Files:**
- Modify: `openspec/changes/add-ledger-calendar-view/tasks.md` (§3: 3.1–3.6 → `[x]`, 3.4's `[ ]` note on `moveSelection` becomes part of the tick)

- [ ] **Step 1: Tick §3** (leave §4, §5 unticked).
- [ ] **Step 2: `cd frontend && npx tsc -p tsconfig.app.json --noEmit`** — clean.
- [ ] **Step 3: `npx ng test --watch=false`** — all pass (the pre-existing flaky `accounting-layout.spec` 1280px row test may time out; re-run once; report).
- [ ] **Step 4: `npx ng build --configuration development`** — succeeds.
- [ ] **Step 5: `cd .. && npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict`** — valid.
- [ ] **Step 6: Commit** — `docs(openspec): tick calendar PR B tasks`
