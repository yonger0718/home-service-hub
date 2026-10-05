# Accounting UX refinement — design

Date: 2026-10-05 · Base: `main` @ b8d4d77 · Scope: frontend only (`frontend/src/app/components/accounting/**`, `frontend/src/app/components/shell/navigation.ts`, `frontend/src/app/components/mobile-nav/**`). No backend change, no API contract change, no change to the accepted schedules contract (`openspec/specs/accounting-schedules/spec.md`).

Source of findings: Multica issue AGENT-60 (static comparison 07:43 UTC, live pass 08:26 UTC, side-by-side pack 08:41 UTC) and the owner's decisions on 2026-10-05.

## Goals

1. Filters and loading states never mislead: the reader can always tell whether a number is for the whole month or the filtered set, and whether a blank list means "nothing", "loading" or "failed".
2. Event type (單次／週期／分期) is a first-class choice in the entry form, not a collapsed "進階" section.
3. Choosing an account shows group, currency and balance, consistently in every form.
4. A side sheet cannot be closed by accident while typing, keeps keyboard focus inside, and never drops unsaved input silently.
5. Wording is consistent (記錄, 記帳設定, 交易類型／事件類型, 首期入帳日) and secondary text is readable.

Non-goals: credit-card statement reconciliation flow (next feature), cross-month search / amount filters (needs API), hide-amounts toggle, charts, templates, keypad ✓ long-press, Phase B loan options, any tab restructuring of 提醒中心.

Owner decisions: month totals stay whole-month and get labelled, with a filtered match count (no summary API change). Wording standard is 記錄. Implementation is handed to Multica (lead-astra) as three PRs; this document is the binding spec for them.

## Global constraints

- Angular 21 standalone components with signals; no new dependencies.
- All user-visible strings in 繁體中文; the standard noun for an entry is 記錄.
- No backend or OpenAPI change. Existing endpoints only: `GET /entries` (page `{items,total,limit,offset}`), `GET /entries/summary`, `GET /entries/summary/daily`, `GET /accounts` (with balances and `group_id`/`group_name`), `GET /imports/latest`, `GET /preference`.
- Every behavioural change has a Vitest spec in the component's existing `*.spec.ts` (or a new sibling spec for a new component). `cd frontend && npm test` must pass; `npm run build` must pass.
- Layout modes come from `LayoutModeService` (`phone` <760px, `sheet` 760–1023px, `panes` ≥1024px, `compactHeight` ≤820px tall); every change is checked at 390×844, 760×820, 1280×800 on the demo preview.
- Accessibility: every new interactive element has an accessible name; keyboard operation (Tab, ↑↓, Enter, Esc) works for every new control; `prefers-reduced-motion` disables new transitions.
- Minimum font size in the accounting feature is `.72rem`.

## 1. Filters, totals and load states

### 1.1 Timeline (`timeline.*`)

**Filter state.** `filtersActive = computed(() => accountFilter() !== null || kindFilter() !== null || query() !== '')`.

**Search input.** `filter-q` switches from `(change)` to `(input)` with a 300 ms debounce (a `setTimeout` cleared on each keystroke, or `toSignal(fromEvent(...).pipe(debounceTime(300)))`); Enter commits immediately. Trimmed value feeds `query()` as today. Esc inside the field clears it (existing shortcut layers are untouched: the field stops propagation only when it has text).

**Month summary header.** When `filtersActive()`:
- the `.sum` block gets a small eyebrow label `全月 · 未套篩選` (class `sum-scope`), always visible above the three figures;
- directly below the `.sum` block a row `.match-row` reads `符合條件 {{ total() }} 筆` with a `清除篩選` button (`aria-label="清除篩選"`) that resets all three filters and the query. `total()` is the `total` of the `/entries` page response (already stored). When `total()` is unknown because the list failed, the row shows `符合條件 — 筆`.

When no filter is active neither the eyebrow nor the match row renders.

**Calendar view.** Daily totals keep ignoring filters (API is month-only); when `filtersActive()` the calendar header shows the same `全月 · 未套篩選` eyebrow, and the selected-day list obeys the filters as today.

**Empty text.**
- list, no filters: `這個月沒有記錄`
- list, filters active: `沒有符合條件的記錄` followed by the same `清除篩選` button
- calendar day, no filters: `這天沒有記錄`; filters active: `這天沒有符合條件的記錄`

