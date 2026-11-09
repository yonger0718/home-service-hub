# Ledger Calendar PR C — Narrow Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Below 1024 px the 日曆 view shows one line of month figures under the navigator, the month grid with dots and a 今天 / 回到今天 bar when no day is selected, and — once a day is picked — the week strip (no dots) with a 展開整月 handle, the day header (‹ label ›, count, net) and the day's rows; horizontal swipes move one day (day area) or seven days (week strip), `[` `]` step the day, and vertical scrolling is never swallowed.

**Architecture:** The timeline component already renders the narrow 日曆 branch (grid + `.day-entries`) and owns every read; PR C changes only that branch. The day section reuses `CalendarDayPanelComponent` (PR B) fed from the timeline's unfolded day rows, with a `narrow` input that hides the 本月摘要 button and shows the swipe hint — so the bill line, header, empty / error / skeleton states and ＋ 在這天記一筆 exist once. `CalendarMonthComponent` already has `weekOf` (week mode) and `compact`; PR C binds `weekOf` below 1024 px, lets week-mode outside cells be picked, and sets the no-day compact cell height. Two small new units: an `appSwipeX` directive (horizontal-dominance gesture, `touch-action: pan-y`) and a `CalendarTodayBarComponent` (今天 / 回到今天). Entry detail stays its own route below 760 px and the right-hand sheet at 760–1023 (design D6); D2's URL restores week + day on return.

**Tech Stack:** Angular 21 standalone components, signals / computed / effect, `input()` / `output()` / `viewChild()`, Pointer Events, Router query params through `writeCalendarQuery` (`replaceUrl`, merge), Vitest (`npx ng test --watch=false --include='**/<file>.spec.ts'` from `frontend/`), `npx tsc -p tsconfig.app.json --noEmit`, `npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict`.

**Spec:** `openspec/changes/add-ledger-calendar-view/` — `design.md` D2, D6, D8; `specs/frontend-accounting-ledger/spec.md` requirements "Calendar layout on narrow screens", "Calendar layout on narrow screens: week strip", "Calendar swipes on narrow screens", plus the unchanged "Calendar day navigation" / "Crossing a month edge" and "Calendar grid keyboard" (`[` `]`). Artboard: `design_handoff_v3/design_refs/CalendarPhone.dc.html`. `tasks.md` §4 (4.1–4.4).

## Global Constraints

- Narrow calendar = `view() === 'calendar' && layoutMode.mode() !== 'panes'` — the existing `!wideCalendar()` branch; it covers phone (< 760) AND sheet (760–1023). Nothing at ≥ 1024 px (PR B) and nothing in the 清單 view changes; every new branch is gated on narrow calendar.
- Every `month` / `day` write goes through the timeline's `writeCalendar(month, day)` (→ `writeCalendarQuery`, merge + `replaceUrl`); `month` always follows `day` (`day.slice(0, 7)`).
- Week strip = `CalendarMonthComponent` with `[weekOf]="selectedDay()"` and `[compact]="true"`: the row containing the day, no dots, no legend, cells 52 px; the week follows `Preference.week_start` (0 Sunday / 1 Monday) as `buildCalendar` already does. No-day compact grid cells are 76 px tall (`.cal.compact:not(.week-mode) .cell { min-height: 76px }`).
- Swipe = pointer gesture whose horizontal movement dominates: `|dx| ≥ 48` px AND `|dx| ≥ 1.5 · |dy|`, measured pointerdown → pointerup; right-to-left (dx < 0) moves LATER (+1 / +7), left-to-right moves earlier. Host gets `touch-action: pan-y` and takes pointer capture on pointerdown (released on up/cancel) so a finger that leaves the 52 px strip still delivers its pointerup; a recognised swipe swallows the `click` that follows it (capture phase, `preventDefault` + `stopPropagation`) so the `<button>` under the finger (cell, row, ‹ ›, 重試, bill) does not navigate a second time. A gesture ending with a non-collapsed text selection, or started on `input, textarea, select, [contenteditable]`, is not a swipe. Day area → ±1 day; week strip → ±7 days (same weekday). Vertical movement scrolls the page.
- Week-mode keyboard (D8 scope): ← → and `[` `]` work across the 7 rendered cells (outside cells included); Home / End / ↑ ↓ whose target is not on the strip stay no-ops (`calendar-month.ts:182` refuses a missing button) — accepted, not a defect.
- Copy, verbatim: `展開整月` (button text; `aria-label="回到整月日曆"`), `今天 · {n} 筆`, `回到今天`, `左右滑動換一天`, `前一天` / `後一天` (aria-labels, existing), `＋ 在這天記一筆`, `這天沒有記錄` / `還沒到這天` / `這天沒有符合條件的記錄` (existing panel copy), one-line figures `支出` / `收入` / `結餘` with `摘要讀取失敗` + `重試`, scope hint `全月 · 未套篩選` (existing). Day header: `{dayLabel(date)}` + `{n} 筆 · {net}` (panel `.dp-label` / `.dp-sub`, existing).
- 今天 bar: shown only when no day is selected in the narrow calendar. In today's month it reads `今天 · {count} 筆`, the names (`TodayCard.names`), the net, and selects today on tap; in any other month it reads `回到今天` and writes `month = today.slice(0, 7), day = today`. `TodayCard` names load in the narrow calendar too (the `loadToday` gate drops its `panes` condition).
- Entry detail below 760 px is its own route (`/accounting/entries/{id}?month&day`), at 760–1023 the right-hand sheet — unchanged; on return the URL's `day` restores the week strip and rows.
- Fixtures are fictional; no owner data in tests, reviews or commits. Commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never run `npm run config` / `set-env.js` / `npm run build` / `npm start` in the worktree; `npx ng build --configuration development`, `npx tsc -p tsconfig.app.json --noEmit` and `npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict` are the checks. Test names containing an apostrophe use double quotes.

