# Redesign v2 — PR 2 (Appearance `system` + Settings page) Implementation Plan (rev 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `light | dark | system` theme to `AppearanceService` (with the pre-paint script and the one-time `hh-dark` migration) and rebuild `/settings` into the six-section page of the spec, including the 導覽 controls for the navigation preferences PR 1 introduced.

**Architecture:** `AppearanceService` keeps its RxJS shape (`BehaviorSubject`s) and gains `theme$` + `setTheme`; `dark$` becomes derived from `theme$` and a live `prefers-color-scheme` listener. The settings page stays one standalone component, split into small presentational row markup; navigation switches write straight to `NavigationPreferencesService` (PR 1), so the sidebar and tab bar update without reload. A new `app-switch` UI primitive replaces ad-hoc toggles on this page.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest via `@angular/build:unit-test`, SCSS with the existing `--app-*` tokens.

**Spec:** `openspec/changes/redesign-frontend-v2/design.md` (D6, D7, D8) and `specs/frontend-appearance-service/spec.md`, `specs/frontend-settings/spec.md`. Visual reference: `design_handoff_v2/design_refs/SettingsDesktop.dc.html`, `SettingsPhone.dc.html`.

**Worktree:** `~/workspace/home-hub-redesign`, branch `feat/redesign-v2-appearance` from `origin/main` b927072 (PR 1 merged). Dummy root `.env` present. Single spec: `cd frontend && npx ng test --watch=false --include <spec>`; full suite 110 files / 1449 tests at b927072.

**Rev 2 (AGENT-206, dev-grok, CHANGES REQUIRED at 6f4a75b):** Musts fixed — one storage rule shared by the service and the pre-paint script (invalid `hh-theme` → `system`, `hh-dark` read only when `hh-theme` is absent) and an agreement table that gives each actor its own storage copy (T1/T2); ≥ 760 means `mode() !== 'phone'` (T4); back uses the router's first-navigation id, not `history.length` (T5); spec amendments for 每天 15:30 and the `/settings` gear/highlight exceptions (T6). Shoulds folded — old `hh-dark` / `setDark` assertions replaced, 即時預覽 and two-column shortcuts asserted, back hidden when its fallback is the current page.

## Not in this PR

- Portfolio / accounting page redesigns (PRs 3–7).
- New design tokens (`frontend-design-system` unchanged). The README's contrast notes stay task 8.3 of the change.

## Decisions this plan makes (code facts checked at b927072)

1. **淨值快照 schedule reads 每天 15:30**, not the spec's 每個交易日 15:30: `portfolio_snapshot` is `CronTrigger(hour=15, minute=30)` with no day-of-week (`services/stock-portfolio-service/app/services/scheduler.py:337`). 盤中報價 is `minute="*/15", hour="9-13", mon-fri` gated to the session (`:330`, docstring `:8-11`) → 交易時段每 15 分鐘; 收盤價回補 is `hour=17, mon-fri` (`:323`) → 每個交易日 17:00.
2. **Last run** is shown only for 盤中報價, from `RecalcStatus.quote_refresh.finished_at` (`frontend/src/app/models/portfolio.model.ts:176-179`, `PortfolioService.getRecalcStatus()` `services/portfolio.service.ts:222`), formatted `HH:mm` in Asia/Taipei; the row shows the schedule alone when the field is absent or the request fails.
3. **Settings on phones is a pushed screen**: a ‹ back button precedes the `h1` 設定 — `Location.back()` when the current navigation is not the router's first one (navigation id > 1), else navigate to `landingPathFor(hiddenGroups)`; the button is hidden when that fallback is `/settings` itself (every hideable group hidden, first navigation). The tab bar highlights nothing on `/settings`, and the page header shows no gear there (it would link to itself). Both are exceptions to `frontend-app-shell` ("active route highlighted", "every page header shows a gear"); Task 6 records them in that spec.
4. **Switch control**: new `SwitchComponent` (`app-switch`, `components/ui/switch/`) — `<button role="switch" aria-checked>`, 52×32 px, track `--app-surface-soft` off / `--app-primary` on, white thumb; the accounting `.tog` toggles are left untouched.
5. **`setDark(value)` stays** (spec keeps it) and maps to `setTheme(value ? 'dark' : 'light')`.

