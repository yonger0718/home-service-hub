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
5. The page with `remind_unmatched` off (nothing listed) and with a cut read (fewer unmatched listed than `counts.einvoice_unmatched`).

---

## PR F3a — API

### Task 1: Link at save

**Files:**
- Modify: `services/accounting-service/app/services/einvoice_matcher.py` (`run_matcher`)
- Modify: `services/accounting-service/app/routers/entries.py` (`post_entry`)
- Test: `services/accounting-service/tests/integration/test_einvoice_link_at_save.py` (new)

**Interfaces:**
- Produces: `run_matcher(db, *, numbers: Collection[str] | None = None) -> dict` — `numbers` limits the pass to invoices with those numbers (same states, same decisions); `None` = every invoice (unchanged behaviour for import / `/match`).

- [ ] **Step 1: Failing tests** (fictional invoice `AB00000001`, $85, issue date inside the period):
  - `test_entry_with_invoice_number_links_open_invoice`: an `unmatched` invoice and `POST /entries` with `invoice_number="AB00000001"`, matching amount/date → 201, response `einvoice.number == "AB00000001"`, `linked_by == "auto"`; invoice state `linked`.
  - same for an `ambiguous` and a `near` invoice (the exact rule wins over amount).
  - `test_entry_with_number_of_dismissed_or_linked_invoice_changes_nothing`: `dismissed(owner)`, `linked` to another entry, `void` → no link row added, states unchanged, 201.
  - `test_entry_number_outside_period_does_not_link`: entry date outside `period_bounds` → invoice stays as it was.
  - `test_entry_without_number_runs_no_matcher` (spy / no state change on other invoices) and `test_einvoices_disabled_no_link`.
  - `test_link_at_save_touches_only_that_number`: a second unmatched invoice whose amount now has a candidate stays `unmatched` (only the 04:10 pass changes it).
- [ ] **Step 2:** run → FAIL (no link).
- [ ] **Step 3:** `run_matcher(numbers=…)`: add `Einvoice.number.in_(numbers)` to the select when given. In `post_entry`, after `create_entry` and before `commit`, when `payload.invoice_number` is set: `einvoice_matcher.run_matcher(db, numbers={payload.invoice_number})` inside the same `service_errors()` block. Only `POST /entries` (owner decision covers saves from the form; `PUT` / transfers / splits unchanged — ruling: a later edit that adds a number links at 04:10 as today).
- [ ] **Step 4:** run → PASS; full accounting suite.
- [ ] **Step 5:** commit `feat(accounting): link an open e-invoice when an entry is saved with its number`.

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

**Page:** intro 「這些發票找不到對應的帳。記一筆、選一筆連上，或標成不用記帳；同一家常常不用記的話，可以以後都不提醒。」; per row 記一筆 (entry form prefill as D10) / 選帳… (target sheet) / 不用記帳; group footer for > 1 invoice 「這 N 張都不用記帳」 and, when `canMute`, 「這家以後都不提醒」 (solo invoice: the mute button under its row); no tax id → note 「沒有統編的商家無法設定不提醒」; empty 「都整理好了」; footer 「不想看到這頁？記帳設定 › 發票 可以關掉提醒 ›」 → `/accounting/settings` (the 發票 section arrives with F4; ruling: link the page now). Reads the inbox itself (`InboxService.get({limit: INBOX_READ_LIMIT})`), newest-read-wins and cancelled on destroy (as the F2 flow), re-read on `entriesChanged`. A cut read (listed unmatched < `counts.einvoice_unmatched`) adds 「還有 K 張未列出，處理完上面的會接著顯示」.

**Toasts / 復原 (D11, artboard copy):**
- 不用記帳 → 「已把 <seller> <MM/DD> 標成不用記帳」; 復原 = `undismiss`.
- 這 N 張都不用記帳 → dismiss one after another; 「已把 <seller> 的 N 張標成不用記帳」; 復原 = `undismiss` each done one. A failure midway stops, keeps the done ones, toasts 「標了 k 張，第 k+1 張沒成功：…」 with 復原 for the k.
- 這家以後都不提醒 → `dismiss(id, true)` for the group's newest invoice; the group leaves; 「以後不再提醒 <seller>（N 張一起收起）」; 復原 = `deleteMute(taxId)` then `undismiss(id)`.
- 選帳… pick → `link`; 「已連結：<seller> $<total>」; 復原 = `unlink` + `undismiss` (as F2; #454 will replace it).
- 記一筆 → the entry form; after the save the invoice is linked (F3a) and leaves on the re-read; no toast here (the form has its own).

**Tests (page spec):** grouping / order / meta (K, 沒有統編); each action's request and toast; 復原 for each (mute order: DELETE then undismiss); dismiss-all partial failure; no tax id → note, no mute; empty state; cut read note; FAB hidden (`shell.spec` already has the URL).

### Task 6: Flow end-state link

**Files:** `einvoice-flow.html/.ts` + spec. When `counts.einvoice_unmatched > 0` the end state adds the link 「有發票沒有帳 N 張 ›」 to `/notifications/einvoices?state=unmatched` (D10). Test: shown with N, absent at 0.

### Task 7: OpenSpec + screenshots

- [ ] tasks 6.2 / 6.4 ticked with notes; spec scenarios for the row (reminders off already there), the page actions and the mute undo.
- [ ] Fictional screenshots (1440 / 1100 / 390, light + dark): notifications list with the 追蹤中 row; the page (several groups, mute offered, no tax id); each toast; empty; the flow end state with the link; before / after.

PR F3b: full frontend suite + production build, review through lead-claude, owner design check of the screenshots, merge on owner go.
