# Scheduled transactions (週期 / 分期 / 待完成交易) — design

Status: owner approved the direction (2026-10-03) with three amendments: native definitions are in scope, posting is automatic by default (like 自動扣繳), recurring items get their own area under 借還款追蹤. This document is the spec for the OpenSpec change `add-accounting-schedules`; the task plan follows it.

## 1. Goal
HomeHub replaces MOZE's 週期 (recurring) and 分期 (installment / loan) events and its 待完成交易 queue:
- a loan (信貸) taken once is repaid per period; each posted repayment lowers the loan's 剩餘 and books its 利息;
- recurring transfers/expenses post themselves on their dates;
- anything that needs the owner's confirmation waits in 待完成交易 (提醒入帳 mode);
- MOZE's already-generated future records are honoured, and new definitions can be created in HomeHub so the stream continues after MOZE is retired.

## 2. Facts (real backup, aggregates only)
- 597 enabled future-dated MOZE records (2026-10 … 2036-08): 204 loan repayments (type 6), 204 interest rows (type 15), 66 transfers, 63 rewards, 60 expenses. Phase 2a kept each as `moze_schedule(kind='skipped_record')`; the schedules release maps them to periods; 534 carry `eventID` (generating period/installment), 471 `packageID` (repayment + interest packaged). Repayments carry `relatedID` → the loan's payable (moze_id).
- 11 `AHPeriod` (recurring definitions: `unit` 1 = week? / 2 = month, `days`, `startDate`, `times`, `type`), 14 `AHInstallment` (loan/installment definitions: `account`, `total`, `installment` (per-period amount), `remainder`, `dayOfMonth`, `dateInfo` (dates per period), `interestType`, `interestRate`, `rewardIntoType`, `receivableType`, `startDate`, `times`).
- Credit-card auto-pay in MOZE creates transfer records tagged `AutoPaid-…` on the due date; HomeHub's 繳費提示 already reads those as payments.

## 3. Domain model
### 3.1 `schedule_definition`
| column | meaning |
|---|---|
| id, moze_id (nullable, unique) | MOZE `AHPeriod`/`AHInstallment` identifier when imported |
| kind | `recurring` \| `installment` |
| name | owner label (e.g. 信貸 每月還款, Netflix) |
| template (JSONB) | the entry/transfer payload to post: lines `[{kind, account_id, to_account_id?, counterparty_id?, category_id?, amount, currency, loan_entry_id?, name?}]` — a loan line has `loan_entry_id` (the payable it repays) and an optional interest line |
| interval_unit, interval_n | `day` \| `week` \| `month` \| `year`, every N |
| anchor_date, day_of_month (nullable) | first date; monthly anchor (clamped to month length) |
| times (nullable), end_date (nullable) | finite run; null = until stopped |
| posting_mode | `auto` (default) \| `confirm` (提醒入帳) |
| status | `active` \| `paused` \| `ended` |
| generated_until | last date instances exist for (rolling horizon = 13 months ahead) |
| created_locally | true for definitions made in HomeHub (never swept by a re-import) |

### 3.2 `schedule_instance`
| column | meaning |
|---|---|
| id, definition_id, seq (k of N) | |
| due_date | the period's date (Taipei), holiday policy none in this phase |
| moze_id (nullable, unique) | the MOZE skipped record's id when imported (survives full re-import) |
| status | `pending` \| `posted` \| `skipped` |
| posted_entry_ids (JSONB), acted_at, acted_by (`auto` \| `owner`) | |
| amount_override (nullable) | per-instance amount from MOZE (interest varies per period) |

Posting an instance writes its lines through the existing services in ONE transaction under the lock-order contract: repayment → settlement entry with `settles_entry_id = loan_entry_id` (422 if the loan is closed or missing); interest → entry kind `interest` on the paying account; transfer → transfer pair; expense/income → entry. Entry dates = `due_date`, `posted_date = due_date`, `source = schedule`. Idempotent (a posted instance never posts twice). Deleting a posted entry resets its instance to `pending` with a note.

