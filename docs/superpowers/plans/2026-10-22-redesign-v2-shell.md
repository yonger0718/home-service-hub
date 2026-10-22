# Redesign v2 — PR 1 (Shell and navigation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the icon dock and frosted header with the labelled, collapsible sidebar, badges, hideable groups, the phone tab-bar parity rule and an app-wide 記一筆 (floating button + `N`), as the first releasable PR of `redesign-frontend-v2`.

**Architecture:** Navigation data stays in `components/shell/navigation.ts` (`NAV_GROUPS`), extended with badge and label fields plus pure layout functions (tab bar, sidebar mode, folded-group summary). Three root services feed the chrome: `NavigationPreferencesService` (device-local prefs), `NavBadgeService` (counts/dots from existing endpoints) and `NavigationStateService` (active item, visible groups, sidebar mode). Every routed page renders `<app-page-header>` (its `<h1>`, its actions, and on phones the gear and sub-nav); the shell renders only the sidebar, the outlet, the floating button and the tab bar.

**Tech Stack:** Angular 21 (standalone, signals, OnPush), Vitest via `@angular/build:unit-test`, SCSS with the `--app-*` / `--c-*` tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D1–D6, D8) and `specs/frontend-app-shell/spec.md`, `specs/frontend-appearance-service/spec.md` (Navigation preferences requirement only), `specs/frontend-accounting-ledger/spec.md` (Entry shortcuts). Visual reference: `design_handoff_v2/design_refs/{Main,SidebarProto,SidebarStates,MobileNavPhone,TimelinePhone}.dc.html`.

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-shell` (from `origin/main` 7a96dab). `.env` there is a dummy dev file (gitignored) so `npm run config` can generate `environment.ts`.

**Single-spec test command:** `cd frontend && npx ng test --watch=false --include <path-to-spec>` (verified). Full: `cd frontend && npx ng test --watch=false` (baseline 99 files pass) and `npm run build`.

## Not in this PR

- Settings UI for the navigation preferences, theme `system` → PR 2. Until then the prefs have defaults only (物資 hidden by default; reachable by URL).
- `/portfolio/import-broker` redirect and the device-local "last import attempt failed" flag → PR 6 (the unified import page writes it). PR 1's 匯入 dot uses the recalc status only.
- Page redesigns (dashboard, lists, timeline category bar) → PRs 3–7. Pages get only `<app-page-header>` here.

## Decisions this plan makes where the spec is silent or off (flag to owner / reviewer)

1. **Group id `system`.** The spec and canvas call the settings group `system` / 系統; code has `'settings'`. Rename `NavGroupId` `'settings'` → `'system'`, label `系統`, `tabLabel: '設定'` (the tab and gear say 設定).
2. **股利 count = rows with `receipt_status === 'pending'` (待確認收款) among the newest 100 dividends** (`GET /api/portfolio/dividends?limit=100&sort=ex_dividend_date:desc`). The server's 有效待收 rule (linked cash, eligible qty, review_reason) is not exposed per row and the list endpoint has no status filter; adding one would break the no-API-change rule. Pending rows are exactly the rows with a 確認收款 action, i.e. the owner's to-do.
3. **採買清單 badge omitted** — the page is a placeholder and 物資 is hidden; D4 says a badge without a source is omitted.
4. **IB Flex (open question in design.md):** covered for free — the dot reads the same recalc status whichever import started the chain.
5. **Phone `<h1>`** shows the page title (e.g. 股票交易紀錄), not the group label the canvas shows (財務). Page PRs may change it.
6. **Logo link** goes to the first visible group's `defaultPath` (not `/`, which is the hidden 庫存 page by default).
7. Spec path `shell/navigation.ts` is `frontend/src/app/components/shell/navigation.ts`.

## Global Constraints

- Breakpoints exactly: phone `< 760px` (`PHONE_QUERY = '(max-width: 759.98px)'`), panes `≥ 1024px`, wide sidebar `≥ 1280px` (new `SIDEBAR_WIDE_QUERY = '(min-width: 1280px)'`).
- Sidebar widths: expanded **248px**, rail **72px**.
- Colours only from existing tokens; no token added (`frontend-design-system` unchanged). Badge count pill `--app-primary` bg + `#fff`; attention dot 8px `--c-red`.
- Active item: `--app-primary` text on `--app-primary-soft`; expanded item's icon square filled `--app-primary` with white icon.
- Page `<h1>`: 28px, weight 800, letter-spacing −0.03em (`--tracking-tighter`).
- localStorage keys exactly: `hh-nav-hidden`, `hh-nav-sidebar`, `hh-nav-autocollapse`, `hh-nav-badges`, `hh-nav-folded`. Defaults: hidden `['supplies']`, sidebar `'expanded'`, autoCollapse `true`, showBadges `true`, folded `[]`. `'system'` is never accepted into hidden.
- Copy: nav `aria-label="主要導覽"`; collapse button `收合側欄`, expand `展開側欄`; FAB `aria-label="記一筆（快捷鍵 N）"`, tooltip `記一筆` + `<kbd>N</kbd>`; heading hint `目前`; badge labels `提醒中心 {n} 項`, `{n} 筆待確認`, `{n} 筆待收股利`, `最近一次匯入失敗`; gear `aria-label="設定"`; phone "+" keeps `aria-label="新增記錄"`.
- Sidebar labels: 庫存 / 採買清單; 投資概覽 / 交易紀錄 / 股利 / 已實現損益 / 現金帳戶 / 匯入; 記帳記錄 / 帳戶 / 記帳設定; 設定. Sub-nav (`label`) for 投資: 概覽 / 交易 / 股利 / 已實現 / 現金 / 匯入 (this order; 匯入 moves last).
- No backend, API, schema or migration change. Badge failures hide the badge silently.
- Match file style: standalone components, `ChangeDetectionStrategy.OnPush`, `input()` / `signal()` / `computed()`, comments only where the why is non-obvious.

