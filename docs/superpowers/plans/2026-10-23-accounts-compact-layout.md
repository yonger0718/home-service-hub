# 現金帳戶 compact layout below 1024 px (#116) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the 現金帳戶 list the redesign cards at every width below 1024 px (sheet 760–1023 and phone < 760 both render the pre-redesign list today), and delete the pre-redesign list rendering.

**Architecture:** The page component already renders the master card list in panes mode; below 1024 px it renders the same list component in "master" mode, single column with the total card on top and a page header carrying 記錄入出金 / 新增帳戶, and a tap navigates to `/portfolio/accounts/:id` (the detail keeps its own page). No auto-select and no sidebar rail below 1024 px. The old `.accounts-page` template branch, its SCSS and its specs go.

**Tech Stack:** Angular 21 standalone/zoneless/signals + Vitest; `LayoutModeService` modes `phone` | `sheet` | `panes`.

**Spec:** GitHub issue #116. Canvas `design_handoff_v2/design_refs/PfCash.dc.html` is desktop-only (`:95-112` master column: `flex: 1 1 280px; max-width: 360px`, total card, 40 px square icon cards) — the single-column arrangement below 1024 px is this plan's decision (Decision 1). Code facts (survey 2026-10-23): `accounts-page.html:1-26` (`@if (panes())` master-detail, `@else if (routeId() === null)` old list, else detail), `accounts-page.ts:32` (`panes`), `:50-59` (auto-navigate only in panes), `:61` (`paneOpen` only in panes); `accounts-list.html:11` `@if (master())` / `:65 @else` (old list `:66-142`), `accounts-list.ts:122-126` (`master`, `selectedId`, `loaded`), `:147` `openCashForm`, `:185` `openCreateDialog`, `:231-233` `goToDetail` navigates; specs `accounts-page.spec.ts:307-336` (sheet/phone pin the old list), `accounts-list.component.spec.ts:13-190, :356-364`, `accounts-list.header.spec.ts`; `accounts-list.scss` 5.1 kB of 10 kB budget.

**Branch:** `fix/accounts-compact-layout` from `origin/main` eca0f57 (worktree `~/workspace/home-hub-batch2b`).

## Global Constraints

- Frontend only; no API change; SPA-only deploy.
- Panes mode (≥ 1024 px) is byte-identical in behaviour: master-detail, auto-select of the first active account, sidebar rail (`accounts-page.spec.ts:140-299` stays green untouched).
- Below 1024 px: no auto-navigation, `paneOpen` stays false, the detail page keeps its own header (`/portfolio/accounts/:id` renders `<app-portfolio-account-detail />` as today).
- The create dialog and the cash-flow form (`accounts-list.ts:147, :185`) keep working from the compact page (they live outside the `@if` in the list component).
- Copy: header title 現金帳戶, subtitle 券商交割戶與複委託帳戶的現金, buttons 記錄入出金 / 新增帳戶 (same as the panes header); total card 現金帳戶總額 · TWD with the overdraft footnote (unchanged from master mode).
- Specs restore `LayoutModeService.set(null)`; no `.only`; `accounts-list.scss` stays under budget (it shrinks).

## Decisions

1. **Compact page = header + master list, one column.** `accounts-page.html`: `@if (panes()) {…} @else if (routeId() === null) { <div class="accounts-compact"> <app-page-header title="現金帳戶" subtitle="…"> 記錄入出金 / 新增帳戶 </app-page-header> <app-portfolio-accounts-list [master]="true" (loaded)="onLoaded($event)" /> </div> } @else { <app-portfolio-account-detail /> }`. The header buttons call the same list methods the panes header calls (`openCashForm()` / `openCreateDialog()` through the existing `list = viewChild(PortfolioAccountsListComponent)` and `openCashForm()` / `openCreateDialog()` wrappers at `accounts-page.ts:40, :69-74`).
2. **Selection vs navigation.** With no `selectedId` input the cards are not `selected`/`aria-pressed`; `goToDetail` navigates (already does). `accounts-page.ts` auto-navigate and `paneOpen` effects are unchanged (gated on `panes()`).
3. **Width.** `accounts-page.scss`: `.accounts-compact .acct-master { max-width: 560px; margin-inline: auto; }` on sheet (`@media (min-width: 760px)`); full width on phone. The list's own `.acct-master` column styles (gap 12 px, cards) are reused as is.
4. **Delete the old list.** Remove `accounts-list.html:65-142` (the `@else` branch) and make `master` default to `true` (or drop the input and the `@if` entirely — Task 2 picks the smaller diff; keep `selectedId` optional); delete the old-list SCSS (`accounts-list.scss` lines for `.accounts-page`, `.accounts-header`, `.accounts-grid`, `.account-card*`, `.summary-value` — whatever is not referenced by the master markup), the old-list specs (`accounts-list.component.spec.ts:13-190`, `:356-364`) and `accounts-list.header.spec.ts`; drop PrimeNG `p-card`/`p-button` imports from the list component if they become unused.

