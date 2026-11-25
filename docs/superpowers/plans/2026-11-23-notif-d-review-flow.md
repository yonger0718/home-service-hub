# Notification Center PR D — Review Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/notifications/recon/:statementId` shows one statement's open questions one at a time (right pane of 通知 at ≥ 1024 px, full screen below), answers them through the existing inbox contract, and offers 復原 on a toast.

**Architecture:** Frontend only. A pure view module (chip, headline, comparison rows) and a pure sequence reducer carry the logic; one `ReconFlowComponent` renders a step and reuses the inbox panel's label function, match sheet and record sheet. `/notifications` and `/notifications/recon/:id` become one matched route so the list stays mounted. A root `ReconUndoService` owns the 復原 toast so it survives the flow closing.

**Tech Stack:** Angular 21 standalone / signals / OnPush, vitest (`cd frontend && npx ng test --watch=false --include='<glob>'`), `npx tsc --noEmit -p tsconfig.app.json` and `-p tsconfig.spec.json`, `npx ng build`, `openspec validate add-notification-center --strict`.

**Spec:** `openspec/changes/add-notification-center/` — `design.md` D5; `specs/frontend-notification-center/spec.md` requirements "One case at a time reconciliation" and "Related questions handled together"; `specs/frontend-ui-primitives/spec.md` "Toast service" scenario "Success with undo"; `tasks.md` §4. Design refs: `design_handoff_v3/design_refs/ReconPhone.dc.html`, `NotifDesktop.dc.html` (review pane), `design_handoff_v3/README.md`. Survey with file:line facts: `/home/opc/reports/notif-d-survey-20261123.md` (outside the repo; quote facts from it, do not commit it).

`FE/` below = `frontend/src/app/`.

## Global Constraints

