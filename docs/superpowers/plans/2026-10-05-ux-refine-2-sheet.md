# UX Refine 2 — Sheet Gesture, Focus and Unsaved Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The 760–1023 px side sheet can no longer be closed by accident while typing, keeps keyboard focus inside itself, and never drops unsaved entry-form input silently — in the phone layout too (spec §4, plus the fee/fx sheets' half of the §3.1 overlay keyboard contract).

**Architecture:** A shared `trapFocus(container, event)` helper in `accounting-ui.ts` wraps Tab inside the sheet pane and inside every element marked `data-overlay`. A root `DirtyFormRegistry` service holds the one registered `DirtyAware` form; every exit (layout ✕ / backdrop / swipe / Esc, form ✕ / Esc / shortcut) goes through `registry.requestClose(run)`, which runs immediately for a clean form and otherwise asks the entry form to show its own 放棄 strip. The fee and fx sheets get their own host `keydown` handlers (Esc closes only that sheet, Tab wraps inside it), and the sheet's swipe moves to a dedicated 56 px grip.

**Tech Stack:** Angular 21 standalone components with signals and `@if`/`@for` control flow, `afterNextRender`, SCSS, Vitest via `@angular/build:unit-test` (jsdom), `RouterTestingHarness`, `HttpTestingController`.

**Spec:** `docs/superpowers/specs/2026-10-05-ux-refinement-design.md` (binding; §4 and the "Overlay keyboard contract" paragraph of §3.1). Fact sheet: `.superpowers/ux/facts-ux-refine.md` (base `main` @ b8d4d77).

**Branch:** `feat/ux-refine-2-sheet`. Prerequisite: PR-1 (`feat/ux-refine-1-states-wording`) merged into `main`; branch from the updated `main`. If PR-1 is not merged yet, branch from `feat/ux-refine-1-states-wording` and rebase onto `main` once it merges (PR-1 also edits `entry-form.html/.ts`; anchors below quote code so they survive the shift).

## Global Constraints

- Angular 21 standalone components with signals; no new dependencies.
- All user-visible strings in 繁體中文; the standard noun for an entry is 記錄.
- No backend or OpenAPI change. Existing endpoints only: `GET /entries` (page `{items,total,limit,offset}`), `GET /entries/summary`, `GET /entries/summary/daily`, `GET /accounts` (with balances and `group_id`/`group_name`), `GET /imports/latest`, `GET /preference`.
- Every behavioural change has a Vitest spec in the component's existing `*.spec.ts` (or a new sibling spec for a new component). `cd frontend && npm test` must pass; `npm run build` must pass.
- Layout modes come from `LayoutModeService` (`phone` <760px, `sheet` 760–1023px, `panes` ≥1024px, `compactHeight` ≤820px tall); every change is checked at 390×844, 760×820, 1280×800 on the demo preview.
- Accessibility: every new interactive element has an accessible name; keyboard operation (Tab, ↑↓, Enter, Esc) works for every new control; `prefers-reduced-motion` disables new transitions.
- Minimum font size in the accounting feature is `.72rem`.
- Frontend only: no backend files, no `openspec/` changes, no `.env` changes, no deploy actions.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The PR description ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Test command (non-watch; `npm test` is `ng test`, builder `@angular/build:unit-test`): `cd frontend && npm test -- --watch=false`. One spec file: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/<path>.spec.ts`.
- Build command: `cd frontend && npm run build` (needs the repo-root `.env` for `set-env.js`; without one, use `cd frontend && npx ng build`; never create or edit `.env`).
- Line anchors are for base `b8d4d77`; match on the quoted code.
- The app runs zoneless: `fixture.detectChanges()` / `harness.detectChanges()` call `ApplicationRef.tick()`, which also runs `afterNextRender` callbacks, so tests see focus moves and render-time snapshots after `settle()`.

## Shared contracts this PR defines (PR-3 consumes them unchanged)

```ts
// frontend/src/app/components/accounting/accounting-ui.ts
export const OVERLAY_ATTR = 'data-overlay';
export const FOCUSABLE_SELECTOR: string;
export function focusables(container: HTMLElement): HTMLElement[];
/** Tab / Shift+Tab wraps inside `container`; returns true (and preventDefaults) when it moved focus. */
export function trapFocus(container: HTMLElement, event: KeyboardEvent): boolean;

// frontend/src/app/components/accounting/dirty-form.service.ts
export interface DirtyAware { isDirty(): boolean; showDiscardPrompt(): void }
export class DirtyFormRegistry {
  readonly promptOpen: Signal<boolean>;
  register(form: DirtyAware): void;
  unregister(form?: DirtyAware): void;
  requestClose(run: () => void): void;
  confirmDiscard(): void;
  cancelDiscard(): void;
  clearPending(): void;
}
```

**Overlay contract:** an overlay root carries the attribute `data-overlay`. Its component's host `keydown` handler (1) ignores `isHandledKey(event)`, (2) on `Escape` closes only itself and calls `event.preventDefault()`, (3) on `Tab` calls `trapFocus(<its dialog element>, event)`. The layout's pane-level Tab wrap skips any event whose target is inside a `[data-overlay]` element.

## Review Focus

1. **Save-and-continue resets the snapshot** — input: type a record, ⇧⏎ (save and continue), then Esc. Expected: the form leaves at once; no `放棄未儲存的內容？`. Pinned in **Task 3** (`is clean after load, dirty after typing…`) and **Task 4** (`leaves without asking after save-and-continue`).
2. **Pane moves to another record** — input: sheet mode, dirty edit of entry 9, the route changes to `/accounting/entries/10/edit`. Expected: no strip, no blocked navigation; entry 10 loads with `isDirty() === false`. Pinned in **Task 4** (`does not prompt when the pane moves to another record…`).
3. **Esc inside the fee / fx sheet** — input: dirty draft, fee sheet open, Esc. Expected: only the fee sheet closes; the draft, the form and the layout sheet stay; the next Esc shows the strip; only 放棄 leaves. Pinned in **Task 5**.
4. **Swipe from a field** — input: pointerdown in the name input (or anywhere outside the grip), move 100 px right; and on the grip dx 100 / dy 60. Expected: neither closes; grip dx 100 / dy 10 closes. Pinned in **Task 6**.
5. **Focus containment** — input: sheet open, focus on the last focusable element in the pane, Tab; Shift+Tab on ✕; then close the sheet. Expected: Tab wraps to ✕, Shift+Tab wraps to the last element, the list pane and `app-dock` carry `inert` only while the sheet is open (also removed if the layout is destroyed with the sheet open). Pinned in **Task 7**.

---

### Task 1: `trapFocus` helper and overlay marker

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-ui.ts` (after `focusSheetField` :141-144)
- Test: `frontend/src/app/components/accounting/accounting-ui.spec.ts`

