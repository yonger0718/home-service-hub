# Notification center F3: invoices without an entry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unmatched` e-invoices become one uncounted 追蹤中 row, 「有發票沒有帳 · N 張」, that opens a page grouped by seller where each invoice is recorded, linked or dismissed (D11); and an entry saved with an invoice's number links that invoice at once.

**Architecture:** Two PRs. **F3a (API):** `POST /entries` with an `invoice_number` runs the matcher for the open invoices carrying that number in the same transaction; the inbox gains `seller_dismissed` per unmatched invoice and `counts.einvoice_unmatched`. **F3b (SPA):** a tracking row from the inbox read, the page at `/notifications/einvoices?state=unmatched` in the flow slot (right pane ≥ 1024 px, full screen below), and the F2 flow's end-state link to it.

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic (accounting-service), pytest integration tests; Angular 21 signals, Vitest; Playwright stubs for screenshots.

**Spec:** `openspec/changes/add-notification-center/` — design D11 (and D10 item 4), `specs/frontend-notification-center/spec.md` 「Invoices without an entry」, `specs/accounting-einvoices/spec.md`; tasks 6.2 (追蹤中 part), 6.4, 6.7. Artboard `design_handoff_v3/design_refs/InvoiceUnmatchedPhone.dc.html`.

## Owner decisions

- 2026-11-27: 記一筆 from an invoice links at save (backend), not at the next 04:10 run. This also applies to the F2 flow's 記成新的一筆 and to any entry created with an invoice number.

## Global Constraints

- Fictional data only in tests, fixtures and screenshots (no real sellers, amounts, tax ids, carrier numbers).
- Never run alembic or uvicorn from a worktree; no migration in this plan (no schema change).
- Backend test recipe needs the OTEL vars; filter `postgresql:` URLs out of test output.
- Commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with the Claude Code line.
- Merge / deploy only on an explicit owner go. Every SPA build carries the copied `environment.ts`.
- OpenSpec strict-valid with the current CLI (1.15).

## Code facts (origin/main d0a9ab42 + F2 28583c4b)

- Matcher pass: `services/accounting-service/app/services/einvoice_matcher.py` `run_matcher(db)` (~L220): settings gate, `lock(db)`, selects `PROCESSED_STATES` + `dismissed/seller_muted` FOR UPDATE, `decide()`, link via `_insert_link` in a savepoint, `stamp()`. Exact rule: an entry whose `invoice_number == number` inside `period_bounds(invoice.period)` (`rule_candidates`).
- Entry create: `app/routers/entries.py` `post_entry` → `entry_write_service.create_entry` → `db.commit()` → `_written` (detail includes `einvoice`).
- Inbox: `app/services/einvoice_read.py` `inbox_section(db, limit)` already computes `dismissed_count` per tax id (owner dismissals) for `offer_mute`, but does not return it; unmatched are listed only when `remind_unmatched` is on; `ask` counts ambiguous + near.
- SPA tracking rows: `services/notification-center.service.ts` `trackingRows` (debts, upcoming, dividends); `NotifTrackingRow.id` union in `components/notifications/notif-task.ts`; rendered in `notifications.html` 追蹤中 `<details>` with `TRACKING_UNIT` in `notifications.ts`.
- Flow slot: `notifications.ts` `NotifFlow` union from the route matcher (`app.routes.ts`, `posParams.flow`), `@if (flow(); as open)` in `notifications.html`; `review-flow-url.ts` hides the FAB on `/notifications/einvoices` (query ignored).
- Writes: `EinvoiceService` `dismiss(id, muteSeller)`, `undismiss(id)`, `deleteMute(taxId)`, `targets(id)`, `link(id, target)`; errors `einvoiceErrorText`. Target sheet `AppEinvoiceTargetSheetComponent`.
- Entry form prefill (F2): `?kind=expense&date&amount&name&merchant&invoice`; the form returns with `location.back()`.

## Review Focus

1. An entry created with a number that matches no open invoice, a `linked` / `dismissed` / `void` invoice, or an invoice outside the number's period: nothing links, nothing else changes, the create still succeeds.
2. An entry created with an invoice number while e-invoices are disabled: no matcher work, create succeeds.
3. 這 N 張都不用記帳 where one dismiss fails midway: the done ones stay done, the toast's 復原 undoes exactly those, the failure is shown.
4. 復原 of 這家以後都不提醒 when the mute already turned the seller's other invoices into `seller_muted`: `DELETE /mutes/{tax_id}` brings them back, then `undismiss` the one dismissed with the mute (the order matters: undismiss refuses while the mute exists).
5. The page with `remind_unmatched` off (nothing listed, direct URL), with a cut read (fewer unmatched listed than `counts.einvoice_unmatched`), and with a read that lists none while the count is > 0 (≥ 1000 questions fill the page).