- Frontend only: no file under `services/` changes. No new dependency.
- Not in D (PR E): `adjust_transfer`, `pair_transfer`, 改金額 on ambiguous, snooze. `InboxItem.transfer` is not added.
- Copy is Traditional Chinese exactly as written in this plan. Hexes from the artboards are never copied: use existing `--app-*` tokens (`--app-primary`, `--app-primary-soft`, `--app-primary-ink-bg`, `--app-surface`, `--app-text`, `--app-text-muted`; find the border / soft-background tokens already used by `notifications.scss`).
- Text contrast ≥ 4.5:1 in light and dark; touch targets ≥ 44 px on phone; no horizontal scroll at 320 px.
- Tests use fictional merchants and amounts (reuse `FE/components/accounting/inbox/testing.ts`: `makeItem`, `makeInbox`, `makeInboxAccount`, `makeCard`, `applied`).
- Answer labels come only from `buttonLabel()` in `FE/components/accounting/inbox/inbox-view.ts`; the answer string sent to the server is built exactly as the panel builds it (`buttonLabel` / `recordAnswer` / `matchAnswer`).
- One `POST /reconciliation/inbox/apply` per press; the flow never calls `NotificationCenterService.invalidate()` (an inbox write already starts a round through `InboxService.changed`).
- Commits: explicit paths only, no amend, trailer `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Rulings made while planning (spec vs code)

Each is a decision the spec text does not settle or settles against what the API can do. Task 6 writes them into the spec delta.

1. **Related questions are not sent in one request.** The server rejects a batch with mixed questions (422 `mixed questions`) and does not offer `accept_bank_amount` for `ledger_only`. The flow shows the pair once, sends the first case only and re-reads the inbox. What happens to the second question is the server's doing (`engine._supersede_siblings`: an answer that writes coverage supersedes the other open cases naming the touched entry): 改成銀行的金額, and 對到… when the owner picks that same entry, close it; 對到… with a different entry, a refused answer, and 不是同一筆 leave it open, and it is then asked next (a follower is ordered directly after its leader). Plan review AGENT-578 corrected the mechanism (not a reconcile) and the `match_candidate` case.
2. **One step = one inbox item** (not a merged `group_key` card). Progress and the end state count items, matching the task's 「N 筆要你決定」. Sheets get a one-item `InboxCard`.
3. **The sequence is re-read after every answer** (`InboxService.get({ accountId })`), so versions are fresh and anything a rule or reconcile closed drops out and counts as handled.
4. **Account id** comes from a first unfiltered read (`limit: 1000`, as the centre does) filtered by `item.statement.id`.
5. **Header counts** are the task's `progress` (`center.tasks()` id `recon:<statementId>`): 「已對上 k / n 行」. No `claimed / lines` exists in the API. Absent → omitted.
6. **Learn switch** shows only when the item has a `rule_hint` and one of its actions is a `learnableAnswer`; label `learnLabel()` (「以後「X」都這樣」). It applies only to a learnable answer. 金額不同也算 is not offered in the flow (`amount_exact: true`, except `accept_bank_amount` which is always `false` — use `inboxLearn(on, false, button)`); the record sheet keeps its own controls and receives the switch as `learnDefault`.
7. **`part_of` items** (#392): no answer is solid (as in the panel); keys still work by position; the warning text from `questionText()` shows in the note box.
8. **復原** reverts every `action_id` of the answer, newest first, then (a) switches off the rule the answer taught, if any (`PATCH /reconciliation/owner-rules/{id}` `enabled: false`) — an enabled rule would repeat the undone answer at the next reconcile — and (b) asks the server to reconcile the statement (`StatementsService.reconcile`), because a revert reopens only the answered case and leaves the questions it had superseded closed until the next reconcile (AGENT-578 P1; `engine.py` `_supersede_siblings` docstring, `test_reconciliation_actions_engine.py:242-268`). Rule first, reconcile second. A failed rule switch or reconcile does not undo the revert; the toast says what was not done.
9. **End-state buttons** are 「繼續對帳」 and 「回到通知」 at every width.
10. **Esc** closes the flow only when no sheet, dialog or toast handled it (`isHandledKey`).

## Review Focus

1. Answer pressed twice quickly (double tap, or key 1 held): exactly one apply request; the second press is ignored while busy.
2. Apply succeeds but the re-read fails: the owner sees that the answer went through and can retry the read; no second apply is sent.
3. The statement has no open question when opened (stale link, or another tab answered them): end state, not a blank pane or a spinner forever.
4. A refused member (`stale_case`, `case_closed`): inline reason, the list is re-read, the flow does not advance past a still-open item.
5. Keys while a sheet is open or a text field has focus: nothing answers, skips or closes behind the sheet.

Each has a named test in Tasks 2–4.

---

### Task 1: Step view helpers

**Files:**
- Create: `FE/components/notifications/recon-flow/recon-flow-view.ts`
- Test: `FE/components/notifications/recon-flow/recon-flow-view.spec.ts`

**Interfaces:**
- Consumes: `InboxItem`, `InboxQuestion` (`inbox.model.ts`); `questionText`, `learnableAnswer` (`inbox-view.ts`); `formatMoney` (`accounting/format`), `slashDate` (`accounting/dates`).
- Produces:

```ts
export function flowChip(question: InboxQuestion): string;
export function flowHeadline(item: InboxItem, currency: string): string;
/** Extra line under the grid: the part-of warning, the transfer hint, or the item's note; null when none. */
export function flowNote(item: InboxItem, currency: string): string | null;
export interface CompareRow { readonly label: '日期' | '商家' | '金額'; readonly bank: string; readonly ledger: string; readonly differs: boolean; readonly bankMissing: boolean; readonly ledgerMissing: boolean; }
export function compareRows(item: InboxItem, currency: string): readonly CompareRow[];
/** The ledger entry a step compares against: `item.entry`, else the first candidate for amount_differs / date_differs. */
export function flowEntry(item: InboxItem): { id: number; date: string; title: string; amount: string } | null;
/** One-item card for the match and record sheets. */
export function flowCard(item: InboxItem, account: InboxAccount): InboxCard;
export function flowLearnable(item: InboxItem): boolean;
```

Copy (exact):

| question | chip | headline |
|---|---|---|
| `bank_only` | 銀行有，你沒記 | `<line.merchant> <signed line amount>` |
| `ledger_only` | 你記了，銀行沒有 | `<entry.title> <signed entry amount>` |
| `amount_differs` | 金額不同 | line amount < 0: 「銀行多扣了 $d」 when \|bank\| > \|entry\|, else 「銀行少扣了 $d」; line amount ≥ 0: 「銀行多入了 $d」 / 「銀行少入了 $d」 (d = absolute difference, `formatMoney`) |
| `date_differs` | 日期不同 | 「同一筆，晚了 N 天入帳」 (line date after entry date), 「同一筆，早了 N 天入帳」, or 「同一筆，日期不同」 when a date is missing |
| `transfer` | 轉帳／繳費 | 「轉帳或繳費，你沒記」 |
| `ambiguous` | 多筆可能 | 「有 N 筆可能是它」 (N = `candidates.length`) |

- `flowNote`: `bank_only` with `part_of` → `questionText(item, currency)`; `transfer` → 「在時間軸記一筆轉帳，或對到已記的。」; else `item.note || null`.
- `compareRows`: three rows 日期 / 商家 / 金額. Bank side from `item.line` (`slashDate(date)`, `merchant`, signed amount); ledger side from `flowEntry(item)` (`slashDate(date)`, `title`, signed amount). A missing side shows 「沒有對應記錄」 in the 商家 row and 「—」 in the other two, with `bankMissing` / `ledgerMissing` true. `differs` is true only when both sides exist and the row is 日期 with different dates or 金額 with different numbers; 商家 never differs.
- `flowCard`: `{ key: String(item.case_id), question, account, items: [item], actions: item.actions, rule_hint: item.rule_hint, learnable: flowLearnable(item) }`.

- [ ] **Step 1: Write the failing tests** — one `it` per table row for chip + headline (include: expense −2,096 vs −2,066 → 「銀行多扣了 $30」; income 500 vs 480 → 「銀行多入了 $20」; line 2026-09-28 vs entry 2026-09-20 → 「同一筆，晚了 8 天入帳」; entry after line → 「早了」); `flowEntry` falls back to `candidates[0]` for `amount_differs` and returns null for `bank_only`; `compareRows` for `bank_only` has `ledgerMissing` on all rows and 「沒有對應記錄」; `amount_differs` marks only 金額 as `differs`; `flowNote` for a `part_of` item equals `questionText`; `flowCard` has one item and `learnable` false without a `rule_hint`.
- [ ] **Step 2: Run** `cd frontend && npx ng test --watch=false --include='src/app/components/notifications/recon-flow/recon-flow-view.spec.ts'` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the functions above. `InboxCandidate`'s id / title field names: read `inbox.model.ts` and map to the `flowEntry` shape.
- [ ] **Step 4: Run the same command** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): step copy and comparison rows for the review flow`.

