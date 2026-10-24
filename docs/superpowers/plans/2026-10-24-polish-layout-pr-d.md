# Polish Layout v2 — PR D (shell and phone) Implementation Plan

_Rev 2 — folds Multica AGENT-279 (majors: accounts panes tests must stay, 刷新行情 test via the refresh-subject pattern; mediums: focus-out scope + `preventDefault`, seg-toggle host width, `ActionsBelowHost` / `dashboard.spec.ts:343-352` / `accounts-page.spec.ts:316` cleanup; minor: phone media query 759.98 px)._

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** D5 — the 記一筆 floating button slides out while the page's scrolling ancestor scrolls down and returns on scroll up, at the end, after 1.2 s idle, or while focused; D9 — phone page headers keep one row (secondary actions in a ⋯ menu, the dashboard's market toggle under the header at full width); phone collapsed holdings rows take two lines.

**Architecture:** D5 is one directive on the shell's FAB (`appHideOnScroll`) fed by a single capture-phase `scroll` listener on the document (scroll does not bubble; capture reaches the document, `.hub-main` and the accounting list pane alike) plus a pure reducer `nextFabVisibility(prev, event)` that the unit tests exercise; the hidden state is a host class the stylesheet turns into translate + fade ending in `visibility: hidden`. Layouts that own their own page scroller mark it with `data-page-scroller` (the accounting list pane), so the shell never names another component's class (D4 spirit). D9 adds a `menu` input to `<app-page-header>` (phone only: a ⋯ button and a `role="menu"` panel, custom like the filter bar, no PrimeNG) and a `block` input to `<app-seg-toggle>`; the three pages with more than one action wrap their secondary inline buttons in `@if (!isPhone())` and pass the same actions as menu items. `actionsBelowOnPhone` becomes dead and is deleted. The holdings row wraps 股數 / 現價 / 今日 in one `.stock-meta` span that is `display: contents` from 760 px (desktop grid unchanged) and one text line on phones.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest + jsdom via `cd frontend && npx ng test --watch=false --include '<spec>'` (zsh: quote globs), SCSS with the `--app-*` tokens, `LayoutModeService.mode()` for phone detection (`TestBed.inject(LayoutModeService).set('phone')` in specs), Playwright-in-Docker for the browser check (stubs in `~/reports/home-hub-playwright-stubs/`, server `~/workspace/home-hub-demo-preview/redesign-v2-pr101/redesign_server.py` on 127.0.0.1:18081).

**Spec:** `openspec/changes/polish-layout-v2/` (rev 11.1 `a223a6b` on main 6fef19c): `design.md` D5 (slide out 160 ms, instant under `prefers-reduced-motion`; return on upward scroll / end of page / 1.2 s idle; ends `visibility: hidden`; never hides while focused; `N` always works; scrolling ancestor = document / `.hub-main` on ordinary pages, the accounting list pane in panes mode), D9 (header = title + at most one primary action + the shell's icon buttons; 匯入券商, 刷新行情, 匯入 CSV into a ⋯ menu; the market `SegToggle` under the header at full width), `tasks.md` §4, deltas `specs/frontend-app-shell/spec.md` (ADDED "Phone page headers keep one row", MODIFIED "App-wide 記一筆 floating button" with scenarios Scrolling a long list / Scrolling back up / Focused button stays / N from anywhere) and `specs/frontend-portfolio-dashboard/spec.md` (ADDED "Phone summary density", MODIFIED "總資產 hero" scenario Phone header). Task 1 adds rev 12 (two header scenarios the deltas lack and the ⋯ menu's accessibility line). Code inventory with verified line numbers: `.superpowers/pr-d-inventory.md` (git-ignored; implementers get the relevant lines in their brief).

## Global Constraints