## Global Constraints

- localStorage keys: `hh-theme` (`'light' | 'dark' | 'system'`), `hh-gainloss` (`'asian' | 'western'`). **One resolution rule, used by both the service and the pre-paint script:** a valid `hh-theme` wins; a present but invalid `hh-theme` means `system` (OS) and never falls through to `hh-dark`; only when `hh-theme` is absent is `hh-dark` read (`'1'` dark, `'0'` light, anything else → `system`). The service then migrates once (only when `hh-theme` was absent and `hh-dark` was `'1'`/`'0'`): write `hh-theme`, remove `hh-dark`. The script never writes. Unreadable storage → `system`.
- Root effects unchanged: class `app-dark-mode` on `document.documentElement`, attribute `data-gainloss`.
- Settings copy (verbatim): sections 外觀 / 投資顯示 / 導覽 / 記帳 / 鍵盤快捷鍵 / 資料更新; 主題 row description `跟隨系統時，會依裝置的深淺色自動切換`, options 淺色 / 深色 / 跟隨系統; 導覽 rows 顯示的分組 (switches 物資, 投資, 財務; 系統 `固定顯示`), 桌面側欄預設 (展開 / 圖示欄, note `較窄的螢幕一律使用圖示欄`), 開啟明細時自動收合側欄, 顯示待處理數字與紅點; 記帳設定 row description `類別、帳戶分組、專案、對象、支出收入顏色、數字鍵盤、匯入 MOZE 備份`; shortcuts N 記一筆, E 編輯目前的記錄, ↑ ↓ 上一筆／下一筆, Enter 儲存, ⇧ Enter 儲存並續記, Esc 關閉明細或表單 (each key a `<kbd>`); jobs 盤中報價 (交易時段每 15 分鐘), 淨值快照 (每天 15:30), 收盤價回補（TWSE / TPEx）(每個交易日 17:00).
- Phone tab-bar preview sentence when 設定 leaves the tab bar: `設定移到每頁右上角的齒輪，讓「＋」維持在中間。`
- Breakpoint: "≥ 760 px" means `LayoutModeService.mode() !== 'phone'` (both `sheet` 760–1023 and `panes` ≥ 1024).
- Layout: content column ≤ 860 px; each row = icon square + title + one-line description + control on the right; < 760 px controls stack under the title at full width; section headings small, uppercase-tracked (`--tracking-label`). Colours from existing tokens only.
- Keep 漲跌顏色 row and its 即時預覽 chips as today.

## Review Focus

1. **Migration edge cases** — `hh-dark` present with a garbage value, `hh-theme` garbage, storage blocked: fall back to `system`, never throw, never remove `hh-dark` unless `hh-theme` was written. → Task 1 tests.
2. **Live OS switch while on `system`, then the owner picks `light`** — the listener must stop affecting the root (no flip back to dark on the next OS change). → Task 1 test.
3. **Pre-paint vs service disagreement** — first paint and the service must pick the same mode for every stored combination (`hh-theme` x `hh-dark` x OS). → Task 2 table test that runs the real inline script.
4. **Hiding a group while on one of its routes, or hiding every hideable group** — the switch must not strand the owner: sidebar/tab bar update immediately, Settings stays reachable (gear rule). → Task 4 test.
5. **Back button with no history** (Settings opened from a bookmark on a phone) — must not leave the app. → Task 5 test.

---

### Task 1: AppearanceService theme

