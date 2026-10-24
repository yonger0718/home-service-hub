# Polish Layout v2 — PR C (filter bar) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `<app-filter-bar>` primitive (D6) — search + up to two inline primary controls + a 篩選 button that opens the secondary filters in a popover (≥ 760 px) or a bottom sheet (phone), with active secondary filters as removable chips — adopted on 交易紀錄, 股利, 已實現損益, 現金帳戶 (account detail) and 記帳時間軸.

**Architecture:** The primitive is layout + state only: three content-projection slots (`[filterBarSearch]`, `[filterBarPrimary]`, `[filterBarSecondary]`), a `chips` input that drives both the 篩選 count and the chip row, and `remove` / `clearAll` / `reset` / `openChange` outputs. Pages keep their query signals, handlers, debounces and client-side filtering untouched; each adds a `filterChips` computed, a `removeFilter(key)` and a `resetSecondaryFilters()`, moves its existing controls into the slots and drops its own 清除篩選 button (the bar's 清除全部 replaces it). No PrimeNG popover/drawer: the panel is a custom overlay in the style of `components/accounting/sheet.scss`.

**Tech Stack:** Angular 21 (standalone, signals, OnPush, zoneless), Vitest via `cd frontend && npx ng test --watch=false --include <spec>` (zsh: quote globs), SCSS with the `--app-*` token set, `LayoutModeService.mode()` for phone detection, Playwright-in-Docker for the browser check (stubs in `~/reports/home-hub-playwright-stubs/`).

**Spec:** `openspec/changes/polish-layout-v2/` (rev 10 `8b54988` on main): `design.md` D6 (row = search + ≤ 2 primary controls + 篩選 with the active-secondary count; popover ≥ 760 px / bottom sheet below; chips under the bar, 清除全部 when ≥ 2; the per-page table), `tasks.md` §3, deltas `specs/frontend-ui-primitives/spec.md` ("FilterBar primitive", three scenarios), `specs/frontend-stock-transactions/spec.md` ("Transactions use the shared filter bar"), `specs/frontend-realized-pnl/spec.md` (MODIFIED "Realized filters"), `specs/frontend-accounting-ledger/spec.md` (MODIFIED "Timeline home page", scenario "Phone filters"). Task 1 adds rev 11: MODIFIED "Violet dividend timeline" (dividend-records) and MODIFIED "Account detail page lives at `/portfolio/accounts/:id`" (portfolio-accounts), which the deltas missed. Code inventory with verified line numbers: `.superpowers/pr-c-inventory.md` (git-ignored; implementers get the relevant lines in their brief).

## Global Constraints

- Frontend only; no API, model or copy change outside the filter UIs. Branch `fix/polish-layout-pr-c` from `origin/main` (6ae1205).
- D6 placement table (verbatim): 交易紀錄 — inline 全部／買進／賣出; behind 篩選 日期範圍, 券商, 排序. 股利 — inline 狀態 (全部 / 待收 / 已收款 / 舊資料); behind 日期範圍, 來源, 排序. 已實現損益 — inline 年度 (YTD / 年份 / 全部); behind 日期範圍, 市場 (全部 / 台股 / 海外), 券商, 僅當沖 (`role="switch"`), 排序 (owner: date range stays behind 篩選). 現金帳戶 — inline 類型; behind 日期範圍, 排序; no search. 記帳時間軸 — inline search + 帳戶; behind 類型, 類別, 專案, 標籤, 商家, 金額範圍 **at every width**.
- Button copy: `篩選` with no count when nothing secondary is active, `篩選 N` (one space) otherwise; panel buttons `套用` and `重設`; chip row button `清除全部` only when ≥ 2 chips; chip remove buttons `aria-label="移除 <chip label>"`.
- Count = number of active **secondary** filters = `chips.length`. Search, the primary control and sort are never chips and never counted (spec scenarios: date range + broker on 交易紀錄 → `篩選 2`; 僅當沖 → `篩選 1`; 類別 + 商家 on the timeline → `篩選 2`).
- Chip label format: `<欄位>：<值>` with the full-width colon (`日期：2026-01-01 – 2026-03-31`, `券商：IB`, `來源：手動`, `市場：台股`, `類別：餐飲 › 午餐`, `商家：全聯`, `標籤：午餐`, `金額：100 – 500` / `金額 ≥ 100` / `金額 ≤ 500`); 僅當沖 is the bare label `僅當沖`. Half-open date ranges print `…` for the missing end (`日期：2026-01-01 – …`).
- Filters apply live in both popover and sheet (pages already apply on change); `套用` only closes the panel; `重設` emits `reset` → the page clears its secondary filters only; `清除全部` emits `clearAll` → the page's existing `clearFilters()` (everything: search, primary, secondary; sort keeps its default as today).
- Unchanged page behaviour: 交易紀錄 300 ms search debounce; 股利 `closeForms()` on every filter change and client-side status paging; 已實現損益 client-side market filter / preset-vs-range exclusivity / `loadAll()`; 現金帳戶 300 ms `filterQuery$` debounce and the 餘額-column rule; 時間軸 IME search handling (`onQueryCompositionStart` / `onQueryInput` / `onQueryKeydown`), `QUERY_DEBOUNCE_MS`, lazy `ensureOptions()`, `.match-row` (符合條件 N 筆 + 清除篩選) and the `.empty-filtered` state.
- Panel accessibility: `role="dialog"`, `aria-label="篩選"`, `aria-modal="true"` on the sheet only; the 篩選 button carries `aria-haspopup="dialog"` and `aria-expanded`; on open focus moves to the first focusable element inside the panel, Escape and `套用` close and return focus to the 篩選 button; a pointerdown outside the host closes the popover; the sheet backdrop click closes the sheet. No Tab trap (the accounting shell's `data-overlay` trap is not reused — ruling, recorded for review).
- Colours and radii only through tokens (`--app-surface`, `--app-surface-soft`, `--app-border`, `--app-text-muted`, `--app-primary`, `--app-focus-ring`, `--radius-pill`, `--radius-sheet`); overlay z-index 1100 (above `.hub-fab` 1000).
- Every task: `cd frontend && npx ng test --watch=false --include <touched specs>` green before commit; the whole suite, `npx ng build` and `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` (repo root) green before the PR.

## Review Focus

1. **Projected secondary controls exist only while the panel is open** (`<ng-content>` inside `@if`): a page whose control holds DOM state (PrimeNG datepicker/multiselect/select, native `<select>` with `[selected]`) must re-render its current value when the panel reopens. Pinned in Task 2 (host test: value shown after close + reopen) and Task 7 (merchant text and bounds persist across reopen, replacing the old `.search-toggle` test).
2. **A chip removed while the panel is open** must update the count and the panel control together. Pinned in Task 3 (broker chip removed with the popover open → broker SegToggle shows 全部, button reads `篩選 1`).
3. **Phone sheet over a page with a FAB and a sticky header**: the sheet backdrop must cover the FAB and the header (z 1100 > 1000). Pinned in Task 8 (browser: at 390 px with the sheet open, `elementFromPoint` at the FAB centre is inside `.filter-overlay`).
4. **Timeline at 390 px with filters active must not push entries off-screen**: spec scenario "Phone filters" (first entry row within 200 px of the bar). Pinned in Task 7 (unit: no `.search-toggle`, bar always rendered) and Task 8 (browser measurement).
5. **Dividend filter change while a receipt form is open** must still close the form when the change comes from a chip removal or 清除全部. Pinned in Task 4.

---

### Task 1: Spec rev 11 — the two requirements the deltas missed

**Files:**
- Modify: `openspec/changes/polish-layout-v2/specs/frontend-dividend-records/spec.md` (append to `## MODIFIED Requirements`)
- Modify: `openspec/changes/polish-layout-v2/specs/frontend-portfolio-accounts/spec.md` (append to `## MODIFIED Requirements`, after "Master-detail layout on wide screens")
- Modify: `openspec/changes/polish-layout-v2/proposal.md` (revision line → rev 11, one bullet naming the two blocks)

**Interfaces:** Produces the two MODIFIED blocks that Tasks 4 and 6 implement.

- [ ] **Step 1: Add MODIFIED "Violet dividend timeline"** — copy the live requirement from `openspec/specs/frontend-dividend-records/spec.md:27-37` verbatim, then replace the sentence `A status \`SegToggle\` (全部 / 待收 / 已收款 / 舊資料) SHALL filter the list.` with: `The list SHALL use \`<app-filter-bar>\`: search and the status \`SegToggle\` (全部 / 待收 / 已收款 / 舊資料) inline; date range, 股利來源 and sort behind 篩選, with the active date range and source as removable chips.` Keep scenarios "Dividend row uses violet" and "Status filter" verbatim and add:

```markdown
#### Scenario: Source behind 篩選
- **WHEN** the owner picks 手動 under 股利來源 behind 篩選
- **THEN** only manual dividends SHALL be listed, the 篩選 button SHALL read `篩選 1` and a chip `來源：手動` SHALL appear under the bar
```

- [ ] **Step 2: Add MODIFIED "Account detail page lives at `/portfolio/accounts/:id`"** — copy the live requirement from `openspec/specs/frontend-portfolio-accounts/spec.md:30-56` verbatim (heading, intro, items 1–3, all four scenarios); replace item 3 with: `3. **Cash transactions list** — paginated list using the existing \`hub-modern-list\` card layout, with an \`<app-filter-bar>\` (the 類型 select inline, no search; date range and sort (\`txn_date desc\` default) behind 篩選, the active date range as a removable chip), page-size selector (\`25 / 50 / 100\`, persisting to localStorage), and a "新增交易" button opening the manual-entry modal.` Add:

```markdown
#### Scenario: Date range behind 篩選
- **WHEN** the user sets a date range behind 篩選
- **THEN** the list SHALL reload with `date_from` / `date_to`, the 篩選 button SHALL read `篩選 1` and a removable chip SHALL show the range
```

- [ ] **Step 3: Validate** — from the repo root: `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict`. Expected: `valid`. If the validator rejects a requirement for length, move clauses into the new scenario rather than shortening the live text.

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/polish-layout-v2
git commit -m "spec(polish-layout-v2): rev 11 — filter bar on 股利 and 現金帳戶 detail"
```

---

### Task 2: `<app-filter-bar>` primitive (3.1)

**Files:**
- Create: `frontend/src/app/components/ui/filter-bar/filter-bar.ts`, `filter-bar.html`, `filter-bar.scss`
- Test: `frontend/src/app/components/ui/filter-bar/filter-bar.spec.ts`
- Modify: `frontend/src/styles.scss` — add after `.hub-mini-datepicker` (≈ :797) the global field helper used inside the panel by every page:

```scss
.hub-filter-field {
  display: flex; flex-direction: column; gap: 4px; min-width: 0;
  > .field-label { color: var(--app-text-muted); font-size: 0.75rem; font-weight: 700; }
  > select, > input { min-height: 36px; width: 100%; border: 1px solid var(--app-border); border-radius: 10px;
    background: var(--app-surface-soft); color: inherit; font: inherit; padding: 0 10px; }
  > select:focus-visible, > input:focus-visible { outline: none; border-color: var(--app-primary); box-shadow: 0 0 0 3px var(--app-focus-ring); }
}
```

**Interfaces:**
- Consumes: `LayoutModeService.mode()` (`services/layout-mode.service.ts`).
- Produces:

```ts
export interface FilterChip { key: string; label: string }

@Component({ selector: 'app-filter-bar', standalone: true, changeDetection: OnPush, templateUrl, styleUrl })
export class FilterBarComponent {
  readonly chips = input<FilterChip[]>([]);
  readonly remove = output<string>();       // chip key
  readonly clearAll = output<void>();
  readonly reset = output<void>();
  readonly openChange = output<boolean>();
  readonly open = signal(false);
  readonly count = computed(() => this.chips().length);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  toggle(): void; close(): void;           // both emit openChange; close() refocuses the 篩選 button
}
```

  Slots (attribute selectors): `[filterBarSearch]`, `[filterBarPrimary]`, `[filterBarSecondary]`. Template skeleton (class names are the test contract):

```html
<div class="filter-bar">
  <div class="filter-bar-row">
    <div class="slot-search"><ng-content select="[filterBarSearch]" /></div>
    <div class="slot-primary"><ng-content select="[filterBarPrimary]" /></div>
    <button #toggleBtn type="button" class="filter-toggle" aria-haspopup="dialog" [attr.aria-expanded]="open()" (click)="toggle()">篩選{{ count() > 0 ? ' ' + count() : '' }}</button>
  </div>
  @if (chips().length > 0) {
    <div class="filter-chips">
      @for (chip of chips(); track chip.key) {
        <span class="filter-chip">{{ chip.label }}<button type="button" class="chip-remove" [attr.aria-label]="'移除 ' + chip.label" (click)="remove.emit(chip.key)">×</button></span>
      }
      @if (chips().length >= 2) { <button type="button" class="clear-all" (click)="clearAll.emit()">清除全部</button> }
    </div>
  }
  @if (open()) {
    @if (isPhone()) {
      <div class="filter-overlay" (click)="close()">
        <div #panel class="filter-panel sheet" role="dialog" aria-modal="true" aria-label="篩選" (click)="$event.stopPropagation()" (keydown.escape)="close()">
          <div class="grab" aria-hidden="true"></div>
          <div class="slot-secondary"><ng-content select="[filterBarSecondary]" /></div>
          <div class="panel-actions"><button type="button" class="panel-reset" (click)="reset.emit()">重設</button><button type="button" class="panel-apply" (click)="close()">套用</button></div>
        </div>
      </div>
    } @else {
      <div #panel class="filter-panel popover" role="dialog" aria-label="篩選" (keydown.escape)="close()"> … same inner content without .grab … </div>
    }
  }
</div>
```

  Note: `<ng-content select="[filterBarSecondary]">` appears twice (sheet / popover branches); Angular projects into whichever branch renders. Outside click: `@HostListener('document:pointerdown', ['$event'])` → `close()` when `open()` and `!host.contains(event.target)` and not phone. Focus: in `toggle()` when opening, `afterNextRender(() => panel first focusable ?.focus(), { injector })` (pattern: `components/accounting/account-picker/account-picker.ts:219`); `close()` → `toggleBtn.nativeElement.focus()`.

  SCSS: `.filter-bar { position: relative; display: flex; flex-direction: column; gap: 8px; margin-bottom: 1rem }`; `.filter-bar-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px }`; `.slot-search { flex: 1 1 200px; min-width: 0 }`; `.slot-primary { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0 }`; `.filter-toggle` pill (`--radius-pill`, `--app-surface-soft`, 1px `--app-border`, min-height 36px, font-weight 700); `.filter-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center }`; `.filter-chip` pill (`--app-surface-soft`, 0.75rem/700, min-height 28px) with `.chip-remove` (transparent, 24 px tap target); `.clear-all` text button; `.filter-panel.popover { position: absolute; top: calc(100% + 6px); right: 0; z-index: 1100; width: min(560px, calc(100vw - 32px)); min-width: 300px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 16px; padding: 12px; box-shadow: 0 12px 32px rgba(0,0,0,.18) }`; `.filter-overlay { position: fixed; inset: 0; z-index: 1100; display: flex; align-items: flex-end; justify-content: center; background: rgba(18, 26, 40, 0.45) }`; `.filter-panel.sheet { width: 100%; max-width: 560px; max-height: 92%; overflow-y: auto; background: var(--app-surface); border-radius: var(--radius-sheet) var(--radius-sheet) 0 0; padding: 8px 16px calc(16px + env(safe-area-inset-bottom, 0px)) }`; `.grab` as in `components/accounting/sheet.scss:23-29`; `.slot-secondary { display: grid; gap: 10px }`; `.panel-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px }` with `.panel-apply` primary (`--app-primary` background) and `.panel-reset` secondary.

- [ ] **Step 1: Write the failing spec** `filter-bar.spec.ts` with a host component:

```ts
@Component({ standalone: true, imports: [FilterBarComponent], template: `
  <app-filter-bar [chips]="chips()" (remove)="removed.push($event)" (clearAll)="cleared = cleared + 1" (reset)="resets = resets + 1" (openChange)="opens.push($event)">
    <input filterBarSearch aria-label="搜尋" />
    <select filterBarPrimary aria-label="狀態"><option>全部</option></select>
    <label class="hub-filter-field" filterBarSecondary><span class="field-label">備註</span><input class="note" [value]="note" (input)="note = $any($event.target).value" /></label>
  </app-filter-bar>` })
