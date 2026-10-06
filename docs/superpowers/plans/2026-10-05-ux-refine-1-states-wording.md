# UX Refine 1 — States, Event Type and Wording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the accounting lists' filters, totals and load states unambiguous, promote 事件類型 (單次／週期／分期) to a first-class tile in the entry form, and make wording and secondary text consistent and readable (spec §1, §2, §5).

**Architecture:** Each list page (timeline, passbook, accounts overview) gets explicit, mutually exclusive loading / error / empty / rows states driven by its own request counter, with one shared `<app-skeleton>` placeholder component. The entry form renders its own 事件類型 tablist and shows `app-schedule-tabs` (tablist hidden via a new `showTabs` input) directly below it, replacing the `<details class="advanced">` fold. Wording and readability are mechanical template/SCSS edits plus one pure helper `textOn()` in `accounting-ui.ts`.

**Tech Stack:** Angular 21 standalone components with signals and `@if`/`@for` control flow, RxJS, SCSS, Vitest via `@angular/build:unit-test` (jsdom), `HttpTestingController`.

**Spec:** `docs/superpowers/specs/2026-10-05-ux-refinement-design.md` (binding). Fact sheet with base-commit line anchors: `.superpowers/ux/facts-ux-refine.md` (base `main` @ b8d4d77).

**Branch:** `feat/ux-refine-1-states-wording`, from `main`.

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
- Build command: `cd frontend && npm run build` (runs `node set-env.js` first; it needs the repo-root `.env`. If your checkout has no `.env`, run `cd frontend && npx ng build` instead, which builds with the committed `src/environments/environment.ts`; never create or edit `.env`).
- Line anchors below are for base `b8d4d77`; earlier tasks shift lines, so always match on the quoted code, not the number.

## Review Focus

Things unit tests may miss; each line names the input, the expected behaviour and the task holding its pinning test.

1. **Stale list response** — input: change the 類型 filter to 收入, then to 支出 before the first response lands; the 收入 page then arrives with zero rows. Expected: it is ignored (request counter); the skeleton stays and no `沒有符合條件的記錄` appears until the 支出 page itself resolves empty; the summary request is unaffected. Pinned in **Task 4** (`ignores a late page for the previous filters…`).
2. **分期 → 單次** — input: choose 分期 (fee button and invoice hidden), get a schedule field error, then choose 單次. Expected: `.fee-open` and `.invoice-tile` are back, `.field-error` and `.form-error` are gone, `fieldErrors()` is `{}`. Pinned in **Task 8** (`restores the hidden tiles and clears schedule errors when 分期 goes back to 單次`).
3. **Summary row at 390px** — input: month totals with large figures at 390×844 after the `.72rem` font bump. Expected: the three figures are never clipped with an ellipsis; if they do not fit they wrap to a second line. Pinned in **Task 12** (computed-style test on `.sum b`) plus the 390×844 screenshot in **Task 13**.
4. **Search debounce** — input: type `午`, then `午餐 ` within 300 ms; later type `拉麵` and press Enter. Expected: exactly one request with `q=午餐` 300 ms after the last keystroke; Enter sends `q=拉麵` at once and the pending timer never sends a second request. Pinned in **Task 3**.
5. **Import retry vs. accounts reload** — input: the import lookup failed; an accounts reload is in flight; the owner taps the import `重試`; the new import succeeds, the reload's accounts arrive, then the reload's (older) import fails. Expected: balances update, no `匯入狀態無法取得`. Pinned in **Task 7** (`keeps an in-flight accounts reload when the import is retried…`).

---

### Task 1: Shared `<app-skeleton>` component

**Files:**
- Create: `frontend/src/app/components/accounting/skeleton/skeleton.ts`
- Create: `frontend/src/app/components/accounting/skeleton/skeleton.scss`
- Test: `frontend/src/app/components/accounting/skeleton/skeleton.spec.ts` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `SkeletonComponent` — selector `app-skeleton`, input `rows: number` (default `3`, accepts the static attribute `rows="3"` via `numberAttribute`); host has `class="skeleton"`, `role="status"`, `aria-busy="true"`, `aria-label="載入中"`; renders `rows` × `.skel-row`. Used by Tasks 4, 6, 7.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/components/accounting/skeleton/skeleton.spec.ts
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { SkeletonComponent } from './skeleton';

@Component({ standalone: true, imports: [SkeletonComponent], template: '<app-skeleton rows="3" />' })
class HostComponent {}

describe('SkeletonComponent', () => {
  it('renders three busy placeholder rows with an accessible name', () => {
    TestBed.configureTestingModule({ imports: [HostComponent] });
    const fixture = TestBed.createComponent(HostComponent);
    fixture.detectChanges();
    const skeleton = (fixture.nativeElement as HTMLElement).querySelector('app-skeleton')!;

    expect(skeleton.classList).toContain('skeleton');
    expect(skeleton.getAttribute('role')).toBe('status');
    expect(skeleton.getAttribute('aria-busy')).toBe('true');
    expect(skeleton.getAttribute('aria-label')).toBe('載入中');
    expect(skeleton.querySelectorAll('.skel-row').length).toBe(3);
  });

  it('renders the number of rows it is given', () => {
    TestBed.configureTestingModule({ imports: [SkeletonComponent] });
    const fixture = TestBed.createComponent(SkeletonComponent);
    fixture.componentRef.setInput('rows', 5);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.skel-row').length).toBe(5);
  });
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/skeleton/skeleton.spec.ts`
Expected: FAIL — `Could not resolve "./skeleton"` (the component file does not exist).

- [ ] **Step 3: Implement**

```ts
// frontend/src/app/components/accounting/skeleton/skeleton.ts
import { ChangeDetectionStrategy, Component, computed, input, numberAttribute } from '@angular/core';

/** Placeholder rows shown while a list's first page (or a filter change) is loading (spec §1.4). */
@Component({
  selector: 'app-skeleton',
  standalone: true,
  template: `
    @for (row of rowList(); track row) {
      <div class="skel-row" aria-hidden="true">
        <span class="skel-ico"></span>
        <span class="skel-lines"><span class="skel-line"></span><span class="skel-line short"></span></span>
      </div>
    }
  `,
  styleUrl: './skeleton.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'skeleton', role: 'status', 'aria-busy': 'true', 'aria-label': '載入中' },
})
export class SkeletonComponent {
  readonly rows = input(3, { transform: numberAttribute });
  readonly rowList = computed(() => Array.from({ length: Math.max(0, this.rows()) }, (_, index) => index));
}
```

```scss
// frontend/src/app/components/accounting/skeleton/skeleton.scss
:host {
  display: block;
  margin: 8px 0;
}

.skel-row {
  align-items: center;
  display: flex;
  gap: 10px;
  padding: 8px 0;
}

.skel-ico,
.skel-line {
  animation: skel-shimmer 1.2s ease-in-out infinite;
  background: linear-gradient(90deg, var(--app-surface-soft) 25%, var(--app-border) 50%, var(--app-surface-soft) 75%);
  background-size: 200% 100%;
}

.skel-ico {
  border-radius: 50%;
  flex: none;
  height: 34px;
  width: 34px;
}

.skel-lines {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}

.skel-line {
  border-radius: 4px;
  height: 10px;
  width: 70%;

  &.short {
    width: 40%;
  }
}

@keyframes skel-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .skel-ico,
  .skel-line {
    animation: none;
  }
}
```

- [ ] **Step 4: Run the test and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/skeleton/skeleton.spec.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/skeleton/
git commit -m "feat(accounting): shared app-skeleton loading placeholder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Timeline — filter scope label, match count and 清除篩選

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (signals at :318-333, `load()` :560-601, `clearList()` :625-631, `setAccount/setKind/setQuery` :758-768)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html` (`.sum` block :22-28, filter selects :38-49)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.scss` (after `.sum` block :48-78)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces on `LedgerTimelineComponent`: `readonly total: WritableSignal<number | null>` (was `signal(0)`); `readonly filtersActive: Signal<boolean>`; `clearFilters(): void`; `readonly hasMore` now `entries().length < (total() ?? 0)`. Template classes `.sum-scope`, `.match-row`, `.match-count`, `.clear-filters`, `.match-row--calendar`.

- [ ] **Step 1: Write the failing tests** (append inside `describe('LedgerTimelineComponent', …)`, before `describe('calendar view'`)

```ts
  describe('filters and month totals', () => {
    it('labels the totals 全月 · 未套篩選 and shows the filtered match count while a filter is active', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10', { expense: '-5000', net: '-5000' });
      flushEntries([makeEntry({ id: 1 })]);
      fixture.detectChanges();
      expect(el.querySelector('.sum-scope')).toBeNull();
      expect(el.querySelector('.match-row')).toBeNull();

      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();
      expect(text(el.querySelector('.sum-scope'))).toBe('全月 · 未套篩選');
      // In flight: never the previous filters' count.
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');

      flushEntries([makeEntry({ id: 1 }), makeEntry({ id: 2 })], 12);
      fixture.detectChanges();
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 12 筆');
      expect(text(el.querySelector('.sum-expense'))).toBe('−$5,000');
      expect(el.querySelector('.clear-filters')!.getAttribute('aria-label')).toBe('清除篩選');
    });

    it('shows — as the match count when the filtered page fails', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([makeEntry({ id: 1 })], 1);
      fixture.detectChanges();

      fixture.componentInstance.setQuery('午餐');
      fixture.detectChanges();
      httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');
    });

    it('clears the account, kind and query filters from 清除篩選', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();

      const timeline = fixture.componentInstance;
      timeline.setAccount('7');
      timeline.setKind('expense');
      timeline.setQuery('午餐');
      fixture.detectChanges();
      flushEntries([], 0);
      fixture.detectChanges();

      (el.querySelector('.clear-filters') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = flushEntries([]);
      expect(req.request.params.has('account_id')).toBe(false);
      expect(req.request.params.has('kind')).toBe(false);
      expect(req.request.params.has('q')).toBe(false);
      fixture.detectChanges();
      expect(timeline.filtersActive()).toBe(false);
      expect(el.querySelector('.match-row')).toBeNull();
      expect(el.querySelector('.sum-scope')).toBeNull();
    });

    it('shows the scope label and 清除篩選 without a count in the calendar view', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render('phone');
      flushSummary('2026-10');
      flushDaily('2026-10');
      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();

      expect(text(el.querySelector('.sum-scope'))).toBe('全月 · 未套篩選');
      expect(el.querySelector('.match-count')).toBeNull();
      expect(el.querySelector('.match-row--calendar .clear-filters')).not.toBeNull();
    });
  });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: FAIL — `expected '' to be '全月 · 未套篩選'` / `Cannot read properties of null (reading 'getAttribute')`, and `timeline.filtersActive is not a function`.

- [ ] **Step 3: Implement**

`timeline.ts` — signals (replace `readonly total = signal(0);`):

```ts
  /** `total` of the current filters' `/entries` page; null while that page is in flight or after it failed. */
  readonly total = signal<number | null>(null);
```

