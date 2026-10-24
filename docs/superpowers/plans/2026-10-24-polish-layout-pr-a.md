# Polish Layout v2 — PR A (defects) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three traced layout defects of the 2026-10-24 review that need no product decision: Bootstrap's global `.row` shifting accounting rows, the dark-mode-invisible sidebar wordmark, and `/portfolio/accounts` driving the shell's pane state (mid-page 記一筆 button, auto-collapsed sidebar).

**Architecture:** (D1) Rename the six component-owned `row` classes to scoped names and add a Vitest gate that scans templates for Bootstrap grid class tokens outside an allowlist. (Logo) Replace the `<img>` lockup with an inline-SVG component whose fills are theme tokens. (D4) The accounts page stops writing `PaneStateService.paneOpen`; the FAB's right offset becomes a `fabRight` signal the accounting layout owns, bound on the FAB as the custom property `--fab-right`, so `shell.scss` no longer hard-codes the accounting column ratio.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest via `cd frontend && npx ng test --watch=false --include <spec>`, SCSS with the `--app-*` token set, Playwright-in-Docker for the visual check (as in earlier redesign PRs).

**Spec:** `openspec/changes/polish-layout-v2/` (PR #137, rev 6 `25c26c6`): `design.md` D1 and D4, `tasks.md` §1, deltas `specs/frontend-ui-primitives/spec.md` ("Component classes do not collide with Bootstrap"), `specs/frontend-accounting-ledger/spec.md` ("Rows keep the page gutter"), `specs/frontend-design-system/spec.md` ("Logo legible in both themes"), `specs/frontend-app-shell/spec.md` ("Pane state belongs to two-pane layouts"). Evidence: `design_handoff_v2/reviews/2026-10-24-layout-review.md` defects 1, 4, 5.

## Global Constraints

- Frontend only; no API, model or copy change. Branch `fix/polish-layout-pr-a` from `origin/main`.
- Colours only through `--app-*` tokens; no hex literal in a component template or stylesheet (the contrast spec parses `styles.scss`).
- Bootstrap grid classes (`row`, `col`, `col-*`, `container`) stay only in the four allowlisted legacy forms: `components/item-list/item-list.html`, `components/portfolio/dividend-list/dividend-list.html`, `components/portfolio/transaction-list/transaction-list.html`, `components/accounting/account-settings/account-settings.html`.
- The selected-row bleed contract is unchanged: `.sel` bleeds by `--row-sel-bleed` only (spec: "a selected row's highlight MAY bleed only by `--row-sel-bleed`").
- Shell default FAB offset stays `right: 30px`; the accounting panes offset stays the current formula, now owned by the accounting layout.
- `N` and the FAB's `href` behaviour are untouched.
- Every task: `cd frontend && npx ng test --watch=false --include <touched specs>` green before commit; the whole suite (`npx ng test --watch=false`) green before the PR.

## Review Focus

1. **Selected row bleed after the rename.** The `.sel` negative margin must still read `--row-sel-bleed`; a missed selector would make the open entry's highlight stop at the gutter or spill. Pinned in Task 1 (timeline spec keeps asserting `.entry-row.sel`) and measured in Task 4.
2. **Gate false positives on `row-*` / `entry-row`.** `class="row-name"`, `class="entry-row"`, `[class.sel]` must not trip the gate; only whole tokens do. Pinned by the matcher cases in Task 1.
3. **Reminders' expanded counterparty rows** share `entry-row.scss`; if `reminders.html:118` kept `row` they would silently regain the Bootstrap margin. Pinned by the gate (Task 1) scanning every template.
4. **FAB offset when the accounting pane is open but the viewport drops to the 760–1023 sheet, or the layout is destroyed with a pane open.** `fabRight` must return to `null` or the button stays mid-page on other routes. Pinned in Task 3 (accounting-layout spec: sheet → null, destroy → null).
5. **Collapsed-rail icon** (`assets/app-icon.svg`) keeps hard-coded fills; the spec covers the wordmark only. Out of scope here — Task 4 records its dark-mode appearance so a follow-up issue can be filed if it reads poorly.

---

### Task 1: Scoped row classes + Bootstrap-grid gate (D1)

**Files:**
- Create: `frontend/src/app/theme/bootstrap-grid.spec.ts`
- Modify: `frontend/src/app/components/accounting/entry-row.scss:3,20,34` (`.row` → `.entry-row`)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html:119`, `accounting/reminders/reminders.html:118`, `accounting/statements/statement-detail/statement-detail.html:140` (`class="row"` → `class="entry-row"`), `accounting/statements/statement-list/statement-list.html:25` (`row statement-row` → `entry-row statement-row`)
- Modify: `frontend/src/app/components/accounting/statements/statement-detail/statement-detail.scss:174` (`.row {` → `.entry-row {`)
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.html:53` (`row entry` → `entry-row entry`), `account-entries.scss:42,53,56,63` (`.row` → `.entry-row`)
- Modify: `frontend/src/app/components/portfolio/cash-flow-form/cash-flow-form.ts:74,84,107` (`row` → `field-row`)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts` (24 `.row` selectors), `accounting/statements/statement-detail/statement-detail.spec.ts` (8), `accounting/reminders/reminders.spec.ts:427,456,561,619` (`.debt-entries .row`, 4) — selector rewrite only; `account-entries.spec.ts` (`.entry`) and `statement-list.spec.ts` (`.statement-row`) survive unchanged; `accounting-settings.spec.ts` matches `.row-name` / `.row-delete` and is **not** touched

**Interfaces:**
- Produces: class names `entry-row` (accounting list rows, all four `entry-row.scss` users and account-entries) and `field-row` (cash-flow form two-column rows). Task 4 measures `button.entry-row`.

- [ ] **Step 1: Write the failing gate spec** `frontend/src/app/theme/bootstrap-grid.spec.ts`

```ts
/// <reference types="node" />
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Legacy PrimeNG forms that use the Bootstrap grid on purpose (design D1); shrink this list as they are rebuilt. */
const ALLOWLIST = [
  'src/app/components/item-list/item-list.html',
  'src/app/components/portfolio/dividend-list/dividend-list.html',
  'src/app/components/portfolio/transaction-list/transaction-list.html',
  'src/app/components/accounting/account-settings/account-settings.html',
];
const GRID_TOKEN = /^(row|col|col-(\d{1,2}|auto|(sm|md|lg|xl|xxl)(-\d{1,2}|-auto)?)|container(-fluid)?)$/;

/** `file:line` for every static `class="…"` attribute holding a Bootstrap grid token. */
export function gridOffenders(root: string): string[] { /* walk root for *.html and non-spec *.ts; for each line, for each class="…" value, split on whitespace, report tokens matching GRID_TOKEN; skip ALLOWLIST paths */ }

describe('Bootstrap grid class names (polish-layout-v2 D1)', () => {
  it('flags only whole grid tokens', () => {
    expect('row'.match(GRID_TOKEN)).not.toBeNull();
    expect('col-6'.match(GRID_TOKEN)).not.toBeNull();
    for (const ok of ['row-name', 'entry-row', 'field-row', 'rows', 'column', 'col-span']) expect(ok.match(GRID_TOKEN), ok).toBeNull();
  });
  it('no component template outside the allowlist uses a Bootstrap grid class', () => {
    expect(gridOffenders(join(process.cwd(), 'src/app'))).toEqual([]);
  });
  it('every allowlisted file still exists', () => {
    for (const p of ALLOWLIST) expect(existsSync(join(process.cwd(), p)), p).toBe(true);
  });
});
```

Implement `gridOffenders` in the spec file (it is test-only). Lines are reported as `relative(process.cwd(), file) + ':' + lineNo`.

- [ ] **Step 2: Run it; expect exactly the six known offenders**

Run: `cd frontend && npx ng test --watch=false --include src/app/theme/bootstrap-grid.spec.ts`
Expected: FAIL; the diff lists `timeline.html:119`, `reminders.html:118`, `statement-detail.html:140`, `statement-list.html:25`, `account-entries.html:53`, `cash-flow-form.ts:74`, `cash-flow-form.ts:84` and nothing else. If another path appears, it is a real collision: rename it too and list it in the commit message.

- [ ] **Step 3: Rename** per the Files list (`entry-row` in the accounting rows and the four stylesheet selectors groups; `field-row` in `cash-flow-form.ts` template and its `styles`). Then rewrite the spec selectors in `timeline.spec.ts`, `statement-detail.spec.ts` and `reminders.spec.ts`: `'.row'` → `'.entry-row'`, `'.row .name'` → `'.entry-row .name'`, `'.day-entries .row'` → `'.day-entries .entry-row'`, `'.debt-entries .row'` → `'.debt-entries .entry-row'`; leave `.row-name` / `.row-delete` untouched. `styles.scss:494-500` (`.p-dialog .row` / `.col-6`) serves the allowlisted dialogs and stays.

- [ ] **Step 4: Run the gate and the touched suites**

Run: `cd frontend && npx ng test --watch=false --include src/app/theme/bootstrap-grid.spec.ts --include 'src/app/components/accounting/**/*.spec.ts' --include 'src/app/components/portfolio/cash-flow-form/*.spec.ts' --include 'src/app/components/portfolio/accounts/*.spec.ts'`
Expected: PASS, and `timeline.spec.ts` still asserts `.entry-row.sel` on the open entry (the existing `classList toContain('sel')` case).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/theme/bootstrap-grid.spec.ts frontend/src/app/components
git commit -m "fix(spa): accounting rows stop inheriting Bootstrap's .row margins; gate grid class names (polish-layout-v2 D1)"
```

---

### Task 2: Sidebar logo lockup legible in dark mode

**Files:**
- Create: `frontend/src/app/components/sidebar/logo-lockup.ts`, `logo-lockup.spec.ts`
- Modify: `frontend/src/app/components/sidebar/sidebar.html:5` (`<img src="assets/logo-lockup.svg" …>` → `<app-logo-lockup />`), `sidebar.ts:13` (add to `imports`), `sidebar.scss:47-58` (`.side-logo img` rule also targets `app-logo-lockup`: `display:block; height:36px; max-width:100%`)
- Delete: `frontend/src/assets/logo-lockup.svg` (only reference was `sidebar.html:5`)
- Test: `frontend/src/app/components/sidebar/sidebar.spec.ts:105`, `frontend/src/app/theme/contrast.spec.ts`

**Interfaces:**
- Produces: `LogoLockupComponent` (`selector: 'app-logo-lockup'`), standalone, OnPush, inline template = the current lockup SVG (`viewBox="0 0 320 80"`, `role="img"`, `aria-label="Home Hub"`) with fills: tile `var(--app-text)`, bars + plus + dot `var(--app-surface)`, the grey bar `var(--app-text-muted)`, wordmark `Home Hub` text `currentColor`, subtitle `家庭服務中樞` `var(--app-text-muted)`. Host `:host { color: var(--app-text); display: block; height: 36px; } svg { display: block; height: 100%; width: auto; }`.

- [ ] **Step 1: Write the failing component spec** `logo-lockup.spec.ts`

```ts
it('renders the lockup inline with token fills only', async () => {
  const fixture = TestBed.createComponent(LogoLockupComponent); fixture.detectChanges();
  const svg = fixture.nativeElement.querySelector('svg[role="img"]')!;
  expect(svg.getAttribute('aria-label')).toBe('Home Hub');
  expect(svg.textContent).toContain('Home Hub');
  expect(svg.textContent).toContain('家庭服務中樞');
  expect(svg.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b/i);           // no hard-coded colour
  expect(svg.querySelector('text')!.getAttribute('fill')).toBe('currentColor');
});
```

- [ ] **Step 2: Run; expect FAIL** (`Cannot find module './logo-lockup'`)

Run: `cd frontend && npx ng test --watch=false --include src/app/components/sidebar/logo-lockup.spec.ts`

- [ ] **Step 3: Implement `LogoLockupComponent`** per Interfaces; copy the paths/text from `assets/logo-lockup.svg`, replacing each `fill="#…"` **and the plus sign's `stroke="#ffffff"` (`logo-lockup.svg:7`)** with the token above (`var(--app-surface)` for the stroke) — the component spec's hex ban fails on a leftover stroke; keep the font stack and sizes. The grey bar (`--app-text-muted` on the `--app-text` tile) lands near 2.4:1 in dark mode; it is decorative, but Task 4 screenshots it so the owner can judge. Wire `sidebar.html` / `sidebar.ts` / `sidebar.scss`; delete the asset.

- [ ] **Step 4: Update `sidebar.spec.ts:105`**: expanded sidebar renders `.side-logo app-logo-lockup svg` (not null) and `.side-logo img` is null; the rail case at `:118` (app-icon `<img>`) stays.

- [ ] **Step 5: Add the contrast pin** in `theme/contrast.spec.ts`: a case `'sidebar wordmark: --app-text on --app-surface-glass over --app-bg ≥ 4.5 in both themes'` using the existing rgba-compositing helper on `:root` and `.app-dark-mode` token maps. (Light: `#151821` on `rgba(250,252,255,.8)` over `#f1f3f6`; dark: `#f3f6fb` on `rgba(23,28,36,.75)` over `#0f1218` — both far above 4.5.)

- [ ] **Step 6: Run the three specs**

Run: `cd frontend && npx ng test --watch=false --include src/app/components/sidebar/*.spec.ts --include src/app/theme/contrast.spec.ts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/components/sidebar frontend/src/app/theme/contrast.spec.ts
git rm -q frontend/src/assets/logo-lockup.svg
git commit -m "fix(spa): inline the sidebar logo lockup with token fills so the wordmark reads in dark mode"
```

---

### Task 3: Pane state belongs to two-pane layouts; FAB offset via `--fab-right` (D4)

**Files:**
- Modify: `frontend/src/app/services/pane-state.service.ts` (add `fabRight`)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts:215-224` (write `fabRight`; clear on destroy)
- Modify: `frontend/src/app/components/shell/shell.html:19` (`[class.beside-pane]="paneState.paneOpen()"` → `[style.--fab-right]="paneState.fabRight()"`), `shell.scss:29-60` (`right: var(--fab-right, 30px)`; delete the `&.beside-pane` block and its comment)
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-page.ts:61-63` (delete the `paneOpen` effect, the `onDestroy` reset and, if then unused, the `PaneStateService` / `DestroyRef` injections and imports)
- Test: `shell.spec.ts:208-215, 503-507`, `accounts-page.spec.ts:280-304, 428`, `accounting-layout.spec.ts:219-247`

**Interfaces:**
- Produces: `PaneStateService.fabRight: WritableSignal<string | null>` — a CSS `<length>`/`calc()` for the floating button's `right`; `null` = the shell default (`30px`). Written only by layouts that open a right pane.
- Produces: `export const FAB_RIGHT_BESIDE_PANE = 'calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) * 6 / 11 + 32px)'` in `accounting-layout.ts`, with the comment moved from `shell.scss` (list column bottom-right: `.hub-page` side padding 1.75rem, `.dbody` gap 16px, columns 5fr / 6fr — see `accounting-layout.scss:25-28`).

- [ ] **Step 1: Write the failing tests**

`accounting-layout.spec.ts`, extend `'publishes pane and form state for the shell'` (:219-237):
```ts
expect(pane.fabRight()).toBe(FAB_RIGHT_BESIDE_PANE);      // panes + entry open
// after navigateByUrl('/accounting/entry'): still FAB_RIGHT_BESIDE_PANE (the form is a pane)
// after fixture.destroy(): toBeNull()
```
and in `'publishes the 820px sheet as open, never as a pane'` (:240-247): `expect(pane.fabRight()).toBeNull()`.

`accounts-page.spec.ts`: replace `'holds PaneStateService.paneOpen while shown and clears it on destroy'` (:280-304) with
```ts
it('never writes the shell pane state (its detail is always visible)', async () => {
  const paneState = TestBed.inject(PaneStateService);
  const harness = await open('/portfolio/accounts/7'); flushList(); await settle(harness); flushDetail(7);
  expect(paneState.paneOpen()).toBe(false);
  expect(paneState.fabRight()).toBeNull();
});
```
and flip `:428` to `expect(TestBed.inject(PaneStateService).paneOpen()).toBe(false)`.

`shell.spec.ts:208-215` becomes
```ts
it('places the floating button where the pane layout says', async () => {
  const { fab, settle } = await render(viewportOf(1440), { url: '/accounting' });
  expect(fab()!.style.getPropertyValue('--fab-right')).toBe('');
  TestBed.inject(PaneStateService).fabRight.set('calc(1px + 2px)');
  await settle();
  expect(fab()!.style.getPropertyValue('--fab-right')).toBe('calc(1px + 2px)');
});
```
and `:507` → `expect(fab()!.style.getPropertyValue('--fab-right')).toBe(FAB_RIGHT_BESIDE_PANE)`.

- [ ] **Step 2: Run; expect FAIL** (`fabRight` undefined / `FAB_RIGHT_BESIDE_PANE` not exported / accounts page still sets `paneOpen`)

Run: `cd frontend && npx ng test --watch=false --include src/app/components/shell/shell.spec.ts --include src/app/components/portfolio/accounts/accounts-page.spec.ts --include src/app/components/accounting/accounting-layout/accounting-layout.spec.ts`

- [ ] **Step 3: Implement** per Files/Interfaces. In `accounting-layout.ts` the existing effect (`:215-219`) **keeps its `formOpen` and `sheetOpen` writes** (`shell.ts:54` hides the button on either; `accounting-layout.spec.ts:230,244` and `shell.spec.ts:195-206,486-512` pin them) and becomes
```ts
const beside = this.mode() === 'panes' && this.paneOpen();
this.paneState.paneOpen.set(beside);
this.paneState.fabRight.set(beside ? FAB_RIGHT_BESIDE_PANE : null);
this.paneState.formOpen.set(this.formOpen());
this.paneState.sheetOpen.set(this.sheetOpen());
```
and the existing `onDestroy` (`:220-224`, which already clears the three signals) adds `this.paneState.fabRight.set(null)`. Update the `PaneStateService` doc comment ("written by the accounting layout" → "written by layouts that open a right pane").

- [ ] **Step 4: Run the three suites + navigation-state spec**

Run: `cd frontend && npx ng test --watch=false --include src/app/components/shell/*.spec.ts --include src/app/components/portfolio/accounts/*.spec.ts --include src/app/components/accounting/accounting-layout/*.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/services/pane-state.service.ts frontend/src/app/components/shell frontend/src/app/components/accounting/accounting-layout frontend/src/app/components/portfolio/accounts
git commit -m "fix(spa): /portfolio/accounts stops driving the shell pane state; FAB offset owned by the accounting layout via --fab-right (polish-layout-v2 D4)"
```

---

### Task 4: Whole-suite run, build and browser verification

**Files:**
- No source change. Report dir `~/reports/home-hub-polish-pr-a-<UTC stamp>/` (screenshots + `probe.json` + `NOTES.md`); script in the session scratchpad, modelled on `batch1shots.py` (stubbed APIs, fictional data, preview server on 127.0.0.1:18081).

- [ ] **Step 1: Full suite + build**

Run: `cd frontend && npx ng test --watch=false && npx ng build --output-path /tmp/pr-a-dist`
Expected: all green; build warnings limited to the pre-existing `dashboard.scss` budget notice and protobufjs.

- [ ] **Step 2: Browser measurements** (Playwright in Docker, light + dark):
  - `/accounting` list mode at 390 px: `button.entry-row` left edge == `.day` (`timeline.html:219`) left edge == 16 px (`.hub-page` padding `1rem`, `shell.scss:127-129`; was 4 px from Bootstrap's −12 px margin).
  - `/accounting/entries/<id>` at 1440 px (the list stays mounted from 760 px; below that the phone route swaps in the entry page, and from 760 to 1023 px `--row-sel-bleed` is 0, so only ≥ 1024 px shows the 8 px bleed): `.entry-row.sel` left == the list pane's content left − 8 px (`--row-sel-bleed` on `.pane`, `accounting-layout.scss:53`).
  - `/accounting/accounts/<id>` entry history at 390 px: `a.entry-row` left == gutter.
  - Sidebar at 1440 px dark: `app-logo-lockup text` computed `fill` resolves to the dark `--app-text` (`rgb(243, 246, 251)`); screenshot of the sidebar head in both themes; also screenshot the rail icon in dark (Review Focus 5).
  - `/portfolio/accounts/1` at 1440 px: `app-sidebar` width 248 px (not the 72 px rail) and `.hub-fab` computed `right` == 30 px.
  - `/accounting/entries/<id>` at 1440 px: `.hub-fab` computed `right` > 30 px and `--fab-right` set.
  - `/portfolio/accounts` → 記錄入出金 (the only mount of `CashFlowFormComponent`, `accounts-list.html:3`): the two `.field-row` grids render two columns — the only browser proof of the inline styles at `cash-flow-form.ts:107`, which the class-attribute gate does not see.
  - Dark 1440 rail (sidebar collapsed): screenshot `assets/app-icon.svg` for the follow-up decision (tile ≈ 1:1 on the dark glass, marks ≈ 17:1).
- [ ] **Step 3: Write `NOTES.md`** with the measured numbers and the before/after pairs; nothing from the owner's data (stubbed fixtures only).