**Interfaces:**
- Consumes: `isHandledKey` (same file).
- Produces: `OVERLAY_ATTR`, `FOCUSABLE_SELECTOR`, `focusables(container)`, `trapFocus(container, event): boolean` (signatures above).

- [ ] **Step 1: Write the failing test** (add `FOCUSABLE_SELECTOR, focusables, trapFocus` to the import list)

```ts
  describe('trapFocus', () => {
    function build(): { box: HTMLElement; first: HTMLButtonElement; middle: HTMLInputElement; last: HTMLButtonElement } {
      const box = document.createElement('div');
      box.innerHTML = `
        <button class="first">a</button>
        <button disabled>skip</button>
        <input class="middle" />
        <div tabindex="-1">skip</div>
        <span inert><button>skip</button></span>
        <button class="last">b</button>`;
      document.body.appendChild(box);
      return {
        box,
        first: box.querySelector('.first')!,
        middle: box.querySelector('.middle')!,
        last: box.querySelector('.last')!,
      };
    }

    const tab = (shiftKey = false) => new KeyboardEvent('keydown', { key: 'Tab', shiftKey, cancelable: true });

    it('lists only reachable focusables', () => {
      const { box, first, middle, last } = build();
      expect(FOCUSABLE_SELECTOR).toContain('button:not([disabled])');
      expect(focusables(box)).toEqual([first, middle, last]);
      box.remove();
    });

    it('wraps Tab from the last to the first and Shift+Tab from the first to the last', () => {
      const { box, first, middle, last } = build();
      last.focus();
      const forward = tab();
      expect(trapFocus(box, forward)).toBe(true);
      expect(forward.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(first);

      const back = tab(true);
      expect(trapFocus(box, back)).toBe(true);
      expect(document.activeElement).toBe(last);

      middle.focus();
      const inner = tab();
      expect(trapFocus(box, inner)).toBe(false);
      expect(inner.defaultPrevented).toBe(false);
      box.remove();
    });

    it('pulls focus back in from outside, ignores other keys and handled events, and holds an empty container', () => {
      const { box, first } = build();
      const outside = document.createElement('button');
      document.body.appendChild(outside);
      outside.focus();
      expect(trapFocus(box, tab())).toBe(true);
      expect(document.activeElement).toBe(first);

      expect(trapFocus(box, new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }))).toBe(false);
      const handled = tab();
      handled.preventDefault();
      expect(trapFocus(box, handled)).toBe(false);

      const empty = document.createElement('div');
      empty.tabIndex = -1;
      document.body.appendChild(empty);
      const held = tab();
      expect(trapFocus(empty, held)).toBe(true);
      expect(document.activeElement).toBe(empty);
      [box, outside, empty].forEach(node => node.remove());
    });
  });
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounting-ui.spec.ts`
Expected: FAIL — `focusables is not a function` / `trapFocus is not a function`.

- [ ] **Step 3: Implement** — in `accounting-ui.ts`, after `focusSheetField`:

```ts
// ---- focus containment ----------------------------------------------------------------------------------------

/** Root attribute of an overlay (picker, fee / fx sheet, 新增拆帳行): it traps its own Tab; outer traps skip it. */
export const OVERLAY_ATTR = 'data-overlay';

export const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]',
].join(',');

/** Elements Tab can reach inside `container`, in DOM order (no negative tabindex, nothing inert or disabled). */
export function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    element => element.tabIndex >= 0 && !element.hidden && !element.closest('[inert]') && !element.matches(':disabled'),
  );
}

/**
 * Tab / Shift+Tab wraps between the first and last focusable element of `container` (focus outside it is pulled in;
 * an empty container keeps focus on itself). Returns true, with the event default-prevented, when it moved focus;
 * a Tab between two inner elements is left to the browser. Handled keys and IME commits are ignored.
 */
export function trapFocus(container: HTMLElement, event: KeyboardEvent): boolean {
  if (event.key !== 'Tab' || isHandledKey(event)) {
    return false;
  }
  const items = focusables(container);
  if (items.length === 0) {
    event.preventDefault();
    container.focus();
    return true;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = container.ownerDocument.activeElement as HTMLElement | null;
  const inside = !!active && container.contains(active);
  const atEdge = event.shiftKey ? active === first || active === container : active === last;
  if (!inside || atEdge) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return true;
  }
  return false;
}
```

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-ui.ts frontend/src/app/components/accounting/accounting-ui.spec.ts
git commit -m "feat(accounting): trapFocus helper and data-overlay marker

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `DirtyFormRegistry` service

**Files:**
- Create: `frontend/src/app/components/accounting/dirty-form.service.ts`
- Test: `frontend/src/app/components/accounting/dirty-form.service.spec.ts` (new)

**Interfaces:**
- Produces: `DirtyAware`, `DirtyFormRegistry` (signatures in "Shared contracts").

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/components/accounting/dirty-form.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { DirtyAware, DirtyFormRegistry } from './dirty-form.service';

function form(dirty: boolean): DirtyAware & { showDiscardPrompt: ReturnType<typeof vi.fn> } {
  return { isDirty: () => dirty, showDiscardPrompt: vi.fn() };
}

