# Account Settings Sections (PR C1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the 帳戶設定 page as six titled sections with shared stepper / amount-field / picker controls, sticky section chips and a header save state on desktop, grouped rows with a 放棄/儲存 bar on phone, and a `1 點 = 1 元` switch — leaving the 外觀 block (PR C2) and the reward-rule rows (PR C3) as they are today.

**Architecture:** Three new presentational components under `components/ui/` (`app-stepper`, `app-amount-field`, `app-picker`) replace the page's native number inputs and selects; the page template is re-grouped into `<section>` blocks keyed by `SECTIONS`; a small set of signals (`activeSection`, `invalidSections`, `dirtyCount`) drives the chips and both save surfaces. Form state stays string-typed as today (`AccountForm`), validation and the save flow are unchanged except for the error→section jump and the new `cash_equivalent` field.

**Tech Stack:** Angular 21 standalone components, signals, `ChangeDetectionStrategy.OnPush` (all three new components and the page); vitest via `cd frontend && npx ng test --watch=false --include=<glob>`; SCSS; `LayoutModeService` for the 760 px breakpoint.

**Spec:** `openspec/changes/polish-reward-and-account-settings/specs/frontend-accounting-ledger/spec.md` (Requirement "Account settings page") and `openspec/changes/polish-reward-and-account-settings/design.md` §D4. §D5 (外觀) and §D6 (rule rows) are out of scope. Artboards: `design_handoff_v3/design_refs/AccountSettingsDesktop.dc.html`, `AccountSettingsPhone.dc.html` — where an artboard disagrees with the spec or the data model, the spec and the model win (see Global Constraints).

## Global Constraints

