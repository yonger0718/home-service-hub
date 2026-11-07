## Why

Pending work is spread over the app and counted noisily. The 提醒中心 lives behind a 🔔 on the accounting timeline, its count (30 on the review data) adds every overdue schedule instance and every reconciliation question one by one, and the same number repeats on the sidebar, the sub-nav and the bell. Portfolio work (待收股利, a failed import, the 國泰 daily statement) has no place there at all. Reconciliation questions are long cards with truncated buttons, the same ledger entry can appear on two cards, and the transfer answers the API already offers (`adjust_transfer`, `pair_transfer`, #150) have no button. Feedback uses two toast systems (PrimeNG `p-toast` top-right on portfolio pages, `AccountingToastService` at the bottom on accounting pages) and several banner styles.

The owner reviewed a redesign on the design canvas (`design_handoff_v3/`) and asked for this handoff on 2026-10-31. **This PR contains no code.**

## What Changes

- **通知 page** at `/notifications`, reachable from a 通知 item at the top of the sidebar (expanded and rail) and a bell button in every phone page header. It lists tasks from accounting and portfolio in tiers 已逾期 → 今天 → 要你確認 → 系統, then a folded 追蹤中 section and a link to recently handled items, with 全部 / 財務 / 投資 chips.
- **Count**: one number, on 通知 only, counting tasks (a card bill, a schedule definition, a statement to reconcile, a dividend batch, a system failure), not rows. Red when anything is overdue or failing. Other navigation items keep dots only. The timeline's 🔔 and its count go away; the timeline links to 通知.
- **Snooze**: 稍後提醒 / 明天提醒 hides a task from the list and the count until the chosen time, stored on the device.
- **對帳逐筆處理** at `/notifications/recon/:statementId` (right pane at ≥ 1024 px, full screen below): one case per screen, bank line and ledger entry side by side, answers as full-width buttons with keys 1–3, 以後都這樣 as a switch, cases about the same ledger entry handled together, progress across the statement, a stop-any-time end state. Transfer cases get 改成帳單的轉帳 and 配成轉帳 (#150) and `ambiguous` cases get 改金額.
- **New task sources** the SPA does not read today: `GET /api/portfolio/imports/cathay-statement/pending` and `/runs/latest` (with 確認 through `POST …/days/{date}/ack`), `GET /reconciliation/audit?event=auto_pay_skipped`, pending dividends past their payment date.
- **One toast** (shell-level service replacing `p-toast` and `AccountingToastService`) and **one banner** primitive (錯誤 / 注意 / 資訊).
- **Auto-pay visibility**: the bill pill says 自動扣繳 MM/DD for linked cards when auto-pay is on; 記帳設定 gets the auto-pay switch and start date (existing `PUT /settings/reconciliation`).
- **匯入** gets a 國泰每日對帳單 status card (last run, dates that need the owner, 確認).
- **電子發票** (added 2026-11-07, after the e-invoice link shipped):
  - Invoices that need a decision (`ambiguous` + `near`) are one task in 要你確認, 「電子發票 · N 張要確認」. It opens a one-invoice-at-a-time flow at `/notifications/einvoices`.
  - Invoices without a ledger entry (`unmatched`) are one 追蹤中 row, 「有發票沒有帳 · N 張」. It opens a page grouped by seller, with 這 N 張都不用記帳 and 這家以後都不提醒.
  - The entry detail's 發票 section gets a designed card.
  - 記帳設定 › 發票 gets the date window and the mute-prompt threshold beside the existing reminder switch.
  - The count adds 1 for the invoice task, not one per invoice. `einvoice_ask` leaves the timeline and the reminders page together with `ask`.

## Capabilities

### New Capabilities

- `frontend-notification-center`: the 通知 page, task model and count, snooze, the reconciliation review flow, the e-invoice flow and page, the new task sources.

### Modified Capabilities

- `frontend-app-shell`: Navigation badges become dots except on 通知.
- `frontend-ui-primitives`: Toast service and Banner primitive.
- `frontend-accounting-ledger`: the timeline bell count is removed; auto-pay in the bill pill and settings; the entry detail's invoice card and the 發票 settings section.
- `accounting-einvoices` (new capability): `PATCH /einvoices/preferences` also accepts `date_window_days` and `mute_prompt_after`.
- `frontend-csv-import`: 國泰每日對帳單 status card on the import page.

## Non-goals

- No backend change except one: the e-invoice preferences PATCH (write scope) learns two more fields, so the owner can change them without the admin-scope settings PUT. Every other source and action already has an endpoint; the weekly-digest checks that live only in `notify/` (missing statements, cards without an auto-pay link, statements without a due date) stay in Discord and are a follow-up that needs an API.
- No push or e-mail notifications; Discord delivery (`accounting-notifications`) is unchanged.
- 提醒中心 (`/accounting/reminders`) keeps its pages for managing 借還款 and 週期／分期; it is no longer the entry point for pending work.

## Impact

- Frontend: new `components/notifications/*`, `services/notification-center.service.ts`, `services/toast.service.ts`, `components/ui/banner`; shell, sidebar, mobile header, timeline header, accounting settings, billing pill, import page. `NavBadgeService` folds into the notification service.
- Portfolio routes have no auth today (#117); the Cathay ack is a write without auth, as every portfolio route is.
- Design reference: `design_handoff_v3/` (NotifDesktop, NotifPhone, ReconPhone, FeedbackSpec; for e-invoices InvoiceDesktop, InvoiceUnmatchedPhone, InvoiceSpec).
