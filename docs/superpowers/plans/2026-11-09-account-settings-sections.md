# Account Settings Sections (PR C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the 帳戶設定 page as six titled sections with shared stepper / amount-field / picker controls, sticky section chips and a header save state on desktop, grouped rows with a 放棄/儲存 bar on phone, and a `1 點 = 1 元` switch — without touching the 外觀 block (PR C2) or the reward-rule rows (PR C3).

**Architecture:** Three new presentational components under `components/ui/` (`app-stepper`, `app-amount-field`, `app-picker`) wrap the native inputs the page uses today; the page template is re-grouped into `<section>` blocks keyed by `SECTIONS`; a small `sectionNav` signal set (active chip, invalid sections, dirty count) drives chips and both save surfaces. Form state, validation and save flow in `account-settings.ts` stay as they are; only the `cash_equivalent` field and the due-rule three-way state are added.

**Tech Stack:** Angular 21 standalone components, signals, OnPush; vitest via `cd frontend && npx ng test --watch=false --include=<glob>`; SCSS with the repo's 760 px breakpoint.

**Spec:** `openspec/changes/polish-reward-and-account-settings/specs/frontend-accounting-ledger/spec.md` (Requirement "Account settings page") and `openspec/changes/polish-reward-and-account-settings/design.md` §D4 (sections), §D5/§D6 are out of scope here. Artboards: `design_handoff_v3/design_refs/AccountSettingsDesktop.dc.html`, `AccountSettingsPhone.dc.html`.

## Global Constraints

- Section order and titles verbatim: 基本 · 外觀 · 信用卡 · 回饋規則 · 國外交易 · 其他. Chip for 回饋規則 shows the rule count in parentheses, e.g. `回饋規則 (3)`.
- Breakpoint is `760px`: `≥ 760` sticky chips + header 儲存 button with `有 N 項未儲存`; `< 760` grouped rows, controls open in a bottom sheet, bottom bar `放棄` / `儲存` replaces the floating 記一筆 button while the form is dirty.
- Steppers read `− <value> ＋`; closing-day suffix `號`, due-days suffix `天`; closing day clamps 1–28; due days clamps 1–60; fixed due day clamps 1–28. Button labels `減一` / `加一` (aria), artboard copy `減一天` / `加一天` for day steppers.
- Due rule three-way copy: `未設定` · `結帳後` · `每月固定日`. `未設定` saves `due_rule: null`; previews use the spec's wording, e.g. `09/16 – 10/15 → 11/04`.
- Cycle preview copy: `本期 10/27–11/26`（see spec scenario "Closing day stepper"）.
- `1 點 = 1 元` switch appears in 基本 only when the account is not a credit account; helper text `回饋入這個帳戶時當成金額計算`; it maps to `cash_equivalent` (PUT omits the key when unchanged; never shown for credit accounts).
- Existing spec hooks keep working: the new controls carry the same host classes the current tests target (`.closing-day`, `.due-rule`, `.due-value`, `.credit-limit`, `.master-select`, `.autopay-select`, `.include-toggle`, `.credit-toggle`, `.save`, `.field-error`, `.form-message`, `.cycle-preview`, `.due-preview`, `.delete-account`, `.reset-flag`). Tests that drive a native `<input>` are rewritten to drive the component's `(valueChange)` instead; no test is deleted.
- Line icons only (`theme/line-icons.spec.ts` gate): the picker caret and drag glyphs are inline SVG, never emoji.
- No real account names, ids or amounts in tests or fixtures.
- No new npm dependencies.

## Review Focus

1. Switching 信用帳戶 off then on must restore the previously entered closing day / due rule, not reset them to defaults — a user toggling to peek loses nothing. Test added in Task 6.
2. Saving with an invalid 信用額度 while the user is scrolled to 其他 must scroll to the 信用卡 section and mark its chip, not silently fail. Test added in Task 8.
3. Picker opened on phone and closed by tapping the overlay must leave the value unchanged and return focus to its trigger. Test added in Task 3.
4. Amount field typed as `200,000` or `200000` or ` 200000 ` must all parse to `200000`; an empty string yields `null`, not `0`. Test added in Task 2.
5. Dirty count must fall back to 0 after a successful save and the bottom bar must disappear even when the server echoes normalised values (e.g. trimmed name). Test added in Task 8.

