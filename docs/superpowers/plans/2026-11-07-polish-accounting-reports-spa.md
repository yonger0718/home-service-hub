# polish-accounting-reports SPA (PR B1 + B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The 報表 pages show running periods 「至今」, never draw an arrow against nothing, and fix the averages, labels, single-entry panel, category detail and header layout of `polish-accounting-reports`. #322's inbox copy rides along.

**Architecture:**
- **B1 (tasks 1–5)** adds pure helpers to `report-math.ts` and swaps `bucketLabel` for `bucketText`. Nothing else on screen changes.
- **B2 (tasks 6–11)** wires the helpers into `reports.ts` (requests, whole-period view, header), the four tabs and `custom-report.ts`.
- The API already ships the D2 window (PR #328, main a8456959).

**Tech Stack:** Angular 20 standalone components with signals, vitest via `ng test` (`@angular/build:unit-test`), SCSS.

**Spec:** `openspec/changes/polish-accounting-reports/` on main: `design.md` D1–D8, `specs/frontend-accounting-reports/spec.md`, `tasks.md`. Read D1 and the spec delta before any B2 task.

## Global Constraints

- **Running:** `from ≤ today < to`, with `to` the inclusive last day. A period whose last day is today is not running, and neither is one that has not started. `elapsedDays` keeps its own rule for 日均 only.
- **Exact copy, verbatim:**
  - `截至 11/7`
  - `本月至今（11/1–11/7）`, and `本期至今（…）` for other units
  - `上月同期`, `上期同期`, `去年同期`
  - `上期無資料`, `去年無資料`
  - `新增`, `— 停止`
  - `另有 11/7 之後已記的 N 筆 $X 未計入`, and for the count metric `另有 11/7 之後已記的 N 筆未計入`
  - `改回截至 11/7`
  - `去年同期沒有記錄，已隱藏比較欄`, and `上一期沒有記錄，已隱藏比較欄` for previous
  - `N 個月平均 X`
  - `11 月 進行中（至 11/7）`, `11/2 週 進行中（至 11/7）`
  - `只有 1 筆 · 10/3 · $1,250`
  - `常去商家`, `（無商家）`
- **Muted styles:** 新增, 停止 and the 無資料 texts use `--app-text-muted` and never `.more` / `.less` (the spend colours).
- **Dates in copy:** `M/D` without zero padding (`11/7`). A range start in another year than `today` reads `2025/12/1`.
- **Worktree rules:**
  - never run `npm run build`, `npm run config`, `set-env.js` or `npm start` in the worktree;
  - tests run with `cd frontend && npx ng test --watch=false --include='<glob>'`;
  - the worktree needs `frontend/node_modules` symlinked to `~/workspace/home-hub/frontend/node_modules` and a placeholder `frontend/src/environments/environment.ts` (git-ignored; copy the shape of `environment.prod.ts` with dummy values).
- **Fixtures** are fictional (the spec's seeded figures). No owner data.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `git add -f`, never commit `.superpowers/`.

## Rulings made in this plan (owner can override)

- **R1, trend chart axes.** The 12-bar axes use the in-year form (`9 月`), with the year only on the first bar and on each January (`2025 年 12 月`, `2026 年 1 月`). Table rows, figures and accessible names follow D7 in full. Why: 12 labels of `2025 年 12 月` do not fit the 358 px phone chart. Cost if wrong: label copy only.
- **R2, running bucket base.** It is the previous bucket's same days for both `compare` kinds, as the requirement words it, and only when the report has a `compare`. Without one there is no change column.
- **R3, the 「另有」 N.** N is Σ `rows.count` (whole) − Σ `rows.count` (to-date). Rows are metric-specific, which `totals.count` is not: it counts expense and income together. A tag report counts per tag, like its 筆數 column. The line shows only when N > 0. $X is the metric total difference, with a `−` when it is negative.
- **R4, the whole-period view.** One signal per page, reset when the period (or the saved report) changes. It is not kept in the URL.

## Review Focus

1. **The last day of a month**, e.g. 2026-11-30 on 11/30. The period is not running: the whole month, no 截至, no 「另有」 line, three requests. The test lives in task 6.
2. **A running period with an empty to-date range.** Example: 11/1 with no rows yet, while rows exist later in the month. The figures read 0, the comparisons read against the base, and the 「另有」 line still names the later rows. The test lives in task 6.
3. **Switching period while the whole view is on.** The view resets to 至今, and stale responses never overwrite a newer period: keep the `requestId` guards. The test lives in task 6.
4. **The first day of the month.** The to-date range is one day, labelled `本月至今（11/1–11/1）`, and the previous window is `10/1–10/1`. The test lives in task 2.
5. **A saved report by week whose last week ends today**, e.g. Sunday. That bucket is not running, so there is no 進行中 suffix and no extra request. The test lives in task 10.

---

## PR B1: helpers, labels, #322 (branch `feat/reports-polish-spa`, one PR)

### Task 1: `compareRange` mirrors D2

**Files:**
- Modify: `frontend/src/app/components/accounting/reports/report-math.ts` (`compareRange`)
- Test: `frontend/src/app/components/accounting/reports/report-math.spec.ts`

**Interfaces:** Produces `compareRange(from, to, 'previous')`, with the same contract as the API's `compare_window`.

- [ ] **Step 1: Write the failing tests.** Put them in `describe('compareRange')`, next to the existing whole-month and day-count cases:
  ```ts
  it('compares a running month with the same days of the previous month', () =>
    expect(compareRange('2026-11-01', '2026-11-07', 'previous')).toEqual({ from: '2026-10-01', to: '2026-10-07' }));
  it('clips a 1st-anchored range to the shorter month', () =>
    expect(compareRange('2026-03-01', '2026-03-30', 'previous')).toEqual({ from: '2026-02-01', to: '2026-02-28' }));
  it('clips to 29 February in a leap year', () =>
    expect(compareRange('2028-03-01', '2028-03-30', 'previous')).toEqual({ from: '2028-02-01', to: '2028-02-29' }));
  it('moves a multi-month running range back by the months it touches', () =>
    expect(compareRange('2026-06-01', '2026-11-07', 'previous')).toEqual({ from: '2025-12-01', to: '2026-05-07' }));
  it('keeps the day-count rule for a range not starting on the 1st', () =>
    expect(compareRange('2026-11-02', '2026-11-07', 'previous')).toEqual({ from: '2026-10-27', to: '2026-11-01' }));
  ```
- [ ] **Step 2: Run.** `npx ng test --watch=false --include='src/app/components/accounting/reports/report-math.spec.ts'`. Expected: the first four FAIL.
- [ ] **Step 3: Implement.** When `from` ends with `-01`: `months` = the calendar months touched. A month-end `to` keeps today's branch. Otherwise return `{ from: <from shifted −months>, to: <to shifted −months, day clipped> }`. Reuse the clip used by the `last_year` branch: lift it into a module helper `shiftDate(iso, months)`. Update the JSDoc to D2.
- [ ] **Step 4: Run.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): compareRange mirrors the API's same-days window (D2)`

### Task 2: Running-period helpers and their copy

**Files:**
- Modify: `report-math.ts`, new section `// ---- running periods (polish D1)`
- Test: `report-math.spec.ts`

**Interfaces.** These are all exported from `report-math.ts`.
- `isRunning(period: ReportPeriod, today: string): boolean`
- `viewRange(period: ReportPeriod, today: string, whole: boolean): { from: string; to: string }`: `{from, to: today}` while running and not `whole`; otherwise `{from, to}`.
- `md(iso: string, today: string): string`: `11/7`; `2025/12/1` when the year differs from `today`'s.
- `asOfText(today: string): string`: `截至 11/7`.
- `toDateNouns(period, today): { current: string; previous: string; lastYear: string; single: boolean }`.
  - Month: `{ current: '本月至今（11/1–11/7）', previous: '上月同期', lastYear: '去年同期', single: false }`.
  - Other units: `current` is `本期至今（…）` and `previous` is `上期同期`.
  - `single` follows `comparesCoincide`. When it is set, both labels read `去年同期`.
- `runningCompareLabels(period): { previous: string; lastYear: string; single: boolean }` (the header).
  - Month: `比 10 月同期` / `比去年同期`.
  - Year: `比去年同期` (single).
  - Custom: `比前一期同期` / `比去年同期`.
- `afterTodayText(today: string, count: number, amount: number | null, whole: boolean): string`.
  - `whole` → `改回截至 11/7`.
  - `amount === null` (the count metric) → `另有 11/7 之後已記的 N 筆未計入`.
  - Otherwise `另有 11/7 之後已記的 N 筆 $X 未計入`, where X is `n0(amount)`.

- [ ] **Step 1: Write the failing tests** (`describe('running periods')`):
  ```ts
  const nov = monthPeriod('2026-11');
  it('runs from the first day until the day before the last', () => {
    expect(isRunning(nov, '2026-11-01')).toBe(true);
    expect(isRunning(nov, '2026-11-07')).toBe(true);
    expect(isRunning(nov, '2026-11-30')).toBe(false);   // last day = finished
    expect(isRunning(nov, '2026-10-31')).toBe(false);   // not started
    expect(isRunning(customPeriod('2026-11-01', '2026-11-07'), '2026-11-07')).toBe(false);
  });
  it('cuts the view at today only while running and not whole', () => {
    expect(viewRange(nov, '2026-11-07', false)).toEqual({ from: '2026-11-01', to: '2026-11-07' });
    expect(viewRange(nov, '2026-11-07', true)).toEqual({ from: '2026-11-01', to: '2026-11-30' });
    expect(viewRange(nov, '2026-11-30', false)).toEqual({ from: '2026-11-01', to: '2026-11-30' });
  });
  it('names the to-date columns', () => {
    expect(toDateNouns(nov, '2026-11-07')).toEqual({ current: '本月至今（11/1–11/7）', previous: '上月同期', lastYear: '去年同期', single: false });
    expect(toDateNouns(nov, '2026-11-01').current).toBe('本月至今（11/1–11/1）');
    expect(toDateNouns({ unit: 'custom', from: '2025-12-01', to: '2026-05-31' }, '2026-03-04').current).toBe('本期至今（2025/12/1–3/4）');
  });
  it('labels the header comparisons of a running month', () =>
    expect(runningCompareLabels(nov)).toEqual({ previous: '比 10 月同期', lastYear: '比去年同期', single: false }));
  it('words the rows-after-today line', () => {
    expect(asOfText('2026-11-07')).toBe('截至 11/7');
    expect(afterTodayText('2026-11-07', 2, 13420, false)).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');
    expect(afterTodayText('2026-11-07', 3, null, false)).toBe('另有 11/7 之後已記的 3 筆未計入');
    expect(afterTodayText('2026-11-07', 2, 13420, true)).toBe('改回截至 11/7');
  });
  ```
- [ ] **Step 2: Run.** Expected: FAIL (not exported).
- [ ] **Step 3: Implement** the signatures above. Each one is a few lines over the existing helpers (`monthOf`, `shiftMonth`, `comparesCoincide`, `n0`).
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): running-period helpers and 至今 copy (D1)`

### Task 3: Changes that know their base (D3)

**Files:** `report-math.ts` (`Change`, `change`, new `baseChange`); `report-math.spec.ts`.

**Interfaces:**
- `Change` gains `kind: 'none' | 'new' | 'gone' | 'delta'`. `change()` returns `kind: 'delta'` and is otherwise unchanged; existing callers keep working.
- `baseChange(current: number, base: number, windowEmpty: boolean, emptyText: '上期無資料' | '去年無資料', withPct = false): Change`:
  - `windowEmpty` → `{ text: emptyText, direction: 'same', kind: 'none' }`;
  - otherwise a rounded base of 0 with current > 0 → `{ text: '新增', direction: 'same', kind: 'new' }`;
  - otherwise a rounded current of 0 with base > 0 → `{ text: '— 停止', direction: 'same', kind: 'gone' }`;
  - otherwise `change(current, base, withPct)`.
- `windowEmpty(response: BreakdownResponse | null | undefined): boolean`: true when `response?.compare` exists and `response.compare.totals.count === 0`.

- [ ] **Step 1: Write the failing tests:**
  ```ts
  it('reads 上期無資料 without an arrow against an empty window', () =>
    expect(baseChange(4233, 0, true, '上期無資料')).toEqual({ text: '上期無資料', direction: 'same', kind: 'none' }));
  it('marks a key new to a non-empty window as 新增, muted', () =>
    expect(baseChange(1200, 0, false, '上期無資料')).toEqual({ text: '新增', direction: 'same', kind: 'new' }));
  it('marks a key gone from the current window as — 停止', () =>
    expect(baseChange(0, 800, false, '去年無資料')).toEqual({ text: '— 停止', direction: 'same', kind: 'gone' }));
  it('falls back to the arrow change', () =>
    expect(baseChange(4233, 6910, false, '上期無資料')).toEqual({ text: '▼ 2,677', direction: 'less', kind: 'delta' }));
  it('detects an empty comparison window from its count', () => {
    expect(windowEmpty({ compare: { totals: { count: 0 } } } as BreakdownResponse)).toBe(true);
    expect(windowEmpty({ compare: null } as BreakdownResponse)).toBe(false);
  });
  ```
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Update existing `change()` expectations in the spec only by adding `kind: 'delta'` where they use `toEqual`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): changes with no base, new and stopped keys (D3)`

