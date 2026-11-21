# Ledger Calendar PR A (state, data, day cells) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the 日曆 view's selected day in the URL, read the per-month calendar data (category dots, top merchants, invoice days) through one cached service, and draw the new day cells — without touching the wide / narrow layouts (PR B / PR C).

**Architecture:** The timeline keeps `month` as a writable signal but now mirrors `month` / `day` to the URL (`replaceUrl`, merged query) and follows the URL on every `NavigationEnd`. A new `CalendarDataService` owns the two `GET /reports/breakdown` reads and the inbox `einvoices` read for the shown month, cached per month and dropped on `entriesChanged` / `InboxService.changed`. `CalendarMonthComponent` gains inputs for dots, invoice days, paid dues, compact figures and week mode, a roving-tabindex grid and the new cell layout; the timeline feeds it.

**Tech Stack:** Angular 21 standalone + signals + OnPush, Vitest (`npx ng test --watch=false`), `npx tsc -p tsconfig.app.json --noEmit`, `npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict`.

**Spec:** `openspec/changes/add-ledger-calendar-view/` (`design.md` D2–D4, D7; `specs/frontend-accounting-ledger/spec.md`; `tasks.md` §1–§2). Branch `feat/ledger-calendar-pr-a` off `d30f491f` in worktree `/home/opc/workspace/home-hub-reports-spa-b2`. All paths below are relative to `frontend/src/app/` unless they start with `openspec/` or `docs/`.

## Global Constraints

- No API or schema change (proposal Non-goals). Both breakdown reads pass `include_excluded_projects=true`, `metric=expense`, `date_from={month}-01`, `date_to={last day}` (design D3; owner ruling 2026-11-09: the calendar counts 不計入報表 projects everywhere).
- URL: `day=YYYY-MM-DD` beside `month=YYYY-MM` on `/accounting`. Every `month` / `day` write uses `replaceUrl: true` and `queryParamsHandling: 'merge'` (spec "Calendar selection history"). Without `day` nothing is selected; 日曆 never selects today by itself.
- `day` forces the 日曆 view only while it is in the URL; the stored view (`localStorage` key `hh.accounting.timelineView`) is not overwritten by a `day` link. 清單 removes `day` and stores 清單; 近 7 天 writes `month` + `day` and stores 日曆.
- Net = `income + expense` from `GET /entries/summary/daily` (`expense` is a negative string). Heat bar = |expense| / month max |expense|, only when `expense < 0`.
- Figures: ≥ 1024 px (`LayoutModeService.mode() === 'panes'`) thousands separators (`−3,096`, `+71,700`); below, compact (`−3.1k`, `+71.7k`, `−250`; one decimal, trailing `.0` dropped). Quiet past day: `—` wide, nothing narrow. Future days: dimmed, no figure.
- Marks copy: `帳單` / `已繳` (wide), muted dot (narrow); invoice `發票待對帳` (wide), dot (narrow) in `--app-warning` (until `--app-banner-warn` lands). 已繳 = due event whose `BillState` has `statement > 0 && remaining <= 0`; a zero statement shows no mark (existing behaviour).
- Accessible name: date, weekday, row count, expense, marks, e.g. 「10 月 21 日 週三，3 筆，支出 $3,096，發票待對帳」. Bar and dots `aria-hidden`.
- Keyboard handlers bind to the grid element, never `document` (D8). Roving tabindex: ← → one day, ↑ ↓ one week, Home / End first / last day of the month, Enter / Space pick (native button click).
- Failures are silent per part: no toast, no `load-error` block — dots left out, merchants replaced by 「商家排行讀取失敗」 (PR B renders it; PR A exposes the state), invoice marks left out.
- Line icons only (no emoji in templates; `theme/line-icons.spec.ts` gates it). Copy in 繁體中文 as quoted. Commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never commit `.superpowers/`.
- Do not change `AccountingLayoutComponent`'s pane layout, `MonthOverviewComponent`'s content or the 清單 view — those are PR B / PR C.

## Review Focus

1. **Back from `/accounting?month=2026-10&day=2026-10-21` to a `/accounting` without `day`** must deselect the day and keep the stored view (Task 2 test "follows Back to a URL without day").
2. **A `day` whose month differs from `month`** (`?month=2026-09&day=2026-10-21`) must show October with 10/21 selected (Task 2 test "a day outside month wins").
3. **A malformed `day`** (`day=2026-13-99`, `day=today`) must be ignored, not crash `buildCalendar` (Task 1 test "rejects malformed values").
4. **`entriesChanged` while on the 清單 view** must not fire the breakdown reads (Task 4 test "idle without a month").
5. **A due day with one paid and one open card** must read 帳單, not 已繳 (Task 7 test "paid days").

---

### Task 1: URL helpers (`calendar-url.ts`)

**Files:**
- Create: `components/accounting/timeline/calendar-url.ts`
- Test: `components/accounting/timeline/calendar-url.spec.ts`

