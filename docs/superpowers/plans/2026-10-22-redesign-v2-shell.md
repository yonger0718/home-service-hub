# Redesign v2 — PR 1 (Shell and navigation) Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the icon dock and frosted header with the labelled, collapsible sidebar, badges, hideable groups, the phone tab-bar parity rule and an app-wide 記一筆 (floating button + `N`), as the first releasable PR of `redesign-frontend-v2`.

**Architecture:** Navigation data stays in `components/shell/navigation.ts` (`NAV_GROUPS`), extended with badge and label fields plus pure layout functions (tab bar, sidebar mode, folded-group summary). Three root services feed the chrome: `NavigationPreferencesService` (device-local prefs), `NavBadgeService` (counts/dots from existing endpoints) and `NavigationStateService` (active item, visible groups, sidebar mode). Every routed page renders `<app-page-header>` (its `<h1>`, its actions, and on phones the gear and sub-nav); the shell renders only the sidebar, the outlet, the floating button and the tab bar.

**Tech Stack:** Angular 21 (standalone, signals, OnPush), Vitest via `@angular/build:unit-test`, SCSS with the `--app-*` / `--c-*` tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D1–D6, D8) and `specs/frontend-app-shell/spec.md`, `specs/frontend-appearance-service/spec.md` (Navigation preferences requirement only), `specs/frontend-accounting-ledger/spec.md` (Entry shortcuts). Visual reference: `design_handoff_v2/design_refs/{Main,SidebarProto,SidebarStates,MobileNavPhone,TimelinePhone}.dc.html`.

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-shell` (from `origin/main` 7a96dab). `.env` there is a dummy dev file (gitignored) so `npm run config` can generate `environment.ts`.

**Single-spec test command:** `cd frontend && npx ng test --watch=false --include <path-to-spec>` (verified). Full: `cd frontend && npx ng test --watch=false` (baseline 99 files pass) and `npm run build`.

**Rev 2 (AGENT-204, dev-astra, CHANGES REQUIRED at ae9492e):** Musts fixed — 股利 count dropped (Decision 2); collapse always stores `rail` (Task 4); drill-down routes and full phone route matrix (Decision 8, Task 6); `N` works inside the accounting sheet (Task 7); route-level `N` test re-hosted on the shell (Task 7). Shoulds folded in — blocked-storage tests (T1), request-generation guards and out-of-order tests (T3), lifecycle tests (T4), sub-nav dot/label/badges-off (T5), soft 新增帳戶 ×2 (T6), FAB hidden behind the sheet (T7), breakpoint boundaries and demo-stack note (T8).

## Not in this PR

- Settings UI for the navigation preferences, theme `system` → PR 2. Until then the prefs have defaults only (物資 hidden by default; reachable by URL).
- `/portfolio/import-broker` redirect and the device-local "last import attempt failed" flag → PR 6 (the unified import page writes it). PR 1's 匯入 dot uses the recalc status only.
- Page redesigns (dashboard, lists, timeline category bar) → PRs 3–7. Pages get only `<app-page-header>` here.

## Decisions this plan makes where the spec is silent or off (flag to owner / reviewer)

1. **Group id `system`.** The spec and canvas call the settings group `system` / 系統; code has `'settings'`. Rename `NavGroupId` `'settings'` → `'system'`, label `系統`, `tabLabel: '設定'` (the tab and gear say 設定). The settings *item* id stays `settings`.
2. **股利 count badge omitted in PR 1 (owner exception, rev 2).** No existing endpoint returns the 有效待收 *count*: `GET /api/portfolio/dividends` (`services/stock-portfolio-service/app/routers/portfolio.py:125`) has no status filter, and the 有效待收 rule (`portfolio_service.py:834`: one row per event, ex-date ≤ today, TWD, no review_reason/correction, no linked cash, eligible qty) is server-only; `getSummary()` gives the amount, not a count. D4 says such a badge is omitted. The spec scenario "Count in the sidebar, dot on the phone tab" is exercised with the 帳戶 count instead. Owner may later choose a renamed 待確認收款 count or an API addition (outside this change's non-goals).
3. **採買清單 badge omitted** — the page is a placeholder and 物資 is hidden; D4 says a badge without a source is omitted.
4. **IB Flex (open question in design.md):** covered for free — the dot reads the same recalc status whichever import started the chain.
5. **Device-local import-failure flag deferred to PR 6 (owner exception).** D4's second 匯入 dot source needs the import pages to record failures; PR 6 rebuilds those pages. PR 1 does not claim the full D4 badge set.
6. **Phone `<h1>`** on list-level pages shows the page title (e.g. 股票交易紀錄), not the group label the canvas shows (財務). Page PRs may change it.
7. **Logo link** goes to the first visible group's `defaultPath`; `/settings` when every hideable group is hidden.
8. **Drill-down routes keep their own top bar (rev 2).** Pages reached from a list — entry form / detail, split group, account new / settings, passbook, statement list / detail, reminders — own an `<h1>` (their existing title `h2` becomes `h1`; entry detail and split group get a `.visually-hidden` `h1`) but carry no gear or sub-nav: they already have ✕ / ‹ back controls and render inside the desktop pane. The gear and sub-nav belong to `<app-page-header>` on list-level pages.
9. Spec path `shell/navigation.ts` is `frontend/src/app/components/shell/navigation.ts`.

## Global Constraints

- Breakpoints exactly: phone `< 760px` (`PHONE_QUERY = '(max-width: 759.98px)'`), panes `≥ 1024px`, wide sidebar `≥ 1280px` (new `SIDEBAR_WIDE_QUERY = '(min-width: 1280px)'`).
- Sidebar widths: expanded **248px**, rail **72px**.
- Colours only from existing tokens; no token added (`frontend-design-system` unchanged). Badge count pill `--app-primary` bg + `#fff`; attention dot 8px `--c-red`.
- Active item: `--app-primary` text on `--app-primary-soft`; expanded item's icon square filled `--app-primary` with white icon.
- Page `<h1>`: 28px, weight 800, letter-spacing −0.03em (`--tracking-tighter`).
- localStorage keys exactly: `hh-nav-hidden`, `hh-nav-sidebar`, `hh-nav-autocollapse`, `hh-nav-badges`, `hh-nav-folded`. Defaults: hidden `['supplies']`, sidebar `'expanded'`, autoCollapse `true`, showBadges `true`, folded `[]`. `'system'` is never accepted into hidden.
- Copy: nav `aria-label="主要導覽"`; collapse button `收合側欄`, expand `展開側欄`; FAB `aria-label="記一筆（快捷鍵 N）"`, tooltip `記一筆` + `<kbd>N</kbd>`; heading hint `目前`; badge labels `提醒中心 {n} 項`, `{n} 筆待確認`, `最近一次匯入失敗` (no 股利 badge in PR 1, Decision 2); gear `aria-label="設定"`; phone "+" keeps `aria-label="新增記錄"`.
- Sidebar labels: 庫存 / 採買清單; 投資概覽 / 交易紀錄 / 股利 / 已實現損益 / 現金帳戶 / 匯入; 記帳記錄 / 帳戶 / 記帳設定; 設定. Sub-nav (`label`) for 投資: 概覽 / 交易 / 股利 / 已實現 / 現金 / 匯入 (this order; 匯入 moves last).
- No backend, API, schema or migration change. Badge failures hide the badge silently.
- Match file style: standalone components, `ChangeDetectionStrategy.OnPush`, `input()` / `signal()` / `computed()`, comments only where the why is non-obvious.

