## 0. Spec

- [x] 0.1 Design canvas (NotifDesktop, NotifPhone, ReconPhone, FeedbackSpec) and `design_handoff_v3/`
- [x] 0.2 Proposal, design (D1–D9), spec deltas; `openspec validate add-notification-center --strict`
- [x] 0.3 E-invoice addendum (2026-11-07): canvas (InvoiceDesktop, InvoiceUnmatchedPhone, InvoiceSpec), design D10–D12, spec additions, `accounting-einvoices` delta
- [x] 0.4 Re-run `openspec validate add-notification-center --strict` after the addendum

## 1. Feedback primitives (PR A)

- [ ] 1.1 `ToastService` + shell outlet (positions, variants, roles); replace every `<p-toast>` and `AccountingToastService`
- [ ] 1.2 `<app-banner>` (error / warn / info); move import failure, bill error, schedule failing and quote delay onto it
- [ ] 1.3 Tests: one toast at a time; 失敗 stays until closed; banner dismissal remembered for info only

## 2. Notification page, ledger sources (PR B)

- [ ] 2.1 `NotificationCenterService`: task model, stable ids, tiers, per-source failure, refresh triggers and 30 s throttle (fold `NavBadgeService` in)
- [ ] 2.2 Sources: card bills, schedule queue grouped by definition, inbox grouped by statement, MOZE import review, debts (tracking)
- [ ] 2.3 `/notifications` page: sections, chips, rows, 追蹤中, handled link, empty state, failed-source line
- [ ] 2.4 Count and dots (D3, D8); sidebar 通知 item in both widths; phone header bell; timeline bell → link
- [ ] 2.5 Tests: grouping (3 Netflix instances → 1 task), count excludes tracking and snoozed, badges off, failed source

## 3. Portfolio and system sources (PR C)

- [ ] 3.1 Dividends past payment date (one batch) and 追蹤中 for the rest
- [ ] 3.2 Import failure task (same signal as the 匯入 dot)
- [ ] 3.3 國泰 daily statement: pending dates with 確認 (ack), run health rule; status card on `/portfolio/import`
- [ ] 3.4 Tests: ack removes the task; stale run at 07:00 with latest at yesterday 06:41 creates a task

## 4. Review flow (PR D)

- [ ] 4.1 Move inbox answer labels into a shared map used by the panel and the flow
- [ ] 4.2 `/notifications/recon/:statementId`: pane at ≥ 1024 px, full screen below; comparison grid; answers; learn switch; progress; end state
- [ ] 4.3 Related questions on one entry applied in one request
- [ ] 4.4 Keys 1–3 / S / ← → / Esc, off while a text field has focus
- [ ] 4.5 Tests: answering advances; related pair sends both case ids; closing midway keeps the task with the remaining count

## 5. Answers, auto-pay, snooze (PR E)

- [ ] 5.1 改成帳單的轉帳, 配成轉帳 (candidate picker), 改金額 on ambiguous (#150); refusal codes inline
- [ ] 5.2 Auto-pay: pill text, 記帳設定 switch + 起始日, skipped audit events → 系統 tasks with the reason copy from D9
- [ ] 5.3 Snooze (D4) with fingerprint wake-up
- [ ] 5.4 Tests: pair_transfer payload; ambiguous adjust asks for the entry; snooze hides and wakes

## 6. E-invoices (PR F)

- [ ] 6.1 API: `PATCH /einvoices/preferences` accepts optional `date_window_days` (0–7) and `mute_prompt_after` (1–10), merged with a version bump; tests for partial update and range
- [ ] 6.2 Task source: one 要你確認 task for `ambiguous` + `near`; one 追蹤中 row for `unmatched` (hidden when `remind_unmatched` is off)
- [ ] 6.3 `/notifications/einvoices` flow (D10)
  - kind chip
  - invoice card
  - candidate answers with difference chips
  - 都不是, 選其他帳…, 記成新的一筆 prefilled
  - keys, end state
- [ ] 6.4 Unmatched page (D11)
  - groups by seller
  - row actions
  - 這 N 張都不用記帳
  - 這家以後都不提醒 via `dismiss {mute_seller: true}`
  - toast 復原
- [ ] 6.5 Entry invoice card and 記帳設定 › 發票 section (D12)
- [ ] 6.6 Remove the inbox panel's 發票待對帳 section and the `ask + einvoice_ask` sums at the three call sites
- [ ] 6.7 Tests
  - one task in the count for three invoices
  - link and dismiss calls
  - mute payload and undo
  - no tax id, no mute
  - reminders off hides the row
  - preferences PATCH payload

## 7. Verify

- [ ] 7.1 Screens at 1440 / 1100 / 390 in light and dark against `design_handoff_v3/`; before / after on each PR