**Interfaces:**
- Produces:
  - `export const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;`
  - `export const DAY_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;`
  - `export interface CalendarQuery { month: string | null; day: string | null }`
  - `export function readCalendarQuery(query: ParamMap): CalendarQuery` — validated values, `null` for missing or malformed; when `day` is valid, `month` is `day.slice(0, 7)` whatever the `month` param says (a `day` outside `month` wins).
  - `export function calendarParams(query: ParamMap): Params` — `{ month?, day? }` holding only the valid values present (empty object when none). Used to carry the selection onto entry / form routes and back.
  - `export function calendarWrite(month: string, day: string | null): Params` — `{ month, day }` with `day: null` when clearing (Angular removes a `null` query param on merge).

- [ ] **Step 1: Write the failing tests**

```ts
import { convertToParamMap } from '@angular/router';
import { describe, expect, it } from 'vitest';
import { calendarParams, calendarWrite, readCalendarQuery } from './calendar-url';

const q = (params: Record<string, string>) => convertToParamMap(params);

describe('readCalendarQuery', () => {
  it('reads a valid month and day', () => {
    expect(readCalendarQuery(q({ month: '2026-10', day: '2026-10-21' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
  });
  it('lets a day outside month win', () => {
    expect(readCalendarQuery(q({ month: '2026-09', day: '2026-10-21' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
  });
  it('reads a month alone', () => {
    expect(readCalendarQuery(q({ month: '2026-10' }))).toEqual({ month: '2026-10', day: null });
  });
  it('rejects malformed values', () => {
    expect(readCalendarQuery(q({ month: '2026-13', day: '2026-13-99' }))).toEqual({ month: null, day: null });
    expect(readCalendarQuery(q({ month: 'oct', day: 'today' }))).toEqual({ month: null, day: null });
  });
});

describe('calendarParams / calendarWrite', () => {
  it('keeps only the valid selection params', () => {
    expect(calendarParams(q({ month: '2026-10', day: '2026-10-21', category_id: '12' }))).toEqual({ month: '2026-10', day: '2026-10-21' });
    expect(calendarParams(q({ month: '2026-10' }))).toEqual({ month: '2026-10' });
    expect(calendarParams(q({ day: 'nope' }))).toEqual({});
  });
  it('writes day: null to clear it', () => {
    expect(calendarWrite('2026-11', null)).toEqual({ month: '2026-11', day: null });
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && npx ng test --watch=false --include='**/calendar-url.spec.ts'` → FAIL (module not found).
- [ ] **Step 3: Implement** `calendar-url.ts` with the four exports above. `readCalendarQuery`: validate `day` with `DAY_RE` first; when valid return `{ month: day.slice(0, 7), day }`; else validate `month` with `MONTH_RE`.
- [ ] **Step 4: Run to verify pass** — same command → PASS.
- [ ] **Step 5: Commit** — `git add frontend/src/app/components/accounting/timeline/calendar-url.ts frontend/src/app/components/accounting/timeline/calendar-url.spec.ts && git commit -m "feat(spa): calendar URL helpers for month/day selection"`

---

### Task 2: The selected day lives in the URL (tasks 1.1, 1.5 URL part)

**Files:**
- Modify: `components/accounting/timeline/timeline.ts` (view / month / selectedDay state: lines 494–499, constructor 652–686, `setView` 1076, `selectDay` 1084, `openUpcoming` 1089, `moveMonth` 1103)
- Test: `components/accounting/timeline/timeline.spec.ts` (new `describe('day in the URL')` block; the existing calendar tests keep passing)

**Interfaces:**
- Consumes: Task 1 `readCalendarQuery`, `calendarWrite`.
- Produces (timeline public surface, used by Tasks 3 and 7 and by PR B / C):
  - `readonly view: Signal<TimelineView>` — now a `computed`: `'calendar'` while `urlDay() !== null`, else `chosenView()`.
  - `readonly selectedDay: Signal<string | null>` — a `computed` of the URL (`urlDay`), no longer a writable signal.
  - `selectDay(date: string): void` — toggles: writes `calendarWrite(date.slice(0, 7), date)`, or clears `day` when `date === selectedDay()`.
  - `stepDay(delta: 1 | -1): void` — `addDays(selectedDay(), delta)`, crossing month edges (`month` follows); no-op without a day. (‹ › buttons come in PR B / C; `[` `]` too.)
  - `clearDay(): void` — public now; writes `day: null` (本月摘要 / 展開整月 in PR B / C call it).
  - `setView(view)`: `'list'` writes `day: null` then stores 清單; `'calendar'` stores 日曆.
  - `openUpcoming()`: `chosenView.set('calendar')`, `storeView('calendar')`, writes `calendarWrite(first.date.slice(0, 7), first.date)`.
  - `moveMonth(delta)`: writes `calendarWrite(shiftMonth(month(), delta), null)`.
  - `private writeCalendar(params: Params): void` — `this.router.navigate([], { queryParams: params, queryParamsHandling: 'merge', replaceUrl: true })`, and applies the same values to `month` / `urlDay` at once (so the UI does not wait for `NavigationEnd`).
  - `private syncFromUrl(): void` — `readCalendarQuery(this.router.routerState.snapshot.root.queryParamMap)`: sets `month` when the result has one, sets `urlDay` (null clears). Called at construction (replacing the one-shot `month` read at lines 661–665; the filter reads at 666–681 stay one-shot) and on every `NavigationEnd` (`router.events` + `takeUntilDestroyed`).