## Review Focus

1. **Collapse button while a pane is open with the stored default `rail`** — pressing 展開側欄 must expand (and store `expanded`), not be undone by auto-collapse. → Task 4 test `expand while auto-collapsed and stored rail stays expanded`.
2. **`N` with a modal or dirty form** — `N` pressed while a PrimeNG dialog (`[role="dialog"]`) has focus, or on the entry form itself, must not navigate. → Task 7 tests in `global-shortcuts.spec.ts`.
3. **Every non-system group hidden** — tab bar must still render "+" alone and the gear must appear; sidebar shows only 系統. → Task 2 test `all groups hidden leaves plus alone and moves settings to the gear`.
4. **On a hidden group's route** (e.g. `/` with 物資 hidden) — no crash, no tab active, page header sub-nav still shows the group's items. → Task 5 test `hidden group route still shows its sub-nav`.
5. **Badge source failure or slow app start** — one failing request must not hide the other badges, and a focus refresh within 30 s of the last must not refire. → Task 3 tests.

---

### Task 1: NavigationPreferencesService

**Files:**
- Create: `frontend/src/app/services/navigation-preferences.service.ts`
- Test: `frontend/src/app/services/navigation-preferences.service.spec.ts`

**Interfaces:**
- Consumes: `NavGroupId` and `SidebarDefault` from `components/shell/navigation.ts`. This task adds `'system'` to the `NavGroupId` union and `export type SidebarDefault = 'expanded' | 'rail';` there (Task 2 renames the group itself).
- Produces:
  ```ts
  @Injectable({ providedIn: 'root' })
  export class NavigationPreferencesService {
    readonly hiddenGroups: Signal<readonly NavGroupId[]>;
    readonly sidebarDefault: Signal<SidebarDefault>;
    readonly autoCollapse: Signal<boolean>;
    readonly showBadges: Signal<boolean>;
    readonly foldedGroups: Signal<readonly NavGroupId[]>;
    setHidden(group: NavGroupId, hidden: boolean): void;   // ignores 'system'
    setSidebarDefault(value: SidebarDefault): void;
    setAutoCollapse(value: boolean): void;
    setShowBadges(value: boolean): void;
    setFolded(group: NavGroupId, folded: boolean): void;
  }
  ```

- [ ] **Step 1: Write the failing tests** (`beforeEach`: `TestBed.resetTestingModule(); localStorage.clear();`)
  - `defaults on first load`: `hiddenGroups()` `['supplies']`, `sidebarDefault()` `'expanded'`, `autoCollapse()` `true`, `showBadges()` `true`, `foldedGroups()` `[]`.
  - `writes each preference to its own key`: after `setHidden('portfolio', true)`, `setSidebarDefault('rail')`, `setAutoCollapse(false)`, `setShowBadges(false)`, `setFolded('accounting', true)` → `JSON.parse(localStorage.getItem('hh-nav-hidden')!)` `['supplies','portfolio']`, `getItem('hh-nav-sidebar')` `'rail'`, `getItem('hh-nav-autocollapse')` `'0'`, `getItem('hh-nav-badges')` `'0'`, `JSON.parse(getItem('hh-nav-folded')!)` `['accounting']`.
  - `restores stored values on a fresh instance` (set, `TestBed.resetTestingModule()`, inject again → same values).
  - `never hides system`: `setHidden('system', true)` → `hiddenGroups()` still `['supplies']` and `hh-nav-hidden` not written.
  - `ignores corrupt or unknown stored values`: `hh-nav-hidden` = `'nope'` → default; `'["system","portfolio","bogus"]'` → `['portfolio']`; `hh-nav-sidebar` = `'wide'` → `'expanded'`.
  - `unhiding removes the group`: `setHidden('supplies', false)` → `[]`.

