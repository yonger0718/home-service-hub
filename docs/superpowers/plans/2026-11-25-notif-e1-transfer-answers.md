# Notification center PR E1 — transfer and amount answers in the review flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the one-question review flow (`/notifications/recon/:statementId`) offers 改成帳單的轉帳, 配成轉帳 (with a candidate picker) and 改金額 on ambiguous questions, through `POST /reconciliation/cases/{id}/actions`, with 復原 and plain-word refusals (tasks.md 5.1 and the 5.4 tests for it).

**Architecture:** frontend only. The inbox already sends `item.transfer`; the SPA model gains it. A new `InboxService.caseAction()` posts to the case-actions endpoint. The flow's answer list becomes a list of `FlowAnswer`s (the inbox buttons plus three case actions); case actions that need a choice open one new picker sheet. The apply path is generalised so a case action shares busy / re-read / 復原 / error handling with the inbox answers.

**Tech Stack:** Angular 21 standalone components, signals, OnPush; Vitest via `npx ng test`.

**Spec:** `openspec/changes/add-notification-center/specs/frontend-notification-center/spec.md` (Requirement "Transfer and amount answers"; "One case at a time reconciliation") and `design.md` D5. Survey with file:line evidence: `/home/opc/reports/notif-e-survey-20261125.md` §A.

## Global Constraints

