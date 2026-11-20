# Reports, reward rules and settings small fixes (PR 2) Implementation Plan

_Rev 2 (2026-11-20): AGENT-537 findings folded — trend window judged on the 全部支出 read (a category response has no `all` key), category table untouched, existing reward / shell / reports specs named, all-calendar card ruling, tick contrast._

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
3. **Item 9:** applies to 趨勢與比較 (the twelve-month chart and everything under it that reads the same buckets: title 「近 N 個月」, table, aria text). The first month with data is judged on 全部支出, so switching the category series does not move the axis (a category read returns only that category's values, so the page remembers the window from the 全部支出 read of the same range). The 類別 comparison table under the chart is not month rows and does not change. Shown months = from the first month whose total is not 0 to the end, at least the last 6, at most the 12 loaded. A trend with no data at all shows the last 6. The six-month spark on 排行 and saved-report charts are unchanged.
4. **Item 10 copy** (`post_month_offset` counts from the month the window ends):
   - offset 0 → `結帳當月 15 號入帳`
   - offset 1 → `下期 15 號入帳`
   - offset 2 → `下下期 15 號入帳`
   - after a transaction → `交易後 3 天入帳`
   - manual → `手動入帳`
5. **Item 10 period line:** a rule row's 「本期 M/D–M/D」 exists today only on a card whose rules run on different windows, and that card's title then shows no period at all. New rule: the card title always shows the card's own window (the statement cycle, or the calendar month for a card without a closing day); a row shows its own period only when it differs from the title's (e.g. a calendar-month rule on a statement-cycle card). So the line the owner saw repeated goes away, and a rule on a different window still says so. A card that has a closing day but whose usage rows are all calendar-month rules keeps today's title `每月 1 日起算 · 本月 M/D–M/D` (there is no statement-cycle row to print) and no row line.
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

- [ ] **Step 1: Failing tests** (`shell.spec.ts`, 1440 px): at `/accounting/reports?month=2026-08` and `/accounting/reports/custom/new` and `/accounting/reports/custom/7` there is no `.hub-fab`; at `/accounting` and `/accounting/reward-rules` there is; navigating from reports to `/accounting` shows it again; pressing `N` on the reports page still opens the entry form. The existing test at `shell.spec.ts:373` clicks the FAB on `/accounting/reports?month=2026-08` and must change: it opens the form with the `N` key instead (N does not read `showFab`). `:177` ("on every route") does not visit reports; rename it to name the exception and add `/accounting/reports-other`-style negative: the regex must not match a path that merely starts with `reports`.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/shell/shell.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** `const NO_FAB_URL = /^\/accounting\/reports(?:[/?#]|$)/;` and a `url` signal from `Router.events` (`NavigationEnd`, seeded with `router.url`); `showFab` adds `&& !NO_FAB_URL.test(this.url())`.
- [ ] **Step 4: Run** — Expected: PASS; also `--include='src/app/components/accounting/reports/*.spec.ts'` (the clearance test at `reports.spec.ts:913` must still pass unchanged).
- [ ] **Step 5: Commit** `fix(shell): no floating 記一筆 on the report pages`.

### Task 2: Custom report hides a total column that repeats the only series

Owner item 8.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/custom-report.ts`, `custom-report.spec.ts`

- [ ] **Step 1: Failing tests.** A split report whose rows all carry the one label 「晚餐」 → headers `['月份', '晚餐', '筆數']`, each body row has 2 numeric cells, the total row reads `合計 | <sum> | <count>`; with comparison on → `['月份', '晚餐', '筆數', '去年同期', '增減']`; while the period runs → still no `本月至今（…）` column. More than one series → 合計 present (the existing three-series test at line ~139 stays unchanged). No split → `['月份', '合計', '筆數']` unchanged.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/custom-report.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** `private readonly totalHidden = computed(() => this.seriesKeys().length === 1);` `columns` and `tableRows` (body rows and the trailing total row) skip the `total` cell when it is true — one helper builds a row's cells so head and body cannot drift.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(reports): a single-series custom report drops the duplicate total column`.

### Task 3: Trend chart starts at the first month with data, at least six bars

Owner item 9.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/report-math.ts`, `report-math.spec.ts`
- Modify: `frontend/src/app/components/accounting/reports/reports.ts`, `reports.html`, `reports.spec.ts`
- Modify: `frontend/src/app/components/accounting/reports/trend-tab.ts`

**Interfaces:**
- `report-math.ts`: `export const TREND_MIN_MONTHS = 6;` and `export function trendStart(totals: number[], min = TREND_MIN_MONTHS): number` — the index of the first shown bucket: `min(firstNonZeroIndex, max(totals.length - min, 0))`, with `firstNonZeroIndex = totals.length` when every total is 0. It never pads beyond what was loaded.
- `reports.ts`: `trendFrom = signal<{ range: string; start: string } | null>(null)` — `range` is `${date_from}/${date_to}` of the read, `start` the `bucket.start` of the first shown bucket, computed from a 全部支出 response (`bucket.values[TREND_ALL]`). `loadTrend`: with `series === null` the one read sets both `trend` and `trendFrom`; with a category and a `trendFrom` for the same range, one read as today; with a category and no `trendFrom` for that range (a period change while a category chip is on), `forkJoin` the category read and one unfiltered read of the same range, and set `trendFrom` from the second. A failed unfiltered read leaves `trendFrom` null for that range (the chart then shows all loaded months). No backend change; the range stays twelve months.
- `trend-tab.ts`: new input `from = input<string | null>(null)` (bound `[from]="trendFromStart()"`, null unless its range matches the loaded trend). `private readonly buckets = computed(...)`: the trend's buckets from the first one whose `start >= from()`; all of them when `from()` is null or matches none. The nine readers of `trend().buckets` use it: `trend-tab.ts:149` (title — keeps the fallback 12 while `trend()` is null, so the heading never reads 近 0 個月 during loading), `:154`, `:157`, `:166`, `:194`, `:209`, `:214`, `:243`, `:274`. `compareRows` (`:339`, the 類別 table) reads breakdown rows and is not touched. The 排行 spark and custom-report charts do not use this.

- [ ] **Step 1: Failing tests.** `report-math.spec.ts` (`trendStart`): totals `[0×10, 5, 7]` → 6; `[0, 0, 0, 4, …12 long]` → 3; all non-zero (12) → 0; all zero (12) → 6; 4 long with data only in the last → 0; `[]` → 0. `reports.spec.ts` (趨勢與比較): the `SERIES` fixture with 8 leading zero months → 6 bars, title `每月支出 · 近 6 個月`, x labels start at the 7th loaded month (index 0 prints its year), `3 個月平均 37,566` unchanged; the default fixture still gives 12 bars and `近 12 個月` / `12 個月平均 9,704` (`:203-219`, `:267-269` stay green); picking a category chip whose response carries only that category's key (no `all`) and whose own data starts later keeps the same bars as 全部支出, with one trend request; changing the period while the chip is on sends two trend requests (the category and the unfiltered one) and windows from the unfiltered one; while the trend is loading the heading reads `近 12 個月`; the 類別 table rows are the same before and after (no month-row assertion).
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/{report-math,reports}.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(reports): the trend chart starts at the first month with data (six bars at least)`.

### Task 4: Reward posting copy and the repeated period line

Owner item 10.

**Files:**
- Modify: `frontend/src/app/components/accounting/reward-rule-text.ts`, `reward-rule-text.spec.ts`
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `reward-rules.spec.ts`
- Modify (expectations only): `frontend/src/app/components/accounting/account-settings/account-settings.spec.ts` (`:1044`, both strings of the pair: `'· 下期 15 號入帳'` and `'· 交易後 3 天入帳'`). `rules-section.spec.ts:208` already expects `· ${postingSummary(r)}` and follows the function: leave it.

**Interfaces:**
- `postingSummary(rule)` returns the strings of ruling 4.
- `reward-rules.ts`: `windowLine(account)` no longer takes `mixed`; it picks the card's own usage row — the first row for which the existing `rowIsCalendar(row, account.rules)` is false when the account has a closing day (so the hidden-rule / `isWholeMonth` fallback keeps working), else the first row — and formats as today; when the account has a closing day and every row is calendar, today's `every(rowIsCalendar)` branch stays (`每月 1 日起算 · 本月 …`) (`結帳日 N 號 · 本期 M/D–M/D`, `每月 1 日起算 · 本月 M/D–M/D`, or just the closing-day text when the account has no usage rows). `OverviewRow.window` = `本期 <windowText(usage)>` (`本月 …` for a calendar-month rule) only when the row's `(window_start, window_end)` differs from the card row's; else `null`.

- [ ] **Step 1: Failing tests.** `reward-rule-text.spec.ts` (replacing the old strings at `:21-25`): the five strings of ruling 4 (offsets 0 / 1 / 2 with `post_day` 15; `delay_days` 3; manual) plus offset 1 with `post_day` 5 → `下期 5 號入帳`. `reward-rules.spec.ts`: single-window card → title `結帳日 25 號 · 本期 10/26–11/25`, no `.rr-row-window` (as today); mixed card (statement cycle 10/26–11/25 + a calendar-month rule) → title `結帳日 25 號 · 本期 10/26–11/25`, exactly one `.rr-row-window` reading `本月 11/1–11/30` on the calendar-month rule's row; the same card with usage rows in the other order gives the same result; card without closing day → `每月 1 日起算 · 本月 11/1–11/30`, no row line; card with a closing day whose rows are all calendar-month → `每月 1 日起算 · 本月 …`, no row line. Existing cases that encode the old behaviour are rewritten here: `reward-rules.spec.ts:210-223` (now title `結帳日 25 號 · 本期 10/26–11/25`, one row line `本月 11/1–11/30`), `:225-237` (same start, different end: title `結帳日 30 號 · 本期 3/1–3/30`, the calendar row `本月 3/1–3/31`, in both usage orders), `:254` (posting literal); `:240` (hidden-rule fallback) stays green. Update `account-settings.spec.ts:1044`.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/{reward-rule-text.spec.ts,reward-rules/*.spec.ts,account-settings/**/*.spec.ts}'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `fix(rewards): plainer posting copy; a rule row repeats no period the card title already shows`.

### Task 5: Phone reports gear on the title row

Owner item 11.

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/reports.scss`, `reports.spec.ts` (the CSS-source checks at ~873–907)

- [ ] **Step 1: Failing test.** The scss source keeps the wrap override only inside `@media (min-width: 760px) and (max-width: 1023.98px)`; no rule sets `flex-wrap: wrap` on `.page-header-row` for widths below 760 px. The existing check at `reports.spec.ts:901` matches the block of `@media (max-width: 1023.98px)`: change its regex to the new query so `order: 1` is asserted inside it. Keep the assertion about the phone period control rendering after the header (`:880`). `.rp-period { flex-wrap: wrap }` and the phone `> * { flex: 1 1 100% }` rules stay.
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
- [ ] **Step 3: Implement.** `.hub-check { appearance: none; width: 20px; height: 20px; flex: none; border: 1.5px solid var(--app-border); border-radius: 6px; background: var(--app-surface); display: inline-grid; place-content: center; }` `:checked { background: var(--app-primary); border-color: var(--app-primary); }` with a ✓ drawn by `::after` (a rotated border, `--app-on-primary`); in the dark theme white on `--app-primary` is 3.24:1 (`styles.scss:206-208`), so the checked fill uses `--app-primary-ink-bg` there, as filled buttons do, `:focus-visible { outline: 2px solid var(--app-focus-ring); outline-offset: 2px; }`, `:disabled { opacity: .5 }`. `.sharing-option { display: flex; align-items: center; gap: 10px; min-height: 40px; }`.
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
