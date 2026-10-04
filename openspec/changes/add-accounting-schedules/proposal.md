## Why

MOZE generates the owner's loan repayments, recurring transfers and subscriptions as future-dated records, and its 通知中心 › 待完成交易 holds the ones that wait for a tap. HomeHub has none of this: phase 2a stores the 597 enabled future records, 11 `AHPeriod` and 14 `AHInstallment` rows as raw JSON in `moze_schedule` and shows them read-only, so after cutover (2026-10-31) nothing would book the 信貸 repayment on the 9th, the interest that goes with it, the monthly transfers or Netflix. The owner approved the direction on 2026-10-03 (`docs/superpowers/specs/2026-10-03-scheduled-transactions-design.md`) with three amendments: definitions can be created in HomeHub, posting is automatic by default (like 自動扣繳), and recurring items get their own area under 借還款追蹤.

## What Changes

- **Data model** (accounting-service): new tables `schedule_definition` (kind `recurring | installment`, a JSONB `template` of lines, interval, anchor, `times` / `end_date`, `posting_mode auto | confirm`, `status active | paused | ended`, `generated_until`, `created_locally`, `moze_id`) and `schedule_instance` (one per period: `seq`, `due_date`, `status pending | posted | skipped`, `posted_entry_ids`, `acted_at` / `acted_by`, `amount_override`, `last_error`, `moze_id`). `entry_source` gains `schedule`. `moze_schedule` is dropped; its rows are reproducible from the backup, and the post-upgrade step is a backup import.
- **Posting engine**: posting an instance writes its lines through the existing ledger services in one transaction (repayment → settlement of the loan, interest → `interest` entry, transfer → transfer pair, expense / income / receivable / payable → entry), dated `due_date`, `source = 'schedule'`, idempotent. Deleting an entry of a posted period deletes only that entry; the period becomes partial, or returns to 待完成交易 when nothing is left.
- **Daily job**: an in-process APScheduler job at 00:05 Asia/Taipei, once at startup, after every backup import and every 10 minutes after an interrupted run generates instances 13 months ahead and posts every due `pending` instance of an `active`, `auto` definition dated on or after the day the definition entered HomeHub, each in its own transaction, under an advisory lock so only one runner works at a time; failures stay `pending` with `last_error`. Every schedule write and the importer coordinate through the import advisory key and lock schedule rows before ledger rows.
- **Endpoints** under `/api/accounting/schedules`: definitions (list, get, create, update, delete, pause, resume, end, mode, catch-up), instances (list, edit — one period, this and the following ones, or all, decision 24 —, post, skip, reopen) and `run-now`.
- **Backup importer**: `AHInstallment` → `installment` definitions, `AHPeriod` → `recurring` definitions, each enabled future record → an instance of its definition (packaged interest and transfer in-legs merged into one instance; 63 reward rows ignored), past records with an `eventID` → `posted` instances so 已入帳 k / N is right, disabled ones → `skipped`. Re-import upserts by `moze_id`, adopts HomeHub-generated periods on the same date, keeps owner and job statuses and owner edits, never touches local definitions (except re-pointing loan links), and skips past MOZE records that HomeHub already posted or skipped. `GET /api/accounting/imports/schedules` is removed.
- **Frontend**: the entry form's 進階 area gains 單次 / 週期 / 分期 tabs with 入帳方式 (自動入帳 / 提醒入帳); editing a scheduled entry offers 編輯這一筆 / 編輯整個排程; 提醒中心 gains a 待完成交易 tab and a 週期／分期 section under 借還款追蹤 with a manage sheet; the bell counts due 待完成交易 items; on the 週期 / 分期 tabs FX, split, fee / discount, rewards, photo and invoice are hidden (`排程不支援`); a loan's detail shows `剩餘 · 已還 · 下期`; entries posted by a schedule show a `週期 #k/N` / `分期 #k/N` pill. The settings page's read-only 分期 / 週期 list is replaced by a link to the new section.

## Capabilities

### New Capabilities
- `accounting-schedules`: definitions, instances, generation, posting, the daily job, schedule endpoints, loan summaries.

### Modified Capabilities
- `accounting-ledger`: `schedule` entry source; `interest` entries may carry the loan's counterparty; entries posted by a schedule (editable, never touched by imports, deleting one updates its period); loans and split groups referenced by schedules are protected.
- `accounting-moze-backup-import`: future records and `AHPeriod` / `AHInstallment` map to definitions and instances instead of `moze_schedule`; re-import rules; report keys.
- `frontend-accounting-ledger`: 進階 schedule tabs, edit scope choice, 待完成交易 tab, 週期／分期 section, bell count, loan breakdown, schedule pills; settings page drops the read-only schedule list.

## Impact