- Frontend only; no backend change. Endpoint: `POST /api/accounting/reconciliation/cases/{case_id}/actions`, body `{action, params}` only (no `case_version`, `answer`, `learn`); 201 applied / 200 replayed; response is a `ReconciliationAction` whose `id` is the action id. WRITE scope (the SPA token has it).
- Params exactly: `adjust_transfer {entry_id, line_id}`; `pair_transfer {peer_line_id}` (= the chosen peer candidate's `line_id`); `adjust_amount {entry_id, line_id}` (`line_id` = `item.line.id`).
- Inbox data exactly: `item.transfer: null | { adjust_transfer: null | {entry_id: number|null, line_id: number, candidate_entry_ids: number[]}, pair_transfer: null | {peer_candidates: {line_id, statement_id, account_name, posted_date, amount}[]} }`. `transfer` may be absent on an older API: treat absent as null.
- Refusals arrive as HTTP errors (409 `{detail:"<code>: …"}` / `{message}`, 422 `{detail:[{loc,msg:"<code>: …"}]}`) and are shown through `flowError()` in plain words; never a code (D5 "Refusals", owner review 2026-11-24).
- 復原 works unchanged through `ReconUndoService.offer({answer, caseId, actionIds:[out.id], accountId, statementId, rule:null})`; a revert reopens the same case id.
- No 以後都這樣 on these answers (`learnableAnswer` stays as is; the switch line keeps naming only the learning answers).
- Test data fictional; no real account ids or amounts.
- Copy (exact): buttons 「改成帳單的轉帳」 (one leg known) / 「改成帳單的轉帳…」 (ambiguous: picks a leg), 「配成轉帳…」, 「改金額…」; picker titles 「哪一筆是這筆轉帳？」, 「配成哪一筆轉帳？」, 「改哪一筆的金額？」; picker confirm 「確定」, cancel 「取消」; peer row 「<account_name> · <M/D> · <amount signed>」; entry row as the match sheet's row (`<YYYY/MM/DD> <title> <amount>`); 改金額 picker sub line 「改成銀行的 <line amount signed>」; 已處理 toast answers 「改成帳單的轉帳」, 「配成轉帳：<account_name>」, 「改金額：<entry title>」.
- Transfer hint when there is no peer candidate: 「另一個帳戶還沒有對得上的那一筆（帳單可能還沒匯入）。已經記過的話選「已記在別處，對到…」；不然先略過，另一邊進來後再配。」. With candidates: 「選「配成轉帳…」把兩個帳戶的這筆對起來。」.

## Review Focus

1. A case action pressed while a 復原 read is pending or another read is in flight: busy must hold until the post-action re-read lands; no stale read restores the wrong question (reuse the PR D `send()` discipline: cancel the in-flight read before posting).
2. Ambiguous `adjust_transfer` with one leg in `candidate_entry_ids` but `entry_id` null: still asks (picker with one row) — the server decided it is ambiguous.
3. `pair_transfer` with an empty `peer_candidates`: no 配成轉帳 button, the no-candidate hint shows; keys still count only the visible answers.
4. 改金額 picker after removing transfer legs is empty: no 改金額 button at all.
5. A 422 with no code ("not allowed for this case") shows the generic sentence without 重新讀取; a 409 `candidate_changed` shows 這一題的資料剛剛變了 + 重新讀取.

---

### Task 1: Model and service

**Files:**
- Modify: `frontend/src/app/components/accounting/inbox/inbox.model.ts`
- Modify: `frontend/src/app/components/accounting/inbox/inbox.service.ts`
- Modify: `frontend/src/app/components/accounting/inbox/inbox-view.ts` (`ACTION_LABELS`)
- Test: `frontend/src/app/components/accounting/inbox/inbox.service.spec.ts`, `inbox-view.spec.ts`

**Interfaces — Produces:**
- `export interface InboxPeerCandidate { line_id: number; statement_id: number; account_name: string; posted_date: string; amount: string }`
- `export interface InboxTransfer { adjust_transfer: { entry_id: number | null; line_id: number; candidate_entry_ids: number[] } | null; pair_transfer: { peer_candidates: InboxPeerCandidate[] } | null }`
- `InboxItem.transfer?: InboxTransfer | null`
- `export type CaseAction = 'adjust_transfer' | 'pair_transfer' | 'adjust_amount'`
- `InboxService.caseAction(caseId: number, action: CaseAction, params: Record<string, number>): Observable<ReconciliationAction>` — POST `${apiUrl}/cases/${caseId}/actions` with `{action, params}`, through `bump()` like `apply`.
- `ACTION_LABELS` gains `adjust_transfer: '改成帳單的轉帳'`, `pair_transfer: '配成轉帳'` (the 已處理 list).

- [ ] Step 1: failing tests — `caseAction` posts the exact URL and body `{action:'pair_transfer', params:{peer_line_id:9}}` and bumps `changed`; `actionLabel('pair_transfer') === '配成轉帳'`.
- [ ] Step 2: run `cd frontend && npx ng test --watch=false --include='src/app/components/accounting/inbox/**/*.spec.ts'` → FAIL.
- [ ] Step 3: implement.
- [ ] Step 4: same command → PASS; `npx tsc --noEmit -p tsconfig.app.json` clean.
- [ ] Step 5: commit (explicit paths).

### Task 2: Flow answer list, copy and error codes (view helpers)

**Files:**
- Modify: `frontend/src/app/components/notifications/recon-flow/recon-flow-view.ts`
- Test: `frontend/src/app/components/notifications/recon-flow/recon-flow-view.spec.ts`

**Interfaces — Consumes:** Task 1 types. **Produces:**
- `export type FlowAnswer = InboxButton | CaseAction`
- `flowActions(item): FlowAnswer[]` (replaces the PR D signature; same part-of rule). Order: `adjust_transfer` first when `item.transfer?.adjust_transfer` is set and its leg list (below) is non-empty; then `pair_transfer` first on a `transfer` question when `peer_candidates.length > 0`; then the inbox `actions` (part-of reorder as today); `adjust_amount` after `match_candidate` on an `ambiguous` question when `adjustEntries(item).length > 0`.
- `transferLegs(item): InboxCandidate[]` — for `entry_id === null`: `item.candidates` whose `entry_id` is in `candidate_entry_ids`; for a known `entry_id`: `[]` (no picker needed).
- `adjustEntries(item): InboxCandidate[]` — ambiguous only: `item.candidates` minus `candidate_entry_ids` of `adjust_transfer` (legs are refused 409 `protected`).
- `flowAnswerLabel(answer: FlowAnswer, item, currency): string` — inbox buttons via `buttonLabel`; the three case actions per Global Constraints copy (`adjust_transfer` with `entry_id === null` ends in 「…」).
- `flowNote` — transfer hint per Global Constraints (two variants by candidate count).
- `flowLearnScope` — filters `flowActions(item)` to inbox buttons that `learnableAnswer` accepts (case actions never learn) and compares that count with `flowActions(item).length` (today it compares with `item.actions.length`), so a step with 改成帳單的轉帳 reads 只有「…」會記住.
- `flowError` — add to `CHANGED_CODES`: `entry_out_of_scope`, `peer_out_of_scope`, `line_claimed`, `line_not_in_case`, `line_not_printed`; add to `PLAIN` (exact owner words, one line each): `peer_leg_claimed` 另一個帳戶的那一筆已經配給別的轉帳了; `peer_ambiguous` 另一個帳戶有好幾筆可能，沒辦法自動配; `not_standalone` 這筆是分筆或群組的一部分，不能直接改; `cross_currency` 兩邊幣別不同，不能直接配; `peer_is_card` 另一邊是信用卡，不能這樣配; `kind_sign` 金額正負方向不對，不能這樣改; `fx_pair_required` 外幣轉帳要兩邊一起改; `has_settlements` 這筆已經有沖銷記錄，不能改; `peer_not_live` 另一個帳戶的帳單還沒開始對帳; `peer_statement_busy` 另一個帳戶的帳單正在處理，等一下再試; `owner_deleted` 那一筆已經被刪掉了; `duplicate_effect` 這個答案剛剛已經做過了; `wrong_account` 那一筆記在別的帳戶; `wrong_currency` 那一筆的幣別不同; `not_member` 那一筆不在這個群組裡; `foreign_zero` 外幣金額是 0，不能改; `not_found` 找不到那一筆記錄; `case_closed` keeps its PR D text.

- [ ] Step 1: failing tests: order for (a) amount_differs leg with `entry_id` set → `['adjust_transfer', 'accept_bank_amount', 'match_candidate', 'dismiss']`; (b) ambiguous with two legs and one non-leg candidate → `['adjust_transfer', 'match_candidate', 'adjust_amount', 'dismiss']`; (c) transfer with one peer → `['pair_transfer', 'match_candidate', 'dismiss']`; (d) transfer with empty peers → `['match_candidate', 'dismiss']`; (e) ambiguous where every candidate is a leg → no `adjust_amount`; (f) `transfer` absent → PR D behaviour unchanged. Labels; both hint variants; `flowError` for `peer_leg_claimed` (plain, no reload), `entry_out_of_scope` (changed + reload), 422 without code → generic, no reload; scan: no `PLAIN` value matches `/[a-z_]{4,}/`.
- [ ] Step 2: run the recon-flow-view spec → FAIL.
- [ ] Step 3: implement.
- [ ] Step 4: PASS; tsc clean.
- [ ] Step 5: commit.

### Task 3: Picker sheet

**Files:**
- Create: `frontend/src/app/components/notifications/recon-flow/pick-sheet/pick-sheet.ts`, `.html`, `.scss`, `.spec.ts`

**Interfaces — Produces:** `AppFlowPickSheetComponent`, selector `app-flow-pick-sheet`; inputs `heading: string`, `sub: string | null`, `options: readonly {id: number; label: string}[]` (required); outputs `picked: number`, `closed: void`. Same overlay contract as `AppInboxMatchSheetComponent`: `.overlay[data-overlay]` > `.sheet[role=dialog][aria-modal=true]` labelled by its h3, `SheetPullDirective` grab bar, focus into the sheet on open (`focusSheetField`), Esc closes only the sheet, Tab trapped (`trapFocus`), focus restored on cancel (`restoreOverlayFocus`), styles from `../../../accounting/sheet.scss`. Radio rows in a `role=radiogroup`; 確定 disabled until a row is picked; with exactly one option it is pre-picked.

- [ ] Step 1: failing tests: renders heading/sub/rows; 確定 disabled until pick; emits the picked id; one option pre-picked; Esc and 取消 emit `closed`; Tab stays inside.
- [ ] Step 2: run → FAIL. Step 3: implement. Step 4: PASS. Step 5: commit.

### Task 4: Flow wiring

**Files:**
- Modify: `frontend/src/app/components/notifications/recon-flow/recon-flow.ts`, `recon-flow.html`, `recon-flow.scss` (only if the 4-answer list needs it)
- Test: `frontend/src/app/components/notifications/recon-flow/recon-flow.spec.ts`

**Interfaces — Consumes:** Tasks 1–3.

Behaviour:
- `step().answers` built from `flowActions` + `flowAnswerLabel`; `learns` only for inbox buttons (unchanged rule). First answer solid unless part-of (unchanged).
- Keys: 1–N for N = min(answers, 4); the hint reads 「1–N 選擇」 (PR D rule extended from 3 to 4; `aria-keyshortcuts` and `<kbd>` for the first 4).
- `press(answer)`: `adjust_transfer` with a known `entry_id` → one tap; with `entry_id === null` → picker of `transferLegs`; `pair_transfer` → picker of peers; `adjust_amount` → picker of `adjustEntries` with sub 「改成銀行的 <amount>」. Picker confirm → `sendCaseAction`.
- `sendCaseAction(item, action, params, answer)`: same guards as `send()` (locked, account), same in-flight-read cancel, `busy` on; `inbox.caseAction`; next → `markEntriesChanged`, `undo.offer({answer, caseId: item.case_id, actionIds: [out.id], accountId, statementId, rule: null})`, snapshot saved for 復原, re-read via the shared pending path; error → `busy` off, `inlineError = flowError(err)`, re-issue a pending 復原 read exactly as `send()` does. Generalise `PendingAnswer` so `settle()` does not need an `InboxApplyOut` for a case action (a case action that succeeded and is still open after the re-read shows no error).
- Keys and the sheet: the flow's keys do nothing while the pick sheet is open (extend the existing `recordCard() || matchCard()` guard).

- [ ] Step 1: failing tests (fictional fixtures modelled on `services/accounting-service/tests/integration/test_inbox_api.py:708-741`):
  - amount_differs leg (`entry_id: 500`): key 1 posts `cases/1/actions` `{action:'adjust_transfer', params:{entry_id:500, line_id:201}}`, no `/inbox/apply`; re-read advances; toast 「已處理：改成帳單的轉帳」 with 復原; 復原 reverts `out.id`, no rule PATCH, reconcile.
  - ambiguous two legs: press 1 → picker 「哪一筆是這筆轉帳？」 with 2 rows; pick second → params carry that `entry_id`.
  - transfer with one peer (spec scenario "Pair a transfer"): key 1 → picker with 「<account> · M/D · amount」; 確定 posts `{action:'pair_transfer', params:{peer_line_id}}` and the next question shows.
  - transfer without peers: no 配成轉帳 button; hint is the no-candidate sentence; answers `['已記在別處，對到…', '忽略']`.
  - ambiguous 改金額: picker lists only non-leg candidates; posts `{action:'adjust_amount', params:{entry_id, line_id}}`.
  - refusal: 409 `{detail:'candidate_changed: …'}` → 這一題的資料剛剛變了 + 重新讀取; 422 `{detail:[{loc:['params'], msg:'peer_leg_claimed: …'}]}` → plain sentence, no button; no re-read after an error.
  - busy: keys and answers disabled until the re-read lands; a 復原 read in flight is cancelled by the press and re-issued after an error.
  - four answers: key 4 presses the fourth; hint 「1–4 選擇」.
  - picker open: keys 1–4 / S / arrows do nothing; Esc closes only the sheet.
- [ ] Step 2: run → FAIL. Step 3: implement. Step 4: PASS; full suite `cd frontend && npx ng test --watch=false` green; tsc app + spec clean; `npx ng build` ok. Step 5: commit.

### Task 5: OpenSpec as built

**Files:** `openspec/changes/add-notification-center/design.md` (D5 Transfers bullet), `specs/frontend-notification-center/spec.md` (Requirement "Transfer and amount answers": refusals in plain words, not codes; adjust_transfer also on amount/date questions; no-candidate hint; keys up to 4), `tasks.md` (tick 5.1 and the 5.4 pair_transfer / ambiguous-adjust items with as-built notes; note the PR E split: E1 = 5.1, E2 = 5.3 snooze, E3 = 5.2 auto-pay with a write-scope auto-pay endpoint per the owner's 2026-11-25 ruling).

- [ ] Step 1: edit. Step 2: `openspec validate add-notification-center --strict` → valid (each requirement ≤ 500 chars). Step 3: commit.

## Rulings made while planning (from the survey, §D)

- R1 refusal copy: plain words via `flowError`, not codes — the owner ruled codes out on 2026-11-24; the spec sentence is amended. Cost if wrong: copy only.
- R2 改成帳單的轉帳 whenever `adjust_transfer` is set (amount_differs / date_differs / ambiguous), first and solid — the server offers it there; the spec only named ambiguous. Cost: a button on more questions than the spec named.
- R3 改金額 on every ambiguous question with non-leg candidates; amount is the bank line's (no amount input) — the server allows it with no condition and refuses legs. Cost: a refusal on an entry the server will not adjust (plain sentence).
- R4 keys 1–4 when a step has four answers — otherwise the fourth answer would have no key. Cost: one more key.
- R5 no client stale check (no `case_version` on this endpoint) — the server re-validates and answers 409 `case_moved` / `candidate_changed`. Cost: none beyond the refusal path.