## Review Focus

1. **Sidebar toggles while a pane is open** — expand with stored `rail` must expand and store `expanded`; a later collapse must store `rail` and survive the pane closing and a reload. → Task 4 tests `expand while auto-collapsed and stored rail stays expanded`, `open pane → expand → collapse → close keeps the rail`.
2. **`N` with a modal, the accounting sheet or a dirty form** — `N` inside a PrimeNG / nested dialog or on the entry form must not navigate; `N` with focus inside the 760–1023 px accounting detail sheet must still open the form (it did before). → Task 7 `global-shortcuts.spec.ts` + shell integration tests.
3. **Every non-system group hidden** — tab bar must still render "+" alone and the gear must appear; sidebar shows only 系統; logo goes to `/settings`. → Task 2 and Task 4 tests.
4. **On a hidden group's route** (e.g. `/` with 物資 hidden) and on phone drill-down routes — no crash, exactly one `h1`, list pages still show their sub-nav. → Task 5 test `hidden group route still shows its sub-nav`, Task 6 route matrix at 390 px.
5. **Badge source failure, slow or out-of-order responses** — one failing request must not hide the others; an older response landing after a newer one must not overwrite it; a focus refresh within 30 s must not refire. → Task 3 async tests.

---

### Task 1: NavigationPreferencesService