- **Code**: `services/accounting-service/app/models/schedule.py` (new), `app/services/schedule_service.py`, `schedule_generation.py`, `schedule_posting.py`, `schedule_job.py`, `schedule_import.py` (new); `entry_write_service`, `transfer_service`, `settlement_service` gain a `source` keyword and an internal lock-check bypass for schedule posting; `entry_write_service.delete_entry` takes the schedule locks first; `moze_backup_import_service` maps schedules; `app/routers/schedules.py`, `app/schemas/schedules.py` (new); `app/main.py` starts and stops the scheduler. New Alembic revision after `7b1e4a2c9d05`. Frontend: `entry-form` (進階 tabs), `entry-detail` (loan line, pill, edit scope), `reminders` (tab, section, manage sheet), `timeline` (bell count, pill), `accounting-settings` (list removed), `accounting.service.ts`, `accounting.model.ts`, new `schedule-math.ts`.
- **Dependencies**: `apscheduler==3.11.0` in accounting-service (the version stock-portfolio-service already pins).
- **APIs**: new `/api/accounting/schedules/…` endpoints; `GET /api/accounting/entries…` rows and the entry detail gain `schedule` (definition id, kind, seq, times); the entry detail of a loan gains `loan_schedule`; `GET /api/accounting/imports/schedules` is removed.
- **Runtime**: the job runs inside the single uvicorn process pm2 starts; `ACCOUNTING_SCHEDULER_ENABLED` (default `true`, tests `false`) turns it off. A pm2 restart triggers the startup catch-up.
- **Data**: the migration drops `moze_schedule` (the enum value `schedule` is added in an autocommit block); the runbook's post-upgrade step is a backup import, which creates the imported definitions and instances. Local definitions and `schedule` entries are never touched by an import. Downgrade recreates `moze_schedule` empty and refuses while HomeHub-posted data exists.
- **Depends on**: PRs #40–#44 (calendar, billing hints, reminder centre on `feat/accounting-reminders`, settlement links and `is_closed`) being on `main` first; this change extends them and restates in its frontend delta the reminder-centre behaviour it relies on.
- **Phase B** (AGENT-57, not here): holiday shifting (`dayAdjustmentPolicy`), interest-rate maths (本息均攤 / 本金均攤, rate changes, 提前償還本金 / 提前繳清), 既有欠款分期 entry, 起始期數 in the form, reward lines from schedules, push notifications and low-balance warnings on 待完成交易, 修改連同未來 of a period's date or rule from a single instance (amounts are in scope, decision 24), future instances on the calendar, CSV importer support.

## Decisions for the owner

1. **Automatic by default.** New and imported schedules post themselves (自動入帳) on their date; 提醒入帳 is the per-schedule alternative.
2. **No silent backlog.** Neither the first run after deploy / import, nor a back-dated 起始日, nor switching a schedule from 提醒入帳 to 自動入帳 posts past periods; they wait in 待完成交易, and 補入帳至今天 posts them on your tap.
3. **Deleting one record of a period** deletes only that record; the period is marked 部分入帳 with a 重新入帳 offer. When every record of a period is deleted it returns to 待完成交易 and is never re-posted automatically; a period MOZE booked becomes 略過.
4. **Mirror period.** Until cutover HomeHub posts imported schedules too; the next backup import skips the MOZE records HomeHub already posted or skipped and reports amount differences. Rules of imported schedules can only be edited after cutover; pause, resume, end, mode and per-period actions work now.
5. **Resume** skips the months a schedule was paused unless you choose 補入帳. Paused schedules leave 待完成交易 and the bell.
6. **End** deletes the remaining periods; a schedule can be deleted only while nothing was posted.
7. **Loans.** The per-period repayment is total ÷ periods rounded down; the last repayment is clamped to what is still owed (300,000 over 36 → 8,333 × 35 and 8,345). When a loan is settled or closed, its remaining periods are skipped (`貸款已結清`) and the schedule ends. 略過 never changes 剩餘. Interest is a flat amount per period until Phase B.
8. **Card installments** put the remainder on the last period (MOZE 分期餘額納入 末期); 剩餘 is shown on the schedule row instead of a 分期帳款 account.
9. **Scope of the form.** 分期 for 支出 and 應付款項 only; 轉帳 gets 週期 only; schedules carry no FX, split, fee / discount, rewards, photo or invoice (`排程不支援`).
10. **Protection.** An account, category, project, counterparty or loan used by an active schedule cannot be archived or deleted until the schedule ends.
11. **Bell** = unpaid card statements + open counterparties + 待完成交易 items due today or earlier.
12. **No holiday shifting**: a period posts on its calendar date.
13. **A failing period blocks the later ones** of the same schedule (a loan's periods post in order) until it is fixed, skipped or posted by hand; other schedules keep posting.
14. **Partial periods** (some records deleted) stay in 待完成交易 and the bell until you choose 重新入帳 or 保留部分.
15. **One period = one action**: a repayment and its interest, or both legs of a transfer, are one period, so the real backup gives about 297 periods rather than 534 rows.
16. **MOZE's past periods** appear as already posted (with their `分期 #k/N` / `週期 #k/N` pills); MOZE-disabled periods appear as 略過.
17. **首次還款日** defaults to one month after the entry date.
18. **During an import** (a few seconds) schedule actions are refused with `匯入進行中，請稍後再試`.
19. **編輯整個排程** drops the future periods you edited one by one and regenerates them from the new rule; overdue, posted and skipped periods stay.
20. **Interest names the lender**: a loan's interest record carries the loan's counterparty.
21. **立即執行 (run-now)** has no extra password; it is protected like the rest of the app: Tailscale-only access plus, when `ACCOUNTING_API_TOKENS` is set, the bearer-token auth of #42 that covers every `/schedules/*` endpoint.
22. **The settings page's 分期 / 週期 list** becomes a link to 提醒中心 › 借還款追蹤 › 週期／分期.
23. **Switching mode never posts a backlog** (see 2); only 補入帳至今天 does.
24. 每期金額可調整；調整時詢問套用範圖：僅這一期／這一期與之後／全部週期。已入帳、已略過的期數不變；匯入的排程在 cutover 前也可以這樣調金額（規則仍鎖）。