### Task 4: Month average and bucket labels (D4, D7)

**Files:**
- `report-math.ts`: new `monthAverage`, `bucketText`, `runningBucket`; remove `bucketLabel`.
- `trend-tab.ts` and `rank-tab.ts`: their `bucketLabel` calls move to `axisText` (below).
- `report-math.spec.ts`.

**Interfaces:**
- `monthAverage(buckets: readonly { start: string; value: number }[], today: string): { months: number; average: number } | null`.
  - The average runs from the first bucket with a non-zero value through the last bucket whose month is before `monthOf(today)`.
  - It returns `null` when that leaves 0 months.
- `bucketText(key: string, granularity: 'month' | 'week' | 'day', spansYears: boolean): string`.
  - `key` is `YYYY-MM` or `YYYY-MM-DD`; a month uses `key.slice(0, 7)`.
  - Month: `9 月` / `2025 年 9 月`. Week: `10/5 週` / `2025/10/5 週`. Day: `10/5` / `2025/10/5`.
- `axisText(start: string, index: number): string`. This is R1: `bucketText(start, 'month', index === 0 || month === 1)`.
- `runningBucket(key: string, granularity: 'month' | 'week' | 'day', today: string): boolean`.
  - It is true when the bucket starts on or before `today` and ends after it (a month: its last day; a week: start + 6).
  - A day bucket is always false.
