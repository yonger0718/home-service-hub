## Context

Today's pieces:

- `NavBadgeService` builds `accounting-timeline` (提醒中心 count), `accounting` (MOZE import review count) and `import` (recalc failed or last device import failed). `countReminders` adds unpaid card bills + counterparties with open debt + due or partial queue instances + `inbox.counts.ask`.
- 提醒中心 (`/accounting/reminders`, tabs 全部 / 待處理 / 信用卡帳單 / 借還款追蹤 / 待完成交易) renders the inbox through `InboxPanelComponent` and `InboxService.apply` (`POST /reconciliation/inbox/apply`, body `{cases[], action, params, answer, learn}`).
- Toasts: PrimeNG `<p-toast>` on seven portfolio components; `AccountingToastService` (one message, bottom) on accounting.

Data the SPA does not read yet: the inbox item's `transfer` field (R6c §7, #150); `GET /api/portfolio/imports/cathay-statement/pending` (`{items:[{trade_date,status,reason,since,last_at}], total, truncated}`) and `/runs/latest` (`{latest, latest_ok}`); `GET /reconciliation/audit?event=auto_pay_skipped&since=` (detail carries `reason` and the amounts).

## Decisions

### D1. One page, one entry point

Route `/notifications` (and `/notifications/recon/:statementId`). The sidebar gets a 通知 item above the groups, in both widths; it is not part of a group, so hiding groups never hides it. Below 760 px every page header shows a bell icon button before the gear (the one-row header rule allows shell icon buttons). The timeline's 🔔 becomes a plain link to `/notifications`; 提醒中心 stays reachable from 通知 rows (逐期處理, 查看借還款) and keeps its management role.

### D2. Task model, built client-side

`NotificationCenterService` (root) turns existing endpoints into tasks:

| Source | Endpoint / service | Task (one per) | Tier |
|---|---|---|---|
| Card bills | `BillingService` (existing) | card with a remaining balance | 已逾期 after the due date, 今天 on it, else 追蹤中 if due ≤ 7 days |
| Schedules | `GET /schedules/instances?queue=true` | definition with due or partial instances (count, sum, oldest due) | 已逾期 / 今天 |
| Reconciliation | `GET /reconciliation/inbox` | statement with `ask` items | 要你確認 |
| Dividends | `GET /api/portfolio/dividends` (`receipt_status=pending`) | one batch for rows whose `payment_date` ≤ today | 要你確認; rows without a date or later → 追蹤中 |
| Import | recalc status + `ImportHistoryService.lastFailed()` | the failure | 系統 |
| 國泰 daily statement | `…/cathay-statement/pending`, `/runs/latest` | each pending date; a stale or failed latest run | 系統 |
| Auto-pay | `GET /reconciliation/audit?event=auto_pay_skipped&since=<30 days>` | each statement whose latest auto-pay row is skipped and not since booked | 系統 |
| Debts | `GET /accounts/debts` | one summary | 追蹤中 |
| MOZE import review | existing `importReviewCount` | the import run | 要你確認 |

Each task: `{id, source: 'ledger'|'portfolio', tier, icon, title, when, meta, amount?, primary: {label, route|command}, secondary[]}`. `id` is stable (`bill:<accountId>:<due>`, `sched:<definitionId>`, `recon:<statementId>`, …) so snoozes survive reloads. A failing source drops its tasks and shows one muted line 「無法讀取：<來源>」 at the bottom; it never blocks other sources.

Run rule for 國泰 (Cathay design §7.2, `cathay_run_time` 06:40, grace 15 min): a task when `latest` is missing, `latest.result` is not `ok`, or `latest.run_at` is before the most recent 06:40 that is at least 15 minutes in the past.

### D3. Count

`count = tasks in 已逾期 + 今天 + 要你確認 + 系統, minus snoozed`. Pill colour `--c-red-ink` when any task is 已逾期 or 系統, else `--app-primary`. Accessible label `通知，N 件要處理[，M 件已逾期]`. The badges setting still turns everything off. Replaces `countReminders` as the shell's number; the function stays for 提醒中心's own tabs.

### D4. Snooze is device-local

`hh-notif-snooze` in localStorage: `{taskId: untilIso}`; options 稍後（3 小時）, 明天 09:00, 下週一 09:00. A snoozed task leaves the list and the count, and returns when the time passes or the task's content changes (amount, count or due date — the id stays, so the stored fingerprint is compared). Nothing is written to the server.

### D5. Review flow reuses the inbox contract

