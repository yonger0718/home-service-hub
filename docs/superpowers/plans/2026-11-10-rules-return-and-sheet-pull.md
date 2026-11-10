# Rules Overview Return + Bottom Sheet Pull-Down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Arriving at account settings from the 回饋規則 overview returns to the overview, and phone bottom sheets close on a pull-down of the grab bar instead of refreshing the page.

**Architecture:** The overview links carry a `back` query parameter (its own URL); account settings validates it and uses it for ✕, save and the link-opened rule sheet. A new standalone `SheetPullDirective` on each `.grab` turns a downward pointer drag into a `pulled` output that every host wires to its existing close method; one global rule stops pull-to-refresh while a sheet is open.

**Tech Stack:** Angular 21 standalone components, signals, OnPush; Pointer Events; vitest (`cd frontend && npx ng test --watch=false [--include='<glob>']`).

**Spec:** GitHub issue #378 (owner report 2026-11-10). No openspec change: behaviour fixes inside shipped capabilities.

## Global Constraints

- Frontend only. No API change, no new route, no Caddy change.
- Copy stays zh-TW; no new visible text is added by this plan.
- Unsaved-input prompts keep working: every close goes through the host's existing close method or the router (the `DirtyFormRegistry` route-leave prompt), never around them.
- `back` is untrusted input: accepted only when it matches `^/accounting/reward-rules(\?[^#]*)?$`. Anything else is ignored (today's behaviour).
- Desktop popovers (`.acct-popover`, `.pk-popover`) and the 760–1023 px side drawer (`accounting-layout`) are out of scope.
- Theme gates (`frontend/src/app/theme/*.spec.ts`) stay green. Tests use fictional data.
- Commits end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

1. A hand-typed or stale `back` (`//evil.example`, `/accounting/accounts`, `https://…`, a value with `#`) must be ignored, not navigated to.
2. Arriving with `back` and then switching to another account inside settings (shared-limit member link, 所有卡片的規則 ›): `back` must not send the owner to a card that is no longer the one shown — the fragment uses the account shown when ✕ is pressed.
3. A drag that starts on the grab bar while the sheet is busy (rule save in flight) must not close it; the host's close method already refuses, and the sheet must spring back.
4. A sheet whose content is scrolled: only the grab bar starts a pull; scrolling the sheet body never moves or closes the sheet.
5. Closing the link-opened rule sheet while the account form itself is dirty must stay on settings (the owner has unsaved settings), not navigate away.

---

### Task 1: Return to the overview

**Files:**
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `reward-rules.html`, `reward-rules.spec.ts`
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts`, `account-settings.html`, `account-settings.spec.ts`

**Interfaces:**
- Produces (overview): `readonly backUrl = computed<string>()` — the overview's current router URL without fragment (path + query). The three links in `reward-rules.html` (rule row, `.rr-add`, `.rr-bare-chip`) add `back: backUrl()` to their `queryParams`. Each `<section class="rr-card">` gets `[id]="'card-' + card.account.account_id"` and `tabindex="-1"`.
- Produces (overview): after the overview data is first shown, a URL fragment `card-<id>` scrolls that section into view (`scrollIntoView({ block: 'start' })`) and focuses it (`focus({ preventScroll: true })`); a fragment with no matching card does nothing.
- Produces (settings): `export function safeBack(raw: string | null): string | null` in `account-settings.ts` — returns `raw` only when it matches `^/accounting/reward-rules(\?[^#]*)?$`.
- Produces (settings): `readonly back = computed<string | null>()` from the `back` query parameter through `safeBack`; `readonly closeTarget = computed<UrlTree | unknown[] | string>()` — with `back` and a shown account: `router.parseUrl(back)` with `fragment = 'card-' + accountId()`; otherwise today's value (`'/accounting/accounts'` or `['/accounting/accounts', accountId()]`). The header ✕ binds `[routerLink]="closeTarget()"`.

- [ ] **Step 1: Write the failing overview tests** in `reward-rules.spec.ts`:
  - `rule links carry back=<overview url>`: with the page at `/accounting/reward-rules?archived=1`, every `.rr-rule`, `.rr-add` and `.rr-bare-chip` href contains `back=%2Faccounting%2Freward-rules%3Farchived%3D1` (decode and compare to `/accounting/reward-rules?archived=1`).
  - `each card has id card-<account id> and tabindex -1`.
  - `fragment card-<id> scrolls to and focuses that card once the overview is shown` (stub `scrollIntoView` on the prototype; assert it was called on that section and `document.activeElement` is the section).
  - `an unknown fragment does nothing` (no scroll call, no throw).
- [ ] **Step 2: Run** `npx ng test --watch=false --include='**/reward-rules.spec.ts'` — Expected: the four new tests FAIL.
- [ ] **Step 3: Implement** `backUrl`, the three `queryParams`, the section `id` / `tabindex`, and the fragment effect (read `route.fragment`; act in `afterNextRender` once `overview()` is non-null; run once per fragment value).
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Write the failing settings tests** in `account-settings.spec.ts`:
  - `safeBack` table: `/accounting/reward-rules` → same; `/accounting/reward-rules?archived=1&account_id=3` → same; `null`, `''`, `//evil.example`, `https://evil.example/accounting/reward-rules`, `/accounting/accounts`, `/accounting/reward-rules/x`, `/accounting/reward-rules#x`, `/accounting/reward-rules?a=1#x` → `null`.
  - `✕ goes to the passbook without back` (existing behaviour pinned: href `/accounting/accounts/<id>`).
  - `✕ goes back to the overview at this card with back`: route `?back=/accounting/reward-rules?archived=1` on account 8 → ✕ href is `/accounting/reward-rules?archived=1#card-8`.
  - `an invalid back is ignored` (`?back=//evil.example` → passbook href).
  - `saving with back navigates to the overview at this card`; `saving without back navigates to the passbook` (spy `router.navigateByUrl` / `router.navigate`).
  - `closing the rule sheet opened by ?rule= with back returns to the overview` — three cases: 取消 (sheet `closed`), `saved`, `deleted`; each expects navigation to `/accounting/reward-rules…#card-<id>`.
  - `closing that sheet stays on settings when the account form is dirty` (change the name first; no navigation).
  - `a rule sheet opened from the settings page itself (row tap) never navigates on close, even with back`.
  - `back survives the ?rule= cleanup` (after `openPendingRule` the URL still has `back`; `rule` is gone).
- [ ] **Step 6: Run** `npx ng test --watch=false --include='**/account-settings.spec.ts'` — Expected: the new tests FAIL (the pinned passbook test passes).
- [ ] **Step 7: Implement** in `account-settings.ts`: `safeBack`, `back`, `closeTarget`; a private `returnToBack(): boolean` that navigates to `closeTarget()` when `back()` is set and returns whether it did; the save success path calls it and falls back to `router.navigate(['/accounting/accounts', id])`; a private flag `ruleFromLink` set in `openPendingRule()` when it opens a sheet and cleared by `openRule()` calls from the page; `closeRuleSheet()` — after its existing body, when `ruleFromLink` was set, no route-leave prompt is pending and `!isDirty()` — calls `returnToBack()`. `openPendingRule()` keeps using `queryParamsHandling: 'merge'` (so `back` survives). Account deletion keeps going to `/accounting/accounts`.
- [ ] **Step 8: Run** the settings spec, then the full suite `npx ng test --watch=false` — Expected: PASS.
- [ ] **Step 9: Commit** `feat(account-settings): return to the reward rules overview when arriving from it (#378)`.

### Task 2: `SheetPullDirective` and no pull-to-refresh under a sheet

**Files:**
- Create: `frontend/src/app/components/accounting/sheet-pull.directive.ts`, `sheet-pull.directive.spec.ts`
- Modify: `frontend/src/styles.scss` (one rule), `frontend/src/app/components/accounting/sheet.scss`, `frontend/src/app/components/ui/filter-bar/filter-bar.scss`

**Interfaces:**
- Produces: `@Directive({ selector: '[appSheetPull]' }) export class SheetPullDirective` with `readonly pulled = output<void>()` and `export const SHEET_PULL_CLOSE_PX = 80`.
  - Host is the grab bar. The moved element is `host.closest('.sheet')`.
  - `pointerdown` (primary button / touch / pen only): capture the pointer, remember `clientY`, add class `pulling` to the sheet.
  - `pointermove`: `dy = max(0, clientY - startY)`; set the sheet's inline `transform: translateY(<dy>px)`.
  - `pointerup`: clear the inline transform and `pulling`; emit `pulled` when `dy >= min(SHEET_PULL_CLOSE_PX, sheetHeight * 0.3)`.
  - `pointercancel`, `lostpointercapture`, `Escape`: clear transform and `pulling`, no emit.
  - The directive never closes anything itself: if the host refuses to close (busy, discard prompt), the sheet is simply back at rest.
  - On destroy mid-drag: listeners released, nothing emitted.
- Produces (CSS): `.grab` becomes a 36 × 4 px bar drawn inside a larger hit area (at least 44 px tall, full sheet width, `touch-action: none`, `cursor: grab`) without changing the visible bar or the sheet's layout height by more than the existing 16 px block; `.sheet.pulling { transition: none; }`, `.sheet { transition: transform 160ms ease; overscroll-behavior: contain; }` with the transition removed under `prefers-reduced-motion: reduce`. Same for `.filter-panel.sheet` and its `.grab` in `filter-bar.scss`.
- Produces (CSS, `styles.scss`): `html:has(.overlay, .filter-overlay) { overscroll-behavior-y: none; }` — the page cannot be pulled to refresh while any bottom sheet is open.

- [ ] **Step 1: Write the failing directive tests** (`sheet-pull.directive.spec.ts`, a test host with `<div class="sheet"><div class="grab" appSheetPull (pulled)="n = n + 1"></div></div>`, the sheet's `getBoundingClientRect` stubbed to height 400):
  - `a drag of 80 px down emits pulled once and clears the transform`.
  - `a drag of 79 px springs back without emitting`.
  - `a short sheet closes at 30 % of its height` (height 200 → 60 px emits, 59 px does not).
  - `the sheet follows the finger: translateY(<dy>px) while dragging, never negative` (drag up 30 px → `translateY(0px)`).
  - `pulling class is on the sheet only during the drag`.
  - `pointercancel, lostpointercapture and Escape end the drag without emitting`.
  - `a secondary mouse button does not start a drag`.
  - `destroying the host mid-drag emits nothing` (later `pointerup` on the detached node is ignored).
- [ ] **Step 2: Run** `npx ng test --watch=false --include='**/sheet-pull.directive.spec.ts'` — Expected: FAIL (directive not found).
- [ ] **Step 3: Implement** the directive and the CSS above.
- [ ] **Step 4: Run** the directive spec and `--include='**/theme/*.spec.ts'` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(sheets): grab-bar pull-down directive; no pull-to-refresh under an open sheet (#378)`.

### Task 3: Wire every bottom sheet

**Files (each: `.html` + the component's `imports` + its spec):**

| Host | Grab line | `(pulled)` calls |
|---|---|---|
| `accounting/reward-rule-sheet/reward-rule-sheet.html` | 3 | `cancel()` |
| `accounting/account-settings/account-settings.html` | 321 | `closeSheet()` |
| `accounting/account-picker/account-picker.html` | 30 | `close()` |
| `accounting/fee-sheet/fee-sheet.html` | 3 | `cancel()` |
| `accounting/fx-sheet/fx-sheet.html` | 3 | `cancel()` |
| `accounting/inbox/einvoice-target-sheet/einvoice-target-sheet.html` | 10 | `cancel()` |
| `accounting/inbox/record-sheet/record-sheet.html` | 10 | `cancel()` |
| `accounting/inbox/match-sheet/match-sheet.html` | 10 | `cancel()` |
| `accounting/reports/custom-report.html` | 28 | `closeSheet()` |
| `accounting/transfer-panel/transfer-panel.html` | 92 | `closeSheet()` |
| `accounting/reminders/schedule-sheet/schedule-sheet.html` | 3 | `close()` |
| `ui/picker/picker.html` | 20 | `close()` |
| `ui/filter-bar/filter-bar.html` | 22 | `close()` |

`accounting/statements/statement-detail/statement-detail.html:189` (強制確認) has no backdrop close by design: it gets **no** `appSheetPull`, and its `.grab` is removed so the sheet does not look pullable.

**Interfaces:**
- Consumes: `SheetPullDirective`, `pulled` (Task 2). Each `(pulled)` handler is the same method the host's backdrop click already calls.

- [ ] **Step 1: Write the failing tests** — one per host spec, same shape: open the sheet, drag its `.grab` 100 px down (pointerdown / pointermove / pointerup; sheet height stubbed to 400), assert the sheet is closed (or, where the host asks first, that its discard prompt is shown). Two hosts get an extra case:
  - `reward-rule-sheet`: `a pull while busy does not close` and `a pull with unsaved input shows 放棄未儲存的內容？ instead of closing`.
  - `statement-detail`: `the 強制確認 sheet has no grab bar`.
- [ ] **Step 2: Run** `npx ng test --watch=false` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement**: add `appSheetPull (pulled)="…"` to each grab line in the table and `SheetPullDirective` to each component's `imports`; delete the `.grab` in `statement-detail.html`.
- [ ] **Step 4: Run** `npx ng test --watch=false`, `npx ng build` — Expected: PASS, build ok.
- [ ] **Step 5: Commit** `feat(sheets): every phone bottom sheet closes on a grab-bar pull (#378)`.

## Evidence before the PR (controller, not a task)

Playwright (Chromium, 390 × 844, touch enabled, production build, stubbed APIs, fictional data):
- overview → rule → 取消 lands on the overview with that card in view; overview → rule → ✕ on settings likewise; settings opened from the passbook still closes to the passbook.
- on the rule sheet and one settings row sheet: a touch drag of the grab bar closes it; a short drag springs back; `getComputedStyle(document.documentElement).overscrollBehaviorY` is `none` while a sheet is open and `auto` after.
- The pull-to-refresh gesture itself cannot be exercised on this host (no iOS Safari, no real Chrome Android): the owner checks it on a phone after release.