- `runningSuffix(today: string): string`: `進行中（至 11/7）`.

- [ ] **Step 1: Write the failing tests:**
  ```ts
  it('averages the months with data through the last completed month', () => {
    const series = ['2025-12','2026-01','2026-02','2026-03','2026-04','2026-05','2026-06','2026-07','2026-08','2026-09','2026-10','2026-11']
      .map((m, i) => ({ start: `${m}-01`, value: [0,0,0,0,0,0,0,0,31200,39608,41890,4233][i] }));
    expect(monthAverage(series, '2026-11-07')).toEqual({ months: 3, average: 37566 });
  });
  it('has no average for a ledger younger than one completed month', () =>
    expect(monthAverage([{ start: '2026-11-01', value: 4233 }], '2026-11-07')).toBeNull());
  it('formats bucket keys', () => {
    expect(bucketText('2026-09', 'month', false)).toBe('9 月');
    expect(bucketText('2025-09-01', 'month', true)).toBe('2025 年 9 月');
    expect(bucketText('2026-10-05', 'week', false)).toBe('10/5 週');
    expect(bucketText('2025-10-05', 'day', true)).toBe('2025/10/5');
  });
  it('marks only a bucket that has started and ends after today as running', () => {
    expect(runningBucket('2026-11', 'month', '2026-11-07')).toBe(true);
    expect(runningBucket('2026-11', 'month', '2026-11-30')).toBe(false);
    expect(runningBucket('2026-11-02', 'week', '2026-11-07')).toBe(true);
    expect(runningBucket('2026-11-02', 'week', '2026-11-08')).toBe(false); // Sunday = last day
    expect(runningBucket('2026-11-07', 'day', '2026-11-07')).toBe(false);
    expect(`11 月 ${runningSuffix('2026-11-07')}`).toBe('11 月 進行中（至 11/7）');
  });
  ```
  - The average is `(31200 + 39608 + 41890) / 3 = 37566`.
  - In the trend and rank specs, update the axis-label expectations from `11月` to `11 月`, plus the year on the first bar and on each January.