## Review Focus

1. A week that straddles a month edge (e.g. 10/29–11/04 with 10/31 selected): the strip shows 11/01–11/04 as outside cells; tapping 11/02 selects it and `month` becomes `2026-11` — pinned in Task 2 (pickable outside cells in week mode) and Task 5.
2. A diagonal or mostly vertical drag on the day list (dy ≥ dx / 1.5) is a scroll, not a swipe: no `day` write, `touch-action: pan-y` on the host — pinned in Task 1 and Task 6.
3. Swiping past the month edge: day area on 10/31 → `?month=2026-11&day=2026-11-01`; strip on 10/28 → `?month=2026-11&day=2026-11-04` — pinned in Task 6.
4. 回到今天 from another month before today's daily row exists: the URL is written first, the grid collapses to today's week, and the day section loads today's rows — pinned in Task 5.
5. Filters active on the phone: the one-line figures keep the `全月 · 未套篩選` hint, the empty day reads `這天沒有符合條件的記錄`, and the grid keeps the month's unfiltered dots — pinned in Task 5 and Task 6.
6. A swipe that ends on a `<button>` (week cell, `.dp-row`, ‹ ›, 重試) must not also fire that button's click, or a second `navigate` overwrites the step — pinned in Task 1 (click swallowed) and Task 6 (swipe over a row → only one navigate).
7. A qualifying swipe on the 52 px week strip whose finger drifts off the strip (dx 70 / dy 40) must still count — pinned in Task 1 (pointer capture; pointerup dispatched on `document`).
8. ArrowRight from 10/31 on the week strip must move focus and `tabindex="0"` onto the outside cell 11/01 (roving candidates include rendered outside cells in week mode) — pinned in Task 2.

---

## File map

| File | Responsibility |
|---|---|
| `frontend/src/app/components/accounting/swipe-x.directive.ts` (+ `.spec.ts`) — **new** | `appSwipeX`: horizontal-dominance pointer gesture → `(swipeX)` emits `1` (right-to-left) or `-1` (left-to-right); host `touch-action: pan-y`. |
| `frontend/src/app/components/accounting/calendar-month/calendar-month.{ts,html,scss}` (+ spec) | Week-mode outside cells pickable; compact no-day cells 76 px; otherwise unchanged. |
| `frontend/src/app/components/accounting/calendar-month/calendar-today-bar.{ts,html,scss}` (+ `.spec.ts`) — **new** | `app-calendar-today-bar`: 今天 / 回到今天 bar (presentational). |
| `frontend/src/app/components/accounting/calendar-day-panel/calendar-day-panel.{ts,html,scss}` (+ spec) | `narrow` input: hides the 本月摘要 button, shows `左右滑動換一天` under the rows. |
| `frontend/src/app/components/accounting/timeline/timeline.{ts,html,scss}` (+ spec) | Narrow 日曆 branch: one-line figures, `[weekOf]`, 展開整月, today bar, day panel reuse (replaces `.day-entries`), swipes, `stepDay(delta: number)`, `onAddEntry`. |
| `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts` | Phone route round-trip: entry ✕ restores the week strip and the day's rows. |
| `openspec/changes/add-ledger-calendar-view/tasks.md` | §4 ticks. |

---

### Task 1: `appSwipeX` directive