class Host { chips = signal<FilterChip[]>([]); removed: string[] = []; cleared = 0; resets = 0; opens: boolean[] = []; note = ''; }
// providers: { provide: MATCH_MEDIA, useValue: fakeMatchMedia } as in services/layout-mode.service.spec.ts; TestBed.inject(LayoutModeService).set('panes' | 'phone')

it('reads 篩選 with no count and no chip row when nothing is active')      // '.filter-toggle' text '篩選'; '.filter-chips' null; aria-expanded 'false'
it('reads 篩選 2 with two chips and 清除全部; a single chip has no 清除全部') // chips [{key:'date',label:'日期：2026-01-01 – 2026-03-31'},{key:'broker',label:'券商：IB'}] → '篩選 2', 2 × '.filter-chip', '.clear-all' text '清除全部'; set one chip → '.clear-all' null, text '篩選 1'
it('emits remove with the chip key and clearAll from 清除全部')              // click '.chip-remove[aria-label="移除 券商：IB"]' → removed ['broker']; click '.clear-all' → cleared 1
it('opens a popover with role=dialog at ≥ 760 px, closes on Escape and 套用, refocuses the button')  // set('panes'); click toggle → '.filter-panel.popover[role="dialog"][aria-label="篩選"]' present, no '.filter-overlay', aria-expanded 'true', opens [true], document.activeElement is '.note'; Escape keydown on panel → panel null, activeElement is '.filter-toggle', opens [true,false]; reopen, click '.panel-apply' → closed
it('opens a bottom sheet on a phone with 套用 and 重設; backdrop click closes; 重設 emits reset')     // set('phone'); click toggle → '.filter-overlay > .filter-panel.sheet[aria-modal="true"]', '.grab', buttons text ['重設','套用']; click '.panel-reset' → resets 1, still open; click '.filter-overlay' → closed
it('closes the popover on a pointerdown outside the host and keeps it on one inside')  // dispatch pointerdown on document.body → closed; reopen; pointerdown on '.note' → open
it('re-renders the projected secondary control with its current value after close and reopen')  // open, type 'abc' into '.note' (input event), close, reopen → '.note' value 'abc'  (Review Focus 1)
```

- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include 'src/app/components/ui/filter-bar/filter-bar.spec.ts'` — expected FAIL (module not found).
- [ ] **Step 3: Implement** `filter-bar.ts/html/scss` per the Interfaces block and add `.hub-filter-field` to `styles.scss`.
- [ ] **Step 4: Run the spec** — expected 7 passing, output pristine (no `NG0` warnings about unknown elements).
- [ ] **Step 5: Commit** — `git add frontend/src/app/components/ui/filter-bar frontend/src/styles.scss && git commit -m "feat(ui): app-filter-bar — search/primary slots, 篩選 popover or sheet, active chips (D6)"`