- [ ] **Step 2: Run** the reports specs: `--include='src/app/components/accounting/reports/**/*.spec.ts'`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Delete `bucketLabel`. Replace its four call sites in `trend-tab.ts` (bars, aria) and `rank-tab.ts` (spark bars, aria) with `axisText(bucket.start, index)`.
- [ ] **Step 4: Run** the same glob. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): month average over months with data; bucketText replaces bucketLabel (D4, D7)`

### Task 5: #322: inbox copy for busy and retry codes

**Files:**
- Modify: `frontend/src/app/components/accounting/statements/statement-view.ts` (`ACTION_ERRORS`, line ~96)
- Test: `frontend/src/app/components/accounting/statements/statement-view.spec.ts`

- [ ] **Step 1: Write the failing test.** In `describe('actionErrorMessage')`, or a new one beside the file's existing error tests:
  ```ts
  it.each([
    ['retry: an account this action writes is being changed; try again', '帳戶正在更新，請再按一次'],
    ['action_busy', '其他作業進行中，請稍後再試'],
    ['settlement_busy', '其他作業進行中，請稍後再試'],
    ['auto_pay_busy', '其他作業進行中，請稍後再試'],
    ['peer_statement_busy', '其他作業進行中，請稍後再試'],
  ])('words %s in Chinese', (detail, text) =>
    expect(actionErrorMessage(new HttpErrorResponse({ status: 409, error: { detail } }))).toBe(text));
  ```
  - Check how `apiErrorText` reads the body (`detail`) in `http-errors.ts`, and build the error the same way the file's other tests do.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/statements/statement-view.spec.ts'`. Expected: FAIL (the raw English comes back).