**Files:**
- Modify: `frontend/src/app/services/appearance.service.ts`
- Test: `frontend/src/app/services/appearance.service.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  export type ThemeMode = 'light' | 'dark' | 'system';
  export class AppearanceService {
    readonly theme$: BehaviorSubject<ThemeMode>;
    readonly dark$: BehaviorSubject<boolean>;            // effective, derived
    readonly gainLoss$: BehaviorSubject<GainLossConvention>;
    setTheme(value: ThemeMode): void;
    setDark(value: boolean): void;                       // = setTheme(value ? 'dark' : 'light')
    setGainLoss(value: GainLossConvention): void;
    initialize(): void;                                  // kept
  }
  ```

- [ ] **Step 1: Write the failing tests** (existing `setMatchMedia` helper extended to return a controllable `MediaQueryList` with `addEventListener('change', …)`):
  - `setTheme('dark')` → `theme$` `'dark'`, `dark$` `true`, root has `app-dark-mode`, `localStorage['hh-theme']` `'dark'`.
  - `system follows the OS live`: OS light, `setTheme('system')` → `dark$` false; fire change with `matches: true` → `dark$` true, root class present.
  - `leaving system detaches the listener`: on `system`, `setTheme('light')`, fire OS dark → `dark$` stays false.
  - `migrates hh-dark once`: `hh-dark='0'`, no `hh-theme` → `theme$` `'light'`, `hh-theme` `'light'`, `hh-dark` removed; same for `'1'` → `'dark'`.
  - `hh-theme wins over hh-dark`: both stored → `hh-theme` used, `hh-dark` left as is.
  - `defaults to system`: nothing stored, OS dark → `theme$` `'system'`, `dark$` true.
  - `garbage values`: `hh-theme='blue'` (with `hh-dark='1'`, OS light) → `system` → light, `hh-dark` untouched, nothing written; `hh-dark='x'` with no `hh-theme` → `system`, `hh-dark` not removed.
  - `blocked storage`: `getItem`/`setItem` throw → defaults, `setTheme('dark')` works in memory.
  - `re-entering system follows the OS again`: on `system`, `setTheme('light')`, `setTheme('system')`, fire OS dark → `dark$` true.
  - `setDark(true)` → `theme$` `'dark'` and `localStorage['hh-theme']` `'dark'`. **Replace** the old assertion `localStorage.getItem('hh-dark') === '1'` after `setDark(true)` (`appearance.service.spec.ts:38`) and retire the old restore test (`:42-51`) whose migration check the new `migrates hh-dark once` test supersedes; gain/loss tests keep passing.

- [ ] **Step 2: Run** `--include src/app/services/appearance.service.spec.ts` → FAIL.
- [ ] **Step 3: Implement.** One `matchMedia('(prefers-color-scheme: dark)')` list held by the service; its `change` listener updates `dark$` and the root only while `theme$` is `system`. Storage access stays wrapped like today.
- [ ] **Step 4: Run** → PASS; run the settings spec too (it stubs the service; update its stub shape only if compilation needs it).
- [ ] **Step 5: Commit** `feat(appearance): light / dark / system theme with hh-theme and hh-dark migration`

---

### Task 2: Pre-paint script reads `hh-theme`

**Files:**
- Modify: `frontend/src/index.html` (inline script, lines ~14-40)
- Test: `frontend/src/app/services/pre-paint.spec.ts` (create)

