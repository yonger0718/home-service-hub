# UX Refine 3 — Account Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `<app-account-picker>` shows group, currency and balance wherever an account is chosen (entry form, 餘額調整, transfer legs, split lines, 還款帳戶), with recent accounts, full keyboard support and the overlay Esc/Tab contract (spec §3).

**Architecture:** A standalone, signals-based picker renders a trigger button plus, when open, either a bottom sheet (phone) or a `position: fixed` popover anchored to the trigger (sheet / panes, so the pane's `overflow` cannot clip it). Grouping, filtering and recent-account storage are pure functions in sibling files with their own specs; the picker's host `keydown` handler implements the overlay contract defined by PR-2 (`data-overlay`, Esc handled and closes only itself, `trapFocus` for Tab). Hosts replace their native `<select>`s and keep their existing state signals.

**Tech Stack:** Angular 21 standalone components with signals (`input`, `model`, `computed`, `viewChild`, `afterNextRender`), `@if`/`@for` control flow, `NgTemplateOutlet`, SCSS, Vitest via `@angular/build:unit-test` (jsdom), `HttpTestingController`, `RouterTestingHarness`.

**Spec:** `docs/superpowers/specs/2026-10-05-ux-refinement-design.md` (binding; §3, plus §4.3 for the discard prompt the Esc chain reaches). Fact sheet: `.superpowers/ux/facts-ux-refine.md` (base `main` @ b8d4d77).

**Branch:** `feat/ux-refine-3-account-picker`, from `main` **after PR-2 (`feat/ux-refine-2-sheet`) is merged** (PR-1 before it). This plan consumes PR-2's code unchanged:

```ts
// frontend/src/app/components/accounting/accounting-ui.ts (from PR-2)
export const OVERLAY_ATTR = 'data-overlay';
export function focusables(container: HTMLElement): HTMLElement[];
/** Tab / Shift+Tab wraps inside `container`; returns true (and preventDefaults) when it moved focus. */
export function trapFocus(container: HTMLElement, event: KeyboardEvent): boolean;
export function isHandledKey(event: KeyboardEvent): boolean; // pre-existing

// frontend/src/app/components/accounting/dirty-form.service.ts (from PR-2)
export interface DirtyAware { isDirty(): boolean; showDiscardPrompt(): void }
export class DirtyFormRegistry { readonly promptOpen: Signal<boolean>; requestClose(run: () => void): void; confirmDiscard(): void; cancelDiscard(): void; /* … */ }
```

Overlay contract (PR-2): an overlay root carries `data-overlay`; its component's host `keydown` handler ignores `isHandledKey(event)`, closes only itself on `Escape` with `event.preventDefault()`, and calls `trapFocus(dialog, event)` on `Tab`. The layout's pane Tab wrap skips targets inside `[data-overlay]`. The entry form's `cancel()` goes through `DirtyFormRegistry.requestClose()` and shows `.discard-strip` (`放棄未儲存的內容？`, `.discard-stay` 留下 / `.discard-leave` 放棄) for a dirty draft. If any of these names differ on `main`, stop and reconcile with PR-2 before Task 3.

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
- Build command: `cd frontend && npm run build` (needs the repo-root `.env` for `set-env.js`; without one use `cd frontend && npx ng build`; never create or edit `.env`).
- Line anchors are for base `b8d4d77` (PR-1/PR-2 shift them); match on the quoted code.
- The app runs zoneless: `fixture.detectChanges()` runs `ApplicationRef.tick()`, which also runs `afterNextRender` callbacks (focus moves are visible right after it).
- Hosts pass `groups = null` (no extra `GET /account-groups`): groups are then derived from `group_name` in order of first appearance, which is the API's group order.

## Review Focus

1. **Popover clipping (spec review item 3)** — input: 760×820 sheet layout, entry form scrolled so the 帳戶 tile sits near the bottom of the pane, picker opened. Expected: the popover is `position: fixed`, flips above the trigger when there is less room below, its `max-height` fits the viewport, and nothing is cut by the pane's `overflow`. Pinned in **Task 5** (placement test) and the 760×820 screenshot in **Task 11**.
2. **Nested Esc order** — input: entry form with a dirty draft, 新增拆帳行 modal open, picker opened from the modal; Esc ×3. Expected: Esc 1 closes only the picker, Esc 2 closes only the modal (the pending split line is discarded, the main draft intact), Esc 3 shows `放棄未儲存的內容？`; only 放棄 leaves. Pinned in **Task 10**.
3. **Enter inside the picker** — input: ⏎ on a highlighted row, in the entry form and inside the 新增拆帳行 modal. Expected: it selects that account, closes the picker, returns focus to the trigger, and neither saves the entry (no POST) nor adds the split line. Pinned in **Task 4** (handled-key assertion) and **Task 10** (no POST, line count unchanged).
4. **Broken `localStorage`** — input: `getItem` / `setItem` throw (private mode). Expected: the picker opens and selects normally without a 最近使用 row; saving an entry still succeeds. Pinned in **Task 1** and **Task 3**.
5. **Transfer legs** — input: 轉出 = A; open 轉入. Expected: A is not offered; after ⇄ swap each picker excludes the new other leg; the separate balance line under each leg is gone (the trigger shows it). Pinned in **Task 7**.

---

### Task 1: Recent accounts storage

**Files:**
- Create: `frontend/src/app/components/accounting/account-picker/recent-accounts.ts`
- Test: `frontend/src/app/components/accounting/account-picker/recent-accounts.spec.ts` (new)

**Interfaces:**
- Produces: `RECENT_ACCOUNTS_KEY = 'hh.accounting.recentAccounts'`, `RECENT_ACCOUNTS_MAX = 8`, `readRecentAccounts(): number[]`, `rememberRecentAccounts(ids: readonly (number | null | undefined)[]): void`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/components/accounting/account-picker/recent-accounts.spec.ts
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RECENT_ACCOUNTS_KEY, readRecentAccounts, rememberRecentAccounts } from './recent-accounts';

describe('recent accounts', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(RECENT_ACCOUNTS_KEY);
  });

  it('keeps the most recent first, without duplicates, at most 8', () => {
    rememberRecentAccounts([3]);
    rememberRecentAccounts([5, 7]);
    rememberRecentAccounts([3, null, undefined]);
    expect(readRecentAccounts()).toEqual([3, 5, 7]);
    rememberRecentAccounts([10, 11, 12, 13, 14, 15]);
    expect(readRecentAccounts()).toEqual([10, 11, 12, 13, 14, 15, 3, 5]);
  });

  it('reads garbage as empty', () => {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, '{"not": "a list"}');
    expect(readRecentAccounts()).toEqual([]);
    localStorage.setItem(RECENT_ACCOUNTS_KEY, '[1, "x", 2.5, 4]');
    expect(readRecentAccounts()).toEqual([1, 4]);
    localStorage.setItem(RECENT_ACCOUNTS_KEY, 'nope');
    expect(readRecentAccounts()).toEqual([]);
  });

  it('tolerates blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readRecentAccounts()).toEqual([]);
    expect(() => rememberRecentAccounts([1])).not.toThrow();
  });
});
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-picker/recent-accounts.spec.ts`
Expected: FAIL — `Could not resolve "./recent-accounts"`.

- [ ] **Step 3: Implement**

```ts
// frontend/src/app/components/accounting/account-picker/recent-accounts.ts
/** This device's recently used accounts (ids, most recent first), written by the hosts after a successful save. */
export const RECENT_ACCOUNTS_KEY = 'hh.accounting.recentAccounts';
export const RECENT_ACCOUNTS_MAX = 8;

export function readRecentAccounts(): number[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_ACCOUNTS_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((id): id is number => Number.isInteger(id)) : [];
  } catch {
    // Blocked storage or garbage: no recent row.
    return [];
  }
}

