# Reports, reward rules and settings small fixes (PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Six independent SPA fixes from the owner's list of 2026-11-20 (items 7–12): no floating 記一筆 on the report pages, no duplicate total column, a trend chart that starts where the data starts, plainer reward posting copy, the phone reports gear on the title row, and an app-styled 額度共用 tick.

**Architecture:** SPA only (`frontend/`), no API change. Each task touches one component and its spec; nothing is shared between tasks except Task 7 (specs + full suite).

**Tech Stack:** Angular 21 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with `--app-*` tokens.

**Spec:** the owner's list (items quoted per task). Requirements that become false are edited in Task 7: `openspec/specs/frontend-app-shell/spec.md`, `openspec/specs/frontend-accounting-reports/spec.md`, `openspec/specs/frontend-accounting-ledger/spec.md`.

## Global Constraints

- The bell and its count are not touched.
- No change under `services/`; no new dependency.
- Colours from `--app-*` tokens only.
- Tests use fictional names and amounts.
- Commit per task, English message, trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Rulings made while planning (owner may overrule)

1. **Item 7:** the floating button is hidden on `/accounting/reports` and `/accounting/reports/custom/*` only; the `N` shortcut and the phone tab bar's ＋ keep working there. The bottom clearance (`--fab-clear` padding) stays, so nothing jumps.
2. **Item 8:** "one series" means the split produced exactly one series column. The 合計 column (also when it is titled 本月至今 / 本期至今) is then hidden in the head, the body and the total row; 筆數 and the comparison columns stay. With no split, or two or more series, nothing changes.
3. **Item 9:** applies to 趨勢與比較 (the twelve-month chart and everything under it that reads the same buckets: title 「近 N 個月」, table, aria text). The first month with data is judged on 全部支出, so switching the category series does not move the axis. Shown months = from the first month whose total is not 0 to the end, at least the last 6, at most the 12 loaded. A trend with no data at all shows the last 6. The six-month spark on 排行 and saved-report charts are unchanged.
4. **Item 10 copy** (`post_month_offset` counts from the month the window ends):
   - offset 0 → `結帳當月 15 號入帳`
   - offset 1 → `下期 15 號入帳`
   - offset 2 → `下下期 15 號入帳`
   - after a transaction → `交易後 3 天入帳`
   - manual → `手動入帳`
5. **Item 10 period line:** a rule row's 「本期 M/D–M/D」 exists today only on a card whose rules run on different windows, and that card's title then shows no period at all. New rule: the card title always shows the card's own window (the statement cycle, or the calendar month for a card without a closing day); a row shows its own period only when it differs from the title's (e.g. a calendar-month rule on a statement-cycle card). So the line the owner saw repeated goes away, and a rule on a different window still says so.
6. **Item 11:** on phones the reports header stops wrapping (its actions slot is empty there; the period control already renders under the header), so the title, subtitle (one line, ellipsis) and gear share one row. 760–1023 px keeps today's wrap.
7. **Item 12:** there is no shared tick component, so this adds a global `.hub-check` style (20 px rounded box, `--app-primary` fill and a white ✓ when checked, focus ring) on the native `<input type="checkbox">` — native semantics and tests keep working. It is applied to 額度共用 at every width (the same template serves phone and desktop). Other native checkboxes in the app are left for a later sweep.

## Review Focus

- FAB: navigating reports → 記帳記錄 → reports toggles it both ways; the 760–1023 px sheet and the entry form rules still win.
- Trend: 12 loaded months with data only in the last 2 → 6 bars; data from month 4 of 12 → 9 bars; all 12 with data → 12; no data → 6; the average line and the running-month forecast still attach to the right bars after trimming.
- Custom report with one series while 本月至今 is the total header, with comparison on.
- Reward card with mixed windows where the first usage row is the calendar-month rule (title must still show the statement cycle).
- 額度共用 tick in dark theme and with keyboard focus.

---

### Task 1: No floating 記一筆 on the report pages

Owner item 7: 「報表頁和自訂報表頁不顯示浮動的『記一筆』按鈕（目前一進頁面就蓋住表格最右欄）。」

**Files:**
- Modify: `frontend/src/app/components/shell/shell.ts`, `shell.spec.ts`