---

### Task 1: `app-stepper`

**Files:**
- Create: `frontend/src/app/components/ui/stepper/stepper.ts`, `stepper.scss`, `stepper.spec.ts`

**Interfaces:**
- Produces: `StepperComponent` selector `app-stepper`; inputs `value: number`, `min: number` (default 1), `max: number` (default 31), `suffix: string` (default `''`), `ariaLabel: string` (required), `decLabel: string` (default `減一`), `incLabel: string` (default `加一`), `disabled: boolean`; output `valueChange: number`. Renders `<button aria-label=decLabel>−</button><b class="val">{{value}} {{suffix}}</b><button aria-label=incLabel>＋</button>` inside `[role=group][aria-label=ariaLabel]`.

- [ ] **Step 1: Write the failing test** `stepper.spec.ts`: `renders value with suffix` (`25 號`), `加一 emits value+1 and 減一 value-1`, `clamps at min and max and disables the button at the edge`, `ArrowUp/ArrowDown on the group step the value`.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='**/ui/stepper/*.spec.ts'` → FAIL (component missing).
- [ ] **Step 3: Implement** `StepperComponent` per Interfaces; buttons `type="button"`, `[disabled]` at the bound edge; group `tabindex="0"` handles ArrowUp/ArrowDown.
- [ ] **Step 4: Run** the same command → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-stepper control`.

### Task 2: `app-amount-field`

**Files:**
- Create: `frontend/src/app/components/ui/amount-field/amount-field.ts`, `amount-field.scss`, `amount-field.spec.ts`

**Interfaces:**
- Produces: `AmountFieldComponent` selector `app-amount-field`; inputs `value: number | null`, `prefix: string` (default `''`, use `$`), `suffix: string` (default `''`, use `%`), `ariaLabel: string` (required), `decimals: number` (default 0), `placeholder: string`, `disabled: boolean`, `invalid: boolean`; output `valueChange: number | null`. Native `<input inputmode="decimal">`; on `input` emits parsed number; on `blur` reformats with `toLocaleString('en-US', {maximumFractionDigits: decimals})`.
- Export `parseAmount(raw: string): number | null` (strips commas/spaces; `''` → `null`; NaN → `null`).

- [ ] **Step 1: Write the failing test**: `parseAmount` cases `'200,000'`, `'200000'`, `' 200000 '` → `200000`; `''` → `null`; `'abc'` → `null`; component `shows prefix and formatted value on blur` (`$` + `200,000`); `emits null when cleared`; `sets aria-invalid when invalid`.
- [ ] **Step 2: Run** `--include='**/ui/amount-field/*.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-amount-field control`.

### Task 3: `app-picker`

**Files:**
- Create: `frontend/src/app/components/ui/picker/picker.ts`, `picker.html`, `picker.scss`, `picker.spec.ts`
- Reference pattern: `components/accounting/account-picker/account-picker.html` (overlay + `.sheet` below 760, `.acct-popover` above; `data-overlay` click closes).

**Interfaces:**
- Produces: `PickerComponent` selector `app-picker`; `PickerOption { value: string; label: string; hint?: string; disabled?: boolean }`; inputs `options: PickerOption[]`, `value: string`, `label: string` (required; sheet title and aria-label), `disabled: boolean`, `hint: string`; output `change: string`. Trigger `<button aria-haspopup="listbox" [attr.aria-expanded]>` shows the selected label + inline-SVG chevron; open state renders `[role=listbox]` with `[role=option][aria-selected]`; `< 760` wraps the list in `.overlay > .sheet[role=dialog][aria-modal=true]`.

- [ ] **Step 1: Write the failing test**: `shows the selected option label`; `choosing an option emits change and closes`; `Escape closes without emitting`; `overlay tap on phone closes, keeps value, returns focus to trigger` (set `window.innerWidth` 390 + `matchMedia` stub as account-picker.spec does); `disabled option is not selectable`.
- [ ] **Step 2: Run** `--include='**/ui/picker/*.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** per Interfaces; reuse the account-picker's `phone()` media-query signal approach (do not import AccountPickerComponent).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-picker control`.