- Frontend only; no API, model or copy change outside the surfaces named here. Branch `fix/polish-layout-pr-d` from `origin/main` (6fef19c).
- D5 values verbatim: transition 160 ms (`transform` + `opacity`), instant under `prefers-reduced-motion: reduce`; idle return after 1200 ms; hidden state ends in `visibility: hidden` and `pointer-events: none`; the button never hides while it is `document.activeElement`; `N` handling (`global-shortcuts.ts`) is not touched; the FAB stays unrendered below 760 px (`showFab`, unchanged).
- Scrolling ancestors the shell reacts to: the document (`event.target === document`, read `document.scrollingElement`), `.hub-main`, and any element carrying `data-page-scroller` (the accounting layout puts it on `.list-pane`). Every other scroll event (sub-nav chips, tables, listboxes, popovers) is ignored.
- D9 copy verbatim: the menu button `aria-label="更多動作"` with icon `pi-ellipsis-h`; item labels 匯入券商, 刷新行情 (dashboard), 匯入 CSV (交易紀錄), 新增帳戶 (現金帳戶 compact). Primary actions that stay in the row: 新增交易 (交易紀錄), 記錄入出金 (現金帳戶). The dashboard keeps no inline action on phones (its primary is the hero). Order inside `.page-header-row` on phones: ‹ back (if any), title, primary action(s), ⋯, gear.
- Menu accessibility: button `aria-haspopup="menu"`, `aria-expanded`, `aria-controls`; panel `role="menu"`, items `role="menuitem"` (`<button type="button">` for `run` items, `<a routerLink>` for `routerLink` items), first item focused on open, ArrowDown / ArrowUp cycle, Home / End jump, Escape closes and returns focus to the button — every key the menu handles calls `preventDefault()` after the `isHandledKey` guard (Home / End must not scroll the page, which the FAB listener would read as a scroll); focus leaving `.header-more-wrap` (`focusout` whose `relatedTarget` is null or outside that wrapper — the projected primary button and the gear count as outside) closes; outside pointerdown closes; `disabled` items carry `aria-disabled="true"`, do not run and leave the menu open. No PrimeNG.
- Phone header height: `.page-header-row` ≤ 64 px on every page (browser check at 390 px); the header never wraps on phones (no `flex-wrap` on `.page-header-row`; `actionsBelowOnPhone` is deleted).
- Holdings rows on phones (`< 760 px`): line 1 = `.stock-main` (abbr, name with market badge, symbol inline and muted) + `.stock-value`; line 2 = `.stock-meta` (`<qty> 股 · <price> · <day change (pct)>`) + `.stock-pnl`; the collapsed row is ≤ 72 px tall at 390 px; from 760 px the six-column grid and the column header row are unchanged (`.stock-qty` text stays `10`, no suffix).
- Colours, radii, z-index only through tokens (`--app-surface`, `--app-border`, `--app-text`, `--app-text-muted`, `--app-focus-ring`, `--radius-sm`, `--app-raised-shadow`, `--ease-standard`); the menu panel z-index 1100 (above `.hub-fab` 1000, same tier as the filter panel).
- Every task: `cd frontend && npx ng test --watch=false --include '<touched specs>'` green before commit; the whole suite, `npx ng build` and `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` (repo root) green before the PR.

## Review Focus

1. **Horizontal-only or inner scrolls must not move the FAB** — the sub-nav chips (`app-sub-nav`, overflow-x), wide tables, the filter popover and PrimeNG listboxes all fire `scroll` events that reach a capture listener. Pinned in Task 2 (reducer: `delta: 0` keeps the previous state; directive: a scroll on a `div` without `data-page-scroller` changes nothing).
2. **A route change resets the scroller** (new page starts at top): the first scroll event may be a downward one from the browser's restoration and would hide the button on a page the owner has not scrolled. Pinned in Task 2 (`NavigationEnd` → visible, forget the stored offsets) and Task 8 (navigate from a scrolled 交易紀錄 to 股利 → visible).
3. **Hidden must not mean unreachable** — a keyboard user tabbing onto the button must see it. `visibility: hidden` removes it from the tab order; a button that had focus when the scroll started must stay. Pinned in Task 2 (reducer `focused: true` → visible for any delta; host `focus` sets visible) and Task 8 (focus + wheel down → still visible).
4. **The ⋯ panel must not be clipped by a page container** (`overflow` on `.accounts-compact`, `.portfolio-dashboard`, the accounting section): pinned in Task 8 (at 390 px on the three pages, every `[role="menuitem"]` rect lies inside the viewport and `elementFromPoint` at its centre is that item).
5. **刷新行情 from the menu gives feedback** — the inline `app-btn` showed a spinner; in the menu the item runs and the panel closes. Pinned in Task 4 (item `disabled` while `loading()`, `aria-disabled="true"` on reopen; the KPI skeleton `@if (loading())` is the visible feedback).

---

### Task 1: Spec rev 12 — header scenarios and the menu's accessibility line

**Files:**
- Modify: `openspec/changes/polish-layout-v2/specs/frontend-app-shell/spec.md` (ADDED "Phone page headers keep one row")
- Modify: `openspec/changes/polish-layout-v2/proposal.md` (one bullet under **Layout**)

**Interfaces:** Produces the scenarios Tasks 3–5 implement.

- [ ] **Step 1: Extend the requirement text.** After the sentence ending `SHALL render below the header at full width.` append: `The ⋯ button SHALL be labelled 更多動作 and open a menu (\`role="menu"\`) of the moved actions: the first item takes focus, arrow keys move between items, Escape closes and returns focus to the button. From 760 px the actions stay inline and no ⋯ button renders.`

- [ ] **Step 2: Add two scenarios** after "Portfolio dashboard on a phone":

```markdown
#### Scenario: Transactions on a phone
- **WHEN** `/portfolio/transactions` renders at 390 px
- **THEN** the header SHALL be one row with 交易紀錄, the 新增交易 button and a ⋯ menu holding 匯入 CSV

#### Scenario: Cash accounts on a phone
- **WHEN** `/portfolio/accounts` renders at 390 px
- **THEN** the header SHALL be one row with 現金帳戶, the 記錄入出金 button and a ⋯ menu holding 新增帳戶
```

- [ ] **Step 3: proposal.md** — under **Layout**, replace `Phone page headers keep one row: secondary actions go into a ⋯ menu.` with `Phone page headers keep one row: secondary actions (匯入券商, 刷新行情, 匯入 CSV, 新增帳戶) go into a ⋯ menu labelled 更多動作; the dashboard's market toggle renders under the header at full width.`

- [ ] **Step 4: Validate.** Run from the repo root: `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` → `valid`.