- Section order and titles verbatim: 基本 · 外觀 · 信用卡 · 回饋規則 · 國外交易 · 其他. The 回饋規則 chip reads `回饋規則 (N)` with the rule count.
- Rows per section (spec "Sections"), all existing form fields, nothing added except `cash_equivalent`:
  - 基本: 名稱, 主幣種, 帳戶分組, 初始金額, 帳單週期 (結帳日 stepper, empty = 月底, **for every account**), 納入總餘額, `1 點 = 1 元` (non-credit only).
  - 外觀: today's Logo block, logo grid and 圖示/顏色 row moved **unchanged** into the section (C2 rebuilds it).
  - 信用卡: 信用帳戶 switch in the section header; when on: 繳款期限, 信用額度, 額度共用 (multi-select checkbox list, as today), 主帳戶, 自動扣繳. When off: `<p class="card-hint">打開「信用帳戶」才會出現繳款期限、額度與自動扣繳。</p>` — 結帳日 is NOT in this list.
  - 回饋規則: today's `.rules-block` / `.rules-new` moved **unchanged** into the section (C3 rebuilds it); the existing `.rules-all` link relabelled 「所有卡片的規則 ›」 (the kept test asserting `全部卡片` is updated) and the current window text in the header.
  - 國外交易: 國外交易手續費 switch in the header; when on: 費率 (%), 計算方式 (the existing `roundingOptions` — keep/round/floor/ceil; the artboard's 另記一筆／併入金額 copy is NOT used), 退款時退回.
  - 其他: 備註, 還原 MOZE 設定 (`.reset-flag`, only when the flag is set), 封存帳戶 switch, 刪除帳戶 (`.delete-account`, as today).
- Breakpoint via `LayoutModeService.mode()` (`'phone'` below 760; tests force it with `TestBed.inject(LayoutModeService).set('phone')`). `≥ 760`: sticky chips + header 儲存 button + `有 N 項未儲存`. `< 760`: grouped rows, controls open in one bottom sheet, bottom bar 放棄 / 儲存 above the tab bar while dirty (the floating 記一筆 already does not exist on phone — `shell.ts` `showFab`; no shell change).
- Ranges follow the page's `validDay` bounds and the API (`writes.py`): 結帳日 1–31 or 月底 (`null`); 每月固定日 1–31; 結帳後 1–120 (`due_value` `le=120`; `localErrors` keeps its 1–366 text, the API rejects 121+ as today). Never the artboard's 1–28 / 15. A stored value is never clamped on load.
- Amount fields carry **decimal strings**, never `number`: `credit_limit`, `opening_balance`, `fx_fee_pct` stay `string` in `AccountForm` (kept test "keeps every digit of balances ≥ 1e12").
- Copy pinned by spec scenarios: cycle line `本期 10/27–11/26` (`本期 ` + `MM/DD–MM/DD`, no spaces around the dash) for every account, `每月 1 日 – 月底` when closing day is empty (today's text); due line keeps today's `結帳日後 20 天 → 11/04` / `每月 5 日 → …`. Due-rule copy: 未設定 · 每月固定日 · 結帳後.
- Existing spec hooks keep working with the same host classes (`.closing-day`, `.due-rule`, `.due-value`, `.credit-limit`, `.opening-input`, `.group-select`, `.master-select`, `.autopay-select`, `.fx-pct`, `.fx-rounding`, `.sharing-option`, `.include-toggle`, `.credit-toggle`, `.fx-toggle`, `.archive-toggle`, `.save`, `.field-error`, `.form-message`, `.cycle-preview`, `.due-preview`, `.delete-account`, `.reset-flag`). Tests that drove a native `<input>`/`<select>` are rewritten to drive the component (`valueChange` / open picker + click `[role=option]`); the file has 68 `it` blocks and none is deleted.
- Line icons only (`theme/line-icons.spec.ts` gate): chevrons and carets are inline SVG.
- No real account names, ids or amounts in tests or fixtures. No new npm dependencies.

## Review Focus

1. 信用帳戶 toggled off then on must restore the previously entered 繳款期限 / 額度 values, not reset them — test in Task 6.
2. Saving while scrolled to 其他 with a server-side 422 on 主帳戶 must jump to 信用卡 and mark its chip; a local 結帳日 error must jump to 基本 — tests in Task 8.
3. Picker opened on phone and closed by overlay tap or Escape must keep the value and return focus to its trigger — test in Task 3.
4. Amount field typed as `200,000`, `200000` or ` 200000 ` must all emit `'200000'`; `''` stays `''`; `9007199254740993` round-trips digit-for-digit — tests in Task 2.
5. A card whose closing day is 29–31, and an account with no closing day (月底), must load, display and re-save unchanged — tests in Task 1 and Task 6.
6. The phone sheet for a stepper / amount-field row must trap Tab, close on Escape and return focus to the row — test in Task 8.

---

### Task 1: `app-stepper`

**Files:**
- Create: `frontend/src/app/components/ui/stepper/stepper.ts`, `stepper.scss`, `stepper.spec.ts`

**Interfaces:**
- Produces: `StepperComponent` (`app-stepper`, OnPush); inputs `value: number | null`, `min: number` (default 1), `max: number` (default 31), `nullable: boolean` (default false), `nullLabel: string` (default `—`), `suffix: string` (default `''`), `ariaLabel: string` (required), `decLabel: string` (default `減一`), `incLabel: string` (default `加一`), `disabled: boolean`, `invalid: boolean`; output `valueChange: number | null`.
- Markup: `[role=group][attr.aria-label=ariaLabel]` > `<button class="dec" aria-label=decLabel>−</button><button class="val" aria-label="輸入數值">{{ value ?? nullLabel }} {{ suffix }}</button><button class="inc" aria-label=incLabel>＋</button>`. Tapping `.val` swaps in `<input class="val-input" inputmode="numeric" [attr.aria-label]=ariaLabel>` pre-filled; Enter or blur emits the parsed integer clamped to [min, max] (empty → `null` when nullable, else no emit), Escape restores without emitting.
- Stepping: 加一 at `null` → `min`; 減一 at `min` → `null` when nullable (else disabled); 加一 at `max` disabled. ArrowUp/ArrowDown on the group do the same.

- [ ] **Step 1: Write the failing test** `stepper.spec.ts`: `renders value with suffix` (`25 號`); `加一 emits 26 and 減一 emits 24`; `加一 is disabled at max 31`; `nullable: 減一 at 1 emits null and renders the null label`; `nullable: 加一 at null emits min`; `non-nullable: 減一 disabled at min`; `tapping the value opens an input; typing 25 + Enter emits 25`; `typing 27 then blur emits 27`; `typing 40 with max 31 emits 31`; `empty input on a nullable stepper emits null`; `Escape restores the old value without emitting`; `ArrowUp on the group emits value+1`.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='**/ui/stepper/*.spec.ts'` → FAIL (component missing).
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-stepper control`.

### Task 2: `app-amount-field`

**Files:**
- Create: `frontend/src/app/components/ui/amount-field/amount-field.ts`, `amount-field.scss`, `amount-field.spec.ts`

**Interfaces:**
- Produces: `AmountFieldComponent` (`app-amount-field`, OnPush); inputs `value: string`, `prefix: string` (default `''`), `suffix: string` (default `''`), `ariaLabel: string` (required), `placeholder: string`, `disabled: boolean`, `invalid: boolean`; output `valueChange: string`. Native `<input class="amt" inputmode="decimal">`; on `input` emits `stripAmount(raw)`; while unfocused shows `groupAmount(value)`; on focus shows the raw value.
- Export `stripAmount(raw: string): string` — removes `,` and whitespace, nothing else (`''` → `''`; `'abc'` → `'abc'`, left for server/local validation). Export `groupAmount(text: string): string` — inserts `,` every three integer digits by string operations, keeps sign and the fractional part verbatim (`'9007199254740993'` → `'9,007,199,254,740,993'`, `'-1234567890123.45'` → `'-1,234,567,890,123.45'`, non-numeric text returned unchanged). No `Number()` anywhere.

- [ ] **Step 1: Write the failing test**: `stripAmount` cases `'200,000'`, `'200000'`, `' 200000 '` → `'200000'`; `''` → `''`; `groupAmount('9007199254740993')` → `'9,007,199,254,740,993'`; `groupAmount('-1234567890123.45')` → `'-1,234,567,890,123.45'`; `groupAmount('1.500')` keeps `'1.500'`; component `shows prefix and grouped value when unfocused`; `shows raw value while focused`; `typing 200,000 emits '200000'`; `sets aria-invalid when invalid`.
- [ ] **Step 2: Run** `--include='**/ui/amount-field/*.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-amount-field control`.

### Task 3: `app-picker`

**Files:**
- Create: `frontend/src/app/components/ui/picker/picker.ts`, `picker.html`, `picker.scss`, `picker.spec.ts`
- Reuse: `trapFocus`, `restoreOverlayFocus`, `isHandledKey` from `components/accounting/accounting-ui.ts`; the `phone()` pattern from `components/accounting/account-picker/account-picker.ts` (do not import that component).

**Interfaces:**
- Produces: `PickerComponent` (`app-picker`, OnPush); `PickerOption { value: string; label: string; hint?: string; disabled?: boolean }`; inputs `options: PickerOption[]`, `value: string`, `label: string` (required; sheet title + aria-label), `disabled: boolean`; output `change: string`. Trigger `<button class="pk-trigger" aria-haspopup="listbox" [attr.aria-expanded] [attr.data-value]="value()">{{ selected label }}<svg chevron/></button>`; open: `[role=listbox]` of `[role=option][attr.aria-selected][attr.data-value]`; `≥ 760` a popover under the trigger, `< 760` `.overlay > .sheet[role=dialog][aria-modal=true]`. Escape / overlay tap / outside pointerdown close without emitting and return focus to the trigger; Tab is trapped in the open panel; ArrowUp/Down move the active option; Enter chooses.

- [ ] **Step 1: Write the failing test**: `shows the selected option label and data-value`; `choosing an option emits change, closes and refocuses the trigger`; `Escape closes without emitting`; `phone: overlay tap closes, keeps value, returns focus to trigger` (force phone mode); `Tab inside the open panel stays inside`; `disabled option is not selectable`.
- [ ] **Step 2: Run** `--include='**/ui/picker/*.spec.ts'` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ui): add app-picker control`.

### Task 4: `cash_equivalent` on the form

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (`AccountInput.cash_equivalent?: boolean`)
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts` (`AccountForm.cash_equivalent: boolean`, `EMPTY_FORM`, `formFrom`, `formToInput`, `accountToInput`), `account-settings.html`, `account-settings.spec.ts`

**Interfaces:**
- Produces: `AccountForm.cash_equivalent: boolean`; `formToInput(form, members, loaded: AccountDetail | null)` adds `cash_equivalent` only when `!form.is_credit` and the value differs from `loaded?.cash_equivalent ?? false` (omitted otherwise, so a PUT keeps the stored value); `accountToInput` never sets it.
- Template: after 納入總餘額: `@if (!form().is_credit) { <app-switch class="cash-equivalent" ariaLabel="1 點 = 1 元" …/> <small class="hint">回饋入這個帳戶時當成金額計算</small> }`.

- [ ] **Step 1: Write the failing tests**: `shows the 1 點 = 1 元 switch and helper on a non-credit account`; `hides it on a credit account`; `toggling it on sends cash_equivalent true`; `untouched switch omits cash_equivalent from the PUT body`.
- [ ] **Step 2: Run** `--include='**/account-settings/*.spec.ts'` → the four FAIL, 68 others pass.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS (72).
- [ ] **Step 5: Commit** `feat(account-settings): add 1 點 = 1 元 cash_equivalent switch`.

### Task 5: Section split, sticky chips, header save state

**Files:**
- Modify: `account-settings.html` (six `<section class="sec" [id]="'s-' + id" [attr.aria-labelledby]="'h-' + id">` blocks with `<h3>` titles; every existing row moved into its section per Global Constraints — 納入總餘額 and 備註 move accordingly; 外觀 and 回饋規則 contents unchanged), `account-settings.ts`, `account-settings.scss`, `account-settings.spec.ts`

**Interfaces:**
- Produces: `type SectionId = 'basic'|'look'|'card'|'reward'|'fx'|'more'`; `SECTIONS: readonly { id: SectionId; title: string }[]`; signals `activeSection = signal<SectionId>('basic')`, `invalidSections = signal<Set<SectionId>>(new Set())`; `dirtyCount = computed(() => isDirty() ? changedFormKeys + (sharing differs ? 1 : 0) + (rule sheet dirty ? 1 : 0) : 0)` (never 0 while `isDirty()` is true); `jumpTo(id: SectionId)` scrolls `#s-<id>` into view and sets `activeSection`. Chips: `<nav class="sec-chips" aria-label="設定區塊"><button [attr.aria-current]="activeSection() === s.id ? 'true' : null" [class.invalid]="invalidSections().has(s.id)">`. Header: existing `.save` button plus `<span class="dirty-count">有 {{ dirtyCount() }} 項未儲存</span>` when `> 0`. Scroll-spy: one `IntersectionObserver` created in `afterNextRender`, observing the six sections, setting `activeSection` to the topmost intersecting one; `disconnect()` in `DestroyRef.onDestroy`; skipped when `IntersectionObserver` is undefined.

- [ ] **Step 1: Write the failing tests**: `renders six section headings in order` (`基本 外觀 信用卡 回饋規則 國外交易 其他`); `回饋規則 chip shows the rule count`; `clicking a chip marks it current and scrolls` (spy `scrollIntoView`); `scroll-spy marks the section reported in view` (stub `IntersectionObserver` with a captured callback, fire `[{target: #s-card, isIntersecting: true}]`, expect the 信用卡 chip `aria-current`); `disconnects the observer on destroy`; `editing the name shows 有 1 項未儲存; a second field shows 有 2 項未儲存`; `an open dirty rule sheet keeps the count at least 1`; `successful save clears the count`; `基本 holds 名稱 … 納入總餘額 and 其他 holds 備註` (row labels per section).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; SCSS `.sec-chips { position: sticky; top: 0 }` at `@media (min-width: 760px)`, `display: none` below.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): split into sections with sticky chips and save state`.

### Task 6: 基本 / 信用卡 controls

**Files:**
- Modify: `account-settings.html`, `account-settings.ts`, `account-settings.spec.ts`, `frontend/src/app/components/accounting/cycle.ts`

**Interfaces:**
- Consumes Tasks 1–3.
- 基本: 帳戶分組 → `<app-picker class="group-select" label="帳戶分組">` options `''`→`未分組` + groups (`String(id)`); handler `setIdField('group_id', value)` (existing). 初始金額 → `<app-amount-field class="opening-input" ariaLabel="初始金額">` bound to `opening_balance`. 帳單週期 → `<app-stepper class="closing-day" ariaLabel="結帳日" suffix="號" [min]="1" [max]="31" [nullable]="true" nullLabel="月底" decLabel="減一天" incLabel="加一天" [value]="closingDay()" (valueChange)="set('closing_day', $event === null ? '' : String($event))">` + `<small class="cycle-preview">{{ cycleLabel() }}</small>`; `cycleLabel` becomes `closingDay() === null ? '每月 1 日 – 月底' : '本期 ' + cycleText(period())`, with `export function cycleText(period: Period): string` = `shortDate(start) + '–' + shortDate(end)` added to `cycle.ts` beside `periodLabel` (which other callers keep).
- 信用卡 header: `<app-switch class="credit-toggle" ariaLabel="信用帳戶">`. When on: 繳款期限 → `<app-seg-toggle class="due-rule" ariaLabel="繳款期限">` options `[{value:'none',label:'未設定'},{value:'fixed_day',label:'每月固定日'},{value:'days_after_closing',label:'結帳後'}]`, `[value]="form().due_rule || 'none'"`, `(change)="set('due_rule', $event === 'none' ? '' : $event)"`; `<app-stepper class="due-value">` shown when `due_rule !== ''`, `suffix` `號`/`天`, `max` 31 (fixed_day) / 120 (days_after_closing), `min` 1, non-nullable; `<small class="due-preview">{{ dueLabel() }}</small>` unchanged. 信用額度 → `<app-amount-field class="credit-limit" prefix="$">` bound to `credit_limit`. 額度共用 → today's `.sharing-list` checkbox block unchanged. 主帳戶 / 自動扣繳 → `<app-picker class="master-select">` / `<app-picker class="autopay-select">`, options `''`→`無` + `masterOptions()` / `autopayOptions()` mapped `{ value: String(id), label: accountLabel(account) }` (archived label `舊卡（已封存）` comes from `accountLabel`), handler `setIdField`.
- Form values are kept in memory while 信用帳戶 is off (today's behaviour; `formToInput` already nulls them on save).

- [ ] **Step 1: Write / rewrite failing tests**: spec scenario `closing day 25 on 2026-11-07, 加一 → stepper reads 26 號 and cycle line reads 本期 10/27–11/26`; `account with closing_day null shows 月底 and 每月 1 日 – 月底, saves closing_day null`; `account with closing_day 31 shows 31 號 and saves 31`; `due preview: cycle 本期 09/16–10/15 and 結帳日後 20 天 → 11/04` (rewrite of the kept preview test; seg-toggle click on 每月固定日 + stepper typed 5 → `每月 5 日 → …`); `未設定 saves due_rule null and due_value null`; `信用帳戶 off keeps 帳單週期 in 基本 and PUTs closing_day 15` (rewrite of the kept bank test); `信用帳戶 off then on restores 繳款期限 and 信用額度`; `.master-select` / `.autopay-select` tests rewritten to open the picker and read `[role=option]` labels / `data-value` (`無, 玉山信用卡, 玉山 UNI, 玉山 U Bear`; archived `舊卡（已封存）` selected and saved unchanged); `.group-select` test rewritten likewise; the `≥ 1e12` digits test reads `.credit-limit .amt` / `.opening-input .amt` values; the "reads today" test asserts `本期 09/16–10/15`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): stepper, picker and due three-way in 基本/信用卡`.