### Task 4: `cash_equivalent` on the form

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (add `cash_equivalent?: boolean` to `AccountInput`)
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts` (`AccountForm.cashEquivalent: boolean`, `EMPTY_FORM`, `accountToInput`, `formFrom`, `formToInput`)
- Modify: `account-settings.html` (基本 block switch), `account-settings.spec.ts`

**Interfaces:**
- Produces: `AccountForm.cashEquivalent: boolean`; `formToInput` emits `cash_equivalent` only when it differs from the loaded account (omit key otherwise); for credit accounts always omitted.

- [ ] **Step 1: Write the failing tests** in `account-settings.spec.ts`: `shows 1 點 = 1 元 switch with helper on a non-credit account`; `hides it on a credit account`; `toggling it sends cash_equivalent true on save`; `untouched switch omits cash_equivalent from the PUT body`.
- [ ] **Step 2: Run** `--include='**/account-settings/*.spec.ts'` → the four FAIL.
- [ ] **Step 3: Implement** the model/form/template changes; switch uses `app-switch` with `ariaLabel="1 點 = 1 元"`, host class `.cash-equivalent`, helper `<small>回饋入這個帳戶時當成金額計算</small>`.
- [ ] **Step 4: Run** → PASS, existing 56 still green.
- [ ] **Step 5: Commit** `feat(account-settings): add 1 點 = 1 元 cash_equivalent switch`.

### Task 5: Section split, sticky chips, header save state (desktop)

**Files:**
- Modify: `account-settings.html` (wrap existing rows into `<section id="s-basic" aria-labelledby="h-basic">` … six sections; 外觀 and 回饋規則 blocks move unchanged inside their sections), `account-settings.ts`, `account-settings.scss`, `account-settings.spec.ts`

**Interfaces:**
- Produces: `SECTIONS: readonly { id: 'basic'|'look'|'card'|'reward'|'fx'|'more'; title: string }[]`; signals `activeSection`, `invalidSections: Set<id>`, `dirtyCount: number` (count of `AccountForm` keys differing from the loaded form); method `jumpTo(id)` (`scrollIntoView({block:'start'})` + sets active). Chips: `<nav class="sec-chips" aria-label="設定區塊">` with `<button [attr.aria-current]="active ? 'true' : null" [class.invalid]>`. Header: `.save` button + `<span class="dirty-count">有 N 項未儲存</span>` when `dirtyCount() > 0`. Scroll-spy via one `IntersectionObserver` on the six sections (guard `typeof IntersectionObserver === 'undefined'` in tests).

- [ ] **Step 1: Write the failing tests**: `renders six section headings in order`; `回饋規則 chip shows the rule count`; `clicking a chip marks it current`; `editing the name shows 有 1 項未儲存 and two edits show 有 2 項未儲存`; `successful save clears the count`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; SCSS: `.sec-chips { position: sticky; top: 0 }` at `@media (min-width: 760px)`, hidden below.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): split into sections with sticky chips and save state`.

### Task 6: 信用卡 section controls

**Files:**
- Modify: `account-settings.html`, `account-settings.ts`, `account-settings.spec.ts`

**Interfaces:**
- Consumes Tasks 1–3 components.
- Produces: `AccountForm.dueMode: 'none' | 'after' | 'fixed'` derived in `formFrom` (`due_rule === null` → `none`); `formToInput` maps `none` → `due_rule: null`. Closing day → `<app-stepper class="closing-day" suffix="號" [min]="1" [max]="28" decLabel="減一天" incLabel="加一天">` + `<small class="cycle-preview">`. Due: `<app-seg-toggle class="due-rule">` options `none/after/fixed` → labels `未設定/結帳後/每月固定日`; `<app-stepper class="due-value">` suffix `天` (after, 1–60) or `號` (fixed, 1–28), hidden for `none`; `<small class="due-preview">`. 信用額度 → `<app-amount-field class="credit-limit" prefix="$">`. 額度共用 / 主帳戶 / 自動扣繳 → `app-picker` with classes `.sharing-select`, `.master-select`, `.autopay-select` (existing `.sharing-option` tests rewritten to picker options). When 信用帳戶 is off render `<p class="card-hint">打開「信用帳戶」才會出現結帳日、繳款期限、額度與自動扣繳。</p>`; the form keeps the card fields in memory.