### Task 2: Sequence reducer

**Files:**
- Create: `FE/components/notifications/recon-flow/recon-flow-state.ts`
- Test: `FE/components/notifications/recon-flow/recon-flow-state.spec.ts`

**Interfaces:**
- Consumes: `InboxItem`, `isAskQuestion`, `flowEntry` (Task 1).
- Produces:

```ts
export interface FlowState {
  /** Case ids in first-seen order: the opening snapshot, then any case that appeared later (a revert). */
  readonly order: readonly number[];
  /** Still-open ask items of the statement from the latest read, by case id. */
  readonly open: ReadonlyMap<number, InboxItem>;
  readonly skipped: ReadonlySet<number>;
  /** Case id on screen; null in the end state. */
  readonly cursor: number | null;
}
export function askItems(items: readonly InboxItem[], statementId: number): InboxItem[]; // !folded && isAskQuestion && statement.id
export function startFlow(items: readonly InboxItem[]): FlowState;            // cursor = first step
export function afterRead(state: FlowState, items: readonly InboxItem[]): FlowState; // refresh `open`, append new ids; cursor kept
/** Case ids hidden behind an earlier step: a `ledger_only` item whose entry id equals `flowEntry()` of an open non-ledger_only item. Maps follower → leader. */
export function followers(state: FlowState): ReadonlyMap<number, number>;
export function steps(state: FlowState): number[];                             // open ids in `order`, followers removed
export function advance(state: FlowState): FlowState;                          // next step after cursor that is not skipped; none → cursor null
export function skip(state: FlowState): FlowState;                             // add cursor to skipped, then advance
export function move(state: FlowState, delta: -1 | 1): FlowState;              // prev / next step incl. skipped; no wrap; no end state
export function resume(state: FlowState): FlowState;                           // clear skipped, cursor = first step (null if none)
export function goTo(state: FlowState, caseId: number): FlowState;             // cursor = caseId if it is a step
export function progress(state: FlowState): { position: number; total: number; handled: number; remaining: number };
```

