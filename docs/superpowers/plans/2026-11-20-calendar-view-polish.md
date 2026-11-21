# Calendar view polish (PR 1) Implementation Plan

_Rev 2 (2026-11-20): AGENT-537 findings folded — `rowHidden` / `chipsHidden` split, resize across 1024 px, drawer body completeness and scroll container, invoice hint state, existing specs named, debounce commit path._

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring 記帳記錄 › 日曆 in line with the adjusted artboards: a month grid of only the needed weeks, quieter day cells with text tags, one search / filter entry point, a compact entry drawer, and clickable 這個月要處理 rows.

**Architecture:** SPA only (`frontend/`), no API change. The month grid (`calendar-month`) changes shape and marks. The timeline keeps owning filter state and the one `<app-filter-bar>`; in 日曆 its row is hidden (≥ 1024 px) or collapsed behind a title-row icon (< 1024 px), and the right pane's 篩選 button opens the same panel through `CalendarPaneService` commands. `entry-detail` gains a `drawer` presentation that is on only inside the calendar drawer.

**Tech Stack:** Angular 21 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with the `--app-*` tokens of `frontend/src/styles.scss`.

**Spec:** the owner's list of 2026-11-20 (items 1–6, quoted per task) and the artboards `design_handoff_v3/design_refs/CalendarFixDesktop.dc.html` / `CalendarFixPhone.dc.html`. Requirements that change live in `openspec/specs/frontend-accounting-ledger/spec.md` and are edited in Task 8.

## Global Constraints

- Layout, spacing and copy follow the two artboards. Colours come from `--app-*` tokens; no hex value is copied from an artboard. Artboard token → app token: `primary` → `--app-primary`, `primarySoft` → `--app-primary-soft`, `warnInk` / `warnSoft` → `--app-warning` / `color-mix(in srgb, var(--app-warning) 12%, transparent)`, `muted` → `--app-text-muted`, `line` / `border` → `--app-border`, `soft` / `soft2` → `--app-surface-soft` (use the token the neighbouring rules already use when a name differs; add no new global token).
- The bell and its count are not touched (scope of `add-notification-center`).
- 清單 mode renders exactly as today (filter bar row, account select, chips, match row).
- The week strip (< 1024 px with a day selected) keeps its adjacent-month days: they stay pickable there (existing requirement "week strip"). Only the month grid loses them.
- Entry detail opened from 清單, the sheet (760–1023 px), a phone page, or any non-calendar entry point renders exactly as today.
- No new dependency. No change under `services/`.
- Tests use fictional names and amounts.
- Commit per task, message in English, trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Rulings made while planning (owner may overrule)