### 3.3 Import mapping (backup importer)
- `AHInstallment` → definition kind `installment` (lines from the MOZE repayment/interest package shape, `loan_entry_id` resolved through the first repayment's `relatedID`); `AHPeriod` → `recurring` (lines from the first generated record of that `eventID`).
- Each MOZE future record → an instance of its definition (`moze_id` = record id, `amount_override` from `total`, packaged interest merged into the same instance). Future records without an `eventID` → a single-instance definition of kind `recurring` with `times = 1`.
- Re-import: definitions/instances with a `moze_id` are upserted; `posted`/`skipped` statuses are kept; `created_locally` definitions untouched. The old `moze_schedule` table is dropped by the migration; the post-upgrade backup import recreates its content as definitions and periods.

## 4. Posting engine
- A scheduler inside the accounting service: at 00:05 Asia/Taipei daily and once at startup (catch-up), post every `pending` instance of an `active`, `posting_mode = auto` definition with `due_date <= today` (and on or after the definition's `auto_post_from`, never a reopened one), per definition in `seq` order, never `due_date` order: a period waits while an earlier `seq` of the same definition is pending and due for the job, or failed (`last_error`), even when the owner moved that earlier period's `due_date` past this one's (Multica R-F6); an earlier period the job may not post (due before `auto_post_from`, or reopened) without an error does not hold the series. Each post is its own transaction; failures are logged (no owner data) and the instance stays `pending` with `last_error` text for the UI.
- `confirm` definitions never auto-post; their due instances show in 待完成交易.
- Generation: definitions roll their instances forward to `today + 13 months` on the same job; editing a definition regenerates only `pending` instances after today.
- Endpoints: `GET /schedules/definitions`, `POST/PUT/DELETE /schedules/definitions/{id}` (+ `/pause`, `/resume`, `/end`), `GET /schedules/instances?from=&until=&status=`, `POST /schedules/instances/{id}/post|skip|reopen`, `POST /schedules/definitions/{id}/catch-up` (posts all pending ≤ today; the 補入帳至今天 button), `POST /schedules/run-now` (manual trigger of the daily job, token-protected like everything else: behind the optional `ACCOUNTING_API_TOKENS` bearer auth of #42 when it is set).

## 5. UI
### 5.1 Entry form 進階 tab (single / recurring / installment)
- 單次 = today's behaviour.
- 週期: 每 N {天|週|月|年}, 起始日, 結束 {無限期 | N 次 | 日期}, 入帳方式 {自動入帳 | 提醒入帳}. Saving creates the definition and posts nothing by itself (the first instance posts on its date; "起始日 = today" posts today via the catch-up).
- 分期 (for a payable/loan or an expense): 總額, 期數, 首次還款日, 每期金額 (auto = total / times, editable), optional 利息 per period (flat amount; rate maths deferred), 入帳方式. For a loan: the payable is created now (money received), repayments are the instances.
- Editing an existing entry that belongs to a definition offers 編輯這一筆 / 編輯整個排程.

### 5.2 提醒中心
- Tab 待完成交易: `confirm` instances due (today and overdue, oldest first, `已逾期 N 天` in the warning tone) plus the next 30 days; each item: title, lines, total, [入帳] [略過] (略過 asks inline once), per-definition [補入帳至今天] when more than one is overdue. The bell count includes these.
- Under 借還款追蹤: section **週期／分期**: one row per active definition — name, next date, `已入帳 k / N` (or `每月` for unlimited), 剩餘 for loans, mode badge (自動 / 提醒), paused state; tap → manage sheet (pause/resume, change mode, end, edit).
- Posting failures (`last_error`) show on the definition row with a retry.

### 5.3 Details
- Loan (payable) detail: `剩餘 −X · 已還 Y · 下期 MM/DD`, link to its definition.
- Entries posted by a schedule show a `週期 #k/N` / `分期 #k/N` pill (MOZE wording) and a link back.

## 6. Out of scope (later)
Holiday shifting (`dayAdjustmentPolicy`), interest-rate maths, reward lines from schedules (63 MOZE rows ignored; rules engine covers rewards), push notifications, editing MOZE-generated amounts in bulk, CSV importer support.

## 7. Acceptance (preview data + synthetic tests)
- After import: 11 + 14 definitions, 597 − 63 instances. (Task 28 on the real backup: 11 recurring + 12 installment definitions — 2 installments have no records — and 542 instances, past ones included; all 3 loans' `remainder` differs from HomeHub's 剩餘, an open owner question; `design.md` "Implementation notes".) No silent backlog (proposal decisions 2 and 23): the job posts auto definitions' periods dated on or after the import day (`auto_post_from`) by itself; periods dated before it (MOZE's records between the backup's export and the import, backlog from 10/1) wait in 待完成交易 even for auto definitions, and owner amendment 4 (自動 catch up) is the 補入帳至今天 tap. After that tap (one catch-up per definition) every instance due ≤ today is posted, and loans' 剩餘 match MOZE's remaining principal (`remainder`) within the posted periods.
- Creating a 週期 in the form generates 13 months of instances; the job posts today's; pausing stops posting; ending removes pending instances.
- Re-import keeps posted statuses and local definitions.
- Suites green; per-task reviews; owner checklist on the preview.

## 8. Phasing
- Phase A (this change): §3–§5 as written, auto + confirm modes, catch-up, import mapping, form 進階 tab for 週期/分期, 提醒中心 tab and section.
- Phase B: holiday policy, interest-rate maths, notifications, bulk edit — tracked on AGENT-57.