- [ ] **Step 5: Commit** — `docs(spec): polish-layout-v2 rev 12 — phone header scenarios for 交易紀錄 / 現金帳戶, ⋯ menu accessibility`.

---

### Task 2: FAB hide-on-scroll (D5)

**Files:**
- Create: `frontend/src/app/components/shell/fab-visibility.ts` (pure reducer + constants)
- Create: `frontend/src/app/components/shell/fab-visibility.spec.ts`
- Create: `frontend/src/app/components/shell/hide-on-scroll.ts` (directive)
- Modify: `frontend/src/app/components/shell/shell.html:16-27` (add `appHideOnScroll` to `a.hub-fab`), `shell.ts:34` (import the directive)
- Modify: `frontend/src/app/components/shell/shell.scss:29-54` (`.hub-fab` transition + `.is-hidden`), `:126-130` (reduced motion)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html:5` (`data-page-scroller` on `section.list-pane`)
- Test: `frontend/src/app/components/shell/shell.spec.ts` (new describe), `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts` (one attribute test)

**Interfaces:**
- Produces: `export const FAB_IDLE_MS = 1200;` `export const PAGE_SCROLLER_SELECTOR = '.hub-main, [data-page-scroller]';` `export interface ScrollSample { delta: number; atEnd: boolean; focused: boolean }` `export function nextFabVisibility(visible: boolean, sample: ScrollSample): boolean` — `focused` → true; `atEnd` → true; `delta < 0` → true; `delta > 0` → false; `delta === 0` → `visible`. `HideOnScrollDirective` (`selector: 'a[appHideOnScroll]'`, standalone, `host: { '[class.is-hidden]': 'hidden()', '(focus)': 'onFocus()' }`), `readonly hidden = signal(false)`.

- [ ] **Step 1: Reducer spec** `fab-visibility.spec.ts`:

```ts
describe('nextFabVisibility', () => {
  it('hides on a downward scroll and shows on any upward one', () => {
    expect(nextFabVisibility(true, { delta: 12, atEnd: false, focused: false })).toBe(false);
    expect(nextFabVisibility(false, { delta: -1, atEnd: false, focused: false })).toBe(true);
  });
  it('keeps the state when the offset did not change (horizontal scroll)', () => {
    expect(nextFabVisibility(false, { delta: 0, atEnd: false, focused: false })).toBe(false);
    expect(nextFabVisibility(true, { delta: 0, atEnd: false, focused: false })).toBe(true);
  });
  it('shows at the end of the page even while scrolling down', () => {
    expect(nextFabVisibility(false, { delta: 40, atEnd: true, focused: false })).toBe(true);
  });
  it('never hides a focused button', () => {
    expect(nextFabVisibility(true, { delta: 40, atEnd: false, focused: true })).toBe(true);
  });
});
```

- [ ] **Step 2: Run it** — `npx ng test --watch=false --include 'src/app/components/shell/fab-visibility.spec.ts'` → FAIL (module missing).

- [ ] **Step 3: Implement `fab-visibility.ts`** with the exports above (one `if` chain; no classes).

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Directive spec in `shell.spec.ts`** — new `describe('記一筆 hides while scrolling (D5)')` using the existing `render(viewportOf(1440), …)` / `settle` helpers and `fab()` (`:122`). jsdom does not lay out, so each test fakes the scroller's metrics with a helper placed in the spec:

```ts
function fakeScroller(el: Element, top: number, scrollHeight = 2000, clientHeight = 800) {
  for (const [k, v] of Object.entries({ scrollTop: top, scrollHeight, clientHeight })) {
    Object.defineProperty(el, k, { value: v, configurable: true });
  }
}
const scrollDocument = async (top: number) => { fakeScroller(document.scrollingElement!, top); document.dispatchEvent(new Event('scroll')); await settle(); };
```

Tests (each: `vi.useFakeTimers()` in `beforeEach`, `vi.useRealTimers()` in `afterEach`; the FAB is `a.hub-fab`):
  - `'hides after a downward document scroll and returns on an upward one'` — `scrollDocument(0)` (baseline) → `scrollDocument(120)` → `fab()!.classList` contains `is-hidden`; `scrollDocument(100)` → not contains.
  - `'returns at the end of the page'` — baseline 0, `fakeScroller(doc, 1200, 2000, 800)` + scroll → visible (1200 + 800 ≥ 2000 − 1) even though delta > 0.
  - `'returns after 1.2 s without scrolling'` — hide (0 → 120), `vi.advanceTimersByTime(1199)` + `settle` → still hidden; `advanceTimersByTime(1)` + `settle` → visible.
  - `'never hides while it has focus'` — `fab()!.focus()`; 0 → 120 → visible.
  - `'ignores scrolls that are not the page scroller'` — create `<div>` appended to `.hub-page`, `fakeScroller(div, 300)`; `div.dispatchEvent(new Event('scroll'))` → still visible; then a `div` with `data-page-scroller` → hidden after 0 → 300 (two dispatches).
  - `'reacts to .hub-main when it is the scroller'` — `.hub-main` 0 → 200 → hidden.
  - `'comes back on a route change'` — hide, then `router.navigateByUrl('/portfolio/dividends')` + `settle` → visible.
  - `'the hidden state is a class the stylesheet turns into visibility: hidden'` — read `shell.scss` as text (`readFileSync`, the pattern used by `theme/*.spec.ts`) and assert it contains `.is-hidden` with `visibility: hidden`, `transition` with `160ms`, and a `prefers-reduced-motion: reduce` block that sets `.hub-fab` `transition: none`.

- [ ] **Step 6: Run** `--include 'src/app/components/shell/shell.spec.ts'` → the new tests FAIL (no class), the existing ones still pass.

- [ ] **Step 7: Implement `hide-on-scroll.ts`.** `inject(DOCUMENT)`, `inject(ElementRef)`, `inject(Router)`, `inject(DestroyRef)`, `inject(NgZone)` is not needed (zoneless). In the constructor: `document.addEventListener('scroll', onScroll, { capture: true, passive: true })`, removed on destroy; `router.events.pipe(filter(e => e instanceof NavigationEnd), takeUntilDestroyed())` → `reset()` (`hidden.set(false)`, clear the offset map and the idle timer). `onScroll(event)`: resolve the scroller — `event.target === document` → `document.scrollingElement`; else `target instanceof Element && target.matches(PAGE_SCROLLER_SELECTOR)` → target; else return. `delta = scroller.scrollTop − (offsets.get(scroller) ?? scroller.scrollTop)`; store the new offset (a `WeakMap<Element, number>`). `atEnd = scroller.scrollHeight > scroller.clientHeight && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight − 1`. `focused = document.activeElement === host`. `hidden.set(!nextFabVisibility(!hidden(), { delta, atEnd, focused }))`; when hidden, `clearTimeout` + `setTimeout(() => hidden.set(false), FAB_IDLE_MS)` (the global `setTimeout`, which `vi.useFakeTimers()` patches; not `document.defaultView.setTimeout`). `onFocus()` → `hidden.set(false)`.

- [ ] **Step 8: Template + styles.** `shell.html:17` add `appHideOnScroll` to the `<a class="hub-fab">`; `shell.ts` imports `HideOnScrollDirective`. `shell.scss` `.hub-fab`: add `transition: transform 160ms var(--ease-standard), opacity 160ms var(--ease-standard), visibility 0s linear 0s;` and

```scss
  &.is-hidden {
    opacity: 0;
    pointer-events: none;
    transform: translateY(24px);
    transition-delay: 0s, 0s, 160ms; /* visibility flips after the slide, so the button leaves the tab order last */
    visibility: hidden;
  }