describe('DirtyFormRegistry', () => {
  const registry = () => TestBed.inject(DirtyFormRegistry);

  it('runs the close at once without a form or with a clean one', () => {
    const run = vi.fn();
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);

    const clean = form(false);
    registry().register(clean);
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(2);
    expect(clean.showDiscardPrompt).not.toHaveBeenCalled();
    expect(registry().promptOpen()).toBe(false);
  });

  it('holds the close of a dirty form until 放棄, and drops it on 留下', () => {
    const dirty = form(true);
    const run = vi.fn();
    registry().register(dirty);

    registry().requestClose(run);
    expect(run).not.toHaveBeenCalled();
    expect(dirty.showDiscardPrompt).toHaveBeenCalledTimes(1);
    expect(registry().promptOpen()).toBe(true);
    registry().cancelDiscard();
    expect(registry().promptOpen()).toBe(false);
    registry().confirmDiscard();
    expect(run).not.toHaveBeenCalled();

    registry().requestClose(run);
    registry().confirmDiscard();
    expect(run).toHaveBeenCalledTimes(1);
    expect(registry().promptOpen()).toBe(false);
  });

  it('clears a pending close on clearPending and on unregister, ignoring another form unregistering', () => {
    const dirty = form(true);
    const other = form(false);
    const run = vi.fn();
    registry().register(dirty);
    registry().requestClose(run);
    registry().clearPending();
    expect(registry().promptOpen()).toBe(false);
    registry().confirmDiscard();
    expect(run).not.toHaveBeenCalled();

    registry().requestClose(run);
    registry().unregister(other);
    expect(registry().promptOpen()).toBe(true);
    registry().unregister(dirty);
    expect(registry().promptOpen()).toBe(false);
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/dirty-form.service.spec.ts`
Expected: FAIL — `Could not resolve "./dirty-form.service"`.

- [ ] **Step 3: Implement**

```ts
// frontend/src/app/components/accounting/dirty-form.service.ts
import { Injectable, signal } from '@angular/core';

/** A form that can hold unsaved input (the entry form). */
export interface DirtyAware {
  isDirty(): boolean;
  showDiscardPrompt(): void;
}

/**
 * The one guard for every exit of the entry form in both layouts (spec §4.3): layout ✕ / backdrop / swipe / Esc and
 * the form's own ✕ / Esc / shortcut call `requestClose(run)`. A clean (or no) form closes at once; a dirty one shows
 * its 放棄 strip and keeps `run` pending until 放棄 (`confirmDiscard`) or 留下 (`cancelDiscard`).
 */
@Injectable({ providedIn: 'root' })
export class DirtyFormRegistry {
  private form: DirtyAware | null = null;
  private pending: (() => void) | null = null;
  private readonly prompting = signal(false);
  /** True while the registered form should show its 放棄未儲存的內容？ strip. */
  readonly promptOpen = this.prompting.asReadonly();

  register(form: DirtyAware): void {
    this.form = form;
    this.clearPending();
  }

  /** Without an argument, or with the registered form: forget it and any pending close. */
  unregister(form?: DirtyAware): void {
    if (form && form !== this.form) {
      return;
    }
    this.form = null;
    this.clearPending();
  }

  requestClose(run: () => void): void {
    const form = this.form;
    if (!form || !form.isDirty()) {
      this.clearPending();
      run();
      return;
    }
    this.pending = run;
    this.prompting.set(true);
    form.showDiscardPrompt();
  }

  /** 放棄: run the pending close. */
  confirmDiscard(): void {
    const run = this.pending;
    this.clearPending();
    run?.();
  }

  /** 留下: keep the draft. */
  cancelDiscard(): void {
    this.clearPending();
  }

  /** A save (or save-and-continue) succeeded: nothing is left to discard. */
  clearPending(): void {
    this.pending = null;
    this.prompting.set(false);
  }
}
```

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/dirty-form.service.ts frontend/src/app/components/accounting/dirty-form.service.spec.ts
git commit -m "feat(accounting): DirtyFormRegistry guards closing a form with unsaved input

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Entry form `isDirty` (snapshot after load, reset after save)

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts` (imports :2-12; fields `rulesTouched` :265 and its uses :428, :564, :673, :768, :808; constructor subscriber :444-452; `applyLoaded` end :638; `applyDefinition` end :1176; `finish` :1356-1362; `continueEntry` :1365-1381; `start` :500)
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts` (computed members near `rateLabel`)
- Test: `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts`

**Interfaces:**
- Consumes: `ScheduleDraft` (existing).
- Produces: `EntryFormComponent.isDirty: Signal<boolean>`; private `markClean(): void`; `TransferPanelComponent.draftKey: Signal<string>`; exported `scheduleDraftKey(draft: ScheduleDraft): unknown` in `entry-form.ts`.

- [ ] **Step 1: Write the failing tests** (append inside `describe('EntryFormComponent')`)

```ts
  describe('unsaved input', () => {
    it('is clean after load, dirty after typing a name, clean again after save-and-continue and after save', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
      expect(form.isDirty()).toBe(false);

      typeInto(el, '.name-input', '午餐');
      expect(form.isDirty()).toBe(true);

      tap(el, '.cat', '飲食');
      tap(el, '.cat', '午餐');
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      keys(el, '1', '7', '0');
      form.save(true);
      settle();
      httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
        .flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
      settle();
      expect(left()).toBe(false);
      expect(form.isDirty()).toBe(false);

      typeInto(el, '.name-input', '晚餐');
      keys(el, '2', '0', '0');
      expect(form.isDirty()).toBe(true);
      form.save(false);
      settle();
      httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
        .flush(makeEntryDetail({ id: 100, amount: '-200.0000', account_id: 2, category_id: 12 }));
      settle();
      expect(left()).toBe(true);
      expect(form.isDirty()).toBe(false);
    });

    it('is clean right after an edit loads, even once its category and account detail arrive', async () => {
      const { el } = await open('/accounting/entries/9/edit');
      respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
      expect(form.isDirty()).toBe(false);
      typeInto(el, '.name-input', '改名');
      expect(form.isDirty()).toBe(true);
    });

    it('is clean after a transfer form loads and dirty once an amount is typed', async () => {
      const { el } = await open('/accounting/entry?kind=transfer');
      respondTransferCategories();
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
      expect(form.isDirty()).toBe(false);
      typeInto(el, 'app-transfer-panel .out-amount', '3000');
      expect(form.isDirty()).toBe(true);
    });
  });
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: FAIL — `form.isDirty is not a function`.

- [ ] **Step 3: Implement**

`transfer-panel.ts` — below `readonly rateLabel = computed(…)`:

```ts
  /** What the owner typed or chose in the panel, for the form's unsaved-input check (category defaults excluded). */
  readonly draftKey = computed(() =>
    JSON.stringify([this.fromId(), this.toId(), this.outText(), this.inText(), this.children()]),
  );
```

`entry-form.ts`:

1. Imports: add `Injector`, `afterNextRender` to the `@angular/core` import.
2. Above the `@Component` decorator add:

```ts
/** The schedule part of the unsaved-input key: dates that still follow the entry date, and the rule day, are derived. */
export function scheduleDraftKey(draft: ScheduleDraft): unknown {
  if (draft.tab === 'single') {
    return 'single';
  }
  const { start, firstDate, dayOfMonth: _day, dayHydrated: _hydrated, ...rest } = draft;
  return { ...rest, start: draft.startTouched ? start : null, firstDate: draft.firstTouched ? firstDate : null };
}
```

3. Fields: add `private readonly injector = inject(Injector);` next to `private readonly host = …`. Replace `private rulesTouched = false;` with `private readonly rulesTouched = signal(false);` and update every use: `!this.rulesTouched` → `!this.rulesTouched()` (in the `accountId` subscriber), `this.rulesTouched = false;` → `this.rulesTouched.set(false);` (in `resetFields` and `setAccount`), `this.rulesTouched = true;` → `this.rulesTouched.set(true);` (in `applyDetail` and `toggleRule`).
4. Below `readonly scheduling = computed(…)`:

```ts
  /** Serialised draft as the save would read it; derived values (auto rule ids, defaulted dates) are left out. */
  private readonly formState = computed(() =>
    JSON.stringify([
      this.kind(),
      this.accountId(),
      this.projectId(),
      this.category()?.id ?? this.pendingCategoryId,
      this.amountExpr(),
      this.targetExpr(),
      this.name(),
      this.merchant(),
      this.counterpartyName(),
      this.entryDate(),
      this.entryTime(),
      this.postedDate(),
      this.invoiceNumber(),
      this.invoiceRandom(),
      this.description(),
      this.tags(),
      this.rulesTouched() ? this.ruleIds() : null,
      this.fx(),
      this.fee(),
      this.discount(),
      this.members(),
      scheduleDraftKey(this.scheduleDraft()),
      this.kind() === 'transfer' ? (this.transferPanel()?.draftKey() ?? null) : null,
    ]),
  );
  /** Snapshot of `formState` once the record (or blank form) has rendered; null while loading. */
  private readonly baseline = signal<string | null>(null);
  /** The draft differs from what was loaded (spec §4.3). */
  readonly isDirty = computed(() => {
    const baseline = this.baseline();
    return baseline !== null && baseline !== this.formState();
  });
```

5. Add the method (next to `resetFields`):

```ts
  /** Takes the clean snapshot after the next render, once child effects (schedule dates, transfer legs) settled. */
  private markClean(): void {
    this.baseline.set(null);
    afterNextRender(() => this.baseline.set(this.formState()), { injector: this.injector });
  }
```

6. Call sites:
   - `start(…)`: first line `this.baseline.set(null);`.
   - In the constructor's `combineLatest(…).subscribe(([params, query]) => { … })`, replace its body with:

```ts
        const schedule = query.get('schedule');
        const target = this.start(params.get('id'), query.get('kind'), query.get('copy'), schedule);
        this.load(target);
        if (schedule !== null) {
          this.loadDefinition(Number(schedule));
        } else if (target === null) {
          this.markClean();
        }
```

   - End of `applyLoaded(…)` (after `this.loading.set(false);`): `this.markClean();`.
   - End of `applyDefinition(…)`: `this.markClean();`.
   - `continueEntry()`: last line `this.markClean();`.
   - `finish(keepGoing)`: first line `this.markClean();` (covers save, save-and-continue, catch-up and the fee prompt paths).

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts --include src/app/components/accounting/transfer-panel/transfer-panel.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/entry-form/ frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts
git commit -m "feat(entry-form): isDirty against a snapshot taken after load and after each save

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Entry form registers with the registry and shows the 放棄 strip

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts` (class declaration :169; `ngOnInit` :473; `cancel()` :1383-1385)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.html` (after `</header>` :22)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.scss`
- Test: `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts`

**Interfaces:**
- Consumes: `DirtyFormRegistry`, `DirtyAware` (Task 2); `isDirty` (Task 3).
- Produces: `EntryFormComponent implements OnInit, OnDestroy, DirtyAware`; `showDiscardPrompt(): void`; `protected readonly registry: DirtyFormRegistry` (template); `cancel()` → `registry.requestClose(() => this.doCancel())`; strip markup `.discard-strip[role=alertdialog]` with `.discard-stay` (留下) and `.discard-leave` (放棄).

- [ ] **Step 1: Write the failing tests** (inside `describe('unsaved input')`; add `import { DirtyFormRegistry } from '../dirty-form.service';` at the top of the spec)

```ts
    it('asks before ✕ or Esc drops a dirty draft on a phone; 留下 keeps it, 放棄 leaves', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      typeInto(el, '.name-input', '午餐');

      el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      settle();
      const strip = el.querySelector('.discard-strip')!;
      expect(text(strip)).toContain('放棄未儲存的內容？');
      expect(strip.getAttribute('role')).toBe('alertdialog');
      expect(document.activeElement).toBe(el.querySelector('.discard-stay'));
      expect(left()).toBe(false);

      (el.querySelector('.discard-stay') as HTMLButtonElement).click();
      settle();
      expect(el.querySelector('.discard-strip')).toBeNull();
      expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('午餐');

      (el.querySelector('.topbar .cancel') as HTMLButtonElement).click();
      settle();
      expect(el.querySelector('.discard-strip')).not.toBeNull();
      (el.querySelector('.discard-leave') as HTMLButtonElement).click();
      settle();
      expect(left()).toBe(true);
    });

    it('leaves without asking after save-and-continue', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      tap(el, '.cat', '飲食');
      tap(el, '.cat', '午餐');
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      keys(el, '1', '7', '0');
      el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }));
      settle();
      httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
        .flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
      settle();

      el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      settle();
      expect(el.querySelector('.discard-strip')).toBeNull();
      expect(left()).toBe(true);
    });

    it('shows the strip when the layout asks to close the sheet over a dirty draft', async () => {
      const { el } = await open('/accounting/entry', 'sheet');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      typeInto(el, '.name-input', '午餐');
      const run = vi.fn();

      TestBed.inject(DirtyFormRegistry).requestClose(run);
      settle();
      expect(el.querySelector('.discard-strip')).not.toBeNull();
      expect(run).not.toHaveBeenCalled();
      (el.querySelector('.discard-leave') as HTMLButtonElement).click();
      settle();
      expect(run).toHaveBeenCalledTimes(1);
    });

    it('does not prompt when the pane moves to another record, and the new record starts clean', async () => {
      const { el, left } = await open('/accounting/entries/9/edit', 'sheet');
      respond('/api/accounting/entries/9', makeEntryDetail({ id: 9, account_id: 2, category_id: 12, amount: '-170.0000' }));
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      typeInto(el, '.name-input', '改名');

      await harness.navigateByUrl('/accounting/entries/10/edit');
      settle();
      respond('/api/accounting/entries/10', makeEntryDetail({ id: 10, account_id: 2, category_id: 12, amount: '-80.0000' }));
      httpMock.match(r => r.url === '/api/accounting/accounts/2').forEach(req => req.flush(makeAccountDetail({ id: 2 })));
      settle();

      const current = harness.routeDebugElement!.componentInstance as EntryFormComponent;
      expect(harness.routeNativeElement!.querySelector('.discard-strip')).toBeNull();
      expect(left()).toBe(false);
      expect(current.isDirty()).toBe(false);
      expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
    });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: FAIL — `.discard-strip` is null (`Cannot read properties of null (reading 'getAttribute')`), and Esc leaves at once (`expected true to be false`).