---

### Task 3: 交易紀錄 adopts the filter bar (3.2, ui-primitives scenarios 1–3, stock-transactions "One filter row")

**Files:**
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.html:62-101` (replace `.filter-bar` and `.broker-filter-row`)
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.ts` (imports; add `filterChips`, `removeFilter`, `resetSecondaryFilters`)
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.scss` (drop the `.filter-bar` / `.filter-controls` / `.clear-filters` / `.broker-filter-row` rules that no longer match; keep `.search-wrap`)
- Test: `frontend/src/app/components/portfolio/transaction-list/transaction-list.spec.ts`

**Interfaces:**
- Consumes: `FilterBarComponent`, `FilterChip` from `components/ui/filter-bar/filter-bar`.
- Produces (page):

```ts
readonly filterChips = computed<FilterChip[]>(() => …);  // 'date' → `日期：${date_from ?? '…'} – ${date_to ?? '…'}` when either is set; 'broker' → `券商：${brokerLabel(broker)}` when selectedBroker() !== 'ALL'
removeFilter(key: string): void;        // 'date' → onDateRangeChange(null); 'broker' → onBrokerFilterChange('ALL')
resetSecondaryFilters(): void;          // both of the above; sort untouched
```

- [ ] **Step 1: Rewrite the template**:

```html
<app-filter-bar [chips]="filterChips()" (remove)="removeFilter($event)" (clearAll)="clearFilters()" (reset)="resetSecondaryFilters()">
  <div class="search-wrap" filterBarSearch> …existing <i> + <input placeholder="搜尋代碼或名稱" aria-label="搜尋代碼或名稱">… </div>
  <app-seg-toggle filterBarPrimary ariaLabel="買賣篩選" …existing bindings… />
  <div filterBarSecondary class="filter-fields">
    <label class="hub-filter-field"><span class="field-label">日期範圍</span> …existing p-datepicker (ariaLabel 日期範圍, appendTo body)… </label>
    @if (brokerFilterOptions().length > 0) { <div class="hub-filter-field"><span class="field-label">券商</span> …existing app-seg-toggle ariaLabel="券商篩選"… </div> }
    <label class="hub-filter-field"><span class="field-label">排序</span> …existing <select aria-label="排序">… </label>
  </div>