- [ ] **Step 3: Implement.** Add the five keys to `ACTION_ERRORS` with the texts above.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** The body carries `Closes #322`:
  ```
  fix(inbox): Chinese copy for retry and busy 409 codes (#322)

  Closes #322
  ```

**B1 close:** run the whole SPA suite, `npx ng test --watch=false`. Every task's spec stays green and nothing else turns red. Open PR B1, then do one non-Claude review via Multica lead-claude.

---

## PR B2: screens (branch `feat/reports-polish-spa-b2`, from B1's head; rebase onto main once B1 merges)

### Task 6: Page data: to-date requests, the whole view, the header

**Files:** `reports.ts`, `reports.html`, `reports.scss`; test `reports.spec.ts`.

**Interfaces:**
- Consumes `isRunning`, `viewRange`, `runningCompareLabels`, `asOfText`, `baseChange`, `windowEmpty` and `afterTodayText` from tasks 2–3.
- Produces these signals on `AccountingReportsComponent`:
  - `running = computed(() => isRunning(period(), today()))`;
  - `wholeView = signal(false)`, reset in the period effect;
  - `expenseWhole` and `incomeWhole`: `signal<BreakdownResponse | null>`;
  - `viewExpense` and `viewIncome`: `computed`, the whole responses when `wholeView()`, else the to-date ones;
  - `showCompare = computed(() => !wholeView())`;
  - `toggleWhole(): void`.

`load(period)`:
- While running, the three existing breakdowns use `{ date_from: period.from, date_to: today }`. `forkJoin` adds `expenseWhole` and `incomeWhole` (whole period, `split_by: 'subcategory'`, no `compare`).
- Otherwise it keeps today's three requests and sets the whole signals to null.
- `loadTrend` and `loadRank` use `viewRange(...)`, so their effects also read `wholeView()`. The trend's `to` is the view range's end. The spark keeps `trendRange` over the view end.