Below `readonly query = signal('');` add nothing; below `readonly isPhone = …` add:

```ts
  /** Any list filter set: the month totals are then labelled as unfiltered (spec §1.1). */
  readonly filtersActive = computed(
    () => this.accountFilter() !== null || this.kindFilter() !== null || this.query() !== '',
  );
```

Replace `readonly hasMore = computed(() => this.entries().length < this.total());` with:

```ts
  readonly hasMore = computed(() => this.entries().length < (this.total() ?? 0));
```

In `load(reset)`, directly after `this.loadError.set(false);` add:

```ts
    if (reset) {
      // Never show the previous filters' count while this page is in flight.
      this.total.set(null);
    }
```

In its `error` handler replace `this.total.set(0);` with `this.total.set(null);`. In `clearList()` replace `this.total.set(0);` with `this.total.set(null);`.

After `setQuery(…)` add:

```ts
  clearFilters(): void {
    this.accountFilter.set(null);
    this.kindFilter.set(null);
    this.query.set('');
  }
```

`timeline.html` — replace the `.sum` block (`@if (summary(); as s) { <div class="sum"> … </div> }`, :22-28) with:

```html
  @if (summary(); as s) {
    <div class="sum">
      @if (filtersActive()) {
        <small class="sum-scope">全月 · 未套篩選</small>
      }
      <div><small>支出</small><b class="neg sum-expense">{{ expenseText(s) }}</b></div>
      <div><small>收入</small><b class="pos sum-income">{{ incomeText(s) }}</b></div>
      <div><small>結餘</small><b class="sum-net">{{ formatSigned(s.net, s.currency) }}</b></div>
    </div>
  }
  @if (filtersActive()) {
    @if (view() === 'list') {
      <div class="match-row">
        <span class="match-count">符合條件 {{ total() ?? '—' }} 筆</span>
        <button type="button" class="clear-filters" aria-label="清除篩選" (click)="clearFilters()">清除篩選</button>
      </div>
    } @else {
      <div class="match-row match-row--calendar">
        <button type="button" class="clear-filters" aria-label="清除篩選" (click)="clearFilters()">清除篩選</button>
      </div>
    }
  }
```

(Task 5 replaces the `.sum` block again to add its own states; keep the `.sum-scope` line there.)

In the two filter selects, make the 全部 options follow a cleared filter:

```html
        <option value="" [selected]="accountFilter() === null">全部帳戶</option>
```
```html
        <option value="" [selected]="kindFilter() === null">全部類型</option>
```

`timeline.scss` — after the `.sum { … }` block add:

```scss
.sum-scope {
  color: var(--app-text-muted);
  font-size: 0.72rem;
  font-weight: 700;
  grid-column: 1 / -1;
  letter-spacing: 0.04em;
}

.match-row {
  align-items: center;
  display: flex;
  font-size: 0.8rem;
  gap: 8px;
  justify-content: space-between;
  margin: -4px 0 10px;

  &--calendar {
    justify-content: flex-end;
  }
}

.match-count {
  color: var(--app-text-muted);
  font-weight: 700;
}

.clear-filters,
.retry {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 3px 10px;
}
```

- [ ] **Step 4: Run the timeline spec and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: PASS (all existing tests plus the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/timeline/
git commit -m "feat(timeline): label whole-month totals and show the filtered match count

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Timeline — debounced search, Enter commits, Esc clears

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (imports :1-41, constructor :424, after `setQuery` :766-768)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html` (`.filter-q` input :50-57)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:**
- Consumes: `isHandledKey(event: KeyboardEvent): boolean` from `../accounting-ui`.
- Produces: `export const QUERY_DEBOUNCE_MS = 300;` `onQueryInput(value: string): void`, `onQueryKeydown(event: KeyboardEvent): void`; `clearFilters()` now also cancels a pending search and empties the field.

- [ ] **Step 1: Write the failing tests** (inside `describe('filters and month totals', …)` from Task 2)

```ts
    it('searches 300 ms after the last keystroke and at once on Enter', () => {
      vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
      vi.setSystemTime(new Date(2026, 9, 2, 12, 0, 0));
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      const input = el.querySelector('.filter-q') as HTMLInputElement;

      input.value = '午';
      input.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(200);
      input.value = '午餐 ';
      input.dispatchEvent(new Event('input'));
      vi.advanceTimersByTime(299);
      fixture.detectChanges();
      expectNoEntryPage();
      vi.advanceTimersByTime(1);
      fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('午餐');

      input.value = '拉麵';
      input.dispatchEvent(new Event('input'));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(flushEntries([]).request.params.get('q')).toBe('拉麵');
      vi.advanceTimersByTime(300);
      fixture.detectChanges();
      expectNoEntryPage();
    });

    it('clears a typed search on Esc without letting the key reach the page, and lets Esc through when empty', () => {
      const { fixture, el } = render('sheet');
      flushSummary('2026-10');
      flushEntries([]);
      fixture.detectChanges();
      fixture.componentInstance.setQuery('午餐');
      fixture.detectChanges();
      flushEntries([]);
      fixture.detectChanges();
      const outer = vi.fn();
      document.addEventListener('keydown', outer);
      const input = el.querySelector('.filter-q') as HTMLInputElement;
      input.value = '午餐';

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(outer).not.toHaveBeenCalled();
      expect(input.value).toBe('');
      expect(flushEntries([]).request.params.has('q')).toBe(false);

      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      expect(outer).toHaveBeenCalledTimes(1);
      document.removeEventListener('keydown', outer);
    });
```

Note: `render()` attaches the fixture to `document` (TestBed default), so the bubbling `keydown` reaches `document` unless stopped.

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: FAIL — `Expected no matching requests … found 0` passes but `expect(flushEntries([])…` fails with `Expected one matching request for criteria "Match by function: ", found none.` (the field still listens to `change`), and the Esc test fails with `expected "spy" to not be called`.

- [ ] **Step 3: Implement**

`timeline.ts` — imports: add `isHandledKey` to the `../accounting-ui` import:

```ts
import { KIND_PILLS, KindPill, colorOf, fxLine, iconOf, isHandledKey, pad, shiftMonth as shiftYearMonth } from '../accounting-ui';
```

Below `export const TIMELINE_VIEW_KEY = …` add:

```ts
/** The search field commits this long after the last keystroke (Enter commits at once). */
export const QUERY_DEBOUNCE_MS = 300;
```

Fields, below `private dayRequestId = 0;`:

```ts
  private queryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly queryInput = viewChild<ElementRef<HTMLInputElement>>('queryInput');
```

At the end of the constructor body (after the IntersectionObserver effect) add:

```ts
    inject(DestroyRef).onDestroy(() => this.clearQueryTimer());
```

After `setQuery(…)`:

```ts
  onQueryInput(value: string): void {
    this.clearQueryTimer();
    this.queryTimer = setTimeout(() => {
      this.queryTimer = null;
      this.setQuery(value);
    }, QUERY_DEBOUNCE_MS);
  }

  /** Enter commits now; Esc empties a field that has text and stops there (an empty field lets Esc through). */
  onQueryKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    const input = event.target as HTMLInputElement;
    if (event.key === 'Enter') {
      event.preventDefault();
      this.clearQueryTimer();
      this.setQuery(input.value);
    } else if (event.key === 'Escape' && input.value !== '') {
      event.stopPropagation();
      this.clearQueryTimer();
      input.value = '';
      this.setQuery('');
    }
  }

  private clearQueryTimer(): void {
    if (this.queryTimer !== null) {
      clearTimeout(this.queryTimer);
      this.queryTimer = null;
    }
  }
```

Replace `clearFilters()` from Task 2 with:

```ts
  clearFilters(): void {
    this.clearQueryTimer();
    const input = this.queryInput()?.nativeElement;
    if (input) {
      input.value = '';
    }
    this.accountFilter.set(null);
    this.kindFilter.set(null);
    this.query.set('');
  }
```

`timeline.html` — the search input becomes:

```html
      <input
        #queryInput
        class="chip tag filter-q"
        type="search"
        placeholder="⌕ 搜尋名稱 / 商家 / 備註"
        aria-label="搜尋名稱、商家或備註"
        [value]="query()"
        (input)="onQueryInput($any($event.target).value)"
        (keydown)="onQueryKeydown($event)"
      />
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/timeline/
git commit -m "feat(timeline): debounce the search field, commit on Enter, clear on Esc

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Timeline — list and calendar-day load states (skeleton / error / empty / rows)

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (`imports` :293, signals :322-333, `load()` :560-601, `clearList()`, `loadDay()` :654-685, `clearDay()`)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html` (calendar day list :113-122, list :126-144)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.scss` (`.empty, .load-error` :92-98)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts` (new tests; rewrite "keeps the current rows on screen while a refresh is in flight" :280-294 and "clears the previous month's rows…" :296-310)

**Interfaces:**
- Consumes: `SkeletonComponent` (Task 1), `filtersActive`, `clearFilters()` (Task 2).
- Produces: `loading` starts `true`; `load(reset)` clears the rows when the filter key (month/account/kind/query/hide-rewards) changed and keeps them for a same-key refresh (entry write, preference save); `retryDay(): void`; template classes `.load-error-block`, `.list-retry`, `.day-retry`, `.empty-filtered`.

- [ ] **Step 1: Write the failing tests**

Replace the test `'keeps the current rows on screen while a refresh is in flight'` with:

```ts
  it('replaces the rows with the skeleton while another month loads', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    (el.querySelector('.month-next') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelectorAll('.row').length).toBe(0);
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(el.querySelector('.empty')).toBeNull();

    flushSummary('2026-11');
    flushEntries([makeEntry({ id: 2, entry_date: '2026-11-01', name: '捷運' })]);
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['捷運']);
    expect(el.querySelector('app-skeleton')).toBeNull();
  });

  it('keeps the current rows on screen while the same month refreshes after an entry write', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    TestBed.inject(AccountingService).deleteEntry(99).subscribe();
    httpMock!.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
    fixture.detectChanges();
    flushCounterparties();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['午餐']);
    expect(el.querySelector('app-skeleton')).toBeNull();

    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 3, name: '晚餐' })]);
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['晚餐']);
  });