- Order: the statement's `ask` items in inbox order. Progress `n / total` plus 已自動對上 `claimed / lines` from the statement.
- Answers are the item's `actions` mapped to the existing labels (`inbox-panel.ts`), first one solid. `match_candidate` opens the existing match sheet; `record` the existing record sheet.
- 以後都這樣 is a switch bound to `learn` (shown only where the panel offers learning today).
- **Related cases**: when two `ask` items reference the same `entry.id` (e.g. `amount_differs` and `ledger_only`), the screen shows the first with the note 「這筆也出現在…，選了就一起處理」 and applies with both cases in `cases[]`; the second is skipped in the sequence.
- **Transfers (#150)**: when `item.transfer.adjust_transfer` is set, offer 改成帳單的轉帳 (`POST /reconciliation/cases/{id}/actions`, `{action:'adjust_transfer', params:{entry_id, line_id}}`; on an ambiguous case the owner first picks one of `candidate_entry_ids`). When `pair_transfer.peer_candidates` is non-empty, offer 配成轉帳 with a picker of the candidates (`params:{peer_line_id}`). On `ambiguous` cases where `adjust_amount` is allowed, offer 改金額. Refusal codes show as an inline error under the buttons.
- Keys 1–3 answer, S skips (stays open, moves on), ← → move without answering, Esc closes. Global shortcuts stay off while a text field has focus.
- Leaving mid-way is fine; the end state says how many were handled and how many remain.

### D6. One toast

`ToastService` at shell level: one visible toast, newest replaces older; variants 完成 (5 s, optional 復原), 進行中 (stays until replaced by its result), 失敗 (stays until closed, optional 重試). Position: desktop bottom-left of the content area (the FAB owns bottom-right); phone above the tab bar. `role="status"` (完成 / 進行中) or `role="alert"` (失敗). 復原 is offered only where an inverse endpoint exists (`POST /reconciliation/actions/{id}/revert`). All `<p-toast>` usages and `AccountingToastService` move to it.

### D7. One banner

`<app-banner tone="error|warn|info" title action dismissible>`: error and warn are not dismissible and disappear when the condition clears; info may be dismissed (remembered per id on the device). Used by: import failure, bill read error, schedule definition failing, quote delay, Cathay run stale (on the import page).

### D8. Navigation dots

Group items show a dot when their page has a task: 股利 (dividend batch), 匯入 (import or Cathay failure, red), 帳戶 (a card bill overdue, red), 記帳記錄 (MOZE import review). Counts appear only on 通知. The phone tab bar keeps group dots.

### D9. Auto-pay surfaces

- Bill pill: for a card with `auto_pay_account_id` while `auto_pay.enabled` and the statement is on or after `enabled_from`, the pill reads 自動扣繳 MM/DD (the shifted business day) until paid; after the booking it reads 已自動扣繳.
- 記帳設定 › 對帳: switch 自動扣繳 (`auto_pay.enabled`) and date 起始日 (`enabled_from`, required when on), saved through `PUT /settings/reconciliation`.
- Skip reasons map to copy: `over_cap` 超過單筆上限, `monthly_budget` 超過當月上限, `no_link` 沒有設定扣款帳戶, `link_ineligible` 扣款帳戶不符規定, `header_disputed` 帳單金額待確認, `twin` 已有相同的繳款記錄, `duplicate_statement` 重複的帳單, `cutoff` 早於起始日, `expired` 已超過重試期限. Transient reasons are 系統 tasks; `twin`, `cutoff`, `duplicate_statement` are informational and do not create a task.

## Risks / Trade-offs

- Client-side aggregation adds requests on load; each source is fetched once per refresh (start, window focus, visibility) with the existing 30 s throttle.
- Snoozes do not sync between devices; acceptable for one owner, revisit if a second device complains.
- The review flow depends on the inbox labels staying in one place; move them out of `inbox-panel.ts` into a shared map first.

## Migration Plan

1. PR A — `ToastService` + `<app-banner>`; migrate all toasts and banners. No new features.
2. PR B — `NotificationCenterService` with ledger sources, `/notifications` page, count and dots, sidebar and phone header entry, timeline bell link.
3. PR C — portfolio sources: dividends, import, 國泰 daily statement; status card on 匯入.
4. PR D — review flow incl. related cases, keyboard, learn switch.
5. PR E — transfer and 改金額 answers (#150); auto-pay pill, settings and skipped tasks; snooze.

## Open Questions

- Should 提醒中心 move under 通知 as tabs later? This change leaves it in 財務.
