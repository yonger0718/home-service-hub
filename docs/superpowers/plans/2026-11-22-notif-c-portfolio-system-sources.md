# Notification center PR C: portfolio and system sources (股利, 匯入, 國泰對帳單) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通知 also lists portfolio and system work: one batch task for dividends past their payment date (the rest in 追蹤中), one task for a failed import, one task per 國泰 daily-statement date that needs the owner (with 確認 in place) and one task when the 06:40 run is failed, latched or stale. The 投資 chip gets real numbers, 股利 and 匯入 get their dots, and `/portfolio/import` gets a 國泰每日對帳單 status card.

**Architecture:** SPA only (`frontend/`). Three new task sources join `NotificationCenterService`'s round next to the five ledger ones: `dividends` (every dividend page, filtered in the client), `imports` (the recalc status, now read inside the round, plus the device-local last failure) and `cathay` (`…/cathay-statement/pending` + `/runs/latest`, one source). Pure builders in `components/notifications/sources/` turn the answers into `NotifTask`s; the run-health rule is a pure function of `(latest, now)` computed in Asia/Taipei, with `now` from an injectable clock read once per round. 確認 is a third `NotifCommand` kind handled by the page like 入帳; an ack bumps no change signal, so the handler calls `center.invalidate()` itself. The import page's card renders the same `center.cathay()` view, so the card, the tasks and the dot cannot disagree.