```

In the `prefers-reduced-motion: reduce` block add `.hub-fab { transition: none; }`. `accounting-layout.html:5`: add `data-page-scroller` to `<section #listPane class="pane list-pane" …>`; in `accounting-layout.spec.ts` add `it('marks the list pane as the page scroller for the shell', …)` asserting `querySelector('.list-pane')?.hasAttribute('data-page-scroller')`.

- [ ] **Step 9: Run** shell + fab-visibility + accounting-layout specs → PASS.

- [ ] **Step 10: Commit** — `feat(shell): 記一筆 button hides on scroll down, returns on scroll up / end / idle (D5)`.

---

### Task 3: Page header ⋯ menu (D9 primitive)

**Files:**
- Modify: `frontend/src/app/components/shell/page-header/page-header.ts`, `.html`, `.scss`
- Test: `frontend/src/app/components/shell/page-header/page-header.spec.ts`

**Interfaces:**
- Produces: `export interface PageMenuItem { label: string; icon?: string; routerLink?: string | readonly unknown[]; queryParams?: Params; run?: () => void; disabled?: boolean; }` and `readonly menu = input<PageMenuItem[]>([])` on `PageHeaderComponent`; template ids `.header-more` (button), `.header-menu` (panel), `.header-menu-item`. The menu renders only when `phone() && menu().length > 0`.
- Consumes: nothing new (`actionsBelowOnPhone` still exists until Task 5).