export function rememberRecentAccounts(ids: readonly (number | null | undefined)[]): void {
  const fresh = ids.filter((id): id is number => typeof id === 'number' && Number.isInteger(id));
  if (fresh.length === 0) {
    return;
  }
  const next = [...new Set([...fresh, ...readRecentAccounts()])].slice(0, RECENT_ACCOUNTS_MAX);
  try {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify(next));
  } catch {
    // Storage blocked (private mode): the list simply is not remembered.
  }
}
```

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-picker/recent-accounts.ts frontend/src/app/components/accounting/account-picker/recent-accounts.spec.ts
git commit -m "feat(account-picker): recent accounts in localStorage, tolerant of blocked storage

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Grouping and filtering

**Files:**
- Create: `frontend/src/app/components/accounting/account-picker/picker-groups.ts`
- Test: `frontend/src/app/components/accounting/account-picker/picker-groups.spec.ts` (new)

**Interfaces:**
- Produces:

```ts
export const UNGROUPED = '未分組';
export const ARCHIVED_GROUP = '已封存';
export interface PickerGroup { key: string; name: string; accounts: LedgerAccount[] }
export interface PickerFilter { allowArchived: boolean; exclude: readonly number[]; query: string }
export function visibleAccounts(accounts: LedgerAccount[], filter: Omit<PickerFilter, 'query'>): LedgerAccount[];
export function pickerGroups(accounts: LedgerAccount[], groups: AccountGroup[] | null, filter: PickerFilter): PickerGroup[];
```

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/app/components/accounting/account-picker/picker-groups.spec.ts
import { describe, expect, it } from 'vitest';

import { AccountGroup } from '../../../models/accounting.model';
import { makeAccount } from '../testing/fixtures';
import { pickerGroups, visibleAccounts } from './picker-groups';

const ACCOUNTS = [
  makeAccount({ id: 1, name: '錢包', group_id: 1, group_name: '現金' }),
  makeAccount({ id: 2, name: '玉山 UNI', group_id: 3, group_name: '信用卡', is_credit: true }),
  makeAccount({ id: 3, name: 'Line Bank', group_id: 2, group_name: '銀行' }),
  makeAccount({ id: 4, name: '舊卡', group_id: 3, group_name: '信用卡', is_archived: true }),
  makeAccount({ id: 5, name: '悠遊卡', group_id: null, group_name: null }),
];
const GROUPS: AccountGroup[] = [
  { id: 3, name: '信用卡', sort_order: 0, moze_id: null },
  { id: 1, name: '現金', sort_order: 1, moze_id: null },
  { id: 2, name: '銀行', sort_order: 2, moze_id: null },
];
const names = (groups: ReturnType<typeof pickerGroups>) => groups.map(group => [group.name, group.accounts.map(a => a.name)]);

describe('pickerGroups', () => {
  it('orders groups by sort_order, then 未分組, then 已封存 when allowed', () => {
    expect(names(pickerGroups(ACCOUNTS, GROUPS, { allowArchived: true, exclude: [], query: '' }))).toEqual([
      ['信用卡', ['玉山 UNI']],
      ['現金', ['錢包']],
      ['銀行', ['Line Bank']],
      ['未分組', ['悠遊卡']],
      ['已封存', ['舊卡']],
    ]);
  });

  it('derives groups from group_name order without AccountGroup data and hides archived by default', () => {
    expect(names(pickerGroups(ACCOUNTS, null, { allowArchived: false, exclude: [], query: '' }))).toEqual([
      ['現金', ['錢包']],
      ['信用卡', ['玉山 UNI']],
      ['銀行', ['Line Bank']],
      ['未分組', ['悠遊卡']],
    ]);
  });

  it('hides excluded ids and filters by name or group name, ignoring case', () => {
    const filter = { allowArchived: false, exclude: [1], query: '' };
    expect(visibleAccounts(ACCOUNTS, filter).map(a => a.id)).toEqual([2, 3, 5]);
    expect(names(pickerGroups(ACCOUNTS, null, { ...filter, query: 'line' }))).toEqual([['銀行', ['Line Bank']]]);
    expect(names(pickerGroups(ACCOUNTS, null, { ...filter, query: '信用' }))).toEqual([['信用卡', ['玉山 UNI']]]);
    expect(pickerGroups(ACCOUNTS, null, { ...filter, query: 'zzz' })).toEqual([]);
  });
});
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-picker/picker-groups.spec.ts`
Expected: FAIL — `Could not resolve "./picker-groups"`.

- [ ] **Step 3: Implement**

```ts
// frontend/src/app/components/accounting/account-picker/picker-groups.ts
import { AccountGroup, LedgerAccount } from '../../../models/accounting.model';

export const UNGROUPED = '未分組';
export const ARCHIVED_GROUP = '已封存';

export interface PickerGroup {
  key: string;
  name: string;
  accounts: LedgerAccount[];
}

export interface PickerFilter {
  allowArchived: boolean;
  exclude: readonly number[];
  query: string;
}

/** Accounts the picker may offer at all (before the search box narrows them). */
export function visibleAccounts(accounts: LedgerAccount[], filter: Omit<PickerFilter, 'query'>): LedgerAccount[] {
  const excluded = new Set(filter.exclude);
  return accounts.filter(account => !excluded.has(account.id) && (filter.allowArchived || !account.is_archived));
}

/**
 * Picker sections (spec §3.1): `groups` in `sort_order` (or, without them, `group_name` in order of first
 * appearance), then 未分組, then 已封存 (only when allowed). Search matches the account or group name, any case.
 */
export function pickerGroups(accounts: LedgerAccount[], groups: AccountGroup[] | null, filter: PickerFilter): PickerGroup[] {
  const query = filter.query.trim().toLowerCase();
  const matches = (account: LedgerAccount) =>
    !query ||
    account.name.toLowerCase().includes(query) ||
    (account.group_name ?? UNGROUPED).toLowerCase().includes(query);
  const shown = visibleAccounts(accounts, filter).filter(matches);
  const open = shown.filter(account => !account.is_archived);
  const archived = shown.filter(account => account.is_archived);

  const known = new Set((groups ?? []).map(group => group.id));
  const keyOf = (account: LedgerAccount): string => {
    if (groups) {
      return account.group_id !== null && known.has(account.group_id) ? `g${account.group_id}` : 'none';
    }
    return account.group_name ? `n${account.group_name}` : 'none';
  };
  const order: { key: string; name: string }[] = groups
    ? [...groups].sort((a, b) => a.sort_order - b.sort_order).map(group => ({ key: `g${group.id}`, name: group.name }))
    : [];
  if (!groups) {
    for (const account of open) {
      const key = keyOf(account);
      if (key !== 'none' && !order.some(entry => entry.key === key)) {
        order.push({ key, name: account.group_name! });
      }
    }
  }
  order.push({ key: 'none', name: UNGROUPED });

  const sections = order
    .map(entry => ({ ...entry, accounts: open.filter(account => keyOf(account) === entry.key) }))
    .filter(section => section.accounts.length > 0);
  if (archived.length > 0) {
    sections.push({ key: 'archived', name: ARCHIVED_GROUP, accounts: archived });
  }
  return sections;
}
```

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-picker/picker-groups.ts frontend/src/app/components/accounting/account-picker/picker-groups.spec.ts
git commit -m "feat(account-picker): group, archive and search rules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `AccountPickerComponent` — trigger, panel, search, recent, selection

**Files:**
- Create: `frontend/src/app/components/accounting/account-picker/account-picker.ts`
- Create: `frontend/src/app/components/accounting/account-picker/account-picker.html`
- Create: `frontend/src/app/components/accounting/account-picker/account-picker.scss`
- Test: `frontend/src/app/components/accounting/account-picker/account-picker.spec.ts` (new)

**Interfaces:**
- Consumes: Tasks 1–2; `formatMoney` (`../format`); `LayoutModeService`; PR-2's `isHandledKey`, `trapFocus`.
- Produces (`selector: 'app-account-picker'`):

```ts
readonly accounts = input<LedgerAccount[]>([]);
readonly groups = input<AccountGroup[] | null>(null);
readonly value = model<number | null>(null);          // output: valueChange: number | null
readonly label = input.required<string>();             // accessible name (帳戶 / 轉出帳戶 / 轉入帳戶 / 還款帳戶)
readonly allowArchived = input(false);
readonly exclude = input<readonly number[]>([]);
readonly compact = input(false);
readonly disabled = input(false);                      // added: locked transfer legs, disabled schedule tabs
readonly open: WritableSignal<boolean>;
toggle(): void; choose(id: number): void; close(restoreFocus?: boolean): void;
onHostKeydown(event: KeyboardEvent): void;             // completed in Task 4
balanceText(account: LedgerAccount): string;
export const SEARCH_THRESHOLD = 8;
```

DOM contract used by host specs: trigger `button.acct-trigger[aria-haspopup=dialog][aria-expanded][data-value]`; panel `.acct-panel` (`role=dialog`, `tabindex=-1`), inside a `.overlay[data-overlay]` on phone or itself `[data-overlay]` as `.acct-popover`; search `input.acct-search`; recent chips `button.acct-recent-chip[data-account-id]`; groups `.acct-group` with `.acct-group-name`; rows `.acct-option[role=option][data-account-id][aria-selected]` inside `[role=listbox]`.

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/app/components/accounting/account-picker/account-picker.spec.ts
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LedgerAccount } from '../../../models/accounting.model';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { makeAccount } from '../testing/fixtures';
import { AccountPickerComponent } from './account-picker';
import { RECENT_ACCOUNTS_KEY } from './recent-accounts';

const WALLET = makeAccount({ id: 1, name: '錢包', icon: '👛', balance: '3070', group_name: '現金' });
const CARD = makeAccount({ id: 2, name: '玉山 UNI', icon: '💳', balance: '-27218', is_credit: true, available_credit: '251678', group_name: '信用卡' });
const BANK = makeAccount({ id: 3, name: 'Line Bank', icon: '🏦', balance: '100000', group_name: '銀行' });
const YEN = makeAccount({ id: 4, name: '日幣現金', currency: 'JPY', balance: '53635', group_name: '現金' });
const OLD = makeAccount({ id: 9, name: '舊卡', is_archived: true, group_name: '信用卡' });

function many(count: number): LedgerAccount[] {
  return Array.from({ length: count }, (_, index) => makeAccount({ id: 100 + index, name: `帳戶${index}`, group_name: '銀行' }));
}

interface Setup {
  accounts?: LedgerAccount[];
  value?: number | null;
  mode?: LayoutMode;
  allowArchived?: boolean;
  exclude?: number[];
  compact?: boolean;
}

function render(setup: Setup = {}): { fixture: ComponentFixture<AccountPickerComponent>; el: HTMLElement; picker: AccountPickerComponent } {
  TestBed.configureTestingModule({ imports: [AccountPickerComponent] });
  TestBed.inject(LayoutModeService).set(setup.mode ?? 'panes');
  const fixture = TestBed.createComponent(AccountPickerComponent);
  const ref = fixture.componentRef;
  ref.setInput('accounts', setup.accounts ?? [WALLET, CARD, BANK, YEN, OLD]);
  ref.setInput('label', '帳戶');
  ref.setInput('value', setup.value ?? null);
  ref.setInput('allowArchived', setup.allowArchived ?? false);
  ref.setInput('exclude', setup.exclude ?? []);
  ref.setInput('compact', setup.compact ?? false);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement, picker: fixture.componentInstance };
}