- `progress`: `total = order.length`; `position` = 1-based index of `cursor` in `order` (0 in the end state); `handled` = ids in `order` not in `open`; `remaining = open.size`.
- `advance` after the cursor's case left `open` uses the cursor's place in `order` to find the next step.

- [ ] **Step 1: Write the failing tests:**
  - `startFlow` of 10 ask items → cursor = first id, `progress` `{position:1,total:10,handled:0,remaining:10}`.
  - answering advances: `afterRead` without case 1 then `advance` → cursor 2, `position 2`, `handled 1` (spec "Answering moves on").
  - related pair: `amount_differs` (entry 812 via `candidates[0]`) + `ledger_only` (entry 812) → `steps` omits the follower; after a read where both are gone, `handled` is 2; after a read where only the leader is gone, the follower is a step again.
  - `skip` on the last step → cursor null; `resume` → cursor = first step, skipped empty.
  - `move(+1)` on the last step and `move(-1)` on the first are no-ops; `move` lands on a skipped step.
  - a case id that appears in a later read (revert) is appended to `order` and `goTo` lands on it.
  - `startFlow([])` → cursor null, total 0 (Review Focus 3).
  - stop midway: 10 items, 4 gone → `{handled:4, remaining:6}` (spec "Stop midway").
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/notifications/recon-flow/recon-flow-state.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** as pure functions (no Angular imports).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): sequence reducer for the review flow`.

### Task 3: Undo service

**Files:**
- Create: `FE/components/notifications/recon-flow/recon-undo.service.ts`
- Test: `FE/components/notifications/recon-flow/recon-undo.service.spec.ts`

**Interfaces:**
- Consumes: `ToastService.scope(DestroyRef)` (`FE/services/toast.service.ts`), `InboxService.revert(actionId)`, `inboxErrorMessage`, the not-revertible codes as `handled-list.ts` reads them.
- Produces:

```ts
@Injectable({ providedIn: 'root' })
export class ReconUndoService {
  /** Bumped after a 復原 finished (success or not): the case to show again. */
  readonly reverted: Signal<{ readonly caseId: number; readonly seq: number } | null>;
  /** Raise 完成 with 復原 for one applied answer. `actionIds` in apply order; `rulePattern` when the answer taught a rule. */
  offer(answer: string, caseId: number, actionIds: readonly number[], rulePattern: string | null): void;
}
```

Behaviour:
- `offer` → `done('已處理：' + answer, { undo })`. With no action id (all replayed without an id) → `done` without undo.
- undo → `revert` each id sequentially, newest first; stop at the first failure.
- all reverted → `done('已復原')`, or with `rulePattern`: `done('已復原。「' + rulePattern + '」的規則還在，可在記帳設定關掉')`.
- a failure → `error('復原沒成功：' + message)` where message is 「之後已有其他變更，無法復原」 for `not_revertable` / `not_revertible`, else `inboxErrorMessage(err)`; no 重試.
- `reverted` is bumped in both outcomes (a partial revert still reopened something).
- The service's toast scope is created from the root injector's `DestroyRef`, so the action outlives the flow component.

- [ ] **Step 1: Write the failing tests** (HttpTestingController + real `ToastService`): `offer` shows 「已處理：改成銀行的 -$2,096」 with a 復原 action; running it posts `/actions/902/revert` then `/actions/901/revert` for ids `[901, 902]`, then shows 「已復原」 and bumps `reverted` with the case id; with a rule pattern the text names it; a 422 `not_revertable` on the first call sends no second call and shows 「復原沒成功：之後已有其他變更，無法復原」 as an error toast; `offer` with `[]` has no action.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/notifications/recon-flow/recon-undo.service.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): undo toast for a reconciliation answer`.

### Task 4: Flow component

**Files:**
- Create: `FE/components/notifications/recon-flow/recon-flow.ts`, `recon-flow.html`, `recon-flow.scss`
- Test: `FE/components/notifications/recon-flow/recon-flow.spec.ts`

**Interfaces:**
- Consumes: Tasks 1–3; `InboxService.get/apply` + `changed`; `AppInboxMatchSheetComponent` (`[card] [account] (confirmed) (closed)`), `AppInboxRecordSheetComponent` (`[card] [currency] [learnDefault] [amountAnyDefault] (confirmed) (closed)`); `buttonLabel`, `recordAnswer`, `matchAnswer`, `lineKind`, `learnLabel`, `inboxLearn`, `learnableAnswer`, `inboxErrorMessage`; `NotificationCenterService.tasks()`; `AccountingService.markEntriesChanged()`; `LayoutModeService.mode()`; `isHandledKey` (`accounting-ui.ts`).
- Produces: `ReconFlowComponent`, selector `app-recon-flow`, inputs `statementId: number` (required), output `closed: void`. Host: `role="region"`, `aria-label="對帳"`, `tabindex="-1"`, `(document:keydown)`.