- [ ] **Step 1: Write the failing tests** (`describe('day in the URL')`, inside the top-level `describe`; reuse `render`, `flushSummary`, `flushDaily`, `flushEntries`, `flushCounterparties`; use `provideRouter([{ path: '**', children: [] }])` as the drill-through test at line 120 does, and `TestBed.inject(Router)`):

```ts
it('selects the day named in the URL and forces 日曆 without storing it', async () => {
  // navigateByUrl('/accounting?month=2026-10&day=2026-10-21') before createComponent, mode 'phone', nothing stored
  // expect flushDaily('2026-10'); day entries request date_from === date_to === '2026-10-21'
  // expect cell '2026-10-21' has class sel; localStorage.getItem(VIEW_KEY) === null
});
it('a day outside month wins', async () => {
  // '/accounting?month=2026-09&day=2026-10-21' → flushDaily('2026-10'), monthText shows 2026 年 10 月
});
it('writes month and day with replaceUrl, merged into the drill-through query', () => {
  // start at '/accounting?month=2026-10&category_id=12', render calendar, click cell 2026-10-21
  // expect router.url === '/accounting?month=2026-10&category_id=12&day=2026-10-21'
  // expect navigate spy called with ([], expect.objectContaining({ queryParamsHandling: 'merge', replaceUrl: true }))
});
it('clears day when the selected day is tapped again and when the month navigator moves', () => {
  // tap 10/21 twice → url has no day; tap again, then .month-next → url '/accounting?month=2026-11' (no day)
});
it('stepDay crosses the month edge', () => {
  // select 2026-10-31, component.stepDay(1) → url '?month=2026-11&day=2026-11-01', flushDaily('2026-11')
});
it('follows Back to a URL without day', async () => {
  // select 10/21, then router.navigateByUrl('/accounting?month=2026-10') → no .sel cell, .day-entries gone, view still 日曆 (stored 'calendar' set up by test)
});
it('a day link does not overwrite a stored 清單: the list returns when day leaves the URL', async () => {
  // localStorage VIEW_KEY 'list'; open '/accounting?month=2026-10&day=2026-10-21' → 日曆 shown, VIEW_KEY still 'list'
  // navigateByUrl('/accounting?month=2026-10') → 清單 radio checked, list request flushed
});
it('清單 removes day and stores 清單', () => {
  // select 10/21, click viewButton(el, '清單') → url '/accounting?month=2026-10', localStorage VIEW_KEY === 'list', list request flushed
});
it('近 7 天 writes month and day and stores 日曆', () => {
  // reuse the fixture of the test at line 1690: after openUpcoming the url has month + day of the first due day and VIEW_KEY === 'calendar'
});
```

- [ ] **Step 2: Run to verify failure** — `npx ng test --watch=false --include='**/timeline.spec.ts'` → the new tests FAIL (`stepDay` undefined, URL unchanged).
- [ ] **Step 3: Implement** in `timeline.ts`: `private readonly chosenView = signal<TimelineView>(initialView(...))`, `private readonly urlDay = signal<string | null>(null)`, `readonly view = computed(...)`, `readonly selectedDay = computed(() => this.urlDay())`, the methods in Interfaces, the `NavigationEnd` subscription. Replace every `this.selectedDay.set/update` and `this.view.set` with the new writes. The template's `(daySelected)="selectDay($event)"` and `setView(...)` bindings stay.
- [ ] **Step 4: Run to verify pass** — the whole `timeline.spec.ts` → PASS (existing tests at 1201–1268, 1331, 1690 adapt only if they asserted on `selectedDay.set`; keep their behaviour).
- [ ] **Step 5: Commit** — `git commit -am "feat(spa): calendar day selection lives in the URL (#318 D2)"`

---

### Task 3: Carry `month` / `day` through entry and form exits (task 1.2)

**Files:**
- Modify: `components/accounting/timeline/timeline.ts:1285-1296` (`open(row)`)
- Modify: `components/accounting/accounting-layout/accounting-layout.ts:305-312` (`navigateClose`), `:364-387` (`moveSelection`)
- Modify: `components/accounting/entry-detail/entry-detail.ts:424-430` (`parentUrl`), `:535`, `:605` (edit), `:659` (repost next entry)
- Modify: `components/accounting/entry-form/entry-form.ts:738-748` (query subscription), `:831-861` (`start`), `:2072` (post-split-save navigation), `:2105-2113` (`leaveToGroup`), `:2116-2124` (`reloadAfterSave`), `:2412-2419` (`leave`)
- Test: `timeline.spec.ts`, `accounting-layout.spec.ts`, `entry-detail.spec.ts`, `entry-form.spec.ts`