- [ ] **Step 3: Implement**

`entry-form.ts`:
- Imports: add `OnDestroy` to `@angular/core`; add `import { DirtyAware, DirtyFormRegistry } from '../dirty-form.service';`.
- `export class EntryFormComponent implements OnInit {` → `export class EntryFormComponent implements OnInit, OnDestroy, DirtyAware {`.
- Field: `protected readonly registry = inject(DirtyFormRegistry);`.
- `ngOnInit()`: first line `this.registry.register(this);`.
- Add:

```ts
  ngOnDestroy(): void {
    this.registry.unregister(this);
  }

  /** DirtyAware: the strip is rendered from `registry.promptOpen()`; 留下 takes the focus. */
  showDiscardPrompt(): void {
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>('.discard-stay')?.focus(), {
      injector: this.injector,
    });
  }
```

- Replace `cancel()`:

```ts
  /** ✕, Esc and the cancel shortcut: a dirty draft first asks 放棄未儲存的內容？ (DirtyFormRegistry). */
  cancel(): void {
    this.registry.requestClose(() => this.doCancel());
  }

  private doCancel(): void {
    this.markClean();
    this.leave();
  }
```

- In `finish(keepGoing)` after `this.markClean();` add `this.registry.clearPending();`.

`entry-form.html` — directly after `</header>`:

```html
  @if (registry.promptOpen()) {
    <div class="discard-strip" role="alertdialog" aria-label="放棄未儲存的內容？">
      <span>放棄未儲存的內容？</span>
      <span class="discard-actions">
        <button type="button" class="discard-stay" (click)="registry.cancelDiscard()">留下</button>
        <button type="button" class="discard-leave" (click)="registry.confirmDiscard()">放棄</button>
      </span>
    </div>
  }
```

`entry-form.scss` — add:

```scss
.discard-strip {
  align-items: center;
  background: var(--app-state-warning-bg);
  border-radius: var(--radius-md);
  color: var(--app-text);
  display: flex;
  flex-wrap: wrap;
  font-size: var(--fs-sm);
  font-weight: 700;
  gap: 8px;
  justify-content: space-between;
  margin: 4px 0 8px;
  padding: 8px 12px;

  .discard-actions {
    display: flex;
    gap: 6px;
  }

  button {
    border: 1px solid var(--app-border);
    border-radius: var(--radius-pill);
    cursor: pointer;
    font: inherit;
    padding: 4px 12px;
  }

  .discard-stay {
    background: var(--app-primary);
    border-color: var(--app-primary);
    color: var(--app-on-primary, #fff);
  }

  .discard-leave {
    background: var(--app-surface);
    color: var(--app-text);
  }
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`
Expected: PASS (the existing `'cancels on Esc unless the Esc was already handled'` still leaves at once: that form is clean).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/entry-form/
git commit -m "feat(entry-form): 放棄未儲存的內容？ strip before ✕ / Esc drops a dirty draft

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Fee and fx sheets own their Esc and Tab (overlay contract)

**Files:**
- Modify: `frontend/src/app/components/accounting/fee-sheet/fee-sheet.ts`, `fee-sheet/fee-sheet.html:1`
- Modify: `frontend/src/app/components/accounting/fx-sheet/fx-sheet.ts` (decorator :46-52, `ngOnInit` :120), `fx-sheet/fx-sheet.html:1`
- Test: `fee-sheet/fee-sheet.spec.ts`, `fx-sheet/fx-sheet.spec.ts`, `entry-form/entry-form.spec.ts`

**Interfaces:**
- Consumes: `isHandledKey`, `trapFocus`, `focusables` (Task 1); the strip (Task 4).
- Produces: `FeeSheetComponent.onHostKeydown(event: KeyboardEvent): void`, `FxSheetComponent.onHostKeydown(event: KeyboardEvent): void`; both `.overlay` roots carry `data-overlay`; both focus their first focusable on open.

- [ ] **Step 1: Write the failing tests**

`fee-sheet.spec.ts`:

```ts
  it('closes on Esc as a handled key and keeps Tab inside the sheet', () => {
    const { fixture, el, component } = render();
    const closed = vi.fn();
    component.closed.subscribe(closed);
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);

    const confirm = el.querySelector<HTMLButtonElement>('.sheet-confirm')!;
    confirm.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    confirm.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.fee-amount')!.dispatchEvent(escape);
    fixture.detectChanges();
    expect(escape.defaultPrevented).toBe(true);
    expect(closed).toHaveBeenCalledTimes(1);
  });
```

(add `vi` to the vitest import; `TestBed` attaches the fixture to `document`, so `focus()` works.)

`fx-sheet.spec.ts` (add `vi` to the import):

```ts
  it('closes on Esc as a handled key and marks its overlay', () => {
    const { fixture, el } = render(null);
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.sheet')!.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(true);
    expect(closed).toHaveBeenCalledTimes(1);
  });
```

(Use the `render` helper's own return shape; it returns `{ fixture, el, … }` like the fee spec. `render(null)` opens without a conversion, so no rate request is made.)

`entry-form.spec.ts` (inside `describe('unsaved input')`):

```ts
    for (const overlay of [
      { name: 'fee', open: '.fee-open', host: 'app-fee-sheet' },
      { name: 'fx', open: 'button.cur', host: 'app-fx-sheet' },
    ]) {
      it(`Esc closes only the ${overlay.name} sheet; the next Esc asks about the dirty draft`, async () => {
        const { el, left } = await open('/accounting/entry');
        respond('/api/accounting/categories', [FOOD]);
        respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
        typeInto(el, '.name-input', '午餐');
        (el.querySelector(overlay.open) as HTMLButtonElement).click();
        settle();
        expect(el.querySelector(overlay.host)).not.toBeNull();

        el.querySelector(`${overlay.host} .sheet`)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        settle();
        expect(el.querySelector(overlay.host)).toBeNull();
        expect(el.querySelector('.discard-strip')).toBeNull();
        expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('午餐');
        expect(left()).toBe(false);

        el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        settle();
        expect(el.querySelector('.discard-strip')).not.toBeNull();
        expect(left()).toBe(false);
        (el.querySelector('.discard-leave') as HTMLButtonElement).click();
        settle();
        expect(left()).toBe(true);
      });
    }

    it('with a clean draft, Esc closes the fee sheet and the next Esc cancels the form', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      (el.querySelector('.fee-open') as HTMLButtonElement).click();
      settle();
      el.querySelector('app-fee-sheet .sheet')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      settle();
      expect(left()).toBe(false);
      el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      settle();
      expect(left()).toBe(true);
    });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/fee-sheet/fee-sheet.spec.ts --include src/app/components/accounting/fx-sheet/fx-sheet.spec.ts --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: FAIL — `expected false to be true` for `hasAttribute('data-overlay')` and for `escape.defaultPrevented` in the unit specs.

- [ ] **Step 3: Implement**

`fee-sheet.html:1` — `<div class="overlay" (click)="cancel()">` → `<div class="overlay" data-overlay (click)="cancel()">`. Same change in `fx-sheet.html:1`.

`fee-sheet.ts`:

```ts
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  inject,
  input,
  model,
  output,
  signal,
} from '@angular/core';
// …
import { focusables, isHandledKey, trapFocus } from '../accounting-ui';
```

Decorator: add `host: { '(keydown)': 'onHostKeydown($event)' },`. Class fields: `private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);` and `private readonly injector = inject(Injector);`. At the end of `ngOnInit()`:

```ts
    afterNextRender(() => this.dialog() && focusables(this.dialog()!)[0]?.focus(), { injector: this.injector });
