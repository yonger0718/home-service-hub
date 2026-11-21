# Notification center PR B: 通知 page, count, entry points, ledger sources — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give pending ledger work one place and one number: a `/notifications` page listing tasks (card bills, schedules by definition, statements to reconcile, the import to review, an interim e-invoice task) in 已逾期 / 今天 / 要你確認 / 系統 with a folded 追蹤中, a task count shown only on the 通知 entry points (sidebar item, phone header bell), and dots instead of counts everywhere else.

**Architecture:** SPA only (`frontend/`), no API change. Pure builder functions turn each endpoint's response into `NotifTask`s (stable id, tier, fingerprint); `NotificationCenterService` (root) reads every source once per refresh, each with its own generation guard and failure state, and exposes `tasks`, `summary` (count, colour, label), `failedSources` and the nav `badges` (dots). `NavBadgeService` stays as a facade over it. The page is one column that renders the service's signals; 入帳 / 全部入帳 run in place through the existing schedule calls and report through `injectToasts()`; every other action is a link to a page that exists today.

**Tech Stack:** Angular 21.2 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with the tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/add-notification-center/` — design D1, D2, D3, D8, Risks; tasks §2 (2.1–2.5); `specs/frontend-notification-center/spec.md` (Requirements "Notification page and entry points", "Tasks grouped by urgency", "Notification count", "Empty state"), `specs/frontend-app-shell/spec.md` ("Navigation badges"), `specs/frontend-accounting-ledger/spec.md` ("Reminder bell count" removed). Artboards `design_handoff_v3/design_refs/NotifDesktop.dc.html`, `NotifPhone.dc.html`, `FeedbackSpec.dc.html` (sections A and D); colour rules in `design_handoff_v3/README.md`. Rulings R1–R24: `/home/opc/reports/notif-b-rulings-20261121.md` (copied below). `openspec/specs/` is not edited in this PR.

## Global Constraints

- Frontend only. No backend, no new dependency, no new endpoint. Never run uvicorn or alembic; never read `~/.config`, `~/.hermes` or any `.env`.
- Colours only through tokens of `frontend/src/styles.scss`; artboard hexes are mapped, never copied (`design_handoff_v3/README.md` § Colour mapping). No hex, `rgb()` or `rgba()` literal in a component stylesheet or inline style. A new token is added in both theme blocks (`:root` and `.app-dark-mode`).
- Chinese UI copy exactly as this plan's copy tables give it. "(copy: planner)" marks copy the artboard does not give.
- Count rule (D3): tasks in 已逾期 + 今天 + 要你確認 + 系統; 追蹤中 never counts. Label `通知，N 件要處理` plus `，M 件已逾期` when M > 0. Red (`--c-red-ink`) when any counted task is 已逾期 or 系統, else `--app-primary`. The count renders on the 通知 sidebar item and the phone header bell only.
- Each source is read once per refresh; refreshes from start / window focus / page visible are throttled to one per 30 s (`BADGE_REFRESH_MIN_MS`); a source that fails drops its tasks and its dot and never blocks another source.
- No snooze, no ⋯ menu, no 稍後提醒, no right pane, no `/notifications/recon/…` route (R3, R20, R1). No dividend, import-failure, 國泰 or auto-pay task (PR C / E).
- The reminders page (`/accounting/reminders`), the inbox panel and `inboxBellCount`'s two remaining call sites (`inbox-panel.ts`, `reminders.ts`) are not changed.
- Tests use fictional data only: 測試銀行卡, 測試月費, 測試分期, 測試房租, 測試帳戶, people 甲 / 乙 / 丙, invented amounts; fixed today `2026-10-24`.
- Commit per task with explicit paths, message in English, ending with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. No `--amend`, no push.
- Known load-flaky tests (pass alone; not this PR's): two fragment-scroll tests in `reward-rules.spec.ts`, `shell.spec` "820 px: N inside the detail sheet opens the form", `rules-section.spec.ts` "a menu that does not fit below the viewport opens above ⋯".

## Rulings made while planning

R1–R24 are the controller's (2026-11-21); P1–P12 are the planner's, made where the rulings and the spec were silent. Each names its reason and what it costs if wrong.

| # | Ruling | Reason | If wrong |
|---|---|---|---|
| R1 | A statement task's 對帳 goes to `/accounting/accounts/:id/statements/:sid`, built by one function `reconTaskRoute()` | `/notifications/recon/:statementId` is PR D | PR D edits one function |
| R2 | 「N 筆要你決定」 counts unfolded inbox items whose question is in the ask bucket, per statement. The other buckets (proposed, balance_gap, settlement, dividend, other) make no task | The spec says "inbox items"; cards fold several | One predicate in `recon.ts` |
| R3 | No snooze in B: no storage, no menu, no snooze test; `summarize()` takes the task list only | Snooze is tasks.md 5.3 (PR E) | E adds a filter before `summarize()` |
| R4 | In place: 入帳 (`postScheduleInstance`) and 全部入帳 (`catchUpSchedule`), result as a toast, busy state on the row. Links: 記繳款 → the card's account page; 對帳 → R1; import review → `/accounting/accounts`; debts → `/accounting/reminders?tab=debts`. A failed write shows 失敗 with 重試 only for a network failure, re-sending the captured ids (`guardedRetry`) | Both calls exist and are idempotent per instance; a bill payment has no in-place call | A row's action changes kind |
| R5 | One interim task in 要你確認, 「N 張發票要確認」, when the inbox's `counts.einvoice_ask` > 0, linking to `/accounting/reminders?tab=todo` | Without it pending invoices stop counting anywhere between B and F | Remove one builder; PR F replaces it anyway |
| R6 | A card bill's tier date is `BillState.payDate` (the debit day). 已逾期 before today, 今天 on it, 追蹤中 1–7 days ahead, absent beyond; paid bills absent | The reminders page and #311 use the debit day | One field name in `bills.ts` |
| R7 | One schedule task per definition: 已逾期 if any pending instance is due before today, else 今天 if one is due today; only posted-partial instances → 要你確認 「金額未入滿」. Sum = totals per currency joined with 「、」. 全部入帳 when more than one instance is overdue, else 入帳. Failing definitions make no task | D2; the queue also returns future instances | Tier rules in `schedules.ts` |
| R8 | The debts row reads `getCounterparties()` (people with `open_count > 0`); never counted. Debt-event reviews stay on the reminders page | `/accounts/debts` has no names | One source swap |
| R9 | Dots: import review → 記帳記錄 (`accounting-timeline`), label `有待確認的匯入紀錄`; an overdue card bill → red dot on 帳戶 (`accounting`), label `有逾期的卡費`; 匯入 dot unchanged | D8 | Two keys |
| R10 | `NotifTask.fingerprint` exists now (bill: remaining + tier date; schedule: open count + oldest due; recon: ask count; import: review count); unused in B | PR E's wake-up needs it | A model change in E |
| R11 | Oldest first inside a section: bill by tier date, schedule by oldest open due date, statement by `period_end`, import by `started_at`; ties by id | Spec gives no key | `sortKey` values |
| R12 | 「已處理（近 7 天）N 件 ›」 links to the reconciliation handled list, N from `InboxService.actions()`; hidden when that read fails | No cross-source history exists | Row removed |
| R13 | Every source is read and counted whatever groups are hidden | 通知 is outside the groups | A filter in the service |
| R14 | The phone bell lives in `PageHeaderComponent` only, below 760 px, on every route but `/notifications`. The timeline's month-bar bell becomes a plain link and is not rendered below 760 px | Never two bells on a phone | See the gap list under Task 6 |
| R15 | `/notifications` resolves to a synthetic nav item outside `NAV_GROUPS` (title 通知): no group active, no sub-nav, no tab highlighted. Phone header ‹ goes back in history, fallback `/accounting` | Unknown paths fall back to 庫存 today (`navigation.ts:92`) | `navItemForUrl` branch |
| R16 | 股利 dot, dividend and system tasks are PR C. The 投資 chip exists and shows 0 | Migration plan | — |
| R17 | Opening the page refreshes past the throttle unless a refresh is in flight | The owner came to see current work | One call in the page |
| R18 | A card whose statement read failed contributes no task and 帳單 is named. Source names: 帳單, 週期, 對帳, 匯入檢查, 借還款, joined with 、. A failed source hides its dot | Spec names only 對帳 | Strings |
| R19 | Chip counts are counted tasks only | Artboard 7 / 5 / 2 | One filter |
| R20 | No right-pane host; one column at every width | Nothing to show in the pane before D | D builds the host |
| R21 | `NavBadgeService` stays as a facade delegating to `NotificationCenterService`; its logic and its spec's count cases move | About 9 spec files stub it | See Conflicts (one new member) |
| R22 | The page ends with 「提醒中心（借還款、週期與分期管理）」 → `/accounting/reminders` | The timeline bell was the only way in | — |
| R23 | The new service drives `BillingService.ensure / refresh` exactly as `nav-badge.service.ts:105–119` does | Timeline and reminders bill figures depend on it | Regression there |
| R24 | Request load accepted: no aggregate endpoint; one read per source per refresh, one inbox read shared by the statement tasks and the e-invoice task. Noted in the PR body | Frontend-only PR | An endpoint later |
| P1 | A partial-only schedule task's primary is the link 逐期處理 (reminders 待完成交易); every schedule task has 逐期處理 as its secondary link | Accepting or re-posting a partial needs the reminders row's controls | One action |
| P2 | Statement task title 「對帳 · <帳戶>（至 MM/DD）」 from `statement.period_end` | The artboard's 「9 月帳單」 needs the period start, which the inbox item does not carry | String |
| P3 | With badges off the page keeps its subtitle and chip counts | They are the page's content, not badges; the spec scenario names the entry points | Two `@if`s |
| P4 | The 「無法讀取：…」 line carries a 重試 button and stays in place through the retry; 都處理好了 is never shown while a source failed | "All done" would be false; PR A's rule for read failures | Markup |
| P5 | Count pill text uses a new token `--app-count-ink` (`#fff` light; the dark `--app-bg` value `#0f1218` dark) | White on dark `--c-red-ink` `#ff6369` is 2.9:1, on dark `--app-primary` 3.2:1 | Token value |
| P6 | A row whose command succeeded stays busy until the refreshed list lands; if the row then leaves while focus is on `<body>`, focus goes to the row now at its place, else to the 追蹤中 summary | A second press in the gap would post twice; focus must not fall out of the page | Two effects |
| P7 | The facade gains one member, `summary`, read by `NavigationStateService` | The entry points need the count, and injecting the real service into nav components would pull `HttpClient` into specs that stub the facade | See Conflicts |
| P8 | Handled count = actions with `reverted_by_action_id === null` created in the last 7 × 24 h, from `actions(50)`; 50 of 50 inside the window reads 「50+ 件」; 0 hides the row | The feed's default page is 50 | Limit / text |
| P9 | The chip choice is a local signal (not in the URL). A chip with no task shows 「這個來源目前沒有要處理的事」 (copy: planner) | Nothing links to a chip yet | Query param later |
| P10 | Upcoming schedule instances are not in 追蹤中 (the artboard lists 「Spotify 10/28」) | D2 tiers schedules 已逾期 / 今天 only | One more row source |
| P11 | The timeline's 「近 7 天有 N 筆繳費到期」 button (`timeline.html:186`) stays | It is not a count and opens the upcoming list | Remove later |
| P12 | Navigation primaries are `<a>`; a row is not itself a link | No nested interactive elements; one tab stop per action | Markup |