### Task 7: 國外交易 and 其他 sections

**Files:**
- Modify: `account-settings.html`, `account-settings.scss`, `account-settings.spec.ts`

**Interfaces:**
- 國外交易 header `<app-switch class="fx-toggle" ariaLabel="國外交易手續費">` bound to `fx_fee_on`; rows when on: 費率 `<app-amount-field class="fx-pct" suffix="%" ariaLabel="手續費百分比">` bound to `fx_fee_pct` (string; `1.500` stays `1.500`), 計算方式 `<app-picker class="fx-rounding" label="計算方式">` from `roundingOptions` (`{value: mode, label}`), 退款時退回 `<app-switch class="fx-refund-toggle" ariaLabel="退款時退回手續費">`. 其他: 備註 `<input class="note-input">` (as today), the `.custom-note` + `.reset-flag` block moved here, 封存帳戶 `<app-switch class="archive-toggle">`, 刪除帳戶 row as today.

- [ ] **Step 1: Write failing tests**: `fx rows hidden until the fee switch is on`; `fee percent typed 1.5 saves fx_fee_pct '1.5'`; `計算方式 picker lists the four rounding labels and saves the chosen mode`; `其他 holds 備註, 還原 MOZE 設定 (when flagged), 封存 switch and 刪除 button`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(account-settings): 國外交易 and 其他 sections`.

### Task 8: Phone layout, save bar, error scroll

**Files:**
- Modify: `account-settings.html`, `account-settings.scss`, `account-settings.ts`, `account-settings.spec.ts`

**Interfaces:**
- `phone = computed(() => inject(LayoutModeService).mode() === 'phone')`. On phone, 基本 / 信用卡 / 國外交易 / 其他 render as `.card > .row` lists: label, hint, value, chevron SVG. Switch rows keep the switch inline. Picker rows render the `app-picker` trigger as the row (its own sheet opens; nothing nested). Stepper and amount-field rows are `<button class="row">`s that set `sheetRow = signal<string | null>(null)` to the field key; one page-level `<div class="overlay" (click)="closeSheet()"><div class="sheet" role="dialog" aria-modal="true" [attr.aria-label]="row label" tabindex="-1">` hosts that one control plus a 完成 button; `trapFocus` on Tab, Escape → `closeSheet()`, focus returns to the row via `restoreOverlayFocus`. 外觀 and 回饋規則 keep today's markup inside their section cards on phone (C2/C3 convert them).
- `.save-bar` (phone only) follows the entry-detail bottom bar pattern (`entry-detail.scss` ~L164: `position: sticky; bottom: 0; padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px))` inside the page, which the layout already keeps above the 64 px `.m-tabbar`), shown when `dirtyCount() > 0`: `<span>有 {{ dirtyCount() }} 項未儲存</span><button class="discard">放棄</button><button class="save-bar-save">儲存</button>`; 放棄 → `form.set(baseline()); sharing.set(sharingBaseline())`; 儲存 → `save()`.
- `FIELD_SECTION: Record<string, SectionId>` = name/currency/group_id/opening_balance/closing_day/include_in_total/cash_equivalent → basic; icon/color/logo → look; due_rule/due_value/credit_limit/combined_account_id/credit_sharing_id/credit_sharing_members/auto_pay_account_id → card; fx_fee_pct/fx_fee_rounding → fx; note/is_archived → more. `jumpToFirstError(fields: Record<string, string>)`: first key in `SECTIONS` order → `invalidSections.set(new Set([that id]))`, `jumpTo(id)`, focus the first `[aria-invalid=true]` in that section. Called from `save()` at all three exits: empty name, `localErrors` non-empty, and the 422 `fieldErrors(err)` branch. `invalidSections` is cleared on the next successful save and when the offending field changes.

- [ ] **Step 1: Write failing tests**: `phone: 基本 renders label/value rows and the 結帳日 row opens a sheet with the stepper`; `phone sheet: Tab is trapped, Escape closes and refocuses the row`; `phone: 放棄/儲存 bar appears only when dirty`; `放棄 restores loaded values and hides the bar`; `bar hides after a successful save`; `422 on combined_account_id jumps to 信用卡 and marks its chip invalid` (existing 422 test pattern + `scrollIntoView` spy); `local 結帳日 error jumps to 基本`; `empty name jumps to 基本`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS; then full suite `cd frontend && npx ng test --watch=false` → all green (3202 + new).
- [ ] **Step 5: Commit** `feat(account-settings): phone rows, save bar and error scroll`.

### Task 9: tasks.md bookkeeping

**Files:**
- Modify: `openspec/changes/polish-reward-and-account-settings/tasks.md`: tick 4.1, 4.3, 4.5, 4.6; leave 4.2, 4.4 unticked; under 4.7 add a sub-line `  - "Account settings page" scenarios + 繳款期限 未設定 → PR C1; "Reward rule rows" scenarios → PR C3` and leave 4.7 unticked.
- Run: `openspec validate polish-reward-and-account-settings --strict` → passes.

- [ ] **Step 1: Edit tasks.md**, run validate, commit `docs(openspec): mark C1 account-settings tasks`.