```

Add:

```ts
  private dialog(): HTMLElement | null {
    return this.host.nativeElement.querySelector<HTMLElement>('.sheet');
  }

  /** Overlay contract (spec §3.1): Esc closes only this sheet, marked handled; Tab stays inside it. */
  onHostKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.cancel();
      return;
    }
    const dialog = this.dialog();
    if (event.key === 'Tab' && dialog) {
      trapFocus(dialog, event);
    }
  }
```

`fx-sheet.ts` — the same four additions: `ElementRef`, `Injector`, `afterNextRender` imports (`inject` is already imported), `import { focusables, isHandledKey, trapFocus } from '../accounting-ui';`, decorator `host: { '(keydown)': 'onHostKeydown($event)' },`, fields `host` / `injector`, the `afterNextRender` focus line at the end of `ngOnInit()`, and the same `dialog()` / `onHostKeydown()` methods (its `cancel()` already emits `closed`).

The entry form's own Esc branch (`if (this.sheet()) { this.sheet.set(null); }`) stays: it still closes a sheet when focus was left on the form (e.g. on the `+` button).

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/fee-sheet/ frontend/src/app/components/accounting/fx-sheet/ frontend/src/app/components/accounting/entry-form/entry-form.spec.ts
git commit -m "feat(accounting): fee and fx sheets close on their own Esc and keep Tab inside

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Layout — swipe only from the 56 px grip

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html` (:15-26)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts` (:56 constants; :113 `swipeStartX`; :261-270 handlers)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.scss` (`.detail-pane` padding-top :59; `.sheet-close` :84-96)
- Test: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts` (rewrite `'closes the sheet with a swipe to the right, not with a short drag'` :342-357)

**Interfaces:**
- Produces: `.sheet-grip` element (always inside the detail pane; `.sheet-grip--active` in sheet mode with the handle and ✕); `onGripPointerDown/Move/Up(event: PointerEvent)`, `onGripPointerCancel()`; constants `SWIPE_CLOSE_PX = 80`, `SWIPE_MAX_DY = 40`, `SWIPE_IGNORE`.

- [ ] **Step 1: Write the failing test** — replace the old swipe test with:

```ts
  it('closes the sheet only on a horizontal swipe that starts on the grip', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    const sheet = await find(harness, '.detail-pane.open');
    const grip = await find(harness, '.sheet-grip');
    expect(grip.querySelector('.sheet-close')).not.toBeNull();
    const swipe = (on: Element, dx: number, dy: number) => {
      on.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
      on.dispatchEvent(new MouseEvent('pointermove', { clientX: 100 + dx, clientY: 20 + dy, bubbles: true }));
      on.dispatchEvent(new MouseEvent('pointerup', { clientX: 100 + dx, clientY: 20 + dy, bubbles: true }));
    };

    // Anywhere else in the pane (a text field of the page) never closes.
    const field = document.createElement('input');
    sheet.appendChild(field);
    swipe(field, 100, 10);
    swipe(sheet, 100, 10);
    swipe(grip, 50, 0);
    swipe(grip, 100, 60);
    harness.detectChanges();
    expect(router.url).toBe('/accounting/entries/5');

    // A cancelled pointer never closes either.
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointercancel', { bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 200, clientY: 20, bubbles: true }));
    expect(router.url).toBe('/accounting/entries/5');

    swipe(grip, 100, 10);
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
    field.remove();
  });

  it('keeps a grip without handle or ✕ in the two-pane layout and never swipes there', async () => {
    const harness = await start('panes', '/accounting/entries/5');
    await find(harness, PANE_PAGE);
    const grip = await find(harness, '.sheet-grip');
    expect(grip.classList).not.toContain('sheet-grip--active');
    expect(grip.querySelector('.sheet-close')).toBeNull();
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 300, clientY: 20, bubbles: true }));
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/5');
  });
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`
Expected: FAIL — `no .sheet-grip yet` from `find()` timing out.

- [ ] **Step 3: Implement**

`accounting-layout.html` — the detail section becomes:

```html
    <section
      #detailPane
      class="pane detail-pane"
      aria-label="明細"
      [attr.role]="sheetOpen() ? 'dialog' : null"
      [attr.aria-modal]="sheetOpen() ? 'true' : null"
      [class.open]="paneOpen()"
    >
      <div
        class="sheet-grip"
        [class.sheet-grip--active]="sheetOpen()"
        (pointerdown)="onGripPointerDown($event)"
        (pointermove)="onGripPointerMove($event)"
        (pointerup)="onGripPointerUp($event)"
        (pointercancel)="onGripPointerCancel()"
      >
        @if (sheetOpen()) {
          <span class="sheet-handle" aria-hidden="true"></span>
          <button #closeButton type="button" class="sheet-close" aria-label="關閉" (click)="close()">✕</button>
        }
      </div>
      <router-outlet name="pane" />
      @if (!paneOpen()) {
        <p class="pane-empty">選擇一筆記錄查看明細，或按 ＋ 新增</p>
      }
    </section>
```

(also give the list section a reference for Task 7: `<section #listPane class="pane list-pane" aria-label="列表">`.)

Check the existing `PANE_PAGE` selector (`.detail-pane router-outlet + :not(.pane-empty)`) still matches: the grip sits *before* the outlet, so it is unaffected.

`accounting-layout.ts` — replace `const SWIPE_CLOSE_PX = 80;` with:

```ts
const SWIPE_CLOSE_PX = 80;
/** A swipe that drifts more than this vertically is a scroll, not a close. */
const SWIPE_MAX_DY = 40;
/** Pointers starting on a control never begin a swipe (✕ itself may). */
const SWIPE_IGNORE = 'input, textarea, select, button:not(.sheet-close), [contenteditable], .keypad';
```

Replace `private swipeStartX: number | null = null;` with:

```ts
  private swipe: { x: number; y: number } | null = null;
```

Replace `onPanePointerDown` / `onPanePointerUp` with:

```ts
  /** Swipe-to-close starts only on the grip, only in the 760–1023 px sheet (spec §4.1). */
  onGripPointerDown(event: PointerEvent): void {
    const target = event.target as Element | null;
    if (!this.sheetOpen() || target?.closest(SWIPE_IGNORE)) {
      this.swipe = null;
      return;
    }
    this.swipe = { x: event.clientX, y: event.clientY };
    const grip = event.currentTarget as HTMLElement | null;
    if (typeof event.pointerId === 'number') {
      grip?.setPointerCapture?.(event.pointerId);
    }
  }

  onGripPointerMove(event: PointerEvent): void {
    if (this.swipe && Math.abs(event.clientY - this.swipe.y) >= SWIPE_MAX_DY) {
      // Clearly vertical: a scroll, not a close.
      this.swipe = null;
    }
  }

  onGripPointerUp(event: PointerEvent): void {
    const swipe = this.swipe;
    this.swipe = null;
    if (!swipe || !this.sheetOpen()) {
      return;
    }
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    if (dx > SWIPE_CLOSE_PX && Math.abs(dy) < SWIPE_MAX_DY) {
      this.close();
    }
  }

  onGripPointerCancel(): void {
    this.swipe = null;
  }
```