**Files:**
- Create: `frontend/src/app/services/navigation-preferences.service.ts`
- Modify: `frontend/src/app/components/shell/navigation.ts:1` (add `'system'` to `NavGroupId`, add `SidebarDefault`; the valid-group list in the service is derived from `NAV_GROUPS` ids so Task 2's rename needs no change here)
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
  - `ignores corrupt or unknown stored values`: `hh-nav-hidden` = `'nope'` → default; `'{"a":1}'` and `'"portfolio"'` (valid JSON, not an array) → default; `'["system","portfolio","bogus"]'` → `['portfolio']`; `hh-nav-sidebar` = `'wide'` → `'expanded'`.
  - `blocked storage keeps in-memory state`: `vi.spyOn(Storage.prototype, 'getItem')` and `'setItem'` throwing → service initialises with defaults, `setSidebarDefault('rail')` does not throw and `sidebarDefault()` is `'rail'`.
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
- Item ids: `accounting-timeline` (count, `提醒中心 {n} 項`), `accounting` (count, `{n} 筆待確認`), `import` (dot, `最近一次匯入失敗`). Count 0 → no entry. No `dividends` source (Decision 2).

- [ ] **Step 1: Write failing tests.**
  - `reminder-count.spec.ts` — `counts 1 open bill + 1 counterparty + 2 due queue items = 4`: dues = two due events whose `bill()` returns remaining 500 and 0; counterparties `open_count` 2 and 0; queue `{is_partial:true, due_date: tomorrow}`, `{is_partial:false, due_date: today}`, `{is_partial:false, due_date: tomorrow}` → `4`. Plus `unresolved bill (null) is not counted`.
  - `import-review-count.spec.ts`: `null` → 0; `{summary:{needs_review:{count:3}}}` → 3; `{summary:{unpaired_transfers:[1,2]}}` → 2; needs_review wins over unpaired.
  - `nav-badge.service.spec.ts` (stub `AccountingService`, `PortfolioService` with `Subject`s the test completes by hand, so ordering is controlled; real `BillingService` with a stubbed `AccountingService` where the billing case needs it; fake timers):
    - `failed recalc chain → import dot {dot:true,label:'最近一次匯入失敗'}` (`state:'failed'`); `state:'completed'` → no `import` key; `kind:'quotes', state:'failed'` → no dot.
    - `latest import with 1 needs_review → accounting {count:1,label:'1 筆待確認'}`; latest import 404 → no key.
    - `a failing source hides only its own badge`: `getCounterparties` errors → no `accounting-timeline` key, `accounting` badge still present, no throw.
    - `refresh within 30 s is skipped, after 30 s refires` (count calls to `getRecalcStatus`).
    - `start() twice registers one focus listener` (dispatch `focus` on window after 31 s → one extra call).
    - `older response landing after a newer one is dropped`: refresh #1 (recalc pending), advance 31 s, refresh #2; complete #2 with `completed`, then #1 with `failed` → no `import` key. Same for a newer *failure* after an older success: #2 errors, then #1 succeeds with `failed` → still no key (the newer round decided).
    - `slow first load across the 30 s window`: #1 still pending at 31 s, focus → #2 starts; both land → one consistent result from #2.
    - `stale accounts snapshot never calls ensure`: refresh #1 accounts response arrives after refresh #2's → `BillingService.ensure` called only with #2's dues (spy).
    - `timeline and badge agree`: with one card owing and one counterparty open, the badge count equals `countReminders` over the same inputs (guards the shared extraction).

- [ ] **Step 2: Run** each spec → FAIL.

- [ ] **Step 3: Implement.** Move the body of `timeline.ts` `reminderCount` into `countReminders` and the body of `accounts.ts` `reviewCount` into `importReviewCount`; both components call them (behaviour unchanged — their existing specs must stay green). The three terms are exactly `timeline.ts:482,515`: bills with `(bill?.remaining ?? 0) > 0`, counterparties with `open_count > 0` (counted, not summed), queue items with `is_partial || due_date <= today`. `NavBadgeService`: each source (reminder inputs, latest import, recalc status) has its own `catchError(() => of(null))` → null removes that item's badge, and its own request generation: a response whose generation is not the source's latest is dropped (older success after newer failure included). Reminder inputs: `getAccounts()`, `getCounterparties()`, `getScheduleInstances({ queue: true })`; only the latest generation's accounts call `bills.ensure(reminderDues(accounts, today).filter(e => e.kind === 'due'), today)`; the badge is a `computed` over those signals and `bills.bill`, so it updates when statements land (`BillingService` already dedupes by key + generation + fingerprint and drops superseded rounds, `billing.service.ts:93,129`). Recalc via `PortfolioService.getRecalcStatus`, import via `AccountingService.getLatestImport` (404 → null like `accounts.ts importLookup`).

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
    readonly sheetOpen: WritableSignal<boolean>;  // the 760–1023 px modal detail sheet is open
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
    - `open pane → expand → collapse → close keeps the rail`: wide, stored expanded, pane opens (rail), toggle (expanded), toggle (rail, prefs `sidebarDefault()` `'rail'`), pane closes → still `'rail'`; fresh service instance (reload) → `'rail'`.
    - `override resets through the real lifecycle`: pane open, toggle (expanded), `paneOpen.set(false)`, `TestBed.flushEffects()`, `paneOpen.set(true)`, flush → `'rail'`.
    - `direct load of a pane route starts as the rail` (paneOpen true before the service is first injected → `'rail'`).
    - `badges off hides every badge` (`prefs.setShowBadges(false)` → `badges()` `{}`).
    - `active group never folds` (`toggleGroup('accounting')` on `/accounting` → `isFolded('accounting')` false, prefs unchanged).
  - `sidebar.spec.ts` (render at wide/narrow; provide `NavBadgeService` stub with a writable `badges` signal):
    - `expanded at 1440 with headings and labels`: host has class `expanded`; headings `['投資','財務','系統']` (物資 hidden by default); item texts include `投資概覽`.
    - `rail at 1100 shows a tooltip per item`: class `rail`; each `a.side-item` has `aria-label` equal to its sidebarLabel (plus `，{badge label}` when badged) and a `.side-tip` containing the label.
    - `active item marked`: on `/portfolio/transactions` the `[aria-current="page"]` item text is `交易紀錄`.
    - `folded group shows sum and dot`: fold 財務 while on `/portfolio` with badges `accounting-timeline` 2 + `accounting` 1 → 財務 heading `aria-expanded="false"`, `.group-count` `3`; fold 投資 while on `/accounting` with the `import` dot → 投資 `.group-dot` present.
    - `active group shows 目前 and stays open on click`.
    - `collapse button stores rail` (`收合側欄` click → prefs `sidebarDefault()` `'rail'`); no toggle button rendered at 1100.
    - `logo links to the first visible group` (`/portfolio` by default; `/settings` with 物資, 投資, 財務 hidden).

- [ ] **Step 2: Run** both specs → FAIL.

- [ ] **Step 3: Implement.** `paneOverride` is a private signal in `NavigationStateService`, reset by an `effect` when `paneState.paneOpen()` becomes false. `toggleSidebar()`: when current mode is `rail` → if stored is `rail` set it `expanded`; if pane open and autoCollapse set override `true`. When `expanded` → `setSidebarDefault('rail')` and override `false` (a collapse is always the owner's stored choice, rev 2). Sidebar markup and spacing follow `design_handoff_v2/design_refs/Main.dc.html` (sidebar block) and `SidebarStates.dc.html`: expanded = logo lockup `assets/logo-lockup.svg` + collapse button, group heading buttons (`aria-expanded`, chevron or `目前`, folded sum pill + dot), items (40px row, 32px icon square, label, right-aligned count pill or dot); rail = `assets/app-icon.svg`, expand button (wide only), divider per group, 44px icon buttons with count/dot overlaid and `.side-tip` shown on `:hover` and `:focus-visible`. Keep the dock's own-scroll-container rules (`overflow-y: auto; overscroll-behavior: contain`) and its compact-height density (`host` class `compact` from `layout.compactHeight()`). Position fixed, full height, `z-index: 1000`, `width: var(--sidebar-w)` where the host sets `--sidebar-w` to `248px` / `72px`.

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
  - `sub-nav.spec.ts`: on `/accounting` → links `記錄/帳戶/記帳設定` with hrefs `/accounting`, `/accounting/accounts`, `/accounting/settings` and `aria-current="page"` on 記錄 (moved from mobile-nav.spec); with badge `accounting:{count:1,label:'1 筆待確認'}` the 帳戶 link shows `1` and its `aria-label` is `帳戶，1 筆待確認`; on `/portfolio` → chips `概覽/交易/股利/已實現/現金/匯入`, with `import:{dot:true,label:'最近一次匯入失敗'}` the 匯入 chip has `.sub-dot` and `aria-label` `匯入，最近一次匯入失敗`; badges off → no count or dot; `hidden group route still shows its sub-nav` (on `/`, 物資 hidden → links `庫存/採買`); on `/settings` → nothing rendered.
  - `mobile-nav.spec.ts`: default prefs → slots `['投資','+','財務']`; all visible → `['物資','投資','+','財務','設定']`; "+" `href="/accounting/entry"` and `aria-label="新增記錄"` (kept); `dot on a non-active tab only`: on `/accounting` with the `import` dot → 投資 tab has `.tab-dot`; on `/portfolio` with `accounting` count → 財務 tab has a dot and 投資 none; badges off → no dots.

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
- `components/accounting/accounts/accounts.html` — replace `<h2>帳戶</h2>`; the `+` 新增帳戶 link becomes the header action in the soft-primary style (`.add-account` restyled: `--app-primary-soft` bg, `--app-primary` text)
- `components/accounting/accounting-settings/accounting-settings.html` — replace `<h2>記帳設定</h2>`
- `components/portfolio/accounts/accounts-list.html:25-30` — the 新增帳戶 `p-button` becomes `<app-btn variant="soft" icon="pi-plus">新增帳戶</app-btn>`
- Drill-down routes (Decision 8; title `h2` → `h1`, same classes, SCSS selector `h2` → `h1` so the look is unchanged): `components/accounting/entry-form/entry-form.{html:5,scss:51}`, `account-settings/account-settings.{html:4,scss:8}`, `account-entries/account-entries.{html:4,scss:9}`, `statements/statement-list/statement-list.{html:11,scss:16}`, `statements/statement-detail/statement-detail.{html:11,scss:16}`, `reminders/reminders.{html:6,scss:17}`; `entry-detail/entry-detail.html` and `split-group/split-group.html` add `<h1 class="visually-hidden">記錄明細</h1>` / `<h1 class="visually-hidden">多類別</h1>` as the section's first child (Bootstrap's `.visually-hidden` is global via `styles.scss:2`)
- `components/ui/btn/btn.ts` (+ `btn.scss`, `btn.spec.ts`): `BtnVariant` adds `'soft'` → class `btn-soft` (`--app-primary-soft` background, `--app-primary` text, no shadow)
- Each touched component's `imports` gains `PageHeaderComponent`.

**Interfaces:**
- Consumes: `PageHeaderComponent` (Task 5).

- [ ] **Step 1: Write failing tests.**
  - `btn.spec.ts`: `variant="soft"` renders `button.btn-soft`.
  - `app.routes.spec.ts` — new block `phone route matrix (390 px)`: with `stubMatchMedia` answering `PHONE_QUERY` true, `provideHttpClientTesting()` (pending requests left unanswered — headers do not depend on data), `RouterTestingHarness` navigate to every URL and assert exactly one `h1` with the expected text, plus gear / sub-nav presence:
    - list-level (h1 · gear `a[aria-label="設定"][href="/settings"]` · active sub-nav link): `/` 庫存管理 (hidden supplies route), `/shopping-list` 採買清單, `/portfolio` 投資概覽, `/portfolio/transactions` 股票交易紀錄, `/portfolio/dividends` 股利領取紀錄, `/portfolio/realized-pnl` 已實現損益, `/portfolio/accounts` 現金帳戶, `/portfolio/accounts/5` 現金帳戶, `/portfolio/import` 匯入 CSV, `/portfolio/import-broker` 匯入券商對帳單, `/accounting` 記帳記錄, `/accounting/accounts` 記帳帳戶, `/accounting/settings` 記帳設定; `/settings` 設定 (gear yes, no sub-nav).
    - drill-down (h1 only, no gear, no sub-nav): every other URL in the existing `PAGES` table (`app.routes.spec.ts:15-31`); h1 texts `新增帳戶` (`/accounting/accounts/new`), `帳戶資訊`, the passbook's `帳戶 #5` fallback, `對帳帳單`, `對帳明細`, `記錄明細`, `多類別`, the entry form's `title()` (`entry-form.ts:545`: `新增記錄` for `/accounting/entry`, `編輯記錄` for `/accounting/entries/9/edit`), `提醒中心`.
    - Same matrix at 1440 px (wide): list-level pages one `h1`, no gear, no sub-nav. On accounting pane URLs the list's `h1` plus the pane page's `h1` may both render; assert ≥ 1 and that the list title is present.
  - `transaction-list.spec.ts`: `新增交易` is an `app-btn` rendering `.btn-soft`; no element with text `記一筆` in the page. `accounts-list.spec.ts` / `accounts.spec.ts`: 新增帳戶 uses the soft style.

- [ ] **Step 2: Run** the touched specs → FAIL.

- [ ] **Step 3: Implement.** Create actions (新增物品, 新增交易, 新增股利, both 新增帳戶) use the soft style. No 記一筆 button exists in any page header today (verified: `grep -rn 記一筆 --include='*.html'` is empty) — task 1.9 of the change is satisfied by the test above.

- [ ] **Step 4: Run** full `npx ng test --watch=false` → PASS (page specs with `.page-head` selectors updated where they relied on them).

- [ ] **Step 5: Commit** `feat(pages): every routed page owns its h1 via app-page-header; soft create buttons`

---

### Task 7: Shell — mount the sidebar, remove the header, app-wide 記一筆

**Files:**
- Modify: `frontend/src/app/components/shell/shell.{ts,html,scss,spec.ts}`
- Create: `frontend/src/app/components/shell/global-shortcuts.ts` (+ spec)
- Modify: `frontend/src/app/components/accounting/keyboard-shortcuts.ts` (remove the `n`/`N` case) and `keyboard-shortcuts.spec.ts` (lines 12, 14, 39, 58–62, 73: `n` now returns `null` from `resolveShortcut`; move the positive cases to `global-shortcuts.spec.ts`)
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.{ts,html,scss,spec.ts}`: remove `showFab` and the `.fab` link/styles; publish `PaneStateService` (`paneOpen = mode()==='panes' && paneOpen()`, `formOpen = formOpen()`, `sheetOpen = sheetOpen()`; all reset to `false` on destroy); `setOutsideInert` queries `app-sidebar` instead of `app-dock`; the detail-pane `<section>` (`accounting-layout.html:15-24`) gets `data-global-shortcuts` so `N` still works inside the sheet; `.dbody` height `calc(100dvh - var(--page-pad-y) - var(--page-pad-b))`
- Modify: `frontend/src/app/app.routes.spec.ts:155-170` (`opens the entry form with N from the timeline`): the harness has no shell, so after the move it has no `N` handler. Rewrite it to render `ShellComponent` (`TestBed.createComponent(ShellComponent)` with `provideRouter(routes)`, navigate `/accounting` via `Router`), keep the same `vi.waitFor` assertions (URL `/accounting/entry`, `app-entry-form` rendered). Keep the `⇧⏎` / Esc test as is (still the layout's).
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
  - `global-shortcuts.spec.ts`: `n` and `N` (shift allowed) on `/portfolio`, `/settings`, `/accounting`, `/accounting/entries/9`, `/accounting/accounts/5`, `/accounting/reminders`, `/accounting/accounts/5/settings` → navigate; `null` for targetTag `INPUT`/`TEXTAREA`/`SELECT`, any of ctrl/meta/alt, `inDialog: true`, url `/accounting/entry` and `/accounting/entries/9/edit`; other keys `null`.
  - `shell.spec.ts` (keep the three compact-height tests; add, with fake `MATCH_MEDIA` per width and stub `NavBadgeService`):
    - `no frosted header`: no `header.hub-header`, no `.hub-logo` in the shell.
    - `floating button on every route ≥760`: `a.hub-fab` `href="/accounting/entry"`, `aria-label="記一筆（快捷鍵 N）"`, tooltip text `記一筆` + `kbd` `N`; absent at phone width; absent when `PaneStateService.formOpen` is `true`; has class `beside-pane` when `paneOpen` is `true`.
    - `N on /portfolio opens the entry form` (dispatch `keydown` `n` on `document.body` → router navigated to `/accounting/entry`); `N handled elsewhere is ignored` (`preventDefault()`ed event → no navigation).
    - Integration with real routes (`provideRouter(routes)`, shell + accounting layout on one document): `820 px: N inside the detail sheet opens the form` (open `/accounting/entries/9`, focus the sheet's ✕, dispatch `n` from it → `/accounting/entry`); `N inside a nested dialog does nothing` (append `<div role="dialog"><button>` to `document.body`, dispatch from the button → URL unchanged); `N on a dirty entry form is ignored` (on `/accounting/entries/9/edit` with focus on body → URL unchanged); `E`, ↑/↓ and ⇧⏎ still reach the layout (one each, unchanged behaviour).
    - `FAB is hidden while the 820 px sheet is open` (`sheetOpen` true → no `a.hub-fab`), and returns when the sheet closes; focus returns to the opener as before (existing layout focus test keeps passing).
    - `main offset follows the sidebar`: `.hub-layout` has `sidebar-expanded` at wide, `sidebar-rail` otherwise.
    - `starts the badge refresh once`.
  - `accounting-layout.spec.ts`: delete the FAB tests at lines 93 and 215–217 (moved to shell); update `SCREEN` selector at line 47 (no `.fab` sibling); change `app-dock` to `app-sidebar` at lines 551 and 690; add `publishes pane and form state` (open `/accounting/entries/9` at panes → `paneOpen()` true; `/accounting/entry` → `formOpen()` true; destroy → both false); the line 362 test (handled `n` ignored) moves to shell.

- [ ] **Step 2: Run** the four specs → FAIL.

- [ ] **Step 3: Implement.** `shell.html`: `<app-sidebar>`, `.hub-main` (`margin-left: var(--sidebar-w)` with `--sidebar-w` set from `nav.sidebarMode()`; 0 on phones; `transition: margin-left var(--dur-base) var(--ease-standard)`), `<main class="hub-page"><router-outlet/></main>`, the FAB, `<app-mobile-nav>`. FAB rendered when `mode() !== 'phone' && !paneState.formOpen() && !paneState.sheetOpen()`; `--sidebar-w` is set on `.hub-layout` (common ancestor of `.hub-main` and the FAB). FAB: 56px circle, `--app-primary`, `#fff` "+", `box-shadow: 0 8px 20px rgba(83, 58, 253, 0.35)` (current `.fab`), `position: fixed; right: 30px; bottom: 30px; z-index: 1000`; tooltip shown on `:hover`/`:focus-visible`. `.beside-pane` (panes mode, pane open) moves it to the list column's bottom-right: `right: calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) * 6 / 11 + 32px)` (`.hub-page` side padding 1.75rem, `.dbody` gap 16px, columns 5fr / 6fr). Shell host `(document:keydown)`: skip `isHandledKey`; `targetTag` computed as in `AccountingLayoutComponent.onShortcutKey`; `inDialog = !!target?.closest('[role="dialog"]:not([data-global-shortcuts]), [aria-modal="true"]:not([data-global-shortcuts])')` (the accounting sheet opts in; PrimeNG and other dialogs block); on a match `preventDefault()` then `registry.requestClose(() => router.navigate(['/accounting/entry']))`. Phone `.hub-main` bottom padding: `calc(80px + env(safe-area-inset-bottom))` (sub-nav left the bottom bar). Delete the dock folder.

- [ ] **Step 4: Run** full `npx ng test --watch=false` and `npm run build` → PASS / build succeeds.

- [ ] **Step 5: Commit** `feat(shell): sidebar mounted, header removed, app-wide 記一筆 button and N`

---

### Task 8: Verification and change bookkeeping

**Files:**
- Modify: `openspec/changes/redesign-frontend-v2/tasks.md` (tick 1.1–1.10 that this PR completes)
- Create: `~/reports/home-hub-redesign-v2-shell-<UTC>/` screenshots (outside the repo)

- [ ] **Step 1:** `cd frontend && npx ng test --watch=false` → all files pass; `npm run build` → success. Record counts.
- [ ] **Step 2:** Breakpoint boundary tests in `layout-mode.service.spec.ts` (fake `MATCH_MEDIA` evaluating the real query strings against a width): 759 → phone, 760 → sheet; 1023 → sheet, 1024 → panes; 1279 → `wideSidebar()` false, 1280 → true. Run → PASS; commit with Step 1's fixes if any.
- [ ] **Step 3:** `npx @fission-ai/openspec validate redesign-frontend-v2 --strict` → valid.
- [ ] **Step 4:** Screenshots against the owner's demo preview stack (fictional data, 127.0.0.1:18080, set up by the owner for agent checks — the operator confirms it is up and the one in use; never the production stack) at 390, 820, 1280, 1440 px, light and dark, for the full route matrix of Task 6 (list-level and drill-down URLs, with real ids from the demo data) plus `/accounting/entries/<id>` at 1280 / 1440 (auto-collapse, FAB beside the pane). Check: every page has its title, FAB never covers the pane and is hidden behind the 820 px sheet, tab bar shows `投資 · ＋ · 財務` with a gear on list pages on phones.
- [ ] **Step 5:** Tick tasks 1.1–1.10 in `tasks.md` (1.2's badge set noted as partial: 股利 count and device-local import flag are owner exceptions, Decisions 2 and 5); commit `docs(openspec): tick redesign-frontend-v2 shell tasks`.