**Tech Stack:** Angular 21.2 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`, quote the glob), SCSS with the tokens of `frontend/src/styles.scss`.

**Spec:** `openspec/changes/add-notification-center/` — design D2 (Dividends / Import / 國泰 rows, the run rule), D3, D7, D8; tasks §3 (3.1–3.4); `specs/frontend-notification-center/spec.md` (Requirement "Portfolio and system task sources", scenario "Source filter"), `specs/frontend-csv-import/spec.md` ("國泰每日對帳單 status card"), `specs/frontend-app-shell/spec.md` ("Navigation badges"). Server side of the rule, for reference only: `services/accounting-service/notify/cathay.py:42–48` (`last_scheduled_run`) and `:208–214` (the three run messages). `openspec/specs/` is not edited in this PR.

## Open points for the owner

Only what still needs an answer. Each has the resolution this plan uses until told otherwise.

1. **A dividend whose `payment_date` is today.** D2 says the batch is `payment_date ≤ today`; the spec requirement says "past their `payment_date`" and its scenario "before today". Plan: `≤ today` (the money arrives that day; D2 is the more precise text). One comparison in `dividendBatch()` if you prefer `<`.
2. **Tone of a latched / stale run on 通知.** Decision 4 makes latched and stale *warnings*, but every 系統 row is drawn in the alert (red) style and turns the 通知 count red (D3), and the 匯入 dot is red for any bad run (D8). A row has no warning style. Plan: on 通知 and on the dot all three states are red as the spec says; the warning tone shows in the import card's banner (注意 for latched / stale, 錯誤 for failed) and in the wording. The alternative is a new row style, which no artboard has.
3. **A held date turns the 通知 count red without a 匯入 dot.** Follows from D2 (pending dates are 系統) and owner decision 2 (pending dates do not light 匯入). The app-shell spec says a nav item *MAY* carry a dot, so nothing is violated; listed so it is not a surprise. Plan: as decided.
4. **Portfolio API unreachable hides the device-local import failure too.** Today the 匯入 dot stays lit from this device's last failed import even when the recalc read fails. With 匯入 as a named source, the spec's "a failing source SHALL hide its dot" applies: the dot and the task leave and the page says 「無法讀取：匯入」. Plan: follow the spec (the import page's own banner still shows the device failure). Say so if the device-local part should survive a failed read.
5. **Dividend rows carry a symbol, not a name.** `Dividend` (`models/portfolio.model.ts:202`) has `symbol` only, so the batch task's second line lists symbols (`9901、9902`), not names as the spec's example (玉山金, 台積電) suggests. Plan: symbols; names would need a second read per round.
6. **The copy table below** (planner's wording, marked) — edit freely.

Not an owner question, but unverified here because env and token files are off limits: the SPA's portfolio token must have `write` scope for the ack (it already POSTs `confirm-receipt`, so it should). Task 8's live check covers it; a 401 / 403 would show as 「… 確認失敗」.

## Decisions

O = owner, C = coordinator, P = planner (made where the others were silent).

| # | Decision | If wrong |
|---|---|---|
| O1 | Dividends are read client-side: every page, as the 股利 page does (`allPages`, `limit: 100`), filtered in the SPA. No backend change, no API deploy | A `receipt_status` filter later replaces one read |
| O2 | The 匯入 dot lights only for an import failure or a bad 國泰 run (missing / failed / latched / stale). Pending dates (held / mismatch / conflict) are 通知 tasks, counted, and do not light it | One condition in `badges` |
| C3 | Run health is computed explicitly in Asia/Taipei (06:40 + 15 min grace) from an injectable clock, mirroring `notify/cathay.py`; precedence latched → failed → missing-or-stale, as the notifier | One pure function |
| C4 | failed = error wording; latched and stale = warning wording (see open point 2 for where the tone can show) | Copy / banner tone |
| C5 | Pending dates beyond the API's 50 are one uncounted note line, like PR B's `center.overflow()` note | Markup |
| C6 | 確認 is a new `NotifCommand` kind; 404 reads as done; no 復原 (no un-ack endpoint); failure toast 「<name> 確認失敗」 with 重試 for a network failure only, a reason appended only when `knownWriteReason` gives one, raw error to the console. One request per press (#411) | Handler branch |
| C7 | The dividend task links to `/portfolio/dividends?status=pending` (new query param); the 股利 dot is badge key `dividends`, `--app-primary` (not overdue or failed work), gone with badges off | Link / tone |
| C8 | Source names for the failure line: 股利, 匯入, 國泰對帳單; each fails on its own and hides its dot | Strings |
| C9 | Ids `div:batch`, `import:fail:<key>`, `cathay:<trade_date>`, `cathay:run`; a changed state is a new fingerprint (snooze is PR E) | Strings |
| C10 | The import-failure signal keeps its two inputs (server recalc status, device-local last failure). **Known limitation, not fixed here:** the recalc status is in-process on the API (lost on a restart) and the last failure is per device, so the task can differ between devices and can vanish by itself | A durable server record later |
| C11 | The status card is a third card in the import page's side column; no artboard, content from the spec text | Placement |
| P1 | `cathay` is one source over two reads: if either fails the whole source fails (no pending tasks without a run verdict, no half card) | Split later |
| P2 | The recalc status is read inside every round through `read()` (it was `refresh()`-only and silent). `readRecalc()` and its generation counter go | Three existing tests are rewritten (Task 4) |
| P3 | `now` is taken once per round. A page left open across 06:55 shows the stale task at the next round (focus, open, a write), not at 06:55 sharp | A timer later |
| P4 | Dividend "today" is the round's `today` (browser-local calendar day, as every other source); only the run rule is pinned to Asia/Taipei | One argument |
| P5 | `NotifTrackingRow` gains `source`; a row shows under 全部 and under its own chip. The all-clear's 下一件 stays ledger-only | One filter |
| P6 | 匯入 dot label: the import failure's 最近一次匯入失敗 (spec), or the run task's title, or both joined with 「、」 | String |
| P7 | A pending date's fingerprint is `status|reason` (not `last_at`, which moves on every run) | E's wake-up |
| P8 | No amount on the dividend task (gross / net / currency mixes are the 股利 page's business) | One field |
| C12 | A dividend scan that hits `allPages`' page cap (`capped: true`; default 50 pages, `portfolio/paging.ts:13`, `:24–25`) marks the `dividends` source FAILED for that round: 「無法讀取：股利」, no task, no row, no dot. A partial scan never reads as an all-clear. O1 (client-side filtering) stands | Beyond 5,000 dividend rows the source needs the server filter |
| C13 | `read()`'s 20 s `timeout` (`notification-center.service.ts:440`) covers the whole paginated dividend stream: `allPages` emits once, after its last page, so pages that together take longer than `READ_TIMEOUT_MS` fail the whole source for that round | A per-page timeout later |
| C14 | The card releases a 確認 row when `settledRound() >= N`, where N is the id `invalidate()` returned (`:324–325`) — the round started after the ack's answer. This differs on purpose from the 通知 page, which stores the round id read *before* its `invalidate()` and releases on `settled > that` (`notifications.ts:415–417`, `:270`); both mean "a round started after the answer has settled" | One comparison |

## Copy

"(spec)" is given by the change spec, "(notifier)" by `notify/cathay.py`; everything else is the planner's. `{stamp}` = a run time in Asia/Taipei: `今天 06:41`, `昨天 06:41`, else `MM/DD HH:mm`.

| Where | Text |
|---|---|
| Source names (failure line) | `股利` · `匯入` · `國泰對帳單` |
| Dividend task title | `股利待確認收款 · {n} 筆` (spec) |
| Dividend task line 2 | when `最早 {MM/DD} 發放`; meta up to 3 distinct symbols joined with `、`, then ` 等 {k} 檔` for the rest |
| Dividend task action | `查看` → 股利, 待收 |
| 股利 dot label | `{n} 筆待確認收款` (spec) |
| Dividend 追蹤中 row | title `股利待發放 · {n} 筆`; meta up to 3 of `{symbol} {MM/DD}` / `{symbol} 日期未定` joined with ` · `, then ` · 等 {k} 筆`; link `查看`; summary part `股利 {n} 筆` |
| Import failure task | title `最近一次匯入的重算失敗` (server recalc failed) else `最近一次匯入失敗`; when `{MM/DD HH:mm}` (local, as the import review task); meta the file name for the device failure, none for the recalc; action `查看` → 匯入 |
| 匯入 dot label | `最近一次匯入失敗` (spec) / the run title below / both joined with `、` |
| Pending date title | `國泰對帳單 {MM/DD}` |
| Pending date line 2 (when) | held: `保留` · `保留：名稱對不到代號 {x}` · `保留：含融資融券交易` · `保留：{reason}`; mismatch: `對不上 CSV`; conflict: `收到不同版本的對帳單` (all notifier) |
| Pending date meta | `自 {MM/DD} 起` (Taipei date of `since`); none when `since` is null |
| Pending date actions | `確認` (command); secondary `查看` → 匯入 |
| Ack toasts | done `已確認：國泰對帳單 {MM/DD}`; 404 `國泰對帳單 {MM/DD}：已經不需要確認` (done); failed `國泰對帳單 {MM/DD} 確認失敗` |
| Run failed (error) | title `國泰對帳單匯入失敗` (notifier); when `{stamp}`; meta `錯誤代碼 {code}` or none |
| Run latched (warning) | title `國泰對帳單匯入已停用` (notifier); when `{stamp}`; meta `排程已停用，之後不會自動匯入` |
| Run stale (warning) | title `國泰對帳單今天沒有執行` (notifier); when `06:40 的排程沒有回報`; meta `上次執行 {stamp}` or `還沒有執行紀錄` |
| Run task action | `查看` → 匯入 |
| Truncation note on 通知 | `還有 {n} 天的國泰對帳單未列出 · 到匯入頁查看 ›` |
| Card title | `國泰每日對帳單` |
| Card run line (ok) | `{stamp} · 已匯入 {posted} 份` (spec); `{stamp} · 沒有新的對帳單` when `posted` is 0; plus ` · 收到 {files_seen} 份` when `files_seen > posted` |
| Card banner (bad run) | heading = the run title; detail = when and meta joined with ` · `; tone error for failed, warn for latched / stale |
| Card latest-run details (bad run, beside the banner) | `最近一次執行：{stamp} · {result} · 收到 {files_seen} 份 · 已匯入 {posted} 份`, `{result}` = `失敗` / `已停用` / `成功` (a stale run whose latest was ok); absent only when there is no run at all |
| Card last success | `上次成功 {stamp}` (only under a bad run, when `latest_ok` exists) |
| Card dates | heading `需要你確認的日期`; row `{MM/DD}` + the line-2 text; button `確認` (accessible name `確認：國泰對帳單 {MM/DD}`); none: `沒有需要確認的日期` |
| Card truncation | `還有 {n} 天未列出` |
| Card loading / unread | `讀取中…` / `無法讀取國泰對帳單狀態` with `重試` |

## Global Constraints

- Frontend only. No backend change, no new dependency, no new endpoint, no migration. Never run uvicorn or alembic; never read `~/.config`, `~/.hermes` or any `.env`.
- Colours only through tokens of `frontend/src/styles.scss`; no hex, `rgb()` or `rgba()` literal in a component stylesheet or inline style.
- Chinese UI copy exactly as the Copy table gives it.
- Count rule unchanged (D3): 已逾期 + 今天 + 要你確認 + 系統; 追蹤中 and both note lines never count. A 系統 task makes the count red.
- A source that fails drops its tasks and its dot, is named in 「無法讀取：…」 and never blocks another source; a round still settles only when every task source has marked ok or failed (now eight).
- Time: the run rule uses the fixed offset +08:00 and UTC getters only — no `getHours()`, `getDate()`, `todayIso()` or `toLocale…` inside `sources/cathay.ts`.
- Out of scope: the review flow (PR D), snooze / auto-pay / answers (PR E), e-invoices (PR F), the phone bell on the ten own-header pages (#413), issues #414 #416 #418, any un-ack, any batch 確認收款 from 通知.
- Tests use fictional data only: symbols `9901`, `9902`, `9903`, the name 測試股票, file `測試匯入.csv`, invented amounts and dates. No real holding, amount or account.
- Request load, accepted and stated in the PR body: each round adds 3 requests (the recalc status, two 國泰 reads) plus the dividend pages — one per 100 rows, up to 50 (the `allPages` cap, C12). That is before the app's interceptor, which re-sends a failed GET once (`interceptors/error-logging.interceptor.ts:12`, `:19`), so a failing round can double it.
- Commit per task with explicit paths, message in English, ending with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. No `--amend`, no push.
- Known load-flaky tests (pass alone; not this PR's): two fragment-scroll tests in `reward-rules.spec.ts`, `shell.spec` "820 px: N inside the detail sheet opens the form", `rules-section.spec.ts` "a menu that does not fit below the viewport opens above ⋯".

## Conflicts between the change spec and this plan

| Spec text | Conflict | Resolution |
|---|---|---|
| D2 "Dividends — `GET /api/portfolio/dividends` (`receipt_status=pending`)" | The endpoint has no such filter (`routers/portfolio.py` `get_dividends`; `DividendQuery`, `portfolio.model.ts:358`) | O1: read every page, filter in the client |
| NC requirement "past their `payment_date`" vs D2 "`payment_date` ≤ today" | The day itself | Open point 1 |
| csv-import "A stale or failed latest run SHALL show as a 注意 or 錯誤 banner"; D2 "`latest.result` is not `ok`" | Neither names the tone of `latched` | C4: failed 錯誤; latched and stale 注意 |
| D8 "匯入 (import or Cathay failure, red)" | Whether pending dates count as a "Cathay failure" | O2: no |
| NC scenario "Dividends past payment date": 玉山金 and 台積電 | The dividend rows carry no name | Open point 5 |
| Proposal Impact: "Portfolio routes have no auth today (#117)" | Token auth shipped (#117); the ack needs `write` | Checked live in Task 8 |
| csv-import card "(time, result, files seen, posted)" vs its scenario 「今天 06:41 · 已匯入 1 份」 | The scenario line has no result or files-seen part | Healthy run: the scenario's line, `收到 N 份` added only when it differs from `posted` (the result is implied by the absence of a banner). Bad run: the banner plus a details line that always carries all four (time, result, 收到 N 份, 已匯入 N 份) |

## Review Focus

Five input classes no happy-path test would exercise; each has a test in the task named.

- **A device outside Taipei, at the day boundary.** `now` given as `…Z` instants around 22:54Z / 22:55Z (06:54 / 06:55 Taipei, the previous UTC day): the rule must not depend on the machine's zone. Writing one instant two ways is not a host-zone test, so the focused spec is run twice, once per host zone, and must pass both times (Task 2 Step 4): `cd frontend && TZ=UTC npx ng test --watch=false --include='src/app/components/notifications/sources/cathay.spec.ts'` and `cd frontend && TZ=Asia/Taipei npx ng test --watch=false --include='src/app/components/notifications/sources/cathay.spec.ts'`. → Task 2.
- **Portfolio API down.** All four portfolio reads fail: three sources are named, 股利 and 匯入 lose their dots (the device-local failure included), ledger tasks and their count stay, the round settles. And half a 國泰 answer (pending ok, runs fails) fails the whole source. → Task 4.
- **Ack that nothing announces.** A 200 ack bumps no change signal: the row must leave through the handler's own `invalidate()`. A 500 that committed, then 重試 → 404 → reads as done. One POST per press with the app's interceptor installed. → Task 5.
- **Truncated or inconsistent `pending`.** `total: 73` with 50 items → 50 counted tasks and a note saying 23; `truncated: true` with `total` ≤ the items received → no note and never a negative number. → Tasks 2, 5.
- **Dividend rows at the edges.** Pending with `payment_date` null, today, tomorrow; `receipt_status` undefined, `legacy_unknown`, `unresolved`, `confirmed`; 101 rows (two requests, both counted); a scan stopped by the page cap or by the 20 s limit mid-stream (source failed, never an all-clear). → Tasks 3, 4.
- Real browser only (jsdom cannot see it): the card in the side column at 1440 against the two cards above it, and under the footer at 390; the 確認 button's 44 px target on a phone. → Screenshot plan.

## File map

| File | Responsibility | Task |
|---|---|---|
| `frontend/src/app/models/portfolio.model.ts`, `services/portfolio.service.ts` | 國泰 types and the three calls | 1 |
| `components/notifications/notif-task.ts`, `sources/ledger-misc.ts` | New source keys, command kind, tracking row `source` | 1 |
| `components/notifications/sources/cathay.ts` | Run rule, stamps, tasks, card view | 2 |
| `components/notifications/sources/portfolio.ts` | Dividend batch / tracking row / dot, import failure task | 3 |
| `services/notification-center.service.ts` | Three sources, clock, `cathay` view, badges | 4 |
| `components/notifications/notifications.{ts,html}` | 確認 command, tracking rows by source, the note | 5 |
| `components/portfolio/dividend-list/dividend-list.ts` | `?status=` | 6 |
| `components/portfolio/import/cathay-status-card/`, `import.{ts,html}` | The card | 7 |
| `openspec/changes/add-notification-center/tasks.md` | Ticks | 8 |

**Order.** Task 1 first. Tasks 2, 3 and 6 in parallel (disjoint files). Task 4 after 2 and 3. Tasks 5 and 7 in parallel after 4. Task 8 last.

---

### Task 1: Types, API calls, model additions

tasks.md 3.1–3.3 (plumbing). Keeps every existing test green: the three new source keys are marked `ok` by a shim until Task 4 reads them.

**Files:**
- Modify: `frontend/src/app/models/portfolio.model.ts`, `frontend/src/app/services/portfolio.service.ts`, `portfolio.service.spec.ts` (create next to the service if absent), `frontend/src/app/components/notifications/notif-task.ts`, `notif-task.spec.ts`, `sources/ledger-misc.ts`, `sources/ledger-misc.spec.ts`, `frontend/src/app/services/notification-center.service.ts`, `frontend/src/app/components/notifications/notifications.ts` (`TRACKING_UNIT` only), `notifications.spec.ts` (the `ROWS` fixtures gain `source: 'ledger'`)

**Interfaces:**
- Consumes: `NotifSourceKey`, `SOURCE_NAMES`, `NotifCommand`, `NotifTrackingRow` (`notif-task.ts:3`, `:70`, `:11`, `:44`); the status initialiser (`notification-center.service.ts:103–105`) and `startRound()` (`:328`).
- Produces:
  ```ts
  // models/portfolio.model.ts  (shapes: broker_cathay_statement_service.py pending_days / latest_runs)
  export type CathayPendingStatus = 'held' | 'mismatch' | 'conflict';
  export interface CathayPendingDay { trade_date: string; status: CathayPendingStatus; reason: string | null; since: string | null; last_at: string | null; }
  export interface CathayPending { items: CathayPendingDay[]; total: number; truncated: boolean; }
  export interface CathayRun { run_at: string; result: 'ok' | 'latched' | 'failed'; code: string | null; files_seen: number; posted: number; }
  export interface CathayRuns { latest: CathayRun | null; latest_ok: CathayRun | null; }
  // services/portfolio.service.ts
  getCathayPending(): Observable<CathayPending>;                                   // GET  /api/portfolio/imports/cathay-statement/pending
  getCathayRuns(): Observable<CathayRuns>;                                         // GET  /api/portfolio/imports/cathay-statement/runs/latest
  ackCathayDay(tradeDate: string): Observable<{ trade_date: string; ack_status: string }>;  // POST …/cathay-statement/days/{tradeDate}/ack, body null
  // notif-task.ts
  export type NotifSourceKey = 'bills' | 'schedules' | 'recon' | 'importReview' | 'debts' | 'dividends' | 'imports' | 'cathay';
  // SOURCE_NAMES gains, in this order after 借還款: dividends '股利', imports '匯入', cathay '國泰對帳單'
  export type NotifCommand = /* the two existing kinds */ | { readonly kind: 'ackCathay'; readonly tradeDate: string; readonly name: string };
  export interface NotifTrackingRow { readonly id: 'debts' | 'upcoming' | 'dividends'; readonly source: NotifSource; /* title, meta, count, link unchanged */ }
  ```
  `TRACKING_UNIT` (`notifications.ts:64`) gains `dividends: { name: '股利', unit: '筆' }`. `debtsRow` and `upcomingRow` return `source: 'ledger'`.

- [ ] **Step 1: Failing tests.**
  - `portfolio.service.spec.ts` (HttpTestingController): `getCathayPending` → one GET to `/api/portfolio/imports/cathay-statement/pending`; `getCathayRuns` → one GET to `…/runs/latest`; `ackCathayDay('2026-10-28')` → one POST to `/api/portfolio/imports/cathay-statement/days/2026-10-28/ack` with `request.body` null.
  - `notif-task.spec.ts`: `failureLine(['cathay', 'bills', 'dividends', 'imports'])` → `無法讀取：帳單、股利、匯入、國泰對帳單`.
  - `ledger-misc.spec.ts`: `debtsRow(...)` and `upcomingRow(...)` results have `source: 'ledger'`.
- [ ] **Step 2: Run** `--include='src/app/{services/portfolio.service,components/notifications/notif-task,components/notifications/sources/ledger-misc}.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** the types, the three calls and the model additions. In `notification-center.service.ts`: add `dividends: IDLE, imports: IDLE, cathay: IDLE` to the status initialiser and, at the end of `startRound()`, `for (const key of ['dividends', 'imports', 'cathay'] as const) this.mark(key, round, 'ok');` with the comment `// Task 4 replaces this with the reads.` Nothing in `send()` handles `ackCathay` yet: give its `else` branch an explicit `else if (command.kind === 'catchUp')` so the union stays exhaustive under tsc without behaviour change.
- [ ] **Step 4: Run** the same command, then `--include='src/app/{services/notification-center*,components/notifications/**/*}.spec.ts'` and both `tsc` commands — Expected: PASS (existing round tests still settle).
- [ ] **Step 5: Commit** `feat(notifications): portfolio source keys, ack command kind and 國泰 statement calls`.