</app-filter-bar>
```

  No 清除篩選 button remains in this template.

- [ ] **Step 2: Rewrite the affected tests** in `transaction-list.spec.ts` (TestBed as today; open the panel with `(el.querySelector('.filter-toggle') as HTMLButtonElement).click(); fixture.detectChanges()` before touching secondary controls):
  - :174-182 → `it('renders search, the 買賣 toggle and 篩選 on one bar; date range, 券商 and 排序 only behind 篩選')` — `.filter-bar-row` contains `.search-wrap input`, `[role="radiogroup"][aria-label="買賣篩選"]`, `.filter-toggle`; `p-datepicker`, `[aria-label="券商篩選"]`, `select[aria-label="排序"]` are null until the toggle is clicked, then present inside `.filter-panel`.
  - :193-216 (清除篩選 describe) → `describe('chips and 清除全部')`: `it('reads 篩選 2 with a date and a broker chip')` — `onDateRangeChange([new Date(2026,0,1), new Date(2026,2,31)])`, `onBrokerFilterChange('TW_CATHAY')` (after `availableBrokers`/options are seeded as the existing broker test does) → `.filter-toggle` text `篩選 2`, chip labels `['日期：2026-01-01 – 2026-03-31', '券商：國泰']`, `.clear-all` present; `it('removing the broker chip clears the broker and refreshes the list')` — click `.chip-remove[aria-label="移除 券商：國泰"]` → next GET `/api/portfolio/transactions` has no `broker` param, chips `['日期：…']`, text `篩選 1`; `it('removing a chip while the popover is open updates the panel control')` — open panel, remove broker chip → `[aria-label="券商篩選"] [aria-checked="true"]` text `全部` (Review Focus 2); `it('清除全部 clears search, side, date and broker')` — set `searchInput` + side BUY + the two secondary filters, click `.clear-all` → query symbol/side/date_from/date_to/broker all null, `.filter-chips` null; `it('重設 clears only the secondary filters')` — side BUY + date → open, click `.panel-reset` → side still `BUY`, chips empty.
  - `it('shows the secondary filters in a bottom sheet with 套用 and 重設 on a phone')` — `TestBed.inject(LayoutModeService).set('phone')`, click toggle → `.filter-panel.sheet[aria-modal="true"]`, buttons `['重設','套用']` (ui-primitives scenario "Phone sheet").
  - Keep :184-190 (header has no 清除篩選) and :267-279 as they are.
- [ ] **Step 3: Run** `--include 'src/app/components/portfolio/transaction-list/transaction-list.spec.ts'` — expected FAIL on the new tests.
- [ ] **Step 4: Implement** the ts members and scss cleanup; `imports` gains `FilterBarComponent`.
- [ ] **Step 5: Run the spec + `theme/bootstrap-grid.spec.ts`** (the html is allowlisted and must still use the grid elsewhere) — expected PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat(transactions): shared filter bar — 日期/券商/排序 behind 篩選, chips + 清除全部"`