Behaviour:
- **Load:** on `statementId` change → `inbox.get({ limit: 1000 })`; find the account whose items include the statement; `startFlow(askItems(...))`. Later reads use `inbox.get({ accountId })`. While the first read is pending: 「讀取中…」. Read failure: `<app-banner>` error 「對帳資料讀取失敗」 with a 重試 button. No item for the statement → end state with total 0.
- **Header:** small line 「對帳 · <account name>」; title 「至 <M/D of period_end> 帳單」; sub 「已對上 k / n 行 · 這次要你決定 <total> 筆」 (first part only when the task has `progress`); close button `aria-label="關閉對帳"` at ≥ 1024 px, back link `aria-label="返回通知"` below (both emit `closed`). Link 「看整張帳單」 → `/accounting/accounts/<accountId>/statements/<statementId>`.
- **Progress:** segmented bar, one segment per `order` entry up to 20 (above 20 a plain bar with the same fraction); done = primary, current = text colour, rest = soft; text 「第 n / total 筆」 (phone: 「n / total」); in the end state 「handled / total 筆」. The bar is `aria-hidden`; the text is the accessible progress.
- **Step:** chip, headline (`<h2>`), comparison grid as a `<table>` with column headers 「銀行帳單」 「你的記錄」 and row headers; cells with `differs` get the highlight class (background `--app-primary-soft`, weight 800); missing cells muted. Pair note when the step has a follower: 「你這筆 <signed entry amount> 也出現在「你記了，銀行沒有」，選了就一起處理。」 Then `flowNote` when present.
- **Answers:** `item.actions` in order as full-width buttons with `buttonLabel`; first is solid unless the item has `part_of`; at ≥ 1024 px each has a `<kbd>` 1 / 2 / 3. `record` opens the record sheet (`learnDefault` = switch state, `amountAnyDefault` false), `match_candidate` the match sheet; others apply at once. All answers disabled while busy.
- **Learn switch:** `<button role="switch" aria-checked>` labelled `learnLabel(hint, …)`, sub line 「之後同樣的行自動處理，可在記帳設定關掉」; shown only when `flowLearnable(item)`; off by default; reset on every step change. On phone it sits above the answers, on desktop below.
- **Apply:** body `{ cases: [{case_id, case_version}], action, params, answer, learn }` — `learn` = `inboxLearn(switchOn, false, action)` when `learnableAnswer(question, action)` and a hint exists, else `null`; for `record` the sheet's `result.learn`; for match `null`. Then:
  - HTTP error → inline `<p role="alert">` 「沒成功：<inboxErrorMessage(err)>」 under the answers; stay.
  - result → `accounting.markEntriesChanged()`; re-read with `accountId`; then `afterRead`; if the cursor's case is no longer open → `ReconUndoService.offer(answer, caseId, actionIds, rule_id !== null ? hint.merchant_pattern : null)` and `advance`; if it is still open (refused) → inline 「沒成功：<inboxErrorMessage(code)>」 and stay. When the response has a `rule_error`, the answer itself went through: advance as usual, but raise `toasts.error('答案已記下，但規則沒存：' + inboxErrorMessage(rule_error))` in place of the 完成 toast (no 復原 on that one).
  - re-read failure after a successful apply → inline 「答案已送出，但清單讀取失敗」 with 重試 (re-read only; never re-apply).