Header `figures`:
- The amounts come from `viewExpense` / `viewIncome`.
- The 支出 foot while running is `runningCompareLabels`, each segment `${label} ${baseChange(...).text}`. A `kind: 'none'` segment is just its text. Then ` · 截至 11/7`.
- In the whole view the foot is `${count} 筆`, with no comparison.
- A finished period keeps today's foot, but goes through `baseChange`, so an empty window reads 上期無資料.

D8 header: `.rp-period` is projected into `<app-page-header>`. From 1024 px it sits on the title row. Below 1024 px a `reports.scss` rule wraps `.page-actions` to its own row under the title. Keep one DOM instance.

- [ ] **Step 1: Write the failing tests** in `reports.spec.ts`, following its HttpTestingController patterns.
  - `it('loads a running month to date plus two whole-month breakdowns', …)`:
    - today 2026-11-07, `?month=2026-11`;
    - expect three requests with `date_to=2026-11-07` and two with `date_to=2026-11-30` and no `compare`;
    - flush expense 4,233 with compare 6,910 and last year 5,120;
    - the 支出 figure is `$4,233`, and its foot is `比 10 月同期 ▼ 2,677 · 比去年同期 ▼ 887 · 截至 11/7`.
  - `it('loads a month on its last day as a finished month', …)`: today 2026-11-30. Three requests, all `date_to=2026-11-30`; no 截至.
  - `it('reads 上期無資料 when the previous window counts nothing', …)`: compare totals count 0 → the foot contains `上期無資料` and no `▲`.
  - `it('keeps a to-date view with no rows yet and still names the later rows', …)`:
    - today 2026-11-01;
    - to-date rows are empty; the whole rows hold 2 rows / 13,420;
    - the 支出 figure is `$0`;
    - the 總覽 shows `另有 11/1 之後已記的 2 筆 $13,420 未計入`.
  - `it('switches to the whole month and back, and resets on another month', …)`:
    - pressing the line shows the whole figures with no comparison text;
    - moving `‹` resets `wholeView` to false;
    - a stale response for the old period is ignored.
  - `it('projects the period control into the page header', …)`: `.page-actions .rp-period` exists.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/reports.spec.ts'`. Expected: FAIL.
- [ ] **Step 3: Implement** as above. Pass `[expenseWhole]`, `[incomeWhole]`, `[whole]`, `[today]` and `(toggleWhole)` to 總覽 and 趨勢 (tasks 7 and 8 define those inputs). Land those template bindings together with tasks 7 and 8 if the build needs it; this task's tests cover the page.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): running periods load to date, whole-period view, one-row header (D1, D8)`

### Task 7: 總覽: to-date detail, 常去商家, the 「另有」 line

**Files:** `overview-tab.ts`, `overview-tab.html`, `overview-tab.scss`; test: the 總覽 describe in `reports.spec.ts`, or a new `overview-tab.spec.ts`.

**Interfaces.** New inputs:
- `expenseWhole` and `incomeWhole`: `input<BreakdownResponse | null>(null)`;
- `whole = input(false)`;
- `today = input.required<string>()`;
- `range = input.required<{ from: string; to: string }>()`, the view range for the merchant read.

New output: `toggleWhole = output<void>()`. It injects `ReportsService`.

- The detail's change stat is `baseChange(amount, compare_amount, windowEmpty(response), '上期無資料')`. A muted class is applied when `kind !== 'delta'`. The stat is left out when `whole()`.
- The 「另有」 line for the shown kind comes from R3 over (`kind` to-date response, `kind` whole response). It is a `<button class="ov-after">` under the list, emitting `toggleWhole`.
- D5: when `selected().splits` is exactly one split whose label equals `selected().label`, the card reads 常去商家.
  - The merchants come from `getBreakdown({ date_from: range.from, date_to: range.to, metric: kind, group_by: 'merchant', category_id: [Number(key)] })`.
  - It lists the first five non-null rows, plus the null row as `（無商家）` when present.
  - The result is cached in a `Map` keyed `${from}|${to}|${kind}|${key}`.
  - A failed read shows only the figures and the drill link.