```

Replace the test `"clears the previous month's rows when the reset load fails, keeping the error"` with:

```ts
  it('shows 記錄讀取失敗 with 重試 instead of rows or empty text, and 重試 reloads page 1', () => {
    const { fixture, el } = render();
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1, name: '午餐' })]);
    fixture.detectChanges();

    (el.querySelector('.month-next') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-11');
    httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelectorAll('.row').length).toBe(0);
    expect(text(el.querySelector('.load-error'))).toBe('記錄讀取失敗');
    expect(el.querySelector('.empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).toBeNull();

    (el.querySelector('.list-retry') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    const req = flushEntries([makeEntry({ id: 2, entry_date: '2026-11-01', name: '捷運' })]);
    fixture.detectChanges();
    expect(req.request.params.get('offset')).toBe('0');
    expect(el.querySelector('.load-error')).toBeNull();
    expect(Array.from(el.querySelectorAll('.row .name')).map(text)).toEqual(['捷運']);
  });
```

Add (top level of the main `describe`):

```ts
  it('shows the skeleton, not the empty text, while the first page loads', () => {
    const { fixture, el } = render();
    const skeleton = el.querySelector('app-skeleton');
    expect(skeleton).not.toBeNull();
    expect(skeleton!.getAttribute('aria-busy')).toBe('true');
    expect(skeleton!.getAttribute('aria-label')).toBe('載入中');
    expect(el.querySelector('.empty')).toBeNull();

    flushSummary('2026-10');
    flushEntries([]);
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).toBeNull();
    expect(text(el.querySelector('.empty'))).toBe('這個月沒有記錄');
  });

  it('says 沒有符合條件的記錄 with 清除篩選 when the filters match nothing', () => {
    const { fixture, el } = render('sheet');
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1 })]);
    fixture.detectChanges();
    fixture.componentInstance.setKind('income');
    fixture.detectChanges();
    flushEntries([], 0);
    fixture.detectChanges();

    expect(text(el.querySelector('.empty'))).toBe('沒有符合條件的記錄');
    expect(el.querySelector('.empty-filtered .clear-filters')).not.toBeNull();
  });

  it('ignores a late page for the previous filters and shows the empty text only for the current ones', () => {
    const { fixture, el } = render('sheet');
    flushSummary('2026-10');
    flushEntries([makeEntry({ id: 1 })]);
    fixture.detectChanges();

    fixture.componentInstance.setKind('income');
    fixture.detectChanges();
    const stale = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
    fixture.componentInstance.setKind('expense');
    fixture.detectChanges();
    const current = httpMock!.expectOne(r => r.url === '/api/accounting/entries');
    expect(current.request.params.get('kind')).toBe('expense');

    stale.flush({ items: [], total: 0, limit: 50, offset: 0 });
    fixture.detectChanges();
    expect(el.querySelector('.empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(text(el.querySelector('.match-count'))).toBe('符合條件 — 筆');

    current.flush({ items: [], total: 0, limit: 50, offset: 0 });
    fixture.detectChanges();
    expect(text(el.querySelector('.empty'))).toBe('沒有符合條件的記錄');
    expect(text(el.querySelector('.match-count'))).toBe('符合條件 0 筆');
  });
```

Inside `describe('calendar view', …)` add:

```ts
    it('shows the day skeleton, then 這天沒有記錄 or 這天沒有符合條件的記錄', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(el.querySelector('.day-entries app-skeleton')).not.toBeNull();
      flushEntries([]);
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .empty'))).toBe('這天沒有記錄');

      fixture.componentInstance.setKind('expense');
      fixture.detectChanges();
      flushEntries([]);
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .empty'))).toBe('這天沒有符合條件的記錄');
    });

    it('shows 記錄讀取失敗 with 重試 for a failed day and reloads that day', () => {
      const { fixture, el } = renderCalendar();
      (el.querySelector('button.cell[data-date="2026-10-05"]') as HTMLButtonElement).click();
      fixture.detectChanges();
      httpMock!.expectOne(r => r.url === '/api/accounting/entries').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.day-entries .load-error'))).toBe('記錄讀取失敗');
      expect(el.querySelector('.day-entries .empty')).toBeNull();

      (el.querySelector('.day-retry') as HTMLButtonElement).click();
      fixture.detectChanges();
      const req = flushEntries([makeEntry({ id: 9, entry_date: '2026-10-05' })]);
      fixture.detectChanges();
      expect(req.request.params.get('date_from')).toBe('2026-10-05');
      expect(el.querySelectorAll('.day-entries .row').length).toBe(1);
    });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: FAIL — `expected null not to be null` for `app-skeleton`, `expected '記錄讀取失敗，請稍後再試。' … ` style text mismatches (old copy `紀錄讀取失敗，請稍後再試。`), `.list-retry` null.

- [ ] **Step 3: Implement**

`timeline.ts`:

Imports — add `import { SkeletonComponent } from '../skeleton/skeleton';` and change the decorator's `imports: [CalendarMonthComponent, NgTemplateOutlet]` to `imports: [CalendarMonthComponent, NgTemplateOutlet, SkeletonComponent]`.

Signals — `readonly loading = signal(false);` → `readonly loading = signal(true);` (the first page is loading from the start, so `@empty` never flashes).

Fields, below `private dayRequestId = 0;`:

```ts
  /** Filters of the rows on screen; a reset load for another key clears them (skeleton), the same key keeps them. */
  private listKey: string | null = null;
  private dayKey: string | null = null;
```

Add a private helper next to `load()`:

```ts
  private filterKey(scope: string): string {
    return [scope, this.accountFilter(), this.kindFilter(), this.query(), this.hideRewards()].join('|');
  }
```

In `load(reset)`, replace the comment line `// On a reset the old rows stay until the new page lands (no empty flash, scroll kept).` and the reset block added in Task 2 with:

```ts
    if (reset) {
      // Another month / filter: the old rows are not this list, so the skeleton replaces them. The same key (a
      // refresh after an entry write or a preference save) keeps them until the new page lands.
      const key = this.filterKey(this.month());
      if (key !== this.listKey) {
        this.entries.set([]);
        this.listKey = key;
      }
      this.total.set(null);
    }
```

(Keep `this.loading.set(true); this.loadError.set(false);` before it. `offset` is computed before this block from `this.entries().length` only when `!reset`, so it is unaffected.)

In `clearList()` add `this.listKey = null;`.

In `loadDay(date)`, after `this.dayError.set(false);` add:

```ts
    const key = this.filterKey(date);
    if (key !== this.dayKey) {
      this.dayEntries.set([]);
      this.dayKey = key;
    }
```

In `clearDay()` add `this.dayKey = null;`. After `clearDay()` add:

```ts
  retryDay(): void {
    const day = this.selectedDay();
    if (day) {
      this.loadDay(day);
    }
  }
```

`timeline.html` — calendar day list, replace :113-122 (`@if (dayError()) { … }` through the `@for … @empty { … }`) with:

```html
        @if (dayError()) {
          <div class="load-error-block">
            <p class="load-error">記錄讀取失敗</p>
            <button type="button" class="retry day-retry" (click)="retryDay()">重試</button>
          </div>
        } @else if (dayLoading() && dayEntries().length === 0) {
          <app-skeleton rows="3" />
        } @else {
          @for (row of dayRows(); track row.key) {
            <ng-container *ngTemplateOutlet="rowTemplate; context: { $implicit: row }" />
          } @empty {
            <p class="empty">{{ filtersActive() ? '這天沒有符合條件的記錄' : '這天沒有記錄' }}</p>
          }
        }
```

List branch, replace :126-144 (from `@if (loadError()) {` to the closing `}` of `@if (hasMore())`) with:

```html
    @if (loadError()) {
      <div class="load-error-block">
        <p class="load-error">記錄讀取失敗</p>
        <button type="button" class="retry list-retry" (click)="load(true)">重試</button>
      </div>
    } @else if (loading() && entries().length === 0) {
      <app-skeleton rows="3" />
    } @else {
      @for (day of days(); track day.date) {
        <div class="day"><span>{{ day.label }}</span><b>{{ formatSigned(day.net, mainCurrency()) }}</b></div>
        @for (row of day.rows; track row.key) {
          <ng-container *ngTemplateOutlet="rowTemplate; context: { $implicit: row }" />
        }
      } @empty {
        @if (filtersActive()) {
          <div class="empty-filtered">
            <p class="empty">沒有符合條件的記錄</p>
            <button type="button" class="clear-filters" aria-label="清除篩選" (click)="clearFilters()">清除篩選</button>
          </div>
        } @else {
          <p class="empty">這個月沒有記錄</p>
        }
      }

      @if (hasMore()) {
        <div #sentinel class="sentinel" aria-hidden="true"></div>
        <button type="button" class="more" [disabled]="loading()" (click)="loadMore()">載入更多</button>
      }
    }
```

`timeline.scss` — after the `.empty, .load-error { … }` block add:

```scss
.load-error-block,
.empty-filtered {
  align-items: center;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 2rem 0;

  .empty,
  .load-error {
    margin: 0;
  }
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: PASS. If `'loads the next page from the 載入更多 fallback'` fails, check that `hasMore` still reads `total() ?? 0` (Task 2).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/timeline/
git commit -m "feat(timeline): exclusive skeleton, error with 重試, empty and rows states

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Timeline — summary and daily-summary states independent of the list

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (signals :319, :329; `loadSummary()` :609-623; `loadDaily()` :633-647; `dropDaily()` :649-652)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html` (`.sum` block from Task 2; above `<app-calendar-month>` :83)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.scss`
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:**
- Produces: `summaryLoading`, `summaryError`, `dailyLoading`, `dailyError` (`WritableSignal<boolean>`); `retrySummary(): void`; `retryDaily(): void`. `.sum` is always rendered.

- [ ] **Step 1: Write the failing tests** (top level of the main `describe`; second one inside `describe('calendar view')`)

```ts
  it('keeps the totals block with — while the summary loads and offers 重試 when it fails', () => {
    const { fixture, el } = render();
    flushEntries([]);
    fixture.detectChanges();
    const sum = el.querySelector('.sum')!;
    expect(sum.getAttribute('aria-busy')).toBe('true');
    expect([text(el.querySelector('.sum-expense')), text(el.querySelector('.sum-income')), text(el.querySelector('.sum-net'))]).toEqual(['—', '—', '—']);

    httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.sum')).not.toBeNull();
    expect(text(el.querySelector('.sum-error'))).toContain('摘要讀取失敗');
    expect(el.querySelector('.empty')).not.toBeNull();

    (el.querySelector('.sum-retry') as HTMLButtonElement).click();
    fixture.detectChanges();
    flushSummary('2026-10', { expense: '-100' });
    fixture.detectChanges();
    expect(el.querySelector('.sum-error')).toBeNull();
    expect(text(el.querySelector('.sum-expense'))).toBe('−$100');
  });
```

```ts
    it('offers 重試 when the daily figures fail, without touching the day list', () => {
      localStorage.setItem(VIEW_KEY, 'calendar');
      const { fixture, el } = render('phone');
      flushSummary('2026-10');
      httpMock!.expectOne(r => r.url === '/api/accounting/entries/summary/daily').flush('boom', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      expect(text(el.querySelector('.daily-error'))).toContain('每日摘要讀取失敗');

      (el.querySelector('.daily-retry') as HTMLButtonElement).click();
      fixture.detectChanges();
      flushDaily('2026-10', OCT);
      fixture.detectChanges();
      expect(el.querySelector('.daily-error')).toBeNull();
      expect(text(el.querySelector('button.cell[data-date="2026-10-02"] .exp'))).toBe('1,234');
    });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: FAIL — `Cannot read properties of null (reading 'getAttribute')` (`.sum` is not rendered without a summary) and `.daily-error` null.

- [ ] **Step 3: Implement**

`timeline.ts` — below `readonly summary = signal<MonthSummary | null>(null);`:

```ts
  readonly summaryLoading = signal(true);
  readonly summaryError = signal(false);
```

Below `readonly daily = signal<DailySummary | null>(null);`:

```ts
  readonly dailyLoading = signal(false);
  readonly dailyError = signal(false);
```

Replace `loadSummary(month)` with:

```ts
  private loadSummary(month: string): void {
    const id = ++this.summaryRequestId;
    this.summary.set(null);
    this.summaryLoading.set(true);
    this.summaryError.set(false);
    this.accounting.getMonthSummary(month).subscribe({
      next: summary => {
        if (id === this.summaryRequestId) {
          this.summary.set(summary);
          this.summaryLoading.set(false);
        }
      },
      error: () => {
        if (id === this.summaryRequestId) {
          this.summary.set(null);
          this.summaryError.set(true);
          this.summaryLoading.set(false);
        }
      },
    });
  }

  retrySummary(): void {
    this.loadSummary(this.month());
  }
```

Replace `loadDaily(month)` and `dropDaily()` with:

```ts
  private loadDaily(month: string): void {
    const id = ++this.dailyRequestId;
    this.dailyLoading.set(true);
    this.dailyError.set(false);
    this.accounting.getDailySummary(month).subscribe({
      next: daily => {
        if (id === this.dailyRequestId) {
          this.daily.set(daily);
          this.dailyLoading.set(false);
        }
      },
      error: () => {
        if (id === this.dailyRequestId) {
          this.daily.set(null);
          this.dailyError.set(true);
          this.dailyLoading.set(false);
        }
      },
    });
  }

  retryDaily(): void {
    this.loadDaily(this.month());
  }

  private dropDaily(): void {
    ++this.dailyRequestId;
    this.daily.set(null);
    this.dailyLoading.set(false);
    this.dailyError.set(false);
  }
```

`timeline.html` — replace the `@if (summary(); as s) { <div class="sum"> … </div> }` block (Task 2 version) with:

```html
  <div class="sum" [attr.aria-busy]="summaryLoading() ? 'true' : null">
    @if (filtersActive()) {
      <small class="sum-scope">全月 · 未套篩選</small>
    }
    @if (summaryError()) {
      <p class="sum-error">
        摘要讀取失敗
        <button type="button" class="retry sum-retry" (click)="retrySummary()">重試</button>
      </p>
    } @else {
      <div><small>支出</small><b class="neg sum-expense">{{ summary() ? expenseText(summary()!) : '—' }}</b></div>
      <div><small>收入</small><b class="pos sum-income">{{ summary() ? incomeText(summary()!) : '—' }}</b></div>
      <div><small>結餘</small><b class="sum-net">{{ summary() ? formatSigned(summary()!.net, summary()!.currency) : '—' }}</b></div>
    }
  </div>
```

Directly above `<app-calendar-month` (inside `@if (view() === 'calendar') {`) add:

```html
    @if (dailyError()) {
      <p class="load-error daily-error">
        每日摘要讀取失敗
        <button type="button" class="retry daily-retry" (click)="retryDaily()">重試</button>
      </p>
    }
```

and give the calendar element `[attr.aria-busy]="dailyLoading() ? 'true' : null"`.

`timeline.scss` — add:

```scss
.sum-error {
  align-items: center;
  color: var(--app-text-muted);
  display: flex;
  font-size: var(--fs-sm);
  gap: 8px;
  grid-column: 1 / -1;
  justify-content: center;
  margin: 0;
}

.daily-error {
  margin: 0 0 8px;
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/timeline/
git commit -m "feat(timeline): summary and daily figures keep their own loading and error states

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Passbook — exclusive load states

**Files:**
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.ts` (imports :18; `loading` :40; `ngOnInit` :93-111; `loadAccount` :113-131)
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.html` (:39-86)
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.scss`
- Test: `frontend/src/app/components/accounting/account-entries/account-entries.spec.ts`

**Interfaces:**
- Consumes: `SkeletonComponent` (Task 1).
- Produces: `retry(): void` (reloads the account when it never arrived, else `load(true)`); `loading` starts `true`.

- [ ] **Step 1: Write the failing tests** (append to `describe('AccountingAccountEntriesComponent (passbook)')`)

```ts
  it('shows the skeleton, then 記錄讀取失敗 with 重試 instead of rows and empty text, and retries page 1', () => {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄');

    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    fixture.detectChanges();
    expectSummary('2026-09-16', '2026-10-15').flush(SUMMARY);
    expectEntries().flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el.querySelector('.load-error')?.textContent?.trim()).toBe('記錄讀取失敗');
    expect(el.querySelector('.entries-empty')).toBeNull();
    expect(el.querySelector('app-skeleton')).toBeNull();
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄');

    el.querySelector<HTMLButtonElement>('.entries-retry')!.click();
    fixture.detectChanges();
    expectEntries().flush(page(PERIOD));
    fixture.detectChanges();
    expect(el.querySelector('.load-error')).toBeNull();
    expect(el.querySelectorAll('.entry').length).toBe(4);
    expect(el.querySelector('.lbl')?.textContent?.trim()).toBe('本期記錄 (4)');
  });

  it('retries the account itself when it failed to load', () => {
    const fixture = TestBed.createComponent(AccountingAccountEntriesComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    http.expectOne('/api/accounting/accounts/7').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    expect(el.querySelector('.load-error')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('.entries-retry')!.click();
    fixture.detectChanges();
    http.expectOne('/api/accounting/accounts/7').flush(ACCOUNT);
    expectEntries().flush(page([]));
    fixture.detectChanges();
    expectSummary('2026-09-16', '2026-10-15').flush(SUMMARY);
    fixture.detectChanges();
    expect(el.querySelector('.entries-empty')?.textContent?.trim()).toBe('沒有符合條件的記錄');
  });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-entries/account-entries.spec.ts`
Expected: FAIL — `expected null not to be null` (no skeleton) and `.entries-retry` null.

- [ ] **Step 3: Implement**

`account-entries.ts` — add `import { SkeletonComponent } from '../skeleton/skeleton';`, decorator `imports: [RouterLink, SkeletonComponent]`; `readonly loading = signal(false);` → `readonly loading = signal(true);`. In the `paramMap` subscriber, after `this.loadError.set(false);` add `this.loading.set(true);`. In `loadAccount()`'s `error` handler set `this.loading.set(false);` next to `this.loadError.set(true);`. Add:

```ts
  /** 重試: page 1 of the current filters, or the account itself when it never arrived. */
  retry(): void {
    if (this.account()) {
      this.load(true);
    } else {
      this.loadError.set(false);
      this.loading.set(true);
      this.loadAccount();
    }
  }
```

`account-entries.html` — replace :39-86 (from `@if (loadError()) {` to the end of the `@if (hasMore())` block) with:

```html
  <div class="lbl">本期記錄@if (!loading() && !loadError()) { ({{ total() }})}</div>
  <div class="entry-list">
    @if (loadError()) {
      <div class="load-error-block">
        <p class="load-error">記錄讀取失敗</p>
        <button type="button" class="retry entries-retry" (click)="retry()">重試</button>
      </div>
    } @else if (loading() && entries().length === 0) {
      <app-skeleton rows="3" />
    } @else {
      @for (entry of entries(); track entry.id) {
        <!-- the existing <a class="row entry" …> … </a> row, unchanged -->
      } @empty {
        <p class="entries-empty">沒有符合條件的記錄</p>
      }
    }
  </div>

  @if (hasMore() && !loadError()) {
    <button type="button" class="load-more" [disabled]="loading()" (click)="load(false)">
      載入更多（{{ entries().length }} / {{ total() }}）
    </button>
  }
```

Move the existing `<a class="row entry" …>…</a>` element (old :46-74) verbatim into the `@for` body where the comment is (the comment line is not kept).

`account-entries.scss` — add:

```scss
.load-error-block {
  align-items: center;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 1.5rem 0;

  .load-error { margin: 0; }
}

.retry {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 3px 10px;
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-entries/account-entries.spec.ts`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-entries/
git commit -m "feat(passbook): exclusive skeleton, error with 重試, empty and rows states

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Accounts overview — import error with its own counter, 重試, skeleton, empty state

**Files:**
- Modify: `frontend/src/app/components/accounting/accounts/accounts.ts` (imports :1-12; `requestId` :117; signals :119-126; `load()` :180-211)
- Modify: `frontend/src/app/components/accounting/accounts/accounts.html` (:7-11, :34-43)
- Modify: `frontend/src/app/components/accounting/accounts/accounts.scss`
- Test: `frontend/src/app/components/accounting/accounts/accounts.spec.ts` (rewrite `'links the empty state to the settings page'` :324-328; new tests)

**Interfaces:**
- Consumes: `SkeletonComponent` (Task 1); `AccountingService.getLatestImport(): Observable<ImportRun | null>` (404 → `null`).
- Produces: `importError: WritableSignal<boolean>`; `retryImport(): void` (bumps only `importRequestId`); `retry(): void` (whole-page reload); private `accountsRequestId`, `importRequestId` (replaces `requestId`).

- [ ] **Step 1: Write the failing tests**

Replace `'links the empty state to the settings page'` with:

```ts
  it('offers 新增帳戶 and 前往記帳設定 when there are no accounts', () => {
    const el = render([], null).nativeElement as HTMLElement;
    const empty = el.querySelector('.empty-state')!;
    expect(empty.querySelector('h4')?.textContent?.trim()).toBe('還沒有帳戶');
    expect(empty.querySelector('p')?.textContent?.trim()).toBe('新增帳戶開始記帳，或上傳 MOZE 備份匯入。');
    const links = Array.from(empty.querySelectorAll('a'));
    expect(links.map(a => [a.textContent?.trim(), a.getAttribute('href')])).toEqual([
      ['新增帳戶', '/accounting/accounts/new'],
      ['前往記帳設定', '/accounting/settings'],
    ]);
  });
```

Add:

```ts
  const PREFERENCE_BODY = {
    expense_income_colors: 'red_green', keypad_layout: 'calculator', week_start: 0, main_currency: 'TWD',
    hide_rewards_on_timeline: false, abbreviate_totals: true,
  };
  const ACCOUNTS_GET = (r: { url: string; urlWithParams: string; method: string }) =>
    r.method === 'GET' && r.url === '/api/accounting/accounts' && !r.urlWithParams.includes('include_archived=true');

  it('shows the skeleton until the first load answers', () => {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    http.expectOne(ACCOUNTS_GET).flush(ACCOUNTS);
    http.expectOne('/api/accounting/imports/latest').flush(LATEST);
    http.expectOne('/api/accounting/preference').flush(PREFERENCE_BODY);
    fixture.detectChanges();
    flushBills();
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).toBeNull();
    expect(rows(el).length).toBeGreaterThan(0);
  });

  it('shows nothing for a 404 and 匯入狀態無法取得 with 重試 for a failed lookup, keeping the list', () => {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    http.expectOne(ACCOUNTS_GET).flush(ACCOUNTS);
    http.expectOne('/api/accounting/imports/latest').flush('boom', { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/preference').flush(PREFERENCE_BODY);
    fixture.detectChanges();
    flushBills();
    fixture.detectChanges();

    expect(el.querySelector('.import-error')?.textContent).toContain('匯入狀態無法取得');
    expect(rows(el).length).toBeGreaterThan(0);

    el.querySelector<HTMLButtonElement>('.import-retry')!.click();
    fixture.detectChanges();
    http.expectOne('/api/accounting/imports/latest').flush({ detail: 'none' }, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    expect(el.querySelector('.import-error')).toBeNull();
    expect(el.querySelector('.import-status')).toBeNull();
  });

  it('keeps an in-flight accounts reload when the import is retried, and drops the older import failure', () => {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    http.expectOne(ACCOUNTS_GET).flush(ACCOUNTS);
    http.expectOne('/api/accounting/imports/latest').flush('boom', { status: 500, statusText: 'Server Error' });
    http.expectOne('/api/accounting/preference').flush(PREFERENCE_BODY);
    fixture.detectChanges();
    flushBills();
    fixture.detectChanges();

    // An accounts reload (entry write elsewhere) is in flight…
    const service = TestBed.inject(AccountingService);
    service.deleteEntry(99).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/99').flush(null);
    fixture.detectChanges();
    const reload = http.expectOne(ACCOUNTS_GET);

    // …when the owner retries the import lookup.
    el.querySelector<HTMLButtonElement>('.import-retry')!.click();
    fixture.detectChanges();
    const [olderImport, newerImport] = http.match('/api/accounting/imports/latest');
    newerImport.flush(LATEST);
    reload.flush([account({ id: 1, name: '錢包', balance: '9999', balance_main: '9999' }), ...ACCOUNTS.slice(1)]);
    olderImport.flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    flushBills();
    fixture.detectChanges();

    expect(el.querySelector('.import-error')).toBeNull();
    expect(el.querySelector('.import-status')?.textContent).toContain('成功');
    expect(rows(el)[0].querySelector('.bal')?.textContent?.trim()).toBe('$9,999');
  });

  it('offers 重試 on a failed account list and reloads it', () => {
    const fixture = TestBed.createComponent(AccountingAccountsComponent);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    http.expectOne(ACCOUNTS_GET).flush('boom', { status: 500, statusText: 'Server Error' });
    for (const pending of http.match('/api/accounting/imports/latest')) {
      if (!pending.cancelled) pending.flush(LATEST);
    }
    for (const pending of http.match('/api/accounting/preference')) {
      if (!pending.cancelled) pending.flush(PREFERENCE_BODY);
    }
    fixture.detectChanges();
    expect(el.querySelector('.load-error')?.textContent).toContain('帳戶讀取失敗');

    el.querySelector<HTMLButtonElement>('.accounts-retry')!.click();
    fixture.detectChanges();
    expect(el.querySelector('app-skeleton')).not.toBeNull();
    http.expectOne(ACCOUNTS_GET).flush(ACCOUNTS);
    http.expectOne('/api/accounting/imports/latest').flush(LATEST);
    for (const pending of http.match('/api/accounting/preference')) {
      pending.flush(PREFERENCE_BODY);
    }
    fixture.detectChanges();
    flushBills();
    fixture.detectChanges();
    expect(el.querySelector('.load-error')).toBeNull();
    expect(rows(el).length).toBeGreaterThan(0);
  });
```

(`forkJoin` unsubscribes the sibling requests when `getAccounts()` errors, so they arrive `cancelled`; the loops tolerate either case. After a failed first load the preference is not cached, so the retry may ask for it again — the last loop flushes it if asked.)

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounts/accounts.spec.ts`
Expected: FAIL — `expected '尚未匯入 MOZE 資料' to be '還沒有帳戶'`, no `app-skeleton`, `.import-error` null (a failed import currently fails the whole forkJoin and shows `帳戶讀取失敗`).

- [ ] **Step 3: Implement**

`accounts.ts`:

```ts
import { Observable, catchError, forkJoin, map, of } from 'rxjs';
// …
import { SkeletonComponent } from '../skeleton/skeleton';

interface ImportLookup {
  ok: boolean;
  v: ImportRun | null;
}
```

Decorator `imports: [DatePipe, NgTemplateOutlet, RouterLink, SkeletonComponent]`.

Replace `private requestId = 0;` with:

```ts
  /** Guards the page load (accounts + preference). */
  private accountsRequestId = 0;
  /** Guards the import lookup; an import 重試 bumps only this one (spec §1.3). */
  private importRequestId = 0;
```

Below `readonly loadError = signal(false);` add `readonly importError = signal(false);`.

Replace `load()` with:

```ts
  private load(): void {
    const id = ++this.accountsRequestId;
    const importId = ++this.importRequestId;
    this.today.set(todayIso());
    forkJoin({
      accounts: this.accountingService.getAccounts(),
      latest: this.importLookup(),
      preference: this.accountingService.getPreference().pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ accounts, latest, preference }) => {
        if (importId === this.importRequestId) {
          this.applyImport(latest);
        }
        if (id !== this.accountsRequestId) {
          return;
        }
        this.accounts.set(accounts);
        this.mainCurrency.set(preference?.main_currency ?? 'TWD');
        this.loadError.set(false);
        this.loaded.set(true);
        if (this.archived() !== null) {
          this.archived.set(null);
          if (this.archivedOpen()) {
            this.loadArchived();
          }
        }
      },
      error: () => {
        if (id !== this.accountsRequestId) {
          return;
        }
        this.loadError.set(true);
        this.loaded.set(true);
      },
    });
  }

  /** `GET /imports/latest` that never fails the page: 404 → `{ok: true, v: null}`, any error → `{ok: false}`. */
  private importLookup(): Observable<ImportLookup> {
    return this.accountingService.getLatestImport().pipe(
      map((v): ImportLookup => ({ ok: true, v })),
      catchError(() => of<ImportLookup>({ ok: false, v: null })),
    );
  }

  private applyImport(result: ImportLookup): void {
    this.latestImport.set(result.v);
    this.importError.set(!result.ok);
  }

  /** The import panel's 重試: only the import lookup, so an accounts reload in flight stays valid. */
  retryImport(): void {
    const importId = ++this.importRequestId;
    this.importLookup().subscribe(result => {
      if (importId === this.importRequestId) {
        this.applyImport(result);
      }
    });
  }

  /** The whole-page 重試 after `帳戶讀取失敗`. */
  retry(): void {
    this.loadError.set(false);
    this.loaded.set(false);
    this.load();
  }