### Task 2: 國泰 run rule, tasks and card view (parallel with 3 and 6)

tasks.md 3.3 (run health rule, pending dates), 3.4 (the 07:00 test). C3, C4, C9, P7.

**Files:**
- Create: `frontend/src/app/components/notifications/sources/cathay.ts`, `cathay.spec.ts`

**Interfaces:**
- Consumes: `CathayPending`, `CathayPendingDay`, `CathayRun`, `CathayRuns` (Task 1); `NotifTask` (`notif-task.ts:20`); `shortDate` (`accounting/dates.ts`).
- Produces:
  ```ts
  export const CATHAY_RUN_HOUR = 6;
  export const CATHAY_RUN_MINUTE = 40;
  export const CATHAY_GRACE_MINUTES = 15;
  export type CathayRunState = 'ok' | 'failed' | 'latched' | 'stale';
  /** The most recent 06:40 Asia/Taipei that is at least 15 minutes before `now` (as notify/cathay.py last_scheduled_run). */
  export function lastScheduledRun(now: Date): Date;
  /** latched → 'latched'; failed → 'failed'; null, or run_at before lastScheduledRun(now) → 'stale'; else 'ok'. */
  export function cathayRunState(latest: CathayRun | null, now: Date): CathayRunState;
  /** `今天 06:41` / `昨天 06:41` / `10/26 06:41`, all by the Asia/Taipei calendar. */
  export function runStamp(iso: string, now: Date): string;
  /** Line 2 of a pending date (Copy table). */
  export function dayStatusText(day: Pick<CathayPendingDay, 'status' | 'reason'>): string;
  /** `total − items.length` when `truncated` and positive, else 0. */
  export function cathayMore(pending: CathayPending): number;
  /** One task per pending date (sorted by trade_date), then `cathay:run` unless the state is 'ok'. */
  export function cathayTasks(pending: CathayPending, runs: CathayRuns, now: Date): NotifTask[];
  export interface CathayDayView { readonly tradeDate: string; readonly label: string; readonly text: string; }   // label `10/28`
  export interface CathayView {
    readonly state: CathayRunState;
    readonly runLine: string | null;                                                            // ok only
    readonly banner: { readonly tone: 'error' | 'warn'; readonly heading: string; readonly detail: string } | null;
    /** Bad run only: `最近一次執行：{stamp} · {result} · 收到 N 份 · 已匯入 N 份`; null when ok or when there is no run. */
    readonly runDetails: string | null;
    readonly lastOk: string | null;                                                             // `上次成功 …`, bad run only
    readonly days: readonly CathayDayView[];
    readonly more: number;
    /** The 匯入 dot's label for a bad run (the run title), else null. */
    readonly dotLabel: string | null;
  }
  export function cathayView(pending: CathayPending, runs: CathayRuns, now: Date): CathayView;
  ```