## Conflicts between the change spec and shipped behaviour

| Spec text | Conflict | Ruling |
|---|---|---|
| NC "One case at a time reconciliation": the statement row opens `/notifications/recon/:statementId` | Route is PR D | R1: existing statement page |
| NC "Notification count … that are not snoozed"; tasks.md 2.5 "count excludes tracking and snoozed" | Snooze is 5.3 | R3: no snooze; 2.5 ticked with the note 「(snoozed: PR E, 5.3)」 |
| NC "E-invoice task": 「電子發票 · N 張要確認」 opening `/notifications/einvoices`; AL removal: "the `ask + einvoice_ask` sums on the timeline and the reminders page go with it" | The flow is PR F; the reminders page's sum is 6.6 | R5: interim task 「N 張發票要確認」 → reminders 待處理; the timeline's sum goes now, the reminders page's in F |
| D2 "已逾期 after the due date" | The shipped bill rule uses the debit day | R6: `payDate` |
| NC "a link to items handled in the last 7 days" (artboard: 12 件 across sources) | Only reconciliation has a handled feed | R12: reconciliation actions only |
| NC "Below 760 px every page header SHALL show a bell" | Ten pages draw their own header | R14: `PageHeaderComponent` only; gap list in Task 6 |
| NC "It SHALL open in the right pane of `/notifications` at ≥ 1024 px" | No pane content in B | R20: one column |
| Proposal Impact: "`NavBadgeService` folds into the notification service" | About 9 specs stub it | R21 + P7: facade kept, one new member `summary`; each stub gains one line |
| D2 "Debts — `GET /accounts/debts` — one summary" | The artboard row names people | R8: `getCounterparties()` |
| README "red count pill: … white text" | Fails contrast in dark | P5 |
| Artboard schedule meta 「每月 20 日 · 國泰 CUBE 卡」 | The queue row has no cadence text | Meta is the paying account's name only |
| AS scenario "Count in the sidebar, dot on the phone tab" (股利 dot) | Dividend data is PR C | Dot plumbing ships; the scenario is verified in C |

## Review Focus

- A late answer: 入帳 answers after the owner left the page — the toast still shows (no 重試), nothing throws, and the count updates because the write bumps `entriesChanged`.
- A failed write then the silent reload: the 失敗 toast is still the one shown after the service re-reads (the reload raises no toast and no 完成).
- 重試 after the list moved: the retry re-sends the captured instance / definition id even if the row is gone; while the same task is busy it re-raises the toast instead of sending.
- Parallel refreshes: a focus refresh and a write reload overlap — only the newest answer of each source is applied; an older inbox answer never brings back a statement task that the newer one dropped.
- A source that fails after it once succeeded: its tasks and its dot leave, the count drops, the line names it, and 都處理好了 does not appear.
- Double bell: at 390 on `/accounting` exactly one bell is in the DOM; at 1440 the phone bell is absent and the month-bar link is present.
- `/notifications` at 390: no tab highlighted, no sub-nav, h1 通知; on a direct load ‹ goes to `/accounting`.
- Real-browser overlap (jsdom cannot see it): the last row's buttons against the floating 記一筆 at 820 / 1100 / 1440 and against the tab bar's + at 390; the toast above both.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `frontend/src/app/components/notifications/notif-task.ts` | Model, sort, summary, label, chip counts, failure line | 1 |
| `…/notifications/sources/bills.ts`, `schedules.ts` | Card bill and schedule builders | 2 |
| `…/notifications/sources/recon.ts`, `ledger-misc.ts` | Statement, e-invoice, import review builders; debts and upcoming rows | 3 |
| `frontend/src/app/services/notification-center.service.ts` | Reads, generations, throttle, failure, signals, badges | 4 |
| `frontend/src/app/services/nav-badge.service.ts` | Facade | 4 |
| `components/shell/navigation.ts`, `navigation-state.service.ts`, sidebar / sub-nav / mobile-nav | `/notifications` resolution; dots with a tone | 5 |
| sidebar, `shell/page-header`, `accounting/timeline` | 通知 item, phone bell, timeline link | 6 |
| `…/notifications/notif-row/` | One row | 7 |
| `…/notifications/notifications.*`, `app.routes.ts` | Page and route | 8–10 |

**Order.** Task 1 first. Then Tasks 2, 3 and 5 in parallel (disjoint files). Task 4 after 2, 3 and 5. Then Tasks 6 and 7 in parallel. Tasks 8 → 9 → 10 → 11 in sequence.

---

### Task 1: Task model, order, count and label

tasks.md 2.1 (model, stable ids, tiers), 2.4 (count rule). Pure functions.

**Files:**
- Create: `frontend/src/app/components/notifications/notif-task.ts`, `notif-task.spec.ts`

**Interfaces (produced):**

```ts
export type NotifTier = 'overdue' | 'today' | 'confirm' | 'system' | 'tracking';
export type NotifSource = 'ledger' | 'portfolio';
export type NotifSourceKey = 'bills' | 'schedules' | 'recon' | 'importReview' | 'debts';
export interface NotifLink { readonly label: string; readonly route: string; readonly queryParams?: Readonly<Record<string, string>>; }
export type NotifCommand =
  | { readonly kind: 'postInstance'; readonly instanceId: number; readonly name: string }
  | { readonly kind: 'catchUp'; readonly definitionId: number; readonly name: string };
export interface NotifCommandAction { readonly label: string; readonly command: NotifCommand; }
export interface NotifTask {
  readonly id: string;             // bill:<accountId>:<due> | sched:<definitionId> | recon:<statementId> | import:<runId|started_at> | einvoice
  readonly source: NotifSource;
  readonly from: NotifSourceKey;
  readonly tier: NotifTier;
  readonly icon: string;           // a `pi-…` class or an emoji (schedule category icon)
  readonly title: string;
  readonly when: string;           // bold part of line 2; red in 已逾期
  readonly meta: string | null;    // muted part of line 2
  readonly amount: string | null;  // already formatted
  readonly progress: { readonly k: number; readonly n: number } | null;
  readonly primary: NotifLink | NotifCommandAction;
  readonly secondary: readonly NotifLink[];
  readonly sortKey: string;        // ISO date or datetime; ascending = oldest first
  readonly fingerprint: string;    // R10
}
export interface NotifTrackingRow { readonly id: 'debts' | 'upcoming'; readonly title: string; readonly meta: string; readonly count: number; readonly link: NotifLink; }
export interface NotifSummary { readonly total: number; readonly overdue: number; readonly tone: 'red' | 'primary'; readonly label: string; }
export type NotifChip = 'all' | NotifSource;

export const COUNTED_TIERS: readonly NotifTier[] = ['overdue', 'today', 'confirm', 'system'];
export const TIER_LABELS: Record<NotifTier, string> = { overdue: '已逾期', today: '今天', confirm: '要你確認', system: '系統', tracking: '追蹤中' };
export const SOURCE_TAGS: Record<NotifSource, string> = { ledger: '財務', portfolio: '投資' };
export const SOURCE_NAMES: Record<NotifSourceKey, string> = { bills: '帳單', schedules: '週期', recon: '對帳', importReview: '匯入檢查', debts: '借還款' };
export const NO_TASKS: NotifSummary = { total: 0, overdue: 0, tone: 'primary', label: '通知' };

export function isCommand(action: NotifLink | NotifCommandAction): action is NotifCommandAction;
/** Tier order of COUNTED_TIERS then tracking; inside a tier by sortKey, then id. Returns a new array. */
export function sortTasks(tasks: readonly NotifTask[]): NotifTask[];
/** Counted tasks only. overdue = tasks in 已逾期; tone red when any is 已逾期 or 系統. */
export function summarize(tasks: readonly NotifTask[]): NotifSummary;
/** `通知` at 0; `通知，N 件要處理`; `通知，N 件要處理，M 件已逾期` when M > 0. */
export function notifLabel(total: number, overdue: number): string;
/** Counted tasks per chip. */
export function chipCounts(tasks: readonly NotifTask[]): Record<NotifChip, number>;
/** `無法讀取：帳單、對帳` in SOURCE_NAMES key order; null when empty. */
export function failureLine(keys: readonly NotifSourceKey[]): string | null;
```

- [ ] **Step 1: Failing tests** (`notif-task.spec.ts`; a local `task(overrides)` factory):
  ```ts
  it('count on review data: 3 overdue + 1 today + 2 confirm + 1 system, 3 tracked → 7, red', () => {
    expect(summarize(tasks)).toEqual({ total: 7, overdue: 3, tone: 'red', label: '通知，7 件要處理，3 件已逾期' });
  });
  it('only today and confirm tasks → primary, no overdue clause', () => { expect(summarize([today, confirm])).toEqual({ total: 2, overdue: 0, tone: 'primary', label: '通知，2 件要處理' }); });
  it('a system task alone is red and adds no overdue clause', () => { expect(summarize([system])).toEqual({ total: 1, overdue: 0, tone: 'red', label: '通知，1 件要處理' }); });
  it('tracking never counts', () => { expect(summarize([tracking, tracking])).toEqual(NO_TASKS); });
  it('sortTasks: tiers in order, oldest first, ties by id', () => { expect(sortTasks([c2, o_1020, t1, o_0820, o_0820b]).map(t => t.id)).toEqual(['o_0820', 'o_0820b', 'o_1020', 't1', 'c2']); });
  it('chipCounts counts counted tasks per source', () => { expect(chipCounts([...five ledger counted, tracking])).toEqual({ all: 5, ledger: 5, portfolio: 0 }); });
  it('failureLine joins names in source order with 、', () => { expect(failureLine(['recon', 'bills'])).toBe('無法讀取：帳單、對帳'); expect(failureLine([])).toBeNull(); });
  ```
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='src/app/components/notifications/notif-task.spec.ts'` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the functions above.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): task model, order, count and label`.

### Task 2: Card bill and schedule builders (parallel with 3 and 5)

tasks.md 2.2 (card bills, schedule queue grouped by definition). R6, R7, R10, R11, P1.

**Files:**
- Create: `frontend/src/app/components/notifications/sources/bills.ts`, `bills.spec.ts`, `schedules.ts`, `schedules.spec.ts`

**Interfaces:**
- Consumes: `NotifTask` (Task 1); `BillingEvent`, `payDateOf` (`billing/billing-math.ts:13`, `:34`); `BillState` (`billing/billing.service.ts:15`); `ScheduleInstance` (`models/accounting.model.ts:1031`); `shortDate`, `daysBetween` (`accounting/dates.ts`); `formatMoney`, `formatSigned` (`accounting/format.ts`).
- Produces:
  ```ts
  export const BILL_TRACKING_DAYS = 7;
  /** One task per due event whose bill is resolved with remaining > 0 and whose payDate is ≤ today + 7. */
  export function billTasks(input: { dues: readonly BillingEvent[]; bill: (event: BillingEvent) => BillState | null; today: string }): NotifTask[];
  /** One task per definition with a pending instance due ≤ today, or with only posted-partial instances. */
  export function scheduleTasks(queue: readonly ScheduleInstance[], today: string): NotifTask[];
  ```