- [ ] **Step 1: Failing tests** (`shell.spec.ts`, 1440 px): at `/accounting/reports?month=2026-08` and `/accounting/reports/custom/new` and `/accounting/reports/custom/7` there is no `.hub-fab`; at `/accounting` and `/accounting/reward-rules` there is; navigating from reports to `/accounting` shows it again; pressing `N` on the reports page still opens the entry form. Replace the existing test that opens the form from the FAB on `/accounting/reports` with the `N` key (line ~373) and rename "on every route" (line ~177) to name the exception.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/shell/shell.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** `const NO_FAB_URL = /^\/accounting\/reports(?:[/?#]|$)/;` and a `url` signal from `Router.events` (`NavigationEnd`, seeded with `router.url`); `showFab` adds `&& !NO_FAB_URL.test(this.url())`.
- [ ] **Step 4: Run** — Expected: PASS; also `--include='src/app/components/accounting/reports/*.spec.ts'` (the clearance test at `reports.spec.ts:913` must still pass unchanged).
- [ ] **Step 5: Commit** `fix(shell): no floating 記一筆 on the report pages`.

### Task 2: Custom report hides a total column that repeats the only series

Owner item 8.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/custom-report.ts`, `custom-report.spec.ts`

- [ ] **Step 1: Failing tests.** A split report whose rows all carry the one label 「晚餐」 → headers `['月份', '晚餐', '筆數']`, each body row has 2 numeric cells, the total row reads `合計 | <sum> | <count>`; with comparison on → `['月份', '晚餐', '筆數', '去年同期', '增減']`; while the period runs → still no `本月至今（…）` column. Two series → 合計 present (existing test at line ~139 unchanged). No split → `['月份', '合計', '筆數']` unchanged.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/custom-report.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** `private readonly totalHidden = computed(() => this.seriesKeys().length === 1);` `columns` and `tableRows` (body rows and the trailing total row) skip the `total` cell when it is true — one helper builds a row's cells so head and body cannot drift.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(reports): a single-series custom report drops the duplicate total column`.

### Task 3: Trend chart starts at the first month with data, at least six bars

Owner item 9.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/report-math.ts`, `report-math.spec.ts`
- Modify: `frontend/src/app/components/accounting/reports/trend-tab.ts`, `reports.spec.ts`

**Interfaces:**
- Produces: `export const TREND_MIN_MONTHS = 6;` and `export function trendWindow<T>(buckets: T[], total: (bucket: T) => number, min = TREND_MIN_MONTHS): T[]` — returns `buckets.slice(start)` where `start = min(firstNonZeroIndex, max(buckets.length - min, 0))`, and `firstNonZeroIndex = buckets.length` when every total is 0.

- [ ] **Step 1: Failing tests.** `report-math.spec.ts`: 12 buckets with totals `[0×10, 5, 7]` → last 6; `[0, 0, 0, 4, …]` → 9 buckets starting at index 3; all non-zero → 12; all zero → last 6; 4 buckets loaded with data in the last → all 4 (never pads beyond what was loaded). `reports.spec.ts` (趨勢與比較): the `SERIES` fixture with 8 leading zero months → 6 bars (not 12), title `每月支出 · 近 6 個月`, x labels start at the 7th loaded month, `3 個月平均` unchanged, the table under the chart has 6 month rows + the total row; the default 12-month fixture still gives 12 bars; switching the series to a category whose own data starts later keeps the same bars.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/{report-math,reports}.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** In `trend-tab.ts` add `private readonly buckets = computed(() => trendWindow(this.trend()?.buckets ?? [], bucket => num(bucket.values[TREND_ALL])));` and make every reader of `this.trend()?.buckets` / `this.trend()!.buckets` in the file use `this.buckets()` (title, values, average, forecast, layout slots, bars, labels, aria text, table rows). No change to the read in `reports.ts` (still twelve months).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(reports): the trend chart starts at the first month with data (six bars at least)`.

### Task 4: Reward posting copy and the repeated period line

Owner item 10.

**Files:**
- Modify: `frontend/src/app/components/accounting/reward-rule-text.ts`, `reward-rule-text.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `reward-rules.spec.ts`
- Modify (expectations only): `frontend/src/app/components/accounting/account-settings/account-settings.spec.ts` (~line 1044), `account-settings/rules-section/rules-section.spec.ts` (~line 208)

**Interfaces:**
- `postingSummary(rule)` returns the strings of ruling 4.
- `reward-rules.ts`: `windowLine(account)` no longer takes `mixed`; it picks the card's own usage row — the first row that is not a calendar-month rule's when the account has a closing day, else the first row — and formats as today (`結帳日 N 號 · 本期 M/D–M/D`, `每月 1 日起算 · 本月 M/D–M/D`, or just the closing-day text when the account has no usage rows). `OverviewRow.window` = `本期 <windowText(usage)>` (`本月 …` for a calendar-month rule) only when the row's `(window_start, window_end)` differs from the card row's; else `null`.

- [ ] **Step 1: Failing tests.** `reward-rule-text.spec.ts`: the five strings of ruling 4 (offsets 0 / 1 / 2 with `post_day` 15; `delay_days` 3; manual). `reward-rules.spec.ts`: single-window card → title `結帳日 25 號 · 本期 10/26–11/25`, no `.rr-row-window` (as today); mixed card (statement cycle 10/26–11/25 + a calendar-month rule) → title `結帳日 25 號 · 本期 10/26–11/25`, exactly one `.rr-row-window` reading `本月 11/1–11/30` on the calendar-month rule's row; the same card with usage rows in the other order gives the same result; card without closing day → `每月 1 日起算 · 本月 11/1–11/30`, no row line. Update the two settings specs to `· 下期 15 號入帳`.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/{reward-rule-text.spec.ts,reward-rules/*.spec.ts,account-settings/**/*.spec.ts}'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(rewards): plainer posting copy; a rule row repeats no period the card title already shows`.