## Review Focus

1. Sheet and phone at `/portfolio/accounts`: header + total + cards, no detail pane, no navigation on load, `paneOpen` false; tapping a card navigates to `/portfolio/accounts/:id` (Task 1 tests for both modes).
2. Panes behaviour unchanged (existing describe stays green without edits).
3. Switching from panes to sheet while on `/portfolio/accounts/7` (resize) keeps showing the detail page; switching to panes shows master-detail with 7 selected (Task 1 test).
4. 記錄入出金 and 新增帳戶 from the compact header open the same dialogs as in panes (Task 1 test spies the list methods).
5. No leftover references to deleted selectors (`grep -rn "account-card\|summary-value\|accounts-grid" frontend/src` → none outside unrelated components) and the style budget (Task 3).

---

### Task 1: Compact page rendering

**Files:**
- Modify: `frontend/src/app/components/portfolio/accounts/accounts-page.{ts,html,scss}`
- Test: `accounts-page.spec.ts:307-336` (rewrite the sheet/phone cases) + new cases per Review Focus 1, 3, 4

- [ ] **Step 1: Failing tests** — for `mode of ['sheet','phone']`: `/portfolio/accounts` renders `app-page-header` (title 現金帳戶, two buttons) + `.acct-master` + `.acct-total` + N `.acct-card`, no `app-portfolio-account-detail`, `router.navigate` not called, `paneOpen` false; clicking the first card navigates to `/portfolio/accounts/<id>`; `/portfolio/accounts/7` renders only the detail with its own header; header 新增帳戶 click → list `openCreateDialog` spy called; sheet `.acct-master` computed `max-width` 560px, phone none; resize flows per Review Focus 3.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include src/app/components/portfolio/accounts/accounts-page.spec.ts` → FAIL.
- [ ] **Step 3: Implement** Decisions 1–3.
- [ ] **Step 4: Run** → PASS (panes describe untouched and green).
- [ ] **Step 5: Commit** `feat(accounts): redesign card list below 1024 px with tap-to-detail (#116)`.

### Task 2: Delete the pre-redesign list

**Files:**
- Modify: `accounts-list.{ts,html,scss}`; delete `accounts-list.header.spec.ts`; edit `accounts-list.component.spec.ts`

- [ ] **Step 1: Failing/adjusted tests** — remove the old-rendering tests (`:13-190`, `:356-364`) and the header spec; keep the master-mode and `cashTotal` tests; add one test that the component renders the master markup without the `master` input (default true) and still accepts `selectedId`.
- [ ] **Step 2: Run** the list spec → FAIL on the default-master test.
- [ ] **Step 3: Implement** Decision 4; remove unused imports/styles.
- [ ] **Step 4: Run** list + page specs → PASS; `npx ng build --configuration production 2>&1 | grep -iE "error|budget"` → no new warning; `grep -rn "account-card\|summary-value\|accounts-grid\|accounts-header" frontend/src/app/components/portfolio/accounts` → none.
- [ ] **Step 5: Commit** `refactor(accounts): remove the pre-redesign account list (#116)`.

### Task 3: Verification

- [ ] **Step 1 (implementer):** `npx ng test --watch=false` (full) + `npm run build`.
- [ ] **Step 2 (controller):** Playwright on the preview (stubbed accounts fixture, fictional): `/portfolio/accounts` at 390 / 820 / 1280 × light / dark, plus `/portfolio/accounts/7` at 390 / 820; no page errors, no h-scroll; sheet cards centred ≤ 560 px; report dir `~/reports/home-hub-accounts-compact-<TS>/`.
- [ ] **Step 3 (controller):** PR with `Fixes #116`.

**Order:** 1 → 2 → 3.

## Deploy

SPA only (sandboxed build → prepare → owner approve → publish → live probe).

## Self-review

- Spec coverage: #116 → Tasks 1–2; verification → Task 3.
- Type consistency: `master` input default flips to `true` in Task 2 — the panes branch passing `[master]="true"` stays valid.
- Review Focus 1–5 each pinned.