Fields (all `source: 'portfolio'`, `from: 'cathay'`, `tier: 'system'`, `amount: null`, `progress: null`; texts from the Copy table):

| Task | `id` | `icon` | `primary` | `secondary` | `sortKey` | `fingerprint` |
|---|---|---|---|---|---|---|
| Pending date | `cathay:${trade_date}` | `pi-file` | `{ label: '確認', command: { kind: 'ackCathay', tradeDate: trade_date, name: '國泰對帳單 ' + shortDate(trade_date) } }` | `[{ label: '查看', route: '/portfolio/import' }]` | `trade_date` | `${status}|${reason ?? ''}` |
| Run | `cathay:run` | `pi-exclamation-triangle` | `{ label: '查看', route: '/portfolio/import' }` | `[]` | `9999-12-31` (after the dates) | failed / latched: `${state}|${latest.run_at}`; stale: `stale|${lastScheduledRun(now).toISOString()}` |

- [ ] **Step 1: Failing tests** (`cathay.spec.ts`; `at(s)` = `new Date(s)`; `run(overrides)` builds `{ run_at, result: 'ok', code: null, files_seen: 1, posted: 1 }`):
  ```ts
  it('spec 3.4: at 07:00 with the latest run at yesterday 06:41 the run is stale and a 系統 task says the 06:40 run did not report', () => {
    const now = at('2026-10-28T07:00:00+08:00');
    const runs = { latest: run({ run_at: '2026-10-27T06:41:00+08:00' }), latest_ok: null };
    expect(cathayRunState(runs.latest, now)).toBe('stale');
    expect(cathayTasks({ items: [], total: 0, truncated: false }, runs, now)).toEqual([expect.objectContaining({
      id: 'cathay:run', tier: 'system', source: 'portfolio', from: 'cathay', title: '國泰對帳單今天沒有執行',
      when: '06:40 的排程沒有回報', meta: '上次執行 昨天 06:41', primary: { label: '查看', route: '/portfolio/import' },
      fingerprint: 'stale|2026-10-27T22:40:00.000Z' })]);
  });
  it.each([
    ['2026-10-28T06:54:00+08:00', 'ok'],      // grace not over: yesterday's 06:40 is still the deadline
    ['2026-10-28T06:55:00+08:00', 'stale'],   // 06:40 + 15 min reached
  ])('latest yesterday 06:41, now %s → %s', (now, state) => { expect(cathayRunState(run({ run_at: '2026-10-27T06:41:00+08:00' }), at(now))).toBe(state); });
  it('latest today 06:41 ok at 07:00 → ok, no run task', …);                       // cathayTasks(...) → []
  it('the same instants written in UTC give the same verdicts (Review Focus: device zone)', …);
  //   now 2026-10-27T22:54:00Z → 'ok'; 2026-10-27T22:55:00Z → 'stale'; run_at '2026-10-26T22:41:00Z'
  it('lastScheduledRun: 06:54 → yesterday 06:40; 06:55 → today 06:40; 00:10 → yesterday 06:40', …);   // compare toISOString(): …T22:40:00.000Z
  it('no run at all is stale with 還沒有執行紀錄', …);                                // latest null → meta '還沒有執行紀錄'
  it('failed today: 國泰對帳單匯入失敗, when 今天 06:41, meta 錯誤代碼 password_file', …);
  it('failed yesterday and nothing since stays failed (notifier precedence), when 昨天 06:41', …);
  it('latched: 國泰對帳單匯入已停用 with 排程已停用，之後不會自動匯入', …);
  it('a failed run without a code has no meta', …);
  it('runStamp: 今天 / 昨天 / MM/DD HH:mm by the Taipei calendar', …);               // now 10/28 07:00+08:00; '2026-10-25T22:41:00Z' → '10/26 06:41'
  it('a held date: 確認 command, 查看 secondary, text from the reason', () => {
    const [t] = cathayTasks({ items: [{ trade_date: '2026-10-28', status: 'held', reason: 'unresolved_symbol:測試股票', since: '2026-10-29T06:41:00+08:00', last_at: '2026-10-29T06:41:00+08:00' }], total: 1, truncated: false }, okRuns, now);
    expect(t).toMatchObject({ id: 'cathay:2026-10-28', tier: 'system', title: '國泰對帳單 10/28', when: '保留：名稱對不到代號 測試股票', meta: '自 10/29 起',
      primary: { label: '確認', command: { kind: 'ackCathay', tradeDate: '2026-10-28', name: '國泰對帳單 10/28' } },
      secondary: [{ label: '查看', route: '/portfolio/import' }], sortKey: '2026-10-28', fingerprint: 'held|unresolved_symbol:測試股票' });
  });
  it.each([
    [{ status: 'held', reason: null }, '保留'],
    [{ status: 'held', reason: 'unsupported_category' }, '保留：含融資融券交易'],
    [{ status: 'held', reason: '其他原因' }, '保留：其他原因'],
    [{ status: 'mismatch', reason: null }, '對不上 CSV'],
    [{ status: 'conflict', reason: null }, '收到不同版本的對帳單'],
  ])('dayStatusText %o → %s', …);
  it('a date whose status changed has a new fingerprint; a newer last_at alone does not', …);
  it('dates come oldest first and the run task last', …);
  it('cathayMore: total 73 with 50 items → 23; truncated with total 50 and 50 items → 0; not truncated → 0', …);
  it('cathayView healthy: runLine 今天 06:41 · 已匯入 1 份, no banner, no days', …);  // the csv-import scenario
  it('cathayView: posted 0 → 沒有新的對帳單; files_seen 3, posted 1 → … · 已匯入 1 份 · 收到 3 份', …);
  it('cathayView failed: error banner naming password_file, lastOk 上次成功 昨天 06:41, dotLabel 國泰對帳單匯入失敗', …);
  it('cathayView latched / stale: warn banner; ok: dotLabel null', …);
  it('cathayView failed with files_seen 3, posted 2: runDetails 最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份', …);
  it('runDetails: latched → 已停用; stale after an ok run → 昨天 06:41 · 成功 · 收到 1 份 · 已匯入 1 份; no run → null; healthy → null', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/notifications/sources/cathay.spec.ts'` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement** per the signatures, the table and the Copy table. Taipei arithmetic: shift the epoch by +8 h and read UTC fields (Global Constraints).