Copy and fields — bill (`from: 'bills'`, `source: 'ledger'`, `icon: 'pi-credit-card'`, `secondary: []`):

| Field | Value |
|---|---|
| `id` | `bill:${accountId}:${bill.due}` |
| `tier` | `payDate < today` overdue; `=== today` today; 1–7 days ahead tracking |
| `title` | `${name}帳單` (artboard 「國泰 CUBE 卡帳單」) |
| `when` | overdue `${shortDate(payDate)} 截止 · 逾期 ${n} 天` (artboard); today `今天截止` (copy: planner); tracking `${shortDate(payDate)} 截止` |
| `meta` | `帳單 ${formatMoney(statement, currency)}`, plus ` · 已繳 ${formatMoney(paid, currency)}` when `paid > 0` |
| `amount` | `formatMoney(remaining, currency)` |
| `primary` | `{ label: '記繳款', route: '/accounting/accounts/' + accountId }` |
| `sortKey` / `fingerprint` | `payDate` / `${remaining}|${payDate}` |

Schedule (`from: 'schedules'`, `source: 'ledger'`). "Open" = `status === 'pending' && due_date <= today`; `n` = open count; `overdue` = open with `due_date < today`; `oldest` = the open instance with the smallest `due_date` (then `seq`). `icon` = the oldest instance's `category_icon ?? (kind === 'installment' ? '💳' : '🔁')` (as `schedule-queue.ts:72`).