- [ ] **Step 1: Tests** in `page-header.spec.ts` (new `describe('⋯ menu (phone)')`; use the file's existing host-component pattern and `LayoutModeService.set('phone')`). Host template: `<app-page-header title="T" [menu]="items"><button class="primary">主要</button></app-page-header>` with `items: PageMenuItem[] = [{ label: '匯入券商', icon: 'pi-upload', routerLink: '/portfolio/import', queryParams: { type: 'broker' } }, { label: '刷新行情', icon: 'pi-refresh', run: () => this.ran++ }, { label: '停用', run: () => this.ran++, disabled: true }]`.
  - `'renders a 更多動作 button after the actions and before the gear, closed'` — `.header-more` exists with `aria-label="更多動作"`, `aria-haspopup="menu"`, `aria-expanded="false"`; DOM order `.page-actions` → `.header-more` → `.header-gear` (gear present via the file's 物資-hidden setup); no `.header-menu`.
  - `'renders no menu from 760 px or with no items, and the gear then follows the actions'` — `set('sheet')` → no `.header-more`; phone with `items = []` → none, and `.page-actions`'s `nextElementSibling` is `.header-gear` (replaces the gear-order assertion that leaves with `page-header.spec.ts:194` in Task 5).
  - `'opens on click, focuses the first item and lists the items in order'` — click → `.header-menu[role="menu"]` with `aria-expanded="true"`, three `[role="menuitem"]` texts `['匯入券商','刷新行情','停用']`, `document.activeElement` is the first item; the first is an `<a>` with `href` containing `/portfolio/import?type=broker`; the third has `aria-disabled="true"`.
  - `'runs an item and closes'` — open, click 刷新行情 → `ran === 1`, menu gone, focus back on `.header-more`; a click on 停用 → `ran` unchanged, menu stays.
  - `'arrow keys cycle, Home/End jump, Escape closes and restores focus'` — ArrowDown twice → third item; ArrowDown → first (cycle); End → third; Home → first; Escape → closed, `.header-more` focused.
  - `'closes on an outside pointerdown and when focus leaves the menu'` — open, `document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))` → closed; open, dispatch `focusout` on the panel with `relatedTarget` = the host's projected primary button (outside `.header-more-wrap`) → closed; open, dispatch `focusout` with `relatedTarget` = the second menu item → stays open.
  - `'Home and End are handled (no page scroll)'` — open, dispatch `keydown` End on the panel → `event.defaultPrevented === true`, focus on the last item.

- [ ] **Step 2: Run** `--include 'src/app/components/shell/page-header/page-header.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** `page-header.ts`: add the interface, `menu` input, `menuOpen = signal(false)`, `menuId = 'page-menu-' + <counter>`, `viewChild('more')` / `viewChildren('menuItem')`; `toggleMenu()`, `closeMenu(restoreFocus = true)`, `onMenuKeydown(event)` (return if `isHandledKey(event)`; for ArrowDown / ArrowUp / Home / End / Escape call `event.preventDefault()` then act; other keys fall through), `runItem(item)` (`if (item.disabled) return;` before `item.run?.()` and `closeMenu()`), `onMenuFocusout(event)` (close with `restoreFocus = false` when `event.relatedTarget` is null or not inside the `.header-more-wrap` element). While open, `Renderer2.listen(document, 'pointerdown', …)` closes on a target outside the host (`close(false)`); the unlisten runs on close and in `DestroyRef`. Focus the first item in `afterNextRender` / `queueMicrotask` after open. `page-header.html`: after `.page-actions` and before the trailing gear:

```html
@if (phone() && menu().length) {
  <div class="header-more-wrap">
    <button #more type="button" class="header-more" aria-label="更多動作" aria-haspopup="menu"
            [attr.aria-expanded]="menuOpen()" [attr.aria-controls]="menuId" (click)="toggleMenu()">
      <i class="pi pi-ellipsis-h" aria-hidden="true"></i>
    </button>
    @if (menuOpen()) {
      <div class="header-menu" role="menu" [id]="menuId" (keydown)="onMenuKeydown($event)" (focusout)="onMenuFocusout($event)">
        @for (item of menu(); track item.label) {
          @if (item.routerLink) {
            <a #menuItem class="header-menu-item" role="menuitem" [routerLink]="item.routerLink" [queryParams]="item.queryParams ?? null" (click)="runItem(item)">
              @if (item.icon) { <i class="pi {{ item.icon }}" aria-hidden="true"></i> }
              {{ item.label }}
            </a>
          } @else {
            <button #menuItem type="button" class="header-menu-item" role="menuitem" [attr.aria-disabled]="item.disabled || null" (click)="runItem(item)">
              @if (item.icon) { <i class="pi {{ item.icon }}" aria-hidden="true"></i> }
              {{ item.label }}
            </button>
          }
        }
      </div>
    }
  </div>
}
```

`page-header.scss`: `.header-more` = the `.header-gear` rules (44 px round, surface, border); `.header-more-wrap { position: relative; flex: 0 0 auto }`; `.header-menu { position: absolute; right: 0; top: calc(100% + 6px); min-width: 180px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: var(--radius-sm); box-shadow: var(--app-raised-shadow); padding: 4px; z-index: 1100; display: flex; flex-direction: column }`; `.header-menu-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 12px; border: 0; background: transparent; color: var(--app-text); font: inherit; text-align: left; text-decoration: none; border-radius: 6px; &:hover, &:focus-visible { background: var(--app-surface-soft); outline: none } &[aria-disabled="true"] { color: var(--app-text-muted); cursor: default } }`.

- [ ] **Step 4: Run** → PASS (all 20 existing tests too).

- [ ] **Step 5: Commit** — `feat(page-header): phone ⋯ menu for secondary actions (D9)`.

---

### Task 4: Dashboard header on phones — menu + full-width market toggle

**Files:**
- Modify: `frontend/src/app/components/ui/seg-toggle/seg-toggle.ts` (`block` input), `seg-toggle.scss`; Test: `frontend/src/app/components/ui/seg-toggle/seg-toggle.spec.ts` (create if absent)
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.html:1-17`, `dashboard.ts` (`phoneMenu` computed), `dashboard.scss` (`.market-toggle-row`)
- Test: `frontend/src/app/components/portfolio/dashboard/dashboard.spec.ts` (describe around `:354-383`)

**Interfaces:**
- Consumes: `PageMenuItem`, `menu` (Task 3).
- Produces: `SegToggleComponent.block = input(false)` → host class `block`: `:host(.block) { display: block; width: 100%; }`, `:host(.block) .seg-toggle { display: flex; width: 100%; }` and `:host(.block) .seg-toggle button { flex: 1 1 0; }` (the host has no `display` rule today and stays inline, so the inner bar alone would not stretch the host's box). Dashboard: `readonly phoneMenu = computed<PageMenuItem[]>(() => [{ label: '匯入券商', icon: 'pi-upload', routerLink: '/portfolio/import', queryParams: { type: 'broker' } }, { label: '刷新行情', icon: 'pi-refresh', run: () => this.loadSummary(), disabled: this.loading() }])`.

- [ ] **Step 1: SegToggle test** — `'block stretches the host, the group and its buttons'`: host `<app-seg-toggle [block]="true" [options]="…">` → host element has class `block`; read `seg-toggle.scss` text and assert `:host(.block)` with `display: block` and `width: 100%`, `:host(.block) .seg-toggle` with `display: flex` and `width: 100%`, and its `button` with `flex: 1 1 0`. Run → FAIL. Implement (`host: { '[class.block]': 'block()' }` + the three rules). Run → PASS.

- [ ] **Step 2: Dashboard tests** (existing helper `header(fixture)` at `:251`; phone via `TestBed.inject(LayoutModeService).set('phone')` before `detectChanges`, two-market summary fixture as `:361`):
  - `'phone: the market toggle renders under the header at full width and the header holds a ⋯ menu'` — `header().querySelector('.market-toggle')` is null; `fixture.nativeElement.querySelector('.portfolio-dashboard > .market-toggle-row app-seg-toggle.block')` exists and is the element right after `app-page-header` (`nextElementSibling`); `header().querySelector('.header-more')` exists; no `<a>` containing 匯入券商 and no `app-btn` containing 刷新行情 inside the header; `app-page-header` has no class `actions-below`.
  - `'phone: the ⋯ menu lists 匯入券商 (link) and 刷新行情 (runs the refresh, disabled while it loads, KPI skeleton shown)'` — this file mocks `PortfolioService` (`:97-127`), not HTTP; follow the refresh tests at `:196-234`: hold `refreshQuotes` on a `Subject` (or return `of({ refresh_scheduled: true })` with a `getRecalcStatus` that never completes); click `.header-more`; items `['匯入券商', '刷新行情']`; first `<a href>` includes `/portfolio/import?type=broker`; click 刷新行情 → `refreshQuotes` called once, menu closed, `.kpi-row p-skeleton` present (`dashboard.html:19` `@if (loading())`); reopen → 刷新行情 has `aria-disabled="true"`; complete the subject (`next` + `complete`, or let the status poll finish) → skeleton gone, reopen → no `aria-disabled`.
  - Replace `:343-352` (phone `actions-below` expectation) with the first phone test above; `:354-383` keep passing unchanged (default mode ≠ phone; the toggle stays in the header at ≥ 760).
  - `'phone: single market — no toggle row at all'` — one-market fixture, phone → no `.market-toggle-row`.

- [ ] **Step 3: Run** `--include 'src/app/components/portfolio/dashboard/dashboard.spec.ts'` → new tests FAIL.

- [ ] **Step 4: Implement.** `dashboard.html:2`: `<app-page-header [subtitle]="quoteLine()" [menu]="phoneMenu()">` (the header itself renders the menu only on phones, so pages pass it unconditionally); wrap the existing toggle (`:3-11`) and the two actions (`:12-16`) in `@if (!isPhone()) { … }`; after `</app-page-header>` add

```html
  @if (isPhone() && availableMarkets().length > 1) {
    <div class="market-toggle-row">
      <app-seg-toggle class="market-toggle" ariaLabel="市場" [block]="true" [options]="marketFilterOptions()" [value]="marketFilter()" (change)="selectMarketFilter($event)"></app-seg-toggle>
    </div>
  }
```

`dashboard.scss`: `.market-toggle-row { margin: -0.25rem 0 1rem; }` (the header already carries `margin-bottom: 1rem`). `dashboard.ts`: import `PageMenuItem`, add `phoneMenu`.

- [ ] **Step 5: Run** dashboard + seg-toggle specs → PASS.

- [ ] **Step 6: Commit** — `feat(dashboard): phone header one row — ⋯ menu, market toggle under the header (D9)`.

---

### Task 5: 交易紀錄 and 現金帳戶 headers; delete `actionsBelowOnPhone`

**Files:**
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.html:4-10`, `transaction-list.ts` (`phoneMenu`); Test: `transaction-list.spec.ts:134-168`
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-page.html:21-24`, `accounts-page.ts` (`phone` computed, `phoneMenu`); Test: `accounts-page.spec.ts:291-316` (the shared sheet + phone loop) — `:154-159` and `:262-275` are the **panes** describe (`:140`, `layout.set('panes')`) and stay untouched
- Modify: `frontend/src/app/components/shell/page-header/page-header.ts:28,37` (delete `actionsBelowOnPhone`, `actionsBelow`, the host class), `page-header.html:11-17` (one gear position), `page-header.scss` (`:host(.actions-below)` block); Test: `page-header.spec.ts:31-35` (`ActionsBelowHost` + its `render()` parameter) and `:169-196` (the describe) — both deleted

**Interfaces:** Consumes `PageMenuItem` / `menu` (Task 3). Produces nothing new.

- [ ] **Step 1: Transactions tests** (`transaction-list.spec.ts`): replace `:164-168` (actions-below) with `'phone: 匯入 CSV moves into the ⋯ menu and 新增交易 stays in the row'` — phone mode: no `<a>` with 匯入 CSV in the header, `app-page-header app-btn` 新增交易 present, `.header-more` present; open → one item `匯入 CSV` as `<a href="/portfolio/import">`; `app-page-header` has no class `actions-below`. Keep `:142` (links 匯入 CSV at default width) as is.

- [ ] **Step 2: Accounts-page tests** (`accounts-page.spec.ts`): leave the panes describe (`:140-275`, including `:154-159` and `:262-275`) unchanged — panes keep both inline `btn-soft` buttons. Split the shared `sheet` + `phone` loop (`:291-316`): **sheet** still expects two inline `btn-soft` `app-btn`s (記錄入出金, 新增帳戶) and no `.header-more`; **phone** expects one header `app-btn` (記錄入出金, `btn-soft`), `.header-more` present, and after opening it a `[role="menuitem"]` 新增帳戶 whose click opens the create dialog exactly as the inline button did. `:316` (`actions-below` only on phone) → the class is absent in both modes.

- [ ] **Step 3: Run** both specs → the new assertions FAIL.

- [ ] **Step 4: Implement.** `transaction-list.html:4`: `<app-page-header [subtitle]="headerSubtitle()" [menu]="phoneMenu">`, wrap the 匯入 CSV `<a>` in `@if (!isPhone())`; `transaction-list.ts`: `readonly phoneMenu: PageMenuItem[] = [{ label: '匯入 CSV', icon: 'pi-upload', routerLink: '/portfolio/import' }]`. `accounts-page.ts`: `readonly phone = computed(() => this.layout.mode() === 'phone')`, `readonly phoneMenu: PageMenuItem[] = [{ label: '新增帳戶', icon: 'pi-plus', run: () => this.openCreateDialog() }]`; `accounts-page.html:21`: `<app-page-header title="現金帳戶" subtitle="…" [menu]="phoneMenu">`, keep 記錄入出金, wrap 新增帳戶 in `@if (!phone())`.

- [ ] **Step 5: Delete `actionsBelowOnPhone`.** `page-header.ts`: remove the input, `actionsBelow`, the `host` class binding; `page-header.html`: one gear render after `.header-more-wrap`; `page-header.scss`: remove the `:host(.actions-below)` block and its comment; `page-header.spec.ts`: remove `ActionsBelowHost` (`:31-35`) and the `render()` parameter that selects it, and the describe at `:169-196` (its gear-order assertion `:194` is covered by the Task 3 "no items" test). `dashboard.spec.ts:343-352` was replaced in Task 4; `accounts-page.spec.ts:316` in Step 2. `grep -rn "actionsBelowOnPhone\|actions-below\|ActionsBelowHost" frontend/src` → no matches.

- [ ] **Step 6: Run** transaction-list, accounts-page, page-header, dashboard specs → PASS.

- [ ] **Step 7: Commit** — `feat(headers): 交易紀錄 / 現金帳戶 phone ⋯ menu; drop actionsBelowOnPhone (D9)`.

---

### Task 6: Phone holdings rows — two lines

**Files:**
- Modify: `frontend/src/app/components/portfolio/dashboard/dashboard.html:278-289` (wrap in `.stock-meta`), `dashboard.scss:512-540` (right-align selector), `:697-740` (phone grid)
- Test: `frontend/src/app/components/portfolio/dashboard/dashboard.spec.ts` (holdings describe `~:1281-1345`)

**Interfaces:** Produces `.stock-meta` (wraps `.stock-qty`, `.stock-price`, `.stock-day`); `.stock-qty` keeps its text (`10`).

- [ ] **Step 1: Tests** — in the holdings describe: `'wraps 股數 / 現價 / 今日 in one meta span, in that order'` (every `.stock-row .stock-meta` has children with classes `['stock-qty','stock-price','stock-day']`; `.stock-qty` text still `10` for the TW row at `:1327`); `'the phone stylesheet lays a collapsed row out as two lines'` — read `dashboard.scss` text: inside the `@media (max-width: 759.98px)` block (the shell's phone query, `layout-mode.service.ts:8`; no `max-width: 760px` query remains in the file) `grid-template-areas:` is exactly `'main value' 'meta pnl'` (whitespace-insensitive), `.stock-meta` has `display: flex`, `.stock-qty::after { content: ' 股' }`, no `股數 ` rule remains, and `.stock-sym { flex-direction: row` is present; outside the media block `.stock-meta { display: contents }`.

- [ ] **Step 2: Run** `--include '…dashboard.spec.ts'` → FAIL.

- [ ] **Step 3: Implement.** Template: `<span class="stock-meta">` around the three spans (`:278-289`). SCSS: in `.stock-row` replace `> span:not(.stock-main) { text-align: right }` with `.stock-qty, .stock-price, .stock-day, .stock-value, .stock-pnl { text-align: right }`; add `.stock-meta { display: contents; }` at the desktop level. Phone block (`:697-740`; change its query from `(max-width: 760px)` to `(max-width: 759.98px)` so 760 px itself keeps the six-column grid): `.stock-row { gap: 0.25rem 0.75rem; grid-template-areas: 'main value' 'meta pnl'; grid-template-columns: minmax(0, 1fr) auto; padding: 0.75rem 0.25rem; }`; `.stock-meta { color: var(--app-text-muted); display: flex; font-size: 0.75rem; font-weight: 600; gap: 0.375rem; grid-area: meta; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`; `.stock-qty::after { content: ' 股'; }`; `.stock-price::before, .stock-day::before { content: '· '; }`; `.stock-price, .stock-day, .stock-qty { text-align: left; }` and drop their phone-only `font-size` / `color` rules so they inherit from `.stock-meta` (set no `color` on `.stock-day`: its `.up` / `.down` trend colour must keep winning); `.stock-sym { align-items: baseline; flex-direction: row; gap: 0.375rem; .n { font-size: 0.75rem; color: var(--app-text-muted); } }`; remove the old `price` / `day` / `qty` grid-area rules and `.stock-qty::before`. Keep `.stock-value` and `.stock-pnl` rules.

- [ ] **Step 4: Run** → PASS (including `:1287` column headers, `:1319`, `:1337`).

- [ ] **Step 5: Commit** — `feat(dashboard): phone holdings rows on two lines (D9 density)`.

---

### Task 7: Suite, build, spec validation

**Files:** none (verification only).

- [ ] **Step 1:** `cd frontend && npx ng test --watch=false 2>&1 | tail -15` → all files pass; record the counts.
- [ ] **Step 2:** `npx ng build 2>&1 | tail -20` → exit 0; only the pre-existing protobufjs warning.
- [ ] **Step 3:** repo root `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` → valid.
- [ ] **Step 4:** no commit unless something had to change (then `fix(pr-d): …`).

---

### Task 8: Browser verification (D5 + D9) with stubbed data

**Files:**
- Create: `~/reports/home-hub-playwright-stubs/pr_d_shots.py` (copy the stub routing from `pr_c_shots.py`; fictional fixtures only)
- Create: evidence dir `~/reports/home-hub-polish-pr-d-<UTC TS>/` with `NOTES.md`, `probe.json`, PNGs, `ng-test.log`, `ng-build.log`, `openspec.log`

**Interfaces:** none.

- [ ] **Step 1: Build + serve.** `npx ng build` output copied flat to `~/workspace/home-hub-demo-preview/redesign-v2-pr101/dist` (the server's hard-coded ROOT); start `python3 -I redesign_server.py` (127.0.0.1:18081; never touch 18080); stop it by PID from `ss -ltnp` afterwards.

- [ ] **Step 2: Probes** (Playwright 1.49.1 in `mcr.microsoft.com/playwright/python:v1.49.1-noble`, `--network host`, plain `python3`; light + dark):
  - D5 at 1440 × 900 on `/portfolio/transactions` with 40 stubbed rows: `mouse.wheel(0, 600)` → wait 250 ms → `getComputedStyle(.hub-fab).visibility === 'hidden'` and `opacity === '0'`; `wheel(0, −10)` → visible within 250 ms; wheel to the bottom → visible; wheel down then wait 1.4 s → visible; `fab.focus()` + wheel down → visible; `emulate_media(reduced_motion='reduce')` → `transition-duration` is `0s`; while hidden, `elementFromPoint` at a row ⋯ button centre near the bottom is that button (nothing covered). Navigate from the scrolled 交易紀錄 to `/portfolio/dividends` → visible (Review Focus 2).
  - D5 compact height 1280 × 800 (`.hub-main` scroller): same hide / show.
  - D5 accounting panes 1440 × 900 on `/accounting` (list pane scroller, 60 stubbed entries): wheel over the list → hidden; wheel up → visible.
  - D9 at 390 × 844 on `/portfolio` (two markets), `/portfolio/transactions`, `/portfolio/accounts`: `.page-header-row` `getBoundingClientRect().height ≤ 64`; `h1`, `.header-more` (and `.header-gear` when present) share the same row (`top` within 8 px); on `/portfolio` the `.market-toggle-row app-seg-toggle` sits directly below the header and its width equals `.hub-page` content width (±2 px); open `.header-more` → every `[role="menuitem"]` rect inside the viewport and `elementFromPoint` at its centre is the item (Review Focus 4); item texts match the spec. `/portfolio/accounts/7` and `/accounting` headers also ≤ 64 px.
  - Holdings rows at 390: every collapsed `.stock-row` `clientHeight ≤ 72`; inside one row `.stock-sym .s` and `.n` share a `top` (one line); `.stock-meta` text matches `/^\d[\d,]* 股 · .+ · [+−-].+\)$/`.
  - Screenshots `<page>-<width>-<theme>.png`; `probe.json` with every measurement; `NOTES.md` summarising.

- [ ] **Step 3: Fix** anything the probe contradicts in the task that owns it (fix commit `fix(pr-d): …`), re-probe, then record the final head in `NOTES.md`.

- [ ] **Step 4: Copy** `ng-test.log`, `ng-build.log`, `openspec.log` from Task 7 into the evidence dir. No commit (evidence lives outside the repo).