---

### Task 4: 股利 adopts the filter bar (3.2, spec rev 11 "Source behind 篩選")

**Files:**
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.html:53-82`
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.ts` (`filterChips`, `removeFilter`, `resetSecondaryFilters`; imports)
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.scss` (drop dead `.filter-bar` / `.clear-filters` rules)
- Test: `frontend/src/app/components/portfolio/dividend-list/dividend-list.spec.ts`

**Interfaces:** same member signatures as Task 3. Chips: `'date'` → `日期：…` from `query().date_from/date_to`; `'source'` → `來源：${label}` where label is the `sourceOptions` entry's label for `query().source` (`手動`, `自動 (TWSE)`, `CSV 匯入`). `removeFilter('date')` → `onDateRangeChange(null)`; `removeFilter('source')` → `onSourceChange(null)`; both go through the existing handlers so `closeForms()` still runs (Review Focus 5).

- [ ] **Step 1: Template** — `<app-filter-bar>` with `.search-wrap` in `[filterBarSearch]`, the status `app-seg-toggle ariaLabel="收款狀態"` in `[filterBarPrimary]`, and in `[filterBarSecondary]` three `.hub-filter-field`s: 日期範圍 (existing `p-datepicker`), 股利來源 (existing `<select aria-label="股利來源">`, first option `全部來源`), 排序 (existing `<select aria-label="排序">`). Remove the 清除篩選 button.
- [ ] **Step 2: Tests** — rewrite :542-567 as `it('shows chips and 清除全部 for the secondary filters; a status alone shows none')`: `onStatusChange('pending')` → `.filter-chips` null and `.filter-toggle` text `篩選`; `onSourceChange('manual')` → text `篩選 1`, chips `['來源：手動']`; `onDateRangeChange([…])` → `篩選 2`, `.clear-all` present; click `.clear-all` → query symbol/date_from/date_to/source null **and status back to `'all'`**. Add `it('removing the 來源 chip clears the source and closes an open receipt form')` — open a 確認收款 form as :1127-1150 does, `onSourceChange('manual')`, click `.chip-remove[aria-label="移除 來源：手動"]` → next request has no `source`, `.receipt-form` null (the selector :141-180 use). Keep :569-583 (status texts) and :99-117 unchanged; adjust any selector that reached the datepicker/select without opening the panel to click `.filter-toggle` first.
- [ ] **Step 3: Run** `--include 'src/app/components/portfolio/dividend-list/dividend-list.spec.ts'` — FAIL, then implement, then PASS; also run `theme/bootstrap-grid.spec.ts`.
- [ ] **Step 4: Commit** — `git commit -m "feat(dividends): shared filter bar — 日期/來源/排序 behind 篩選 (spec rev 11)"`

---

### Task 5: 已實現損益 adopts the filter bar (3.2, realized-pnl "Realized filters")

**Files:**
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.html:53-117` (replace the `<app-bento class="filter-card" title="篩選">`)
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.ts` (`filterChips`, `removeFilter`, `resetSecondaryFilters`; imports)
- Modify: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.scss` (drop `.filter-card` / `.switch-field` / `.clear-filters` rules; keep `.filter-field` only if still referenced — the panel uses `.hub-filter-field`)
- Test: `frontend/src/app/components/portfolio/realized-pnl/realized-pnl.component.spec.ts` (+ `realized-pnl.broker-filter.spec.ts` if it queries the card)