**Interfaces:**
- Consumes: Task 1 `calendarParams(query: ParamMap): Params`, `DAY_RE`.
- Produces: none new. Rule for every site: read `calendarParams(this.router.routerState.snapshot.root.queryParamMap)` (or the component's `ActivatedRoute.snapshot.queryParamMap` where one is injected) and pass it as `queryParams`; where the target is a URL string, build it with `this.router.createUrlTree([...], { queryParams }).toString()` → `router.serializeUrl`.

Sites:
1. Timeline `open(row)`: both `navigate` calls gain `queryParams: calendarParams(...)` — only when `this.view() === 'calendar'` (the 清單 view keeps dropping the query as today).
2. Layout `navigateClose()`: the `/accounting` branch becomes `this.router.navigate(['/accounting'], { queryParams: calendarParams(...) })`; the passbook and accounts branches are unchanged.
3. Layout `moveSelection()`: the final `navigate` (timeline rows, non-passbook) gains `queryParams: calendarParams(...)`.
4. Entry detail `parentUrl()`: the `/accounting` return becomes `serializeUrl(createUrlTree(['/accounting'], { queryParams: calendarParams(this.route.snapshot.queryParamMap) }))`; reminders and passbook returns unchanged. `close()` and the after-delete path call `parentUrl()` already, so they inherit it.
5. Entry detail edit navigations (lines 535, 605) and the repost `replaceUrl` navigation (659): add `queryParamsHandling: 'preserve'`.
6. Entry form: `start()` gains a sixth parameter `dateParam: string | null = null`; when `id === null && dateParam !== null && DAY_RE.test(dateParam)` call `this.setParentDate('entryDate', dateParam)` after `resetFields(...)` and then reset `dateTouched` to false is NOT required (a prefilled date counts as chosen). The subscription at 742 passes `query.get('date')`.
7. Entry form `leave()` fallback and `reloadAfterSave()` fallback: `navigate(['/accounting'], { queryParams: calendarParams(this.route.snapshot.queryParamMap) })`.
8. Entry form after a successful split save: the `navigateByUrl` onto the new entry (~line 2072, `replaceUrl`, `closeTo: 'list'`) becomes `navigate(['/accounting/entries', id], { replaceUrl: true, queryParamsHandling: 'preserve', state })`, and `leaveToGroup()`'s `navigate(groupCommands(...))` (~2112) gains `queryParamsHandling: 'preserve'` — with `closeTo` the detail's ✕ goes to `parentUrl()`, which must still see `month` / `day` (spec: "saving … restores the selection").

- [ ] **Step 1: Write the failing tests**
  - `timeline.spec.ts`: `it('opens a row from the calendar with month and day in the query')` — select 10/21, click the first `.entry-row`, expect `navigate` called with `(['/accounting/entries', 1], { queryParams: { month: '2026-10', day: '2026-10-21' } })`; and `it('opens a row from the list without the query')` keeps the existing expectation.
  - `accounting-layout.spec.ts` (use `start('panes', '/accounting/entries/5?month=2026-10&day=2026-10-21', 1440)`): `it('✕ returns to the calendar day')` → after `close()` the router URL is `/accounting?month=2026-10&day=2026-10-21`; `it('↓ keeps month and day')` — after a `moveSelection` the URL still carries both.
  - `entry-detail.spec.ts`: `it('closes to the selected calendar day with no history')` — route query `month=2026-10&day=2026-10-21`, `lastSuccessfulNavigation` without `previousNavigation`, `close()` → `navigateByUrl('/accounting?month=2026-10&day=2026-10-21')`; `it('keeps the query on 編輯')` → `navigate(['/accounting/entries', id, 'edit'], { queryParamsHandling: 'preserve' })`.
  - `entry-form.spec.ts`: `it('prefills the date from ?date=')` — open `/accounting/entry?date=2026-10-21&month=2026-10&day=2026-10-21`, expect the date input value `2026-10-21`; `it('cancel with no history returns to the calendar day')` → `navigate(['/accounting'], { queryParams: { month: '2026-10', day: '2026-10-21' } })`; `it('ignores a malformed date')` → today's date stays; `it('keeps the query on the entry opened after a split save')` → the post-save navigation carries `queryParamsHandling: 'preserve'` (extend the existing split-save test near line 1218).
- [ ] **Step 2: Run to verify failure** — the four spec files → the new tests FAIL.
- [ ] **Step 3: Implement** the seven sites.
- [ ] **Step 4: Run to verify pass** — `npx ng test --watch=false --include='**/{timeline,accounting-layout,entry-detail,entry-form}*.spec.ts'` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(spa): entry and form exits keep the calendar month and day (#318 D2)"`

---

### Task 4: `CalendarDataService` (tasks 1.3, 1.4, 1.5 data part)

**Files:**
- Create: `components/accounting/calendar-month/calendar-data.service.ts`
- Test: `components/accounting/calendar-month/calendar-data.service.spec.ts`

**Interfaces:**
- Consumes: `ReportsService.getBreakdown(query: BreakdownQuery)`, `InboxService.get()` + `InboxService.changed`, `AccountingService.entriesChanged`, `monthRange(month)` from `../timeline/timeline` (move it to `../dates` if the import would be circular — `dates.ts` has no imports from timeline; re-export from timeline stays).
- Produces:

```ts
export type CalendarPartState = 'loading' | 'ready' | 'error';
export interface CalendarMonthData {
  /** `YYYY-MM-DD` → that day's expense splits by category id (`key`), largest first. */
  dots: ReadonlyMap<string, BreakdownSplit[]>;
  dotsState: CalendarPartState;
  /** Top five merchants of the month (rows with a null key dropped), as the API orders them. */
  merchants: BreakdownRow[];
  merchantsState: CalendarPartState;
  /** `issue_date` → number of inbox e-invoices awaiting a match that day. */
  invoiceDays: ReadonlyMap<string, number>;
  invoiceState: CalendarPartState;
}
export const EMPTY_CALENDAR_DATA: CalendarMonthData; // empty maps/arrays, every state 'loading'

@Injectable({ providedIn: 'root' })
export class CalendarDataService {
  /** The month the 日曆 view shows; null when no calendar is on screen (nothing is read then). */
  readonly month = signal<string | null>(null);
  /** The data for `month()`; EMPTY_CALENDAR_DATA while idle. */
  readonly data: Signal<CalendarMonthData>;
}
```
  Behaviour: a constructor `effect` reads `month()`, `accounting.entriesChanged()` and `inbox.changed()`. When `entriesChanged` moved since the last run the whole cache (a `Map<string, CalendarMonthData>` behind a signal) is cleared; when only `inbox.changed` moved, every month's `invoiceDays` part is reset to loading and re-read. For a non-null month whose record is missing, the three reads start (each with its own request id so a late answer for a dropped month is ignored):
  - dots: `getBreakdown({ group_by: 'day', split_by: 'category', metric: 'expense', include_excluded_projects: true, date_from, date_to })` → `rows` → map `row.key!` → `(row.splits ?? []).filter(s => s.key !== null).sort((a, b) => Number(b.amount) - Number(a.amount))`; error → `dotsState 'error'`, empty map.
  - merchants: `getBreakdown({ group_by: 'merchant', metric: 'expense', include_excluded_projects: true, date_from, date_to })` → `rows.filter(r => r.key !== null).slice(0, 5)`; error → `'error'`, `[]`.
  - invoices: `inbox.get()` → `(einvoices ?? []).filter(e => e.issue_date.startsWith(month))` counted by `issue_date`; error → `'error'`, empty map.

- [ ] **Step 1: Write the failing tests** (`provideHttpClient()`, `provideHttpClientTesting()`; flush `/api/accounting/reports/breakdown` by `params.get('group_by')`; inbox `/api/accounting/reconciliation/inbox` with `makeInbox([], {})` plus an `einvoices` array):

```ts
it('is idle without a month', () => { /* month null → httpMock.expectNone for breakdown and inbox; data() === EMPTY_CALENDAR_DATA */ });
it('reads dots, merchants and invoice days for the month with the spec query', () => {
  // month '2026-10': day read has group_by day, split_by category, metric expense, include_excluded_projects 'true', date_from '2026-10-01', date_to '2026-10-31'
  // merchant read has group_by merchant and the same range; inbox read has no limit param
  // flush day rows [{key:'2026-10-21', splits:[{key:'3',…,amount:'100'},{key:'7',…,amount:'2996'}]}] → dots.get('2026-10-21') keys ['7','3']
  // flush merchants [{key:null,…},{key:'全聯',…},…seven more] → merchants.length 5, none with null key
  // flush inbox einvoices issued 2026-10-22 ×2 and 2026-09-30 → invoiceDays.get('2026-10-22') === 2, no '2026-09-30'
});
it('leaves the other parts when one read fails', () => { /* day read 500 → dotsState 'error', dots empty; merchantsState 'ready' after flush */ });
it('caches a month and re-reads it after entriesChanged', () => { /* set '2026-10' twice via '2026-11' and back → no new reads; `TestBed.inject(AccountingService).markEntriesChanged()` → three reads again */ });
it('re-reads only the invoices on InboxService.changed', () => { /* apply() flush → one inbox read, no breakdown */ });
it('drops a late answer for a month no longer shown', () => { /* set '2026-10', switch to '2026-11' before flushing October's day read, flush it → data() is November's (loading), no October keys */ });
```

- [ ] **Step 2: Run to verify failure** — `--include='**/calendar-data.service.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** the service.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(spa): CalendarDataService caches calendar breakdowns and invoice days (#318 D3)"` (add both files).

---

### Task 5: Cell figure helpers (`calendar-math.ts`)

**Files:**
- Modify: `components/accounting/calendar-month/calendar-math.ts`
- Test: `components/accounting/calendar-month/calendar-math.spec.ts`

**Interfaces:**
- Produces:
  - `export function compactNet(value: number): string` — `0 → '0'`; |v| < 1000 → signed whole (`−250`, `+71`); else `(v / 1000)` one decimal, trailing `.0` dropped, `k` suffix (`−3.1k`, `+71.7k`, `−12k`). Minus is `−` (U+2212, as `format.ts` `MINUS`), plus is `+`.
  - `export function wideNet(value: number): string` — `0 → '0'`; else sign + `Math.abs(value)` grouped `en-US`, no decimals (`−3,096`, `+71,700`).
  - `export function heatRatio(expense: number, maxExpense: number): number` — `expense < 0 && maxExpense > 0 ? Math.min(1, -expense / maxExpense) : 0`.
  - `export function maxDailyExpense(days: DailySummaryDay[]): number` — the largest `-Number(expense)` over days with `expense < 0`, else 0.
  - `export function categoryColorMap(roots: CategoryNode[]): ReadonlyMap<number, string>` — id → `node.color ?? parent.color ?? FALLBACK_COLOR` (`FALLBACK_COLOR` from `../timeline/category-distribution`), walking `children` to any depth.
  - `export function cellAriaLabel(parts: { date: string; count: number; expense: number; income: number; currency: string; bill: 'due' | 'paid' | null; dueCount: number; invoices: number }): string` — `「{M} 月 {D} 日 {週X}」` then, comma-joined (`，`): `{count} 筆` when count > 0; `支出 {formatMoney(-expense, currency)}` when expense < 0; `收入 {formatMoney(income, currency)}` when income > 0; `{dueCount} 張卡繳費到期` for `bill === 'due'`, `已繳` for `'paid'`; `發票待對帳` when invoices > 0. Weekday names `週日`…`週六` from the ISO date (UTC).

- [ ] **Step 1: Write the failing tests** — one `it` per function with the spec values: `compactNet(-3096) === '−3.1k'`, `compactNet(71700) === '+71.7k'`, `compactNet(-250) === '−250'`, `compactNet(-12000) === '−12k'`, `compactNet(0) === '0'`; `wideNet(-3096) === '−3,096'`; `heatRatio(-3096, 12120)` ≈ 0.2554 (`toBeCloseTo(0.255, 3)`), `heatRatio(500, 12120) === 0`, `heatRatio(-5, 0) === 0`; `maxDailyExpense` of the spec's October (−12,120 on 10/19, −3,096 on 10/21, income-only 10/05) `=== 12120`; `categoryColorMap` child without colour takes the parent's, root without colour takes `FALLBACK_COLOR`; `cellAriaLabel({date:'2026-10-21', count:3, expense:-3096, income:0, currency:'TWD', bill:null, dueCount:0, invoices:1}) === '10 月 21 日 週三，3 筆，支出 $3,096，發票待對帳'` and the paid variant ends with `已繳`.
- [ ] **Step 2: Run to verify failure** — `--include='**/calendar-math.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** the six functions (the `buildCalendar` export is unchanged).
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(spa): calendar cell figure helpers (net, heat, colours, aria)"`

---

### Task 6: Day cells (tasks 2.1–2.4)

**Files:**
- Modify: `components/accounting/calendar-month/calendar-month.ts`, `calendar-month.html`, `calendar-month.scss`
- Test: `components/accounting/calendar-month/calendar-month.spec.ts`

**Interfaces:**
- Consumes: Task 5 helpers; `BillingEvent` (unchanged `billing` input semantics: closing + due events; the timeline decides which due events to pass — Task 7).
- Produces (new inputs; existing `month`, `days`, `loading`, `currency`, `weekStart`, `selected`, `billing`, `today`, `daySelected` stay):
  - `readonly dots = input<ReadonlyMap<string, readonly string[]>>(new Map())` — date → colours, largest category first (all of them; the cell shows three + `+N`).
  - `readonly invoiceDays = input<ReadonlyMap<string, number>>(new Map())`.
  - `readonly paidDays = input<ReadonlySet<string>>(new Set())` — due days whose every due event's statement is fully paid (Task 7 computes it).
  - `readonly compact = input(false)` — narrow figures and dot / mark style (< 1024 px).
  - `readonly weekOf = input<string | null>(null)` — week mode: only the row containing that date renders, and no dots.
  - Cell view fields replacing `expenseText` / `incomeText`: `net: string | null` (wideNet / compactNet; `'—'` for a quiet past day in wide; `null` for quiet narrow and for future), `heat: number`, `dots: string[]` (≤ 3), `more: number`, `bill: 'due' | 'paid' | null`, `invoices: number`, `future: boolean`, `label` from `cellAriaLabel`, `tabIndex: 0 | -1`.
  - Roving focus: `private readonly focusDate = signal<string | null>(null)`; the cell with `tabindex="0"` is `focusDate() ?? selected() ?? (today in month ? today : `${month}-01`)`. `onGridKeydown(event: KeyboardEvent)` bound as `(keydown)` on `.cal`: ArrowLeft/Right ±1, ArrowUp/Down ±7 (clamped inside the month), Home / End → first / last day of the month; moves `focusDate`, focuses the matching `button.cell[data-date]`, `preventDefault()`. Enter / Space are the button's native click.
  - Template per cell, top to bottom: `.d` date (today keeps the disc: `.today .d` gets `--app-primary` background, white text; `[class.weekend]` on Saturday / Sunday cells → `.weekend .d { color: var(--app-text-muted) }`), `.net` (`[class.neg]` when expense net < 0, `.pos` when > 0, `.quiet` for `—`), `.dots` (`aria-hidden`, `span.dot[style.background]` ×≤3 + `.more` `+N`), `.heat` (`aria-hidden`, `[style.width.%]="cell.heat * 100"`, only when `heat > 0`), marks: `.bill-mark` text `帳單` / `已繳` (wide) or `.bill-dot` (compact); `.inv-mark` text `發票待對帳` (wide) or `.inv-dot` (compact). Remove the `.due-badge` / `pi-credit-card` badge. `.closing` keeps the top rule. `[class.future]` dims. `[attr.tabindex]`.
  - Legend: `@if (!compact() && !weekOf())` a `.legend` under the grid: 「▬ 支出比例 · ● 類別 · 帳單 到期」 drawn with spans (heat swatch, dot swatch, text), `aria-hidden="true"` with an `sr-only` sentence 「色條為當日支出比例，圓點為類別，帳單為繳費到期」.
  - SCSS: `.heat` absolute bottom, height 4px (3px when `:host-context` compact → use a `.compact` class on `.cal`), `background: color-mix(in srgb, var(--tone-neg, var(--c-red)) 35%, transparent)`; `.dot` 6px circle; `.inv-mark`, `.inv-dot` colour `var(--app-warning)`; `.bill-dot` `var(--app-text-muted)`; `.future { opacity: .45 }`; `.week-mode .cell { min-height: 52px }`; wide cells `min-height: 76px` when not compact.

- [ ] **Step 1: Write the failing tests** (extend `render()` to accept the new inputs; keep the existing tests, updating the three that assert `.exp` / `.inc` / `.due-badge` to the new structure):

```ts
const OCT: DailySummaryDay[] = [
  { date: '2026-10-05', expense: '0.0000', income: '72000.0000', count: 1 },
  { date: '2026-10-19', expense: '-12120.0000', income: '0.0000', count: 4 },
  { date: '2026-10-21', expense: '-3096.0000', income: '0.0000', count: 3 },
  { date: '2026-10-23', expense: '250.0000', income: '0.0000', count: 1 },   // refund-heavy
];
// today = '2026-10-22'
it('spans the heaviest day and about a quarter on 10/21; no bar on a refund-heavy or income-only day')
   // heat width style '100%' on 10/19, ≈ '25.5%' on 10/21 (parseFloat toBeCloseTo 25.5, 0), no .heat on 10/23 and 10/05
it('shows net = income + expense: −3,096 wide, −3.1k compact, +72,000 / +72k')
it('shows — for a quiet past day on wide, nothing compact, and dims future days without a figure')
   // 10/05? no — use 10/06 (count 0): wide .net text '—', compact no .net; 10/25 has class future and no .net
it('draws up to three dots then +N, none in week mode, none when the map is empty')
   // dots.set('2026-10-21', ['#a','#b','#c','#d']) → 3 span.dot + .more '+1'; weekOf '2026-10-21' → 1 .week row, 0 .dot
it('marks bills 帳單 / 已繳 wide and as dots compact; a paid day reads 已繳 in the name')
   // billing due 10/05 (two cards) + paidDays {'2026-10-12'} with a due on 10/12 → 10/05 .bill-mark '帳單', aria contains '2 張卡繳費到期'; 10/12 '已繳'; compact → .bill-dot, no text
it('marks invoice days and ends the accessible name with 發票待對帳')
   // invoiceDays {'2026-10-22': 1}; wide .inv-mark text '發票待對帳'; aria-label '10 月 22 日 週四，發票待對帳' (count 0 → no 筆)
it('hides the bar and dots from assistive technology') // aria-hidden on .heat, .dots
it('is one tab stop: the selected day (else today, else the 1st) has tabindex 0 and arrows move the focus')
   // selected 10/21 → tabindex 0 only there; dispatch ArrowDown on .cal with 10/21 focused → focus on 10/28, tabindex moved; ArrowRight from 10/31 stays 10/31; Home → 10/01; End → 10/31
   // then with focus on 10/28, `.click()` on the focused button (Enter / Space are the native button activation, not preventDefault'ed) → daySelected emits '2026-10-28'
it('mutes weekend day numbers') // Saturday / Sunday cells have class weekend on .d (D4: weekend numbers muted)
it('shows the legend only on wide month mode')
```

- [ ] **Step 2: Run to verify failure** — `--include='**/calendar-month.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** component, template and styles as in Interfaces.
- [ ] **Step 4: Run to verify pass** — `calendar-month.spec.ts` and `theme/line-icons.spec.ts` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(spa): calendar day cells with net, heat bar, dots and marks (#318 D4)"`

---

### Task 7: Wire the timeline to the service and the new cells (tasks 1.3–1.5, 2.2 data)

**Files:**
- Modify: `components/accounting/timeline/timeline.ts` (effects 708–716, `billing` 608, `ensureOptions` 1144–1155, new computeds), `timeline.html:180-191`
- Test: `components/accounting/timeline/timeline.spec.ts`

**Interfaces:**
- Consumes: Task 4 `CalendarDataService` (`month`, `data`), Task 5 `categoryColorMap`, Task 6 inputs.
- Produces (timeline):
  - `private readonly calendarData = inject(CalendarDataService)`; constructor `effect(() => this.calendarData.month.set(this.view() === 'calendar' ? this.month() : null))`; `DestroyRef.onDestroy(() => this.calendarData.month.set(null))`.
  - `private readonly categoryColors = signal<ReadonlyMap<number, string>>(new Map())` — set in `ensureOptions()` from the raw roots (`categoryColorMap([...expense, ...income])`) beside `categoryOptions`. An effect calls `ensureOptions()` when `view() === 'calendar'` (its `optionsRequested` flag already stops repeat reads — keep it).
  - `readonly calendarDots = computed<ReadonlyMap<string, readonly string[]>>()` — `data().dots` mapped through `categoryColors` (`FALLBACK_COLOR` for an unknown key).
  - `readonly invoiceDays = computed(() => this.calendarData.data().invoiceDays)`.
  - One predicate for the grid: `private markedBill(event): BillState | null` — `bill(event)` when `bill !== null && bill.statement > 0`, else null (a zero or refund-heavy statement, or an unresolved one, stays unmarked as today — `bill()` does return a `BillState` for a resolved zero statement, so `!== null` alone is NOT enough). `readonly billing` keeps closing events and the due events with `markedBill(event) !== null` (open or fully paid). `readonly paidDays = computed<ReadonlySet<string>>()` — the dates whose set of marked due events is non-empty and every member has `remaining <= 0`. One open card beside a paid one → not in `paidDays` → 帳單. `billLines` is unchanged (open bills only, `openBill`).
  - Template: `<app-calendar-month … [dots]="calendarDots()" [invoiceDays]="invoiceDays()" [paidDays]="paidDays()" [compact]="layoutMode.mode() !== 'panes'" />` (expose `layoutMode` as `protected`, or add `readonly compactCells = computed(...)`). `weekOf` stays unbound (PR C).
  - Test seam: `render()` in `timeline.spec.ts` provides a fake `CalendarDataService` by default — `{ month: signal(null), data: signal(EMPTY_CALENDAR_DATA) }` typed loosely via `useValue` — so the existing calendar tests see no extra HTTP; tests of this task set the fake's `data` signal.

- [ ] **Step 1: Write the failing tests** (in the calendar `describe`):

```ts
it('tells CalendarDataService the month in 日曆 and null in 清單 or on destroy')
it('colours the dots from the category list and falls back for an unknown key')
   // fake data dots {'2026-10-21': [{key:'3'},{key:'99'}]}; categories flushed from ensureOptions with id 3 colour '#abc' → cell dots ['#abc', FALLBACK_COLOR]
it('passes invoice days to the grid') // fake invoiceDays {'2026-10-22': 1} → cell 10/22 has .inv-mark (panes) / .inv-dot (phone)
it('renders the grid without dots when the breakdown failed') // dotsState 'error', dots empty → no .dot, no .load-error, no toast element text
it('passes paid due days: a fully paid statement reads 已繳, a day with one open card reads 帳單')
   // reuse billRequests() from the billing describe at 1566+: two cards due 10/05, one statement paid in full (transfer_in == statement), other open → 10/05 .bill-mark '帳單'; a single paid card due 10/12 → '已繳';
   // a zero statement and a refund-heavy statement (spend 0 / spend > 0) → no .bill-mark and no .bill-dot on their due day; a zero-statement card beside a fully paid card → that day reads '已繳'
   // (test 1598 "hides a zero statement everywhere" still passes; update its `.due-badge` assertion to `.bill-mark, .bill-dot` since Task 6 removes the badge)
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run to verify pass** — `timeline.spec.ts` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(spa): timeline feeds dots, invoice days and paid dues to the calendar grid"`

---

### Task 8: Spec bookkeeping and whole-branch checks

**Files:**
- Modify: `openspec/changes/add-ledger-calendar-view/tasks.md` (tick 1.1–1.5, 2.1–2.4)

- [ ] **Step 1:** Tick the §1 and §2 boxes. Note under 1.4 that the invoice read lives in `CalendarDataService` (D7 swap point).
- [ ] **Step 2:** `cd frontend && npx tsc -p tsconfig.app.json --noEmit` → no output.
- [ ] **Step 3:** `npx ng test --watch=false` → all green (report the count).
- [ ] **Step 4:** `npx ng build --configuration development` → succeeds (never `npm run build`).
- [ ] **Step 5:** `npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict` → valid.
- [ ] **Step 6: Commit** — `git commit -am "docs(openspec): tick calendar PR A tasks"`

---

## Self-review notes

- Spec coverage (PR A scope): "Calendar day selection in the URL", "Calendar day navigation" (minus the ‹ › buttons — the `stepDay` method is here, the buttons are PR B / C), "Calendar view switch with a selected day", "Calendar selection history", "Calendar selection survives opening an entry" → Tasks 1–3. "Calendar day cell figures / empty and future / category dots / marks and accessible name" → Tasks 4–7. The grid half of "Calendar grid keyboard" (roving tabindex, ← → ↑ ↓ Home End, Enter / Space) → Task 6; `[` `]` need the day panel → PR B. Everything under "layout on wide / narrow screens", 本月摘要, day panel, drawer, swipes, 今天 bar → PR B / C.
- Type consistency: `CalendarMonthData`, `calendarParams`, `calendarWrite`, `compactNet`, `wideNet`, `heatRatio`, `maxDailyExpense`, `categoryColorMap`, `cellAriaLabel`, inputs `dots` / `invoiceDays` / `paidDays` / `compact` / `weekOf` are named the same in every task.
- Known test churn: Task 6 rewrites the three `calendar-month.spec.ts` tests that assert `.exp` / `.inc` / `.due-badge`; Task 7 adds the fake `CalendarDataService` to the timeline spec's `render()` so the other calendar tests stay untouched.