- **Busy:** one flag from press until the re-read settles; presses and keys are ignored while it is set.
- **Skip:** text button 「略過，稍後再看」 → `skip`.
- **Keys** (active at every width; only the on-screen hint and the `<kbd>` marks are limited to ≥ 1024 px): ignore when `isHandledKey(event)`, a modifier is down, busy, a sheet is open, any `[role=dialog]` / `[aria-modal=true]` is open, or `document.activeElement` is an `INPUT` / `TEXTAREA` / `SELECT` / contenteditable / `[role=combobox|listbox|spinbutton]`. `1` `2` `3` → that answer (if it exists); `s` / `S` → skip; `ArrowLeft` / `ArrowRight` → `move(∓1)`; `Escape` → `closed`. Every handled key calls `preventDefault()`. Hint at ≥ 1024 px: 「1–3 選擇 · S 略過 · ← → 上下筆 · Esc 關閉」.
- **External changes:** an effect on `ReconUndoService.reverted()` re-reads and `goTo(caseId)`.
- **End state** (cursor null): check tile; `remaining > 0`: 「已處理 H 筆，還有 R 筆」 + 「剩下的會留在通知裡，帳單對完才會從數字中拿掉。」 + buttons 「繼續對帳」 (`resume`) and 「回到通知」 (`closed`); `remaining === 0 && total > 0`: 「這張帳單都決定好了」 + 「回到通知」; `total === 0`: 「這張帳單沒有要你決定的了」 + 「回到通知」. `H` = `handled`.
- **Focus:** when the step changes, focus moves to the headline (`tabindex="-1"`) so a screen reader announces the new question; entering the end state focuses its heading.

- [ ] **Step 1: Write the failing tests** (HttpTestingController; `LayoutModeService.set`):
  - renders chip, headline, grid and three answers for an `amount_differs` item; first answer has the solid class; with `part_of` none has it.
  - **answering advances** (spec): press key `1` on question 1 of 10 → one `POST /inbox/apply` with one case and `answer` = the button label; after the re-read without case 1 the second question shows with 「第 2 / 10 筆」.
  - **double press** (Review Focus 1): two clicks before the response → one request.
  - **related pair** (task 4.5, per ruling 1): the pair shows once with the note; the apply carries the leader's case id only; a re-read without both shows the third question and the end state later reports both as handled; a re-read that still has the follower shows it as the next question.
  - **refused**: result `refused` / `stale_case` → inline 「沒成功：…」, still on the same question after the re-read (Review Focus 4).
  - **re-read fails** (Review Focus 2): inline 「答案已送出，但清單讀取失敗」; 重試 sends a GET, no POST.
  - **learn**: switch hidden without `rule_hint`; on + `dismiss` on `bank_only` → `learn: {amount_exact: true}`; on + `accept_bank_amount` → `{amount_exact: false}`; on + a non-learnable answer → `learn: null`; switch is off again on the next step.
  - **record / match**: `record` opens the record sheet with `learnDefault` = switch state; confirming posts `action: 'record'` with `category_id` and the sheet's `learn`; `match_candidate` posts `entry_ids`.
  - **keys** (Review Focus 5): `s` skips; arrows move without a request; `Escape` emits `closed`; with focus in an `<input>` or a sheet open, `1`, `s` and `Escape` do nothing; a `defaultPrevented` Escape does not close.
  - **end state**: last question skipped → 「已處理 4 筆，還有 6 筆」 with 繼續對帳 and 回到通知; 繼續對帳 returns to the first open question; all answered → 「這張帳單都決定好了」.
  - **stale link** (Review Focus 3): no ask item for the statement → 「這張帳單沒有要你決定的了」.
  - **undo wiring**: after an applied answer the toast 「已處理：…」 has 復原; when `reverted` fires the flow re-reads and shows that case again.
  - **read failure**: banner + 重試.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/notifications/recon-flow/recon-flow.spec.ts'` — Expected: FAIL.
- [ ] **Step 3: Implement** component, template and styles (layout numbers from the survey §9: desktop grid `72px 1fr 1fr`, phone `56px 1fr 1fr`; answers min-height 48 / 50 px; switch 52×32).
- [ ] **Step 4: Run** the spec, then `npx tsc --noEmit -p tsconfig.app.json && npx tsc --noEmit -p tsconfig.spec.json` — Expected: PASS, no type errors.
- [ ] **Step 5: Commit** `feat(notifications): one-question-at-a-time review flow`.

### Task 5: Route, pane and list wiring

**Files:**
- Modify: `FE/app.routes.ts` (+ its spec if one exists), `FE/components/notifications/notifications.ts`, `notifications.html`, `notifications.scss`, `notifications.spec.ts`, `FE/components/notifications/notif-row/notif-row.ts` (+ html, scss, spec), `FE/components/notifications/sources/recon.ts` + `recon.spec.ts`, `FE/components/shell/shell.ts` + spec.