**Load states (list and calendar day).** Exactly one of these renders at a time:
1. loading (first load or filter change with `reset`): a `.skeleton` block of three placeholder rows (`aria-busy="true"`, `aria-label="載入中"`); the previous rows are not shown behind it. "載入更多" during pagination keeps today's behaviour (button disabled, rows stay).
2. error: `<p class="load-error">記錄讀取失敗</p>` plus a `重試` button that calls `load(true)` (or `loadDay(day)` for the calendar list).
3. empty: text per the table above.
4. rows.

The `@empty` branch therefore requires `!loading() && !loadError()`, and the error branch renders instead of the rows, never beside them.

**Summary failures.** When `/entries/summary` fails, the `.sum` block stays and shows `摘要讀取失敗` with a `重試` button (re-runs `loadSummary()`), instead of disappearing. The daily summary failure in calendar view shows the same text in the calendar header.

### 1.2 Passbook (`account-entries.*`)

Same three-way exclusivity. The `loadError()` paragraph moves inside the list region and is mutually exclusive with the `@for`/`@empty` output: `@if (loadError()) {error + 重試} @else if (loading() && entries().length === 0) {skeleton} @else { @for … @empty { 沒有符合條件的記錄 } }`. `重試` calls `load(true)`. The `本期記錄 (N)` label shows `本期記錄` without a count while loading or failed.

### 1.3 Accounts overview (`accounts.*`)

- `getLatestImport()` in `load()` gets `catchError(() => of(null))`, matching `preference`. A failed import lookup renders the import panel as `匯入狀態無法取得` with a `重試` button that re-runs only `getLatestImport()`; the account list renders normally.
- The whole-page `帳戶讀取失敗` state remains only for a failed `getAccounts()`, and gains a `重試` button (`load()`).
- Zero-accounts empty state: title `還沒有帳戶`, body `新增帳戶開始記帳，或上傳 MOZE 備份匯入。`, two actions: primary `新增帳戶` → `/accounting/accounts/new`, secondary `前往記帳設定` (existing link).
- A `.skeleton` list (three rows) renders while `!loaded()`.

### 1.4 Shared pieces

- `AC/skeleton/skeleton.ts` — tiny standalone component `<app-skeleton rows="3">` rendering N placeholder rows (`.skel-row` with a shimmering gradient; no animation under `prefers-reduced-motion`). Used by timeline, passbook, accounts.
- Strings stay in templates; the skeleton component is the only shared piece.

## 2. Event type in the entry form

### 2.1 Placement

In `entry-form.html`, after the date and time tiles and before the invoice tile, a full-width tile `.tile.wide.event-type`:

```
事件類型   [ 單次 | 週期 | 分期 ]
```