```

`accounts.html` — replace :7-11 with:

```html
  @if (importError()) {
    <p class="import-status import-status--failed import-error">
      匯入狀態無法取得
      <button type="button" class="retry import-retry" (click)="retryImport()">重試</button>
    </p>
  } @else if (latestImport(); as run) {
    <p class="import-status" [class.import-status--failed]="run.status === 'failed'">
      最近匯入 {{ run.started_at | date: 'yyyy-MM-dd HH:mm' }} · {{ statusLabel(run) }} · 待確認 {{ reviewCount() }} 筆
    </p>
  }
```

Replace :34-43 (`@if (loadError()) { … } @else if (loaded() && accounts().length === 0) { … } @else if (loaded()) {`) with:

```html
  @if (loadError()) {
    <div class="load-error-block">
      <p class="load-error">帳戶讀取失敗，請稍後再試。</p>
      <button type="button" class="retry accounts-retry" (click)="retry()">重試</button>
    </div>
  } @else if (!loaded()) {
    <app-skeleton rows="3" />
  } @else if (accounts().length === 0) {
    <div class="empty-state">
      <i class="pi pi-wallet" aria-hidden="true"></i>
      <h4>還沒有帳戶</h4>
      <p>新增帳戶開始記帳，或上傳 MOZE 備份匯入。</p>
      <div class="empty-actions">
        <a class="empty-primary" routerLink="/accounting/accounts/new">新增帳戶</a>
        <a class="empty-secondary" routerLink="/accounting/settings">前往記帳設定</a>
      </div>
    </div>
  } @else {
```

(the rest of the loaded content and its closing `}` stay as they are).

`accounts.scss` — add:

```scss
.import-error {
  align-items: center;
  display: flex;
  gap: 8px;
}

.load-error-block {
  align-items: center;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 1.5rem 0;

  .load-error { margin: 0; }
}

.retry {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 700;
  padding: 3px 10px;
}

.empty-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.empty-primary {
  background: var(--app-primary);
  border-radius: var(--radius-pill);
  color: var(--app-on-primary, #fff);
  font-weight: 700;
  padding: 6px 14px;
  text-decoration: none;
}

.empty-secondary {
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text);
  font-weight: 700;
  padding: 6px 14px;
  text-decoration: none;
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounts/accounts.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounts/
git commit -m "feat(accounts): import lookup with its own counter and 重試, skeleton, empty state actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Entry form — 事件類型 tile replaces the 進階 fold

**Files:**
- Modify: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.ts` (inputs :31-46)
- Modify: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.html` (:2-14, :131-133)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts` (imports :52, :62; members near `scheduling` :254; `selectKind` :685)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.html` (:205-216, :253-273)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.scss` (`.advanced` :281-307)
- Test: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts`, `frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`, `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts` (:376-394, :520-540)

**Interfaces:**
- Consumes: `SCHEDULE_TABS`, `ScheduleTab`, `tabsFor(kind, {editing})`, `ruleDayFor(draft)` from `../schedule-tabs/schedule-draft`; `isHandledKey`.
- Produces: `ScheduleTabsComponent.showTabs = input(true)`; footer `<p class="schedule-footer event-summary">`. On `EntryFormComponent`: `readonly eventTabs = SCHEDULE_TABS`; `readonly enabledEventTabs: Signal<ScheduleTab[]>`; `eventTabEnabled(tab: ScheduleTab): boolean`; `selectEventTab(tab: ScheduleTab): void`; `onEventTabKeydown(event: KeyboardEvent): void`. The new tabs keep class `schedule-tab` (plus `event-tab`, `data-tab`), so existing `tap(el, '.schedule-tab', '週期')` calls keep working.

- [ ] **Step 1: Write the failing tests**

`schedule-tabs.spec.ts` — extend `Setup` with `showTabs?: boolean;`, add `ref.setInput('showTabs', setup.showTabs ?? true);` in `render()` before `ref.setInput('draft', …)`, and add:

```ts
  it('renders no tablist of its own when the host shows the tabs', () => {
    const { el } = render({ showTabs: false, draft: { ...defaultDraft('2026-10-22'), tab: 'recurring' } });
    expect(el.querySelector('[role="tablist"]')).toBeNull();
    expect(el.querySelector('.sched-start')).not.toBeNull();
    expect(el.querySelector('.schedule-footer')!.classList).toContain('event-summary');
  });
```

`entry-form-schedule.spec.ts` — add:

```ts
  function eventTab(el: HTMLElement, label: string): HTMLButtonElement {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('.event-type .event-tab')).find(tab => text(tab) === label)!;
  }

  it('renders 事件類型 as a full-width tab list between 時間 and the invoice, without a 進階 fold', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const tile = el.querySelector('.tile.wide.event-type')!;
    const list = tile.querySelector('[role="tablist"]')!;
    expect(list.getAttribute('aria-label')).toBe('事件類型');
    expect(Array.from(list.querySelectorAll('[role="tab"]')).map(text)).toEqual(['單次', '週期', '分期']);
    expect(eventTab(el, '單次').getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.advanced')).toBeNull();
    expect(el.querySelector('app-schedule-tabs [role="tablist"]')).toBeNull();
    const tiles = Array.from(el.querySelector('.tiles')!.children);
    expect(tiles.indexOf(tile)).toBeGreaterThan(tiles.indexOf(el.querySelector('.time-input')!.closest('.tile')!));
    expect(tiles.indexOf(tile)).toBeLessThan(tiles.indexOf(el.querySelector('.invoice-tile')!));
  });

  it('keeps 分期 visible but disabled for 轉帳 and ignores a click on it', async () => {
    const { el } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const installment = eventTab(el, '分期');
    expect(installment.getAttribute('aria-disabled')).toBe('true');
    expect(installment.getAttribute('tabindex')).toBe('-1');
    installment.click();
    settle();
    expect(eventTab(el, '單次').getAttribute('aria-selected')).toBe('true');
  });

  it('moves between enabled tabs with ← / → and never saves on ⏎ there', async () => {
    const { el, left } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const single = eventTab(el, '單次');
    single.focus();
    single.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(eventTab(el, '週期'));
    eventTab(el, '週期').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(single); // 分期 is skipped
    single.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    settle();
    httpMock.expectNone(r => r.method === 'POST');
    expect(left()).toBe(false);
  });

  it('restores the hidden tiles and clears schedule errors when 分期 goes back to 單次', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    tap(el, '.event-tab', '分期');
    expect(el.querySelector('.fee-open')).toBeNull();
    expect(el.querySelector('.invoice-tile')).toBeNull();
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.fieldErrors.set({ times: '分期至少 2 期' });
    form.error.set('分期至少 2 期');
    settle();
    expect(el.querySelector('.field-error[data-field="times"]')).not.toBeNull();

    tap(el, '.event-tab', '單次');
    expect(el.querySelector('.fee-open')).not.toBeNull();
    expect(el.querySelector('.invoice-tile')).not.toBeNull();
    expect(el.querySelector('.field-error')).toBeNull();
    expect(el.querySelector('.form-error')).toBeNull();
    expect(form.fieldErrors()).toEqual({});
  });

  it('shows the definition kind selected with the other tabs disabled in definition mode', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5 }), instances: [] });
    respond('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(eventTab(el, '週期').getAttribute('aria-selected')).toBe('true');
    expect(eventTab(el, '單次').getAttribute('aria-disabled')).toBe('true');
    expect(eventTab(el, '分期').getAttribute('aria-disabled')).toBe('true');
  });
```

Change the existing assertion in `'saves a recurring transfer and offers no 分期 for 轉帳'`:

```ts
    expect(Array.from(el.querySelectorAll('.schedule-tab')).map(text)).toEqual(['單次', '週期', '分期']);
    expect(eventTab(el, '分期').getAttribute('aria-disabled')).toBe('true');
```

`entry-form.spec.ts` — in `'offers only 單次 when editing an entry…'` replace the two `not.toContain` lines with:

```ts
    const disabled = Array.from(el.querySelectorAll('.event-tab'))
      .filter(node => node.getAttribute('aria-disabled') === 'true')
      .map(node => text(node));
    expect(tabs).toEqual(['單次', '週期', '分期']);
    expect(disabled).toEqual(['週期', '分期']);
```

In `'does not save on ⏎ while an IME candidate is committed (keyCode 229) or on the 進階 summary'` rename the title to `'… or on an 事件類型 tab'` and replace `el.querySelector('.advanced summary')!` with `el.querySelector('.event-tab')!`.

Add the single-entry regression:

```ts
  it('still shows 入帳日 for a new single entry and sends posted_date', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '7', '0');
    const posted = el.querySelector('.event-type ~ .schedule-block .posted-input') as HTMLInputElement;
    expect(posted).not.toBeNull();
    posted.value = '2026-10-05';
    posted.dispatchEvent(new Event('change'));
    settle();
    keys(el, '✓');
    const req = httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(req.request.body).toMatchObject({ entry_date: '2026-10-02', posted_date: '2026-10-05' });
    req.flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
    settle();
  });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: FAIL — `NG0303: Can't set value of the 'showTabs' input`, `Cannot read properties of null (reading 'querySelector')` for `.event-type`.

- [ ] **Step 3: Implement**

`schedule-tabs.ts` — below `readonly errors = input<Record<string, string>>({});`:

```ts
  /** False when the host renders the 單次／週期／分期 tabs itself (the entry form's 事件類型 tile). */
  readonly showTabs = input(true);
```

`schedule-tabs.html` — wrap the tablist (:2-14) in `@if (showTabs()) { … }` and change the footer to `<p class="schedule-footer event-summary">{{ footer() }}</p>`.

`entry-form.ts` — imports:

```ts
import { NO_ENTER_SAVE_TAGS, accountLabel, isHandledKey } from '../accounting-ui';
// …
import { SCHEDULE_TABS, ScheduleDraft, ScheduleTab, defaultDraft, ruleDayFor, tabsFor } from '../schedule-tabs/schedule-draft';
```

Below `readonly scheduling = computed(…)`:

```ts
  readonly eventTabs = SCHEDULE_TABS;
  /** Enabled 事件類型 tabs: a definition keeps its own kind; otherwise `tabsFor` (editing → 單次 only). */
  readonly enabledEventTabs = computed<ScheduleTab[]>(() =>
    this.scheduleId() !== null ? [this.scheduleDraft().tab] : tabsFor(this.kind(), { editing: this.editing() }),
  );
```

Below `selectKind(…)`:

```ts
  eventTabEnabled(tab: ScheduleTab): boolean {
    return this.enabledEventTabs().includes(tab);
  }

  /** As the old 進階 tabs did (rule day re-derived); schedule-only errors go with the old tab. */
  selectEventTab(tab: ScheduleTab): void {
    if (!this.eventTabEnabled(tab) || tab === this.scheduleDraft().tab) {
      return;
    }
    this.scheduleDraft.update(draft => {
      const next = { ...draft, tab };
      return { ...next, dayOfMonth: ruleDayFor(next) };
    });
    this.fieldErrors.set({});
    this.error.set(null);
  }

  /** ← / → move focus between enabled tabs; Space / ⏎ press the focused tab (native button). */
  onEventTabKeydown(event: KeyboardEvent): void {
    if ((event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') || isHandledKey(event)) {
      return;
    }
    event.preventDefault();
    const enabled = SCHEDULE_TABS.map(option => option.tab).filter(tab => this.eventTabEnabled(tab));
    if (enabled.length === 0) {
      return;
    }
    const current = ((event.target as HTMLElement).dataset['tab'] as ScheduleTab | undefined) ?? this.scheduleDraft().tab;
    const index = Math.max(0, enabled.indexOf(current));
    const next = enabled[(index + (event.key === 'ArrowRight' ? 1 : enabled.length - 1)) % enabled.length];
    this.host.nativeElement.querySelector<HTMLElement>(`.event-tab[data-tab="${next}"]`)?.focus();
  }
```

`entry-form.html` — directly after the 時間 tile (`<label class="tile"> … time-input … </label>`, :210-213) insert:

```html
        <div class="tile wide event-type">
          <span class="cap">事件類型</span>
          <div class="event-tabs" role="tablist" aria-label="事件類型" (keydown)="onEventTabKeydown($event)">
            @for (option of eventTabs; track option.tab) {
              <button
                type="button"
                role="tab"
                class="schedule-tab event-tab"
                [class.on]="scheduleDraft().tab === option.tab"
                [attr.data-tab]="option.tab"
                [attr.aria-selected]="scheduleDraft().tab === option.tab"
                [attr.aria-disabled]="eventTabEnabled(option.tab) ? null : 'true'"
                [attr.tabindex]="scheduleDraft().tab === option.tab ? 0 : -1"
                (click)="selectEventTab(option.tab)"
              >{{ option.label }}</button>
            }
          </div>
        </div>
        <div class="schedule-block">
          <app-schedule-tabs
            [kind]="kind()"
            [entryDate]="entryDate()"
            [amount]="amount()"
            [currency]="accountCurrency()"
            [accounts]="accountOptions()"
            [accountId]="accountId()"
            [disabled]="locked() || definitionLocked() || definitionReadOnly() || createdScheduleId() !== null"
            [definitionMode]="scheduleId() !== null"
            [editing]="editing()"
            [errors]="fieldErrors()"
            [showTabs]="false"
            [(draft)]="scheduleDraft"
          >
            <label class="adv-row">
              <span>入帳日</span>
              <input type="date" class="posted-input" [value]="postedDate() || entryDate()" (change)="postedDate.set($any($event.target).value)" />
            </label>
          </app-schedule-tabs>
        </div>
```

and delete the old `<details class="advanced" …> … </details>` block (:253-273).

`entry-form.scss` — replace the `.advanced { … }` block with:

```scss
.event-type {
  flex-wrap: wrap;
}

.event-tabs {
  display: flex;
  gap: 2px;

  .event-tab {
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    color: var(--app-text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
    font-weight: 700;
    padding: 4px 10px;

    &.on {
      border-color: var(--app-primary);
      color: var(--app-text);
    }

    &[aria-disabled='true'] {
      cursor: default;
      opacity: 0.45;
    }
  }
}

.schedule-block {
  color: var(--app-text-muted);
  font-size: 0.8rem;
  grid-column: 1 / -1;

  .adv-row {
    align-items: center;
    display: flex;
    gap: 10px;
    justify-content: space-between;
    padding: 6px 0;
  }

  input {
    background: var(--app-surface-soft);
    border: 1px solid var(--app-border);
    border-radius: var(--radius-sm);
    color: var(--app-text);
    font: inherit;
    padding: 4px 8px;
  }
}

.event-summary {
  color: var(--app-text-muted);
  font-size: 0.78rem;
}
```

- [ ] **Step 4: Run and see it pass**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: PASS. (The test `'shows only 單次-compatible fields…'` that asserts `'排程不支援'` still passes here; Task 9 changes it.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/schedule-tabs/ frontend/src/app/components/accounting/entry-form/
git commit -m "feat(entry-form): 事件類型 tile replaces the 進階 fold

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Entry form — unsupported-fields line and editing hint

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.html` (:214-216 and after the `.schedule-block` from Task 8)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.scss` (`.tile.schedule-unsupported` :162-167)
- Test: `frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts` (:245-263), `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts`

**Interfaces:**
- Produces: `.tile.wide.schedule-unsupported[aria-live=polite]` text `週期／分期不含：手續費、拆帳、外幣、發票`; `.tile.wide.event-hint` text `要改週期／分期，請到提醒中心的排程管理` when `editing()`.

- [ ] **Step 1: Write the failing tests**

In `entry-form-schedule.spec.ts` replace `expect(text(el.querySelector('.schedule-unsupported'))).toBe('排程不支援');` with:

```ts
    const unsupported = el.querySelector('.tile.wide.schedule-unsupported')!;
    expect(text(unsupported)).toBe('週期／分期不含：手續費、拆帳、外幣、發票');
    expect(unsupported.getAttribute('aria-live')).toBe('polite');
    tap(el, '.event-tab', '單次');
    expect(el.querySelector('.schedule-unsupported')).toBeNull();
```

In `entry-form.spec.ts`, inside `'offers only 單次 when editing an entry…'` after the `disabled` assertion add:

```ts
    expect(text(el.querySelector('.event-hint'))).toBe('要改週期／分期，請到提醒中心的排程管理');
```

and in `'creates an expense in three taps…'` before `keys(…)` add `expect(el.querySelector('.event-hint')).toBeNull();`.

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts --include src/app/components/accounting/entry-form/entry-form.spec.ts`
Expected: FAIL — `expected '排程不支援' to be '週期／分期不含：手續費、拆帳、外幣、發票'`; `.event-hint` text `''`.

- [ ] **Step 3: Implement**

`entry-form.html` — delete the old block

```html
        @if (scheduling()) {
          <p class="tile wide schedule-unsupported">排程不支援</p>
        }
```

and, directly after the closing `</div>` of `.schedule-block`, insert:

```html
        @if (editing()) {
          <p class="tile wide event-hint">要改週期／分期，請到提醒中心的排程管理</p>
        }
        @if (scheduling()) {
          <p class="tile wide schedule-unsupported" aria-live="polite">週期／分期不含：手續費、拆帳、外幣、發票</p>
        }
```

`entry-form.scss` — replace `.tile.schedule-unsupported { … }` with:

```scss
.tile.schedule-unsupported,
.tile.event-hint {
  background: none;
  color: var(--app-text-muted);
  font-size: 0.8rem;
  font-weight: 400;
  justify-content: flex-start;
  margin: 0;
  min-height: 0;
  padding: 0 2px;
}
```

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/entry-form/
git commit -m "feat(entry-form): name the fields schedules leave out and point edits to 排程管理

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Wording — 記錄, 記帳設定, 首期入帳日

**Files (full list):**
- Modify: `frontend/src/app/components/shell/navigation.ts:53` (`label: '紀錄', title: '記帳紀錄'` → `label: '記錄', title: '記帳記錄'`), `:55` (`label: '設定'` → `label: '記帳設定'`, title unchanged)
- Modify: `frontend/src/app/components/accounting/reminders/reminders.html:4` (`aria-label="返回紀錄"` → `aria-label="返回記錄"`)
- Modify: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.html:90` (`首次還款日` → `首期入帳日`)
- Modify: `frontend/src/app/components/accounting/schedule-math.ts:117` (footer string; the doc comment at :115 and identifiers stay)
- Test: `frontend/src/app/app.routes.spec.ts:114-132`, `frontend/src/app/components/mobile-nav/mobile-nav.spec.ts:36-40`, `frontend/src/app/components/accounting/schedule-math.spec.ts:45`, `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts:127`, `frontend/src/app/components/accounting/reminders/reminders.spec.ts` (new assertion)

The other five accounting 紀錄 occurrences (`account-entries.html:40, 77`, `timeline.html:114, 120, 127, 137`) were rewritten by Tasks 4 and 6.

**Interfaces:** none (strings only).

- [ ] **Step 1: Update the specs first**

`app.routes.spec.ts`:

```ts
  it('points the accounting sub-nav at 記錄, 帳戶 and 記帳設定', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting');
    expect(accounting.items.map(item => [item.label, item.path])).toEqual([
      ['記錄', '/accounting'],
      ['帳戶', '/accounting/accounts'],
      ['記帳設定', '/accounting/settings'],
    ]);
    expect(accounting.items[0].title).toBe('記帳記錄');
  });
```

and in `HIGHLIGHT`: every `'紀錄'` → `'記錄'`, `['/accounting/settings', '設定']` → `['/accounting/settings', '記帳設定']`.

`mobile-nav.spec.ts`:

```ts
  it('shows the accounting sub-nav 記錄 / 帳戶 / 記帳設定', () => {
    const el = render('accounting-timeline', 'accounting');

    const links = Array.from(el.querySelectorAll('.m-subnav a'));
    expect(links.map(a => a.textContent?.trim())).toEqual(['記錄', '帳戶', '記帳設定']);
```

`schedule-math.spec.ts:45`:

```ts
    expect(installmentFooter(10000, 3, '2026-11-03', 'TWD')).toBe('分期：#1 / 3（$10,000） 首期入帳日將從 2026/11/03 開始（3 期）');
```

`schedule-tabs.spec.ts:127`:

```ts
    expect(text(el.querySelector('.schedule-footer'))).toBe('分期：#1 / 3（$10,000） 首期入帳日將從 2026/11/03 開始（3 期）');
```

and in the same test add `expect(text(el.querySelector('.sched-first')!.closest('.sched-row')!.querySelector('.sched-label'))).toBe('首期入帳日');`.

`reminders.spec.ts` — in the first test that renders the page add `expect(el.querySelector('.back-link')?.getAttribute('aria-label')).toBe('返回記錄');` (use that test's own root-element variable name).

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/app.routes.spec.ts --include src/app/components/mobile-nav/mobile-nav.spec.ts --include src/app/components/accounting/schedule-math.spec.ts --include src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts --include src/app/components/accounting/reminders/reminders.spec.ts`
Expected: FAIL — `expected [ [ '紀錄', '/accounting' ], …] to deeply equal [ [ '記錄', '/accounting' ], …]`, footer `首次還款日將從 … 開始進行` mismatch.

- [ ] **Step 3: Apply the edits listed under Files.** `schedule-math.ts:117` becomes:

```ts
  return `分期：#1 / ${times}（${formatMoney(total, currency)}） 首期入帳日將從 ${slashDate(firstDate)} 開始（${times} 期）`;
```

Then confirm nothing user-visible is left: `grep -rn "紀錄" frontend/src/app/components/accounting frontend/src/app/components/shell/navigation.ts frontend/src/app/components/mobile-nav` must print nothing; `grep -rn "首次還款日" frontend/src/app --include=*.html` must print nothing (comments in `.ts` files stay).

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/shell/navigation.ts frontend/src/app/components/accounting/reminders/reminders.html frontend/src/app/components/accounting/reminders/reminders.spec.ts frontend/src/app/components/accounting/schedule-tabs/ frontend/src/app/components/accounting/schedule-math.ts frontend/src/app/components/accounting/schedule-math.spec.ts frontend/src/app/app.routes.spec.ts frontend/src/app/components/mobile-nav/mobile-nav.spec.ts
git commit -m "fix(accounting): wording 記錄, 記帳設定, 首期入帳日

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: `textOn()` contrast helper and 交易類型／事件類型 in entry detail

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-ui.ts` (new section after `colorOf` :38-41)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (import :30; members near `colorOf` :211-212; after `kindLabel` :445-447)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.html` (`.dhead` :14; `.dgrid` :58)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.scss` (`.dhead { color: #151821; }` :8)
- Test: `frontend/src/app/components/accounting/accounting-ui.spec.ts`, `frontend/src/app/components/accounting/entry-detail/entry-detail.spec.ts`

**Interfaces:**
- Produces: `export function contrastRatio(a: string, b: string): number | null`; `export function textOn(bg: string | null): string | null` (returns `'#1d1c1a'` or `'#ffffff'`, ties → dark; `null` for null / non-hex input); `EntryDetailComponent.textOn`, `EntryDetailComponent.eventKindLabel(detail: EntryDetail): '單次' | '週期' | '分期'`.

- [ ] **Step 1: Write the failing tests**

`accounting-ui.spec.ts` — add `contrastRatio, textOn` to the import list and:

```ts
  it('picks the readable header text for a category colour (WCAG contrast)', () => {
    expect(contrastRatio('#d4823b', '#1d1c1a')).toBeCloseTo(5.72, 2);
    expect(contrastRatio('#d4823b', '#ffffff')).toBeCloseTo(2.98, 2);
    expect(textOn('#d4823b')).toBe('#1d1c1a');
    expect(textOn('#2b2d42')).toBe('#ffffff');
    expect(textOn('#fff')).toBe('#1d1c1a');
    expect(textOn(null)).toBeNull();
    expect(textOn('var(--app-surface-soft)')).toBeNull();
  });
```

`entry-detail.spec.ts` — add:

```ts
  it('splits 類型 into 交易類型 and 事件類型 and colours the header text for contrast', () => {
    const fixture = render(RECEIVABLE);
    const cells = Array.from(el(fixture).querySelectorAll('.dgrid > div')).map(cell => [
      cell.querySelector('small')?.textContent?.trim(),
      cell.querySelector('b')?.textContent?.trim(),
    ]);
    expect(cells).toContainEqual(['交易類型', '應收款項']);
    expect(cells).toContainEqual(['事件類型', '單次']);
    expect(cells.some(([label]) => label === '類型')).toBe(false);
    const head = el(fixture).querySelector<HTMLElement>('.dhead')!;
    expect(['rgb(29, 28, 26)', '#1d1c1a']).toContain(head.style.color);

    const component = fixture.componentInstance;
    expect(component.eventKindLabel({ ...RECEIVABLE, schedule: { kind: 'recurring' } } as unknown as EntryDetail)).toBe('週期');
    expect(component.eventKindLabel({ ...RECEIVABLE, schedule: { kind: 'installment' } } as unknown as EntryDetail)).toBe('分期');
  });
```

(`ENTRY_KIND_LABELS.receivable` is `應收款項`; if the label table says otherwise, assert `component.kindLabel('receivable')` instead of the literal.)

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/accounting-ui.spec.ts --include src/app/components/accounting/entry-detail/entry-detail.spec.ts`
Expected: FAIL — `textOn is not a function` / `contrastRatio is not a function`; `.dgrid` has `類型`.

- [ ] **Step 3: Implement**

`accounting-ui.ts` — after `colorOf`:

```ts
// ---- header text contrast -------------------------------------------------------------------------------------

const DARK_TEXT = '#1d1c1a';
const LIGHT_TEXT = '#ffffff';

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of `#rgb` / `#rrggbb`; null for anything else (CSS variables, names). */
function luminance(color: string): number | null {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!match) {
    return null;
  }
  const hex = match[1].length === 3 ? [...match[1]].map(digit => digit + digit).join('') : match[1];
  const [r, g, b] = [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio of two hex colours; null when either is not a hex colour. */
export function contrastRatio(a: string, b: string): number | null {
  const la = luminance(a);
  const lb = luminance(b);
  if (la === null || lb === null) {
    return null;
  }
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Text colour for a category-coloured header: the higher-contrast of dark / white (ties → dark); null → theme. */
export function textOn(bg: string | null): string | null {
  if (!bg) {
    return null;
  }
  const dark = contrastRatio(bg, DARK_TEXT);
  const light = contrastRatio(bg, LIGHT_TEXT);
  if (dark === null || light === null) {
    return null;
  }
  return dark >= light ? DARK_TEXT : LIGHT_TEXT;
}
```

`entry-detail.ts` — import `textOn` from `../accounting-ui`; next to `readonly colorOf = colorOf;` add `readonly textOn = textOn;`; after `kindLabel(…)`:

```ts
  /** 事件類型 of the entry: the schedule that wrote it, else 單次. */
  eventKindLabel(detail: EntryDetail): '單次' | '週期' | '分期' {
    const kind = detail.schedule?.kind;
    return kind === 'recurring' ? '週期' : kind === 'installment' ? '分期' : '單次';
  }
```

`entry-detail.html` — `<div class="dhead" [style.background]="colorOf(d)">` → `<div class="dhead" [style.background]="colorOf(d)" [style.color]="textOn(d.category_color)">`; replace `<div><small>類型</small><b>{{ kindLabel(d.kind) }}</b></div>` with:

```html
      <div><small>交易類型</small><b>{{ kindLabel(d.kind) }}</b></div>
      <div><small>事件類型</small><b>{{ eventKindLabel(d) }}</b></div>
```

`entry-detail.scss:8` — `color: #151821;` → `color: var(--app-text);` (a null `textOn` leaves the theme token in charge).

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/accounting-ui.ts frontend/src/app/components/accounting/accounting-ui.spec.ts frontend/src/app/components/accounting/entry-detail/
git commit -m "feat(entry-detail): 交易類型 / 事件類型 cells and contrast-checked header text

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Minimum font size `.72rem` and a non-clipping summary row

**Files (every `font-size` ≤ `.7rem` in the accounting feature; all become `0.72rem`):**

| File | Lines (old value) |
|---|---|
| `account-entries/account-entries.scss` | 33 (.7), 37 (.7), 77 (.68), 78 (.68), 80 (.62) |
| `entry-row.scss` | 66 (.6), 93 (.68), 111 (.62) |
| `accounting-settings/accounting-settings.scss` | 3 (.7), 87 (.65) |
| `category-picker/category-picker.scss` | 21 (.68), 104 (.7), 135 (.7) |
| `entry-detail/entry-detail.scss` | 92 (.65), 176 (.68) |
| `split-lines/split-lines.scss` | 53 (.7) |
| `timeline/timeline.scss` | 66 (.7 — `.sum small`), 227 (.58 — `.bell-count`, not a calendar figure → plain `.72rem`) |
| `transfer-panel/transfer-panel.scss` | 19 (.68), 56 (.7), 95 (.7) |
| `accounts/accounts.scss` | 28 (.7), 50 (.62), 90 (.6), 97 (.62) |
| `reminders/reminders.scss` | 138, 320, 406, 483 (.68) |
| `reminders/schedule-sheet/schedule-sheet.scss` | 64 (.68) |
| `entry-form/entry-form.scss` | 104 (.7), 153 (.7) |

All paths are under `frontend/src/app/components/accounting/`. That is 32 declarations (the spec says 33; the base tree has 32 — Step 3 re-greps so nothing is missed). Line numbers are at base `b8d4d77`; Tasks 2–9 add lines to `timeline.scss`, `entry-form.scss`, `accounts.scss`, `account-entries.scss`, so match on the declaration.

- Modify additionally: `timeline/timeline.scss` `.sum` block (:48-78) for the wrap rule.
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:** none.

- [ ] **Step 1: Write the failing test** (top level of the timeline `describe`)

```ts
  it('never clips the three summary figures; they may wrap instead', () => {
    const { fixture, el } = render('phone');
    flushSummary('2026-10', { expense: '-1234567', income: '7654321', net: '6419754' });
    flushEntries([]);
    fixture.detectChanges();
    const figure = el.querySelector('.sum-expense')!;
    const style = getComputedStyle(figure);
    expect(style.whiteSpace).toBe('nowrap'); // the component stylesheet is applied
    expect(style.overflow).toBe('visible');
    expect(style.textOverflow).not.toBe('ellipsis');
    expect(getComputedStyle(el.querySelector('.sum small')!).fontSize).toBe('0.72rem');
  });
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/timeline/timeline.spec.ts`
Expected: FAIL — `expected 'hidden' to be 'visible'`.

- [ ] **Step 3: Implement**

1. Apply the 32 edits from the table (`font-size: 0.7rem;` / `0.68rem` / `0.65rem` / `0.62rem` / `0.6rem` / `0.58rem` → `font-size: 0.72rem;`), then verify none remain:
   `grep -rnE "font-size:\s*0?\.(([0-6][0-9]?)|70?)rem" frontend/src/app/components/accounting` → no output.
2. `timeline.scss` — replace the `.sum { … }` block with:

```scss
.sum {
  background: var(--app-surface-soft);
  border-radius: var(--radius-md);
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin: 4px 0 10px;
  padding: 10px 12px;

  > div {
    display: flex;
    flex: 1 1 6.5rem;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
  }

  small {
    color: var(--app-text-muted);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  /* Never clipped: a figure that does not fit moves the row onto two lines (spec review focus 5). */
  b {
    font-size: 0.95rem;
    font-variant-numeric: tabular-nums;
    overflow: visible;
    white-space: nowrap;
  }
}
```

and change `.sum-scope` and `.sum-error` (Tasks 2, 5) from `grid-column: 1 / -1;` to `flex: 1 1 100%;`.

- [ ] **Step 4: Run the whole suite** (font sizes touch many components)

Run: `cd frontend && npm test -- --watch=false`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/
git commit -m "style(accounting): .72rem minimum font size; summary figures wrap instead of clipping

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Verification and PR

**Files:** none changed (screenshots are attached to the PR, not committed).

- [ ] **Step 1: Full test run** — `cd frontend && npm test -- --watch=false` → all suites PASS. Save the summary lines for the PR.
- [ ] **Step 2: Build** — `cd frontend && npm run build` (or `npx ng build` if there is no repo-root `.env`; see Global Constraints) → exit 0, no budget errors (component styles stay under 20 kB).
- [ ] **Step 3: Leftover scan** — `grep -rn "紀錄" frontend/src/app/components/accounting` → nothing; `grep -rnE "font-size:\s*0?\.(([0-6][0-9]?)|70?)rem" frontend/src/app/components/accounting` → nothing; `git diff main --stat -- backend openspec .env` → empty.
- [ ] **Step 4: Demo screenshots (before / after) at 390×844, 760×820, 1280×800.**
  - *Before*: `http://127.0.0.1:18080/hub/accounting` (the demo proxy serves `/home/opc/workspace/home-hub-schedules/frontend/dist/inventory-ui/browser` against the fictional-data demo API).
  - *After*: if your checkout **is** `/home/opc/workspace/home-hub-schedules`, the Step 2 build already lands in that `dist/` folder — reload `http://127.0.0.1:18080/hub/accounting`. Otherwise run your own dev server against the demo API without touching `.env` or `proxy.conf.js`: write a scratch proxy file outside the repo, e.g. `/tmp/hh-demo-proxy.json` containing `{"/api/accounting": {"target": "http://127.0.0.1:8011", "pathRewrite": {"^/api/accounting": ""}, "changeOrigin": true}}`, then `cd frontend && npx ng serve --proxy-config /tmp/hh-demo-proxy.json --port 4310` and open `http://127.0.0.1:4310/hub/accounting`.
  - Capture with any headless browser you have, e.g. `npx playwright screenshot --viewport-size=390,844 http://127.0.0.1:4310/hub/accounting after-390.png` (repeat for `760,820` and `1280,800`), plus the entry form (`/hub/accounting/entry`, 事件類型 tile with 分期 selected) and `/hub/accounting/accounts`.
  - Check by eye at 390×844: the three summary figures are not clipped (Review Focus 3); filter a month to show `全月 · 未套篩選` and `符合條件 N 筆`.
  - If you cannot reach the demo API or run a browser, say so in the PR and attach the Vitest output from Step 1 instead.
- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/ux-refine-1-states-wording
gh pr create --base main --head feat/ux-refine-1-states-wording \
  --title "feat(accounting): UX refine 1 — load states, 事件類型 tile, wording" \
  --body-file /tmp/pr-ux-refine-1.md
```

PR body template (`/tmp/pr-ux-refine-1.md`):

```markdown
## Summary
Spec: docs/superpowers/specs/2026-10-05-ux-refinement-design.md §1, §2, §5 (PR-1 of 3).
- Timeline: `全月 · 未套篩選` label, `符合條件 N 筆` + 清除篩選, debounced search, exclusive skeleton / error+重試 / empty / rows for list and calendar day, independent summary and daily states.
- Passbook and accounts overview: same exclusive states; import lookup with its own request counter and 重試; new empty state.
- Entry form: 事件類型 tile (單次／週期／分期) replaces 進階; unsupported-fields line; editing hint.
- Wording 記錄 / 記帳設定 / 首期入帳日; 交易類型 + 事件類型 in entry detail; `textOn()` header contrast; `.72rem` minimum font size.

## Review focus
1. Stale list response ignored (timeline.spec `ignores a late page for the previous filters…`).
2. 分期 → 單次 restores tiles and clears schedule errors (entry-form-schedule.spec).
3. Summary row never clipped at 390px (timeline.spec + 390×844 screenshot).
4. Search debounce / Enter (timeline.spec).
5. Import retry vs. accounts reload (accounts.spec).

## Test plan
- [ ] `cd frontend && npm test -- --watch=false` — <paste summary>
- [ ] `cd frontend && npm run build`
- [ ] Screenshots before/after at 390×844, 760×820, 1280×800 (attached)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Self-review

- **Spec coverage:** §1.1 filter state/search/summary header/calendar/empty text/load states/summary states → Tasks 2, 3, 4, 5; §1.2 → Task 6; §1.3 (per-resource counters, tests a/b, retry, empty state, skeleton) → Task 7; §1.4 → Task 1; §2.1 placement/tablist/disabled tabs/schedule-tabs below/`showTabs=false`/regression/event-summary → Task 8; §2.2 → Task 9; §2.3 → Tasks 8 (disabled tabs) and 9 (hint); §2.4 → Task 8 (definition test); §2.5 → Task 8 (arrow + Enter tests); §5 wording → Tasks 4, 6, 10; 交易類型／事件類型 + `textOn` → Task 11; font sizes + 390px check → Task 12. Gaps fixed while drafting: daily-summary error state (spec "follows the same rule"), passbook retry when the account itself failed, `[selected]` on 全部 options so 清除篩選 resets the selects, calendar-day stale-row clearing.
- **Resolved ambiguities:** a same-filter refresh (entry write) keeps rows while a filter/month change shows the skeleton; `null` import without error shows nothing new (today's behaviour when accounts exist); the 分期 footer follows the spec literally (`…開始（N 期）`, `進行` dropped); `.bell-count` (timeline.scss:227) is not a calendar figure → plain `.72rem`; 32 font declarations exist, not 33.