- [ ] **Step 2: Run** `npx ng test --watch=false --include src/app/services/navigation-preferences.service.spec.ts` → FAIL (module not found).

- [ ] **Step 3: Implement.** Booleans stored `'1'`/`'0'`, lists as JSON arrays filtered to known `NavGroupId`s (and `'system'` removed from hidden). Storage access wrapped in try/catch exactly like `AppearanceService.persist` / `storage` getter (blocked storage keeps in-memory state). Writes are idempotent (`setHidden` of an already-hidden group does not duplicate).

- [ ] **Step 4: Run** the same command → PASS.

- [ ] **Step 5: Commit** `feat(nav): NavigationPreferencesService with hh-nav-* keys`

---

### Task 2: Navigation model and pure layout functions

**Files:**
- Modify: `frontend/src/app/components/shell/navigation.ts`
- Modify: `frontend/src/app/app.routes.spec.ts:197-200` (group ids test)
- Test: `frontend/src/app/components/shell/navigation.spec.ts` (create)

**Interfaces:**
- Produces (all exported from `navigation.ts`):
  ```ts
  export type NavGroupId = 'supplies' | 'portfolio' | 'accounting' | 'system';
  export interface NavBadge { count?: number; dot?: boolean; label: string }
  export interface NavItem { id; path; icon; label; sidebarLabel: string; title; group: NavGroupId; sub?; exact? }
  export interface NavGroup { id; label; tabLabel?: string; icon; defaultPath; items: NavItem[] }
  export type TabSlot = { kind: 'group'; group: NavGroup } | { kind: 'plus' };
  export interface TabBarLayout { slots: TabSlot[]; settingsInHeader: boolean }
  export function buildTabBar(groups: readonly NavGroup[], hidden: readonly NavGroupId[]): TabBarLayout;
  export interface GroupBadgeSummary { count: number; dot: boolean }
  export function summarizeGroupBadges(group: NavGroup, badges: Readonly<Record<string, NavBadge>>): GroupBadgeSummary;
  export interface SidebarModeInput { wide: boolean; stored: SidebarDefault; autoCollapse: boolean; paneOpen: boolean; paneOverride: boolean }
  export function resolveSidebarMode(input: SidebarModeInput): SidebarDefault;
  ```
  (`SidebarDefault` is defined in this file by Task 1.)

- [ ] **Step 1: Write the failing tests** in `navigation.spec.ts`:
  - `portfolio sub-nav order and labels`: `NAV_GROUPS.find(g => g.id==='portfolio')!.items.map(i => i.label)` → `['概覽','交易','股利','已實現','現金','匯入']`; `sidebarLabel`s → `['投資概覽','交易紀錄','股利','已實現損益','現金帳戶','匯入']`.
  - `system group`: id `'system'`, label `'系統'`, tabLabel `'設定'`, `navItemForUrl('/settings').group === 'system'`.
  - `tab bar with 物資 hidden`: `buildTabBar(NAV_GROUPS, ['supplies'])` slots rendered as labels (`tabLabel ?? label`, `'+'` for plus) → `['投資','+','財務']`, `settingsInHeader` `true`.
  - `tab bar with all visible`: `buildTabBar(NAV_GROUPS, [])` → `['物資','投資','+','財務','設定']`, `settingsInHeader` `false`.
  - `all groups hidden leaves plus alone and moves settings to the gear`: hidden `['supplies','portfolio','accounting']` → `['+']`, `true`.
  - `two hidden, even list`: hidden `['supplies','portfolio']` (list `[財務, 設定]`, length 2) → `['財務','+','設定']`, `false`.
  - `folded group sums counts and shows a dot`: portfolio with badges `{dividends:{count:2,label:''}, import:{dot:true,label:''}}` → `{count:2, dot:true}`; no badges → `{count:0, dot:false}`.
  - `sidebar mode`: table test over `resolveSidebarMode` — `wide:false` → `'rail'` regardless; wide+stored `'rail'` → `'rail'`; wide+expanded+paneOpen+autoCollapse+!override → `'rail'`; same with override → `'expanded'`; same with `autoCollapse:false` → `'expanded'`; wide+expanded+!paneOpen → `'expanded'`.
  - Keep existing `navItemForUrl` coverage passing (`app.routes.spec.ts` HIGHLIGHT table); update the group-id test at `app.routes.spec.ts:198-200` to `['supplies','portfolio','accounting','system']` and `.group` `'system'`.

- [ ] **Step 2: Run** `--include src/app/components/shell/navigation.spec.ts` → FAIL.

- [ ] **Step 3: Implement.** Data edits per Global Constraints labels. `buildTabBar`: `list = [visible non-system groups in NAV order, system group]`; if `list.length` is odd drop the system group and set `settingsInHeader`; insert `{kind:'plus'}` at `Math.floor(list.length / 2)`. `resolveSidebarMode`: `!wide → rail`; `stored === 'rail' → rail`; `paneOpen && autoCollapse && !paneOverride → rail`; else `expanded`. Update `MobileNavComponent` / `DockComponent` compile errors only as needed for the rename (`group.label` for the settings tab now reads `tabLabel`).