---

## PR F3a — API

### Task 1: Link at save

**Files:**
- Modify: `services/accounting-service/app/services/einvoice_matcher.py` (new `link_saved_entry`; `run_matcher` unchanged except sharing the open-state filter `_open_invoice()`)
- Modify: `services/accounting-service/app/routers/entries.py` (`post_entry`)
- Test: `services/accounting-service/tests/integration/test_einvoice_link_at_save.py` (new)

**Interfaces:**
- Produces: `link_saved_entry(db, entry_id: int, number: str) -> None` (AGENT-629 P1 ×2). Exact rule only (§7.3): the one open invoice with `number` whose period holds the entry's date, linked when the new entry is the only eligible entry stamped with `number` in that period. No amount / date fallback; no other invoice is read or changed (so no `decide()` over a subset). Open = the matcher's states (`unmatched`, `ambiguous`, `near`, `dismissed`/`seller_muted`); owner / runbook dismissals, `void`, `linked` untouched. The whole step runs in a savepoint; the matcher lock is taken with `pg_try_advisory_xact_lock` and the invoice row with `FOR UPDATE SKIP LOCKED` (never waits: busy → skip); `IntegrityError` / `OperationalError` roll the savepoint back and log the exception type only (messages carry SQL parameters). The save always succeeds.

- [ ] **Step 1: Failing tests** (fictional `AB00000001`, $120, issued 2026-10-05) in `test_einvoice_link_at_save.py`:
  - links: `unmatched`, `ambiguous`, `near`, `dismissed/seller_muted` → 201, response `einvoice.number`, `linked_by == "auto"`, state `linked`, link row on the new entry; the number wins over a different amount.
  - untouched: `dismissed/owner`, `dismissed/runbook`, `void`, linked to another entry; a number with no invoice (201, number stored).
  - `test_outside_the_period_never_falls_back_to_amount`: issued 2026-02-28 (period Jan–Feb), saved 2026-03-01 same number and amount → not linked.
  - `test_shared_candidate_leaves_the_other_invoice`: two $120 invoices ambiguous on one unnumbered entry; save with the first number → first linked, second still `ambiguous`.
  - `test_two_entries_with_the_number_wait_for_the_daily_pass`; `test_only_that_number_is_matched`; no number; e-invoices disabled.
  - `test_busy_matcher_lock_skips_without_waiting` (another connection holds the lock); `test_failure_mid_link_rolls_back_the_link_and_keeps_the_entry` (fail after the link insert → 201, entry kept, no link row); `test_edit_adding_the_number_does_not_link` (PUT).
- [ ] **Step 2:** run → FAIL. The outside-period case must fail against a number-filtered `run_matcher` too.
- [ ] **Step 3:** implement `link_saved_entry`; `post_entry` calls it after `create_entry`, before `commit`, when `payload.invoice_number` is set. Only `POST /entries` (ruling: an edit that adds a number links at the daily pass as today; transfers / splits unchanged).
- [ ] **Step 4:** run → PASS; full accounting suite.
- [ ] **Step 5:** commit.

### Task 2: Inbox fields for the unmatched page

**Files:**
- Modify: `app/services/einvoice_read.py` (`inbox_section` returns the unmatched count too), `app/schemas/einvoices.py` (`InboxEinvoice`, ~L184: + `seller_dismissed`), `app/schemas/inbox.py` (counts ~L148: + `einvoice_unmatched: int = 0`), `app/services/inbox_service.py` ~L501 (fills both counts; an `account_id` read keeps 0).
- Test: `tests/integration/test_einvoice_inbox.py` (extend).