**Interfaces:**
- Consumes: `ReconFlowComponent` (Task 4).
- Produces:

```ts
// app.routes.ts — `notifications` and `notifications/recon/<digits>` as one route; anything else under notifications/ does not match.
export function notificationsMatcher(segments: UrlSegment[]): UrlMatchResult | null; // posParams: { statementId }
// sources/recon.ts
export function reconTaskRoute(accountId: number, statementId: number): string; // `/notifications/recon/${statementId}`
// notif-row.ts
readonly selected = input(false); // row highlighted, primary control reads 「對帳中」 and has aria-current="true"
```

Behaviour:
- `NotificationsComponent` reads `statementId` from `ActivatedRoute.paramMap` (as `accounts-page.ts` does).
- `mode() === 'panes'` and an id: list and `<app-recon-flow>` side by side (list `flex: 5 1 420px`, pane `flex: 6 1 480px`, pane on `--app-surface` with the page's card radius and shadow, sticky to the top of the viewport and scrolling inside when taller). Otherwise with an id: only the flow renders (page header, chips and list are not rendered). Without an id: the page as today.
- `(closed)` → `router.navigate(['/notifications'])`; after closing at panes width focus returns to that statement row's primary control.
- The row whose task id is `recon:<statementId>` gets `selected`.
- Opening the flow moves focus to the flow host.
- Shell: the floating 記一筆 is hidden on `/notifications/recon/…` when the layout is not `panes`.
- The old statement page stays reachable through the flow's 「看整張帳單」 link.

- [ ] **Step 1: Write the failing tests:** matcher accepts `/notifications` and `/notifications/recon/12`, rejects `/notifications/recon/abc`, `/notifications/recon` and `/notifications/x`; `reconTaskRoute(3, 12) === '/notifications/recon/12'` (update `recon.spec.ts`); page at panes with id 12 renders both the list and `app-recon-flow` and marks row `recon:12` selected with 「對帳中」; page at phone with id renders the flow and no list; `closed` navigates to `/notifications`; without id no flow; shell hides the FAB on `/notifications/recon/12` at phone and shows it at panes.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/notifications/**/*.spec.ts' --include='src/app/components/shell/*.spec.ts' --include='src/app/*.spec.ts'` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command plus both `tsc` commands — Expected: PASS.
- [ ] **Step 5: Commit** `feat(notifications): open the review flow beside the list or full screen`.

### Task 6: Spec text, task ticks, whole-suite check

**Files:**
- Modify: `openspec/changes/add-notification-center/specs/frontend-notification-center/spec.md`, `design.md` (D5), `tasks.md`.

- [ ] **Step 1: Spec delta.** In "Related questions handled together": the flow shows the pair once with the note and applies the answer to the first question; the second is not asked again when the answer closes it, and is asked on its own when it stays open. Replace the scenario's THEN with: one apply request SHALL carry the `amount_differs` case, and after the list is read again the `ledger_only` question SHALL not be shown. In "One case at a time reconciliation": header counts are 已對上 k / n 行 from the statement task; end-state buttons 繼續對帳 and 回到通知; Esc closes only when no sheet or dialog is open. D5: `claimed / lines` → the task's matched / total; related cases as above; one step per inbox item. Keep every requirement ≤ 500 characters.
- [ ] **Step 2: Tick 4.1–4.5** with notes: 4.1 (labels were already one function, `buttonLabel()` in `inbox-view.ts`; the flow imports it and adds chip / headline copy in `recon-flow-view.ts`), 4.3 (per ruling 1: one request with the first case; the server closes the second), 4.5 (the pair test asserts one case id and the skipped follower), and 1.1's 復原 note (wired here through `ReconUndoService`).
- [ ] **Step 3: Run** `openspec validate add-notification-center --strict` — Expected: valid.
- [ ] **Step 4: Run** `cd frontend && npx ng test --watch=false` and `npx ng build` — Expected: all pass (known load-flaky tests listed in the ledger pass alone), build ok.
- [ ] **Step 5: Commit** `docs(openspec): review flow as built (notification center PR D)`.

## After the tasks (controller, not an implementer task)

- Browser evidence at 1440 / 1100 / 390, light and dark, before / after, including focus order, key handling, contrast probes and the phone full-screen flow.
- Release needs the Caddy `@hub_spa` matcher extended with `/notifications/recon/[0-9]+` before the SPA publish.