**Files:**
- Create: `frontend/src/app/components/accounting/swipe-x.directive.ts`, `swipe-x.directive.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  /** Horizontal swipe on the host: emits 1 for right-to-left (later), -1 for left-to-right (earlier). Vertical drags scroll. */
  @Directive({ selector: '[appSwipeX]', host: { style: 'touch-action: pan-y', '(pointerdown)': 'onDown($event)', '(pointerup)': 'onUp($event)', '(pointercancel)': 'reset()' } })
  export class SwipeXDirective { readonly swipeX = output<1 | -1>(); }
  export const SWIPE_MIN_DX = 48; export const SWIPE_DOMINANCE = 1.5;
  ```
  `onDown` records `{ x, y, pointerId }` unless `event.button !== 0`, the target is inside `input, textarea, select, [contenteditable]`, or `event.isPrimary === false`; then `host.setPointerCapture(pointerId)` (wrapped in try/catch — jsdom may lack it) so pointerup reaches the host after the finger leaves it. `onUp` with a matching `pointerId`: release capture, `dx = clientX − x`, `dy = clientY − y`; when `|dx| ≥ SWIPE_MIN_DX && |dx| ≥ SWIPE_DOMINANCE · |dy|` and `document.getSelection()?.isCollapsed !== false` → add a one-shot capture-phase `click` listener on the host (`{ capture: true, once: true }`, removed on the next macrotask via `setTimeout(…, 0)` if no click arrives) that calls `preventDefault()` + `stopPropagation()`, then emit; always resets.

- [ ] **Step 1: Failing tests** (`swipe-x.directive.spec.ts`, host `<div appSwipeX (swipeX)="got.push($event)" style="width:300px;height:200px">` rendered through a test component, events dispatched with `new PointerEvent(type, { clientX, clientY, pointerId: 1, isPrimary: true, button: 0, bubbles: true })`):

```ts
it('emits 1 for a right-to-left swipe', () => { /* down (200,100) → up (120,110) → [1] */ });
it('emits -1 for a left-to-right swipe', () => { /* down (100,100) → up (170,96) → [-1] */ });
it('ignores a short move', () => { /* down (100,100) → up (140,100): 40 px → [] */ });
it('ignores a drag whose vertical movement is not dominated (scroll)', () => { /* down (100,100) → up (160,150): dx 60, dy 50 → [] */ });
it('ignores a gesture that started on a text field', () => { /* down on a child <input> → up 100 px left → [] */ });
it('ignores a pointer id that did not start the gesture, and resets on pointercancel', () => {});
it('ignores a gesture that ends with a text selection', () => { /* stub document.getSelection → { isCollapsed: false }; down (200,100) → up (120,100) → [] */ });
it('swallows the click that follows a swipe on a child button (Review Focus 6)', () => { /* host contains <button (click)="clicks++">; down (200,100) on the button → up (120,100) on it → dispatch click on the button → got [1], clicks 0; a later plain click → clicks 1 */ });
it('still emits when pointerup lands outside the host after capture (Review Focus 7)', () => { /* spy host.setPointerCapture called with pointerId 1 on down; dispatch pointerup on document (120,140) with pointerId 1 → [1] (the directive listens on the host; with capture the browser retargets — in the test, dispatch the up on the host after asserting the capture call) */ });
it('sets touch-action: pan-y on the host', () => { /* host.style.touchAction === 'pan-y' */ });
```