function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function openPanel(fixture: ComponentFixture<AccountPickerComponent>): HTMLElement {
  (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('.acct-trigger')!.click();
  fixture.detectChanges();
  return (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.acct-panel')!;
}

describe('AccountPickerComponent', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(RECENT_ACCOUNTS_KEY);
  });

  it('shows icon, name and balance on the trigger, 可用 for a card, and 選擇帳戶 when empty', () => {
    const { fixture, el } = render({ value: 1 });
    const trigger = el.querySelector('.acct-trigger')!;
    expect(text(trigger)).toContain('👛 錢包 · $3,070');
    expect(trigger.getAttribute('aria-haspopup')).toBe('dialog');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.getAttribute('aria-label')).toBe('帳戶：錢包');
    expect(trigger.getAttribute('data-value')).toBe('1');

    fixture.componentRef.setInput('value', 2);
    fixture.detectChanges();
    expect(text(trigger)).toContain('玉山 UNI · 可用 $251,678');

    fixture.componentRef.setInput('value', null);
    fixture.detectChanges();
    expect(text(trigger)).toContain('選擇帳戶');
  });

  it('shows the name only when compact', () => {
    const { el } = render({ value: 1, compact: true });
    expect(text(el.querySelector('.acct-trigger'))).not.toContain('$3,070');
  });

  it('lists groups with currency badges and balances, marks the selected row, archived only when allowed', () => {
    const { fixture, el } = render({ value: 4 });
    const panel = openPanel(fixture);
    expect(el.querySelector('.acct-trigger')!.getAttribute('aria-expanded')).toBe('true');
    expect(Array.from(panel.querySelectorAll('.acct-group-name')).map(text)).toEqual(['現金', '信用卡', '銀行']);
    const yen = panel.querySelector('.acct-option[data-account-id="4"]')!;
    expect(yen.getAttribute('aria-selected')).toBe('true');
    expect(text(yen.querySelector('.acct-currency'))).toBe('JPY');
    expect(text(yen.querySelector('.acct-balance'))).toBe('¥53,635');
    expect(yen.querySelector('.acct-check')).not.toBeNull();
    expect(panel.querySelector('[role="listbox"]')!.getAttribute('aria-label')).toBe('帳戶');
    expect(panel.querySelector('.acct-option[data-account-id="9"]')).toBeNull();
  });

  it('puts archived accounts in a trailing 已封存 group when allowed', () => {
    const { fixture } = render({ allowArchived: true });
    const panel = openPanel(fixture);
    const groups = Array.from(panel.querySelectorAll('.acct-group-name')).map(text);
    expect(groups[groups.length - 1]).toBe('已封存');
  });

  it('shows the search box from 8 visible accounts and narrows by name or group', () => {
    const seven = render({ accounts: many(7) });
    expect(openPanel(seven.fixture).querySelector('.acct-search')).toBeNull();
    TestBed.resetTestingModule();

    const { fixture } = render({ accounts: [...many(7), BANK] });
    const panel = openPanel(fixture);
    const search = panel.querySelector<HTMLInputElement>('.acct-search')!;
    expect(search.getAttribute('placeholder')).toBe('搜尋帳戶');
    search.value = 'line';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(Array.from(panel.querySelectorAll('.acct-option')).map(row => row.getAttribute('data-account-id'))).toEqual(['3']);
  });

  it('hides excluded accounts', () => {
    const { fixture } = render({ exclude: [1, 2] });
    const panel = openPanel(fixture);
    expect(Array.from(panel.querySelectorAll('.acct-option')).map(row => row.getAttribute('data-account-id'))).toEqual(['4', '3']);
  });

  it('emits once on selection, closes and returns focus to the trigger; the current value emits nothing', () => {
    const { fixture, el, picker } = render({ value: 1 });
    const emitted = vi.fn();
    picker.value.subscribe(emitted);
    openPanel(fixture).querySelector<HTMLElement>('.acct-option[data-account-id="3"]')!.click();
    fixture.detectChanges();
    expect(emitted).toHaveBeenCalledTimes(1);
    expect(emitted).toHaveBeenCalledWith(3);
    expect(el.querySelector('.acct-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.acct-trigger'));

    openPanel(fixture).querySelector<HTMLElement>('.acct-option[data-account-id="3"]')!.click();
    fixture.detectChanges();
    expect(emitted).toHaveBeenCalledTimes(1);
  });

  it('offers up to four 最近使用 chips, hidden while searching', () => {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify([3, 999, 2, 1, 4, 100]));
    const { fixture, picker } = render({ accounts: [...many(7), WALLET, CARD, BANK, YEN] });
    const panel = openPanel(fixture);
    const chips = Array.from(panel.querySelectorAll('.acct-recent-chip'));
    expect(chips.map(chip => chip.getAttribute('data-account-id'))).toEqual(['3', '2', '1', '4']);
    (chips[1] as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(picker.value()).toBe(2);

    const again = openPanel(fixture);
    const search = again.querySelector<HTMLInputElement>('.acct-search')!;
    search.value = '錢';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(again.querySelector('.acct-recent')).toBeNull();
  });

  it('works without a 最近使用 row when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { fixture, picker } = render();
    const panel = openPanel(fixture);
    expect(panel.querySelector('.acct-recent')).toBeNull();
    panel.querySelector<HTMLElement>('.acct-option[data-account-id="1"]')!.click();
    expect(picker.value()).toBe(1);
  });

  it('uses a bottom sheet on a phone and a popover elsewhere, both marked as overlays', () => {
    const phone = render({ mode: 'phone' });
    openPanel(phone.fixture);
    expect(phone.el.querySelector('.overlay[data-overlay] .sheet.acct-panel')).not.toBeNull();
    phone.el.querySelector<HTMLElement>('.overlay')!.click();
    phone.fixture.detectChanges();
    expect(phone.el.querySelector('.acct-panel')).toBeNull();
    TestBed.resetTestingModule();

    const desk = render({ mode: 'sheet' });
    openPanel(desk.fixture);
    expect(desk.el.querySelector('.acct-popover.acct-panel[data-overlay]')).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run and see it fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-picker/account-picker.spec.ts`
Expected: FAIL — `Could not resolve "./account-picker"`.

- [ ] **Step 3: Implement**

```ts
// frontend/src/app/components/accounting/account-picker/account-picker.ts
import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  input,
  model,
  signal,
  viewChild,
} from '@angular/core';

import { AccountGroup, LedgerAccount } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { isHandledKey, trapFocus } from '../accounting-ui';
import { formatMoney } from '../format';
import { pickerGroups, visibleAccounts } from './picker-groups';
import { readRecentAccounts } from './recent-accounts';

/** The search box appears from this many offerable accounts (spec §3.1). */
export const SEARCH_THRESHOLD = 8;
const RECENT_CHIPS = 4;
const POPOVER_MIN_WIDTH = 260;
const POPOVER_GAP = 4;

/**
 * 帳戶 chooser for every form (spec §3): trigger `[icon] name · balance`; a bottom sheet on phones, a fixed popover
 * under (or above) the trigger elsewhere. Owns its focus and implements the overlay keyboard contract.
 */
@Component({
  selector: 'app-account-picker',
  standalone: true,
  imports: [NgTemplateOutlet],
  templateUrl: './account-picker.html',
  styleUrls: ['../sheet.scss', './account-picker.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(keydown)': 'onHostKeydown($event)',
    '(document:pointerdown)': 'onDocumentPointerDown($event)',
    '[class.compact]': 'compact()',
  },
})
export class AccountPickerComponent {
  private readonly layoutMode = inject(LayoutModeService);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly accounts = input<LedgerAccount[]>([]);
  readonly groups = input<AccountGroup[] | null>(null);
  readonly value = model<number | null>(null);
  readonly label = input.required<string>();
  readonly allowArchived = input(false);
  readonly exclude = input<readonly number[]>([]);
  readonly compact = input(false);
  readonly disabled = input(false);

  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  readonly open = signal(false);
  readonly query = signal('');
  readonly activeId = signal<number | null>(null);
  private readonly recent = signal<number[]>([]);
  readonly popoverStyle = signal<Record<string, string>>({});

  readonly phone = computed(() => this.layoutMode.mode() === 'phone');
  readonly selected = computed(() => this.accounts().find(account => account.id === this.value()) ?? null);
  private readonly offerable = computed(() =>
    visibleAccounts(this.accounts(), { allowArchived: this.allowArchived(), exclude: this.exclude() }),
  );
  readonly showSearch = computed(() => this.offerable().length >= SEARCH_THRESHOLD);
  readonly groupList = computed(() =>
    pickerGroups(this.accounts(), this.groups(), {
      allowArchived: this.allowArchived(),
      exclude: this.exclude(),
      query: this.query(),
    }),
  );
  readonly options = computed(() => this.groupList().flatMap(group => group.accounts));
  readonly recentAccounts = computed(() => {
    if (this.query().trim()) {
      return [];
    }
    const byId = new Map(this.offerable().filter(account => !account.is_archived).map(account => [account.id, account]));
    return this.recent()
      .map(id => byId.get(id))
      .filter((account): account is LedgerAccount => !!account)
      .slice(0, RECENT_CHIPS);
  });
  readonly triggerLabel = computed(() => {
    const account = this.selected();
    return account ? `${this.label()}：${account.name}` : `${this.label()}：選擇帳戶`;
  });

  balanceText(account: LedgerAccount): string {
    if (account.is_credit && account.available_credit !== null) {
      return `可用 ${formatMoney(account.available_credit, account.currency)}`;
    }
    return formatMoney(account.balance, account.currency);
  }

  toggle(): void {
    if (this.open()) {
      this.close();
    } else {
      this.openPanel();
    }
  }

  private openPanel(): void {
    if (this.disabled()) {
      return;
    }
    this.query.set('');
    this.recent.set(readRecentAccounts());
    this.activeId.set(this.firstActive());
    if (!this.phone()) {
      this.popoverStyle.set(this.placePopover());
    }
    this.open.set(true);
    afterNextRender(() => this.focusInitial(), { injector: this.injector });
  }

  /** Esc, selection and outside clicks close; focus returns to the trigger unless the click went elsewhere. */
  close(restoreFocus = true): void {
    if (!this.open()) {
      return;
    }
    this.open.set(false);
    if (restoreFocus) {
      this.trigger().nativeElement.focus();
    }
  }

  choose(id: number): void {
    this.close();
    if (id !== this.value()) {
      this.value.set(id);
    }
  }

  onSearch(text: string): void {
    this.query.set(text);
    this.activeId.set(this.options()[0]?.id ?? null);
  }

  onDocumentPointerDown(event: Event): void {
    if (this.open() && !this.phone() && !this.host.nativeElement.contains(event.target as Node | null)) {
      this.close(false);
    }
  }

  /** Overlay keyboard contract; completed in Task 4. */
  onHostKeydown(event: KeyboardEvent): void {
    if (!this.open() || isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.close();
    }
  }

  private firstActive(): number | null {
    const value = this.value();
    const options = this.options();
    return options.some(account => account.id === value) ? value : (options[0]?.id ?? null);
  }

  private optionElement(id: number | null): HTMLElement | null {
    return id === null ? null : (this.panel()?.nativeElement.querySelector<HTMLElement>(`.acct-option[data-account-id="${id}"]`) ?? null);
  }

  /** Desktop: the search box when present, else the selected (or first) row; phone: never the search box. */
  private focusInitial(): void {
    const panel = this.panel()?.nativeElement;
    if (!panel) {
      return;
    }
    const search = this.phone() ? null : panel.querySelector<HTMLElement>('.acct-search');
    (search ?? this.optionElement(this.activeId()) ?? panel).focus();
  }

  /** Fixed position from the trigger's rect, so a scrolling pane's `overflow` cannot clip it; flips above if needed. */
  private placePopover(): Record<string, string> {
    const rect = this.trigger().nativeElement.getBoundingClientRect();
    const view = this.document.defaultView;
    const height = view?.innerHeight ?? 800;
    const width = view?.innerWidth ?? 1024;
    const below = height - rect.bottom - 8;
    const above = rect.top - 8;
    const popoverWidth = Math.max(rect.width, POPOVER_MIN_WIDTH);
    const style: Record<string, string> = {
      position: 'fixed',
      left: `${Math.round(Math.max(8, Math.min(rect.left, width - popoverWidth - 8)))}px`,
      width: `${Math.round(popoverWidth)}px`,
      'max-height': `${Math.round(Math.min(height * 0.6, Math.max(below, above)))}px`,
    };
    if (below >= 240 || below >= above) {
      style['top'] = `${Math.round(rect.bottom + POPOVER_GAP)}px`;
    } else {
      style['bottom'] = `${Math.round(height - rect.top + POPOVER_GAP)}px`;
    }
    return style;
  }
}
```

(`trapFocus` is imported now and used in Task 4; if the linter flags it as unused in this commit, add it in Task 4 instead.)

```html
<!-- frontend/src/app/components/accounting/account-picker/account-picker.html -->
<button
  #trigger
  type="button"
  class="acct-trigger"
  [class.empty]="!selected()"
  aria-haspopup="dialog"
  [attr.aria-expanded]="open()"
  [attr.aria-label]="triggerLabel()"
  [attr.data-value]="value()"
  [disabled]="disabled()"
  (click)="toggle()"
>
  @if (selected(); as account) {
    <span class="acct-ico" aria-hidden="true">{{ account.icon ?? '🏦' }}</span>
    <span class="acct-name">{{ account.name }}</span>
    @if (!compact()) {
      <span class="acct-trigger-balance">· {{ balanceText(account) }}</span>
    }
  } @else {
    <span class="acct-name">選擇帳戶</span>
  }
  <span class="acct-caret" aria-hidden="true">▾</span>
</button>

@if (open()) {
  @if (phone()) {
    <div class="overlay" data-overlay (click)="close()">
      <div #panel class="sheet acct-panel" role="dialog" aria-modal="true" [attr.aria-label]="label()" tabindex="-1" (click)="$event.stopPropagation()">
        <div class="grab"></div>
        <h3>{{ label() }}</h3>
        <ng-container *ngTemplateOutlet="body" />
      </div>
    </div>
  } @else {
    <div #panel class="acct-popover acct-panel" data-overlay role="dialog" [attr.aria-label]="label()" tabindex="-1" [style]="popoverStyle()">
      <ng-container *ngTemplateOutlet="body" />
    </div>
  }
}

<ng-template #body>
  @if (showSearch()) {
    <input class="acct-search" type="search" placeholder="搜尋帳戶" aria-label="搜尋帳戶" [value]="query()" (input)="onSearch($any($event.target).value)" />
  }
  @if (recentAccounts().length > 0) {
    <div class="acct-recent" role="group" aria-label="最近使用">
      <span class="acct-recent-label">最近使用</span>
      @for (account of recentAccounts(); track account.id) {
        <button type="button" class="acct-recent-chip" [attr.data-account-id]="account.id" (click)="choose(account.id)">
          {{ account.icon ?? '🏦' }} {{ account.name }}
        </button>
      }
    </div>
  }
  <div class="acct-list" role="listbox" [attr.aria-label]="label()">
    @for (group of groupList(); track group.key) {
      <div class="acct-group" role="group" [attr.aria-label]="group.name">
        <div class="acct-group-name" aria-hidden="true">{{ group.name }}</div>
        @for (account of group.accounts; track account.id) {
          <div
            class="acct-option"
            role="option"
            [class.active]="account.id === activeId()"
            [attr.data-account-id]="account.id"
            [attr.aria-selected]="account.id === value()"
            [attr.tabindex]="account.id === activeId() ? 0 : -1"
            (click)="choose(account.id)"
          >
            <span class="acct-ico" aria-hidden="true">{{ account.icon ?? '🏦' }}</span>
            <span class="acct-name">{{ account.name }}</span>
            <span class="acct-currency">{{ account.currency }}</span>
            <span class="acct-balance" [class.neg]="+account.balance < 0">{{ balanceText(account) }}</span>
            @if (account.id === value()) {
              <span class="acct-check" aria-hidden="true">✓</span>
            }
          </div>
        }
      </div>
    } @empty {
      <p class="acct-empty">沒有符合的帳戶</p>
    }
  </div>
</ng-template>
```

Note on the balance format: `balanceText` for a non-credit account in the row shows `formatMoney(balance, currency)`, so the JPY row reads `¥53,635`; the spec test above asserts exactly that.

```scss
// frontend/src/app/components/accounting/account-picker/account-picker.scss
:host {
  display: block;
  min-width: 0;
  position: relative;
}

.acct-trigger {
  align-items: center;
  background: none;
  border: 0;
  color: var(--app-text);
  cursor: pointer;
  display: flex;
  font: inherit;
  gap: 4px;
  justify-content: center;
  max-width: 100%;
  min-height: 30px;
  min-width: 0;
  padding: 2px 4px;
  width: 100%;

  &.empty .acct-name {
    color: var(--app-text-muted);
  }

  &:disabled {
    cursor: default;
    opacity: 0.6;
  }
}

.acct-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acct-trigger-balance {
  color: var(--app-text-muted);
  flex: none;
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}

.acct-caret {
  color: var(--app-text-muted);
  flex: none;
  font-size: 0.72rem;
}

.acct-popover {
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-md);
  box-shadow: var(--app-raised-shadow);
  overflow-y: auto;
  padding: 6px;
  z-index: 1200;
}

