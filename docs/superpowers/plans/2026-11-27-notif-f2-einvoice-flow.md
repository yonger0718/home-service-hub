# Notification center PR F2: e-invoice task and flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invoices in state `ambiguous` / `near` become one 要你確認 task 「電子發票 · N 張要確認」 that opens `/notifications/einvoices`, a one-invoice-at-a-time flow (right pane ≥ 1024 px, full screen below) with candidate answers, 都不是, 選其他帳… and 記成新的一筆.

**Architecture:** Angular SPA only (`frontend/`), no API change (F1 shipped the preferences fields; the flow needs none). The route matcher and `isReviewFlowUrl` learn `notifications/einvoices`; `NotificationsComponent` mounts either flow in the same pane; a new `EinvoiceFlowComponent` reuses the recon flow's frame (markup copied, `recon-flow.scss` shared through `styleUrl` array, key helpers moved to an exported `flow-keys.ts`); a root `EinvoiceFlowSession` keeps the flow's order and outcomes across the entry-form round trip; the entry form learns `amount` / `merchant` / `invoice` query params.

**Tech Stack:** Angular 21 standalone + signals, Vitest (`npx ng test --watch=false --include …`), OpenSpec CLI 1.15.

**Spec:** `openspec/changes/add-notification-center/` design D5 + D10; spec requirements "E-invoice task and flow", "E-invoice flow screen", "E-invoice flow answers"; tasks 6.2 (要你確認 part), 6.3, 6.7 (flow tests).

## Scope split (tasks.md, 2026-11-27)

- F2 = this plan. F3 = 6.2's 追蹤中 row + 6.4 unmatched page (`/notifications/einvoices?state=unmatched`) + the end state's link to it. F4 = 6.5 + 6.6.
- Until F3 the end state has no 有發票沒有帳 link (only counts + 回到通知); the inbox panel's 發票待對帳 section and the `ask + einvoice_ask` sums stay until F4.

## Global Constraints