| Field | Value |
|---|---|
| `id` | `sched:${definition_id}` |
| `tier` | any overdue → overdue; else `n > 0` → today; `n === 0` and a `is_partial` instance → confirm; otherwise no task |
| `title` | `n > 1`: `${name} · ${n} 期未入帳` (spec); `n === 1` with `times`: `${name} 第 ${seq} / ${times} 期` (artboard); else `${name}` |
| `when` | overdue, `n > 1`: `最早 ${shortDate(oldest.due_date)}`; overdue, `n === 1`: `${shortDate(due)} · 逾期 ${days} 天`; today: `今天`; partial-only: `金額未入滿` |
| `meta` | `oldest.lines[0]?.account_name ?? null` |
| `amount` | per currency Σ `totals` of the open instances (of the partial ones when partial-only), each `formatSigned`, joined with `、` |
| `primary` | more than one overdue: `{ label: '全部入帳', command: { kind: 'catchUp', definitionId, name } }`; else open: `{ label: '入帳', command: { kind: 'postInstance', instanceId: oldest.id, name } }`; partial-only: `{ label: '逐期處理', route: '/accounting/reminders', queryParams: { tab: 'pending' } }` |
| `secondary` | `[{ label: '逐期處理', route: '/accounting/reminders', queryParams: { tab: 'pending' } }]`; empty when it is already the primary |
| `sortKey` / `fingerprint` | `oldest.due_date` (partial-only: that instance's `due_date`) / `${n}|${sortKey}` |

- [ ] **Step 1: Failing tests.** `bills.spec.ts` (today `2026-10-24`; `due(overrides)` builds a `BillingEvent` for 測試銀行卡, account 11; `state(overrides)` a `BillState` with statement 16639, paid 0, remaining 16639, TWD), table-driven:
  ```ts
  it.each([
    ['2026-10-20', 'overdue', '10/20 截止 · 逾期 4 天'],
    ['2026-10-24', 'today', '今天截止'],
    ['2026-10-25', 'tracking', '10/25 截止'],
    ['2026-10-31', 'tracking', '10/31 截止'],
  ])('payDate %s → %s', (payDate, tier, when) => { … expect(t).toMatchObject({ id: 'bill:11:2026-10-20', tier, when, title: '測試銀行卡帳單', amount: '$16,639', primary: { label: '記繳款', route: '/accounting/accounts/11' } }); });
  it('a bill due in 8 days is absent', …);            // payDate 2026-11-01 → []
  it('a paid bill (remaining 0) and an unresolved bill (null) are absent', …);
  it('tiers by the debit day, keeps the printed due date in the id', …); // due 2026-10-24 (Sat), payDate 2026-10-26 → tier tracking, id bill:11:2026-10-24
  it('meta names the payment only when something was paid', …);          // 帳單 $16,639 | 帳單 $16,639 · 已繳 $5,000
  it('fingerprint changes with the remaining amount and the date', …);
  ```
  `schedules.spec.ts` (`makeInstance` from `accounting/testing/fixtures.ts:232`):
  ```ts
  it('three overdue instances of one definition form one task', () => {
    const [t, ...rest] = scheduleTasks([inst(1, '2026-08-20'), inst(2, '2026-09-20'), inst(3, '2026-10-20')], '2026-10-24');   // 測試月費, −390 each
    expect(rest).toEqual([]);
    expect(t).toMatchObject({ id: 'sched:1', tier: 'overdue', title: '測試月費 · 3 期未入帳', when: '最早 08/20', amount: '−$1,170',
      primary: { label: '全部入帳', command: { kind: 'catchUp', definitionId: 1, name: '測試月費' } }, sortKey: '2026-08-20', fingerprint: '3|2026-08-20' });
    expect(t.secondary).toEqual([{ label: '逐期處理', route: '/accounting/reminders', queryParams: { tab: 'pending' } }]);
  });
  it('one overdue instance of a 12-period plan: 入帳 by instance id', …); // title 測試分期 第 5 / 12 期, when 10/10 · 逾期 14 天, command { kind: 'postInstance', instanceId: 5, name: '測試分期' }
  it('due today only → 今天 with 入帳', …);                                // 測試房租, when 今天
  it('one overdue and one due today → 已逾期, 2 期未入帳, 入帳 for the overdue one', …);
  it('future instances make no task and are not summed', …);
  it('only a posted-partial instance → 要你確認 金額未入滿 with the link 逐期處理 and no secondary', …);
  it('a partial instance beside an overdue one does not change the count or the sum', …);
  it('two currencies are joined with 、', …);                             // −$390、−US$5.00 (use formatSigned's own output in the expectation)
  it('two definitions give two tasks', …);
  ```
  Take the exact minus sign and currency symbol from `formatSigned` / `formatMoney` output (compute the expectation with the helper where the glyph matters).
- [ ] **Step 2: Run** `--include='src/app/components/notifications/sources/{bills,schedules}.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** both builders per the tables.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): card bill and schedule task builders`.

### Task 3: Statement, e-invoice, import review builders; 追蹤中 rows (parallel with 2 and 5)

tasks.md 2.2 (inbox by statement, MOZE import review, debts). R1, R2, R5, R8, R12, P2, P8.

**Files:**
- Create: `frontend/src/app/components/notifications/sources/recon.ts`, `recon.spec.ts`, `ledger-misc.ts`, `ledger-misc.spec.ts`
- Modify: `frontend/src/app/components/accounting/inbox/inbox-view.ts` (export the ask predicate the private `bucket()` already encodes), `inbox-view.spec.ts`

**Interfaces:**
- Consumes: `InboxOut`, `InboxItem`, `InboxCounts` (`inbox/inbox.model.ts:72`, `:103`, `:115`); `ReconProgress`, `reconProgress` (`statements/statement-view.ts:163`); `importReviewCount` (`accounting/import-review-count.ts:4`); `ImportRun`, `Counterparty` (`models/accounting.model.ts:845`, `:736`); `ReconciliationAction` (`inbox.model.ts:162`).
- Produces:
  ```ts
  // inbox-view.ts
  /** True for a question the owner must answer (the 待處理 bucket): not proposed / balance_gap / settlement / dividend / other. */
  export function isAskQuestion(question: InboxQuestion): boolean;
  // sources/recon.ts
  /** The one place that knows where a statement task goes (PR D changes it to /notifications/recon/:statementId). */
  export function reconTaskRoute(accountId: number, statementId: number): string;   // `/accounting/accounts/${accountId}/statements/${statementId}`
  /** Ids of the accounts that have an unfolded ask item (whose statement lists carry the progress). */
  export function askAccountIds(inbox: InboxOut): number[];
  /** One task per statement with unfolded ask items; `progress` by statement id (absent → no bar, no meta). */
  export function reconTasks(inbox: InboxOut, progress: ReadonlyMap<number, ReconProgress>): NotifTask[];
  export function einvoiceTask(counts: Pick<InboxCounts, 'einvoice_ask'>): NotifTask | null;
  // sources/ledger-misc.ts
  export function importReviewTask(run: ImportRun | null): NotifTask | null;
  export function debtsRow(counterparties: readonly Counterparty[]): NotifTrackingRow | null;
  export function upcomingRow(trackingBills: readonly NotifTask[]): NotifTrackingRow | null;
  /** P8. `now` is an ISO datetime. Returns e.g. `12 件`, `50+ 件`, or null for 0. */
  export function handledText(actions: readonly ReconciliationAction[], now: string, limit: number): string | null;
  ```

Copy and fields (all `source: 'ledger'`, `amount: null`, `secondary: []`):

| Task | `id` | tier | `title` | `when` | `meta` | `primary` | `sortKey` / `fingerprint` |
|---|---|---|---|---|---|---|---|
| Statement (`from: 'recon'`, icon `pi-check-square`) | `recon:${statement.id}` | confirm | `對帳 · ${account.name}（至 ${shortDate(period_end)}）` (copy: planner, P2) | `${n} 筆要你決定` (spec) | `已對上 ${k} / ${n} 行` (artboard) when progress is known, else null | `{ label: '對帳', route: reconTaskRoute(account.id, statement.id) }` | `period_end` / `${n}` |
| E-invoice (`from: 'recon'`, icon `pi-receipt`) | `einvoice` | confirm | `${n} 張發票要確認` (R5) | `電子發票` (copy: planner) | `到提醒中心的待處理回答` (copy: planner) | `{ label: '查看', route: '/accounting/reminders', queryParams: { tab: 'todo' } }` | `9999-12-31` (last in its section) / `${n}` |
| Import review (`from: 'importReview'`, icon `pi-upload`) | `import:${run.id ?? run.started_at}` | confirm | `匯入紀錄 · ${n} 筆待確認` (copy: planner) | `started_at` as local `MM/DD HH:mm` | `run.file_name` | `{ label: '查看', route: '/accounting/accounts' }` | `started_at` / `${n}` |

追蹤中 rows:

| Row | `title` | `meta` | `count` | `link` |
|---|---|---|---|---|
| `debts` | `借還款 · ${people} 位` (artboard) | `應收 ${amounts}（${names}）· 應付 ${amounts}（${names}）` — a side with nothing is left out; amounts per currency `formatMoney(|Σ|)` joined with `、`; names: the first two, then `等 ${k} 位` (artboard 「應收 $1,550（陳小明、阿傑）· 應付 $5,000（媽媽）」) | people | `{ label: '查看', route: '/accounting/reminders', queryParams: { tab: 'debts' } }` |
| `upcoming` | `即將到來 · ${n} 件` (artboard) | the first three as `${title} ${shortDate(sortKey)}` joined with ` · `, then ` · 等 ${k} 件` | n | `{ label: '查看', route: '/accounting/reminders', queryParams: { tab: 'cards' } }` |

A person is a counterparty with `open_count > 0`. Each `open_amounts` item with a positive amount is 應收 (owed to the owner), a negative one 應付 (the API sends `−Σ amount`, `settings_service.py:1122`); the figure is the person's net per currency, as the API gives it.

- [ ] **Step 1: Failing tests.** `inbox-view.spec.ts`: `isAskQuestion: bank_only, transfer, ledger_only, amount_differs, date_differs, ambiguous yes; proposed, balance_gap, settlement, dividend, other no`.
  `recon.spec.ts` (`makeInbox`, `makeInboxAccount`, `makeItem` from `inbox/testing.ts`):
  ```ts
  it('ten ask items of one statement form one task', () => {
    const [t] = reconTasks(inboxWith(10 items, statement { id: 31, period_end: '2026-10-05' }, account { id: 11, name: '測試銀行卡' }), new Map([[31, { k: 17, n: 28 }]]));
    expect(t).toMatchObject({ id: 'recon:31', tier: 'confirm', title: '對帳 · 測試銀行卡（至 10/05）', when: '10 筆要你決定', meta: '已對上 17 / 28 行',
      progress: { k: 17, n: 28 }, primary: { label: '對帳', route: '/accounting/accounts/11/statements/31' }, fingerprint: '10' });
  });
  it('counts items, not cards: three items sharing a group_key count 3', …);
  it('folded items and proposed / balance_gap / settlement / dividend / other items make no task', …);
  it('two statements of one account give two tasks, oldest period first after sortTasks', …);
  it('without progress the task has no bar and no meta', …);
  it('askAccountIds lists each account once, only those with an unfolded ask item', …);
  it('einvoiceTask: 3 → 「3 張發票要確認」 linking to the reminders 待處理 tab; 0 or absent → null', …);
  ```
  `ledger-misc.spec.ts`:
  ```ts
  it('importReviewTask: needs_review 4 → 匯入紀錄 · 4 筆待確認 → /accounting/accounts; 0, null run → null', …);
  it('debtsRow: 甲 +1000, 乙 +550, 丙 −5000 → 借還款 · 3 位 / 應收 $1,550（甲、乙）· 應付 $5,000（丙）', …);
  it('debtsRow: three people on one side → 「甲、乙 等 1 位」', …);
  it('debtsRow: open_count 0 or missing is not a person; nobody → null', …);
  it('debtsRow: only 應付 leaves 應收 out', …);
  it('upcomingRow: two tracking bills → 即將到來 · 2 件 / 測試銀行卡帳單 10/27 · 測試二卡帳單 10/30', …);
  it('upcomingRow: four → first three, then 等 1 件; none → null', …);
  it('handledText: counts actions of the last 7 days that were not reverted', …);   // now 2026-10-24T12:00:00Z: 10-18T09:00 in, 10-17T11:59 out, reverted out → '1 件'
  it('handledText: a full page inside the window reads 50+ 件; none → null', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/notifications/sources/{recon,ledger-misc}.spec.ts'` and `--include='src/app/components/accounting/inbox/inbox-view.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** `bucket()` in `inbox-view.ts` uses `isAskQuestion` for its default branch (one definition).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): statement, e-invoice and import review tasks; debts and upcoming rows`.

### Task 4: `NotificationCenterService`; `NavBadgeService` becomes a facade

tasks.md 2.1 (per-source failure, refresh triggers, 30 s throttle, fold), 2.4 (dots). R9, R13, R17, R18, R21, R23, R24, P7. After Tasks 2, 3 and 5.

**Files:**
- Create: `frontend/src/app/services/notification-center.service.ts`, `notification-center.service.spec.ts`
- Modify: `frontend/src/app/services/nav-badge.service.ts` (facade), `nav-badge.service.spec.ts` (cases move; three facade cases stay)
- Modify (one line per stub, `summary: signal(NO_TASKS)` beside `badges`): `app.routes.spec.ts`, `components/accounting/reports/reports.spec.ts`, `components/accounting/reward-rules/reward-rules.spec.ts`, `components/mobile-nav/mobile-nav.spec.ts`, `components/shell/navigation-state.service.spec.ts`, `components/shell/page-header/page-header.spec.ts`, `components/shell/shell.spec.ts`, `components/shell/sub-nav/sub-nav.spec.ts`, `components/sidebar/sidebar.spec.ts`
- Modify: `components/shell/navigation-state.service.ts` (`notifications` signal), `navigation-state.service.spec.ts`

**Interfaces:**
- Consumes: Tasks 1–3; `NavBadge` with `tone` (Task 5); `BillingService.ensure / refresh / bill / failed / generation` (`billing.service.ts:101`, `:121`, `:189`, `:194`, `:94`); `reminderDues` (`billing-math.ts:163`); `StatementsService.list` (`statements.service.ts:23`); `InboxService.get` / `actions` (`inbox.service.ts:24`, `:53`); `AccountingService.getAccounts / getCounterparties / getScheduleInstances / getLatestImport` (`accounting.service.ts:118`, `:395`, `:485`, `:542`); `PortfolioService.getRecalcStatus`; `ImportHistoryService.lastFailed`.
- Produces:
  ```ts
  export const BADGE_REFRESH_MIN_MS = 30_000;                       // moves here; nav-badge.service.ts re-exports it
  @Injectable({ providedIn: 'root' })
  export class NotificationCenterService {
    readonly tasks: Signal<readonly NotifTask[]>;                   // counted tasks, sortTasks order
    readonly trackingRows: Signal<readonly NotifTrackingRow[]>;     // debts row, then upcoming row
    readonly nextUpcoming: Signal<NotifTask | null>;                // nearest tracking bill (empty state)
    readonly summary: Signal<NotifSummary>;                         // not gated by the badges setting
    readonly failedSources: Signal<readonly NotifSourceKey[]>;      // SOURCE_NAMES key order
    readonly handled: Signal<string | null>;                        // handledText(); null hides the row
    readonly loading: Signal<boolean>;                              // any source read in flight
    readonly loaded: Signal<boolean>;                               // every task source has answered once (ok or failed)
    readonly badges: Signal<Readonly<Record<string, NavBadge>>>;
    start(): void;                                                  // idempotent; first load + focus / visibilitychange listeners
    /** Throttled to one per BADGE_REFRESH_MIN_MS. `force`: skip the throttle unless a read is in flight. */
    refresh(options?: { readonly force?: boolean }): void;
  }
  // nav-badge.service.ts
  @Injectable({ providedIn: 'root' })
  export class NavBadgeService { readonly badges; readonly summary; start(): void; refresh(): void; }   // each delegates
  // navigation-state.service.ts
  readonly notifications: Signal<NotifSummary>;   // prefs.showBadges() ? navBadges.summary() : NO_TASKS
  ```

Sources (each its own generation counter and state `idle | ok | failed`; a later-started read of a source wins, as `nav-badge.service.ts:208–215`):

| Read | Trigger | Feeds | Failure |
|---|---|---|---|
| `getAccounts()` | refresh, write | dues → `BillingService` → `billTasks`; 帳戶 dot | `bills` failed |
| each due's statement (`BillingService`) | as `nav-badge.service.ts:105–119` (ensure per change round; `refresh` once after an accepted refresh) | bill tasks | `bills.failed(event)` for any due → `bills` failed; that card has no task, the others keep theirs |
| `getScheduleInstances({ queue: true })` | refresh, write | `scheduleTasks` | `schedules` failed |
| `inbox.get()` (no `limit`) | refresh, write | `reconTasks`, `einvoiceTask` | `recon` failed (both tasks leave) |
| `statements.list(id)` per `askAccountIds` | when the inbox answer is applied; dropped if a newer inbox read started | progress map (`reconProgress`) | silent: that statement has no bar |
| `getLatestImport()` | refresh, write | `importReviewTask`; 記帳記錄 dot | `importReview` failed (a 404 is already `null` = ok) |
| `getCounterparties()` | refresh, write | `debtsRow` | `debts` failed |
| `inbox.actions(50)` | refresh, write | `handled` | silent: `handled` null |
| `getRecalcStatus()` | refresh only | 匯入 dot | silent: dot only from `lastFailed()` |

"write" = `entriesChanged` / `accountsChanged` after `start()`, not throttled, skipping the effect's first run (`nav-badge.service.ts:121–134`). The business-day calendar is asked for lazily on the first load (`:171–175`). No read raises a toast.

Badges: `accounting` `{ dot: true, tone: 'red', label: '有逾期的卡費' }` when a bill task is 已逾期; `accounting-timeline` `{ dot: true, tone: 'primary', label: '有待確認的匯入紀錄' }` when `importReviewTask` is non-null; `import` `{ dot: true, tone: 'red', label: '最近一次匯入失敗' }` under today's rule (`:93–98`). No `count` on any badge.

- [ ] **Step 1: Failing tests.** `notification-center.service.spec.ts`, modelled on `nav-badge.service.spec.ts` (a `Subject` per call through `calls<T>()`, stubbed `AccountingService` / `PortfolioService` / `InboxService` / `StatementsService` / `BillingService`-through-real-service as that spec does, fake timers, today `2026-10-24`). Carried over with the same bodies, re-pointed at the new service (old line in brackets): import dot cases (144–190), `refresh within 30 s is skipped, after 30 s refires` (233), `start() twice registers one focus listener` (248), `an entry or account write reloads … not the recalc status` (261), `writes before start() load nothing` (287), the visibilitychange block (295–336), `removes the focus listener` (337), `older response landing after a newer one is dropped` (345), `an older success landing after a newer failure is dropped` (355), `slow first load across the 30 s window` (365), `stale accounts snapshot never reaches the statements` (382), `re-asks the statements with the debit day once the business-day calendar lands` (400), the card-statement refresh block (469–520, asserting the bill task instead of a count). New:
  ```ts
  it('three overdue instances of one definition count once', …);                    // summary().total 1; tasks()[0].title '測試月費 · 3 期未入帳'
  it('count on review data: 7, red, 「通知，7 件要處理，3 件已逾期」; three people owing do not count', …);
  it('reads every source once per refresh', …);                                     // each stub called exactly once; inbox.get called with no limit
  it('a failed inbox read: no statement task, no e-invoice task, failedSources [recon], the other tasks stay', …);
  it('a failed queue read names 週期 and keeps the bills', …);
  it('a failed accounts read names 帳單 and hides the 帳戶 dot', …);
  it('one card whose statement read failed: that card has no task, the other card keeps its task, 帳單 is named', …);
  it('a failed counterparties read names 借還款 and changes no count', …);
  it('a failed latest-import read names 匯入檢查 and hides the 記帳記錄 dot; a 404 (null) names nothing', …);
  it('a source that fails after it succeeded drops its tasks', …);
  it('an older inbox answer landing after a newer one does not bring its statement back', …);
  it('statement progress: one statements read per account with ask items; a failed one leaves the task without progress', …);
  it('a statements answer for a superseded inbox read is dropped', …);
  it('refresh({ force: true }) inside the 30 s window reads again; while a read is in flight it does nothing', …);
  it('loading is true from the first read until the last answer; loaded turns true once every task source answered', …);
  it('badges: overdue bill → accounting red dot 有逾期的卡費; a bill due today → no dot', …);
  it('badges: review count 2 → accounting-timeline primary dot 有待確認的匯入紀錄; no badge carries a count', …);
  it('handled: 「3 件」 from the actions read; null when it fails', …);
  it('nextUpcoming is the tracking bill with the earliest date; trackingRows = [debts, upcoming]', …);
  it('hidden groups change nothing', …);                                            // prefs hiddenGroups ['accounting'] → same summary
  ```
  `nav-badge.service.spec.ts` shrinks to: `badges and summary are the centre's signals`, `start() and refresh() delegate` (a stubbed `NotificationCenterService`). The old cases `counts the reminders…` (213), `a failing source hides only its own badge` (220), `counts 待處理 inbox cards` (426), `counts e-invoice questions` (435), `a failed inbox read counts 0` (443), `timeline and badge agree` (451), `latest import with 1 needs_review → accounting count` (192–210) are deleted; their replacements are the new cases above.
  `navigation-state.service.spec.ts`: `notifications follows the facade's summary`; `badges off → NO_TASKS and no badges`.
- [ ] **Step 2: Run** `--include='src/app/services/{notification-center,nav-badge}.service.spec.ts'` and `--include='src/app/components/shell/navigation-state.service.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** Move the body of `NavBadgeService` into the new service and split its `forkJoin` (`nav-badge.service.ts:178–187`) into the per-source reads of the table; `countReminders` is no longer imported by either service (it stays for the reminders page). Add the stub line in the nine spec files.
- [ ] **Step 4: Run** the three commands, then `--include='src/app/components/{shell,sidebar,mobile-nav}/**/*.spec.ts'` and `--include='src/app/app.routes.spec.ts'` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): NotificationCenterService reads each ledger source on its own; NavBadgeService delegates`.

### Task 5: `/notifications` in the nav model; dots with a tone (parallel with 2 and 3)

tasks.md 2.4 (dots, D8). R9, R15.

**Files:**
- Modify: `frontend/src/app/components/shell/navigation.ts`, `navigation.spec.ts`
- Modify: `components/shell/navigation-state.service.ts` (`activeGroup`, `:46–48`)
- Modify: `components/sidebar/sidebar.html` (`:56–71`, `:97–111`), `sidebar.scss` (`:183`, `:198`, `:283`), `sidebar.spec.ts`
- Modify: `components/shell/sub-nav/sub-nav.html` (`:13–17`), `sub-nav.scss` (`:71`, `:82`), `sub-nav.spec.ts`
- Modify: `components/mobile-nav/mobile-nav.html` (`:17–20`), `mobile-nav.ts` (`:21–32`), `mobile-nav.scss` (`:60`), `mobile-nav.spec.ts`
- Modify: `components/shell/page-header/page-header.ts` (`backFallback` input; `goBack`, `:196–202`), `page-header.spec.ts`
- Modify: `frontend/src/app/services/nav-badge.service.ts` (`:86`, `:91`: the two counts become dots, see Step 3), `nav-badge.service.spec.ts`

**Interfaces (produced):**
```ts
// navigation.ts
export type NavGroupId = 'supplies' | 'portfolio' | 'accounting' | 'system' | 'notifications';
export const NOTIFICATIONS_ITEM: NavItem;   // { id: 'notifications', path: '/notifications', icon: 'pi-bell', label: '通知', sidebarLabel: '通知', title: '通知', group: 'notifications' }
export const NOTIFICATIONS_GROUP: NavGroup; // { id: 'notifications', label: '通知', icon: 'pi-bell', defaultPath: '/notifications', items: [NOTIFICATIONS_ITEM] } — NOT in NAV_GROUPS / NAV_ITEMS
export interface NavBadge { dot?: boolean; tone?: 'red' | 'primary'; label: string; }          // `count` is removed
export interface GroupBadgeSummary { dot: boolean; tone: 'red' | 'primary'; }                  // red when any item's dot is red; `count` is removed
// page-header.ts
readonly backFallback = input<string | null>(null);   // goBack(): history back when the app has history, else backFallback() ?? nav.landingPath()
```
`navItemForUrl` returns `NOTIFICATIONS_ITEM` for `/notifications` and anything under it (checked first). `NavigationStateService.activeGroup` looks the group up in `[...NAV_GROUPS, NOTIFICATIONS_GROUP]`. With that: no sidebar group head is `current`, `SubNavComponent.show` is false (one item, `sub-nav.ts:19`), `MobileNavComponent.highlighted` matches no tab, and `hasDot` shows every group's dot. `NavigationPreferencesService` validates hidden groups against `NAV_GROUPS` (`navigation-preferences.service.ts:14`), so 通知 cannot be hidden.

Markup: every `itemBadge?.count` / `sum.count` branch is removed. A dot is `<span class="item-dot" [class.item-dot--primary]="itemBadge.tone === 'primary'" aria-hidden="true">` (likewise `group-dot`, `sub-dot`, `tab-dot`); red is the default. Styles: red dots `background: var(--c-red-ink)` (today `--c-red`), primary dots `var(--app-primary)`; the `.item-count`, `.group-count` and `.sub-count` rules are deleted. Accessible names are unchanged in form: `${sidebarLabel}，${badge.label}` (`sidebar.ts:43–46`), the folded group's `sr-only` list (`sidebar.html:63–71`), the tab's 「，有待處理項目」 (`mobile-nav.html:19`).

- [ ] **Step 1: Failing tests.**
  - `navigation.spec.ts`: `navItemForUrl('/notifications')`, `('/notifications?x=1')`, `('/notifications/recon/5')` → `NOTIFICATIONS_ITEM`; `('/nowhere')` still → 庫存; `NAV_ITEMS` does not contain it; `summarizeGroupBadges: a red and a primary dot → { dot: true, tone: 'red' }; only primary → primary; none → { dot: false, tone: 'primary' }`.
  - `navigation-state.service.spec.ts`: `on /notifications activeItem is 通知 and activeGroup is no visible group`.
  - `sidebar.spec.ts`: replace the `提醒中心 2 項` fixtures (`:115`, `:159`, `:229`) with `'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' }` and `'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' }`; `:135` → `expect(label('/accounting')).toBe('記帳記錄，有待確認的匯入紀錄')`; new `no nav item renders a count (expanded and rail)` (`.item-count` null); `a primary dot carries item-dot--primary, a red one does not`; `a folded group shows one dot, red when any of its items is red`.
  - `sub-nav.spec.ts`: `帳戶 shows a dot and is named 帳戶，有逾期的卡費; no .sub-count`; `no sub-nav on /notifications`.
  - `mobile-nav.spec.ts`: `on /notifications no tab is highlighted and no tab has aria-current`; `a non-active group with a dot shows tab-dot; primary-only → tab-dot--primary`; `the active group shows none`.
  - `page-header.spec.ts`: `backFallback: without app history ‹ navigates to it`; `without backFallback the landing path is used (unchanged)`.
  - Every `count:` in a `NavBadge` fixture becomes `dot: true` (`navigation.spec.ts:60–62`, `sub-nav.spec.ts:51`, `:72`, `mobile-nav.spec.ts:15`, `navigation-state.service.spec.ts:17`, `sidebar.spec.ts` as above); `app.routes.spec.ts` is not touched here (Task 8 adds its rows).
  - scss assertions (read the three files as PR A's `toast-outlet.spec` does): contain `var(--c-red-ink)` and `var(--app-primary)` for the dots and no `.item-count` / `.sub-count` / `.group-count` rule.
- [ ] **Step 2: Run** `--include='src/app/components/{shell,sidebar,mobile-nav}/**/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** `nav-badge.service.ts` still writes `count` at `:86` and `:91` until Task 4: in this task change those two badges to `{ dot: true, label }` so the build stays green (Task 4 replaces them).
- [ ] **Step 4: Run** the same command and `--include='src/app/services/nav-badge.service.spec.ts'` (update its two `count` expectations to `dot`) — Expected: PASS.
- [ ] **Step 5: Commit** `feat(shell): /notifications resolves to 通知; nav items show dots only, red or primary`.

### Task 6: Entry points — sidebar 通知, phone header bell, timeline link (parallel with 7)

tasks.md 2.4. D1, R14, P5. After Tasks 4 and 5.

**Files:**
- Modify: `frontend/src/styles.scss` (`--app-count-ink` in both theme blocks; global `.notif-count`), `frontend/src/app/theme/contrast.spec.ts`
- Modify: `components/sidebar/sidebar.html` (after the 記一筆 link `:27–40`, before the groups loop `:41`), `sidebar.ts` (`placeTip` selector already matches `a.side-item`), `sidebar.scss`, `sidebar.spec.ts`
- Modify: `components/shell/page-header/page-header.html` (before the gear, `:37–39`), `page-header.ts`, `page-header.scss`, `page-header.spec.ts`
- Modify: `components/accounting/timeline/timeline.html` (`:14`), `timeline.ts`, `timeline.scss` (`:343–374`), `timeline.spec.ts`

**Interfaces:**
- Consumes: `NavigationStateService.notifications: Signal<NotifSummary>` (Task 4); `NOTIFICATIONS_ITEM` (Task 5).
- Produces (markup contracts later probes use):
  - Global `.notif-count` (`styles.scss`): 16 px high, min-width 16 px, padding 0 4 px, radius pill, 10 px / 800, `color: var(--app-count-ink)`, `background: var(--app-primary)`; `.notif-count--red { background: var(--c-red-ink) }`. Tokens: `:root { --app-count-ink: #fff }`, `.app-dark-mode { --app-count-ink: #0f1218 }`.
  - Sidebar: `<a class="side-item side-notif" routerLink="/notifications" [attr.aria-label]="nav.notifications().label" [attr.aria-current]="…">` with `pi pi-bell`; rendered at every width the sidebar exists and whatever groups are hidden. Expanded: label 通知 then the pill; rail: the pill at the icon's top-right and the tip `通知` + `<small>` with the label's part after 「通知，」 when the count is above 0. The pill (`aria-hidden`) renders only when `total > 0`.
  - Page header: `protected readonly bell = computed(() => this.phone() && this.nav.activeItem().id !== 'notifications')`; `<a class="header-bell" routerLink="/notifications" [attr.aria-label]="nav.notifications().label">` (44 × 44 target, `pi pi-bell`, the pill when `total > 0`), placed after the ⋯ menu and before the gear.
  - Timeline: `@if (!isPhone()) { <a class="bell" routerLink="/notifications" aria-label="通知"><i class="pi pi-bell" aria-hidden="true"></i></a> }` — no count. `.bell-count` styles are deleted.
- Timeline removals: `reminderCount` (`timeline.ts:722–731`), `counterparties` (`:491–492`), `counterpartyRequestId` (`:493`), `inboxAsk` (`:500`), `inboxRequestId` (`:501`), `loadInboxCount` (`:1507–1517`), `loadCounterparties` (`:1520–1532`), `openReminders` (`:1534–1536`), the `InboxService` injection (`:485`), the `countReminders` / `inboxBellCount` / `InboxService` imports (`:44`, `:55`, `:56`); the write effect at `:863–871` keeps `loadQueue()` only (the queue also feeds the overview's due list).

Labels: `通知` (count 0 or badges off); `通知，7 件要處理，3 件已逾期`; `通知，2 件要處理`.

**Known gap (R14) — phone pages without the bell** (they draw their own header, not `PageHeaderComponent`): 帳戶明細 (`account-entries`), 帳戶設定 (`account-settings`), 記一筆 / 編輯 (`entry-form`), 提醒中心 (`reminders`), 記錄明細 (`entry-detail`), 多類別群組 (`split-group`), 自訂報表 (`custom-report`), 回饋規則 (`reward-rules`), 對帳帳單 and 對帳明細 (`statement-list`, `statement-detail`). All are pushed screens one tap from a page that has the bell. No header is added in B.

- [ ] **Step 1: Failing tests.**
  - `contrast.spec.ts`: `describe('notification count pill')` — both themes: `--app-count-ink` on `--c-red-ink` ≥ 4.5 and on `--app-primary` ≥ 4.5 (computed: light 5.2 / 6.2, dark 6.5 / 5.8).
  - `sidebar.spec.ts` (stub `summary` signal): `通知 sits above the first group, after 記一筆, and links to /notifications` (expanded 1440 and rail); `shows 7 in the red pill with the label 通知，7 件要處理，3 件已逾期`; `primary pill when nothing is overdue`; `no pill at 0; the label is 通知`; `badges off: no pill, label 通知` (via `NavigationPreferencesService.setShowBadges(false)`); `hiding 財務 and 投資 keeps 通知`; `aria-current="page" on /notifications and no group head is current`; `rail: the tip reads 通知 and 7 件要處理，3 件已逾期`.
  - `page-header.spec.ts`: `phone: a bell before the gear links to /notifications with the count label` (DOM order `.header-bell` before `.header-gear`, with `settingsInHeader`); `phone without the gear still shows the bell`; `no bell at 820 or 1440`; `no bell on /notifications`; `badges off: bell without a pill, label 通知`.
  - `timeline.spec.ts`: `flushCounterparties` (defined `:112`; called at `:88`, `:175`, `:606`, `:1184`, `:1462`, `:1586`, `:1615`, `:1832`, `:2719`) stops expecting the counterparties and inbox requests (rename it to what it still flushes); the bell block (`:2798–2940`) is replaced by: `the month bar links to /notifications with the label 通知 and no count` (1440), `no month-bar bell below 760 px` (390), `the timeline reads neither the counterparties nor the reconciliation inbox` (`http.expectNone` on both URLs after init and after `entriesChanged`), `a write still reloads the queue`.
- [ ] **Step 2: Run** `--include='src/app/components/sidebar/*.spec.ts'`, `--include='src/app/components/shell/page-header/*.spec.ts'`, `--include='src/app/components/accounting/timeline/*.spec.ts'`, `--include='src/app/theme/contrast.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the four commands — Expected: PASS. `grep -n "reminderCount\|openReminders\|bell-count" frontend/src/app/components/accounting/timeline/` is empty.
- [ ] **Step 5: Commit** `feat(shell): 通知 in the sidebar and the phone header carry the task count; the timeline bell is a link`.

### Task 7: One row (parallel with 6)

tasks.md 2.3 (rows). P12.

**Files:**
- Create: `frontend/src/app/components/notifications/notif-row/notif-row.ts`, `notif-row.html`, `notif-row.scss`, `notif-row.spec.ts`

**Interfaces:**
- Consumes: `NotifTask`, `isCommand`, `SOURCE_TAGS` (Task 1); `app-btn` (`components/ui/btn/btn.ts`: `variant`, `loading`, `disabled`).
- Produces: `NotifRowComponent`, selector `app-notif-row`, host `class="notif-row"`, `[attr.data-task-id]`, `[class.notif-row--alert]` (tier overdue or system). Inputs `task = input.required<NotifTask>()`, `busy = input(false)`, `solid = input(false)` (phone 已逾期: solid primary). Output `run = output<NotifCommand>()`.

DOM (tab order = DOM order: primary, then secondary links):
`span.notif-icon` (40 × 40 tile, `aria-hidden`; a `pi` class renders `<i>`, anything else as text) · `div.notif-body` > `div.notif-head` (`span.notif-title`, `span.notif-tag` = 財務 / 投資, `span.notif-amount` when set) + `div.notif-line` (`b.notif-when`, `span.notif-meta` prefixed by ` · ` when both exist) + `div.notif-bar[aria-hidden="true"]` > `span` with `width: k / n` (only with `progress`, `n > 0`; 6 px high, max 260 px) · `div.notif-actions` > primary + `a.notif-secondary` per secondary link.
Primary: a command → `<app-btn class="notif-primary" [variant]="solid() ? 'primary' : 'soft'" [loading]="busy()" [disabled]="busy()" (click)="run.emit(command)">`; a link → `<a class="notif-primary notif-primary--link" [class.solid]="solid()" [routerLink] [queryParams]>` styled as the button. Each action's accessible name is `${label}：${title}` (`aria-label`), so 入帳 buttons differ by task.
Layout: ≥ 760 px one line (icon, body, amount, actions at the right; `min-height: 68px`); below 760 px the amount stays on the title line and the actions wrap under the text (artboard NotifPhone). Tokens: tile `--app-primary-soft`, alert tile `--c-red-soft` with `--c-red-ink` icon; `when` in an alert row `--c-red-ink`; meta `--app-text-muted`; tag `--app-surface-soft` / `--app-text-muted`; bar track `--app-surface-soft`, fill `--app-primary`; title 15 px / 700 with ellipsis, line 2 12 px.

- [ ] **Step 1: Failing tests** (`notif-row.spec.ts`, a host component with `provideRouter([])`):
  - `renders title, source tag, when, meta and amount` (schedule task: `測試月費 · 3 期未入帳`, `財務`, `最早 08/20`, ` · 測試銀行卡`, the amount);
  - `an overdue row marks itself and shows when in the alert tone` (`.notif-row--alert`);
  - `a command primary is a button named 「全部入帳：測試月費 · 3 期未入帳」 and emits its command once`;
  - `busy: the button is disabled and emits nothing`;
  - `a link primary is an anchor to /accounting/accounts/11 named 「記繳款：測試銀行卡帳單」`;
  - `secondary links follow the primary in the DOM with their query params` (`a.notif-secondary[href="/accounting/reminders?tab=pending"]`);
  - `progress 17 / 28 renders a bar 60.7% wide, hidden from assistive tech; no bar without progress`;
  - `no amount element when amount is null`;
  - `solid: the command button uses the primary variant; the link gets .solid`;
  - `an emoji icon renders as text, a pi icon as <i>`;
  - `scss: no colour literal` (read the file; no `#hex` / `rgb(`).
- [ ] **Step 2: Run** `--include='src/app/components/notifications/notif-row/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): task row with one primary action`.

### Task 8: The page — route, header, chips, sections, 追蹤中, links

tasks.md 2.3. D1, R15, R17, R19, R20, R22, P3, P9. After Tasks 4, 5, 7.

**Files:**
- Create: `frontend/src/app/components/notifications/notifications.ts`, `notifications.html`, `notifications.scss`, `notifications.spec.ts`
- Modify: `frontend/src/app/app.routes.ts` (one route after `settings`, `:89`), `app.routes.spec.ts`

**Interfaces:**
- Consumes: `NotificationCenterService` (Task 4), `app-notif-row` (Task 7), `app-page-header` with `back` / `backFallback` / `subtitle` (Task 5), `LayoutModeService.mode()`.
- Produces: `NotificationsComponent` (selector `app-notifications`); route `{ path: 'notifications', loadComponent: () => import('./components/notifications/notifications').then(m => m.NotificationsComponent) }`; `protected readonly chip = signal<NotifChip>('all')`; `protected readonly sections: Signal<{ tier: NotifTier; label: string; tasks: NotifTask[] }[]>` (counted tiers that have a task under the chosen chip); `protected onRun(task: NotifTask, command: NotifCommand): void` — a no-op stub here, implemented in Task 9.

Structure and copy (top to bottom; one column, `max-width: 720px`, `padding-bottom: 96px` from 760 px so the last row clears the floating 記一筆):

| Element | Markup | Copy |
|---|---|---|
| Header | `<app-page-header [back]="true" backFallback="/accounting" [subtitle]="subtitle()">` (h1 from the nav item) | h1 `通知`. Subtitle ≥ 760 px: `7 件要處理 · 3 件已逾期 · 數字只算要你動手的事` (artboard); below 760 px: `7 件要處理 · 3 件已逾期` (artboard); the 已逾期 part only when M > 0; at 0: `沒有要處理的事` (copy: planner) |
| Chips | `div.notif-chips[role="radiogroup"][aria-label="來源"]` > `button[role="radio"][aria-checked]` (36 px pills, the pattern of `reminders.html:9–13`) | `全部` / `財務` / `投資`, each followed by `span.chip-count` with its count (0 shown as `0`) |
| Sections | `section.notif-section[aria-labelledby]` > `h2` (`span.sec-dot` + label + `span.sec-count`) + `ul.notif-card` > `li` > `<app-notif-row [task] [busy]="isBusy(task.id)" [solid]="phone() && section.tier === 'overdue'" (run)="onRun(task, $event)">` | `已逾期`, `今天`, `要你確認`, `系統`; `sec-dot` red (`--c-red-ink`) for 已逾期 and 系統, `--app-text-muted` otherwise |
| Empty chip | `p.notif-none` when the chosen chip has no counted task but 全部 has | `這個來源目前沒有要處理的事` (copy: planner) |
| 追蹤中 | `details.notif-tracking` > `summary` (`追蹤中 · 不計入數字` + `span.tracking-sum`) + one `div.tracking-row` per row: title, meta, `a.tracking-link`. Hidden under 投資 and when there is no row | summary right side `借還款 3 位 · 即將到來 3 件` (artboard; a part is left out when its row is absent); link `查看` named `查看：借還款 · 3 位` |
| Handled | `a.notif-handled[routerLink="/accounting/reminders"][queryParams]="{ tab: 'todo' }"` when `center.handled()` is non-null | `已處理（近 7 天）12 件 ›` (artboard) |
| 提醒中心 | `a.notif-reminders[routerLink="/accounting/reminders"]` (muted) | `提醒中心（借還款、週期與分期管理）` (R22) |

On init: `center.refresh({ force: true })` (R17). The chip is not kept in the URL (P9). Loading, empty and failure states are Task 10.

- [ ] **Step 1: Failing tests** (`notifications.spec.ts`; `NotificationCenterService` stubbed with writable signals and a `refresh` spy; `provideRouter`; the `matchMedia` viewport stub of `app.routes.spec.ts:43–52`):
  - `opening the page forces a refresh` (`refresh` called once with `{ force: true }`);
  - `sections render in the order 已逾期, 今天, 要你確認, 系統 with their counts; a tier without tasks has no section`;
  - `rows keep the service's order inside a section`;
  - `subtitle: 「7 件要處理 · 3 件已逾期 · 數字只算要你動手的事」 at 1440; without the last part at 390; no 已逾期 part when none is overdue; 「沒有要處理的事」 at 0`;
  - `chips: 全部 5 / 財務 5 / 投資 0, 全部 checked`; `picking 投資 leaves no row, shows 這個來源目前沒有要處理的事 and hides 追蹤中`; `picking 財務 keeps the ledger rows`; `chip counts never include tracking rows`;
  - `追蹤中 is folded by default and lists 借還款 · 3 位 and 即將到來 · 2 件 with 查看 links to ?tab=debts and ?tab=cards`; `summary reads 追蹤中 · 不計入數字 and 借還款 3 位 · 即將到來 2 件`; `no 追蹤中 block without rows`;
  - `handled link: 已處理（近 7 天）3 件 › → /accounting/reminders?tab=todo; absent when handled is null`;
  - `the 提醒中心 link is always there`;
  - `phone: rows of 已逾期 are solid, others soft; ≥ 760 px none is solid`;
  - `badges off: the page still lists every task, the subtitle and the chip counts stay` (P3);
  - `one column: no pane host, no router-outlet`.
  `app.routes.spec.ts`: `LIST_LEVEL` (`:176`) gains `['/notifications', '通知']`; the highlight cases (`:425`) gain `/notifications → no tab, no sidebar group current, 通知 item aria-current`; `at 390 /notifications shows ‹ and no bell`.
- [ ] **Step 2: Run** `--include='src/app/components/notifications/notifications.spec.ts'` and `--include='src/app/app.routes.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.** Section title, chip and link colours through tokens (`--app-text`, `--app-text-muted`, `--app-surface`, `--app-surface-soft`, `--app-border`; the checked chip is `--app-accent` with `--app-surface` text, as the artboard's inverted pill).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): /notifications page with sections, source chips, 追蹤中 and links`.

### Task 9: 入帳 and 全部入帳 in place

tasks.md 2.3. R4, P6. PR A lessons: one toast, captured request, late results.

**Files:**
- Modify: `frontend/src/app/components/notifications/notifications.ts`, `notifications.html`, `notifications.spec.ts`

**Interfaces:**
- Consumes: `AccountingService.postScheduleInstance(id)` (`accounting.service.ts:493`), `catchUpSchedule(id): Observable<ScheduleCatchUpResult>` (`:481`; `{ posted: number[]; failed: { instance_id; error } | null }`); `injectToasts()` (`services/toast.service.ts:33`), `toastText`; `guardedRetry` (`services/toast-retry.ts:9`); `writeErrorMessage` (`accounting/http-errors.ts:60`); `isImportRunning`, `IMPORT_RUNNING_TOAST` (`accounting/schedule-math.ts`).
- Produces: `protected onRun(task: NotifTask, command: NotifCommand): void` (ignored while `isBusy(task.id)`); `private send(taskId: string, command: NotifCommand): void`; `protected isBusy(id: string): boolean`; `private readonly busy = signal<ReadonlySet<string>>(new Set())`.

Rules:
1. `send` adds `taskId` to `busy` and subscribes without `takeUntilDestroyed` (the write must finish; the toast scope handles a destroyed page).
2. Success: the toast below; the id stays busy until `center.loading()` next turns false after the write (both calls bump `entriesChanged`, which makes the service re-read at once — the page calls no refresh on success).
3. Failure: the id leaves `busy` at once; the toast below; then `center.refresh({ force: true })` (silent: it raises nothing, so the 失敗 toast stays).
4. 重試 calls `send(taskId, command)` with the captured values; while that id is busy `guardedRetry` re-raises the toast instead.
5. When a succeeded task's row has left and `document.activeElement` is `<body>`, focus goes to the `.notif-primary` of the row now at that position in the DOM, else to `details.notif-tracking > summary`, else to the first chip (`afterNextRender`; focus the owner moved elsewhere is left alone — the pattern of `accounting/retry-focus.ts`).

Toasts (all through `this.toasts`; `name` = the command's `name`):

| Case | Call |
|---|---|
| 入帳 ok | `done('已入帳：' + name)` (copy: planner) |
| 全部入帳 ok, `failed === null` | `done('已入帳 ' + posted.length + ' 期：' + name)` (copy: planner) |
| 全部入帳 ok with `failed` | `error(name + '：已入帳 ' + posted.length + ' 期，之後的入帳失敗（' + failed.error + '）')` (copy: planner), no 重試 |
| `import_running` (409) | `error(IMPORT_RUNNING_TOAST)`, no 重試 |
| any other failure | `text = toastText(name + ' 入帳失敗', writeErrorMessage(err))`; `error(text, { retry: guardedRetry(err, this.toasts, text, () => this.isBusy(taskId), () => this.send(taskId, command)) })` — 重試 only for status 0 / ≥ 500 |

- [ ] **Step 1: Failing tests** (`notifications.spec.ts`; real `ToastService`, `AccountingService` stubbed with `Subject`s):
  ```ts
  it('入帳 posts the task\'s instance and shows 「已入帳：測試房租」', …);                 // postScheduleInstance called once with 7; current() = { kind: 'done', text: '已入帳：測試房租' }
  it('全部入帳 calls catch-up for the definition and shows 「已入帳 3 期：測試月費」', …);
  it('a catch-up that stops midway shows 失敗 with the count and the reason, without 重試', …);   // { posted: [1, 2], failed: { instance_id: 3, error: '帳戶已封存' } } → '測試月費：已入帳 2 期，之後的入帳失敗（帳戶已封存）'
  it('the row is busy while the write runs and a second press sends nothing', …);
  it('after success the row stays busy until the refreshed list lands', …);                 // loading true → still disabled; loading false → enabled / gone
  it('a 500 shows 失敗 with 重試, which posts the same instance again', …);                 // runAction → second call with 7
  it('重試 pressed while that task is busy sends nothing and shows the toast again', …);
  it('重試 still sends after the row left the list', …);                                    // tasks.set([]) then runAction → call with the captured id
  it('a 409 import_running shows 「匯入進行中，請稍後再試」 without 重試', …);               // the exact text is IMPORT_RUNNING_TOAST
  it('a 422 shows the server message without 重試', …);
  it('a failed write refreshes the list silently: the 失敗 toast is still current after the reload', …);   // refresh called with { force: true }; current() unchanged after tasks.set(...)
  it('success does not call refresh', …);
  it('a result landing after the page is destroyed still shows, without an action', …);     // fixture.destroy(); subject.error(500) → current().action null
  it('focus: when the posted row leaves and focus fell to <body>, the next row\'s primary gets focus', …);
  it('focus: with no row left it goes to the 追蹤中 summary; focus moved elsewhere is left alone', …);
  it('a 記繳款 row runs no command', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/notifications/notifications.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement** per the rules.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): 入帳 and 全部入帳 in place with a toast, a busy row and a guarded retry`.

### Task 10: States — loading, empty, a failed source

tasks.md 2.3 (empty state, failed-source line), 2.5. P4.

**Files:**
- Modify: `frontend/src/app/components/notifications/notifications.ts`, `notifications.html`, `notifications.scss`, `notifications.spec.ts`

**Interfaces:**
- Consumes: `center.loaded()`, `loading()`, `failedSources()`, `nextUpcoming()`, `trackingRows()`; `failureLine` (Task 1); `focusRetryIfLost` (`accounting/retry-focus.ts`).
- Produces: `protected readonly state: Signal<'loading' | 'empty' | 'list'>`; `protected retry(): void` (`center.refresh({ force: true })`); `protected openTracking(event: Event): void`.

| State | When | Markup and copy |
|---|---|---|
| Loading | `!loaded()` and no task yet | `p.notif-loading[role="status"]` `讀取中…` (copy: planner); chips and links render |
| Empty | `loaded()`, no counted task, no failed source | `div.notif-empty`: icon `pi pi-check-circle`, `h2` `都處理好了` (artboard); `p.empty-next` `下一件：測試銀行卡帳單 · 10/27 截止 · $1,824` = `下一件：${title} · ${when} · ${amount}` of `nextUpcoming()` (artboard), absent without one; `a.empty-tracking` `看追蹤中的 ${n} 件 ›` (artboard; n = Σ `trackingRows().count`), absent without rows — it opens the `details`, scrolls it into view and focuses its `summary` |
| List | any counted task | Task 8's sections |
| Failed source | `failedSources().length > 0`, in any state, as the last element of the page | `p.notif-failed` (`--app-text-muted`): `無法讀取：對帳` (spec) + `button.notif-retry` `重試`, disabled while `loading()`; the line stays in place during the retry and leaves only when no source is failed; after a retry that fails again focus returns to 重試 if it fell to `<body>` |

With a failed source and no counted task the page shows neither 都處理好了 nor 讀取中…: only the chips, 追蹤中 (if any), the links and the failure line.

- [ ] **Step 1: Failing tests** (`notifications.spec.ts`):
  - `before the first answers: 讀取中…, no 都處理好了`;
  - `nothing to do: 都處理好了, 「下一件：測試銀行卡帳單 · 10/27 截止 · $1,824」 and 「看追蹤中的 5 件 ›」` (debts 3 + upcoming 2);
  - `看追蹤中 opens the 追蹤中 block and focuses its summary`;
  - `nothing to do and nothing tracked: 都處理好了 alone`;
  - `a failed inbox: 「無法讀取：對帳」 is the last element of the page, the other tasks stay`;
  - `two failed sources read 「無法讀取：帳單、對帳」`;
  - `a failed source with no task left: no 都處理好了, the failure line shows`;
  - `重試 forces a refresh; the line stays and the button is disabled while loading; it leaves when the source recovers`;
  - `a retry that fails again returns focus to 重試 when focus fell to <body>`;
  - `tasks arriving while another source is still loading render at once (no 讀取中… over a list)`;
  - `a later refresh keeps the list on screen while it loads`.
- [ ] **Step 2: Run** `--include='src/app/components/notifications/*.spec.ts'` — Expected: the new cases FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): loading and empty states; a failed source is named with 重試`.

### Task 11: Tasks, design index, full suite, evidence

**Files:**
- Modify: `openspec/changes/add-notification-center/tasks.md` — tick 2.1–2.5 with notes: 2.1 「(`NavBadgeService` kept as a facade)」; 2.2 「(plus an interim e-invoice task until 6.2; debts from `getCounterparties`)」; 2.3 「(statement rows open the existing statement page until 4.2; handled link counts reconciliation actions only; one column, no pane)」; 2.4 「(phone bell on pages that use the shared page header; 股利 dot with 3.1)」; 2.5 「(snoozed: PR E, 5.3)」. Edit 6.6 to name the two call sites that remain (`inbox-panel.ts`, `reminders.ts`).
- Modify: `design_handoff_v3/README.md` — **Colour mapping**: the count pill row becomes "background `--c-red-ink` / `--app-primary`, text `--app-count-ink` (white in light, the page background in dark: white is 2.9:1 on the dark red)"; add "navigation dots: `--c-red-ink` for overdue or failed, `--app-primary` otherwise".
- Modify: `frontend/src/app/app.routes.ts` — the Caddy comment (`:8–12`) gains `/notifications`.

- [ ] **Step 1:** Edit the three files.
- [ ] **Step 2: Run** `openspec validate add-notification-center --strict` — Expected: valid.
- [ ] **Step 3: Run** `cd frontend && npx ng test --watch=false` — Expected: all pass (re-run a listed load-flaky test alone before calling it a failure); `npx ng build` — Expected: success. `grep -rn "item-count\|sub-count\|bell-count\|提醒中心 . 項" frontend/src/app --include=*.html --include=*.scss --include=*.ts` shows no production hit.
- [ ] **Step 4: Evidence (outside the repo).** Before (the PR A branch head) / after builds served statically; Playwright with routed API stubs and fictional data; screens per the Screenshot plan and the probes below. Screens go into the PR description as a private gallery link.
- [ ] **Step 5: Commit** `docs(openspec): tick add-notification-center 2.1–2.5 with notes; count pill and dot colour mapping`.

PR body must carry: the request load per refresh (R24: accounts, counterparties, queue, full inbox, latest import, actions, recalc status = 7, plus one statements list per account with questions, plus two reads per card), the R14 gap list, and the release note **`/notifications` must be added to the Caddy `@hub_spa` allowlist before the SPA is published** (outside the repo; reload and compare host / container checksums).

---

## Screenshot plan

Before / after at 1440 and 390, light and dark, unless the row says otherwise. "Before" for a new screen is the page it replaces (the timeline with its bell; the sidebar with counts). Stubs are Playwright `page.route` handlers; today is pinned with `page.clock` to 2026-10-24; localStorage seeds go in `addInitScript`.

| Screen | Route / size | Stubbed data |
|---|---|---|
| Populated, every section | `/notifications` at 1440, 1100, 820, 390 (light / dark) | 測試銀行卡 bill overdue since 10/20 (16,639); 測試月費 × 3 overdue; 測試分期 5 / 12 overdue; 測試房租 today; a statement with 10 ask items (17 / 28); import run with 4 to review; `einvoice_ask` 2; 甲 / 乙 / 丙 debts; 測試二卡 due 10/27 (1,824). 系統 has no row in B (stated in the caption) |
| Chip 財務 / chip 投資 | `/notifications` at 1440 and 390 | same; 投資 shows 0 and 「這個來源目前沒有要處理的事」 |
| 追蹤中 open | `/notifications` at 1440 and 390 | same, `details` opened |
| Empty state | `/notifications` at 1440 and 390 (light / dark) | no counted task; 測試二卡 due 10/27; three debts |
| A failed source | `/notifications` at 1440 and 390 | inbox → 500: 「無法讀取：對帳」 with 重試; a second shot with every source failing |
| In-place 入帳 | `/notifications` at 1440 and 390 | press 入帳 on 測試房租: busy row, then the 完成 toast; a 500 → 失敗 with 重試 |
| Sidebar expanded | `/accounting` at 1440 (light / dark), before / after | count 7 red; a second shot with nothing overdue (primary pill); dots on 帳戶 (red) and 記帳記錄 (primary) |
| Sidebar rail | `/accounting/entries/:id` at 1440 and `/accounting` at 1100, before / after | the pill on the bell icon; rail tip on hover |
| Badges off | `/accounting` and `/notifications` at 1440 and 390 | `hh-nav-badges` off: no pill, no dot; the page still lists every task |
| Phone header bell | `/portfolio/dividends` and `/accounting` at 390 (light / dark), before / after | bell with 7 before the gear; `/accounting` shows one bell |
| Timeline bell | `/accounting` at 1440 and 820, before / after | month-bar link without a count |
| Phone tab dots / sub-nav dots | `/portfolio` at 390, before / after | 財務 tab dot (red); on `/accounting` the 帳戶 sub-nav dot |
| Compact height | `/accounting` at 1280 × 640 | 通知, 記一筆 and the groups fit the sidebar without clipping |

Browser probes (Playwright, written into the evidence README with their output):

- **Count colour and label:** the sidebar pill's computed `background-color` equals the resolved `--c-red-ink` with an overdue task and `--app-primary` without; `aria-label` of `a.side-notif` and `a.header-bell` equals `通知，7 件要處理，3 件已逾期`; both themes.
- **Dot positions:** rail — the pill's box lies within the 通知 item's box and overlaps the icon's top-right quadrant; item dots sit inside their item at 1440 expanded and rail; the tab dot inside its tab at 390.
- **No double bell:** at 390 on `/accounting`, `document.querySelectorAll('.header-bell, .monthbar .bell').length === 1`; at 1440 it is 1 (the month-bar link) and `.header-bell` is absent.
- **Nav highlight and title on `/notifications`:** h1 `通知`; `a.side-notif[aria-current="page"]`; no `.group-head.current`; at 390 no `.m-tab.active`, no `.sub-nav`, no `.header-bell`, ‹ present; a direct load then ‹ lands on `/accounting`.
- **Keyboard order through a row:** Tab from the last chip reaches the first row's primary, then its 逐期處理, then the next row's primary; Enter on 入帳 runs it; each focused control has a visible focus ring (screenshot).
- **In-place 入帳 toast:** after 入帳 the toast's box does not intersect the floating 記一筆 (820, 1100, 1440) and sits above the tab bar and its + at 390; `elementFromPoint` at the last row's primary centre (scrolled to the page end) is that button at 820 / 1100 / 1440 (not the FAB) and at 390 (not the tab bar).
- **Overlays:** with the 失敗 toast shown, the PageHeader ⋯ menu is not present on this page; a PrimeNG overlay is not opened by this page (stated, not probed).
- **Requests:** one page open issues each source URL once (count the routed requests); a focus event inside 30 s issues none.

## Verification

Checked against the worktree at `323228f4` (branch `feat/notif-b-notification-page`). The survey's line numbers were re-checked; where they differed the numbers here are the worktree's (the survey's citations for `navigation-state.service.ts`, `sidebar.ts`, `sub-nav.ts`, `mobile-nav.ts` and `page-header.ts` were past the end of those files).

- **Service being folded:** `nav-badge.service.ts` — throttle constant `:22`; `badges` `:80–100`; statements effect `:105–119`; write effect `:121–134`; `start` `:138–158`; `refresh` `:160–168`; `loadAccounting` `:171–205` with the single `forkJoin` `:178–187` and the `limit: 1` inbox read `:183`; `load` with the generation check `:208–215`. Started from `shell.ts:77`. Injected by `navigation-state.service.ts:34`, gated by the setting at `:55–57`.
- **Stubs of `NavBadgeService`:** nine spec files (`grep -rln NavBadgeService frontend/src/app`): `app.routes.spec.ts`, `reports.spec.ts`, `reward-rules.spec.ts`, `mobile-nav.spec.ts`, `navigation-state.service.spec.ts`, `page-header.spec.ts`, `shell.spec.ts`, `sub-nav.spec.ts`, `sidebar.spec.ts` (`sidebar.spec.ts:64`; badge fixtures `:115`, `:159`, `:229`; label assertion `:135`).
- **Nav model:** `navItemForUrl` and its 庫存 fallback `navigation.ts:84–93`; `NavBadge` `:95–99`; `summarizeGroupBadges` `:125–135`; `activeItem` / `activeGroup` `navigation-state.service.ts:40`, `:46–48`; sub-nav `show` `sub-nav.ts:19`; tab highlight and `hasDot` `mobile-nav.ts:21–32`; hidden groups validated against `NAV_GROUPS` `navigation-preferences.service.ts:14`.
- **Entry points:** sidebar 記一筆 `sidebar.html:27–40`, groups loop `:41`, counts and dots `:56–71`, `:97–111`; `placeTip` selector `sidebar.ts:60`; page header gear `page-header.html:37–39`, `gear` `page-header.ts:79–81`, `back` `:50`, `subtitle` `:52`, `goBack` `:196–202`; timeline bell `timeline.html:14`, its state `timeline.ts:485–501`, `:722–731`, `:863–871`, `:1507–1536`, styles `timeline.scss:343–374` (`.bell-count` carries a `#fff` literal that goes with it); `isPhone` `timeline.ts:576`. The timeline's `counterparties` and `inbox` have no other consumer (grep).
- **Own-header pages (R14 gap):** `grep -l "<h1"` minus `app-page-header` users gives the ten templates listed in Task 6; on a phone the accounting layout renders only the routed page (`accounting-layout.html:1–2`).
- **Sources:** `getAccounts` `accounting.service.ts:118`, `getCounterparties` `:395`, `catchUpSchedule` `:481`, `getScheduleInstances` `:485`, `postScheduleInstance` `:493`, `getLatestImport` `:542` (404 → null); every schedule write bumps `entriesChanged` (`:439`). `InboxService.get` `inbox.service.ts:24`, `actions(limit = 50)` `:53`. `StatementsService.list` `statements.service.ts:23`; `reconProgress` `statement-view.ts:163–167`. `reminderDues` (45-day horizon) `billing-math.ts:137`, `:163–165`; `payDateOf` `:34`; `BillState` `billing.service.ts:15–33`; `ensure` `:101`, `refresh` `:121`, `bill` `:189`, `failed` `:194`. `importReviewCount` `import-review-count.ts:4–10`; the review count is shown on 帳戶 (`accounts.html:15`) — there is no separate review page. Ask bucket: the private `bucket()` in `inbox-view.ts` (default branch). `catch_up` posts "the pending periods due today or earlier" (`schedule_service.py:377`); the queue reaches 30 days ahead (`schedule_read.py:21`).
- **Reminders targets:** tab keys `all / todo / cards / debts / pending` `reminders.ts:55–61`, read from `?tab=` at `:439–440`; the handled list sits in the 待處理 tab (`reminders.html:26–29`).
- **Tokens:** `--app-primary` `styles.scss:38` / `:195`; `--c-red-ink` `:69` / `:218`; `--c-red-soft` `:66` / `:216`; `--app-text-muted` `:28` / `:191`; dark `--app-bg` `#0f1218` `:184`. Computed contrast for P5: white on `#c53a3f` 5.18, on `#533afd` 6.19; `#0f1218` on `#ff6369` ≈ 6.5, on `#8b7dff` ≈ 5.8 (Task 6 re-computes them from the stylesheet).
- **Artboard copy quoted:** 「數字只算要你動手的事」, chips `aria-label="來源"`, 「追蹤中 · 不計入數字」, 「已處理（近 7 天）12 件 ›」 (NotifDesktop / NotifPhone); 「都處理好了」, 「下一件：… · 10/27 截止 · $1,824」, 「看追蹤中的 6 件 ›」 (FeedbackSpec D); row copy 「10/20 截止 · 逾期 4 天」, 「最早 8/20」, 「N 筆要你決定」, 「已對上 17 / 28 行」, 記繳款 / 全部入帳 / 入帳 / 逐期處理 (NotifPhone `renderVals`). `shortDate` pads the month (`08/20`, `dates.ts:23`), so the plan's dates read `08/20` where the artboard reads `8/20`.
- **Could not confirm:** that a positive `Counterparty.open_amounts` amount is 應收 — inferred from `−Σ amount` over receivable / payable entries (`settings_service.py:1122`); Task 3's implementer checks it against a receivable fixture and swaps the two labels if it is the other way, saying so in the task report. Whether the inbox's default page (500 items) can truncate a statement's items (the per-statement count would then be low). Whether `formatSigned` prints `−` or `-` (tests take the helper's output). Row / FAB / tab-bar overlap and rail pill placement (browser probes only).