### Task 5: Phone reports gear on the title row

Owner item 11.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/reports.scss`, `reports.spec.ts` (the CSS-source checks at ~873–907)

- [ ] **Step 1: Failing test.** The scss source keeps the wrap override only inside `@media (min-width: 760px) and (max-width: 1023.98px)`; no rule sets `flex-wrap: wrap` on `.page-header-row` for widths below 760 px. Keep the existing assertions about `order: 1` for the 760–1023 range and about the phone period control rendering after the header.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/reports.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** the media-query change; update the comment above it.
- [ ] **Step 4: Run** — Expected: PASS. Layout itself is checked in Task 7's Playwright probe (gear's top within the title's row box at 390 px, with a long 「不計入：…」 subtitle).
- [ ] **Step 5: Commit** `fix(reports): the phone header keeps its gear on the title row`.

### Task 6: 額度共用 uses an app-styled tick

Owner item 12.

**Files:**
- Modify: `frontend/src/styles.scss` (new `.hub-check` block next to the other `hub-*` form styles)
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.html` (lines ~373–387), `account-settings.scss`, `account-settings.spec.ts`

- [ ] **Step 1: Failing tests.** Each `.sharing-option input[type="checkbox"]` has class `hub-check`; checking still calls `toggleSharing` (existing tests at ~625–660 keep passing); `styles.scss` source contains a `.hub-check` rule with `appearance: none` and a `:checked` rule using `var(--app-primary)`, and a `:focus-visible` rule.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/account-settings/account-settings.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** `.hub-check { appearance: none; width: 20px; height: 20px; flex: none; border: 1.5px solid var(--app-border); border-radius: 6px; background: var(--app-surface); display: inline-grid; place-content: center; }` `:checked { background: var(--app-primary); border-color: var(--app-primary); }` with a ✓ drawn by `::after` (a rotated border, `--app-on-primary`), `:focus-visible { outline: 2px solid var(--app-focus-ring); outline-offset: 2px; }`, `:disabled { opacity: .5 }`. `.sharing-option { display: flex; align-items: center; gap: 10px; min-height: 40px; }`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(settings): 額度共用 options use the app tick style`.

### Task 7: Specs, full suite, evidence

**Files:**
- Modify: `openspec/specs/frontend-app-shell/spec.md` (floating button requirement ~136–170: "every route" → every route except the reports page and the saved-report page; add a scenario)
- Modify: `openspec/specs/frontend-accounting-reports/spec.md` (~26: twelve bars → the months from the first one with data, six to twelve; ~206–218: the floating-button scenario now says the button is absent on these pages, the clearance stays; add a scenario for the single-series total column near ~57–76; ~206: phone header row with the gear)
- Modify: `openspec/specs/frontend-accounting-ledger/spec.md` (~819–843: card window line and the row period rule of ruling 5; ~851 / ~899: name the posting copy)

- [ ] **Step 1:** Edit the three specs; no edited requirement exceeds 500 characters.
- [ ] **Step 2: Run** `openspec validate frontend-app-shell --type spec && openspec validate frontend-accounting-reports --type spec && openspec validate frontend-accounting-ledger --type spec` — Expected: valid; strict mode adds no new warning on the edited requirements.
- [ ] **Step 3: Run** `cd frontend && npx ng test --watch=false` — Expected: all pass; `npx ng build` — Expected: success.
- [ ] **Step 4: Evidence (outside the repo).** Before (origin/main) and after builds; Playwright at 1440 × 900 and 390 × 844, light and dark: 報表 (trend tab), a custom report with one series, 回饋規則 overview, 帳戶設定 with 額度共用. Probes: no `.hub-fab` on the report pages at 1440; the gear's box shares the title row at 390. Screens go into the PR description as a private gallery link.
- [ ] **Step 5: Commit** `docs(openspec): report pages without the floating button, trend window, reward copy`.