- [ ] **Step 4: Run twice, once per host zone** — Expected: PASS both times:
  `cd frontend && TZ=UTC npx ng test --watch=false --include='src/app/components/notifications/sources/cathay.spec.ts'`
  `cd frontend && TZ=Asia/Taipei npx ng test --watch=false --include='src/app/components/notifications/sources/cathay.spec.ts'`
- [ ] **Step 5: Commit** `feat(notifications): 國泰 statement run rule, tasks and card view`.

### Task 3: Dividend and import-failure builders (parallel with 2 and 6)

tasks.md 3.1, 3.2. O1, C7, C9, C10, P4, P8.

**Files:**
- Create: `frontend/src/app/components/notifications/sources/portfolio.ts`, `portfolio.spec.ts`
- Modify: `sources/ledger-misc.ts` (export the existing `localStamp`, `:11`)

**Interfaces:**
- Consumes: `Dividend`, `RecalcStatus` (`models/portfolio.model.ts:202`, `:182`); `ImportAttempt` (`portfolio/import/import-math.ts:18`); `NotifTask`, `NotifTrackingRow`; `NavBadge` (`shell/navigation.ts:115`); `shortDate`; `localStamp`.
- Produces:
  ```ts
  export const DIVIDENDS_ROUTE = '/portfolio/dividends';
  /** `receipt_status === 'pending'` rows split by payment date: `due` = date ≤ today, `waiting` = later or no date. */
  export function splitPendingDividends(dividends: readonly Dividend[], today: string): { due: Dividend[]; waiting: Dividend[] };
  /** The one 要你確認 task for `due`, or null when empty. */
  export function dividendBatch(due: readonly Dividend[]): NotifTask | null;
  /** The 追蹤中 row for `waiting`, or null when empty. */
  export function dividendsRow(waiting: readonly Dividend[]): NotifTrackingRow | null;
  /** The 股利 dot for `due`, or null when empty. */
  export function dividendsBadge(due: readonly Dividend[]): NavBadge | null;
  /** The recalc chain failed (`kind !== 'quotes' && state === 'failed'`: the rule at notification-center.service.ts:240). */
  export function recalcFailed(recalc: RecalcStatus | null): boolean;
  /** One 系統 task when the recalc failed or the device's newest attempt has `ok === false`; the recalc wins when both. */
  export function importFailureTask(recalc: RecalcStatus | null, lastAttempt: ImportAttempt | undefined): NotifTask | null;
  ```

Fields (`source: 'portfolio'`, `amount: null`, `progress: null`, `secondary: []`; texts from the Copy table):

| Item | `id` | `from` / tier / icon | `primary` / `link` | `sortKey` | `fingerprint` |
|---|---|---|---|---|---|
| Dividend batch | `div:batch` | `dividends` / confirm / `pi-percentage` | `{ label: '查看', route: DIVIDENDS_ROUTE, queryParams: { status: 'pending' } }` | the earliest `payment_date` | `due` ids ascending, joined with `,` |
| Dividend row | `dividends` (`source: 'portfolio'`, `count` = n) | — | the same link | — | — |
| Import failure | `import:fail:${key}`; key = `recalc.started_at ?? 'recalc'` for the recalc, the attempt's `at` for the device | `imports` / system / `pi-upload` | `{ label: '查看', route: '/portfolio/import' }` | key | key |

The row's meta lists dated rows first (earliest first), then undated ones. The badge is `{ dot: true, tone: 'primary', label: '${n} 筆待確認收款' }`.

- [ ] **Step 1: Failing tests** (`portfolio.spec.ts`; today `2026-10-24`; `div(id, symbol, overrides)` builds a pending `Dividend` with an invented amount):
  ```ts
  it('two pending dividends past their payment date form one 要你確認 task linking to 股利 待收', () => {
    const { due, waiting } = splitPendingDividends([div(2, '9902', { payment_date: '2026-10-22' }), div(1, '9901', { payment_date: '2026-10-20' })], '2026-10-24');
    expect(waiting).toEqual([]);
    expect(dividendBatch(due)).toMatchObject({ id: 'div:batch', source: 'portfolio', from: 'dividends', tier: 'confirm', title: '股利待確認收款 · 2 筆',
      when: '最早 10/20 發放', meta: '9901、9902', amount: null, primary: { label: '查看', route: '/portfolio/dividends', queryParams: { status: 'pending' } },
      sortKey: '2026-10-20', fingerprint: '1,2' });
    expect(dividendsBadge(due)).toEqual({ dot: true, tone: 'primary', label: '2 筆待確認收款' });
  });
  it.each([
    ['2026-10-24', 'due'], ['2026-10-25', 'waiting'], [null, 'waiting'], [undefined, 'waiting'],
  ])('pending with payment_date %s is %s', …);                                      // open point 1: today is due
  it.each(['confirmed', 'legacy_unknown', 'unresolved', undefined])('receipt_status %s is in neither list', …);
  it('four symbols: meta 9901、9902、9903 等 1 檔; a symbol twice is listed once', …);
  it('nothing due: no task, no badge', …);
  it('dividendsRow: 股利待發放 · 3 筆, dated rows first then 日期未定, count 3, source portfolio', …);
  //   meta '9901 10/30 · 9902 11/05 · 9903 日期未定'; five rows → '… · 等 2 筆'
  it('a failed recalc chain: 最近一次匯入的重算失敗, id import:fail:<started_at>, tier system', …);
  it('a failed quotes run, or completed / partial / running / idle, is no failure', …);
  it('the device\'s newest attempt failed: 最近一次匯入失敗, meta 測試匯入.csv, id import:fail:<at>', …);
  it('both: one task, the recalc\'s', …);
  it('neither (null recalc, undefined or ok attempt): null', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/notifications/sources/portfolio.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** that spec and `ledger-misc.spec.ts` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): dividend batch, tracking row and import failure builders`.

### Task 4: The three sources in the service; dots; clock

tasks.md 3.1–3.3 (reads, dots), D8. O2, C8, P1–P3, P6.

**Files:**
- Modify: `frontend/src/app/services/notification-center.service.ts`, `notification-center.service.spec.ts`, `notification-center.rounds.spec.ts`, `frontend/src/app/components/notifications/notifications.integration.spec.ts` (harness only), `frontend/src/app/components/shell/navigation.spec.ts`, `navigation-state.service.spec.ts`, `frontend/src/app/components/sidebar/sidebar.spec.ts`