- Copy (exact, spec/design):
  - Task title `電子發票 · N 張要確認` (N = `counts.einvoice_ask`, whole set, not the page); meta = up to three distinct `seller_name`s of the listed ambiguous/near invoices joined by `、` (null when none listed); `when` = `最新 M/D` of the newest listed `issue_date` (omitted when none listed).
  - Kind chips: `有兩筆都像` (ambiguous; with N ≠ 2 candidates still this label), `差一點` (near).
  - Reasons: ambiguous → `同一天、同金額的帳有 N 筆` when every candidate's date = `issue_date`, else `金額相同的帳有 N 筆`; near → `沒有金額完全相同的帳，這筆最接近` when the first candidate's `|amount| − total` is within `max(50, total × 0.2)` (the matcher's `near_tolerance`), else `商家名稱對得上，金額不同`.
  - Answers: `連結 <name ?? category ?? 這筆帳>`; second line `account_name · M/D · category`; amount right; chips `日期差 N 天` (N = |candidate.date − issue_date| > 0) and `金額差 $N` (N = ||amount| − total| > 0, `invoiceAmount` format). First candidate `primary`, others `soft`.
  - Then `都不是`, `選其他帳…`, `記成新的一筆`; skip button `略過，稍後再看` (as recon).
  - Progress `第 n / N 張` in panes, `n / N` full screen; end `已處理 h / N 張`.
  - End state title `發票都處理完了` when nothing skipped, else `還有 K 張略過`; lines `連結 a 張 · 都不是 b 張 · 記成新的一筆 c 張 · 略過 K 張` (zero parts omitted); buttons `繼續確認` (only when K > 0, resumes the skipped) and `回到通知`.
  - Write error inline under the answers: `einvoiceErrorText(err)` (existing).
- Keys (flow host `(document:keydown)`, same guards as recon: Ctrl/Meta/Alt, busy, open dialog, typing): `1`–`9` → candidate i (i ≤ N); `N+1` (when ≤ 9) → 都不是; `S` skip; `←` / `→` previous / next; `Esc` close. Hint `1–N 選擇 · S 略過 · ← → 上下張 · Esc 關閉` via the moved `keyHint`.
- Writes: link → `EinvoiceService.link(id, targetBody(candidate))` (`{entry_id}` or `{group_id}` by `kind`); 都不是 → `dismiss(id)` (no mute); 選其他帳… → `<app-einvoice-target-sheet>` then `link(id, picked)`. On success the invoice's outcome is recorded and the flow moves to the next open invoice; on error the screen stays with the inline error.
- 記成新的一筆 → `router.navigate(['/accounting/entry'], { queryParams: { kind: 'expense', date: issue_date, amount: total_amount, merchant: seller_name, invoice: number } })` after `session.awaitRecord(id, accounting.entriesChanged())`. On return, if `entriesChanged()` > the stored value the outcome is `recorded` and the flow moves on; else the same invoice shows again.
- Entry form query params (new-record only, ignored with `id` / `copy` / `schedule`): `amount` must match `^\d{1,12}(\.\d{1,4})?$` → `amountExpr`; `merchant` trimmed, ≤ 100 chars → `merchant`; `invoice` must match `^[A-Z]{2}[0-9]{8}$` → `setInvoice('number', …)`. Invalid values are ignored silently. Added to the `distinctUntilChanged` key.
- Test data fictional (sellers 測試咖啡 / 測試超商, tax ids 00000001…, numbers AB00000001…); every SPA build carries the copied `environment.ts` (never printed).

## Decisions (my calls, recorded for review)

- Wake: `{ members: <ids of listed ambiguous/near invoices> }` instead of `{ count }`, so a new invoice wakes a snoozed task even when another was answered (dividends' rule). Snooze id stays `einvoice`.
- `from: 'recon'` kept (same inbox read; a failed inbox read fails both).
- The flow's order = the listed ambiguous/near invoices at first read (API order: state, newest first). Later reads drop answered ones (left the inbox) but never reorder or add; a new invoice shows on the next open.
- Merchant is not in `EinvoiceTarget`; the answer shows `name` (D10's "merchant" has no API field). No API change.
- The near reason is computed in the SPA from the tolerance rule (the API sends no reason).
- `EinvoiceFlowSession` (root): `{ order: number[]; outcome: Map<id, 'linked'|'dismissed'|'recorded'>; skipped: Set<id>; cursor: id|null; pendingRecord: {id, mark}|null }`; reset when the flow opens from the task (not when it returns from the entry form: `pendingRecord` set), and on 回到通知 / Esc close.
- `NotificationsComponent`: `statementId` becomes `flow = { kind: 'recon', id } | { kind: 'einvoice' } | null`; `selectedTaskId` → `recon:<id>` or `einvoice`; `focusFlow` queries `app-recon-flow, app-einvoice-flow`; `flowAlone` and the pane markup are shared.

## Code facts (verified at origin/main f88fbef0)

- Route: `app.routes.ts:92-103` `notificationsMatcher`; `review-flow-url.ts` `REVIEW_FLOW_ID`, `isReviewFlowUrl` (shell `shell.ts:69` hides the FAB).
- Page: `notifications.ts:112-123` (`statementId`, `panes`, `flowAlone`, `selectedTaskId`), `:300-312` focus effect, `:390` `closeFlow`, `:400` `focusFlow`, `:414` `focusAfterClose`; `notifications.html:1,3,109-116` pane.
- Recon flow: `recon-flow.ts:107-124` `typing`/`TYPING_ROLES`/`TYPING_TAGS`/`OPEN_DIALOG`/`EDITABLE` (file-private), `:502-553` `onKey`, `:887` `keyHint`; `recon-flow.html:1-56` header + segments, `:138` skip, `:145-160` end; `recon-flow.scss` (`:host` scoped).
- Task: `sources/recon.ts:74-93` `einvoiceTask(counts)` (interim, primary `/accounting/reminders?tab=todo`); pushed at `notification-center.service.ts:371-378`; specs `recon.spec.ts:99-128`, `notification-center.service.spec.ts:849,881`.
- Data: `inbox/einvoice.model.ts` `InboxEinvoice` (`candidates: EinvoiceTarget[]`, `offer_mute`), `EinvoiceTarget {kind,id,account_name,date,amount,currency,category,name}`; `inbox/einvoice.service.ts` `link`, `dismiss`, `targets`; `einvoice-target-sheet.ts` `AppEinvoiceTargetSheetComponent` (inputs `invoice`, outputs `picked`, `closed`; modal → `OPEN_DIALOG` pauses keys), `targetBody`, `candidateLabel`; `einvoice-format.ts` `invoiceAmount`; `einvoice-section.ts:30` `einvoiceErrorText`.
- Center: `NotificationCenterService.inboxData` (:277), read with `INBOX_READ_LIMIT`; re-reads on `accounting.entriesChanged` (EinvoiceService writes call `markEntriesChanged`).
- Entry form: `entry-form.ts:739-761` query → `start()` `:844-886`; `amountExpr` `:294`, `merchant` `:308` (`setParentText`), `setInvoice` `:1456`; `leave()` `:2437` → `location.back()`.

## Tasks

### Task 1: Route, URL test, page frame

**Files:** `app.routes.ts`, `components/notifications/review-flow-url.ts` (+ spec), `notifications.ts/html` (+ spec), `shell/shell.ts` only if the test shows it needs it.

- [ ] RED: `review-flow-url.spec.ts` — `isReviewFlowUrl('/notifications/einvoices')` true, `'/notifications/einvoices?x=1'` true, `'/notifications/einvoices/1'` false, `'/notifications/einvoicesx'` false. Matcher: `['notifications','einvoices']` → `posParams.flow`; `['notifications','einvoices','2']` → null.
- [ ] RED: `notifications.spec.ts` — at `/notifications/einvoices` in panes the list stays and `app-einvoice-flow` mounts in `.notif-pane`; below 1024 px only the flow renders; the `einvoice` row is selected; Esc/close → `/notifications` (replaceUrl) and focus returns to the `einvoice` row.
- [ ] GREEN: `flow` union signal; matcher + `isReviewFlowUrl`; template switch. A stub `EinvoiceFlowComponent` (Task 3 fills it).
- [ ] Run the two specs + `recon-flow.spec.ts` + `notifications.integration.spec.ts`; commit.

### Task 2: Task source

**Files:** `sources/recon.ts` (`einvoiceTask(inbox: Pick<InboxOut,'counts'|'einvoices'>)`), `recon.spec.ts`, `notification-center.service.ts:374`, its spec.

- [ ] RED: 3 listed invoices (2 ambiguous, 1 near, sellers 測試咖啡 ×2 / 測試超商) and `einvoice_ask: 3` → one task `電子發票 · 3 張要確認`, meta `測試咖啡、測試超商`, when `最新 10/22`, primary `{label: '確認', route: '/notifications/einvoices'}`, wake `{members: ['1','2','3']}`; counts 1 in `summary()` (spec scenario: 3 invoices + 6 counted → 7); 4 sellers → first three; unmatched-only list + `einvoice_ask: 0` → null; truncated list (`einvoice_ask: 5`, 2 listed) → title says 5.
- [ ] GREEN; run specs; commit.

### Task 3: Flow component

**Files:** create `components/notifications/einvoice-flow/einvoice-flow.{ts,html,scss,spec.ts}`, `einvoice-flow-session.ts` (+ spec), `einvoice-flow-view.ts` (+ spec: pure kind/reason/chip/label functions); create `components/notifications/flow-keys.ts` (move `typing`, `OPEN_DIALOG`, `keyHint` from `recon-flow.ts`, export; recon imports them).

- [ ] RED (`einvoice-flow-view.spec.ts`): the spec scenario — ambiguous 測試咖啡 $85 10/22, candidates −85 on 10/22 and −85 on 10/21 → chip 有兩筆都像, reason `金額相同的帳有 2 筆`, second answer chip `日期差 1 天`, keys 1, 2, 都不是 3; both same day → `同一天、同金額的帳有 2 筆`; near −80 vs 85 → `沒有金額完全相同的帳，這筆最接近` + `金額差 $5`; near −300 vs 85 → `商家名稱對得上，金額不同`; group candidate → `targetBody` `{group_id}`; 10 candidates → keys 1–9 on candidates, 都不是 no key.
- [ ] RED (`einvoice-flow.spec.ts`, HttpTestingController): pressing 1 posts `/api/accounting/einvoices/1/link` `{entry_id: …}` and shows `第 2 / 3 張`; 3 (N+1) posts `/dismiss` `{mute_seller: false}`; a 409 keeps the invoice with the inline error and no move; S skips → end lists 略過 1 張 with 繼續確認; ← → move; Esc emits `closed`; a key while the target sheet is open does nothing; 選其他帳… → picked → link; 記成新的一筆 navigates with the exact query params; returning with `entriesChanged` bumped counts 記成新的一筆 1 張 and shows the next; returning without a bump shows the same invoice; invoices that left the inbox on a later read are not shown again.
- [ ] GREEN: implement. Re-run `recon-flow.spec.ts` (moved helpers).
- [ ] Commit.

### Task 4: Entry form prefill

**Files:** `entry-form.ts`, `entry-form.spec.ts` (or the existing query-param spec file).

- [ ] RED: `/accounting/entry?kind=expense&date=2026-10-22&amount=85&merchant=測試咖啡&invoice=AB00000001` → amount 85, merchant 測試咖啡, invoice number AB00000001, date 10/22; `amount=8x5`, `invoice=ab1`, a 101-char merchant → each ignored; with `copy=` the prefill is ignored; a later calendar query merge does not reset the prefilled draft.
- [ ] GREEN; run the entry-form specs; commit.

### Task 5: Verify + OpenSpec

- [ ] Full frontend suite (`npx ng test --watch=false`), `npx ng build` with the copied environment, `openspec validate add-notification-center --strict`.
- [ ] Tick 6.3; note on 6.2 / 6.7 what F2 covered. Screens (fictional stubs) at 1440 / 1100 / 390 light + dark: task row, flow ambiguous, flow near, target sheet, end state; before/after of the notifications list.

## Review Focus

1. The entry-form round trip: back from the form with and without a save, and with 連續記帳 (stays in the form; one return counts once).
2. Keys with a focused input / open sheet / modifier, and with ≥ 9 candidates.
3. An invoice answered elsewhere (another tab, the inbox panel) while the flow is open: it leaves on the next read and the flow does not show it or count it as handled.
4. A snoozed 電子發票 task wakes when a new invoice arrives, not when one is answered.
5. 1024 px boundary: pane vs full screen switch with the flow open keeps the flow state (session service).

## Plan review resolution (AGENT-621, CHANGES @ d62c0d4d; resolved 2026-11-27)

1. **記成新的一筆 counted by any `entriesChanged` bump (P2).** Fixed: `AccountingService.lastInvoiceCreated` records the invoice number of each successful `createEntry` that carries one, with a growing sequence. The flow's `pendingRecord` is `{id, number, mark}`; on the way back it counts the invoice only when an entry with that number was created since. Another page's write, a schedule-only save, a cancel or a changed number do not count; 連續記帳 counts once (the pending record is consumed). Tests: `einvoice-flow.spec.ts` (save counts, two saves count once, other write / no number / other number do not).
2. **Cut page vs. membership (P2).** Fixed: the flow passes `complete` (listed asking ≥ `counts.einvoice_ask`) to `afterRead`; on a cut read an absent invoice stays with its data instead of leaving. The task's wake is `{count, members}` when the page is complete and `{count}` when it is cut (an invoice moving into the page is not new); the count also wakes snoozes saved before members (`{count}` only). Ruling: no separate batch copy: the read limit is 1000 and a household never has that many invoices to confirm; the flow shows what is listed. Tests: `recon.spec.ts` (new invoice wakes, answered one does not, legacy count snooze, cut page), `einvoice-flow.spec.ts` (cut read keeps an absent invoice).
3. **Card and key guards not in acceptance (P2).** Already implemented (card with items and subtotals; repeat / handled / modifier / typing / busy / dialog guards, arrows repeat). Tests added: card DOM, held digit and S answer nothing while held arrows move, busy / open dialog / handled event, key 9 with eight vs nine candidates, crossing 1024 px keeps the place and answers.

Also from the review: the e-invoice prefill never touches a copy or an edit (`entry-form-split.spec.ts`); the flow reads the inbox itself (no access to the private `NotificationCenterService.inboxData`); the FAB hiding on `/notifications/einvoices` is covered in `shell.spec.ts`; a group target links by `group_id` (`einvoice-flow.spec.ts`).

## Code review resolution (AGENT-626, CHANGES @ 45919269; resolved 2026-11-27)

1. **連續記帳 A then B lost A (P2).** `AccountingService` now keeps every invoice number created with an entry (`invoiceCreateMark()` / `invoiceCreatedSince(number, mark)`); the flow counts the pending invoice when its number was created at any point since the mark. Test: A → B → back counts A.
2. **Read lifecycle and order (P2).** Each read carries a sequence; only the newest read's answer (or error) applies, and reads are cancelled when the flow is destroyed (`takeUntilDestroyed`), so a refresh landing after the owner left for the form never consumes `pendingRecord`. Tests: an in-flight read is cancelled on leaving and the pending record survives; an older read answering after a newer one is dropped.
3. **Restored invoice dropped when unmatched is not listed (P2).** The session keeps `restored` ids: an invoice undone here stays open whatever a read lists, until it is answered again. Tests: session (`afterRead` keeps it, `answer` clears it) and flow (復原 with `remind_unmatched` off, then answer again).