`accounting-layout.scss`:
- In `.dbody--sheet .detail-pane` change `padding-top: 44px;` → `padding-top: 56px;`.
- Replace the `.sheet-close { … }` block with:

```scss
.sheet-grip {
  height: 0;
  left: 0;
  position: absolute;
  right: 0;
  top: 0;

  &--active {
    height: 56px;
    touch-action: pan-y;
    z-index: 1;
  }
}

.sheet-handle {
  background: var(--app-border);
  border-radius: 2px;
  height: 4px;
  left: 50%;
  position: absolute;
  top: 8px;
  transform: translateX(-50%);
  width: 36px;
}

.sheet-close {
  background: var(--app-surface-soft);
  border: 0;
  border-radius: 50%;
  color: var(--app-text);
  cursor: pointer;
  font-size: 1rem;
  height: 32px;
  position: absolute;
  right: 12px;
  top: 12px;
  width: 32px;
}
```

(`.detail-pane` must be the grip's containing block: it is `position: fixed` in sheet mode; in panes mode add `position: relative;` to the base `.detail-pane` rule if it has no position.)

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-layout/
git commit -m "feat(layout): swipe-to-close only from the sheet grip, never from a field

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Layout — `inert` outside the sheet and Tab wrap inside it

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts` (imports :1-25; viewChildren :86; constructor effects :160-163)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.html` (the `<section #detailPane …>` from Task 6)
- Test: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Consumes: `trapFocus`, `OVERLAY_ATTR` (Task 1).
- Produces: `onPaneKeydown(event: KeyboardEvent): void`; the list pane and `app-dock` get `inert` while `sheetOpen()`.

- [ ] **Step 1: Write the failing tests**

```ts
  it('makes the list and the dock inert while the sheet is open, and only then', async () => {
    const dock = document.createElement('app-dock');
    document.body.appendChild(dock);
    const harness = await start('sheet', '/accounting');
    const list = harness.routeNativeElement!.querySelector('.list-pane')!;
    await find(harness, LIST);
    expect(list.hasAttribute('inert')).toBe(false);

    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    await find(harness, '.detail-pane.open');
    await vi.waitFor(() => {
      harness.detectChanges();
      expect(list.hasAttribute('inert')).toBe(true);
      expect(dock.hasAttribute('inert')).toBe(true);
    });
    expect(harness.routeNativeElement!.querySelector('.sheet-backdrop')!.getAttribute('aria-hidden')).toBe('true');

    await router.navigateByUrl('/accounting');
    await vi.waitFor(() => {
      harness.detectChanges();
      expect(list.hasAttribute('inert')).toBe(false);
      expect(dock.hasAttribute('inert')).toBe(false);
    });
    dock.remove();
  });

  it('wraps Tab inside the sheet: from the last focusable to ✕ and back with Shift+Tab', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    await TestBed.inject(Router).navigateByUrl('/accounting/entries/5');
    const pane = await find(harness, '.detail-pane.open');
    const closeButton = await find(harness, '.sheet-close') as HTMLButtonElement;
    const last = document.createElement('button');
    last.textContent = '最後';
    pane.appendChild(last);

    last.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    last.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(closeButton);

    const back = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    closeButton.dispatchEvent(back);
    expect(document.activeElement).toBe(last);

    // An overlay inside the pane traps its own Tab: the pane leaves it alone.
    const overlay = document.createElement('div');
    overlay.setAttribute('data-overlay', '');
    const inner = document.createElement('button');
    overlay.appendChild(inner);
    pane.appendChild(overlay);
    inner.focus();
    const overlayTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    inner.dispatchEvent(overlayTab);
    expect(overlayTab.defaultPrevented).toBe(false);
    [last, overlay].forEach(node => node.remove());
  });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`
Expected: FAIL — `expected false to be true` for `inert`, and `tab.defaultPrevented` false.

- [ ] **Step 3: Implement**

`accounting-layout.ts`:
- Imports: add `DestroyRef` to `@angular/core`; `import { isHandledKey, trapFocus } from '../accounting-ui';` (extend the existing import).
- Fields next to `closeButton`:

```ts
  private readonly listPane = viewChild<ElementRef<HTMLElement>>('listPane');
  private readonly detailPane = viewChild<ElementRef<HTMLElement>>('detailPane');
```

- Constructor, after the focus effect:

```ts
    // Spec §4.2: nothing behind the sheet is reachable (list pane, dock) while it is open.
    effect(() => {
      const open = this.sheetOpen();
      const list = this.listPane()?.nativeElement;
      untracked(() => this.setOutsideInert(open, list));
    });
    inject(DestroyRef).onDestroy(() => this.setOutsideInert(false, this.listPane()?.nativeElement));
```

- Methods:

```ts
  private setOutsideInert(inert: boolean, list: HTMLElement | undefined): void {
    for (const element of [list, this.document.querySelector<HTMLElement>('app-dock')]) {
      element?.toggleAttribute('inert', inert);
    }
  }

  /** Tab / Shift+Tab wraps inside the open sheet; an overlay inside it (data-overlay) traps its own Tab. */
  onPaneKeydown(event: KeyboardEvent): void {
    if (!this.sheetOpen() || event.key !== 'Tab' || isHandledKey(event)) {
      return;
    }
    if ((event.target as Element | null)?.closest?.('[data-overlay]')) {
      return;
    }
    const pane = this.detailPane()?.nativeElement;
    if (pane) {
      trapFocus(pane, event);
    }
  }
```

`accounting-layout.html` — on the `<section #detailPane …>` add `(keydown)="onPaneKeydown($event)"`.

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-layout/
git commit -m "feat(layout): inert list and dock behind the sheet, Tab wraps inside it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Layout — every sheet exit goes through the registry

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts` (`close()` :167-174)
- Test: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

**Interfaces:**
- Consumes: `DirtyFormRegistry` (Task 2).
- Produces: `close()` → `registry.requestClose(() => this.navigateClose())`; private `navigateClose()` (the old body).

- [ ] **Step 1: Write the failing test** (add `import { DirtyFormRegistry } from '../dirty-form.service';`)

```ts
  it('asks a dirty registered form before ✕, backdrop, Esc or swipe closes the sheet', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    await find(harness, '.detail-pane.open');
    const registry = TestBed.inject(DirtyFormRegistry);
    const form = { isDirty: () => true, showDiscardPrompt: vi.fn() };
    registry.register(form);

    (await find(harness, '.sheet-close') as HTMLButtonElement).click();
    (await find(harness, '.sheet-backdrop') as HTMLElement).click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    const grip = await find(harness, '.sheet-grip');
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 200, clientY: 20, bubbles: true }));
    expect(form.showDiscardPrompt).toHaveBeenCalledTimes(4);
    expect(router.url).toBe('/accounting/entries/5');

    registry.cancelDiscard();
    harness.detectChanges();
    expect(router.url).toBe('/accounting/entries/5');
    expect(harness.routeNativeElement!.querySelector('.detail-pane.open')).not.toBeNull();

    (await find(harness, '.sheet-close') as HTMLButtonElement).click();
    registry.confirmDiscard();
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
    registry.unregister(form);
  });
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`
Expected: FAIL — `expected "spy" to be called 4 times, but got 0 times` (the sheet closes at once).

- [ ] **Step 3: Implement** — `accounting-layout.ts`: inject `private readonly registry = inject(DirtyFormRegistry);` (import from `'../dirty-form.service'`), and replace `close()`:

```ts
  /** Close the pane / sheet (✕, backdrop, swipe, Esc): a dirty entry form first asks 放棄未儲存的內容？. */
  close(): void {
    this.registry.requestClose(() => this.navigateClose());
  }

  /** Back to the list's own URL. */
  private navigateClose(): void {
    const passbook = PASSBOOK_URL.exec(this.url().split(/[?#]/)[0]);
    if (passbook && this.selectedEntryId() !== null) {
      void this.router.navigateByUrl(`/accounting/accounts/${passbook[1]}`);
      return;
    }
    void this.router.navigateByUrl(this.listKey() === 'accounts' ? '/accounting/accounts' : '/accounting');
  }
```

- [ ] **Step 4: Run the whole suite** — `cd frontend && npm test -- --watch=false`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-layout/
git commit -m "feat(layout): sheet ✕, backdrop, swipe and Esc respect unsaved input

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Verification and PR

- [ ] **Step 1: Full test run** — `cd frontend && npm test -- --watch=false` → PASS; keep the summary lines.
- [ ] **Step 2: Build** — `cd frontend && npm run build` (or `npx ng build` without a repo-root `.env`) → exit 0.
- [ ] **Step 3: Scope check** — `git diff main --stat -- backend openspec .env` → empty; `grep -rn "window.confirm" frontend/src/app/components/accounting` → nothing.
- [ ] **Step 4: Demo screenshots and manual pass at 390×844, 760×820, 1280×800.**
  - *Before*: `http://127.0.0.1:18080/hub/accounting` (the demo proxy serves `/home/opc/workspace/home-hub-schedules/frontend/dist/inventory-ui/browser` against the fictional-data demo API).
  - *After*: if your checkout is `/home/opc/workspace/home-hub-schedules`, the Step 2 build lands in that `dist/` — reload the same URL. Otherwise write a scratch proxy file outside the repo, `/tmp/hh-demo-proxy.json` = `{"/api/accounting": {"target": "http://127.0.0.1:8011", "pathRewrite": {"^/api/accounting": ""}, "changeOrigin": true}}`, run `cd frontend && npx ng serve --proxy-config /tmp/hh-demo-proxy.json --port 4310`, and open `http://127.0.0.1:4310/hub/accounting`.
  - Capture e.g. `npx playwright screenshot --viewport-size=760,820 http://127.0.0.1:4310/hub/accounting/entry after-760.png` (repeat for `390,844` and `1280,800`), showing: the grip with handle and ✕ at 760×820; the 放棄 strip after typing a name and pressing Esc (390 and 760); the two-pane layout at 1280 unchanged.
  - Manually at 760×820: Tab cycles inside the sheet only; dragging from the name field never closes; dragging the grip right closes; Esc in the fee sheet closes only the fee sheet.
  - If you cannot reach the demo API or run a browser, say so in the PR and attach the Vitest output instead.
- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/ux-refine-2-sheet
gh pr create --base main --head feat/ux-refine-2-sheet \
  --title "feat(accounting): UX refine 2 — sheet grip, focus containment, unsaved-input guard" \
  --body-file /tmp/pr-ux-refine-2.md
```

PR body template (`/tmp/pr-ux-refine-2.md`):

```markdown
## Summary
Spec: docs/superpowers/specs/2026-10-05-ux-refinement-design.md §4 and the fee/fx half of the §3.1 overlay keyboard contract (PR-2 of 3).
- `trapFocus(container, event)` + `data-overlay` marker in accounting-ui.ts (PR-3's picker reuses both).
- `DirtyFormRegistry` (`register` / `unregister` / `requestClose` / `confirmDiscard` / `cancelDiscard` / `clearPending`); entry form `isDirty` snapshot and its own 放棄未儲存的內容？ strip (留下 / 放棄) in both layouts.
- Sheet: swipe only from the 56 px grip (dx > 80, |dy| < 40), inert list pane and dock, Tab wraps inside the pane; ✕ / backdrop / swipe / Esc go through the registry.
- Fee and fx sheets: Esc closes only the sheet (handled key), Tab stays inside.

## Review focus
1. No prompt after save-and-continue (entry-form.spec).
2. No prompt when the pane moves to another record (entry-form.spec).
3. Esc in fee / fx sheet closes only that sheet; next Esc prompts (entry-form.spec).
4. Swipe from a field / vertical drift never closes (accounting-layout.spec).
5. Tab wrap and inert on/off (accounting-layout.spec).

## Test plan
- [ ] `cd frontend && npm test -- --watch=false` — <paste summary>
- [ ] `cd frontend && npm run build`
- [ ] Screenshots before/after at 390×844, 760×820, 1280×800 (attached)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Self-review

- **Spec coverage:** §4.1 grip / ignore list / dx-dy rule / pointer capture / pointercancel / panes grip without visual → Task 6; §4.2 inert list + dock, backdrop `aria-hidden`, Tab wrap, ✕ initial focus and restore unchanged → Task 7; §4.3 `isDirty` snapshot → Task 3, registry API → Task 2, strip in both layouts / 留下 default focus / pending cleared on save, save-and-continue, unregister / form `cancel()` caller → Task 4, layout `close()` caller → Task 8; §4.4 every listed test → Tasks 3, 4, 6, 7, 8; §3.1 overlay contract for fee / fx sheets (with §4 in PR-2) → Task 5. Gaps fixed while drafting: layout destroyed with the sheet open un-inerts the dock; the fee/fx sheets also trap Tab (needed because the pane trap skips `data-overlay`); a vertical drift cancels the swipe during `pointermove`.
- **Resolved ambiguities:** `unregister(form?)` takes an optional form so a late destroy of an old instance cannot clear a newer registration (calling it without an argument behaves as the spec's `unregister()`); the strip's visibility lives in the registry (`promptOpen`) while `showDiscardPrompt()` focuses 留下, which keeps `DirtyAware` exactly as specified; the transfer panel's default category is not part of the dirty key.