**Interfaces:** chips `'date'` (`日期：…`), `'market'` (`市場：台股` / `市場：海外` when `market() !== 'ALL'`), `'broker'` (`券商：${brokerLabel}` when the broker filter !== 'ALL'), `'dayTrade'` (`僅當沖` when `query().day_trade_only`). `removeFilter`: `'date'` → `onDateRangeChange(null)`; `'market'` → `onMarketChange('ALL')`; `'broker'` → `selectBrokerFilter('ALL')` (existing handler, realized-pnl.ts:373; broker signal `selectedBroker()`); `'dayTrade'` → `onDayTradeOnlyChange(false)`. `resetSecondaryFilters()` = all four; the year preset (primary) and search stay.

- [ ] **Step 1: Template** — `<app-filter-bar>`: `.search-wrap` input (aria-label 搜尋代碼或名稱, existing placeholder) in `[filterBarSearch]`; the `p-selectButton` year preset in `[filterBarPrimary]`; in `[filterBarSecondary]`: 日期範圍 (existing `p-datepicker` with `(onSelect)/(onClear)`), 市場 (`app-seg-toggle ariaLabel="市場"`), 券商 (`app-seg-toggle ariaLabel="券商篩選"`, only when `brokerCatalog().length > 0`, under a `.field-label` 券商), 僅當沖 (`label.hub-filter-field` with `app-switch ariaLabel="僅當沖"` + visible text 僅當沖), 排序 (`p-select ariaLabel="排序"`). No `app-bento` and no 清除篩選.
- [ ] **Step 2: Tests** — :709-727 → `it('renders search and the 年度 preset inline; 市場, 僅當沖, 日期範圍, 券商 and 排序 behind 篩選')`: `.filter-bar-row` has `.search-wrap input[type="text"]` and `p-selectbutton`; the five secondary controls are null until `.filter-toggle` click, then present inside `.filter-panel` (market texts `['全部','台股','海外']`, `button[role="switch"][aria-label="僅當沖"]` aria-checked `false`, `.hub-filter-field .field-label` 券商); `.hub-filter-bar` and `.filter-card` null. :754-787 → `it('僅當沖 alone reads 篩選 1 with a 僅當沖 chip; removing it clears the filter')` (click the switch → `篩選 1`, chips `['僅當沖']`, remove → `day_trade_only` null, `篩選`); `it('台股 alone shows a 市場：台股 chip and 清除全部 appears with a second chip')` (market TW + date → `篩選 2`, `.clear-all`; click → market `ALL`, date null, a 全部 fetch as the old test asserted); `it('a date range shows a removable chip and clears the year preset')` (preset `2026` then range → chips `['日期：…']`, `selectedYear()` null, as :148-160 for the inverse). Update the `clear` helper at :516 to click `.clear-all`. `realized-pnl.broker-filter.spec.ts`: open the panel before reading `[aria-label="券商篩選"]`, texts unchanged `['全部','IB','Firstrade','國泰']`.
- [ ] **Step 3: Run** `--include 'src/app/components/portfolio/realized-pnl/*.spec.ts'` — FAIL → implement → PASS.
- [ ] **Step 4: Commit** — `git commit -m "feat(realized): shared filter bar — 年度 inline, 市場/券商/僅當沖/日期/排序 behind 篩選"`

---

### Task 6: 現金帳戶 account detail adopts the filter bar (3.2, spec rev 11 "Date range behind 篩選")

**Files:**
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.html:92-130` (replace `.hub-filter-bar`)
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.ts` (`filterChips`, `removeFilter`, `resetSecondaryFilters`; imports)
- Modify: `frontend/src/app/components/portfolio/accounts/account-detail.scss` (drop rules targeting `.hub-filter-bar` / `.filter-controls`)
- Modify: `frontend/src/styles.scss:743-785` — delete the `.hub-filter-bar` block (this page was its last user; `.search-wrap` rules there are duplicated by the pages that use a search)
- Test: `frontend/src/app/components/portfolio/accounts/account-detail.component.spec.ts`

**Interfaces:** chips `'date'` only (`日期：…` from the current `date_from` / `date_to` in the query); `removeFilter('date')` → `onDateRangeChange(null)`; `resetSecondaryFilters()` = the same (sort stays). No search slot (`[filterBarSearch]` left empty).

