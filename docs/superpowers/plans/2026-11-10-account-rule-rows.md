# Account Settings Rule Rows (#323 PR C3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 回饋規則 rows of the account settings page into the one-line rows of design D6: drag handle, name + rate chip + tags, posting summary, cap bar, enable switch and a ⋯ menu, with drag reorder saved by one order call.

**Architecture:** A new presentational `app-account-rules` component (`account-settings/rules-section/`, the second section extracted per #354) renders the rows and owns the two pieces of UI state that are purely local: the open ⋯ menu and an in-progress drag. It emits intents; `account-settings.ts` keeps every write (`updateRewardRule`, `reorderRewardRules`, the rule sheet). Drag uses Pointer Events on the handle (the repo has no `@angular/cdk`; HTML5 drag-and-drop has no touch support). Delete from the menu reuses the rule sheet's existing confirm / `rule_referenced` → 改為停用 flow through one new sheet input.

**Tech Stack:** Angular 21 standalone components, signals, OnPush; vitest through `cd frontend && npx ng test --watch=false --include=<glob>`.

**Spec:** `openspec/changes/polish-reward-and-account-settings/specs/frontend-accounting-ledger/spec.md` — requirement "Reward rule rows" (line 192) and the 回饋規則 bullet of "Account settings page"; `design.md` §D6; artboards `design_handoff_v3/design_refs/AccountSettingsDesktop.dc.html` / `AccountSettingsPhone.dc.html`.

## Global Constraints

- Copy, verbatim: tags `已停用` / `已過期` / `未開始` (from `RULE_STATE_LABELS`), `基本`, `來自 MOZE`; muted lines `停用中，不估算回饋` / `已結束，不再估算` / `<yyyy/m/d> 起才估算` / `尚未開始` (the overview's `mutedLine`); menu items `編輯`, `上移`, `下移`, `刪除`; handle `aria-label="拖曳排序 <rule name>"`; menu button `aria-label="<rule name> 更多動作"`; switch `ariaLabel="<rule name> 啟用"` (unchanged).
- Class hooks kept (tests and the live probe use them): `.rule-row[data-rule-id]`, `.rule-label`, `.rule-basic`, `.rule-moze`, `.rule-posting`, `.rule-enabled`, `.rule-add`, `.rule-message`, `.rules-block`, `.rules-head`, `.rules-all`. New: `.rule-handle`, `.rule-open`, `.rule-rate`, `.rule-state` (now only on a non-active rule), `.rule-muted`, `.rule-more`, `.rule-menu`, `.rule-row.dragging`, `.rule-row.inactive`, `.rule-drop-line`.
- Removed from the row: `.rule-period`, `.rule-window`, `.rule-usage` text lines, the 編輯 / ↑ / ↓ buttons (`.rule-edit`, `.move-up`, `.move-down`).
- Line icons only (inline SVG, `aria-hidden="true"`); no emoji; the `theme/line-icons.spec.ts`, `bootstrap-grid.spec.ts` and `contrast.spec.ts` gates stay green. No bare `row` / `col` class names.
- No new dependency. No API, route or Caddy change. `reorderRewardRules(accountId, ids)` (`services/accounting.service.ts:193`) and the sheet's delete flow are used as they are.
- Rule writes save at once and are not part of the page's dirty count (unchanged, `account-settings.ts:420`).
- Test fixtures are fictional (測試卡, 測試里程, round amounts); no real account ids or amounts.
- Colours from `--app-*` tokens only; light and dark.

## Review Focus

1. **A drag that ends where it started, or is cancelled (Escape, `pointercancel`, pointer released outside the list), sends nothing.** An order call for an unchanged order is a wasted write and re-renders the list under the pointer. Test in Task 2.
2. **A drop while a rule write is in flight (`busy`) is ignored, and the handle cannot start a drag while `busy`.** The order sent must start from the saved order, same as today's `moveRule` guard. Tests in Tasks 2 and 3.
3. **The rules input changes during a drag (a re-read lands): the drag is cancelled, not applied to the new list.** Indices from the old list would reorder the wrong rules. Test in Task 2.
4. **A failed order call puts the rows back.** The page shows the dropped order optimistically; on error it shows the message and re-reads (`ruleWriteFailed`). Test in Task 3.
5. **Focus after 上移 / 下移 from the menu stays on that rule's ⋯ button** (the row is re-rendered at a new index; `track rule.id` keeps the element, the menu closes and focus must not fall to `<body>` — inside nothing, but the next Tab would start from the top of the page). Test in Task 2.
6. **Tapping the switch, the handle or ⋯ does not open the rule sheet; a drag does not fire the row's click.** Only `.rule-open` opens it. Tests in Tasks 1 and 2.
7. **A shared-cap group shows its bar once, on the first listed member** (today's `usageByRule`, `account-settings.ts:428`); a non-active rule shows no bar even when the usage payload names it. Test in Task 1.

---

### Task 1: `app-account-rules` — rows, tags, bar, switch, open

**Files:**
- Create: `frontend/src/app/components/accounting/account-settings/rules-section/rules-section.ts`, `.html`, `.scss`, `.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rule-text.ts` (export `mutedLine`), `frontend/src/app/components/accounting/reward-rules/reward-rules.ts` (import it instead of the local copy)

**Interfaces:**
- Produces:
  - `export function mutedLine(rule: { state: RewardRuleState; starts_on: string | null }): string | null` in `reward-rule-text.ts` (the body moves unchanged from `reward-rules.ts:117`).
  - `@Component({ selector: 'app-account-rules' }) export class AccountRulesComponent` with
    - inputs `rules = input.required<RewardRule[]>()`, `usage = input<ReadonlyMap<number, RewardUsage>>(new Map())` (rule id → the usage row shown on that rule), `today = input.required<string>()` (ISO date), `busy = input(false)`;
    - outputs `open = output<RewardRule>()`, `toggle = output<{ rule: RewardRule; enabled: boolean }>()`, `reorder = output<number[]>()`, `remove = output<RewardRule>()` (the last two are wired in Task 2);
    - `protected readonly rows = computed<RuleRowView[]>(...)` where `interface RuleRowView { rule: RewardRule; state: RewardRuleState; title: string; rate: string; tag: string | null; basic: boolean; posting: string; usage: RewardUsage | null; unit: RewardUnit | null; muted: string | null }`.

Row rules (mirror `buildCard`, `reward-rules.ts:130`): `state = ruleState(rule, today)`; `title = ruleTitle(rule)`; `rate = rateChip(rule)`; `tag = state !== 'active' ? RULE_STATE_LABELS[state] : null`; `basic = rule.is_basic`; `posting = postingSummary(rule)`; `usage` only when `state === 'active'`; `muted = mutedLine({ state, starts_on: rule.starts_on })`.

Markup per row, in this order inside `<div class="rule-row" [attr.data-rule-id] [class.inactive]="state !== 'active'">`: the handle button (`.rule-handle`, disabled in this task — wired in Task 2); `<button type="button" class="rule-open">` holding `.rule-title` (`<b class="rule-label">`, `.rule-rate`, `.rule-state` when `tag`, `.rule-basic` when `basic`, `.rule-moze` when `rule.moze_id`, `<small class="rule-posting">· {{ posting }}</small>`) and under it either `<app-cap-bar [used] [estimated] [cap] [unit]>` when `usage` or `<small class="rule-muted">` when `muted`; `<app-switch class="rule-enabled">`; the ⋯ button (`.rule-more`, Task 2). `.rule-row.inactive .rule-open { opacity: 0.6 }` (the switch and ⋯ stay full strength).

- [ ] **Step 1: Write the failing tests** (`rules-section.spec.ts`, a host component passing three fictional rules A 一般消費 1% (basic, moze_id set), B 海外消費 2.5% (disabled), C 玩數位 3% (active, capped) and a usage map `{ C → used 180, estimated 13, cap 200, money }`):
  - `renders one .rule-row per rule in input order with data-rule-id`
  - `title drops the repeated rate and the chip carries it` — C: `.rule-label` text `玩數位`, `.rule-rate` text `3%`
  - `an active non-basic rule has no state tag` — C: no `.rule-state`, no `.rule-basic`
  - `基本 and 來自 MOZE tags` — A: `.rule-basic` text `基本`, `.rule-moze` text `來自 MOZE`, no `.rule-state`
  - `disabled rule row is dimmed, shows 已停用 and 停用中，不估算回饋, and no cap bar` — B: `.inactive`, `.rule-state` text `已停用`, `.rule-muted` text `停用中，不估算回饋`, no `app-cap-bar` (spec scenario "Disabled rule row")
  - `expired and upcoming rules show their tag and muted line` — `已過期` / `已結束，不再估算`; `未開始` / `2026/12/1 起才估算`
  - `an active capped rule shows the cap bar` — C: `app-cap-bar` present, its accessible name contains `180`
  - `usage naming a non-active rule shows no bar` — map `{ B → … }`: B has no `app-cap-bar` (Review Focus 7)
  - `posting summary reads · <postingSummary>` — `.rule-posting` text equals `'· ' + postingSummary(rule)`
  - `tapping the row emits open with the rule` — click `.rule-open` on C
  - `the switch emits toggle and does not emit open` — `checkedChange(false)` on C's switch → `toggle` `{ rule: C, enabled: false }`, `open` not called (Review Focus 6)
  - `busy disables the switches`
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='**/rules-section.spec.ts'` — Expected: FAIL (component missing).
- [ ] **Step 3: Move `mutedLine` to `reward-rule-text.ts` and import it in `reward-rules.ts`; implement the component as specified above.**
- [ ] **Step 4: Run** the same command plus `--include='**/reward-rules.spec.ts'` — Expected: PASS (overview tests unchanged).
- [ ] **Step 5: Commit** `feat(account-settings): app-account-rules rows — chip, tags, cap bar, switch (#323 C3)`

### Task 2: ⋯ menu and drag reorder

**Files:**
- Modify: `rules-section.ts`, `.html`, `.scss`, `.spec.ts`

**Interfaces:**
- Consumes: Task 1's component.
- Produces (all `protected` unless noted):
  - `menuFor = signal<number | null>(null)` (rule id whose menu is open); `toggleMenu(rule: RewardRule, event: Event): void`; `closeMenu(restoreFocus = true): void`; `onMenuKeydown(event: KeyboardEvent): void`; `onMenuFocusout(event: FocusEvent): void`; `runMenu(action: 'edit' | 'up' | 'down' | 'delete', rule: RewardRule, index: number): void`.
  - `drag = signal<{ id: number; from: number; to: number } | null>(null)`; `onHandleDown(event: PointerEvent, index: number): void`; `onHandleMove(event: PointerEvent): void`; `onHandleUp(event: PointerEvent): void`; `cancelDrag(): void`.
  - `export function movedIds(ids: number[], from: number, to: number): number[]` — the id at `from` removed and inserted at `to`.
  - `liveText = signal('')` rendered in a `.visually-hidden[aria-live="polite"]` element.

Menu (the `page-header` pattern, `shell/page-header/page-header.ts:107-186`): `.rule-more` has `aria-haspopup="menu"`, `aria-expanded`, `aria-controls`; the panel is `<div class="rule-menu" role="menu">` with four `<button role="menuitem">`: 編輯, 上移 (`disabled` on the first row or while `busy`), 下移 (`disabled` on the last row or while `busy`), 刪除 (class `danger`). Opening focuses the first enabled item; ArrowDown / ArrowUp wrap over the enabled items; Home / End; Escape closes and returns focus to `.rule-more`; focus leaving the row's menu wrapper closes it; a second menu opening closes the first. `runMenu`: `edit` → `open.emit(rule)`; `up` / `down` → `reorder.emit(movedIds(ids, index, index ∓ 1))`, then focus that rule's `.rule-more` after the next render (looked up by `data-rule-id`) and `liveText.set('已移到第 N 項，共 M 項')`; `delete` → `remove.emit(rule)`. Every action closes the menu first.

Drag (Pointer Events on `.rule-handle`, `touch-action: none` on the handle only so the page still scrolls from the rest of the row):
- `pointerdown` (primary button, not `busy`, more than one rule): `preventDefault()`, `setPointerCapture?.(pointerId)`, record each row's `getBoundingClientRect()` top / height once, `drag.set({ id, from: index, to: index })`.
- `pointermove`: `to` = the count of other rows whose vertical midpoint is above the pointer's `clientY`, clamped to `0 … n-1`. The dragged row gets `.dragging`; a 2 px `.rule-drop-line` is drawn before the row at `to` (after the last row when `to` is the end). No live DOM reorder while dragging (keeps the recorded rects valid).
- `pointerup`: if `to !== from` and not `busy` → `reorder.emit(movedIds(ids, from, to))` and `liveText`; always clear `drag`. A click that follows a drag on the handle is swallowed (the handle is a button with no click action).
- `pointercancel`, Escape (document `keydown` listener only while dragging), or an `effect` seeing `rules()` change while `drag()` is set → `cancelDrag()` (no emit).
- The handle is a `<button type="button">` so it is focusable and named; it has no keyboard action — 上移 / 下移 in ⋯ are the keyboard path (design D6).

- [ ] **Step 1: Write the failing tests** (same host; rows get stubbed `getBoundingClientRect` at tops 0 / 60 / 120, height 60; pointer events dispatched as `new MouseEvent('pointerdown' | 'pointermove' | 'pointerup', { clientY, button: 0, bubbles: true })` with `pointerId` defined on the event object):
  - `movedIds moves one id` — `movedIds([1,2,3], 2, 0)` → `[3,1,2]`; `([1,2,3], 0, 2)` → `[2,3,1]`; `([1,2,3], 1, 1)` → `[1,2,3]`
  - `dragging C above A emits reorder once with C, A, B` (spec scenario "Reorder by drag") — down on C's handle at y 150, move to y 10, up → `reorder` called once with `[C, A, B]`
  - `a drag released where it started emits nothing` (Review Focus 1)
  - `Escape during a drag cancels it` and `pointercancel cancels it` — no emit, no `.dragging` left (Review Focus 1)
  - `the handle does not start a drag while busy` and `a drop while busy emits nothing` (set `busy` between down and up) (Review Focus 2)
  - `a rules change during a drag cancels it` — replace the input list mid-drag, then up → no emit (Review Focus 3)
  - `dragging shows .dragging on the row and one .rule-drop-line at the target`
  - `a drag does not emit open` (Review Focus 6)
  - `⋯ opens a menu with 編輯, 上移, 下移, 刪除 and focuses 編輯`
  - `上移 is disabled on the first row, 下移 on the last; both while busy`
  - `編輯 emits open; 刪除 emits remove; each closes the menu`
  - `下移 on A emits reorder with B, A, C once, and focus is on A's ⋯ after the host applies the order` (Review Focus 5)
  - `ArrowDown / ArrowUp wrap over enabled items; Escape closes and focuses ⋯`
  - `opening a second menu closes the first; focus leaving the menu closes it`
  - `⋯ and the handle do not emit open` (Review Focus 6)
  - `a move announces 已移到第 2 項，共 3 項 in the live region`
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='**/rules-section.spec.ts'` — Expected: FAIL on the new tests only.
- [ ] **Step 3: Implement the menu and the drag as specified.**
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(account-settings): rule row ⋯ menu and pointer drag reorder (#323 C3)`

### Task 3: Page wiring, delete from the menu, styles, OpenSpec ticks

**Files:**
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts`, `.html`, `.scss`, `.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rule-sheet/reward-rule-sheet.ts`, `.spec.ts`
- Modify: `openspec/changes/polish-reward-and-account-settings/tasks.md`

**Interfaces:**
- Consumes: `app-account-rules` (Tasks 1–2).
- Produces:
  - Sheet: `readonly startDelete = input(false)` on `RewardRuleSheetComponent` — when true and `rule()` is set, the sheet opens with `confirmingDelete` already true (the same state its 刪除 button's first press sets, `reward-rule-sheet.ts:246`): `.rule-delete` is `.armed`, reads `確定刪除？` and has focus. Nothing else in the delete flow changes.
  - Page: `ruleSheet = signal<{ rule: RewardRule | null; startDelete?: boolean } | null>(null)`; `openRule(rule: RewardRule | null, startDelete = false): void`; `reorderRules(ids: number[]): void` replacing `moveRule(index, delta)`.

`reorderRules(ids)`: same guards as `moveRule` (`loadedId() !== null`, not `ruleBusy()`), plus `ids` must be a permutation of the current rule ids and differ from the current order (else return). It sets `ruleBusy`, applies the order to `detail().reward_rules` at once (optimistic), calls `reorderRewardRules(id, ids)` once; `next` replaces the list with the response and clears `ruleBusy` (as today, `account-settings.ts:1095`); `error` → `ruleWriteFailed` (message + re-read, which restores the saved order).

Template (`account-settings.html:204-228` replaced): inside `.rules-block`, after `.rules-head`,
`<app-account-rules [rules]="rules()" [usage]="usageByRule()" [today]="today()" [busy]="ruleBusy()" (open)="openRule($event)" (toggle)="toggleRule($event.rule, $event.enabled)" (reorder)="reorderRules($event)" (remove)="openRule($event, true)" />`; `.rule-message` and `.rule-add` stay in the page; the sheet gets `[startDelete]="open.startDelete ?? false"`. Remove from the page class: `ruleLabel`, `ruleStateLabels`, `ruleStateOf`, `postingSummary`, `periodText`, `windowLabels`, `usageText`, `usageWindow` members once nothing in the template uses them (keep any the phone rows or the header still use — check with the compiler), and the `.rule-row … .rule-actions` rules in `account-settings.scss:147-194` (their replacements live in `rules-section.scss`).

Phone (< 760 px): the same row at every width — handle, text, switch, ⋯ (the handle is 28 px wide, the switch stays inline per the spec's "Switches stay inline", ⋯ carries 上移 / 下移). The cap bar spans the text column. This differs from the phone artboard, which draws a text-only row with ›; the spec's requirement lists the handle, switch and ⋯ without a width condition and is the authority. Recorded as an open question for the owner in the plan review.

- [ ] **Step 1: Write the failing tests.**
  - `reward-rule-sheet.spec.ts`: `startDelete opens with the delete confirmation showing and focuses it`; `startDelete with rule === null is ignored`.
  - `account-settings.spec.ts` — replace `下移 on the first row sends the swapped order…` (1126) and `編輯 opens the sheet…` (1160), update `renders one .rule-row per rule…` (1029), `lists the rules with a state badge` (333), the three usage tests (1189–1209) and `an expired rule shows the 已過期 badge in the warning colour` (1391) to the new markup, and add:
    - `回饋規則 rows render through app-account-rules with 基本 and 來自 MOZE kept` (4.7b)
    - `dragging the third rule above the first sends one order request with C, A, B and shows that order before the response` (spec scenario; `reorderRewardRules` called once with `(accountId, [C, A, B])`)
    - `a failed order request shows the message and restores the saved order` (Review Focus 4)
    - `reorder is ignored while a rule write is in flight` (Review Focus 2)
    - `⋯ 下移 sends the swapped order once`
    - `tapping a row opens the sheet with that rule; ⋯ 編輯 does too; 新增規則 opens it with rule === null`
    - `⋯ 刪除 opens the sheet with the delete confirmation showing`
    - `a disabled rule's row is dimmed with 已停用 and 停用中，不估算回饋 and has no cap bar` (spec scenario, page level)
    - `usage: the capped rule shows a cap bar; a shared group shows it once on the first listed member; a failed usage request hides the bars only`
    - `at 390 px each rule row keeps the handle, the switch and ⋯`
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='**/account-settings.spec.ts' --include='**/reward-rule-sheet.spec.ts'` — Expected: FAIL on the new and rewritten tests.
- [ ] **Step 3: Implement** the sheet input, `reorderRules`, the template swap, the member and style removals, and the phone styles.
- [ ] **Step 4: Run** `cd frontend && npx ng test --watch=false` — Expected: PASS, whole suite (theme gates included).
- [ ] **Step 5: Tick OpenSpec.** In `tasks.md`: `- [x] 2.2 …` (its users shipped in C1), `- [x] 4.4 …`, `- [x] 4.7b …`. Run `openspec validate polish-reward-and-account-settings --strict` — Expected: `valid`.
- [ ] **Step 6: Commit** `feat(account-settings): one-line rule rows with drag reorder and ⋯ menu (#323 C3)`

---

## Self-review notes

- Spec coverage: handle (T2), name + rate chip + conditional tag (T1), posting summary (T1), cap bar for an active capped rule (T1), switch (T1), ⋯ with 編輯 / 刪除 / 上移 / 下移 (T2), tap opens the sheet (T1, T3), one order call on drop (T2, T3); scenarios "Reorder by drag" (T2 + T3) and "Disabled rule row" (T1 + T3); 「來自 MOZE」 kept (T1 + T3). Task 6.1 (screens at 1440 / 820 / 390) stays for the wrap-up after C3; the release evidence run covers this PR's rows.
- After this PR the live probe's `rr-settings` / `acct-sections` markers need a look: they must not depend on `.rule-edit`, `.move-up`, `.move-down`, `.rule-period`, `.rule-window` or `.rule-usage` (check at release; add `rule-handle` / `rule-menu` / `rule-drop-line` + 拖曳排序 / 更多動作).