.acct-search {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-sm);
  color: var(--app-text);
  font: inherit;
  margin-bottom: 6px;
  padding: 6px 8px;
  width: 100%;
}

.acct-recent {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.acct-recent-label,
.acct-group-name {
  color: var(--app-text-muted);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.acct-recent-chip {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.8rem;
  padding: 3px 9px;
}

.acct-group-name {
  padding: 8px 6px 2px;
}

.acct-option {
  align-items: center;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: grid;
  gap: 6px;
  grid-template-columns: auto minmax(0, 1fr) auto auto auto;
  min-height: 40px;
  padding: 4px 6px;

  &.active,
  &:hover,
  &:focus {
    background: var(--app-surface-soft);
    outline: none;
  }

  &[aria-selected='true'] {
    font-weight: 700;
  }
}

.acct-currency {
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text-muted);
  font-size: 0.72rem;
  padding: 0 6px;
}

.acct-balance {
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
  text-align: right;

  &.neg {
    color: var(--tone-neg, var(--c-red));
  }
}

.acct-check {
  color: var(--app-primary);
}

.acct-empty {
  color: var(--app-text-muted);
  margin: 12px 0;
  text-align: center;
}
```

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-picker/
git commit -m "feat(account-picker): grouped account chooser with balances, search and 最近使用

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Picker keyboard and focus (overlay contract)

**Files:**
- Modify: `frontend/src/app/components/accounting/account-picker/account-picker.ts` (`onHostKeydown`, new `move`)
- Test: `frontend/src/app/components/accounting/account-picker/account-picker.spec.ts`

**Interfaces:**
- Produces: final `onHostKeydown(event)`: Esc → close + `preventDefault` (focus to trigger); Tab → `trapFocus(panel, event)`; ↑/↓ → move the active row (focus follows), `preventDefault`; ⏎ on a row or the search box → choose the active row, `preventDefault`; ⏎ on a chip button → native click.

- [ ] **Step 1: Write the failing tests** (append to the picker spec)

```ts
  function key(target: Element, name: string, init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    return event;
  }

  it('focuses the search box on desktop, the selected row without one, and never the search box on a phone', () => {
    const desk = render({ accounts: [...many(7), BANK], value: 3 });
    openPanel(desk.fixture);
    expect(document.activeElement).toBe(desk.el.querySelector('.acct-search'));
    TestBed.resetTestingModule();

    const small = render({ value: 3 });
    openPanel(small.fixture);
    expect(document.activeElement).toBe(small.el.querySelector('.acct-option[data-account-id="3"]'));
    TestBed.resetTestingModule();

    const phone = render({ accounts: [...many(7), BANK], value: 3, mode: 'phone' });
    openPanel(phone.fixture);
    expect(document.activeElement).toBe(phone.el.querySelector('.acct-option[data-account-id="3"]'));
  });

  it('focuses the panel itself when nothing can be offered', () => {
    const { fixture, el } = render({ exclude: [1, 2, 3, 4] });
    const panel = openPanel(fixture);
    expect(text(panel.querySelector('.acct-empty'))).toBe('沒有符合的帳戶');
    expect(document.activeElement).toBe(el.querySelector('.acct-panel'));
  });

  it('moves with ↑ / ↓ and selects with ⏎ as handled keys', () => {
    const { fixture, el, picker } = render({ value: 1 });
    const panel = openPanel(fixture);
    const down = key(document.activeElement!, 'ArrowDown');
    fixture.detectChanges();
    expect(down.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(panel.querySelector('.acct-option[data-account-id="4"]'));
    key(document.activeElement!, 'ArrowUp');
    fixture.detectChanges();
    key(document.activeElement!, 'ArrowUp');
    fixture.detectChanges();
    expect(document.activeElement).toBe(panel.querySelector('.acct-option[data-account-id="3"]')); // wraps to the last row

    const enter = key(document.activeElement!, 'Enter');
    fixture.detectChanges();
    expect(enter.defaultPrevented).toBe(true);
    expect(picker.value()).toBe(3);
    expect(el.querySelector('.acct-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.acct-trigger'));
  });

  it('closes on Esc without a change, as a handled key, focus back on the trigger', () => {
    const { fixture, el, picker } = render({ value: 1 });
    openPanel(fixture);
    key(document.activeElement!, 'ArrowDown');
    fixture.detectChanges();
    const escape = key(document.activeElement!, 'Escape');
    fixture.detectChanges();
    expect(escape.defaultPrevented).toBe(true);
    expect(picker.value()).toBe(1);
    expect(el.querySelector('.acct-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.acct-trigger'));
  });

  it('keeps Tab inside the panel', () => {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify([2]));
    const { fixture, el } = render({ accounts: [...many(7), WALLET, CARD], value: 1 });
    const panel = openPanel(fixture);
    const row = panel.querySelector<HTMLElement>('.acct-option[data-account-id="1"]')!;
    row.focus();
    const tab = key(row, 'Tab');
    expect(tab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(el.querySelector('.acct-search'));
    const back = key(document.activeElement!, 'Tab', { shiftKey: true });
    expect(back.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(row);
  });
```

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-picker/account-picker.spec.ts`
Expected: FAIL — `down.defaultPrevented` false; Tab test `expected false to be true`.

- [ ] **Step 3: Implement** — replace `onHostKeydown` in `account-picker.ts` and add `move`:

```ts
  /**
   * Overlay keyboard contract (spec §3.1): every key handled here is marked handled, so the split-lines modal, the
   * entry form (⏎ save, Esc cancel) and the layout (Esc, shortcuts) leave it alone. Esc closes only the picker.
   */
  onHostKeydown(event: KeyboardEvent): void {
    if (!this.open() || isHandledKey(event)) {
      return;
    }
    const panel = this.panel()?.nativeElement;
    if (!panel) {
      return;
    }
    const target = event.target as HTMLElement | null;
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        this.close();
        return;
      case 'Tab':
        trapFocus(panel, event);
        return;
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault();
        this.move(event.key === 'ArrowDown' ? 1 : -1);
        return;
      case 'Enter': {
        if (target?.tagName === 'BUTTON') {
          return; // a 最近使用 chip: its native click chooses (no form handler saves on a BUTTON)
        }
        event.preventDefault();
        const id = this.activeId();
        if (id !== null) {
          this.choose(id);
        }
        return;
      }
    }
  }

  private move(delta: 1 | -1): void {
    const options = this.options();
    if (options.length === 0) {
      return;
    }
    const index = options.findIndex(account => account.id === this.activeId());
    const next = options[index === -1 ? 0 : (index + delta + options.length) % options.length];
    this.activeId.set(next.id);
    afterNextRender(() => this.optionElement(next.id)?.focus(), { injector: this.injector });
  }
```

A ⏎ on a chip is left to the browser: the entry form's ⏎-to-save skips `BUTTON` targets (`NO_ENTER_SAVE_TAGS`) and so does the layout's shortcut, and `split-lines`' `sheetKeyAction` leaves buttons inside its sheet to their native click.

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-picker/
git commit -m "feat(account-picker): ↑↓ ⏎ Esc and Tab wrap as an overlay

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Popover placement escapes the pane's overflow

**Files:**
- Test: `frontend/src/app/components/accounting/account-picker/account-picker.spec.ts` (implementation already in Task 3's `placePopover`)

**Interfaces:** none new.

- [ ] **Step 1: Write the test**

```ts
  it('places the popover fixed under the trigger, or above it near the bottom of the screen', () => {
    const { fixture, el } = render({ mode: 'sheet' });
    const trigger = el.querySelector('.acct-trigger')!;
    const rect = (top: number) =>
      ({ top, bottom: top + 40, left: 40, right: 240, width: 200, height: 40, x: 40, y: top, toJSON: () => ({}) }) as DOMRect;

    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(rect(100));
    let panel = openPanel(fixture);
    expect(panel.style.position).toBe('fixed');
    expect(panel.style.top).toBe('144px');
    expect(panel.style.left).toBe('40px');
    expect(panel.style.width).toBe('260px');
    expect(panel.style.maxHeight).toBe(`${Math.round(Math.min(window.innerHeight * 0.6, window.innerHeight - 148))}px`);
    fixture.componentInstance.close();
    fixture.detectChanges();

    const nearBottom = window.innerHeight - 60;
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(rect(nearBottom));
    panel = openPanel(fixture);
    expect(panel.style.top).toBe('');
    expect(panel.style.bottom).toBe(`${Math.round(window.innerHeight - nearBottom + 4)}px`);
  });
```

- [ ] **Step 2: Run** — `cd frontend && npm test -- --watch=false --include src/app/components/accounting/account-picker/account-picker.spec.ts`. Expected: PASS if Task 3's `placePopover` is as written; if it fails, fix `placePopover` (not the test) until it does. To see the test guard the behaviour, temporarily change `position: 'fixed'` to `'absolute'` and confirm the test fails with `expected 'absolute' to be 'fixed'`, then revert.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/components/accounting/account-picker/account-picker.spec.ts
git commit -m "test(account-picker): popover placement is fixed and flips above near the bottom

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Entry form adopts the picker (帳戶 tile and 餘額調整) and remembers recent accounts

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.html` (system select :72-79; 帳戶 tile :163-172)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts` (imports; `setAccount` :697-702; `save()` write callbacks :1080-1135)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.scss`
- Test: `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts` (:203, :258, :348, :447-464, :675, `chooseAccount` helper :859-865), `frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts` (:131, :450-453)

**Interfaces:**
- Consumes: `AccountPickerComponent` (Task 3), `rememberRecentAccounts` (Task 1).
- Produces: `EntryFormComponent.chooseAccount(id: number | null): void` (replaces `setAccount(value: string)`); `<app-account-picker class="account-picker">` in the 帳戶 tile and `class="account-picker system-account"` in the 餘額調整 block.

- [ ] **Step 1: Update and add tests**

In `entry-form.spec.ts` add imports `import { By } from '@angular/platform-browser';`, `import { AccountPickerComponent } from '../account-picker/account-picker';`, `import { RECENT_ACCOUNTS_KEY } from '../account-picker/recent-accounts';` and helpers inside the `describe`:

```ts
  function pickerValue(el: HTMLElement, selector = '.account-picker'): string | null {
    return el.querySelector(`${selector} .acct-trigger`)!.getAttribute('data-value');
  }

  function pick(el: HTMLElement, id: number, selector = '.account-picker'): void {
    (el.querySelector(`${selector} .acct-trigger`) as HTMLButtonElement).click();
    settle();
    (el.querySelector(`${selector} .acct-option[data-account-id="${id}"]`) as HTMLElement).click();
    settle();
  }
```

Replace assertions:
- `:203` → `expect(pickerValue(el)).toBe('1');`
- `:258` → `expect(pickerValue(el)).toBe('2');`
- `:348` → `expect(pickerValue(el)).toBe('2');`
- `:447-449` →

```ts
    (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    expect(Array.from(el.querySelectorAll('.account-picker .acct-option .acct-name')).map(node => text(node))).toEqual(['錢包', '玉山 UNI', '舊卡']);
    expect(Array.from(el.querySelectorAll('.account-picker .acct-group-name')).map(node => text(node))).toEqual(['未分組', '已封存']);
    expect(pickerValue(el)).toBe('3');
    (el.querySelector('.account-picker .acct-panel') as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    settle();
```

- `:463-464` →

```ts
    (fresh.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    expect(Array.from(fresh.querySelectorAll('.account-picker .acct-option .acct-name')).map(node => text(node))).toEqual(['錢包', '玉山 UNI']);
```

- `:675` → `expect(el.querySelector('.account-picker')).toBeNull();`
- `chooseAccount` helper body →

```ts
  function chooseAccount(el: HTMLElement, id: number): void {
    pick(el, id);
    respond(`/api/accounting/accounts/${id}`, makeAccountDetail({ id }));
  }
```

In `entry-form-schedule.spec.ts`: `:131` `set(el, '.account-select', '2', 'change');` →

```ts
    (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    (el.querySelector('.account-picker .acct-option[data-account-id="2"]') as HTMLElement).click();
    settle();
```

and `:450-453` →

```ts
    const trigger = el.querySelector('.account-picker .acct-trigger')!;
    expect(trigger.getAttribute('data-value')).toBe('4');
    expect(text(trigger)).toContain('舊卡');
```

New tests (in `entry-form.spec.ts`):

```ts
  it('passes the open accounts and the chosen account to the picker, and its choice moves the record', async () => {
    const { el } = await open('/accounting/entry', 'panes');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    const picker = harness.routeDebugElement!.query(By.css('app-account-picker.account-picker')).componentInstance as AccountPickerComponent;
    expect(picker.accounts().map(account => account.id)).toEqual([1, 2]);
    expect(picker.value()).toBe(1);
    expect(picker.label()).toBe('帳戶');
    expect(picker.allowArchived()).toBe(false);

    picker.choose(2);
    settle();
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    expect(form.accountId()).toBe(2);
    expect(pickerValue(el)).toBe('2');
  });

  it('uses the picker for the 餘額調整 account too', async () => {
    const { el } = await open('/accounting/entry?kind=system');
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    expect(el.querySelector('.account-picker.system-account')).not.toBeNull();
    pick(el, 2, '.system-account');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('.current-balance'))).toContain('−$27,218');
  });

  it('remembers the saved account as recent', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    keys(el, '1', '7', '0', '✓');
    httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
      .flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
    settle();
    expect(JSON.parse(localStorage.getItem(RECENT_ACCOUNTS_KEY)!)).toEqual([2]);
  });

  it('still saves when storage is blocked', async () => {
    const { el, left } = await open('/accounting/entry');
    respond('/api/accounting/categories', [FOOD]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '飲食');
    tap(el, '.cat', '午餐');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    keys(el, '1', '7', '0', '✓');
    httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/entries')
      .flush(makeEntryDetail({ id: 99, amount: '-170.0000', account_id: 2, category_id: 12 }));
    settle();
    expect(left()).toBe(true);
  });
```

(If `/accounting/entry?kind=system` asks for categories in your tree, add `respond('/api/accounting/categories', [])` — the system kind has no category tree today. If the `rememberEntryUse` write in the blocked-storage test throws because `entry-draft.ts` does not catch, that is a pre-existing bug outside this PR: assert `left()` only after confirming `rememberEntryUse` already wraps its `setItem` in try/catch — it does at base b8d4d77, entry-draft.ts:40-77.)

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`
Expected: FAIL — `Cannot read properties of null (reading 'getAttribute')` for `.account-picker .acct-trigger`.

- [ ] **Step 3: Implement**

`entry-form.ts` — add `AccountPickerComponent` to the decorator `imports` (`import { AccountPickerComponent } from '../account-picker/account-picker';`) and `import { rememberRecentAccounts } from '../account-picker/recent-accounts';`. Replace `setAccount(value: string)` with:

```ts
  chooseAccount(id: number | null): void {
    this.moveToAccount(id);
    if (this.entryId() === null) {
      this.rulesTouched.set(false);
    }
  }
```

(`rulesTouched` became a signal in PR-2; if your tree still has the plain field, write `this.rulesTouched = false;`.)

In `save()`:
- System branch: `this.write(request, () => this.finish(keepGoing));` → `this.write(request, () => { rememberRecentAccounts([this.accountId()]); this.finish(keepGoing); });`
- Single-entry callback: right after `rememberEntryUse(input, this.amount());` add `rememberRecentAccounts([input.account_id]);`.

`entry-form.html` — system block (:72-79) becomes:

```html
        <div class="tile wide account-tile">
          <span class="cap">帳戶</span>
          <app-account-picker
            class="account-picker system-account"
            label="帳戶"
            [accounts]="accountOptions()"
            [value]="accountId()"
            [allowArchived]="editing() || scheduleId() !== null"
            (valueChange)="chooseAccount($event)"
          />
        </div>
```

帳戶 tile (:163-172) becomes:

```html
        @if (kind() !== 'transfer') {
          <div class="tile account-tile">
            <app-account-picker
              class="account-picker"
              label="帳戶"
              [accounts]="accountOptions()"
              [value]="accountId()"
              [allowArchived]="editing() || scheduleId() !== null"
              (valueChange)="chooseAccount($event)"
            />
          </div>
        }
```

`entry-form.scss` — add:

```scss
.account-tile app-account-picker {
  flex: 1;
}
```

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/entry-form/
git commit -m "feat(entry-form): account picker for 帳戶 and 餘額調整, remembers recent accounts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Transfer legs adopt the picker

**Files:**
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.html` (:11-33)
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts` (imports; `setFrom/setTo` :178-184; computed members)
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.scss` (`.acc-select` :59-…)
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts` (transfer branch of `save()`)
- Test: `transfer-panel/transfer-panel.spec.ts` (:64-74, :108-117, :157-168, :262-286), `entry-form/entry-form.spec.ts` (:755), `entry-form/entry-form-schedule.spec.ts` (:287)

**Interfaces:**
- Produces: `TransferPanelComponent.setFrom(id: number | null)`, `setTo(id: number | null)`; `fromExclude`, `toExclude: Signal<number[]>`; pickers `app-account-picker.from-picker` (label 轉出帳戶) and `.to-picker` (label 轉入帳戶); `.acc-balance` removed.

- [ ] **Step 1: Update and add tests**

`transfer-panel.spec.ts` — imports `import { By } from '@angular/platform-browser';`, `import { AccountPickerComponent } from '../account-picker/account-picker';`; helper:

```ts
  function picker(fixture: ComponentFixture<TransferPanelComponent>, side: 'from' | 'to'): AccountPickerComponent {
    return fixture.debugElement.query(By.css(`app-account-picker.${side}-picker`)).componentInstance as AccountPickerComponent;
  }
```

- `:71-73` →

```ts
    expect(picker(fixture, 'from').value()).toBe(1);
    expect(picker(fixture, 'to').value()).toBe(2);
    expect(el.querySelector('.from-picker .acct-trigger')?.textContent).toContain('398,071');
    expect(el.querySelector('.acc-balance')).toBeNull();
    expect(picker(fixture, 'from').exclude()).toEqual([2]);
    expect(picker(fixture, 'to').exclude()).toEqual([1]);
```

- `:115` → `expect(picker(fixture, 'from').value()).toBe(2);` and add `expect(picker(fixture, 'from').exclude()).toEqual([1]);`
- `:159-161` (`refuses the same account on both sides`) → replace the select manipulation with `fixture.componentInstance.setTo(1);` (the picker never offers it; the panel still refuses a programmatic one).
- `:269-272` →

```ts
    const from = picker(fixture, 'from');
    expect(from.value()).toBe(4);
    expect(from.allowArchived()).toBe(true);
    expect(el.querySelector('.from-picker .acct-trigger')?.textContent).toContain('舊帳戶');
    expect(picker(fixture, 'to').accounts().map(account => account.id)).toEqual([1, 3, 4]);
```

- `:283-284` →

```ts
    expect(picker(fixture, 'from').value()).toBe(1);
    expect(picker(fixture, 'from').accounts().map(account => account.id)).toEqual([1, 3]);
```

New test:

```ts
  it('never offers the other leg and follows a swap', async () => {
    const fixture = await render([account(1, '國泰主帳戶', 'TWD', '398071'), account(3, '玉山銀行', 'TWD', '702730'), account(5, '錢包', 'TWD', '100')]);
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('.to-picker .acct-trigger')!.click();
    fixture.detectChanges();
    expect(Array.from(el.querySelectorAll('.to-picker .acct-option')).map(row => row.getAttribute('data-account-id'))).toEqual(['3', '5']);
    el.querySelector<HTMLElement>('.to-picker .acct-option[data-account-id="5"]')!.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.toId()).toBe(5);
    expect(picker(fixture, 'from').exclude()).toEqual([5]);

    el.querySelector<HTMLButtonElement>('.swap')!.click();
    fixture.detectChanges();
    expect(picker(fixture, 'from').exclude()).toEqual([1]);
    expect(picker(fixture, 'to').exclude()).toEqual([5]);
  });
```

`entry-form.spec.ts:755` → `expect(el.querySelector('app-transfer-panel .from-picker .acct-trigger')!.getAttribute('data-value')).toBe('1');`

`entry-form-schedule.spec.ts:287` `set(el, '.to-select', '3', 'change');` →

```ts
    (el.querySelector('.to-picker .acct-trigger') as HTMLButtonElement).click();
    settle();
    (el.querySelector('.to-picker .acct-option[data-account-id="3"]') as HTMLElement).click();
    settle();
```

Recent accounts for transfers — add to `entry-form.spec.ts` inside the existing test `'records a transfer from the 轉帳 tab with the shared name and date tiles'`, after its save response is flushed: `expect(JSON.parse(localStorage.getItem(RECENT_ACCOUNTS_KEY)!)).toEqual([1, 2]);` (from-leg first; that test's legs are accounts 1 → 2 by default).

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/transfer-panel/transfer-panel.spec.ts --include src/app/components/accounting/entry-form/entry-form.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`
Expected: FAIL — `Cannot read properties of null (reading 'componentInstance')` for `.from-picker`.

- [ ] **Step 3: Implement**

`transfer-panel.ts` — add `AccountPickerComponent` to `imports: [AmountKeypadComponent, AccountPickerComponent]`; drop the now-unused `accountLabel` import/field if nothing else uses it. Add:

```ts
  readonly fromExclude = computed(() => {
    const id = this.toId();
    return id === null ? [] : [id];
  });
  readonly toExclude = computed(() => {
    const id = this.fromId();
    return id === null ? [] : [id];
  });
```

Replace `setFrom` / `setTo`:

```ts
  setFrom(id: number | null): void {
    this.fromId.set(id);
  }

  setTo(id: number | null): void {
    this.toId.set(id);
  }
```

`transfer-panel.html` — replace :11-33 with:

```html
    <div class="acc acc-from">
      <span class="ico">{{ from()?.icon ?? '🏦' }}</span>
      <app-account-picker
        class="from-picker"
        label="轉出帳戶"
        [accounts]="legOptions()"
        [value]="fromId()"
        [exclude]="fromExclude()"
        [allowArchived]="keepArchived()"
        [disabled]="locked()"
        (valueChange)="setFrom($event)"
      />
    </div>
    <button type="button" class="swap" aria-label="交換帳戶" [disabled]="locked()" (click)="swap()">⇄</button>
    <div class="acc acc-to">
      <span class="ico">{{ to()?.icon ?? '🏦' }}</span>
      <app-account-picker
        class="to-picker"
        label="轉入帳戶"
        [accounts]="legOptions()"
        [value]="toId()"
        [exclude]="toExclude()"
        [allowArchived]="keepArchived()"
        [disabled]="locked()"
        (valueChange)="setTo($event)"
      />
    </div>
```

`transfer-panel.scss` — delete the `.acc-select { … }` block and the `small` rule inside `.acc` that styled `.acc-balance` (:56); add `.acc app-account-picker { max-width: 100%; }`.

`entry-form.ts` — transfer branch of `save()`:

```ts
      const request = this.transferPanel()?.submit(transferCommonFrom(this.sharedFields()), groupId) ?? null;
      this.write(request, () => {
        const panel = this.transferPanel();
        rememberRecentAccounts([panel?.fromId(), panel?.toId()]);
        this.finish(keepGoing);
      });
```

- [ ] **Step 4: Run and see it pass** — same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/transfer-panel/ frontend/src/app/components/accounting/entry-form/
git commit -m "feat(transfer-panel): account pickers for both legs, each excluding the other

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Split lines adopt the compact picker; the 新增拆帳行 modal joins the overlay contract

**Files:**
- Modify: `frontend/src/app/components/accounting/split-lines/split-lines.html` (:19, :45-52)
- Modify: `frontend/src/app/components/accounting/split-lines/split-lines.ts` (imports :14-19; `setAccount` :144-146; `onHostKeydown` :214-224)
- Test: `frontend/src/app/components/accounting/split-lines/split-lines.spec.ts` (:60, :72; new tests)

**Interfaces:**
- Consumes: `AccountPickerComponent`, `trapFocus`.
- Produces: `SplitLinesComponent.setAccount(id: number | null)`; `app-account-picker.line-account[compact]` (label 帳戶); the modal's `.overlay` carries `data-overlay`; `onHostKeydown` also wraps Tab inside `.sheet`.

- [ ] **Step 1: Update and add tests**

`split-lines.spec.ts` — imports `By`, `AccountPickerComponent`, `vi`; helper:

```ts
  function linePicker(): AccountPickerComponent {
    return fixture.debugElement.query(By.css('app-account-picker.line-account')).componentInstance as AccountPickerComponent;
  }
```

- `:60` → `expect(linePicker().value()).toBe(1); expect(linePicker().compact()).toBe(true);`
- `:72` `setValue('.line-account', '7', 'change');` → `linePicker().choose(7); fixture.detectChanges();`

New tests:

```ts
  it('closes only the picker on Esc, then the modal on the next Esc', () => {
    openSheet();
    el.querySelector<HTMLButtonElement>('.line-account .acct-trigger')!.click();
    fixture.detectChanges();
    const first = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.line-account .acct-panel')!.dispatchEvent(first);
    fixture.detectChanges();
    expect(first.defaultPrevented).toBe(true);
    expect(el.querySelector('.line-account .acct-panel')).toBeNull();
    expect(el.querySelector('.sheet')).not.toBeNull();

    const second = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.line-account .acct-trigger')!.dispatchEvent(second);
    fixture.detectChanges();
    expect(second.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')).toBeNull();
  });

  it('chooses with ⏎ in the picker without adding the line', () => {
    openSheet();
    setValue('.line-amount', '100');
    el.querySelector<HTMLButtonElement>('.line-account .acct-trigger')!.click();
    fixture.detectChanges();
    const row = el.querySelector<HTMLElement>('.line-account .acct-option[data-account-id="7"]')!;
    row.focus();
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    fixture.detectChanges();
    expect(linePicker().value()).toBe(7);
    expect(fixture.componentInstance.members()).toEqual([]);
    expect(el.querySelector('.sheet')).not.toBeNull();
  });

  it('marks the modal as an overlay and keeps Tab inside it', () => {
    openSheet();
    expect(el.querySelector('.overlay')!.hasAttribute('data-overlay')).toBe(true);
    const add = el.querySelector<HTMLButtonElement>('.line-add')!;
    add.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    add.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')!.contains(document.activeElement)).toBe(true);
  });
```

(`fixture` is created in `beforeEach` with `TestBed.createComponent`, which attaches to `document`, so `focus()` works. The split spec does not set a layout mode: `LayoutModeService` measures jsdom's 1024 px window → `panes`, so the picker renders as a popover.)

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/split-lines/split-lines.spec.ts`
Expected: FAIL — `Cannot read properties of null (reading 'componentInstance')`.

- [ ] **Step 3: Implement**

`split-lines.ts` — imports: `import { AccountPickerComponent } from '../account-picker/account-picker';`, add `trapFocus` to the `../accounting-ui` import; decorator `imports: [AccountPickerComponent]`. Replace `setAccount(value: string)` with:

```ts
  setAccount(id: number | null): void {
    this.draftAccountId.set(id);
  }
```

Replace `onHostKeydown`:

```ts
  /**
   * While the sheet is open: ⏎ in a sheet field adds the line, Esc anywhere closes it (see `sheetKeyAction`), Tab stays
   * inside it (overlay contract). Keys the account picker handled (its own Esc / ⏎ / Tab) arrive default-prevented
   * and are left alone, so Esc unwinds picker → modal → form one layer per press.
   */
  onHostKeydown(event: KeyboardEvent): void {
    if (!this.open()) {
      return;
    }
    const sheet = this.host.nativeElement.querySelector<HTMLElement>('.sheet');
    if (event.key === 'Tab') {
      if (sheet) {
        trapFocus(sheet, event);
      }
      return;
    }
    const action = sheetKeyAction(event, sheet);
    if (action === 'confirm') {
      this.add();
    } else if (action === 'close') {
      this.open.set(false);
    }
  }
```

`split-lines.html` — `:19` `<div class="overlay" (click)="open.set(false)">` → `<div class="overlay" data-overlay (click)="open.set(false)">`; `:45-52` becomes:

```html
        <div>
          <span class="k">帳戶<span class="sub2">預設同主記錄，可改</span></span>
          <app-account-picker
            class="v line-account"
            label="帳戶"
            [compact]="true"
            [accounts]="activeAccounts()"
            [value]="draftAccountId()"
            (valueChange)="setAccount($event)"
          />
        </div>
```

The picker's own overlay (phone) is rendered inside the modal's DOM; its `.overlay` has the same `z-index: 1100` as the modal but comes later in the DOM, so it stacks above it. The desktop popover uses `z-index: 1200`.

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/split-lines/
git commit -m "feat(split-lines): compact account picker; 新增拆帳行 modal joins the overlay contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: 還款帳戶 adopts the picker

**Files:**
- Modify: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.html` (:110-119)
- Modify: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.ts` (decorator; `setRepay` :229-231)
- Test: `schedule-tabs/schedule-tabs.spec.ts` (:128-137), `entry-form/entry-form-schedule.spec.ts` (:316)

**Interfaces:**
- Produces: `ScheduleTabsComponent.setRepay(id: number | null)`; `app-account-picker.sched-repay` (label 還款帳戶, `allowArchived` true — `repayOptions()` already limits archived accounts to the current repay account).

- [ ] **Step 1: Update tests**

`schedule-tabs.spec.ts` — import `By` and `AccountPickerComponent`; `:128` stays (`.sched-repay` is null for 支出). Replace `:131-137` body with:

```ts
    const { fixture, el } = render({ kind: 'payable', amount: 300000 });
    tab(fixture, '分期');
    const repay = fixture.debugElement.query(By.css('app-account-picker.sched-repay')).componentInstance as AccountPickerComponent;
    expect(repay.value()).toBe(1);
    expect(repay.label()).toBe('還款帳戶');
    expect(el.querySelector('.sched-repay .acct-trigger')!.getAttribute('aria-label')).toBe('還款帳戶：薪轉');
    repay.choose(2);
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().repayAccountId).toBe(2);
```

(If PR-1 made this spec render with `showTabs`, keep `tab(fixture, '分期')` — the default `showTabs` is `true`.)

`entry-form-schedule.spec.ts:316` → `expect(el.querySelector('.sched-repay .acct-trigger')!.getAttribute('data-value')).toBe('1');`

- [ ] **Step 2: Run and see them fail**

Run: `cd frontend && npm test -- --watch=false --include src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts --include src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`
Expected: FAIL — `Cannot read properties of null (reading 'componentInstance')`.

- [ ] **Step 3: Implement**

`schedule-tabs.ts` — decorator `imports: [AccountPickerComponent]` (`import { AccountPickerComponent } from '../account-picker/account-picker';`); remove `readonly accountLabel = accountLabel;` and its import if unused; replace `setRepay`:

```ts
  setRepay(id: number | null): void {
    this.patch({ repayAccountId: id });
  }
```

`schedule-tabs.html` :110-119 →

```html
        @if (kind() === 'payable') {
          <div class="sched-row">
            <span class="sched-label">還款帳戶</span>
            <app-account-picker
              class="sched-repay"
              label="還款帳戶"
              [accounts]="repayOptions()"
              [value]="repayValue()"
              [allowArchived]="true"
              [disabled]="disabled()"
              (valueChange)="setRepay($event)"
            />
          </div>
        }
```

- [ ] **Step 4: Run and see it pass** — same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/schedule-tabs/ frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts
git commit -m "feat(schedule-tabs): account picker for 還款帳戶

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Entry form — Esc unwinds picker → modal → discard prompt

**Files:**
- Test: `frontend/src/app/components/accounting/entry-form/entry-form.spec.ts` (behaviour comes from Tasks 3–8 and PR-2; this task pins the integrated order)

**Interfaces:** none new.

- [ ] **Step 1: Write the tests** (inside `describe('EntryFormComponent')`)

```ts
  describe('picker Esc order', () => {
    function escape(target: Element): KeyboardEvent {
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      target.dispatchEvent(event);
      settle();
      return event;
    }

    it('Esc closes only the picker; the next Esc asks about a dirty draft, and only 放棄 leaves', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      typeInto(el, '.name-input', '午餐');
      (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
      settle();

      escape(el.querySelector('.account-picker .acct-panel')!);
      expect(el.querySelector('.account-picker .acct-panel')).toBeNull();
      expect(el.querySelector('.discard-strip')).toBeNull();
      expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('午餐');
      expect(left()).toBe(false);

      escape(el.querySelector('.account-picker .acct-trigger')!);
      expect(text(el.querySelector('.discard-strip'))).toContain('放棄未儲存的內容？');
      expect(left()).toBe(false);
      (el.querySelector('.discard-leave') as HTMLButtonElement).click();
      settle();
      expect(left()).toBe(true);
    });

    it('with a clean draft, Esc closes the picker and the next Esc cancels the form', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
      settle();
      escape(el.querySelector('.account-picker .acct-panel')!);
      expect(left()).toBe(false);
      escape(el.querySelector('.account-picker .acct-trigger')!);
      expect(left()).toBe(true);
    });

    it('⏎ on a picker row chooses the account and never saves the entry', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      tap(el, '.cat', '飲食');
      tap(el, '.cat', '午餐');
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      keys(el, '1', '7', '0');
      (el.querySelector('.account-picker .acct-trigger') as HTMLButtonElement).click();
      settle();
      const row = el.querySelector('.account-picker .acct-option[data-account-id="1"]') as HTMLElement;
      row.focus();
      row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
      settle();
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      httpMock.expectNone(r => r.method === 'POST');
      expect(left()).toBe(false);
      expect(el.querySelector('.account-picker .acct-trigger')!.getAttribute('data-value')).toBe('1');
    });

    it('from the 新增拆帳行 modal: picker, then modal, then the discard prompt', async () => {
      const { el, left } = await open('/accounting/entry');
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
      tap(el, '.cat', '飲食');
      tap(el, '.cat', '午餐'); // a picked category makes the draft dirty
      respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
      (el.querySelector('app-split-lines .add') as HTMLButtonElement).click();
      settle();
      respond('/api/accounting/categories', [FOOD]);
      respond('/api/accounting/counterparties', []);
      (el.querySelector('app-split-lines .line-account .acct-trigger') as HTMLButtonElement).click();
      settle();

      escape(el.querySelector('app-split-lines .line-account .acct-panel')!);
      expect(el.querySelector('app-split-lines .line-account .acct-panel')).toBeNull();
      expect(el.querySelector('app-split-lines .sheet')).not.toBeNull();

      escape(el.querySelector('app-split-lines .line-account .acct-trigger')!);
      expect(el.querySelector('app-split-lines .sheet')).toBeNull();
      expect(el.querySelectorAll('app-split-lines .member').length).toBe(0);
      expect(el.querySelector('.discard-strip')).toBeNull();
      expect(text(el.querySelector('.grid .cat.on, .cat.on'))).toContain('午餐');

      escape(el.querySelector('app-split-lines .add')!);
      expect(text(el.querySelector('.discard-strip'))).toContain('放棄未儲存的內容？');
      expect(left()).toBe(false);
    });
  });
```

(The 午餐 assertion checks the main draft's category survived; if the category picker marks the chosen leaf differently in your tree, assert `form.category()?.id === 12` via `harness.routeDebugElement!.componentInstance` instead.)

- [ ] **Step 2: Run** — `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/entry-form.spec.ts`. Expected: PASS. If any Esc reaches the form too early, the failing step names the layer: the picker or `split-lines` handler did not `preventDefault`, or the entry form's `onKeydown` does not return on `isHandledKey` — fix that handler, not the test.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/components/accounting/entry-form/entry-form.spec.ts
git commit -m "test(entry-form): Esc unwinds picker, 新增拆帳行 modal, then the discard prompt

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Verification and PR

- [ ] **Step 1: Full test run** — `cd frontend && npm test -- --watch=false` → PASS; keep the summary.
- [ ] **Step 2: Build** — `cd frontend && npm run build` (or `npx ng build` without a repo-root `.env`) → exit 0; `account-picker.scss` stays under the 20 kB component-style budget.
- [ ] **Step 3: Leftovers** — `grep -rn "account-select\|acc-select\|from-select\|to-select" frontend/src/app/components/accounting` → nothing; `grep -rn "<select" frontend/src/app/components/accounting/entry-form/entry-form.html frontend/src/app/components/accounting/transfer-panel/transfer-panel.html frontend/src/app/components/accounting/split-lines/split-lines.html frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.html` lists only non-account selects (專案, 類別, 單位, 結束); `git diff main --stat -- backend openspec .env` → empty.
- [ ] **Step 4: Demo screenshots and manual pass at 390×844, 760×820, 1280×800.**
  - *Before*: `http://127.0.0.1:18080/hub/accounting/entry` (the demo proxy serves `/home/opc/workspace/home-hub-schedules/frontend/dist/inventory-ui/browser` against the fictional-data demo API).
  - *After*: if your checkout is `/home/opc/workspace/home-hub-schedules`, the Step 2 build lands in that `dist/` — reload. Otherwise write `/tmp/hh-demo-proxy.json` = `{"/api/accounting": {"target": "http://127.0.0.1:8011", "pathRewrite": {"^/api/accounting": ""}, "changeOrigin": true}}` (outside the repo), run `cd frontend && npx ng serve --proxy-config /tmp/hh-demo-proxy.json --port 4310`, open `http://127.0.0.1:4310/hub/accounting/entry`.
  - Capture (e.g. `npx playwright screenshot --viewport-size=760,820 …`): the picker open in each mode — bottom sheet at 390, popover at 760 **with the 帳戶 tile scrolled near the bottom of the sheet pane** (Review Focus 1: not clipped, flips above), popover at 1280; the 轉帳 tab (both legs, no separate balance line); the 新增拆帳行 modal with the compact picker; 分期 on 應付款項 with 還款帳戶.
  - Manually: ↑↓⏎ Esc and Tab inside the picker; Esc ×3 from the split modal (Review Focus 2).
  - If you cannot reach the demo API or run a browser, say so in the PR and attach the Vitest output instead.
- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/ux-refine-3-account-picker
gh pr create --base main --head feat/ux-refine-3-account-picker \
  --title "feat(accounting): UX refine 3 — account picker everywhere an account is chosen" \
  --body-file /tmp/pr-ux-refine-3.md
```

PR body template (`/tmp/pr-ux-refine-3.md`):

```markdown
## Summary
Spec: docs/superpowers/specs/2026-10-05-ux-refinement-design.md §3 (PR-3 of 3; builds on PR-2's `trapFocus`, `data-overlay` and `DirtyFormRegistry`).
- `<app-account-picker>`: trigger `[icon] name · balance` (可用 for cards), grouped list with currency badge and balance, 已封存 group when allowed, search from 8 accounts, 最近使用 (`hh.accounting.recentAccounts`), bottom sheet on phones, fixed popover elsewhere.
- Keyboard: ↑↓ ⏎ Esc, Tab wraps; Esc closes only the picker (handled key), so Esc unwinds picker → 新增拆帳行 modal → discard prompt.
- Adopted in the entry form (帳戶, 餘額調整), transfer legs (each excludes the other; separate balance line removed), split lines (compact), 還款帳戶.

## Review focus
1. Popover not clipped at 760×820 near the pane bottom (account-picker.spec placement test + screenshot).
2. Nested Esc order from the split modal (entry-form.spec `picker Esc order`).
3. ⏎ in the picker never saves / never adds a split line (account-picker.spec, split-lines.spec, entry-form.spec).
4. Blocked localStorage (recent-accounts.spec, account-picker.spec, entry-form.spec).
5. Transfer legs exclude each other across swaps (transfer-panel.spec).

## Test plan
- [ ] `cd frontend && npm test -- --watch=false` — <paste summary>
- [ ] `cd frontend && npm run build`
- [ ] Screenshots before/after at 390×844, 760×820, 1280×800 (attached)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Self-review

- **Spec coverage:** §3.1 inputs/outputs → Task 3 (`disabled` added for locked legs and disabled schedule tabs); trigger text / `可用` / `選擇帳戶` / `aria-haspopup` / `aria-expanded` → Task 3; phone sheet vs. popover → Tasks 3, 5; search threshold and name/group filtering → Tasks 2, 3; 最近使用 (≤4 chips, ≤8 stored, hidden when empty or searching, written on save incl. both transfer legs) → Tasks 1, 3, 6, 7; groups in `sort_order`, 未分組, 已封存 last, `aria-selected` + check → Tasks 2, 3; listbox/option roles, ↑↓ ⏎ Esc, selection closes and refocuses the trigger → Tasks 3, 4; Focus paragraph (desktop search / selected row, phone never search, empty → panel, Tab wrap via `trapFocus`) → Task 4; overlay contract for the picker and the 新增拆帳行 modal incl. the nested 3-Esc test → Tasks 4, 8, 10; §3.2 adoption sites → Tasks 6 (entry-form.html:163-172 and :74), 7, 8, 9; `accountLabel()` stays for other uses; per-category last-use unchanged; §3.3 tests → Tasks 1–10. Gaps fixed while drafting: outside click closes the desktop popover without stealing focus; the modal also wraps Tab (required once it carries `data-overlay`).
- **Resolved ambiguities:** the popover is `position: fixed` with coordinates from the trigger instead of `position: absolute`, because an absolutely positioned panel inside the sheet pane would be clipped by its `overflow` (spec review item 3); hosts pass `groups = null` to avoid a new `GET /account-groups` (allowed by the spec's optional input); selecting the current value emits nothing.