1. **Marks the artboards do not draw stay.** 已繳 (paid due day) and 發票待對帳 keep their place: 已繳 uses the 結帳 tag's neutral style, 發票待對帳 stays the text line under the dots (wide) / a dot (compact). Tag order in the cell's top-right: 帳單 or 已繳 first, then 結帳.
2. **Compact cells (< 1024 px) keep dots for marks**, as the phone artboard draws: warning-colour dot for 帳單, muted dot for 結帳 (new) and 已繳, existing dot for invoices.
3. **Future days:** thin border at ≥ 1024 px (desktop artboard), no border below (phone artboard). Past days and today get the fill at every width.
4. **Heat bar** minimum fill is 6 px at every width (the owner's text; the phone artboard's 4 px is not followed).
5. **≥ 1024 px:** the search box is always at the top of the right pane, also with a day selected. Typing while a day is selected clears the day (so the results show); Esc / clearing the text returns to 本月摘要. Active filter chips render under the search row in the right pane; the 清除篩選 row above the grid is not shown there.
6. **Account filter in 日曆:** it moves into the filter panel as its first field at every width, counts toward 「篩選 N」 and gets a chip (`帳戶：<name>`). In 清單 it stays inline and chip-less.
7. **< 1024 px 日曆:** one icon button 「搜尋與篩選」 in the title row (left of the gear on phones). It toggles the existing filter bar row (search + 篩選 button) under the title; closed by default each time 日曆 is entered; it shows a dot when a search text or filter is active, and opens by itself when 日曆 is entered with one active. The 「全月 · 未套篩選」 note and 清除篩選 keep showing while filters are active.
8. **Drawer top bar:** 上一筆 / 下一筆 buttons, the hint 「↑↓ 切換」 and ✕ (the artboard's bar). The 「第 n / N 筆」 counter is not built: the drawer does not know the month's order.
9. **Drawer fields:** the two-column table lists 帳戶, 商家 (對象 for debts), 日期, 時間 first, then 專案 / 交易類型 / 事件類型 only when they carry something other than their default (project set; kind not plain expense / income; event kind not 單次). Nothing else the full page shows is lost: lock banner, the ← 多類別 group-back link, schedule line, FX line and fee note, 入帳日 line, chips, debt block, 結清記錄 list, reward section, invoice card, related records, notes and every inline panel stay in the scrolling body. The category path: `.dkind` shows the full path (`支出 · 餐飲 › 午餐`), not only the leaf.
10. **Invoice hint:** in the drawer the idle 連結發票… row reads the picker once (`EinvoiceService.picker`) to show 「前後 7 天有 N 張可選」 / 「前後 7 天沒有可選的發票」; a failed or pending read shows no hint. Clicking the row opens the picker from that same result (no second read).
11. **這個月要處理:** bill rows read `[帳單] <card> <amount> · M/D 到期 ›`; invoice rows read `[發票] M/D <seller> <amount> ›` with the neutral tag. Click behaviour is unchanged (bill → that day; invoice → `openInvoice`).
12. **Item 4 test:** vitest runs in jsdom and cannot measure layout, so the "今天 bar visible at 390 × 844" check is a Playwright probe against the built SPA (evidence for the PR), plus unit tests of its preconditions (5 week rows for a 5-week month; no filter rows rendered in narrow 日曆 while collapsed). The probe runs a 5-row month and a 6-row month (2026-08 with Sunday first; 2026-11 with Monday first). By the phone artboard's measures a sixth row (+78 px) still leaves the bar above the tab bar; if the probe shows it does not in the real page, compact month cells drop to 64 px in a 6-row month only (class `rows-6` on `.cal`), and the probe must then pass.

## Review Focus

- A month that needs 6 rows (e.g. 2026-08 with Sunday first) and one that needs 4 (2026-02 with Sunday first): grid rows, keyboard ↑/↓/Home/End at the edges, and the tab stop.
- Week strip in the first / last week of a month: adjacent-month days still render and are pickable.
- Filter panel opened from the right pane's button: position, focus return to that button, outside-click closing without an immediate re-open, Esc.
- A filter active when switching 清單 ↔ 日曆 and when resizing across 1024 px: no filter is lost, the account chip appears / disappears with the mode.
- Drawer: entry with every optional block (debt, reward, linked invoice, schedule, inline delete / refund panel open) — the action bar stays at the bottom and the inline panel is reachable.

---

### Task 1: Month grid renders only the month's weeks

Owner item 1: 「月曆只畫本月需要的週數（4–6 排），不要顯示下個月的日期；月初前的空格留白。」

**Files:**
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-math.ts`, `calendar-math.spec.ts`
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-month.ts`, `calendar-month.html`, `calendar-month.scss`, `calendar-month.spec.ts`

**Interfaces:**
- Produces: `buildCalendar(month, days, weekStart): CalendarCell[][]` now returns `ceil((lead + daysInMonth) / 7)` rows (4–6); `CALENDAR_ROWS` is removed. Outside cells are still produced (leading and the tail of the last week) with `outside: true`.
- Produces: in the template an outside cell renders as `<span class="cell-blank" aria-hidden="true"></span>` unless `weekOf()` is set (week mode keeps the `button.cell.outside` as today).

- [ ] **Step 1: Failing tests.** `calendar-math.spec.ts`: 2026-11 / weekStart 0 → 5 rows, first cell `2026-11-01`, last row ends `2026-12-05` with `outside` true for the five trailing cells; 2026-02 / weekStart 0 → 4 rows, no outside cell; 2026-08 / weekStart 0 → 6 rows; 2026-11 / weekStart 1 → 6 rows (lead 6). Replace the existing "always 42 cells" expectations. `calendar-month.spec.ts`: month mode 2026-11 → 5 `.week`, 30 `button.cell`, 5 `.cell-blank`, no `button.cell.outside`, no element with text of a December day; week mode `weekOf: '2026-11-30'` → 7 `button.cell`, of which 5 `.outside` and enabled.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/accounting/calendar-month/*.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** Row count from `lead` and the month length; template `@if (cell.outside && !weekOf())` blank span, else the button. `.cell-blank` has no border, background or content. Keyboard: `onGridKeydown` already refuses a target without a button — keep; `tabDate` unchanged.
- [ ] **Step 4: Run** the same command — Expected: PASS. Existing cases that encode the 6 × 7 button grid are rewritten in Step 1, not left to fail: `calendar-month.spec.ts:61` (42 `button.cell`), `:70` (first button `2026-09-28`), `:96-120` (month-mode outside cells as disabled buttons; the `cell()` helper queries `button.cell`), `calendar-math.spec.ts:37` and `:55`. The spec's `render()` hardcodes month `2026-10`: give it a `month` parameter. Then `--include='src/app/components/accounting/timeline/timeline.spec.ts'` — fix expectations that counted 42 cells or disabled outside buttons in month mode.
- [ ] **Step 5: Commit** `feat(calendar): month grid draws only the weeks the month needs`.

### Task 2: Day cell look — fills, in-cell heat track, text tags, legend

Owner item 2.

**Files:**
- Modify: `calendar-month.ts`, `calendar-month.html`, `calendar-month.scss`, `calendar-month.spec.ts`, `calendar-math.ts` (`cellAriaLabel`), `calendar-math.spec.ts`

**Interfaces:**
- `CalendarCellView` gains nothing new; `closing` stays. `cellAriaLabel` gains `closing: boolean` and appends `信用卡結帳日` after the bill part.

- [ ] **Step 1: Failing tests** (`calendar-month.spec.ts`, wide unless said):
  - past day and today carry class `past` (today also `today`), a future day carries `future` and not `past`;
  - a day with spend renders `.heat-track > .heat` with `style.width` = ratio × 100 %; a day without spend renders no `.heat-track`; a future day renders none;
  - closing day renders `.tag.tag-closing` with text `結帳` and has no `closing` box-shadow class rule (assert the scss source no longer contains `inset 0 2px 0`);
  - due day renders `.tag.tag-bill` `帳單`; paid due day `.tag.tag-paid` `已繳`; a day that is both due and closing renders both, bill first;
  - compact: `.bill-dot` for a due day, `.closing-dot` for a closing day, no `.tag`;
  - legend items read exactly `當天支出（相對本月最高）`, `類別`, `帳單` + `到期日`, `結帳` + `信用卡結帳日`; sr-only text `色條為當天支出相對本月最高的比例，圓點為類別，帳單為繳費到期日，結帳為信用卡結帳日`;
  - `cellAriaLabel` with `closing: true` ends `，信用卡結帳日`.
- [ ] **Step 2: Run** the calendar-month specs — Expected: FAIL.
- [ ] **Step 3: Implement.** Markup per the desktop artboard: `.cell-head` row (date number left, `.tags` right), `.net`, `.dots`, optional `.inv-mark`, `.heat-track` absolutely placed `left/right: 9px; bottom: 6px; height: 4px` (compact: `6px / 6px / 4px / 3px`) with `overflow: hidden` and the fill `min-width: 6px`. `.cell.past { background: var(--app-surface-soft) }` (use the token the page's soft fills use), `.cell.future` wide: `border: 1px solid var(--app-border); background: transparent`; compact: no border. Drop the `opacity` on `.future`; the future date number uses `--app-text-muted`. Cell radius 12 px (compact 11 px), wide `min-height: 112px`, compact month `74px`, week mode unchanged. Tag: `font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 6px`.
- [ ] **Step 4: Run** — Expected: PASS; run `timeline.spec.ts` and fix selectors that named `.bill-mark` / the old legend.
- [ ] **Step 5: Commit** `feat(calendar): day cells with fills, an in-cell spend bar and 帳單 / 結帳 tags`.

### Task 3: Filter state for 日曆 — account inside the panel, chip, external trigger

Owner item 3 (shared groundwork).

**Files:**
- Modify: `frontend/src/app/components/ui/filter-bar/filter-bar.ts`, `filter-bar.html`, `filter-bar.scss`, `filter-bar.spec.ts`
- Create: `frontend/src/app/components/ui/filter-bar/filter-chips.ts` (+ `.html`, `.scss`, `.spec.ts`)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.html`, `timeline.spec.ts`
- Modify: `frontend/src/app/components/accounting/calendar-month/calendar-pane.service.ts`, `calendar-pane.service.spec.ts`

**Interfaces (produced):**
- `FilterChipsComponent` — selector `app-filter-chips`, inputs `chips: FilterChip[]`, outputs `remove: string`, `clearAll: void`; markup is the current `.filter-chips` block moved out of `filter-bar.html` (which now uses it).
- `FilterBarComponent` — new inputs `rowHidden = input(false)` (hides only `.filter-bar-row`; chips and the panel still render) and `chipsHidden = input(false)` (hides only the chips); `close(restoreFocus = true)` stays, and the resize case calls `close(false)` (any open panel, anchored or not, no focus move); new method `toggle(anchor?: HTMLElement)`: with an anchor the popover is `position: fixed`, its top = anchor bottom + 6 px and its right edge = anchor right edge (clamped to the viewport), focus returns to the anchor on close, and a pointerdown on the anchor does not count as outside. Phone keeps the sheet.
- Timeline: `accountInPanel = computed(() => this.view() === 'calendar')`. The account `<select>` is projected into `[filterBarPrimary]` when false and is the first `.hub-filter-field` (label 帳戶) inside `[filterBarSecondary]` when true. `filterChips` includes `{ key: 'account', label: '帳戶：<name>' }` only when `accountInPanel()`; `removeFilter('account')` clears it; `resetSecondaryFilters` clears the account only when `accountInPanel()`.
- `CalendarPaneService`: `chips = signal<FilterChip[]>([])`; `CalendarCommands` gains `toggleFilters(anchor: HTMLElement): void`, `removeFilter(key: string): void`, `clearFilters(): void`. `clear()` resets `chips`.

- [ ] **Step 1: Failing tests.** `filter-chips.spec.ts`: chips render, × emits `remove` with the key, 清除全部 only from two chips. `filter-bar.spec.ts`: `rowHidden` → no `.filter-bar-row` but chips still render; `chipsHidden` → row renders, chips do not; `close(false)` closes an anchored panel without focusing the anchor; `toggle(anchor)` opens `.filter-panel.popover` with inline `position: fixed`, pointerdown on the anchor keeps it open, close focuses the anchor. `timeline.spec.ts`: in 日曆 the account select is inside the opened panel and absent from `.slot-primary`; choosing an account adds the chip `帳戶：<name>` and 「篩選 1」; 重設 clears it; in 清單 the select is in `.slot-primary` and no account chip exists; switching 清單 → 日曆 with an account chosen keeps the filter. `calendar-pane.service.spec.ts`: timeline publishes `chips`; `commands.removeFilter('account')` clears the account.
- [ ] **Step 2: Run** the three spec globs — Expected: FAIL.
- [ ] **Step 3: Implement** as specified; `publishToPane` adds `effect(() => pane.chips.set(this.filterChips()))` and the three commands. The commands object is created once, so `toggleFilters` reads the `FilterBarComponent` view child at call time (`this.filterBar()?.toggle(anchor)`), never a captured reference.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(calendar): account filter joins the filter panel in 日曆; panel can open from an outside button`.

### Task 4: ≥ 1024 px — search and 篩選 at the top of the right pane

Owner item 3, first bullet.

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html`, `timeline.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html`, `.ts`, `.scss`, `.spec.ts`

**Interfaces:**
- Consumes Task 3's `rowHidden`, `chips`, commands.
- Timeline: `<app-filter-bar [rowHidden]="wideCalendar()" [chipsHidden]="wideCalendar()">`; `.match-row--calendar` is not rendered when `wideCalendar()`.
- Layout: `.cal-top` row at the top of `.cal-base` in every base (`summary` / `day` / `results`): the existing `input.cal-search` (placeholder `搜尋整月`, aria-label unchanged) inside a `label.cal-search-box` with a search icon, and `button.cal-filter` (`aria-haspopup="dialog"`, text `篩選` + count when `pane.chips().length > 0`) calling `pane.commands()?.toggleFilters(button)`. Under it `<app-filter-chips [chips]="pane.chips()">` wired to `removeFilter` / `clearFilters`.
- Layout: when a non-empty search text is committed while `pane.selectedDay()` is set, the layout clears the day (`onClearDay()`) before `setQuery` so `calendarBase()` becomes `results`. This lives in `onSearch(text)`, the one place both commit paths (Enter and the debounce in `onSearchInput`) go through.

- [ ] **Step 1: Failing tests.** `timeline.spec.ts` (panes + 日曆): no `.filter-bar-row`, no `.filter-chips`, no `.match-row--calendar` in the timeline even with filters active; 清單 still has them. `accounting-layout.spec.ts`: `.cal-top` exists in summary, day and results bases; placeholder is `搜尋整月`; `button.cal-filter` click calls `toggleFilters` with the button; with two published chips the pane shows them and 「篩選 2」, × calls `removeFilter(key)`, 清除全部 calls `clearFilters`; typing `咖啡` + Enter with `day=2026-11-20` selected navigates without `day` and then calls `setQuery('咖啡')`; the same through the 300 ms debounce (no Enter); an empty commit with a day selected keeps the day; Esc with a text and no day empties the text (base returns to `summary`).
- [ ] **Step 2: Run** `--include='src/app/components/accounting/accounting-layout/*.spec.ts'` and the timeline spec — Expected: FAIL.
- [ ] **Step 3: Implement.** `.cal-top { display: flex; gap: 8px }`; search box `min-height: 38px; border-radius: 11px; background: var(--app-surface-soft)`; filter button `min-height: 38px; padding: 0 12px; border-radius: 11px; border: 1px solid var(--app-border); font-size: 13px; font-weight: 700`. Keep the existing effect that writes the input's value (never while focused).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(calendar): one search and filter entry at the top of the right pane`.

### Task 5: < 1024 px — search and filter behind a title-row icon

Owner item 3, second bullet, and the unit half of item 4.

**Files:**
- Modify: `frontend/src/app/components/accounting/month-overview/overview.service.ts` (`AccountingOverviewService`), its spec
- Modify: `timeline.ts`, `timeline.html`, `timeline.scss`, `timeline.spec.ts`
- Modify: `accounting-layout.html`, `.ts`, `.spec.ts`

**Interfaces:**
- `AccountingOverviewService`: `calendarFiltersOpen = signal(false)`, `calendarFiltersActive = signal(false)` (timeline writes the second from `filtersActive()`).
- A shared button template: `button.cal-filters-toggle`, `aria-label="搜尋與篩選"`, `aria-expanded`, a `pi pi-search` icon, class `has-active` (a 6 px `--app-primary` dot) when `calendarFiltersActive()`. Rendered inside `<app-page-header>` content by the timeline when `!embedded() && narrowCalendar()` (phone) and by the layout when `mode() === 'sheet' && listKey() === 'timeline' && overview.calendar()`.
- Timeline: `<app-filter-bar>` gets `[rowHidden]="wideCalendar() || (narrowCalendar() && !overview.calendarFiltersOpen())"`; chips stay visible in narrow 日曆 (`chipsHidden` only for wide). Entering narrow 日曆 (view change, month change is not an entry) sets `calendarFiltersOpen` to `filtersActive()`; leaving 日曆 sets it false. Opening moves focus to `.filter-q`.
- Crossing 1024 px while the view stays 日曆 (an effect on `layoutMode.mode()`): filter signals are kept; going narrow sets `calendarFiltersOpen` to `filtersActive()` (so an active search text stays visible in `.filter-q`, whose value is re-synced from `query()` when the row mounts); in both directions `filterBar()?.close(false)` runs — the panel is a sibling of `.filter-bar-row` and panes ↔ sheet keeps the same timeline instance, so an open panel would otherwise survive with its opener gone; focus is not sent to a button that is unmounting.

- [ ] **Step 1: Failing tests.** `timeline.spec.ts` (phone, 日曆): header has `.cal-filters-toggle` with `aria-expanded="false"`; no `.filter-bar-row`; click → row renders, `aria-expanded="true"`, `.filter-q` focused; click again hides it; with a search text active the toggle has `has-active`, and entering 日曆 with a filter active starts expanded; with the row collapsed and one filter active `.filter-chips` is still rendered and its × removes the filter; 清單 has no toggle and always shows the row; panes → phone with a search text active: the row is expanded and `.filter-q` holds the text; panes → phone with the anchored panel open: the panel is closed; phone → panes with the row open: the timeline renders no `.filter-bar-row` and the pane's box holds the text; sheet (820) with the in-row panel open → panes: the panel is gone and `.cal-search` holds the text (same timeline instance, in the layout spec); 2026-11 renders 5 `.week` rows and the today bar directly after `.strip-area` with no filter row between the month bar and the grid. `accounting-layout.spec.ts` (sheet width 820, 日曆): the layout's header has the toggle and it flips `overview.calendarFiltersOpen`; panes mode has none.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.** Button: 40 × 40 px, round, `border: 1px solid var(--app-border); background: var(--app-surface)`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(calendar): narrow 日曆 keeps search and filters behind a title-row button`.

### Task 6: Compact entry detail in the calendar drawer

Owner item 5.

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts`, `entry-detail.html`, `entry-detail.scss`, `entry-detail.spec.ts`
- Modify: `frontend/src/app/components/accounting/entry-detail/einvoice-card.ts` (inline styles), `einvoice-card.html`, `einvoice-card.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.scss`, `accounting-layout.spec.ts`

**Interfaces:**
- `EntryDetailComponent`: `drawer = computed(() => this.layoutMode.mode() === 'panes' && this.overview.calendar())` (inject `AccountingOverviewService`); host class `entry-detail--drawer`. `stepEntry(delta: 1 | -1)` calls `inject(CalendarPaneService).commands()?.stepEntry(delta)`.
- `EntryEinvoiceCardComponent`: `compact = input(false)`. When `compact()` and there is no linked invoice and the card is `visible()`, it reads the picker once on init / entry change and keeps the result; the idle state renders `button.einvoice-open.einvoice-row` (dashed border, receipt icon, `連結發票…`, right-aligned `small.einvoice-hint`). Hint: `前後 7 天有 ${n} 張可選` when the picker is eligible with `n = invoices.length > 0`; `前後 7 天沒有可選的發票` when 0; nothing while loading / failed. The prefetch result lives in its own signal `hint = signal<{ entryId: number; picker: EinvoicePicker } | 'failed' | null>(null)` — never in `pickerState` (whose `ready` renders the list and whose ineligible result hides the card). The read runs in the same effect that resets `pickerState` on an entry-id change, after the reset, once per entry, only when `compact()`, `visible()` and unlinked. `openPicker()` sets `pickerState` to `ready` from the held picker when it is for this entry, else reads as today. The card's styles are inline in `einvoice-card.ts` (there is no scss file).

- [ ] **Step 1: Failing tests.** `entry-detail.spec.ts`, drawer on (panes + `overview.calendar.set(true)`):
  - no `.dhead`; `.drawer-bar` has `button[aria-label="上一筆"]`, `button[aria-label="下一筆"]`, text `↑↓ 切換`, `button[aria-label="關閉（Esc）"]`; the step buttons call `commands.stepEntry(-1 / 1)`; ✕ calls the existing `close()`;
  - `.dcompact` holds `.ico` (category colour as background), `.detail-name`, `.dkind` = kind label + ` · ` + `categoryPath()` (`支出 · 餐飲 › 午餐`; just the kind label when the entry has no category), `.detail-total`;
  - `dl.dtable` cells in order 帳戶, 商家, 日期 (`M/D 週X`, month and day unpadded: `11/5 週四` — built from the existing weekday helper, not `slashDate` / `dayLabel`), 時間; a debt entry labels the second 對象; 專案 appears only when set; 交易類型 / 事件類型 only when non-default;
  - `.actions` is inside `.drawer-foot`, a sibling after the scrolling `.drawer-body`; each inline panel (delete, refund, settle, reopen / close, scope, repost) renders inside `.drawer-body` when opened;
  - with the drawer on, each of these still renders inside `.drawer-body` when its data is present: `.group-back`, `.fee-note`, `.posted` (入帳日), `.debt-events` (結清記錄);
  - drawer off (sheet, phone, panes + 清單): `.dhead` present, no `.drawer-bar`, `.dgrid` with the seven cells as today.
  `einvoice-card.spec.ts`: `compact` + picker with 2 invoices → hint `前後 7 天有 2 張可選`, one GET, the picker list is not shown yet (`pickerState` still idle); click → picker list without a second GET, and the 這筆 / 整組 choice still appears when the held picker asks for a scope; 0 invoices → `前後 7 天沒有可選的發票`; a held picker that is not eligible → the card hides as it does today after a click (no row flashes: the row renders only once the prefetch settles); failed read → no hint, no 讀取失敗 text, and a click does one more GET; the entry id changes → the old hint is gone and one new GET runs; `compact` false → no GET before the click (today's behaviour).
- [ ] **Step 2: Run** `--include='src/app/components/accounting/entry-detail/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** One template with `@if (drawer())` branches for the head / fields / footer; everything between (schedule, FX, chips, debt, reward, invoice card with `[compact]="drawer()"`, related, notes, inline panels) is shared markup inside `.drawer-body`. Sizes per the artboard: bar buttons 36 px, icon disc 46 px, name 17 px / 800, total 22 px / 800, table cells `padding: 8px 12px`, label 12 px muted, value 14 px / 700, 1 px `--app-border` grid lines, radius 12 px; footer `grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); gap: 6px; padding: 10px 12px; border-top: 1px solid var(--app-border)`, buttons 44 px high, 編輯 on `--app-primary-soft`, 刪除 in the danger ink. `.entry-detail--drawer { display: flex; flex-direction: column; height: 100% }`, body `flex: 1; min-height: 0; overflow-y: auto; padding: 16px`. Today `.pane-host.drawer` is itself the scroll container (`overflow-y: auto` plus padding, `accounting-layout.scss:101-108`), which would scroll the footer away: for the detail drawer the host becomes `display: flex; flex-direction: column; overflow: hidden; padding: 0` and the routed component fills it; `.pane-host.drawer--form` (the entry form in the drawer) keeps today's `overflow-y: auto` and padding — add a scss-source test for both rules.
- [ ] **Step 4: Run** — Expected: PASS; run the layout spec (drawer focus trap, Esc, ↑↓) — Expected: PASS without changes to its assertions, or adjust selectors only.
- [ ] **Step 5: Commit** `feat(calendar): compact entry detail in the drawer with a fixed action bar`.

### Task 7: 這個月要處理 rows

Owner item 6.

**Files:**
- Modify: `frontend/src/app/components/accounting/month-overview/month-overview.html`, `.scss`, `.spec.ts`

- [ ] **Step 1: Failing tests.** Bill row: `button.todo-bill` contains `.todo-tag.tag-bill` `帳單`, `.todo-text` `<name> <amountText> · M/D 到期` (no second 「帳單」 word), `.todo-chev`; click emits `pickDay` with the due date. Invoice row: `.todo-tag.tag-invoice` `發票`, `.todo-text` `M/D <seller> <amount>`, chevron; click emits `openInvoice`. Each button's accessible name still contains 帳單 / 發票.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/month-overview/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** Row `display: flex; align-items: center; gap: 10px; min-height: 42px; border-bottom: 1px solid var(--app-border)`; tag as in Task 2; chevron `pi pi-angle-right` muted, `aria-hidden`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(calendar): 這個月要處理 rows carry a tag and a chevron`.

### Task 8: Spec, design index, full suite, evidence

**Files:**
- Modify: `openspec/specs/frontend-accounting-ledger/spec.md` — requirements "Calendar day cell figures", "Calendar day cell empty and future days", "Calendar day cell marks and accessible name", "Calendar layout on wide screens" (+ month summary, entry drawer), "Calendar items to handle", "Calendar layout on narrow screens", "Timeline home" (filter bar in 日曆): rewrite the sentences that this PR makes false and add scenarios for: 5-row month; closing tag; search / filter in the right pane; narrow toggle; drawer compact head. Keep every requirement under 500 characters (move detail into scenarios).
- Modify: `design_handoff_v3/README.md` — two index rows for `CalendarFixDesktop.dc.html` / `CalendarFixPhone.dc.html`, marked as superseding the calendar artboards for the listed points.
- Add: `design_handoff_v3/design_refs/CalendarFixDesktop.dc.html`, `CalendarFixPhone.dc.html` (already copied into the worktree).

- [ ] **Step 1:** Edit the spec and README.
- [ ] **Step 2: Run** `openspec validate frontend-accounting-ledger --type spec` — Expected: valid (strict warnings about pre-existing long requirements are not new; none of the edited requirements may add one).
- [ ] **Step 3: Run** `cd frontend && npx ng test --watch=false` — Expected: all files pass; `npx ng build` — Expected: build succeeds.
- [ ] **Step 4: Evidence (outside the repo).** Build before (origin/main) and after; Playwright at 1440 × 900 and 390 × 844, light and dark: 日曆 month, day selected, drawer open, filter panel open. Probe: at 390 × 844 the `.today-bar` bottom edge is above the tab bar's top edge with `scrollY === 0`, in three cases: 2026-11 Sunday first (5 rows), 2026-08 Sunday first (6 rows), 2026-11 Monday first (6 rows). A failing 6-row case triggers the `rows-6` fallback of ruling 12 (with a unit test: a 6-row month in compact month mode carries `rows-6`), then the probe is re-run. Screens go into the PR description as a private gallery link.
- [ ] **Step 5: Commit** `docs(openspec): calendar cells, search / filter placement and drawer head`.