- [ ] **Step 4: Run** the navigation spec and `--include src/app/app.routes.spec.ts` → PASS.

- [ ] **Step 5: Commit** `feat(nav): system group, sidebar labels, tab bar and sidebar-mode rules`

---

### Task 3: Badge sources

**Files:**
- Create: `frontend/src/app/components/accounting/billing/reminder-count.ts` (+ `reminder-count.spec.ts`)
- Create: `frontend/src/app/components/accounting/import-review-count.ts` (+ spec)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts:515-520` (`reminderCount` calls `countReminders`)
- Modify: `frontend/src/app/components/accounting/accounts/accounts.ts:155-161` (`reviewCount` calls `importReviewCount`)
- Create: `frontend/src/app/services/nav-badge.service.ts` (+ `nav-badge.service.spec.ts`)

**Interfaces:**
- Produces:
  ```ts
  // reminder-count.ts — the 🔔 count, shared by the timeline and the nav badge
  export function countReminders(input: {
    dues: readonly BillingEvent[];                       // reminderDues(accounts, today)
    bill: (event: BillingEvent) => BillState | null;     // BillingService.bill
    counterparties: readonly Counterparty[];
    queue: readonly ScheduleInstance[];
    today: string;
  }): number;
  // import-review-count.ts
  export function importReviewCount(run: ImportRun | null | undefined): number;
  // nav-badge.service.ts
  export const BADGE_REFRESH_MIN_MS = 30_000;
  @Injectable({ providedIn: 'root' })
  export class NavBadgeService {
    readonly badges: Signal<Readonly<Record<string, NavBadge>>>;  // keyed by NavItem.id
    start(): void;   // first load + window 'focus' listener; idempotent
    refresh(): void; // no-op if the last refresh began < BADGE_REFRESH_MIN_MS ago
  }
  ```
- Item ids: `accounting-timeline` (count, `提醒中心 {n} 項`), `accounting` (count, `{n} 筆待確認`), `dividends` (count, `{n} 筆待收股利`), `import` (dot, `最近一次匯入失敗`). Count 0 → no entry.

- [ ] **Step 1: Write failing tests.**
  - `reminder-count.spec.ts` — `counts 1 open bill + 1 counterparty + 2 due queue items = 4`: dues = two due events whose `bill()` returns remaining 500 and 0; counterparties `open_count` 2 and 0; queue `{is_partial:true, due_date: tomorrow}`, `{is_partial:false, due_date: today}`, `{is_partial:false, due_date: tomorrow}` → `4`. Plus `unresolved bill (null) is not counted`.
  - `import-review-count.spec.ts`: `null` → 0; `{summary:{needs_review:{count:3}}}` → 3; `{summary:{unpaired_transfers:[1,2]}}` → 2; needs_review wins over unpaired.
  - `nav-badge.service.spec.ts` (stub `AccountingService`, `PortfolioService`, `BillingService` with `of(...)` / `throwError`; fake timers):
    - `two pending dividends → dividends badge {count:2,label:'2 筆待收股利'}` (fixture: 3 rows, `receipt_status` pending, pending, confirmed); asserts `getDividends` called with `{limit:100, sort:'ex_dividend_date:desc'}`.
    - `failed recalc chain → import dot` (`state:'failed'`); `state:'completed'` → no `import` key; `kind:'quotes', state:'failed'` → no dot.
    - `latest import with 1 needs_review → accounting {count:1,label:'1 筆待確認'}`.
    - `a failing source hides only its own badge`: `getCounterparties` errors → no `accounting-timeline` key, dividends badge still present, no throw.
    - `refresh within 30 s is skipped, after 30 s refires` (count calls to `getRecalcStatus`).
    - `start() twice registers one focus listener` (dispatch `focus` on window after 31 s → one extra call).

- [ ] **Step 2: Run** each spec → FAIL.

- [ ] **Step 3: Implement.** Move the body of `timeline.ts` `reminderCount` into `countReminders` (same three terms) and the body of `accounts.ts` `reviewCount` into `importReviewCount`; both components call them (behaviour unchanged — their existing specs must stay green). `NavBadgeService`: four independent requests, each with its own `catchError(() => of(null))` → null removes that item's badge. Reminder count: `getAccounts()`, `getCounterparties()`, `getScheduleInstances({ queue: true })`; `dues = reminderDues(accounts, todayIso())`; call `bills.ensure(dues.filter(e => e.kind === 'due'), today)`; the timeline badge is a `computed` over those signals and `bills.bill` so it updates when statements land. Dividends via `PortfolioService.getDividends`, recalc via `getRecalcStatus`, import via `AccountingService.getLatestImport` (404 → null like `accounts.ts importLookup`).

- [ ] **Step 4: Run** the three new specs plus `--include src/app/components/accounting/timeline` and `--include src/app/components/accounting/accounts` → PASS.

- [ ] **Step 5: Commit** `feat(nav): badge sources from existing endpoints`

---

### Task 4: NavigationStateService and the sidebar

**Files:**
- Modify: `frontend/src/app/services/layout-mode.service.ts` (add `SIDEBAR_WIDE_QUERY`, `wideSidebar: Signal<boolean>`)
- Create: `frontend/src/app/services/pane-state.service.ts`
- Create: `frontend/src/app/components/shell/navigation-state.service.ts` (+ spec)
- Create: `frontend/src/app/components/sidebar/sidebar.{ts,html,scss,spec.ts}`
- Delete: `frontend/src/app/components/dock/*` (after Task 7 mounts the sidebar; in this task leave the dock in place and only add the sidebar)

**Interfaces:**
- Produces:
  ```ts
  // pane-state.service.ts — written by AccountingLayoutComponent (Task 7), read by the shell
  @Injectable({ providedIn: 'root' })
  export class PaneStateService {
    readonly paneOpen: WritableSignal<boolean>;   // a right pane is open in 'panes' mode (≥1024)
    readonly formOpen: WritableSignal<boolean>;   // the entry form (new/edit) is on screen
  }
  // navigation-state.service.ts
  @Injectable({ providedIn: 'root' })
  export class NavigationStateService {
    readonly activeItem: Signal<NavItem>;                     // navItemForUrl(NavigationEnd.urlAfterRedirects)
    readonly activeGroup: Signal<NavGroup>;
    readonly visibleGroups: Signal<readonly NavGroup[]>;      // NAV_GROUPS minus prefs.hiddenGroups (system always)
    readonly tabBar: Signal<TabBarLayout>;
    readonly badges: Signal<Readonly<Record<string, NavBadge>>>; // {} when prefs.showBadges() is false
    readonly sidebarMode: Signal<SidebarDefault>;             // resolveSidebarMode(...)
    readonly canToggleSidebar: Signal<boolean>;               // layout.wideSidebar()
    toggleSidebar(): void;
    toggleGroup(id: NavGroupId): void;                        // no-op for the active group
    isFolded(id: NavGroupId): boolean;                        // false for the active group
  }
  ```
  Sidebar selector `app-sidebar`, no inputs (reads `NavigationStateService`).

- [ ] **Step 1: Write failing tests.**
  - `navigation-state.service.spec.ts` (fake `MATCH_MEDIA` returning `wide` for `SIDEBAR_WIDE_QUERY`; `PaneStateService` set directly):
    - `opening a pane collapses to the rail and closing restores expanded`.
    - `manual expand wins while the pane is open, and resets when it closes` (toggle while rail+pane → expanded; set paneOpen false then true → rail again).
    - `expand while auto-collapsed and stored rail stays expanded` (stored rail, pane open, toggle → `sidebarMode()` `'expanded'` and prefs `sidebarDefault()` `'expanded'`).
    - `auto-collapse off keeps expanded with a pane open`.
    - `narrow viewport is always the rail and cannot toggle` (`canToggleSidebar()` false).
    - `badges off hides every badge` (`prefs.setShowBadges(false)` → `badges()` `{}`).
    - `active group never folds` (`toggleGroup('accounting')` on `/accounting` → `isFolded('accounting')` false, prefs unchanged).
  - `sidebar.spec.ts` (render at wide/narrow; provide `NavBadgeService` stub with a writable `badges` signal):
    - `expanded at 1440 with headings and labels`: host has class `expanded`; headings `['投資','財務','系統']` (物資 hidden by default); item texts include `投資概覽`.
    - `rail at 1100 shows a tooltip per item`: class `rail`; each `a.side-item` has `aria-label` equal to its sidebarLabel (plus `，{badge label}` when badged) and a `.side-tip` containing the label.
    - `active item marked`: on `/portfolio/transactions` the `[aria-current="page"]` item text is `交易紀錄`.
    - `folded group shows sum and dot`: fold 投資 while on `/accounting` with badges dividends 2 + import dot → 投資 heading `aria-expanded="false"`, `.group-count` `2`, `.group-dot` present.
    - `active group shows 目前 and stays open on click`.
    - `collapse button stores rail` (`收合側欄` click → prefs `sidebarDefault()` `'rail'`); no toggle button rendered at 1100.
    - `logo links to the first visible group` (`/portfolio` by default).

- [ ] **Step 2: Run** both specs → FAIL.

- [ ] **Step 3: Implement.** `paneOverride` is a private signal in `NavigationStateService`, reset by an `effect` when `paneState.paneOpen()` becomes false. `toggleSidebar()`: when current mode is `rail` → if stored is `rail` set it `expanded`; if pane open and autoCollapse set override `true`. When `expanded` → if pane open, autoCollapse and override → override `false`; else `setSidebarDefault('rail')`. Sidebar markup and spacing follow `design_handoff_v2/design_refs/Main.dc.html` (sidebar block) and `SidebarStates.dc.html`: expanded = logo lockup `assets/logo-lockup.svg` + collapse button, group heading buttons (`aria-expanded`, chevron or `目前`, folded sum pill + dot), items (40px row, 32px icon square, label, right-aligned count pill or dot); rail = `assets/app-icon.svg`, expand button (wide only), divider per group, 44px icon buttons with count/dot overlaid and `.side-tip` shown on `:hover` and `:focus-visible`. Keep the dock's own-scroll-container rules (`overflow-y: auto; overscroll-behavior: contain`) and its compact-height density (`host` class `compact` from `layout.compactHeight()`). Position fixed, full height, `z-index: 1000`, `width: var(--sidebar-w)` where the host sets `--sidebar-w` to `248px` / `72px`.

- [ ] **Step 4: Run** both specs and `--include src/app/services/layout-mode.service.spec.ts` → PASS.

- [ ] **Step 5: Commit** `feat(shell): labelled sidebar with rail, folding, badges and auto-collapse state`

---

### Task 5: Page header, sub-nav and phone tab bar

**Files:**
- Create: `frontend/src/app/components/shell/page-header/page-header.{ts,html,scss,spec.ts}`
- Create: `frontend/src/app/components/shell/sub-nav/sub-nav.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/mobile-nav/mobile-nav.{ts,html,scss,spec.ts}` (tab bar only; inputs removed, reads `NavigationStateService`)
- Modify: `frontend/src/app/components/shell/shell.html` (drop the `[activeId]`/`[activeGroupId]` bindings on `app-mobile-nav`)
- Modify: `frontend/src/app/app.routes.spec.ts:217-235` (highlight test: sub-nav from `SubNavComponent`)

**Interfaces:**
- Consumes: `NavigationStateService` (Task 4), `LayoutModeService.mode`.
- Produces:
  ```ts
  // <app-page-header [title]="…">…page actions…</app-page-header>
  export class PageHeaderComponent { readonly title = input<string>(); }  // default: nav.activeItem().title
  // <app-sub-nav /> — the active group's items; 'chips' style for portfolio, segmented otherwise; hidden for groups with one item
  export class SubNavComponent {}
  ```

- [ ] **Step 1: Write failing tests.**
  - `page-header.spec.ts`: renders `<h1>` with the given title, else the active item's title; projects actions into `.page-actions`; at phone mode with the default prefs (物資 hidden) shows `a[aria-label="設定"][href="/settings"]` and an `app-sub-nav`; at phone with all groups visible no gear; at ≥760 neither gear nor sub-nav.
  - `sub-nav.spec.ts`: on `/accounting` → links `記錄/帳戶/記帳設定` with hrefs `/accounting`, `/accounting/accounts`, `/accounting/settings` and `aria-current="page"` on 記錄 (moved from mobile-nav.spec); on `/portfolio/dividends` → chips `概覽/交易/股利/已實現/現金/匯入`, 股利 shows count `2` when the badge stub has `dividends:{count:2}`; `hidden group route still shows its sub-nav` (on `/`, 物資 hidden → links `庫存/採買`); on `/settings` → nothing rendered.
  - `mobile-nav.spec.ts`: default prefs → slots `['投資','+','財務']`; all visible → `['物資','投資','+','財務','設定']`; "+" `href="/accounting/entry"` and `aria-label="新增記錄"` (kept); `dot on a non-active tab only`: on `/accounting` with dividends badge → 投資 tab has `.tab-dot`, 財務 none; badges off → no dots.

- [ ] **Step 2: Run** the three specs → FAIL.

- [ ] **Step 3: Implement.** Header layout: one row, `<h1>` left (28/800/−0.03em, `margin: 0`), `.page-actions` right (flex, gap .75rem, wraps), gear (44px round, `--app-surface` + `--app-border`) at the far right on phones only; sub-nav on its own row below on phones. Sub-nav styles: segmented = existing `.seg-control` rules moved from `mobile-nav.scss`; chips = horizontally scrolling row (`overflow-x: auto`, pills radius 999, active `--app-primary-soft` / `--app-primary`). Count pill per Global Constraints. Mobile nav keeps the existing tab and `.m-plus` styles; iterate `nav.tabBar().slots`; tab label `group.tabLabel ?? group.label`; dot = `summarizeGroupBadges` count > 0 or dot, not on `activeGroup`.

- [ ] **Step 4: Run** the three specs and `--include src/app/app.routes.spec.ts` → PASS.

- [ ] **Step 5: Commit** `feat(shell): page header with phone gear and sub-nav; tab bar parity and dots`

---

### Task 6: Pages adopt the page header; soft create buttons

**Files (modify; replace each page's top `.page-head` block or add the header as the first element):**
- `components/item-list/item-list.html` (+ `.scss` drop `.page-head` rules) — actions: 新增物品
- `components/shopping-list/shopping-list.html`
- `components/settings/settings.ts` (inline template: replace `.page-head` + `h2.section-title` 設定)
- `components/portfolio/dashboard/dashboard.html` (+ `.scss`) — actions: 匯入券商, 刷新行情
- `components/portfolio/transaction-list/transaction-list.html` (+ `.scss`) — 清除篩選, 新增交易
- `components/portfolio/dividend-list/dividend-list.html` (+ `.scss`) — 清除篩選, 新增股利
- `components/portfolio/realized-pnl/realized-pnl.html`
- `components/portfolio/accounts/accounts-list.html`, `account-detail.html`
- `components/portfolio/import/import.html`
- `components/portfolio/broker-import/broker-import.html` — `title="匯入券商對帳單"`, remove its `<h2>` of the same text
- `components/accounting/timeline/timeline.html` (header above `.monthbar`)
- `components/accounting/accounts/accounts.html` — replace `<h2>帳戶</h2>`; keep the `+` 新增帳戶 link as the action
- `components/accounting/accounting-settings/accounting-settings.html` — replace `<h2>記帳設定</h2>`
- `components/ui/btn/btn.ts` (+ `btn.scss`, `btn.spec.ts`): `BtnVariant` adds `'soft'` → class `btn-soft` (`--app-primary-soft` background, `--app-primary` text, no shadow)
- Each touched component's `imports` gains `PageHeaderComponent`.

**Interfaces:**
- Consumes: `PageHeaderComponent` (Task 5).

- [ ] **Step 1: Write failing tests.**
  - `btn.spec.ts`: `variant="soft"` renders `button.btn-soft`.
  - `app.routes.spec.ts` (new block `every routed page renders one h1`): for each top-level route component above (instantiate with the providers its existing spec uses; reuse those specs' setup helpers where they exist, otherwise add one assertion to each page's existing spec): exactly one `h1` and its text equals the expected title (`庫存管理`, `採買清單`, `設定`, `投資概覽`, `股票交易紀錄`, `股利領取紀錄`, `已實現損益`, `現金帳戶`, `匯入 CSV`, `匯入券商對帳單`, `記帳記錄`, `記帳帳戶`, `記帳設定`). Prefer adding the assertion to each page's existing `*.spec.ts` (they already have the providers).
  - `transaction-list.spec.ts`: `新增交易` is an `app-btn` rendering `.btn-soft`; no element with text `記一筆` in the page.

- [ ] **Step 2: Run** the touched specs → FAIL.

- [ ] **Step 3: Implement.** Create actions (新增物品, 新增交易, 新增股利) use `variant="soft"`. No 記一筆 button exists in any page header today (verified: `grep -rn 記一筆 --include='*.html'` is empty) — task 1.9 of the change is satisfied by the test above.

- [ ] **Step 4: Run** full `npx ng test --watch=false` → PASS (page specs with `.page-head` selectors updated where they relied on them).

- [ ] **Step 5: Commit** `feat(pages): every routed page owns its h1 via app-page-header; soft create buttons`

---

### Task 7: Shell — mount the sidebar, remove the header, app-wide 記一筆

**Files:**
- Modify: `frontend/src/app/components/shell/shell.{ts,html,scss,spec.ts}`
- Create: `frontend/src/app/components/shell/global-shortcuts.ts` (+ spec)
- Modify: `frontend/src/app/components/accounting/keyboard-shortcuts.ts` (remove the `n`/`N` case) and `keyboard-shortcuts.spec.ts` (lines 12, 14, 39, 58–62, 73: `n` now returns `null` from `resolveShortcut`; move the positive cases to `global-shortcuts.spec.ts`)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}`: remove `showFab` and the `.fab` link/styles; publish `PaneStateService` (`paneOpen = mode()==='panes' && paneOpen()`, `formOpen = formOpen()`; both reset to `false` on destroy); `setOutsideInert` queries `app-sidebar` instead of `app-dock`; `.dbody` height `calc(100dvh - var(--page-pad-y) - var(--page-pad-b))`
- Modify: `frontend/src/styles.scss:129` (remove `--chrome-header`)
- Delete: `frontend/src/app/components/dock/` (dock.ts/html/scss/spec.ts); update `app.routes.spec.ts` imports

**Interfaces:**
- Consumes: `NavigationStateService`, `NavBadgeService.start()`, `PaneStateService`, `DirtyFormRegistry.requestClose`, `isHandledKey`.
- Produces:
  ```ts
  // global-shortcuts.ts
  export function resolveGlobalShortcut(event: ShortcutKey, context: { url: string; targetTag: string | null; inDialog: boolean }): { type: 'navigate'; commands: ['/accounting/entry'] } | null;
  ```

- [ ] **Step 1: Write failing tests.**
  - `global-shortcuts.spec.ts`: `n` and `N` (shift allowed) on `/portfolio`, `/settings`, `/accounting`, `/accounting/accounts/5/settings` → navigate; `null` for targetTag `INPUT`/`TEXTAREA`/`SELECT`, any of ctrl/meta/alt, `inDialog: true`, url `/accounting/entry` and `/accounting/entries/9/edit`; other keys `null`.
  - `shell.spec.ts` (keep the three compact-height tests; add, with fake `MATCH_MEDIA` per width and stub `NavBadgeService`):
    - `no frosted header`: no `header.hub-header`, no `.hub-logo` in the shell.
    - `floating button on every route ≥760`: `a.hub-fab` `href="/accounting/entry"`, `aria-label="記一筆（快捷鍵 N）"`, tooltip text `記一筆` + `kbd` `N`; absent at phone width; absent when `PaneStateService.formOpen` is `true`; has class `beside-pane` when `paneOpen` is `true`.
    - `N on /portfolio opens the entry form` (dispatch `keydown` `n` on `document.body` → router navigated to `/accounting/entry`); `N handled elsewhere is ignored` (`preventDefault()`ed event → no navigation).
    - `main offset follows the sidebar`: `.hub-layout` has `sidebar-expanded` at wide, `sidebar-rail` otherwise.
    - `starts the badge refresh once`.
  - `accounting-layout.spec.ts`: delete the FAB tests at lines 93 and 215–217 (moved to shell); update `SCREEN` selector at line 47 (no `.fab` sibling); change `app-dock` to `app-sidebar` at lines 551 and 690; add `publishes pane and form state` (open `/accounting/entries/9` at panes → `paneOpen()` true; `/accounting/entry` → `formOpen()` true; destroy → both false); the line 362 test (handled `n` ignored) moves to shell.

- [ ] **Step 2: Run** the four specs → FAIL.

- [ ] **Step 3: Implement.** `shell.html`: `<app-sidebar>`, `.hub-main` (`margin-left: var(--sidebar-w)` with `--sidebar-w` set from `nav.sidebarMode()`; 0 on phones; `transition: margin-left var(--dur-base) var(--ease-standard)`), `<main class="hub-page"><router-outlet/></main>`, the FAB, `<app-mobile-nav>`. FAB: 56px circle, `--app-primary`, `#fff` "+", `box-shadow: 0 8px 20px rgba(83, 58, 253, 0.35)` (current `.fab`), `position: fixed; right: 30px; bottom: 30px; z-index: 1000`; tooltip shown on `:hover`/`:focus-visible`. `.beside-pane` (panes mode, pane open) moves it to the list column's bottom-right: `right: calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) * 6 / 11 + 32px)` (`.hub-page` side padding 1.75rem, `.dbody` gap 16px, columns 5fr / 6fr). Shell host `(document:keydown)`: skip `isHandledKey`; `targetTag` computed as in `AccountingLayoutComponent.onShortcutKey`; `inDialog = !!target?.closest('[role="dialog"], [aria-modal="true"]')`; on a match `preventDefault()` then `registry.requestClose(() => router.navigate(['/accounting/entry']))`. Phone `.hub-main` bottom padding: `calc(80px + env(safe-area-inset-bottom))` (sub-nav left the bottom bar). Delete the dock folder.

- [ ] **Step 4: Run** full `npx ng test --watch=false` and `npm run build` → PASS / build succeeds.

- [ ] **Step 5: Commit** `feat(shell): sidebar mounted, header removed, app-wide 記一筆 button and N`

---

### Task 8: Verification and change bookkeeping

**Files:**
- Modify: `openspec/changes/redesign-frontend-v2/tasks.md` (tick 1.1–1.10 that this PR completes)
- Create: `~/reports/home-hub-redesign-v2-shell-<UTC>/` screenshots (outside the repo)

- [ ] **Step 1:** `cd frontend && npx ng test --watch=false` → all files pass; `npm run build` → success. Record counts.
- [ ] **Step 2:** `npx @fission-ai/openspec validate redesign-frontend-v2 --strict` → valid.
- [ ] **Step 3:** Run the dev server against the demo stack (see memory: demo HomeHub on 127.0.0.1:18080) and capture every route at 390, 820, 1280, 1440 px, light and dark: `/portfolio`, `/portfolio/transactions`, `/portfolio/dividends`, `/portfolio/realized-pnl`, `/portfolio/accounts`, `/portfolio/import`, `/accounting`, `/accounting/entries/<id>` (auto-collapse), `/accounting/accounts`, `/accounting/settings`, `/settings`. Check: no page lost its title, FAB never covers the pane, tab bar shows `投資 · ＋ · 財務` with a gear on phones.
- [ ] **Step 4:** Tick tasks 1.1–1.10 in `tasks.md`; commit `docs(openspec): tick redesign-frontend-v2 shell tasks`.
