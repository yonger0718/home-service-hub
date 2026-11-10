# Account Settings Rule Rows (#323 PR C3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 回饋規則 rows of the account settings page into the one-line rows of design D6: drag handle, name + rate chip + tags, posting summary, cap bar, enable switch and a ⋯ menu, with drag reorder saved by one order call.

**Architecture:** A new presentational `app-account-rules` component (`account-settings/rules-section/`, the second section extracted per #354) renders the rows and owns the two pieces of UI state that are purely local: the open ⋯ menu and an in-progress drag. It emits intents; `account-settings.ts` keeps every write (`updateRewardRule`, `reorderRewardRules`, the rule sheet). Drag uses Pointer Events on the handle (the repo has no `@angular/cdk`; HTML5 drag-and-drop has no touch support). Delete from the menu reuses the rule sheet's existing confirm / `rule_referenced` → 改為停用 flow through one new sheet input.

**Tech Stack:** Angular 21 standalone components, signals, OnPush; vitest through `cd frontend && npx ng test --watch=false --include=<glob>`.

**Spec:** `openspec/changes/polish-reward-and-account-settings/specs/frontend-accounting-ledger/spec.md` — requirement "Reward rule rows" (line 192) and the 回饋規則 bullet of "Account settings page"; `design.md` §D6; artboards `design_handoff_v3/design_refs/AccountSettingsDesktop.dc.html` / `AccountSettingsPhone.dc.html`.

## Global Constraints

- Copy, verbatim: tags `已停用` / `已過期` / `未開始` (from `RULE_STATE_LABELS`), `基本`, `來自 MOZE`; muted lines `停用中，不估算回饋` / `已結束，不再估算` / `<yyyy/m/d> 起才估算` / `尚未開始` (the overview's `mutedLine`); menu items `編輯`, `上移`, `下移`, `刪除`; handle `aria-label="拖曳排序 <rule name>"`; menu button `aria-label="<rule name> 更多動作"`; switch `ariaLabel="<rule name> 啟用"` (unchanged).
- Class hooks kept (tests and the live probe use them): `.rule-row[data-rule-id]`, `.rule-label`, `.rule-basic`, `.rule-moze`, `.rule-posting`, `.rule-enabled`, `.rule-add`, `.rule-message`, `.rules-block`, `.rules-head`, `.rules-all`. New: `.rule-handle`, `.rule-open`, `.rule-rate`, `.rule-state` (now only on a non-active rule; keeps `[attr.data-state]` and `.warn` on `expired`, `account-settings.spec.ts:1391-1400`), `.rule-muted`, `.rule-more`, `.rule-menu`, `.rule-row.dragging`, `.rule-row.inactive`, `.rule-drop-line`.
- Removed from the row: `.rule-period`, `.rule-window`, `.rule-usage` text lines, the 編輯 / ↑ / ↓ buttons (`.rule-edit`, `.move-up`, `.move-down`).
- Line icons only (inline SVG, `aria-hidden="true"`); no emoji; the `theme/line-icons.spec.ts`, `bootstrap-grid.spec.ts` and `contrast.spec.ts` gates stay green. No bare `row` / `col` class names.
- No new dependency. No API, route or Caddy change. `reorderRewardRules(accountId, ids)` (`services/accounting.service.ts:193`) and the sheet's delete flow are used as they are.
- Rule writes save at once and are not part of the page's dirty count (unchanged, `account-settings.ts:420`).
- Test fixtures are fictional (測試卡, 測試里程, round amounts); no real account ids or amounts.
- Colours from `--app-*` tokens only; light and dark.

## Review Focus

1. **A drag that ends where it started, or is cancelled (Escape, `pointercancel`, a scroll during the drag), sends nothing; a pointer that never moved past the 4 px slop is a tap, not a drag.** An order call for an unchanged order is a wasted write and re-renders the list under the pointer. A release outside the list is a real drop (pointer capture still reports `clientY`: above the list = first, below = last). Tests in Task 2.
2. **A drop while a rule write is in flight (`busy`) is ignored, and the handle cannot start a drag while `busy`.** The order sent must start from the saved order, same as today's `moveRule` guard. Tests in Tasks 2 and 3.
3. **The rules input changes during a drag (a re-read lands): the drag is cancelled, not applied to the new list.** Indices from the old list would reorder the wrong rules. Test in Task 2.
4. **A failed order call puts the rows back, even when the re-read fails too.** The page shows the dropped order optimistically; on error it restores the snapshot of the saved order first, then shows the message and re-reads (`ruleWriteFailed`; its re-read error path leaves `detail` alone, `account-settings.ts:1147-1152`). Tests in Task 3.
5. **Focus after 上移 / 下移 from the menu stays on that rule's ⋯ button** (the row is re-rendered at a new index; `track rule.id` keeps the element, the menu closes and focus must not fall to `<body>` — inside nothing, but the next Tab would start from the top of the page). Test in Task 2.
6. **Tapping the switch, the handle or ⋯ does not open the rule sheet; a drag does not fire the row's click.** Only `.rule-open` opens it. Tests in Tasks 1 and 2.
7. **A shared-cap group shows its bar once, on the first listed member** (today's `usageByRule`, `account-settings.ts:424-436`, kept — the component only receives the collapsed map; page test in Task 3, already locked by `account-settings.spec.ts:1204`); **a non-active rule shows no bar even when the usage map names it** (component test in Task 1).

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

Row rules (mirror `buildCard`, `reward-rules.ts:130`): `state = ruleState(rule, today)`; `title = ruleTitle(rule)`; `rate = rateChip(rule)`; `tag = state !== 'active' ? RULE_STATE_LABELS[state] : null`; `basic = state === 'active' && rule.is_basic` (one tag, the state wins — the overview's rule, `reward-rules.ts:140-142`, and the spec's "not active **or** 基本"; today's row shows both on a disabled 基本 rule); `posting = postingSummary(rule)`; `usage = state === 'active' ? usage().get(rule.id) ?? null : null`; `unit = usage ? usageUnit(usage) : null` (`app-cap-bar`'s `unit` is required, `cap-bar.ts:76`); `muted = mutedLine({ state, starts_on: rule.starts_on })`. An active rule whose usage has `cap: null` still renders `app-cap-bar` (its 「無上限 · 本期 …」 line, `cap-bar.ts:39`).

Markup per row, in this order inside `<div class="rule-row" [attr.data-rule-id] [class.inactive]="state !== 'active'">`: the handle button (`.rule-handle`, disabled in this task — wired in Task 2); `<button type="button" class="rule-open">` holding `.rule-title` (`<b class="rule-label">`, `.rule-rate`, `<span class="badge rule-state" [attr.data-state]="state" [class.warn]="state === 'expired'">` when `tag`, `.badge.rule-basic` when `basic`, `.badge.rule-moze` when `rule.moze_id`, `<small class="rule-posting">· {{ posting }}</small>`) and under it either `<app-cap-bar [used] [estimated] [cap] [unit]>` when `usage` or `<small class="rule-muted">` when `muted`; `<app-switch class="rule-enabled">`; the ⋯ button (`.rule-more`, Task 2). `.rule-row.inactive .rule-open { opacity: 0.6 }` (the switch and ⋯ stay full strength). The `.badge`, `.badge.warn`, `.badge.rule-state[data-state=…]` and `.badge.rule-moze` rules (`account-settings.scss:161-174`) are copied into `rules-section.scss` here; Task 3 deletes the page's copies.

- [ ] **Step 1: Write the failing tests** (`rules-section.spec.ts`, a host component passing three fictional rules A 一般消費 1% (basic, moze_id set), B 海外消費 2.5% (disabled), C 玩數位 3% (active, capped) and a usage map `{ C → used 180, estimated 13, cap 200, money }`):
  - `renders one .rule-row per rule in input order with data-rule-id`
  - `title drops the repeated rate and the chip carries it` — C: `.rule-label` text `玩數位`, `.rule-rate` text `3%`
  - `an active non-basic rule has no state tag` — C: no `.rule-state`, no `.rule-basic`
  - `基本 and 來自 MOZE tags` — A: `.rule-basic` text `基本`, `.rule-moze` text `來自 MOZE`, no `.rule-state`
  - `a disabled 基本 rule shows 已停用 only` — one tag, no `.rule-basic`
  - `an expired rule's tag carries data-state="expired" and .warn`
  - `an active rule without a cap still shows the cap bar's 無上限 line` — usage `{ cap: null, used 40, estimated 5 }`
  - `a points usage passes a points unit to the bar` — `reward_is_money: false`, `reward_account_name: '測試里程'` → the bar's text has `測試里程` and no `$`
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

Menu (the `page-header` pattern, `shell/page-header/page-header.ts:107-186`): `.rule-more` has `aria-haspopup="menu"`, `aria-expanded`, `aria-controls`; the panel is `<div class="rule-menu" role="menu">` with four `<button role="menuitem">`: 編輯, 上移 (`disabled` on the first row or while `busy`), 下移 (`disabled` on the last row or while `busy`), 刪除 (class `danger`). Opening focuses the first enabled item; ArrowDown / ArrowUp wrap over the enabled items; Home / End go to the first / last enabled item; Escape closes and returns focus to `.rule-more`; focus leaving the row's menu wrapper closes it; a second menu opening closes the first. Copy the touch `pressing` guard too (`page-header.ts:117-121`, `:178-186`, `page-header.html:13`): Safari blurs a tapped button with `relatedTarget` null before `click`, and on a phone the menu is the reorder path. `runMenu`: `edit` → `open.emit(rule)`; `up` / `down` → `reorder.emit(movedIds(ids, index, index ∓ 1))`, then focus that rule's `.rule-more` after the next render (looked up by `data-rule-id`) and `liveText.set('已移到第 N 項，共 M 項')`; `delete` → `remove.emit(rule)`. Every action closes the menu first.

Drag (Pointer Events on `.rule-handle`, `touch-action: none` on the handle only so the page still scrolls from the rest of the row):
- `pointerdown` (primary button, not `busy`, more than one rule): `preventDefault()`, `setPointerCapture?.(pointerId)`, record each row's `getBoundingClientRect()` top / height once and the pointer's start `clientY`, `drag.set({ id, from: index, to: index })`. The drag is *armed* until the pointer has moved more than 4 px vertically; an armed drag shows no `.dragging` and its release emits nothing (a tap or a jitter).
- `pointermove`: `to` = the count of other rows whose vertical midpoint is above the pointer's `clientY`, clamped to `0 … n-1`. The dragged row gets `.dragging`. One 2 px `.rule-drop-line` marks the gap, computed in the *original* row order (the rects are not reordered): `to < from` → before original row `to`; `to > from` → before original row `to + 1`, or after the last row when `to + 1 === n`; `to === from` → no line. (Rows A B C, A dragged until only B's midpoint is above the pointer: `to = 1`, order `[B, A, C]`, line between B and C.) No live DOM reorder while dragging (keeps the recorded rects valid).
- `pointerup`: returns at once when `drag()` is null (already cancelled); if past the slop, `to !== from` and not `busy` → `reorder.emit(movedIds(ids, from, to))` and `liveText`; always clear `drag`. A release above the list is `to = 0`, below it `to = n-1` — real drops. A click that follows a drag on the handle is swallowed (the handle is a button with no click action).
- `pointercancel`, Escape, a `scroll` (window, capture phase) or `wheel` — document listeners present only while dragging — or the `rules` input becoming a *different array* than the one captured at `pointerdown` → `cancelDrag()` (no emit). The effect compares `rules()` with the captured array by identity and must not fire because `drag()` itself was just set (that would cancel every drag at `pointerdown`).
- The handle is a `<button type="button">` so it is focusable and named; it has no keyboard action — 上移 / 下移 in ⋯ are the keyboard path (design D6).

- [ ] **Step 1: Write the failing tests** (same host; rows get stubbed `getBoundingClientRect` at tops 0 / 60 / 120, height 60; pointer events dispatched as `new MouseEvent('pointerdown' | 'pointermove' | 'pointerup', { clientY, button: 0, bubbles: true })` with `pointerId` defined on the event object):
  - `movedIds moves one id` — `movedIds([1,2,3], 2, 0)` → `[3,1,2]`; `([1,2,3], 0, 2)` → `[2,3,1]`; `([1,2,3], 1, 1)` → `[1,2,3]`
  - `dragging C above A emits reorder once with C, A, B` (spec scenario "Reorder by drag") — down on C's handle at y 150, move to y 10, up → `reorder` called once with `[C, A, B]`
  - `dragging A below B emits B, A, C and draws the drop line between B and C` — down on A at y 30, move to y 100, assert the line sits before C's row, up
  - `a release below the list moves the row to the end` — A: down y 30, move y 400, up → `[B, C, A]`, line after the last row
  - `a drag released where it started emits nothing` (Review Focus 1)
  - `a press that moves 3 px is a tap: no .dragging, no emit` (Review Focus 1)
  - `Escape during a drag cancels it`, `pointercancel cancels it`, `a scroll during a drag cancels it` — each followed by a `pointerup` at the far position: no emit, no `.dragging` left (Review Focus 1)
  - `the handle does not start a drag while busy` and `a drop while busy emits nothing` (set `busy` between down and up) (Review Focus 2)
  - `a rules change during a drag cancels it` — replace the input list mid-drag, then up → no emit (Review Focus 3)
  - `dragging shows .dragging on the row and one .rule-drop-line at the target`
  - `a drag does not emit open` (Review Focus 6)
  - `⋯ opens a menu with 編輯, 上移, 下移, 刪除 and focuses 編輯`
  - `上移 is disabled on the first row, 下移 on the last; both while busy`
  - `編輯 emits open; 刪除 emits remove; each closes the menu`
  - `下移 on A emits reorder with B, A, C once, and focus is on A's ⋯ after the host applies the order`; `上移 on C likewise` (Review Focus 5)
  - `Home / End skip disabled items` — first row: Home lands on 編輯, End on 刪除; ArrowDown from 編輯 skips the disabled 上移
  - `a press inside the menu followed by a focusout with relatedTarget null does not close it before the click` (the `pressing` guard)
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
  - Sheet: `readonly startDelete = input(false)` on `RewardRuleSheetComponent` — read once in `ngOnInit` (never an effect: `remove()` clears `confirmingDelete` at `:250` and a re-arming effect would undo that, also after the referenced-rule error); when true and `rule()` is set, the sheet opens with `confirmingDelete` already true (the same state its 刪除 button's first press sets, `reward-rule-sheet.ts:246`): `.rule-delete` is `.armed`, reads `確定刪除？`, and the sheet's initial focus (`reward-rule-sheet.ts:156-159`, `focusables(dialog)[0]`) goes to it instead. Nothing else in the delete flow changes.
  - Page: `ruleSheet = signal<{ rule: RewardRule | null; startDelete?: boolean } | null>(null)`; `openRule(rule: RewardRule | null, startDelete = false): void`; `reorderRules(ids: number[]): void` replacing `moveRule(index, delta)`.

`reorderRules(ids)`: same guards as `moveRule` (`loadedId() !== null`, not `ruleBusy()`), plus `ids` must be a permutation of the current rule ids and differ from the current order (else return). It clears `ruleMessage` (as `moveRule` does, `account-settings.ts:1088`), sets `ruleBusy`, keeps a snapshot of the saved `reward_rules`, applies the order to `detail().reward_rules` at once (optimistic), and calls `reorderRewardRules(id, ids)` once; `next` replaces the list with the response and clears `ruleBusy` (as today, `account-settings.ts:1095-1100`); `error` (same `loadId` only) → restore the snapshot into `detail`, then `ruleWriteFailed` (message + re-read). The restore comes first because the re-read's own error path (`:1147-1152`) leaves `detail` untouched.

Template (`account-settings.html:204-228` replaced): inside `.rules-block`, after `.rules-head`,
`<app-account-rules [rules]="rules()" [usage]="usageByRule()" [today]="today()" [busy]="ruleBusy()" (open)="openRule($event)" (toggle)="toggleRule($event.rule, $event.enabled)" (reorder)="reorderRules($event)" (remove)="openRule($event, true)" />`; `.rule-message` and `.rule-add` stay in the page; the sheet gets `[startDelete]="open.startDelete ?? false"`. Remove from the page class: `ruleLabel`, `ruleStateLabels`, `ruleStateOf`, `postingSummary`, `periodText`, `windowLabels`, `usageText`, `usageWindow` members once nothing in the template uses them (keep any the phone rows or the header still use — check with the compiler), and the `.rule-row … .rule-actions` rules in `account-settings.scss:147-194`, including the `.badge` rules at `:161-174` — `.badge` is used only by these rows (`account-settings.html:210-212`) and now lives in `rules-section.scss` (Task 1).

Phone (< 760 px): the same row at every width — handle, text, switch, ⋯ (the handle is 28 px wide, the switch stays inline per the spec's "Switches stay inline", ⋯ carries 上移 / 下移). The cap bar spans the text column. This differs from the phone artboard, which draws a text-only row with ›; the spec's requirement lists the handle, switch and ⋯ without a width condition and is the authority. Recorded as an open question for the owner in the plan review.

- [ ] **Step 1: Write the failing tests.**
  - `reward-rule-sheet.spec.ts`: `startDelete opens with the delete confirmation showing and focuses it`; `startDelete with rule === null is ignored`; `after a rule_referenced error the delete button is not re-armed` (confirm → 409 → `.rule-delete` not `.armed`, the 改為停用 message shows).
  - `account-settings.spec.ts` — replace `下移 on the first row sends the swapped order…` (1126) and `編輯 opens the sheet…` (1160), update `renders one .rule-row per rule…` (1029), `lists the rules with a state badge` (333), the three usage tests (1189–1209) and `an expired rule shows the 已過期 badge in the warning colour` (1391, keeps `data-state="expired"` + `.warn`) to the new markup; `:1110` (expects `.rule-state` text `生效中` on an active row) now expects no `.rule-state`; `:1145` (`.move-up`) goes through ⋯ 上移; every `.rule-edit` click that only opens the sheet (`:1241`, `:1255`, `:1277`, `:1302`, `:1322`, `:1889`) clicks `.rule-open`. Add:
    - `回饋規則 rows render through app-account-rules with 基本 and 來自 MOZE kept` (4.7b)
    - `dragging the third rule above the first sends one order request with C, A, B and shows that order before the response` (spec scenario; `reorderRewardRules` called once with `(accountId, [C, A, B])`)
    - `a failed order request shows the message and restores the saved order` (Review Focus 4)
    - `a failed order request whose re-read also fails still shows the saved order` (Review Focus 4)
    - `a new reorder clears the previous rule message`
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

- Rev 2 (2026-11-10): Multica AGENT-500 dev-grok PASS-with-minor @ 836b7274 folded — drop-line index for downward moves, outside release is a drop, 4 px slop, cancel on scroll, identity-keyed rules effect, menu `pressing` guard + Home/End over enabled items, `unit` assignment + uncapped bar, one tag (state wins over 基本), `.badge` / `data-state` / `.warn` kept, `startDelete` read once in `ngOnInit`, snapshot restore before the re-read, the existing page tests that must change named.
- Owner rulings asked at plan approval: (a) phone rows keep handle + switch + ⋯ (spec) although the phone artboard draws a text-only › row; (b) a shared-cap group keeps its bar once on the first listed member in settings (the overview paints every active member).
- Spec coverage: handle (T2), name + rate chip + conditional tag (T1), posting summary (T1), cap bar for an active capped rule (T1), switch (T1), ⋯ with 編輯 / 刪除 / 上移 / 下移 (T2), tap opens the sheet (T1, T3), one order call on drop (T2, T3); scenarios "Reorder by drag" (T2 + T3) and "Disabled rule row" (T1 + T3); 「來自 MOZE」 kept (T1 + T3). Task 6.1 (screens at 1440 / 820 / 390) stays for the wrap-up after C3; the release evidence run covers this PR's rows.
- After this PR the live probe's `rr-settings` / `acct-sections` markers need a look: they must not depend on `.rule-edit`, `.move-up`, `.move-down`, `.rule-period`, `.rule-window` or `.rule-usage` (check at release; add `rule-handle` / `rule-menu` / `rule-drop-line` + 拖曳排序 / 更多動作).
