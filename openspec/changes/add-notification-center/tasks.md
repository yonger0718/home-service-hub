## 0. Spec

- [x] 0.1 Design canvas (NotifDesktop, NotifPhone, ReconPhone, FeedbackSpec) and `design_handoff_v3/`
- [x] 0.2 Proposal, design (D1–D9), spec deltas; `openspec validate add-notification-center --strict`
- [x] 0.3 E-invoice addendum (2026-11-07): canvas (InvoiceDesktop, InvoiceUnmatchedPhone, InvoiceSpec), design D10–D12, spec additions, `accounting-einvoices` delta
- [x] 0.4 Re-run `openspec validate add-notification-center --strict` after the addendum

## 1. Feedback primitives (PR A)

- [x] 1.1 `ToastService` + shell outlet (positions, variants, roles); replace every `<p-toast>` and `AccountingToastService` (復原 API only; the reconciliation 復原 scenario is wired and verified in PR D. 重試 is offered where a failure is shown as a toast; forms that report a failed save inline keep the inline error)
- [x] 1.2 `<app-banner>` (error / warn / info); move import failure, bill error, schedule failing and quote delay onto it (quote delay: no such alert exists; needs a rule — deferred. Also moved: expired reward rules, portfolio account overdraft, and eight whole-page read failures)
- [x] 1.3 Tests: one toast at a time; 失敗 stays until closed; banner dismissal remembered for info only

## 2. Notification page, ledger sources (PR B)

- [x] 2.1 `NotificationCenterService`: task model, stable ids, tiers, per-source failure, refresh triggers and 30 s throttle (fold `NavBadgeService` in) (`NavBadgeService` kept as a facade. Not in B: snooze and its exclusion from the count — 5.3)
- [x] 2.2 Sources: card bills, schedule queue grouped by definition, inbox grouped by statement, MOZE import review, debts (tracking) (plus an interim e-invoice task; debts from `getCounterparties`; the inbox is read up to 1000 items; the rest is an uncounted catch-all note, since the API cannot attribute it to a statement. Not in B: the e-invoice task copy and flow — 6.2 / 6.3; dividends, import failure, 國泰, auto-pay sources — 3.x / 5.2)
- [x] 2.3 `/notifications` page: sections, chips, rows, 追蹤中, handled link, empty state, failed-source line (statement rows open the existing statement page; the handled link counts reconciliation actions only; one column. Not in B: the review route and the right pane — 4.2; 系統 rows — 3.x)
- [x] 2.4 Count and dots (D3, D8); sidebar 通知 item in both widths; phone header bell; timeline bell → link (Not in B: the 股利 dot — 3.1; the phone bell on the ten pages that draw their own header: 帳戶明細, 帳戶設定, 記一筆／編輯, 提醒中心, 記錄明細, 多類別群組, 自訂報表, 回饋規則, 對帳帳單, 對帳明細)
- [x] 2.5 Tests: grouping (3 Netflix instances → 1 task), count excludes tracking and snoozed, badges off, failed source (grouping, tracking excluded, badges off, failed source. Not in B: snoozed — 5.4)

## 3. Portfolio and system sources (PR C)