- [ ] **Step 1: Write the failing tests:**
  - `it('lists 常去商家 for a category without subcategories', …)`:
    - 居家 has one split labelled 居家, plus merchant rows 房東 12,000 and 台電 1,420;
    - the card shows `常去商家`, `房東`, `12,000`, `台電`, `1,420`, and no split row 居家.
  - `it('reads the merchants once per period and category', …)`: select 居家, another row, then 居家 again → one merchant request.
  - `it('shows figures and the drill link only when the merchants fail', …)`.
  - `it('mutes 新增 when the category had nothing last period', …)`: compare_amount 0, window non-empty → the stat text is `新增` and the class is not `more`.
  - `it('names rows after today under the list and emits the switch', …)`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): 總覽 detail to date, 常去商家, rows after today (D1, D3, D5)`

### Task 8: 趨勢與比較: average, running bar, to-date columns, empty columns

**Files:** `trend-tab.ts`, `trend-tab.html`, `trend-tab.scss`; test `reports.spec.ts` (trend describe).

**Interfaces.** New inputs: `expenseWhole`, `whole`, `today`. New output: `toggleWhole`.
- The average comes from `monthAverage(buckets → { start, value }, today())`. The label is `${months} 個月平均 ${n0(average)}`; with `null` there is no line and no label.
- The running bar is the last bucket when `runningBucket(start.slice(0, 7), 'month', today)`. It gets class `running`, a hatched fill in the SCSS that works in light and dark.
- Column headings: while running and not whole, `toDateNouns`; otherwise `periodNouns`.
- Changes: `baseChange` with `windowEmpty(expense)` / `windowEmpty(lastYear)` and `上期無資料` / `去年無資料`.
- A column whose window is empty is dropped, together with its change column, and a muted note follows:
  - `去年同期沒有記錄，已隱藏比較欄` for last year;
  - `上一期沒有記錄，已隱藏比較欄` for previous.
- In the whole view the table has only the current column, and the 「另有」 line sits under the table.

- [ ] **Step 1: Write the failing tests:**
  - `it('averages the months with data and labels 3 個月平均 37,566', …)`, using the task 4 series.
  - `it('draws no average for a ledger younger than a month', …)`.
  - `it('hatches the running month', …)`: the last `rect` has class `running`.
  - `it('heads the table 本月至今（11/1–11/7） / 上月同期 / 去年同期 while running', …)`.
  - `it('hides an empty last-year column with a note', …)`: last year count 0 → no `去年同期` heading, and the note text is present.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): trend average over months with data, running bar, to-date and empty columns (D1, D3, D4)`

### Task 9: 排行: single-entry panel (D6)

**Files:** `rank-tab.ts`, `rank-tab.html`; test `reports.spec.ts` (rank describe).

`stats` for `row.count === 1`:
- one stat `只有 1 筆 · 10/3 · $1,250`, built from `first_date` (M/D via `md`) and the amount;
- without `first_date`: `只有 1 筆 · $1,250`;
- then `占支出` and the month average as today.

For `count > 1`, 平均間隔 is omitted when `first_date === last_date`.

- [ ] **Step 1: Write the failing tests:**
  - `it('shows 只有 1 筆 with the date and amount for one visit', …)`: 饗食天堂, one 1,250 entry on 2026-10-03 → the text contains `只有 1 筆 · 10/3 · $1,250`, and there is no `中位數` / `最大` / `最小` / `平均間隔`.
  - `it('omits the date when the API has no statistics', …)`: `first_date: null`.
  - `it('hides 平均間隔 for entries on one day', …)`.