**Interfaces:**
- Consumes: Tasks 1–3; `allPages` (`portfolio/paging.ts:10`); `ImportHistoryService.attempts` / `lastFailed` (`import-history.service.ts:29–30`); `read()` and `mark()` (`:439`, `:451`).
- Produces:
  ```ts
  /** The round's clock; tests provide a fixed one. */
  export const NOTIF_CLOCK: InjectionToken<() => Date>;      // providedIn root, factory: () => () => new Date()
  // NotificationCenterService
  /** The 國泰 view of the last applied answers; null before the first one and while the source failed. */
  readonly cathay: Signal<CathayView | null>;
  /** Pending dates the API did not list (0 when none or unread): the uncounted note. */
  readonly cathayMore: Signal<number>;
  ```
  Behaviour, each a private data signal set to null on a failed read:
  - `dividends`: `allPages(offset => portfolio.getDividends({ limit: 100, offset }), 100, DIVIDEND_MAX_PAGES)` through one `read()`, with `export const DIVIDEND_MAX_PAGES = 50` (the helper's default, named so the test can cite it). The answer `{ items, capped }` is applied as: `capped` → the data signal is null and `mark('dividends', round, 'failed')` (C12); else the items and `'ok'`. The one `read()` wraps the whole stream, so its 20 s limit is for all pages together (C13). Tasks: `dividendBatch(due)`; `trackingRows` gains `dividendsRow(waiting)` last; `badges['dividends'] = dividendsBadge(due)`.
  - `imports`: `portfolio.getRecalcStatus()` through `read()` in every round; `mark('imports', …)`. While the applied answer is not null: task `importFailureTask(recalc, importHistory.attempts()[0])` built only when `recalcFailed(recalc) || importHistory.lastFailed()` (the dot's signal, unchanged). `readRecalc()`, `recalcGeneration` and the call in `refresh()` are removed.
  - `cathay`: `forkJoin({ pending: portfolio.getCathayPending(), runs: portfolio.getCathayRuns() })` through one `read()`; `mark('cathay', …)`. Tasks `cathayTasks(pending, runs, now)`; `cathay = cathayView(…)`.
  - `now`: a signal set from `NOTIF_CLOCK` in `startRound()`; `today` becomes `todayIso(now)`.
  - `badges['import']`: present when the import failure task exists or `cathay()?.dotLabel` is set; `{ dot: true, tone: 'red', label }` with `label` per P6. Pending dates never set it.
  - The Task 1 shim is deleted. The class comment's "five task sources" becomes eight.

- [ ] **Step 1: Update the harnesses** (no behaviour yet, so these fail until Step 3): the service spec's stub `PortfolioService` gains `getDividends`, `getCathayPending`, `getCathayRuns` as `calls()` subjects and `answer()` gains `dividends` (default `{ items: [], total: 0 }`), `cathayPending` (default `{ items: [], total: 0, truncated: false }`), `cathayRuns` (default a HEALTHY run: provide `NOTIF_CLOCK` and default to `{ latest: okRun, latest_ok: okRun }` with `okRun.run_at` 5 minutes before the clock, so unrelated tests gain no `cathay:run` task; `latest: null` is passed only by the missing-run test) and answers `recalc` in every round. `rounds.spec.ts` `take()` (`:65–71`) and the integration spec's `take()` / `flushSide()` (`:87–99`, `:482`, `:672`) expect and answer `/api/portfolio/dividends` (params `limit=100`, `offset=0`), `…/cathay-statement/pending`, `…/runs/latest` and `…/recalc/status` in every round with the same quiet defaults.
- [ ] **Step 2: Failing tests** (`notification-center.service.spec.ts` unless noted):
  ```ts
  it('two dividends past their payment date: one counted task, the 股利 dot labelled 2 筆待確認收款, tone primary', …);   // summary().total 1, tone 'primary'
  it('pending dividends with a later or no date are a 追蹤中 row after the ledger rows and are not counted', …);
  it('101 dividends are read in two requests (offset 0 and 100) and both pages count', …);
  it('cap boundary: DIVIDEND_MAX_PAGES full pages with total 5001 → no further request, 股利 failed, no div:batch, no row, no 股利 dot, the round settles', …);
  //   land 50 pages of 100 pending due rows each, total 5001; getDividends called exactly 50 times; failedSources() contains 'dividends'
  it('exactly DIVIDEND_MAX_PAGES pages (total 5000) is a complete scan: source ok, one batch of 5000', …);
  it('a missing latest run (latest null) is stale: cathay:run with 還沒有執行紀錄', …);                                   // the only test that passes latest: null
  it('settlement: page 1 of the dividends answers, page 2 is still open at READ_TIMEOUT_MS → 股利 failed for the round, the round settles, page 1\'s rows make no task', …);
  it('settlement: a round does not settle while a dividend page is open before the limit, and settles when the last page lands', …);
  it('a failed recalc chain → a 系統 task import:fail:… and the red 匯入 dot 最近一次匯入失敗', …);                         // replaces :223
  it('the device\'s last failed commit → the same task and dot with a healthy recalc chain', …);                        // replaces :237
  it('a pending 國泰 date is a counted 系統 task (red count) and does not light the 匯入 dot', …);                         // O2
  it.each([['failed', '國泰對帳單匯入失敗'], ['latched', '國泰對帳單匯入已停用']])('a %s run lights the 匯入 dot red with %s', …);
  it('a stale run (clock 2026-10-28T07:00+08:00, latest 2026-10-27T06:41+08:00) creates cathay:run and the dot 國泰對帳單今天沒有執行', …);
  it('an import failure and a bad run share one dot: 最近一次匯入失敗、國泰對帳單匯入失敗', …);
  it('the run verdict uses the round\'s clock: ok at 06:54, stale after the next round at 06:55', …);                    // P3: advance the provided clock, refresh
  it('portfolio API down: 股利, 匯入 and 國泰對帳單 are named, their dots and tasks leave (the device failure too), ledger tasks stay, the round settles', …);
  //   failedSources() → ['dividends', 'imports', 'cathay']; badges() has neither 'dividends' nor 'import'
  it('pending answers but runs fails: the 國泰 source fails as a whole — no cathay task, cathay() null', …);             // P1
  it('a 國泰 source that fails after it succeeded drops its tasks, its dot and cathayMore', …);
  it('cathayMore is total − listed (73, 50 → 23) and 0 when the source is unread', …);
  it('invalidate() reads the recalc status with every other source', …);                                                 // replaces :833 and the :301 clause
  it('a superseded round\'s recalc answer is dropped', …);                                                               // replaces the generation tests :390–405
  ```
  `navigation.spec.ts`: `summarizeGroupBadges(<portfolio group>, { dividends: { dot: true, tone: 'primary', label: '2 筆待確認收款' } })` → `{ dot: true, tone: 'primary' }`; with `import` red too → `tone: 'red'`. `navigation-state.service.spec.ts`: badges off with a `dividends` badge from the facade → no badge. `sidebar.spec.ts`: with the facade's badges `{ dividends: … }` the 股利 item renders its dot with the accessible label `2 筆待確認收款` and 匯入 renders none.
- [ ] **Step 3: Run** `--include='src/app/services/notification-center*.spec.ts'` — Expected: FAIL (new tests; rounds do not settle in the updated harness).
- [ ] **Step 4: Implement** per the Produces block.
- [ ] **Step 5: Run** `--include='src/app/{services/**/*,components/notifications/**/*,components/shell/**/*,components/sidebar/**/*,components/mobile-nav/**/*}.spec.ts'` and both `tsc` commands — Expected: PASS.
- [ ] **Step 6: Commit** `feat(notifications): dividends, import failure and 國泰 statement as task sources with their dots`.

### Task 5: 通知 page — 確認, 投資 rows, the truncation note (parallel with 7)

tasks.md 3.3 (確認), 3.4 (ack removes the task). C5, C6, P5.

**Files:**
- Modify: `frontend/src/app/components/notifications/notifications.ts`, `notifications.html`, `notifications.spec.ts`, `notifications.integration.spec.ts`

**Interfaces:**
- Consumes: `NotifCommand` kind `ackCathay`; `PortfolioService.ackCathayDay`; `center.cathayMore()`, `center.invalidate()`, `center.round()`; `guardedRetry` (`services/toast-retry.ts`), `knownWriteReason` (`accounting/http-errors.ts:71`), `toastText`; the existing `send` / `succeeded` / `failed` / `setBusy` / `markAnswered` (`notifications.ts:355–442`).
- Produces (page behaviour):
  - `send()` gains the `ackCathay` branch: `portfolio.ackCathayDay(command.tradeDate)`. Success → 完成 toast `已確認：${name}`, then the re-read sequence the failure path already uses (`markAnswered`; `const after = center.round(); center.invalidate(); setBusy(taskId, after)`), because an ack bumps no change signal. No 復原.
  - Error with status 404 → 完成 toast `${name}：已經不需要確認`, same re-read sequence.
  - Any other error → `console.error`, 失敗 toast `${name} 確認失敗` (reason appended with `toastText` only when `knownWriteReason(err)` is not null), `retry: guardedRetry(err, toasts, text, () => isBusy(taskId), () => send(taskId, command))`, same re-read sequence. The 入帳 wording of `failed()` is untouched for the two existing kinds.
  - `trackingRows`: under 全部 every row; under 財務 / 投資 the rows of that `source` (replaces the "hidden under 投資" rule at `:190–193`).
  - A second note, `p.notif-overflow.notif-overflow--portfolio`, after the ledger one: shown when the page is not pending, the chip is not 財務 and `center.cathayMore() > 0`; text and link from the Copy table, link `routerLink="/portfolio/import"` with the `notif-foot-link` class and the decorative `aria-hidden` ›. Never counted.
  - `NotificationsComponent` now injects `PortfolioService`; `notifications.spec.ts` provides a stub with `ackCathayDay`.

- [ ] **Step 1: Failing tests.** `notifications.spec.ts` (stub centre):
  ```ts
  it('投資 chip: only the dividend, import and 國泰 tasks remain, and the chip reads 投資，3 件', …);            // spec scenario "Source filter"
  it('a 系統 section holds the 國泰 rows with 確認 as a button named 確認：國泰對帳單 10/28 and 查看 as a link', …);
  it('確認 calls ackCathayDay("2026-10-28") once, the row is busy, then 已確認：國泰對帳單 10/28 and one invalidate()', …);
  it('a second press while busy sends nothing', …);                                                              // ackCathayDay called once
  it('404 reads as done: 國泰對帳單 10/28：已經不需要確認, no 重試, invalidate() called', …);
  it('500: 失敗 toast 國泰對帳單 10/28 確認失敗 with 重試; the raw body goes to console.error, not the toast', …);
  it('403: 失敗 toast without 重試', …);
  it('重試 re-sends the captured date; while the row is busy it re-raises the toast instead', …);
  it('the dividends tracking row shows under 全部 and 投資, the ledger rows under 全部 and 財務', …);            // trackingSum under 投資: '股利 3 筆'
  it('cathayMore 23: 還有 23 天的國泰對帳單未列出 · 到匯入頁查看 › under 全部 and 投資, not under 財務, and no count includes it', …);
  it('cathayMore 0 or the page still pending: no portfolio note', …);
  ```
  `notifications.integration.spec.ts` (real services, HttpTestingController; clock `2026-10-29T07:00+08:00`, latest run ok today; `pending` returns `2026-10-28` `held`):
  ```ts
  it('spec 3.4: 確認 posts the ack and the task disappears', async () => {
    // press 確認 on app-notif-row[data-task-id="cathay:2026-10-28"]
    // expectOne POST /api/portfolio/imports/cathay-statement/days/2026-10-28/ack → flush { trade_date: '2026-10-28', ack_status: 'held' }
    // a new round starts without any accounting write: answer it with pending { items: [], total: 0, truncated: false }
    // → the row is gone, summary().total dropped by 1, toast 已確認：國泰對帳單 10/28
  });
  it('a 500 that committed, then 重試 answered 404: done toast, the row leaves, two POSTs in total (one per press)', …);
  it('with the app\'s error-logging interceptor a failed ack is sent once', …);                                 // in the existing #411 describe block (:348)
  it('portfolio reads all fail: 無法讀取：股利、匯入、國泰對帳單 with the ledger rows and their count intact', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/notifications/notifications*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): 確認 for 國泰 statement dates, portfolio tracking rows and truncation note`.

### Task 6: 股利 page `?status=` (parallel with 2 and 3)

C7.

**Files:**
- Modify: `frontend/src/app/components/portfolio/dividend-list/dividend-list.ts`, `dividend-list.spec.ts`

**Interfaces:**
- Consumes: `status = signal<DividendStatusFilter>('all')` (`dividend-list.ts:174`); `DividendStatusFilter` (`dividend-rows.ts:8`); `ActivatedRoute`.
- Produces: the page's initial `status` is `route.snapshot.queryParamMap.get('status')` when it is `pending`, `confirmed` or `legacy`, else `all`. Read once at construction; the toggle does not write the URL.

- [ ] **Step 1: Failing tests** (`dividend-list.spec.ts`, with `provideRouter([])` and an `ActivatedRoute` stub whose snapshot carries the param; two fictional rows, `9901` pending and `9902` confirmed):
  ```ts
  it('?status=pending opens on 待收: the toggle\'s value is pending and only the pending row is listed', …);
  it('no param, or ?status=nonsense, opens on 全部', …);
  ```
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/dividend-list/dividend-list.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS (the file's existing tests included).
- [ ] **Step 5: Commit** `feat(dividends): open the list pre-filtered with ?status=`.

### Task 7: 國泰每日對帳單 status card on `/portfolio/import` (parallel with 5)

tasks.md 3.3 (status card). C11, D7.

**Files:**
- Create: `frontend/src/app/components/portfolio/import/cathay-status-card/cathay-status-card.ts`, `.html`, `.scss`, `.spec.ts`
- Modify: `frontend/src/app/components/portfolio/import/import.ts` (imports), `import.html` (third card in `#sideColumn`, after 最近匯入, `:275`), `import.spec.ts` (provide a stub `NotificationCenterService`)

**Interfaces:**
- Consumes: `center.cathay()`, `center.loaded()`, `center.loading()`, `center.failedSources()`, `center.refresh({ reason: 'open' })`, `center.invalidate()`; `PortfolioService.ackCathayDay`; `BentoComponent`, `BannerComponent`, `BtnComponent`; `injectToasts`, `guardedRetry`, `knownWriteReason`.
- Produces: `CathayStatusCardComponent`, selector `app-cathay-status-card`, no inputs. One `<app-bento title="國泰每日對帳單">` containing, by state:
  - not `loaded()` → `讀取中…` (`role="status"`);
  - `cathay` in `failedSources()` → `無法讀取國泰對帳單狀態` and a `重試` button calling `center.invalidate()` (disabled while `loading()`);
  - else from the view: the ok run line, or `<app-banner [tone] [heading] [detail]>` (not dismissible) followed by the `runDetails` line (`p.cathay-run-details`; time, result, files seen, posted — the csv-import requirement's four) and the `上次成功` line; the `需要你確認的日期` list (one row per day with a `確認` `app-btn`, `variant="soft"`, busy and disabled while its ack runs) or `沒有需要確認的日期`; `還有 {n} 天未列出` when `more > 0`.
  - 確認: `ackCathayDay(tradeDate)` once per press; success and 404 → the Task 5 完成 toasts then `center.invalidate()`; other errors → the Task 5 失敗 toast with `guardedRetry` (its `busy()` is this row's busy state), then `center.invalidate()`. Busy release (C14): keep `const n = center.invalidate()` — the NEW round's id — per trade date and release the row when `center.settledRound() >= n`. Round N itself releases it; no round N+1 is needed. A superseded round never settles, so when a later round M > N supersedes N the row is released when M settles (`M >= n`). Not the 通知 page's form: that page stores the round id read before its `invalidate()` and compares with `>` (`notifications.ts:415–417`, `:270`); copying its `>` here with N would leave the button and 重試 dead until some unrelated later round.
  - The constructor calls `center.refresh({ reason: 'open' })`.
  Styles use existing tokens only; the date rows reuse the side column's row rhythm (`.recent-row` as the model).

- [ ] **Step 1: Failing tests** (`cathay-status-card.spec.ts`; stub centre with writable signals, stub `PortfolioService`):
  ```ts
  it('healthy, nothing pending: 今天 06:41 · 已匯入 1 份, no banner, 沒有需要確認的日期', …);                 // csv-import scenario
  it('failed run with code password_file: an error banner naming the code, then 上次成功 昨天 06:41', …);       // csv-import scenario; banner tone attribute 'error'
  it('latched and stale runs show a warn banner', …);
  it('a held date lists 10/28 保留：名稱對不到代號 測試股票 with a button named 確認：國泰對帳單 10/28', …);
  it('確認 posts once, the button is busy, then 已確認：國泰對帳單 10/28 and invalidate()', …);
  it('404 → 國泰對帳單 10/28：已經不需要確認 as done; 500 → 國泰對帳單 10/28 確認失敗 with 重試', …);
  it('a failed run with files_seen 3 and posted 2 shows 最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份 beside the error banner', …);
  it('failed ack, invalidate() returned round N: while N is in flight 確認 is disabled and 重試 re-raises the toast; once settledRound() is N the button is enabled and 重試 posts again — no round N+1', …);
  //   stub: invalidate() returns 4; settledRound.set(3) → still busy; settledRound.set(4) → enabled; press 重試 → ackCathayDay called a second time
  it('a later round superseding N also releases: invalidate() returned 4, round 4 never settles, settledRound() becomes 5 → enabled', …);
  it('a successful ack keeps the row busy until settledRound() >= N, then the row is gone with the new view', …);
  it('more 23: 還有 23 天未列出', …);
  it('before the first round: 讀取中…; source failed: 無法讀取國泰對帳單狀態 and 重試 calls invalidate()', …);
  it('opening the card asks for a refresh with reason open', …);
  ```
  `import.spec.ts`: one test — the side column renders `app-cathay-status-card` after the 最近匯入 card, in both the panes and the stacked layout.
- [ ] **Step 2: Run** `--include='src/app/components/portfolio/import/**/*.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command and both `tsc` commands — Expected: PASS.
- [ ] **Step 5: Commit** `feat(import): 國泰每日對帳單 status card`.

### Task 8: Screens, tasks.md, full verification

**Files:**
- Modify: `openspec/changes/add-notification-center/tasks.md`

- [ ] **Step 1: Screens** per the Screenshot plan (fictional data only, through the demo preview stack or stubbed responses; never against production data).
- [ ] **Step 2: Live check** on the deployed preview: press 確認 once on a pending date if one exists (otherwise record "no pending date to try"), confirming the SPA token may write. Record the result in the PR body.
- [ ] **Step 3: Tick 3.1–3.4** with notes that say what C does not do. Use these texts, amended for anything that turned out differently:
  - 3.1 `(dividends are read by paging every row and filtering in the SPA — the API has no receipt_status filter; a scan that hits the 50-page cap or the 20 s limit fails the source instead of showing a partial result; a payment date of today counts as past; rows show symbols, not names; plus the 股利 dot from 2.4 and ?status=pending on the 股利 page)`
  - 3.2 `(the recalc status is now read in every round as source 匯入. Known limit, unchanged: the server status is in-process and the last failure is per device, so the task can differ between devices and disappear after an API restart)`
  - 3.3 `(run rule in Asia/Taipei, evaluated once per round; latched and stale are worded as warnings but 系統 rows and the 匯入 dot are red; pending dates do not light the 匯入 dot; beyond 50 dates one uncounted note. Not in C: 復原 for an ack — no endpoint; snooze — 5.3)`
  - 3.4 `(ack removes the task; 404 reads as done; one request per press; 07:00 / yesterday 06:41 with the 06:54 and 06:55 boundaries and a UTC-written variant)`
- [ ] **Step 4: Verify everything**, from the worktree root unless noted, and paste each command's tail into the PR body:
  - `cd frontend && npx ng test --watch=false` — all pass (the four known load-flaky tests may be re-run alone);
  - `cd frontend && npx tsc --noEmit -p tsconfig.app.json && npx tsc --noEmit -p tsconfig.spec.json`;
  - `cd frontend && npx ng build`;
  - `openspec validate add-notification-center --strict`.
- [ ] **Step 5: Commit** `docs(openspec): tick notification center 3.1–3.4` (path: `openspec/changes/add-notification-center/tasks.md`).

## Screenshot plan

Before / after at 1440 and 390, light and dark, fictional data. "Before" exists only where the screen exists on `origin/main` (通知, the sidebar, the import page); new states are after-only.

| # | Screen | State |
|---|---|---|
| 1 | 通知 | A dividend batch task in 要你確認 (`股利待確認收款 · 2 筆`) beside ledger tasks |
| 2 | 通知 | A 國泰 pending date in 系統: idle, 確認 busy, after done (row gone, 完成 toast), after a failure (失敗 toast with 重試) |
| 3 | 通知 | A bad run, one shot each: failed, latched, stale |
| 4 | 通知 | Truncated pending dates: 50 rows and the note `還有 23 天的國泰對帳單未列出` |
| 5 | 通知 | A failed portfolio source: `無法讀取：股利、匯入、國泰對帳單` with ledger rows intact |
| 6 | 通知 | 投資 chip selected (tasks, the dividends tracking row, the note; no ledger row) |
| 7 | 通知 | 追蹤中 open with the `股利待發放` row under 全部 |
| 8 | Sidebar (1440) and the tab bar + sub-nav (390) | 股利 dot (primary) and 匯入 dot (red); the 投資 tab's group dot from the 財務 tab |
| 9 | `/portfolio/import` | The card ok (`今天 06:41 · 已匯入 1 份`) |
| 10 | `/portfolio/import` | The card bad: failed (錯誤 banner with the code), latched and stale (注意 banner) |
| 11 | `/portfolio/import` | The card with pending dates and 確認, and with the truncation line |
| 12 | 股利 | Opened from the task: 待收 selected |

Check in a real browser: the card's height against the two cards above it at 1440, the 確認 targets at 390 (44 px), and that the toast clears the tab bar.

## Plan review

Reviewer: Multica AGENT-567 (dev-astra), at `b4662b1b`. Three findings, all accepted; cited lines re-checked in the worktree.

| # | Finding | Resolution |
|---|---|---|
| 1 | Dividend paging cap: `allPages` stops at 50 pages and returns `{ items, capped }` (`portfolio/paging.ts:13`, `:24–25`, `:31`); the plan let a capped scan look complete | C12: `capped` marks the `dividends` source failed for the round (named, no task, no row, no dot). C13: `read()`'s 20 s limit covers the whole paginated stream. Task 4 gains the cap-boundary test (50 full pages, total 5001), the exact-50 complete case and two settlement tests, and keeps the 101-row two-request test. Load estimate corrected in Global Constraints. O1 unchanged |
| 2 | Card busy release: `invalidate()` returns the new round N (`notification-center.service.ts:324–325`); "until `settledRound()` passes it" would wait for N+1 | C14: release at `settledRound() >= N`, with the difference from the 通知 page's pre-invalidation id and `>` spelled out (Task 7). Tests: failed ack → N settles → 確認 and 重試 work; a superseding later round also releases |
| 3 | Bad-run card dropped the latest run's result, files seen and posted, which the csv-import requirement lists | `CathayView.runDetails` (Task 2) and a details line beside the banner (Task 7), copy row added, conflicts row updated, tests for a failed run with `files_seen` 3 / `posted` 2 |

Minors: the shared harness default is a healthy latest run, `latest: null` only in the missing-run test (Task 4 Step 1); the time-zone Review Focus item now runs the focused 國泰 spec under `TZ=UTC` and `TZ=Asia/Taipei` (Task 2 Step 4).