- [x] 3.1 Dividends past payment date (one batch) and 追蹤中 for the rest (dividends are read by paging every row and filtering in the SPA — the API has no receipt_status filter; a scan that hits the 50-page cap or the 20 s limit fails the source instead of showing a partial result; a payment date of today counts as past; rows show symbols, not names; plus the 股利 dot from 2.4 and `?status=pending` on the 股利 page, read once when the page opens)
- [x] 3.2 Import failure task (same signal as the 匯入 dot) (the recalc status is now read in every round as source 匯入. Known limit, unchanged: the server status is in-process and the last failure is per device, so the task can differ between devices and disappear after an API restart)
- [x] 3.3 國泰 daily statement: pending dates with 已處理 (ack: records the date as known, imports nothing), run health rule; status card on `/portfolio/import` (run rule in Asia/Taipei, evaluated once per round and always against the answer of that round; 系統 rows and the 匯入 dot are red for failed, latched and stale; on the card failed and latched are 錯誤, stale and missing 注意, a latched card shows the `--unlatch` line, a failed run's code is shown in plain words; pending dates alone light the 匯入 dot primary; beyond 50 dates one uncounted note 「還有 N 天未列出，處理完上面的會接著顯示」; 查看 on a 國泰 row opens the card at `#cathay`. Not in C: 復原 for an ack — no endpoint; snooze — 5.3)
- [x] 3.4 Tests: ack removes the task; stale run at 07:00 with latest at yesterday 06:41 creates a task (ack removes the task; 404 reads as done; one request per press; 07:00 / yesterday 06:41 with the 06:54 and 06:55 boundaries, the exact 06:40:00 boundary and a UTC-written variant)

## 4. Review flow (PR D)

- [x] 4.1 Move inbox answer labels into a shared map used by the panel and the flow (the labels were already one function, `buttonLabel()` in `inbox-view.ts`; the flow imports it and adds its own chip, headline and comparison copy in `recon-flow-view.ts`)
- [x] 4.2 `/notifications/recon/:statementId`: pane at ≥ 1024 px, full screen below; comparison grid; answers; learn switch; progress; end state (one inbox item per step; the list is read again after each answer; header shows 已對上 k / n 行 from the statement task; end state 繼續對帳 / 回到通知; one flow instance across widths; 「看整張帳單」 links to the old statement page. Not in D: transfer answers, 改金額 on ambiguous — 5.1; 金額不同也算 is offered only in the record sheet. Owner design review 2026-11-24: 記一筆 hidden while the flow is open; switch before the answers and shared with the record sheet; 略過 row fixed at the pane's bottom; refusals in plain words with 重新讀取; the compared entry left out of 對到…)
- [x] 4.3 Related questions on one entry applied in one request (as built: one request with the bank-line case only — the API rejects mixed questions; the server closes the entry's own question when the answer matches or adjusts it, otherwise it is asked next)
- [x] 4.4 Keys 1–3 (1–4 since PR E1) / S / ← → / Esc, off while a text field has focus (also off while a sheet or dialog is open, while an answer is being sent, and for auto-repeat)
- [x] 4.5 Tests: answering advances; related pair sends both case ids; closing midway keeps the task with the remaining count (the pair test asserts one case id and that the second question is not shown, plus the 不是同一筆 and 對到-another-entry variants; 復原: revert → rule off → reconcile, wired through `ReconUndoService` — this is the 復原 scenario 1.1 deferred)

## 5. Answers, auto-pay, snooze (PR E)

PR E is split (2026-11-25): E1 = 5.1 (+ its 5.4 tests); E2 = 5.3 snooze; E3 = 5.2 auto-pay, with a write-scope auto-pay endpoint first (owner ruling 2026-11-25: `PUT /settings/reconciliation` is admin and replaces the whole document).

- [x] 5.1 照帳單改這筆轉帳 / 照帳單改轉帳…, 配成轉帳… (candidate picker), 改金額… on ambiguous (#150); refusals inline in plain words (PR E1: also on amount/date questions with a transfer leg; keys 1–4; one picker sheet `app-flow-pick-sheet`. Owner design review 2026-11-25: the transfer answer and 改金額… stay separate; leg known = one tap with a note naming the bank amount; ambiguous order 對到… first; picker rows 「date title amount → bank」 and 「date from → to amount」 (amount without a sign); toasts 已照帳單改轉帳 / 已配成轉帳：<account> <M/D>. Backend follow-up: `adjust_transfer` legs carry the counter account, and the action response says whether the peer question was resolved, for 「另一個帳戶的那一題也處理了」)
- [ ] 5.2 Auto-pay: pill text, 記帳設定 switch + 起始日, skipped audit events → 系統 tasks with the reason copy from D9
- [ ] 5.3 Snooze (D4) with fingerprint wake-up
- [ ] 5.4 Tests: pair_transfer payload; ambiguous adjust asks for the entry; snooze hides and wakes (PR E1: the pair_transfer and ambiguous-leg tests are in `recon-flow.spec.ts`; snooze comes with E2)

## 6. E-invoices (PR F)

- [ ] 6.1 API: `PATCH /einvoices/preferences` accepts optional `date_window_days` (0–7) and `mute_prompt_after` (1–10), merged with a version bump; `GET` returns both fields plus read-only `last_import_at` and `auto_linked_count`; tests for partial update, range, the GET fields and a PATCH that sends a read-only field
- [ ] 6.2 Task source: one 要你確認 task for `ambiguous` + `near`; one 追蹤中 row for `unmatched` (hidden when `remind_unmatched` is off)
- [ ] 6.3 `/notifications/einvoices` flow (D10)
  - kind chip
  - invoice card
  - candidate answers with difference chips (日期差, 金額差; the API has no invoice time); link by `entry_id` or `group_id` from the candidate kind
  - 都不是, 選其他帳…, 記成新的一筆 prefilled
  - keys, end state
- [ ] 6.4 Unmatched page (D11)
  - groups by seller
  - row actions
  - 這 N 張都不用記帳
  - 這家以後都不提醒 via `dismiss {mute_seller: true}`
  - toast 復原
- [ ] 6.5 Entry invoice card and 記帳設定 › 發票 section (D12); the row for an entry without an invoice stays as `Calendar entry drawer invoice link` specifies (#408)
- [ ] 6.6 Remove the inbox panel's 發票待對帳 section and the `ask + einvoice_ask` sums at every `inboxBellCount` call site (`inbox-panel.ts`, `reminders.ts`; `nav-badge.service.ts` and `timeline.ts` no longer sum them after PR B)
- [ ] 6.7 Tests
  - one task in the count for three invoices
  - link and dismiss calls
  - mute payload and undo
  - no tax id, no mute
  - reminders off hides the row
  - preferences PATCH payload

## 7. Verify

- [ ] 7.1 Screens at 1440 / 1100 / 390 in light and dark against `design_handoff_v3/`; before / after on each PR