- Rendered as a `role="tablist"` with `aria-label="事件類型"`; the tabs are the existing `tabsFor(kind, {editing})` result (so `system`/editing → only 單次; 分期 only for expense/payable). Selecting a tab sets `scheduleDraft().tab` exactly as the current 進階 tabs do.
- When `scheduling()` is true, the schedule fields (`app-schedule-tabs` body for the active tab: 區間／起始日／結束／入帳方式 or 總額／期數／首期入帳日／每期金額／利息／還款帳戶／入帳方式) render **directly below this tile**, inside the same tile group, with the projected 入帳日 row kept. The `<details class="advanced">` wrapper and its `進階` summary are removed. `app-schedule-tabs` keeps its API (`[(draft)]`, inputs) but no longer renders its own tablist: the tablist moves into the entry form (or `app-schedule-tabs` gets an input `showTabs=false`; implementer's choice, tests must cover the chosen one).
- Below the fields, the existing footer summary string (e.g. `週期：#1 / 無限期（每月 5號）`) renders as `.event-summary` under the tile whenever a non-single tab is active.

### 2.2 Unsupported fields copy

`排程不支援` (entry-form.html:214-216) becomes one line of secondary text placed where the hidden tiles were:

`週期／分期不含：手續費、拆帳、外幣、發票`

(class `.tile.wide.schedule-unsupported`, `aria-live="polite"`). It renders only when `scheduling()`.

### 2.3 Editing an existing entry

When `editing()` the tile still renders with 單次 selected and the other tabs disabled (`aria-disabled="true"`), followed by a hint `要改週期／分期，請到提醒中心的排程管理`. For an entry that belongs to a schedule, the existing scope/repost panels in entry-detail are unchanged.

### 2.4 Definition mode (`?schedule=<id>`)

Unchanged behaviour: the tile shows the definition's kind selected and the other tabs disabled; `formCanRepresent === false` keeps the read-only fallback and its message.

### 2.5 Keyboard

The tablist supports ←/→ to move between tabs and Space/Enter to select (mirrors the 記錄類型 tabs). Enter inside the tablist must not trigger the form's Enter-to-save (add `role=tab` buttons to the existing `NO_ENTER_SAVE_TAGS` handling by tag `BUTTON`, which is already excluded).

## 3. Account picker (`AC/account-picker/`)

### 3.1 Component

`<app-account-picker>` standalone, signals-based.

Inputs:
- `accounts: LedgerAccount[]` (already fetched by the host; includes balances, `group_id`, `group_name`, `currency`, `icon`, `color`, `is_credit`, `available_credit`, `is_archived`)
- `groups: AccountGroup[] | null` (optional; when null, groups are derived from `group_name` order of first appearance)
- `value: number | null`
- `label: string` (accessible name, e.g. 帳戶 / 轉出帳戶 / 轉入帳戶 / 還款帳戶)
- `allowArchived: boolean = false` (when true, archived accounts appear in a trailing group `已封存`)
- `exclude: number[] = []` (ids hidden, used by the transfer panel to hide the other leg)
- `compact: boolean = false` (split lines: trigger shows name only)

Output: `valueChange: number | null`.

Trigger (always rendered): a button `.acct-trigger` showing `[icon] name · balance` (`formatMoney(balance, currency)`; credit accounts show `可用 {available_credit}` when present). Empty value shows `選擇帳戶`. `aria-haspopup="dialog"`, `aria-expanded`.

Panel: on `phone` mode a bottom sheet (reuse the fee/fx sheet pattern and `sheetKeyAction`); on `sheet`/`panes` a popover anchored under the trigger (`position: absolute`, max-height 60vh, scrollable). Contents, top to bottom:
1. Search input (`placeholder="搜尋帳戶"`, autofocus on desktop, not on phone) — rendered only when the visible account count ≥ 8. Filters by name substring (case-insensitive) and by group name.
2. `最近使用` row of up to 4 chips, from `localStorage` key `hh.accounting.recentAccounts` (array of ids, most recent first, max 8 stored). Updated by the host on successful save (entry-form `save()` success path: the entry's account id; transfer: both legs). Hidden when empty or when searching.
3. Groups in `AccountGroup.sort_order`, each with a header (group name, or `未分組`) and rows: `[icon] name` left, `currency badge` + `balance` right (`.acct-balance`, tabular numerals, `.72rem` minimum). Archived accounts (if allowed) under `已封存` last. The selected row has `aria-selected="true"` and a check mark.
4. Rows are `role="option"` inside `role="listbox"`; ↑/↓ move, Enter selects, Esc closes without change, typing in the search box narrows the list. Selecting closes the panel and returns focus to the trigger.

### 3.2 Adoption

Replace the native `<select>` in:
- `entry-form.html:163-172` (帳戶) — value `accountId`, `allowArchived` when editing or in definition mode (same as today's `accountOptions`).
- `transfer-panel.html:13-17, 25-29` (轉出／轉入) — each excludes the other leg's id; balance shown by the picker so the separate balance line is removed.
- `split-lines.html:47-51` (每行帳戶) — `compact=true`.
- `schedule-tabs.html:110-119` (還款帳戶).
- The 餘額調整 (system) account select at `entry-form.html:74` also adopts it.

`accountLabel()` stays for anywhere else (toasts, detail). Per-category last-use prefill (`hh.accounting.lastUse.<categoryId>`) is unchanged.

### 3.3 Tests

`account-picker.spec.ts`: grouping order, archived placement, exclude, search threshold (7 accounts → no search box, 8 → box), keyboard navigation, selection emits once and closes, recent list read/write with a broken `localStorage` (throws) tolerated. Host specs assert the picker receives the right `accounts`/`value` and that `valueChange` updates the draft.

## 4. Sheet gesture, focus and unsaved input (`accounting-layout.*`, `entry-form.*`)

### 4.1 Swipe-to-close

- Only pointer sequences that start in the pane's top drag zone qualify: a new element `.sheet-grip` (height 56px, full width, contains the ✕ button; `touch-action: pan-y`). `pointerdown` elsewhere in the pane is ignored.
- Also ignored when the `pointerdown` target is inside `input, textarea, select, button:not(.sheet-close), [contenteditable], .keypad`.
- Track `pointermove`: close when `dx > 80 && |dy| < 40` at `pointerup`; otherwise do nothing. `setPointerCapture` on the grip while tracking. Cancel on `pointercancel`.
- The grip renders a thin handle bar beside ✕ in sheet mode; in panes mode the grip exists but has no visual and no swipe handler (panes are not dismissible).

### 4.2 Focus containment

- While `sheetOpen()`: the list pane element and the dock (`app-dock` host, or the element the layout wraps around the dock) get the `inert` attribute; removed on close. The backdrop keeps `aria-hidden`.
- Tab/Shift+Tab inside the pane wraps between the first and last focusable elements (a `keydown` handler on the pane computing focusables with the usual selector list). Initial focus stays on ✕ as today; restore on close as today.

### 4.3 Unsaved input

- `entry-form` exposes `isDirty: Signal<boolean>` = draft differs from the snapshot taken after initial load (compare the serialised draft used for save, excluding derived fields). Saved or cancelled forms reset the snapshot.
- The layout's `close()` (✕, backdrop, swipe, Esc layer 3) and entry-form's `cancel()` (Esc layer 1, ✕ on phone) go through one guard: if the active pane component reports `isDirty()`, render an in-pane confirm strip at the top of the pane: `放棄未儲存的內容？` with buttons `留下` (default focus) and `放棄`. `放棄` runs the original close; `留下` dismisses. No `window.confirm`.
- Pane components opt in by implementing `interface DirtyAware { isDirty(): boolean }`; the layout reads it from the activated `pane` outlet component (`(activate)` event on `router-outlet`). Entry-detail, reminders etc. are not dirty-aware and close immediately.
- Navigation away by the browser back button is out of scope (no `CanDeactivate` guard this round).

### 4.4 Tests

`accounting-layout.spec.ts`: swipe from a text input does not close; swipe on the grip with dx 100/dy 10 closes; dx 100/dy 60 does not; inert toggled on open/close; Tab from the last focusable wraps to ✕; dirty pane shows the strip and `放棄` closes, `留下` keeps. `entry-form.spec.ts`: `isDirty` false after load, true after typing a name, false after save.

## 5. Wording and readability

- 紀錄 → 記錄 in the 7 accounting occurrences and `shell/navigation.ts:53` (`label: '記錄', title: '記帳記錄'`). Specs updated accordingly. Left untouched: any string outside the accounting feature and the shell nav item.
- `navigation.ts:55` accounting settings item `label: '記帳設定'` (title unchanged). The mobile sub-nav shows the new label.
- entry-detail `.dgrid`: `類型` becomes two cells — `交易類型` (`kindLabel(d.kind)`) and `事件類型` (`單次` / `週期` / `分期`, derived from `d.schedule?.kind` → recurring=週期, installment=分期, none=單次).
- `首次還款日` → `首期入帳日` in `schedule-tabs.html:90` and the footer string in `schedule-math.ts:117` (`首期入帳日將從 YYYY/MM/DD 開始（N 期）`); specs updated; identifiers and comments untouched.
- Font sizes: every `font-size` ≤ `.7rem` in the files listed in the fact sheet (33 declarations) is raised to `.72rem`; `timeline.scss:227` (`.58rem`) is inspected: if it is the calendar day figure, it becomes `.72rem` with `font-variant-numeric: tabular-nums` and the day cell may show `萬` abbreviation (already implemented); otherwise `.72rem`. Visual check at 390px for overflow.
- Entry-detail colour header: the text colour is chosen from the category colour's luminance (`relativeLuminance(hex) > 0.5 ? dark : light`); helper `textOn(bg)` added to `accounting-ui.ts` with a unit test.

## 6. Delivery (Multica)

Three PRs against `main`, in order; each independently reviewable, each with Vitest green, `npm run build` green, and before/after screenshots from the demo preview (`http://127.0.0.1:18080/hub/accounting`, fictional data) at 390×844, 760×820, 1280×800 attached to the PR.

- PR-1 `feat/ux-refine-1-states-wording`: §1, §2, §5.
- PR-2 `feat/ux-refine-2-sheet`: §4.
- PR-3 `feat/ux-refine-3-account-picker`: §3.

Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; PR descriptions end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. No backend files, no `openspec/` changes, no `.env`, no deploy actions. Review by the owner's Claude Code session (plus CodeRabbit); merge by the owner; deploy via the existing signed release flow.

## Review focus (what tests may not catch)

1. A filter that returns zero rows while the summary request is still in flight must show the skeleton, not `沒有符合條件的記錄`.
2. Switching 事件類型 from 分期 back to 單次 must restore hidden tiles (fee button, invoice) and clear schedule-only validation errors.
3. The picker popover on `sheet` mode must not be clipped by the pane's `overflow`; verify at 760×820 with the picker opened near the bottom of the form.
4. The dirty guard must not fire after a successful save-and-continue (snapshot reset) or when the sheet closes because the route changed to the same pane with a different id.
5. Raising font sizes must not wrap the three-figure summary row at 390px; if it does, the figures stack to two lines deliberately (not clipped).