- [ ] **Step 1: Template** — `<app-filter-bar>` with the `p-multiselect` 類型 (`[selectionLimit]="1"`, placeholder 類型) in `[filterBarPrimary]`; in `[filterBarSecondary]` 日期範圍 (existing `p-datepicker`) and 排序 (existing `p-select`) as `.hub-filter-field`s. Remove the `pi-filter-slash` button. The header's 合併同筆交易 / 新增交易 controls are untouched.
- [ ] **Step 2: Tests** — :1006-1016 → `it('renders 類型 inline and 日期範圍 / 排序 behind 篩選')`: `.filter-bar-row p-multiselect` present; `p-datepicker` and `p-select` null until `.filter-toggle` click; `.hub-filter-bar` null; header buttons still present. Add `it('a date range behind 篩選 reloads with date_from/date_to, reads 篩選 1 and shows a removable chip')` — `onDateRangeChange([new Date(2026,0,1), new Date(2026,0,31)])`, `tick(300)`/advance timers as :316-333 does → request params `date_from=2026-01-01`, `date_to=2026-01-31`, `.filter-toggle` text `篩選 1`, chip `日期：2026-01-01 – 2026-01-31`; click `.chip-remove` → next request has neither param, text `篩選`. Keep :316-333 and :1058-1070 (餘額 column hides with type/date_to).
- [ ] **Step 3: Run** `--include 'src/app/components/portfolio/accounts/account-detail.component.spec.ts'` — FAIL → implement → PASS; also `--include 'src/app/components/portfolio/realized-pnl/realized-pnl.component.spec.ts'` still green (its `.hub-filter-bar` null assertion).
- [ ] **Step 4: Commit** — `git commit -m "feat(accounts): detail filter bar — 類型 inline, 日期/排序 behind 篩選; drop global .hub-filter-bar (spec rev 11)"`

---

### Task 7: 記帳時間軸 adopts the filter bar (3.2, accounting-ledger "Timeline home page" / "Phone filters")

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.html:4-22` (remove the `.search-toggle` block), `:55-66` (`.match-row` moves below the bar, otherwise unchanged), `:74-116` (replace the `@if (!isPhone() || filtersOpen())` `.chips.filters` block with `<app-filter-bar>` directly under `.monthbar`)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` — remove `filtersOpen` (:425) and `toggleFilters()` (:1078); add `filterChips`, `removeFilter`, `resetSecondaryFilters`, `onFilterPanelOpen(open: boolean)` (calls `ensureOptions()` when `open`); `imports` gains `FilterBarComponent`
- Modify: `frontend/src/app/components/accounting/timeline/timeline.scss` (drop `.search-toggle`; a `.filter-q` rule if the search keeps that class for the IME tests)
- Test: `frontend/src/app/components/accounting/timeline/timeline.spec.ts`

**Interfaces:** chips (keys / labels): `'kind'` → `類型：${label}` from `TIMELINE_FILTER_KINDS`; `'category'` → `類別：${name}` (name from `categoryOptions()` by id, `類別` alone if not loaded); `'project'` → `專案：${name}`; `'tag'` → `標籤：${tagFilter()}`; `'merchant'` → `商家：${merchantFilter()}`; `'amount'` → `金額：${min} – ${max}` / `金額 ≥ ${min}` / `金額 ≤ ${max}`. `removeFilter(key)` calls the matching setter with `''` (`'amount'` clears both bounds); `resetSecondaryFilters()` = all six; account and query stay.

- [ ] **Step 1: Template**:

```html
<app-filter-bar [chips]="filterChips()" (remove)="removeFilter($event)" (clearAll)="clearFilters()" (reset)="resetSecondaryFilters()" (openChange)="onFilterPanelOpen($event)">
  <input filterBarSearch class="filter-q" type="search" placeholder="搜尋名稱 / 商家 / 備註" aria-label="搜尋名稱、商家或備註" …the four existing IME/keydown handlers… />
  <select filterBarPrimary class="filter-account" aria-label="帳戶" …existing…>…</select>
  <div filterBarSecondary class="filter-fields">
    類型 (select.filter-kind aria-label 類型) · 類別 (select.filter-category aria-label 類別) · 專案 (select.filter-project) · 標籤 (input.filter-tag placeholder "# 標籤") · 商家 (input.filter-merchant placeholder 商家) · 金額範圍 (one .hub-filter-field holding both input.filter-amount-min and input.filter-amount-max, placeholders unchanged)
    — each wrapped in <label class="hub-filter-field"><span class="field-label">…</span>…</label>; the `(focus)="ensureOptions()"` hooks stay.
  </div>
</app-filter-bar>
```

  The `⌕ ` prefix leaves the search placeholder (the bar has no glyph button any more). `.match-row` (符合條件 N 筆 + 清除篩選) renders right after the bar; `.empty-filtered` unchanged.