- [ ] **Step 2: Run — FAIL** (`npx ng test --watch=false --include='**/swipe-x.directive.spec.ts'`).  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): appSwipeX horizontal-dominance swipe directive (#318 D6)`

---

### Task 2: Calendar grid — week-mode outside cells and compact cell height

**Files:**
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-month.ts` (`pick`, cell view), `calendar-month.html` (`[disabled]`), `calendar-month.scss`, `calendar-month.spec.ts`

**Interfaces:**
- Consumes: existing `weekOf`, `compact`, `daySelected`, `pick(cell)`.
- Produces: in week mode (`weekOf() !== null`) an outside cell is enabled (`calendar-month.html:24` `[disabled]="cell.outside && !weekOf()"`), gets `aria-pressed` like any cell (`:21` → `cell.outside && !weekOf() ? null : cell.date === selected()`), keeps its `outside` class (greyed, no figures, no dots) and `pick(cell)` (`calendar-month.ts:189`, guard becomes `!cell.outside || this.weekOf()`) emits its date; the roving `tabDate` candidates (`calendar-month.ts:81`) become `cells.filter(cell => !cell.outside || weekOf)` so ArrowRight / `focusDate` can land on a rendered outside cell in week mode (Review Focus 8). Month mode unchanged. SCSS: add `.cal.compact:not(.week-mode) .cell { min-height: 76px; }` next to the existing `.cal:not(.compact) .cell { min-height: 76px }` / `.week-mode .cell { min-height: 52px }` rules (`calendar-month.scss:199–205`); week mode stays 52 px.

- [ ] **Step 1: Failing tests** (`calendar-month.spec.ts`):

```ts
it('week mode: an outside cell is enabled and emits its date when picked', () => { /* month 2026-10, weekStart 0, weekOf 2026-10-01 (week 09/27–10/03): cell('2026-09-28') not disabled, aria-pressed 'false', click → daySelected '2026-09-28' */ });
it('month mode: an outside cell stays disabled', () => { /* no weekOf → cell('2026-09-28').disabled === true, no aria-pressed */ });
it('week mode: ArrowRight from 10/31 moves focus and tabindex to the outside cell 11/01 (Review Focus 8)', () => { /* month 2026-10, weekStart 1 (Monday), weekOf 2026-10-31 → strip 10/26–11/01; keydown ArrowRight on cell 2026-10-31 → document.activeElement is cell 2026-11-01 and its tabindex is '0' */ });
```
The 76 px rule has no unit test (CSS); the task reviewer checks the stylesheet diff.

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): calendar week strip picks neighbour-month days; 76 px compact month cells (#318 D6)`

---

### Task 3: `CalendarTodayBarComponent`

**Files:**
- Create: `frontend/src/app/components/accounting/calendar-month/calendar-today-bar.ts`, `.html`, `.scss`, `calendar-today-bar.spec.ts`

**Interfaces:**
- Consumes: `TodayCard` (`import type` from `./calendar-pane.service`: `{ date, count, names, netText, net }`).
- Produces:
  ```ts
  @Component({ selector: 'app-calendar-today-bar', … })
  export class CalendarTodayBarComponent {
    /** Today's card (null while loading or in another month). */ readonly card = input<TodayCard | null>(null);
    /** True when the shown month is today's month. */ readonly current = input.required<boolean>();
    /** Today's ISO date (for the disc when the card is null). */ readonly today = input.required<string>();
    readonly pick = output<void>();
  }
  ```
  Markup: `<button type="button" class="today-bar" (click)="pick.emit()">` → `<span class="tb-disc">{{ day of month }}</span>`, then when `current()`: `<b class="tb-title">今天 · {{ card()?.count ?? 0 }} 筆</b> <small class="tb-names">{{ card()?.names }}</small> <b class="tb-net" [class.neg]="(card()?.net ?? 0) < 0" [class.pos]="(card()?.net ?? 0) > 0">{{ card()?.netText }}</b>`; when `!current()`: `<b class="tb-title">回到今天</b>`; always a trailing `<span class="tb-chev" aria-hidden="true">›</span>`. `aria-label`: current → `今天 · {count} 筆，{netText}`; other month → `回到今天`. Styles: full-width row, disc 32 px with `--app-primary-soft` background, names ellipsized, net `tabular-nums`.

- [ ] **Step 1: Failing tests**:

```ts
it('reads 今天 · 3 筆, the names and the net in the current month', () => { /* card {count 3, names '全聯、電費、Netflix', netText '−$250', net -250}, current true → text contains '今天 · 3 筆', '全聯、電費、Netflix', '−$250'; .tb-net has .neg */ });
it('reads 回到今天 in another month', () => { /* current false → text '回到今天', no 筆 */ });
it('emits pick on click', () => {});
it('shows 0 筆 while the card is null in the current month', () => {});
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): CalendarTodayBarComponent — 今天 / 回到今天 bar for the narrow calendar (#318 D6)`

---

### Task 4: Day panel `narrow` input

**Files:**
- Modify: `frontend/src/app/components/accounting/calendar-day-panel/calendar-day-panel.ts`, `.html`, `.scss`, `calendar-day-panel.spec.ts`

**Interfaces:**
- Produces: `readonly narrow = input(false);` — when true: the `.dp-summary` (本月摘要) button is not rendered; after the rows / empty block in day mode a `<p class="dp-swipe-hint">左右滑動換一天</p>` is rendered; host class `dp--narrow` (SCSS: no pane padding-top, header without the summary slot). Everything else (‹ › `前一天` / `後一天`, `[` `]` host keys, bill line, states, ＋ 在這天記一筆, outputs) unchanged.

- [ ] **Step 1: Failing tests**:

```ts
it('narrow: hides the 本月摘要 button and shows the swipe hint', () => { /* narrow true, mode day → no .dp-summary; .dp-swipe-hint text 左右滑動換一天 */ });
it('wide (default): keeps 本月摘要 and has no swipe hint', () => {});
```

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): day panel narrow variant (no 本月摘要 button, swipe hint) (#318 D6)`

---

### Task 5: Timeline narrow calendar — figures line, week strip, 展開整月, 今天 bar

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.html`, `timeline.scss`, `timeline.spec.ts`

**Interfaces:**
- Consumes: `CalendarTodayBarComponent` (Task 3), `CalendarMonthComponent` `weekOf` (Task 2).
- Produces (timeline.ts):
  - `readonly narrowCalendar = computed(() => this.view() === 'calendar' && this.layoutMode.mode() !== 'panes');` (= `!wideCalendar()` inside the calendar branch; name it for templates and tests).
  - `protected readonly todayCard` — the existing private computed made `protected`; the `loadToday` effect gate becomes `this.view() === 'calendar' && this.month() === this.today().slice(0, 7)` (no `panes` condition) so names load on phone and sheet too.
  - `readonly isCurrentMonth = computed(() => this.month() === this.today().slice(0, 7));`
  - `goToday(): void` — `const t = this.today(); this.writeCalendar(t.slice(0, 7), t);` (used by the bar in both months; in the current month it equals selecting today).
  - `stepDay(delta: number): void` — generalised from `1 | -1` (Task 6 passes ±7); body unchanged (`addDays(day, delta)` → `writeCalendar(next.slice(0, 7), next)`).
- Template (timeline.html, calendar branch):
  - Under the monthbar, when `narrowCalendar()`: `<dl class="cal-figures" aria-label="本月摘要" [attr.aria-busy]="summaryLoading() ? 'true' : null">` with the same dt/dd pairs and classes as `.monthbar-figures` (`sum-expense` / `sum-income` / `sum-net`, error → `摘要讀取失敗` + `button.retry.sum-retry` 重試), plus `<small class="sum-scope">全月 · 未套篩選</small>` when `filtersActive()`. The `.sum` card is no longer rendered when `narrowCalendar()` (`@if (!wideCalendar() && view() !== 'calendar')` → i.e. `.sum` only in 清單 below 1024, exactly as today for 清單).
  - `<app-calendar-month … [weekOf]="narrowCalendar() ? selectedDay() : null" …>` (existing `[compact]="layoutMode.mode() !== 'panes'"` stays).
  - After the grid, when `narrowCalendar() && selectedDay()`: `<button type="button" class="expand-month" aria-label="回到整月日曆" (click)="clearDay()"><span class="expand-handle" aria-hidden="true"></span>展開整月</button>`.
  - After the grid, when `narrowCalendar() && !selectedDay()`: `<app-calendar-today-bar [card]="todayCard()" [current]="isCurrentMonth()" [today]="today()" (pick)="goToday()" />`.
  - The existing `.day-entries` block stays in this task (Task 6 replaces it).
- SCSS: `.cal-figures` (flex, wrap, gap 4px 16px, dt muted 700 `--fs-micro`, dd tabular, margin 2px 0 6px), `.expand-month` (full width, centered, `--fs-label`, muted, with a 36 × 4 px rounded handle above the text), `.sum-scope` reused.

- [ ] **Step 1: Failing tests** (`timeline.spec.ts`; existing helpers: `render(mode, preference?, providers?)` → `{ fixture, el }`, `viewButton(el, '日曆')`, `flushSummary(month)`, `flushDaily(month)`, `flushEntries(items)`, `flushTodayRead(items)` / `isTodayRead(req)`; the narrow 日曆 is reached with `render('phone')` + `viewButton(el, '日曆')` or a `?day=` route stub, as the existing phone 日曆 tests do; sheet via `render('sheet')`):

```ts
it('narrow 日曆 shows one line of figures under the navigator and no summary card', () => { /* .cal-figures with 支出/收入/結餘 texts from flushSummary; .sum null; .monthbar-figures null */ });
it('narrow 日曆 figures show the error line with 重試', () => { /* summary 500 → .cal-figures text contains 摘要讀取失敗; click .sum-retry → new GET summary */ });
it('narrow 日曆 keeps the 全月 · 未套篩選 hint when filters are active (Review Focus 5)', () => {});
it('清單 below 1024 still shows the summary card', () => { /* render phone, list view → .sum present, .cal-figures null */ });
it('phone default: 42 cells with dots and a 「今天 · 3 筆 … −$250」 bar, no rows (spec Phone default)', () => { /* count / net come from the DAILY summary (todayCard, timeline.ts:999), names from the limit-3 read: flushDaily(today month, { days: [{ date: today, count: 3, expense: '-1250', income: '1000', … }] }) + flushTodayRead(3 entries) → app-calendar-today-bar text contains 今天 · 3 筆, the three names, −$250; 42 button.cell; no .dp-row; no app-calendar-day-panel */ });
it('tapping the 今天 bar selects today', () => { /* today 2026-10-22 → navigate [] {month 2026-10, day 2026-10-22} replaceUrl */ });
it('in another month the bar reads 回到今天 and writes month and day to today (spec 回到今天; Review Focus 4)', () => { /* month 2026-09 → bar text 回到今天; click → ?month=2026-10&day=2026-10-22; flushDaily('2026-10') → 7 cells incl. 2026-10-22 with .sel; flushEntries(today's rows) → app-calendar-day-panel lists them (in Task 5 the old .day-entries rows; Task 6 re-asserts on .dp-row) */ });
it('picking a day collapses the grid to its week without dots and shows 展開整月', () => { /* selectDay 2026-10-21, weekStart 0 → 7 cells 10/18…10/24, no .dot, button.expand-month[aria-label="回到整月日曆"] text 展開整月, no today bar */ });
it('展開整月 clears the day and restores the month grid', () => { /* click → writeCalendar(month, null) → 42 cells, today bar back */ });
it('tapping a neighbour-month day in the strip selects it and month follows (Review Focus 1)', () => { /* day 2026-10-01 (week 09/27–10/03) → click cell 2026-09-28 → navigate {month 2026-09, day 2026-09-28} */ });
it('sheet mode (760–1023) gets the same narrow calendar', () => { /* render('sheet') + 日曆 → .cal-figures present, today bar present */ });
it('wide 日曆 is unchanged: no .cal-figures, no today bar, no weekOf', () => { /* render('panes', makePreference(), [layout stub]) + 日曆 + a selected day → 42 cells, .monthbar-figures present */ });
```
Migrate the existing phone tests that assert `.sum` inside the 日曆 view (around `timeline.spec.ts` 1902–1919) to `.cal-figures`; the `.day-entries` assertions stay until Task 6.
**Suite hazard (fix in this task):** with the `panes` gate gone, every phone/sheet 日曆 of the current month (the suite's today is 2026-10-02) issues the limit-3 read, again on `entriesChanged` / preference changes. `flushEntries` (`timeline.spec.ts:126`) and `expectNoEntryPage` (`:139`) match every `/api/accounting/entries` request, so they would collide with or swallow it: change both to `r.url === '/api/accounting/entries' && !isTodayRead(r)`, and rewrite the `isTodayRead` doc comment at `:92` (no longer "two-pane only"). Tests that render a narrow current-month 日曆 call `flushTodayRead()` where `render()` does not already (`:88`).

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc.**

- [ ] **Step 5: Commit** — `feat(spa): narrow 日曆 — one-line figures, week strip with 展開整月, 今天 / 回到今天 bar (#318 D6)`

---

### Task 6: Timeline narrow calendar — day panel reuse and swipes

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.html`, `timeline.scss`, `timeline.spec.ts`

**Interfaces:**
- Consumes: `CalendarDayPanelComponent` `narrow` (Task 4), `SwipeXDirective` (Task 1), `stepDay(delta: number)` (Task 5).
- Produces (timeline.ts): `readonly paneDayRows = computed(() => this.paneDayGroup()?.rows ?? [])`, `readonly paneDayNet = computed<DayNet | null>(() => dayNetOf(this.paneDayGroup(), this.mainCurrency()))` and `readonly dayState = computed<OverviewState>(() => this.dayError() ? 'error' : this.dayLoading() && this.dayEntries().length === 0 ? 'loading' : 'ready')` — extracted from the three publish effects at `timeline.ts:917–919`, which then read them (`pane.dayRows.set(this.paneDayRows())` etc.); `onAddEntry(date: string): void` → `void this.router.navigate(['/accounting/entry'], { queryParams: { date, ...calendarParams(this.router.routerState.snapshot.root.queryParamMap) } })` (the layout's `onAddEntry` at `accounting-layout.ts:457` is the model; the query read follows `timeline.ts:1303`). `open(row)` (`timeline.ts:1717`) is reused for the panel's `open` output (rows are unfolded, so no batch rows reach it).
- Template: the `.day-entries` block is replaced by
  ```html
  @if (narrowCalendar() && selectedDay()) {
    <div class="day-area" appSwipeX (swipeX)="stepDay($event)">
      <app-calendar-day-panel [narrow]="true" mode="day" [day]="selectedDay()" [today]="today()" [rows]="paneDayRows()" [net]="paneDayNet()"
        [state]="dayState()" [billLines]="billLines()" [currency]="mainCurrency()" [filtered]="filtersActive()" [selectedId]="selectedId()"
        (step)="stepDay($event)" (open)="open($event)" (add)="onAddEntry($event)" (openCard)="openCard($event)" (retry)="retryDay()" />
    </div>
  }
  ```
  (`selectedId` is the timeline's existing signal at `timeline.ts:568`; the panel sets `aria-current` from it, which matters on the 760–1023 sheet where the strip stays on screen under the open detail.) The strip swipe attaches the directive **only in week mode**: `@if (narrowCalendar() && selectedDay()) { <div class="strip-area" appSwipeX (swipeX)="stepDay(7 * $event)"> <ng-container *ngTemplateOutlet="grid" /> </div> } @else { <ng-container *ngTemplateOutlet="grid" /> }` with one `<ng-template #grid><app-calendar-month …/></ng-template>` — a single component instance definition, no `appSwipeX` / `touch-action` on the wide or month grid. The narrow branch (`@if (selectedDay() && !wideCalendar())`) was the only consumer of `.day-entries`; delete that template block only. **Delete no SCSS**: `timeline.scss` has no `.day-entries` / `.day-net` rule, and `.day` (`:146`) / `.day-total` (`:160`) / `.bills` / `.bill` (`:247–`) also style the 清單 list branch (`timeline.html:266–268`).
- Behaviour: `stepDay($event)` from the panel's `[` `]` / ‹ › and from the day-area swipe; `stepDay(7 * $event)` from the strip swipe; `month` follows (`writeCalendar(next.slice(0, 7), next)`).

- [ ] **Step 1: Failing tests** (`timeline.spec.ts`, phone unless noted; swipe helper `swipe(el, from, to)` dispatching `pointerdown` / `pointerup` PointerEvents on the element):

```ts
it("picking 10/21 lists its rows under 「10/21 週三」 with the count and net (spec Picking a day on a phone)", () => { /* app-calendar-day-panel .dp-label '10/21 週三', .dp-sub '3 筆 · −$3,096'; three .dp-row; no .dp-summary */ });
it('the day panel is unfolded, shows the bill line, the empty / future / filtered copy and the error block with 重試', () => { /* migrate the old .day-entries assertions: skeleton, 這天沒有記錄, 這天沒有符合條件的記錄 with a query, 還沒到這天 for a future day, .bills before the rows, 記錄讀取失敗 + 重試 → retryDay GET */ });
it('a row opens the entry route with month and day', () => { /* on the seeded-URL harness used at timeline.spec.ts:1646 (`openAt`-style, the URL snapshot carries month/day BEFORE the navigate spy is installed): click .dp-row → navigate ['/accounting/entries', id] with queryParams {month, day}. The older test at :1415 expects {} because its spy precedes writeCalendar — leave it (or move it onto the seeded harness); do NOT change open() */ });
it('a swipe that ends on a row navigates once (Review Focus 6)', () => { /* day 2026-10-21; pointerdown on a .dp-row (200,300) → pointerup (120,305) on it → click on it → navigate called once, with {day: 2026-10-22}; no ['/accounting/entries', …] */ });
it('＋ 在這天記一筆 opens the form dated that day with month and day', () => {});
it('swiping the day area right to left selects the next day (spec Swiping to the next day)', () => { /* day 2026-10-21, swipe (200,300)→(120,305) → navigate {month 2026-10, day 2026-10-22}; flushDayEntries 10/22 → rows */ });
it('swiping the day area left to right selects the previous day', () => {});
it('a vertical drag on the day area does not change the day (Review Focus 2)', () => { /* (200,300)→(205,180) → no navigate; .day-area style touch-action pan-y */ });
it('swiping the week strip moves seven days and keeps the weekday; month follows at the edge (Review Focus 3)', () => { /* day 2026-10-28 (Wed), strip swipe right-to-left → {month 2026-11, day 2026-11-04}; day 2026-10-31 day-area swipe → {month 2026-11, day 2026-11-01} */ });
it('a swipe on the month grid with no day selected does nothing', () => {});
it('[ and ] from the day panel step the day', () => { /* keydown ']' on app-calendar-day-panel host → {day 2026-10-22} */ });
it('wide 日曆 has no .day-area and no appSwipeX', () => {});
```
Migrate every remaining `.day-entries` / `.day-net` / `.day-total` assertion in `timeline.spec.ts` (find them with `grep -nE 'day-entries|day-net|day-total' timeline.spec.ts`; ~40 hits, most inside the narrow 日曆 describes) to the panel selectors (`app-calendar-day-panel`, `.dp-row`, `.dp-empty`, `.dp-error-block` / `.dp-error`, `.dp-retry`, `.bills .bill`, `app-skeleton`, `.dp-sub .net-hint`, `.dp-add`); keep each test's intent (the day toggled off → panel absent; 7 cells instead of 42 while a day is selected). `.day-net b` and its `.neg` class have no panel counterpart (the net is inline text in `.dp-sub`, muted): assert the `.dp-sub` text, drop the `.neg` assertion. 還沒到這天 for a future day is new on narrow screens (the old template had no future copy) — add that assertion rather than migrate one. `.sum` assertions in the 清單 describes stay as they are.

- [ ] **Step 2: Run — FAIL.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run — PASS; tsc; `npx ng build --configuration development` (the timeline stays the owner; `calendar-day-panel` and `swipe-x` import only types from timeline).**

- [ ] **Step 5: Commit** — `feat(spa): narrow 日曆 day section reuses the day panel; swipes move a day or a week (#318 D6)`

---

### Task 7: Phone route round-trip — back from an entry restores the week and the day

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Consumes: `start('phone', url, 390)`, `find`, the real routes (timeline through `<router-outlet>`, entry detail as its own route), `HttpTestingController` flushes for summary / daily / day entries / detail.

- [ ] **Step 1: Failing test** (phone, `localStorage['hh.accounting.timelineView'] = 'calendar'`):

```ts
it("opening a row at 390 px and closing the entry restores the week strip and the day's rows (tasks 4.4)", async () => {
  /* start('phone', '/accounting?month=2026-10&day=2026-10-21', 390); flush summary/daily/day entries → 7 button.cell, app-calendar-day-panel with 3 .dp-row;
     click a .dp-row → router.url '/accounting/entries/1?month=2026-10&day=2026-10-21', the page is the entry detail (no app-calendar-month);
     flush the detail; click its ✕ (.close[aria-label="關閉"]) → router.url '/accounting?month=2026-10&day=2026-10-21'; 7 cells, cell 2026-10-21 has .sel and aria-pressed true, 3 .dp-row again (re-flushed) */
});
it('at 820 px the row opens the right-hand sheet, the strip stays and the opened row is marked', async () => { /* start('sheet', same url, 820) → click .dp-row → .detail-pane[aria-modal] with the detail; the list pane still has 7 cells; that .dp-row has aria-current="true" (selectedId binding, Task 6) */ });
```

- [ ] **Step 2: Run — FAIL** (before Task 6 the panel does not exist on the phone; after it, the test should pass — if it passes immediately, that is the expected result of Tasks 5–6; keep it as the regression pin and say so in the report).  - [ ] **Step 3: Implement** nothing unless the test exposes a defect; fix in the owning file if so. The existing layout-spec assertions on `.list-pane .sum` / `.list-pane .day-entries` (`accounting-layout.spec.ts:93, 116, 123`) are panes-mode and must keep passing (`.day-entries` absent is still true once the block is gone).  - [ ] **Step 4: Run — PASS.**

- [ ] **Step 5: Commit** — `test(spa): phone and sheet calendar round-trips keep the week strip and day (#318 D2/D6)`

---

### Task 8: openspec ticks and final checks

**Files:**
- Modify: `openspec/changes/add-ledger-calendar-view/tasks.md` (§4: 4.1–4.4 → `[x]`; leave §5 unticked)

- [ ] **Step 1: Tick §4.**
- [ ] **Step 2: `cd frontend && npx tsc -p tsconfig.app.json --noEmit`** — clean.
- [ ] **Step 3: `npx ng test --watch=false`** — all pass (the pre-existing flaky `accounting-layout.spec` 1280 px row test may time out; re-run once; report).
- [ ] **Step 4: `npx ng build --configuration development`** — succeeds; the timeline remains a lazy chunk.
- [ ] **Step 5: `cd .. && npx -y @fission-ai/openspec@latest validate add-ledger-calendar-view --strict`** — valid.
- [ ] **Step 6: Commit** — `docs(openspec): tick calendar PR C tasks`

---

## Rulings recorded in this plan

- Narrow = phone AND sheet (spec says "below 1024 px"); the sheet keeps opening the detail on the right.
- Tapping the selected day again in the week strip keeps today's toggle behaviour (`selectDay` clears the day → the month grid returns); 展開整月 is the explicit affordance, the toggle is the shortcut. Consistent with the wide grid.
- The day section reuses `CalendarDayPanelComponent` (unfolded rows, as ruled for PR B) instead of the old `.day-entries` list; the bill line markup therefore exists once (closes the PR B T5-3 note).
- Week-mode outside cells become pickable (Review Focus 1); in month mode they stay disabled.
- Swipe thresholds: 48 px and 1.5× dominance; pointer events WITH pointer capture (a finger drifting off the 52 px strip still completes), `touch-action: pan-y`, and the click following a recognised swipe is swallowed so the button under the finger does not navigate a second time (plan round AGENT-497 findings 1–2).
- The month grid without a day has no swipe (a month swipe is not in the spec; ‹ › remain the month controls); the directive is attached only in week mode, never on the wide grid.
- `.day-entries` removal deletes template only; `timeline.scss` `.day` / `.day-total` / `.bills` / `.bill` stay (the 清單 uses them).
- Week-mode outside cells are pickable AND reachable by the roving tabindex (AGENT-497 finding 3).

## Owner device check (after the SPA release, iOS / WebKit only — cannot run here)

- A vertical drag that starts on a row or on the week strip scrolls the page.
- A horizontal swipe on the day area changes the day; on the strip, the week.
- The screen-edge back swipe still wins only from the screen edge.
The click-swallow and pointer-capture behaviour is unit-tested and does not wait for the device.