**Interfaces:**
- Produces: each listed invoice carries `seller_dismissed: int` (owner dismissals of that seller's tax id; 0 for no tax id and for ambiguous / near); `counts.einvoice_unmatched: int` = every `unmatched` invoice (whole set, 0 when `remind_unmatched` is off or e-invoices are disabled).

- [ ] **Step 1: Failing tests:** two owner-dismissed invoices of seller A + one unmatched of A → the unmatched one has `seller_dismissed == 2`; a runbook dismissal does not count; no tax id → 0; `counts.einvoice_unmatched` with `limit=1` still counts all; reminders off → 0 and none listed.
- [ ] **Step 2:** run → FAIL. **Step 3:** return the existing `dismissed_count` per invoice; count unmatched before slicing. **Step 4:** PASS. **Step 5:** commit.

### Task 3: OpenSpec

- [ ] `specs/accounting-einvoices/spec.md`: requirement + scenario 「An entry saved with an open invoice's number links it in the same request」; the inbox fields `seller_dismissed`, `counts.einvoice_unmatched`.
- [ ] design D10 item 4: 記成新的一筆 links at save (replace 「links at the next matcher run」); D11 unchanged in substance.
- [ ] `openspec validate add-notification-center --strict` → valid. Commit.

PR F3a: full accounting suite, strict validate, review through lead-claude, merge on owner go.

---

## PR F3b — SPA (after F3a merges)

### Task 4: Tracking row

**Files:** `components/accounting/inbox/einvoice.model.ts`, `inbox.model.ts` (fields), `components/notifications/notif-task.ts` (`NotifTrackingRow.id` += `'einvoices'`), `components/notifications/sources/recon.ts` (`unmatchedRow(inbox)`), `services/notification-center.service.ts` (`trackingRows`), `notifications.ts` (`TRACKING_UNIT.einvoices = {name: '發票', unit: '張'}`). Tests: `sources/recon.spec.ts`, `notification-center.service.spec.ts`.

- `unmatchedRow(inbox): NotifTrackingRow | null` — null when `counts.einvoice_unmatched` is 0 / absent; title 「有發票沒有帳 · N 張」 (N = the count); meta = top three sellers by listed count, 「博客來 3、全聯 2、中油 1」 style (fictional in tests); link `{label: '查看', route: '/notifications/einvoices', queryParams: {state: 'unmatched'}}`; source `ledger`. Never counted (a tracking row, not a task).
- Tests: three unmatched of two sellers → title / meta / link; reminders off (count 0, none listed) → no row while the 要你確認 task still shows; the count is not part of `summarize`.

### Task 5: The page

**Files:** new `components/notifications/einvoice-unmatched/einvoice-unmatched{.ts,.html,.scss,-view.ts}` + specs; `notifications.ts/.html` (`NotifFlow` += `{kind: 'unmatched'}` when the route is `einvoices` and `?state=unmatched`); `notif-row`/focus keyed as for the flow.

**View (`einvoice-unmatched-view.ts`, pure):** `unmatchedGroups(invoices)` → groups by `seller_tax_id || seller_name`, ordered by count desc, then total desc, then seller; rows by date desc. Group: seller, meta 「N 張 · 統編 X」 / 「N 張 · 沒有統編」 + 「 · 已標不用記帳 K 次」 when K > 0 (K = max `seller_dismissed` in the group), total, `canMute` = tax id and any `offer_mute`. Row: `MM/DD`, items 「a、b · N 項」 (first two names), amount. Headline 「N 張 · M 家 · 不計入通知數字」.

**Page:** intro 「這些發票找不到對應的帳。記一筆、選一筆連上，或標成不用記帳；同一家常常不用記的話，可以以後都不提醒。」; per row 記一筆 (entry form prefill as D10) / 選帳… (target sheet) / 不用記帳; group footer for > 1 invoice 「這 N 張都不用記帳」 and, when `canMute`, 「這家以後都不提醒」 (solo invoice: the mute button under its row); no tax id → note 「沒有統編的商家無法設定不提醒」; empty 「都整理好了」; footer 「不想看到這頁？記帳設定 › 發票 可以關掉提醒 ›」 → `/accounting/settings` (the 發票 section arrives with F4; ruling: link the page now). Reads the inbox itself (`InboxService.get({limit: INBOX_READ_LIMIT})`), newest-read-wins and cancelled on destroy (as the F2 flow), re-read on `entriesChanged`. A cut read (0 < listed unmatched < `counts.einvoice_unmatched`) adds 「還有 K 張未列出，處理完上面的會接著顯示」. A read that lists none while the count is > 0 (questions fill the 1000-row page; AGENT-629 P2) shows 「先確認 N 張要確認的發票，這裡才會列出」 with a link to `/notifications/einvoices` (the F2 flow), never 「都整理好了」. 「都整理好了」 only when the count is 0. Reminders off (direct URL): 「提醒已關閉」 + the 記帳設定 link.

**Toasts / 復原 (D11, artboard copy):**
- 不用記帳 → 「已把 <seller> <MM/DD> 標成不用記帳」; 復原 = `undismiss`.
- 這 N 張都不用記帳 → dismiss one after another; 「已把 <seller> 的 N 張標成不用記帳」; 復原 = `undismiss` each done one. A failure midway stops, keeps the done ones, toasts 「標了 k 張，第 k+1 張沒成功：…」 with 復原 for the k.
- 這家以後都不提醒 → `dismiss(id, true)` for the group's newest invoice; the group leaves; 「以後不再提醒 <seller>（N 張一起收起）」; 復原 = `deleteMute(taxId)` then `undismiss(id)`.
- 選帳… pick → `link`; 「已連結：<seller> $<total>」; 復原 = `unlink` + `undismiss` (as F2; #454 will replace it).
- 記一筆 → the entry form; after the save the invoice is linked (F3a) and leaves on the re-read; no toast here (the form has its own).

**Tests (page spec):** grouping / order / meta (K, 沒有統編); each action's request and toast; 復原 for each (mute order: DELETE then undismiss); dismiss-all partial failure; no tax id → note, no mute; empty state (count 0 only); cut read note; listed 0 / count > 0 → the F2 link, not the empty state; reminders off; FAB hidden (`shell.spec` already has the URL).

### Task 6: Flow end-state link and the 記成新的一筆 count

**Files:** `einvoice-flow.html/.ts` + spec.
- When `counts.einvoice_unmatched > 0` the end state adds 「有發票沒有帳 N 張 ›」 to `/notifications/einvoices?state=unmatched` (D10). Test: shown with N, absent at 0.
- **F2 count fix (AGENT-629 P2):** with link at save the next read no longer lists the recorded invoice, and `afterRead` drops it before the pending check (`einvoice-flow.ts` ~L184–191), so a successful 記成新的一筆 is never counted. Apply `answer(current, pending.id, 'recorded')` to the state *before* `afterRead` when `invoiceCreatedSince(number, mark)` is true (an answered invoice survives `afterRead`); keep the cursor move when not saved. Tests: save → read without the invoice → counted `recorded` once; cancel → not counted; an unrelated write → not counted; 連續記帳 two saves → once.
- Deploy order: F3a (API) may ship alone (the live SPA has no F2). The F2 SPA ships together with F3b, never F2 alone after F3a.

### Task 7: OpenSpec + screenshots

- [ ] tasks 6.2 / 6.4 ticked with notes; spec scenarios for the row (reminders off already there), the page actions and the mute undo.
- [ ] Fictional screenshots (1440 / 1100 / 390, light + dark): notifications list with the 追蹤中 row; the page (several groups, mute offered, no tax id); each toast; empty; the flow end state with the link; before / after.

PR F3b: full frontend suite + production build, review through lead-claude, owner design check of the screenshots, merge on owner go.

## Plan review resolution (AGENT-629, CHANGES @ b35a5214)

- P1 failure isolation / lock wait → Task 1: savepoint around the whole step, try-lock + `SKIP LOCKED`, busy-lock and mid-link-failure tests. The step holds no lock another writer waits on before it takes its own (new entry row only), and takes nothing it would wait for.
- P1 exactness → Task 1: dedicated exact-rule link instead of a number-filtered `run_matcher`; outside-period fallback and shared-candidate tests; seller_muted links, owner / runbook dismissals untouched.
- P2 F2 記成新的一筆 count → Task 6 fix + tests; deploy order note.
- P2 cut read with none listed → Task 5 copy, link to the F2 flow, tests; 「都整理好了」 only at count 0.
- Minor (Task 2 assertions for disabled / account_id / questions = 0) → done in `test_unmatched_count_zero_when_reminders_off_or_disabled` and `test_seller_dismissed_is_zero_on_questions`.
- Re-check (AGENT-629 @ 45d597a9, CHANGES, one P2): the settings read sat outside the savepoint → moved inside; `test_failing_settings_read_keeps_the_entry` (RED before), `test_busy_invoice_row_skips_without_waiting` (SKIP LOCKED); the questions = 0 test now puts an owner dismissal, a reminder and both question states on one tax id.

## Owner design review (2026-11-28, applied in F3b)

1. 追蹤中 row meta lists seller names only: 「測試書店、測試超市 一號店、測試加油站 等 4 家」 (N = all sellers).
2. Phone: an open 追蹤中 hides the summary; the title never wraps. (Also: each summary part 「發票 7 張」 wraps as a whole.)
3. 這家以後都不提醒 is an outline button, as 這 N 張都不用記帳.
4. The flow end link 「有發票沒有帳 N 張 ›」 and the page footer link use the primary colour with a trailing ›, as the list's foot links (PR B).
5. The 記帳設定 › 發票 footer is hidden until F4 (`settingsLink = false`; F4 turns it on and tests it).