- [ ] **Step 2: Tests**:
  - :417-433 → `it('renders the filter bar with search and 帳戶 inline at phone width, no ⌕ toggle')` — `render('phone')`: `.search-toggle` null, `.filter-bar-row .filter-q` and `.filter-account` present, `.filter-kind` null until `.filter-toggle` click, then inside `.filter-panel.sheet`.
  - :719-751 → `it('keeps the typed merchant and bounds when the sheet is closed and reopened, and 清除全部 empties them')` — open sheet, set merchant `全聯`, min `100`; `.panel-apply` click; reopen → `.filter-merchant` value `全聯`, `.filter-amount-min` value `100` (Review Focus 1); chips `['商家：全聯', '金額 ≥ 100']`, `.filter-toggle` text `篩選 2`; click `.clear-all` → inputs empty, chips null.
  - Add `it('reads 篩選 2 with a 類別 and a 商家 chip; removing the 商家 chip drops merchant from the request')` — seed options as :660-717 does, `setCategory('<id>')`, `setMerchant('全聯')` → `篩選 2`, chips `['類別：餐飲 › 午餐', '商家：全聯']`; click `.chip-remove[aria-label="移除 商家：全聯"]` → next entries request has no `merchant`, chips `['類別：餐飲 › 午餐']` (spec scenario "Phone filters" + ui-primitives "Removing a chip").
  - Add `it('loads the option lists when the panel opens')` — click `.filter-toggle` → the categories/projects requests that `ensureOptions()` issues are pending (same flush helpers as :660-717).
  - :626-658, :753-776, :591-611, :779-790: open the panel before touching `.filter-kind` / `.filter-category` / … ; assertions on `.match-count` and `.clear-filters` unchanged. :793-900 IME tests keep `.filter-q` as the selector — the input now sits in `.slot-search`, behaviour identical.
- [ ] **Step 3: Run** `--include 'src/app/components/accounting/timeline/timeline.spec.ts'` — FAIL → implement → PASS; also `--include 'src/app/components/accounting/**/*.spec.ts'` (account-entries / reminders share `filters.scss`, untouched) green.
- [ ] **Step 4: Commit** — `git commit -m "feat(timeline): shared filter bar — search + 帳戶 inline, six filters behind 篩選 at every width"`

---

### Task 8: Gate, whole suite, build and browser verification (3.3)

**Files:**
- Create: `frontend/src/app/theme/filter-bar.spec.ts`
- Create: `~/reports/home-hub-playwright-stubs/pr_c_shots.py` (outside the repo; reuse `pr_b_shots.py` stubs and the `ONLY=` filter)
- Evidence dir: `~/reports/home-hub-polish-pr-c-<UTC>/` (NOTES.md, probe.json, screenshots, ng test / build logs)

- [ ] **Step 1: Gate spec** (pattern `theme/kpi-values.spec.ts:1-12`): for the five templates (`portfolio/transaction-list/transaction-list.html`, `portfolio/dividend-list/dividend-list.html`, `portfolio/realized-pnl/realized-pnl.html`, `portfolio/accounts/account-detail.html`, `accounting/timeline/timeline.html`) assert `<app-filter-bar` present; the four portfolio templates contain no `清除篩選`; `account-detail.html` and `src/styles.scss` contain no `hub-filter-bar`; `timeline.html` contains no `search-toggle`; `styles.scss` contains `.hub-filter-field`. Run `--include 'src/app/theme/filter-bar.spec.ts'` → PASS.
- [ ] **Step 2: Whole suite + build** — `cd frontend && npx ng test --watch=false 2>&1 | tee <evidence>/ng-test.log` (all green, count recorded) and `npx ng build 2>&1 | tee <evidence>/ng-build.log` (clean apart from the known protobufjs warning); repo root `npx -y @fission-ai/openspec@latest validate polish-layout-v2 --strict` → valid.
- [ ] **Step 3: Browser check** — stop any server on 18081 by PID (`ss -ltnp`), serve the fresh `dist` with `~/workspace/home-hub-demo-preview/redesign-v2-pr101/redesign_server.py`, run `pr_c_shots.py` in the Playwright image (`pip install --user playwright==1.49.1`, plain `python3`), light + dark, stubbed fictional data. Record in `probe.json` / NOTES.md:
  - `/portfolio/transactions` @1440, no filters: every child of `.filter-bar-row` shares one `getBoundingClientRect().top` (one row); `.filter-toggle` text `篩選`.
  - `/portfolio/transactions` @1440: open 篩選, pick a broker and a date range (two `.p-datepicker-day` clicks) → text `篩選 2`, 2 × `.filter-chip`, `.clear-all` visible; screenshot.
  - `/portfolio/realized-pnl` @390: open the sheet, toggle 僅當沖, 套用 → `篩選 1`, chip `僅當沖`; sheet screenshot shows 重設 / 套用.
  - `/accounting` @390 with 類別 + 商家 set through the sheet: `firstEntryTop − filterBarBottom ≤ 200`, text `篩選 2`, 2 chips (spec "Phone filters"); with the sheet open, `document.elementFromPoint(fabX, fabY)` is inside `.filter-overlay` (Review Focus 3).
  - `/portfolio/dividends` @390/@1100 and `/portfolio/accounts/7` @390/@1440: bar renders, no horizontal scroll (`scrollWidth ≤ innerWidth`), no console errors.
- [ ] **Step 4: Commit the gate** — `git add frontend/src/app/theme/filter-bar.spec.ts && git commit -m "test(theme): filter-bar adoption gate"`. Evidence stays outside the repo and is cited in the PR body.

---

## Not in PR C (follow-ups to file after merge)

- Moving `components/accounting/sheet.scss` under `components/ui/` so the filter bar and the accounting sheets share one stylesheet (PR C duplicates ~10 lines).
- A Tab trap for the filter sheet on portfolio pages (the accounting `data-overlay` trap only covers the accounting shell).
- `.hub-mini-select` / `.hub-mini-datepicker` consolidation once the panel fields settle.