- [ ] **Step 2: Run.** **Step 3: Implement.** **Step 4: Run.** Expected: FAIL, then PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): 排行 single-entry statistics (D6)`

### Task 10: 自訂報表: to date, running bucket, labels, empty columns

**Files:** `custom-report.ts`, `custom-report.html`, `custom-report.scss`; test `custom-report.spec.ts`.

**Interfaces.** New signals: `running`, `wholeView` (reset when the config or the id changes), `previewWhole`, `runningBase = signal<number | null>(null)`.

`loadPreview(config, today)`:
- **While running** (`isRunning(resolvePeriod(config.period, today), today)`):
  - the to-date query `{ ...configQuery(config, today), date_to: today }`;
  - the whole query `{ ...configQuery(config, today), compare: undefined }`;
  - **with a `compare` and `group_by` `month` or `week`** (R2), a third query over the previous bucket's same days. For a month: `compareRange(<bucket start>, today, 'previous')`. For a week: start − 7 through today − 7. The query is `{ …, group_by: config.group_by, compare: undefined }`, and `runningBase` is `totalOf(totals, metric)`.
- **Otherwise** the single query as today.
- The `requestId` guard covers all three.

Rows and labels:
- `rowLabel` for time dimensions is `bucketText(key, group_by, spansYears)`, where `spansYears` means the resolved period's years differ. The running bucket appends ` ${runningSuffix(today)}`. 最高 uses `rowLabel`.
- The running bucket's base is `runningBase()` and its change is `baseChange(...)`. Other rows keep `compare_amount`.
- The total row's base is `totalOf(compare.totals, metric)`.
- `平均每月` / `平均每組` leave the running bucket out of both the sum and the count.
- The running bucket's bar is hatched, as in task 8.

Columns:
- While running and not whole: the 合計 heading is `toDateNouns(...).current`, and the base heading is `上期同期` or `去年同期`.
- An empty compare window drops the base and 增減 columns and shows the note (task 8 copy).
- 新增 / 停止 come from `baseChange`, muted.
- In the whole view there are no compare columns, and the 「另有」 line is under the table (R3, with `metric === 'count'` → amount null).

- [ ] **Step 1: Write the failing tests** (today 2026-11-07):
  - `it('reads November 1–7 against October 1–7 in 近 6 個月 by month', …)`:
    - template 週末外食 with `compare: 'previous'`;
    - expect requests `date_to=2026-11-07` with compare, the whole period without compare, and `2026-10-01..2026-10-07`;
    - the last row reads `11 月 進行中（至 11/7）`;
    - its change is against the short-range total;
    - the total row's base is `compare.totals`;
    - 平均每月 averages June–October.
  - `it('reads 9 月 under 最高', …)`: September is highest → `9 月` with `$367`.
  - `it('hides empty last-year columns with a note and marks no 新增', …)`: the spec scenario "Ledger younger than a year".
  - `it('does not run a week whose last day is today', …)`:
    - today Sunday 2026-11-08, `group_by: 'week'`, `this_month` preset;
    - the period runs (11/8 < 11/30) but the 11/2 week does not;
    - no third request; no 進行中.
  - `it('names rows after today and switches to the whole period', …)`, including the count-metric text.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reports/custom-report.spec.ts'`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(reports-spa): saved reports to date, running bucket, labels and empty columns (D1, D3, D4, D7)`

### Task 11: Spec bookkeeping

**Files:** `openspec/changes/polish-accounting-reports/tasks.md`.

- [ ] **Step 1: Tick.** Tick 2.1–2.5 (B1) and 3.1–3.7 (B2) as they land. Leave 3.8 (screens) for the release step.
- [ ] **Step 2: Validate.** Run `openspec validate polish-accounting-reports --strict`. Expected: valid.
- [ ] **Step 3: Commit.** Fold this into the last commit of each PR.

**B2 close:**
- Full SPA suite green.
- Open PR B2 and do one non-Claude review.
- After both merge, the SPA release prepares a kit from main. Follow the latest release record: `~/reports/home-hub-reprice306-release-20261107T124625Z/RELEASE.md`, plus any later one. Read the store state before pinning.
- Take the screens at 1440 / 1100 / 390, light and dark, on the seeded demo (tasks 3.8). Check that the 記一筆 button covers no figure at the scrolled end, and that the R1 axis labels fit at 390 px.
- Ask the owner to approve the publish. Stop preview 18081 first.