- [ ] **Step 1: Write / rewrite failing tests**: spec scenario `closing day 25 → 加一 → 26 號, cycle preview 本期 10/27–11/26` (fixed clock as existing tests do); `due preview 結帳後 20 天 shows 09/16 – 10/15 → 11/04`; `未設定 saves due_rule null`; `switching 信用帳戶 off then on restores closing day and due rule`; `bank account keeps 帳單週期 copy`; existing `.closing-day`/`.due-value` tests rewired to `valueChange`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): stepper, picker and due three-way in 信用卡 section`.

### Task 7: 國外交易 and 其他 sections

**Files:**
- Modify: `account-settings.html`, `account-settings.scss`, `account-settings.spec.ts`

**Interfaces:**
- 國外交易 header carries the fee `app-switch` (`.fx-toggle`); rows: 費率 `<app-amount-field class="fx-pct" suffix="%" [decimals]="2">`, 計算方式 `app-seg-toggle` `另記一筆手續費 / 併入金額`, 退款時退回 `app-switch` (`.fx-refund-toggle`). Field names come from the existing `AccountForm` fx fields; no new fields. 其他 keeps the existing 還原 MOZE 設定 (`.reset-flag`) and 封存/刪除 (`.delete-account`) buttons side by side under an `其他` heading.

- [ ] **Step 1: Write failing tests**: `fx rows hidden until the fee switch is on`; `fee percent typed as 1.5 saves 1.5`; `其他 section holds reset and delete buttons`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): 國外交易 and 其他 sections`.

### Task 8: Phone layout, save bar, error scroll

**Files:**
- Modify: `account-settings.html`, `account-settings.scss`, `account-settings.ts`, `account-settings.spec.ts`
- Modify: `frontend/src/app/components/shell/shell.ts` only if `showFab` does not already hide while `DirtyFormRegistry` has a dirty form — check first; if it does, no change.

**Interfaces:**
- `< 760`: each section renders as a card of rows (`.row` label / hint / value / chevron); a row tap opens that control's sheet (stepper/picker/amount-field already sheet-capable via their own `< 760` branches; the row renders the component in a `.sheet` on tap). Bottom `.save-bar` appears when `dirtyCount() > 0`: `<span>有 N 項未儲存</span><button class="discard">放棄</button><button class="save">儲存</button>`; 放棄 resets the form to the loaded account via existing reset logic and clears the count.
- `save()` with `localErrors` non-empty: compute `firstInvalidSection`, call `jumpTo(it)`, add it to `invalidSections`, and focus the first `[aria-invalid=true]` element.

- [ ] **Step 1: Write failing tests**: `phone width shows the 放棄/儲存 bar only when dirty`; `放棄 restores loaded values and hides the bar`; `save with invalid 信用額度 while scrolled elsewhere jumps to 信用卡 and marks its chip invalid` (spy on `scrollIntoView`); `bar hides after a save whose response trims the name`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; SCSS `.save-bar` fixed bottom with `env(safe-area-inset-bottom)` padding, shown only `@media (max-width: 759.98px)`.
- [ ] **Step 4: Run** → PASS; then full suite `cd frontend && npx ng test --watch=false` → all green (was 3202).
- [ ] **Step 5: Commit** `feat(account-settings): phone rows, save bar and error scroll`.

### Task 9: Spec + handoff notes

**Files:**
- Modify: `openspec/changes/polish-reward-and-account-settings/tasks.md` (tick 4.1–4.3, 4.5–4.7 items covered here; leave 外觀 and rule rows unticked with `→ PR C2/C3` notes)
- Run: `openspec validate polish-reward-and-account-settings --strict` → passes.

- [ ] **Step 1: Edit tasks.md**, run validate, commit `docs(openspec): mark account-settings section tasks for PR C1`.