- [ ] **Step 1: Write the failing test.** `readFileSync(join(process.cwd(), 'src/index.html'))` (the suite runs from `frontend/`), extract the first inline `<script>` body, run it as `new Function('window', 'document', body)(stubWindow, document)` so the stub `localStorage` / `matchMedia` are the ones it sees. Table over `hh-theme ∈ {absent,'light','dark','system','blue'}` × `hh-dark ∈ {absent,'0','1','x'}` × OS `{light,dark}`, plus one row where `getItem` throws: **each actor gets its own fresh copy of the initial keys** (the service migrates and would change what the script reads); the root's `app-dark-mode` after the script equals `dark$` of a service built on the other copy (reset TestBed per row). Also `data-gainloss` for `hh-gainloss` absent / `'western'`.
- [ ] **Step 2: Run** → FAIL (script ignores `hh-theme`).
- [ ] **Step 3: Implement** the Global Constraints resolution rule exactly (invalid `hh-theme` → OS, no fall-through; `hh-dark` only when `hh-theme` is absent; `hh-dark` other than `'1'`/`'0'` → OS). Keep the existing try/catch around `localStorage` and `matchMedia` (`index.html:18-30`). The script never writes storage.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(appearance): pre-paint script reads hh-theme first`

---

### Task 3: Switch primitive

**Files:**
- Create: `frontend/src/app/components/ui/switch/switch.ts` (+ `switch.scss`, `switch.spec.ts`)

**Interfaces:**
- Produces: `SwitchComponent` (`app-switch`): inputs `checked = input(false)`, `disabled = input(false)`, `ariaLabel = input.required<string>()`; output `checkedChange = output<boolean>()`.

- [ ] **Step 1: Failing tests**: renders `button[role="switch"]` with `aria-checked` mirroring `checked`, `aria-label`; click emits the negation; disabled → `disabled` attribute and no emit; Space / Enter act through the native button (assert one emit per click event).
- [ ] **Step 2–4:** run → FAIL, implement (52×32, tokens per Decision 4, focus ring `--app-focus-ring`, reduced-motion respects transition), run → PASS.
- [ ] **Step 5: Commit** `feat(ui): app-switch`

---

### Task 4: Settings page — sections, theme, 導覽, 記帳, shortcuts, 資料更新

**Files:**
- Modify: `frontend/src/app/components/settings/settings.ts` (move the inline template to `settings.html`), `settings.scss`, `settings.spec.ts`

**Interfaces:**
- Consumes: `AppearanceService` (Task 1), `SwitchComponent` (Task 3), `NavigationPreferencesService` (`hiddenGroups`, `setHidden`, `sidebarDefault`, `setSidebarDefault`, `autoCollapse`, `setAutoCollapse`, `showBadges`, `setShowBadges`), `NAV_GROUPS` + `buildTabBar` (`components/shell/navigation.ts`), `PortfolioService.getRecalcStatus()`, `LayoutModeService.mode()`, `PageHeaderComponent`, `SegToggleComponent`.

- [ ] **Step 1: Failing tests** (`settings.spec.ts`, real `NavigationPreferencesService` with cleared storage, stub `AppearanceService` with `theme$`, stub `PortfolioService`):
  - section headings in order `外觀, 投資顯示, 導覽, 記帳, 鍵盤快捷鍵, 資料更新` in mode `sheet` **and** in mode `panes`; in mode `phone` 鍵盤快捷鍵 absent and rows carry the stacked class. Gate on `mode() !== 'phone'`.
  - **Replace** the existing `setDark(true)` expectation (`settings.spec.ts:38`) with the `setTheme` assertions below.
  - theme seg shows 淺色 / 深色 / 跟隨系統 with `theme$` value selected; clicking 跟隨系統 calls `setTheme('system')`; 深色 → `setTheme('dark')`.
  - 漲跌顏色 still calls `setGainLoss('western')`, and the 即時預覽 chips (`上漲 +2.35%`, `下跌 -1.20%`) still render.
  - 導覽: three switches 物資 (off by default) / 投資 / 財務 (on) and 系統 showing `固定顯示` with no switch; toggling 物資 on → `hiddenGroups()` `[]`; 桌面側欄預設 圖示欄 → `sidebarDefault()` `'rail'`; 自動收合 off → `autoCollapse()` false; 徽章 off → `showBadges()` false.
  - `hiding every hideable group keeps Settings reachable`: switch 投資 and 財務 off (物資 already off) → `buildTabBar` result has `settingsInHeader` true and the preview (phone mode) shows only "+" plus the gear sentence.
  - phone mode, 物資 off: preview slots `投資, +, 財務` and the sentence `設定移到每頁右上角的齒輪，讓「＋」維持在中間。`; all visible → preview `物資, 投資, +, 財務, 設定` and no sentence.
  - 記帳設定 row is a link to `/accounting/settings` with the description verbatim.
  - shortcuts: six rows, keys rendered in `<kbd>`, labels verbatim, inside a container with the two-column grid class (`.shortcut-grid`; CSS `grid-template-columns: repeat(2, minmax(0, 1fr))`).
  - 資料更新: three rows verbatim; with `quote_refresh.finished_at = '2026-10-22T05:30:00Z'` the 盤中報價 row shows `13:30`; with the request failing no time is shown and no error is rendered.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per Global Constraints; reference `SettingsDesktop.dc.html` / `SettingsPhone.dc.html` for spacing.
- [ ] **Step 4: Run** settings spec + full suite → PASS.
- [ ] **Step 5: Commit** `feat(settings): six-section settings with system theme, 導覽, shortcuts and schedules`

---

### Task 5: Settings as a pushed screen on phones

**Files:**
- Modify: `frontend/src/app/components/shell/page-header/page-header.{ts,html,scss,spec.ts}` (optional `back` input), `frontend/src/app/components/mobile-nav/mobile-nav.{ts,spec.ts}` (no highlight on `/settings`), `frontend/src/app/components/settings/settings.html` (uses `back`)

**Interfaces:**
- Produces: `PageHeaderComponent.back = input(false)`; when true and phone mode, renders `<button class="page-back" aria-label="返回">‹</button>` before the `h1`. Click: if the current navigation id (`Router.getCurrentNavigation()`/last `NavigationEnd.id`) is greater than 1 → `Location.back()`; else navigate to `landingPathFor(prefs.hiddenGroups())` (`components/shell/navigation.ts`). Do **not** use `history.length` (a bookmark in a tab with history would leave the app). The button is not rendered when it would be a first navigation whose fallback is the current URL (`/settings` with every hideable group hidden). Gear hidden when the active item is `settings`.

- [ ] **Step 1: Failing tests**: page-header with `back` at phone mode renders the back button before the h1; not rendered in `sheet` / `panes`; after an in-app navigation to `/settings` (id > 1) click → `Location.back` called; on a first navigation to `/settings` click → router navigates to the landing path (`/portfolio` by default) and `Location.back` is not called; first navigation with 物資/投資/財務 hidden → no back button; gear absent on `/settings`. mobile-nav on `/settings` with all groups visible: the 設定 tab is not `.active` and has no `aria-current`.
- [ ] **Step 2–4:** run → FAIL, implement, run full suite → PASS.
- [ ] **Step 5: Commit** `feat(settings): pushed screen on phones with back button`

---

### Task 6: Verification and bookkeeping

- [ ] **Step 1:** full suite + `npm run build` pass; `npx @fission-ai/openspec validate redesign-frontend-v2 --strict` valid.
- [ ] **Step 2:** Screenshots (controller, Playwright in Docker against a preview build on 127.0.0.1:18081 — stop it before any SPA publish, the publisher probe uses that port): `/settings` at 390 / 820 / 1440, light / dark / system (emulate `prefers-color-scheme`); toggle 物資 on and confirm the sidebar shows 物資 without reload; first paint with `hh-theme=dark` has no light flash (screenshot at DOMContentLoaded).
- [ ] **Step 3:** spec amendments in the change: `specs/frontend-settings/spec.md` 淨值快照（每個交易日 15:30）→（每天 15:30）citing `scheduler.py:337`; `specs/frontend-app-shell/spec.md` one sentence each under "Active route highlighted" (no phone tab highlight on the pushed `/settings` screen) and "Hideable groups and tab bar parity" (the gear is omitted on `/settings` itself); `openspec validate --strict` valid.
- [ ] **Step 4:** tick tasks 2.1–2.4 in `openspec/changes/redesign-frontend-v2/tasks.md`; commit `docs(openspec): settings schedule and pushed-screen exceptions; tick appearance tasks`.
