# Add Accounting Schedules (週期 / 分期 / 待完成交易) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HomeHub books the owner's loan repayments (with their interest), recurring transfers, subscriptions and card installments by itself: schedule definitions and per-period instances, a posting engine on the existing ledger services, a daily in-process job, schedule endpoints, MOZE backup mapping with mirror-period reconciliation, and the SPA pieces (進階 單次 / 週期 / 分期, 待完成交易, 週期／分期 under 借還款追蹤, loan breakdown, schedule pills).

**Architecture:** Two new tables (`schedule_definition`, `schedule_instance`) replace `moze_schedule`. Pure date maths lives in `schedule_rules.py`; template validation in `schedule_templates.py`; every schedule write path goes through `schedule_locks.py` (shared import advisory key → definition → instance → the ledger's order). `schedule_generation.py` rolls instances 13 months ahead; `schedule_posting.py` writes one instance in one transaction through `entry_write_service`, `transfer_service` and `settlement_service` (which gain `source=` and an internal cutover bypass); `schedule_service.py` holds the definition and instance actions; `schedule_read.py` builds every response shape; `schedule_entry_hooks.py` is the ledger's side (entry delete, reference protection). `schedule_job.py` is the APScheduler 3.11 job plus a CLI. The backup importer maps `AHPeriod` / `AHInstallment` and future records through the pure `schedule_import_map.py` and applies them with `schedule_import.py` inside its ledger transaction. The Angular SPA adds `schedule-math.ts`, an `app-schedule-tabs` block in the entry form, a 待完成交易 tab and a 週期／分期 section with a manage sheet in 提醒中心, schedule pills and the loan breakdown.

**Tech Stack:** Python 3.13, FastAPI 0.129, SQLAlchemy 2.0.49, Alembic 1.18, APScheduler 3.11.0 (new in accounting-service, same pin as stock-portfolio-service), PostgreSQL 16 (`stonk-postgres-1`), pytest 9; Angular 21 standalone components with signals, Vitest via `ng test`.

**Spec:** `openspec/changes/add-accounting-schedules/` (`proposal.md`, `design.md` D28–D43 and "Decisions for the owner", `specs/accounting-schedules/spec.md`, `specs/accounting-ledger/spec.md`, `specs/accounting-moze-backup-import/spec.md`, `specs/frontend-accounting-ledger/spec.md`) and the owner's design `docs/superpowers/specs/2026-10-03-scheduled-transactions-design.md`. Phase 2a decisions D1–D27 (`openspec/changes/archive/2026-10-03-add-accounting-entry/design.md`) stay in force. The spec files were reviewed twice: implement them as written; where this plan and a spec disagree, the spec wins and the executor stops and reports.

**In-service API paths.** Caddy and `frontend/proxy.conf.js` strip `/api/accounting`, so routers serve `/schedules/...`; the SPA calls `/api/accounting/schedules/...`.

**Worktree:** `/home/opc/workspace/home-hub-schedules`, branch `feat/accounting-schedules` (off `main`). All commands run from there. This shell aliases `rm` and `cp` to interactive forms; use `git rm`, `rm -f` and `command cp`. The root `.env` is symlinked at the worktree root; never print it. Never read `~/workspace/moze-backup` except through the acceptance script of Task 28, which prints aggregates only.

## Global Constraints

- **Dates.** Every schedule date is a naive Asia/Taipei date: `rule_date`, `due_date`, `anchor_date`, `end_date`, `auto_post_from`, `generated_until`, the horizon and `overdue_days`. "Today" is `ledger_service._today()`, always called through the module attribute (`from . import ledger_service` … `ledger_service._today()`), so tests patch one function. Timestamps (`acted_at`, `last_error_at`, `reopened_at`, `created_at`, `updated_at`) are TIMESTAMPTZ.
- **Horizon.** `horizon(today) = add_months(today, 13)` (same day of month, clamped); generation inserts occurrences with `date ≤ horizon` and sets `generated_until = horizon`.
- **Occurrence k** of a rule: `day` → `anchor + k·n` days; `week` → `anchor + 7·k·n` days; `month` → the month `k·n` after the anchor's, day `min(day_of_month or anchor.day, month length)`; `year` → the anchor's month `k·n` years later, clamped the same way. Always from the anchor, never from the previous occurrence. ★The anchor is never rebased implicitly (Multica R-F3): `update_definition` changes `anchor_date` only when the owner sends a different one (then a `month` / `year` rule without `day_of_month` takes the new anchor's day); a Jan-31 rule stays Jan-31-based (Feb-28, then Mar-31) and a Feb-29 yearly anchor returns to Feb-29 in leap years (Task 10).
- **`rule_date` / `due_date`.** `rule_date` is the occurrence the rule produced (set at generation or import, never changed by an instance edit); `due_date` equals it unless the owner moved the period. Generation continues strictly after the latest instance's `rule_date`; adoption matches `rule_date` first, then `due_date`. No holiday shifting.
- **`auto_post_from`.** The Taipei date a definition was created locally or first imported; `PUT …/mode` to `auto` (from `confirm`) moves it to today. The job never posts an instance due before it, never posts a reopened instance (`reopened_at` set), never posts `confirm`, `paused` or `ended` definitions. Only 補入帳至今天 (`catch-up`), `resume` with `backlog = post`, `post` and `repost` post past periods, all with `acted_by = owner`.
- **Lock order (D32), every path:** `pg_try_advisory_xact_lock_shared(IMPORT_LOCK_KEY)` (409 `import_running` when an import holds it) → `schedule_definition` row (`FOR SHARE` when posting or acting on one instance, `FOR UPDATE` when editing, pausing, resuming, ending, changing mode or deleting; a post or repost of a template with a loan line takes `FOR UPDATE` too, because it may end the definition — two `FOR SHARE` holders that both upgrade deadlock) → `schedule_instance` row(s) (`FOR UPDATE`, ascending id; the job adds `SKIP LOCKED`; a post of a loan template also locks the definition's later pending instances, `seq > k`, before any entry lock) → the ledger's order (`entry_group` rows ascending → target entries with their transfer legs in one statement, the loan entry included in that statement for a repost → nothing else). No path that holds an entry or group lock ever locks a schedule row. The entry-delete path reads the instance unlocked, then takes the key, the definition (`FOR SHARE`, `FOR UPDATE` when the definition is `ended`, since the hook may revive it) and the instance (`FOR UPDATE`), re-checks containment, then continues with group → entries. The backup importer holds the key exclusively (retrying `pg_try_advisory_lock` for up to 30 s while shared holders finish) and locks every definition, then every instance, ascending id, before deleting any entry.
- **Import shared lock on every schedule write path:** create, update, delete, pause, resume, end, mode, catch-up, instance edit, post, skip, reopen, repost, accept-partial, generation, run-now and the entry-delete path that touches an instance. The refusal is `schedule_locks.ImportRunningError` (a `ConflictError`, message `import_running`), answered as HTTP 409 with body `{"code": …, "message": "import_running", …}`.
- **Posting.** One database transaction per instance; idempotent by row lock + `status` + the partial unique index `ux_schedule_instance_posted_day`; entries dated `entry_date = posted_date = due_date`, `entry_time = NULL`, `source = 'schedule'`, `moze_id = NULL`, `import_run_id = NULL`, category defaults never remembered. On any error the transaction rolls back and a second short transaction writes `last_error` (`"<field>: <message>"`, never an amount, name or counterparty) and `last_error_at`; the instance stays `pending`. Multi-instance operations commit status changes first, then post one transaction each in `seq` order, stopping at the first failure. ★The job also attempts each definition's periods in `seq` order (never `due_date` order), and under the definition lock a period waits while an earlier `seq` of the same definition is pending and due for the job, or failed — even when its `due_date` was moved past this one's (Multica R-F6, D31 over D34; Task 15 `blocked_by_earlier`).
- **Amounts.** Template and override amounts are unsigned decimal strings with at most 4 decimals (`"8333"`, `"0.5"`); the server applies signs. A line's amount for a period is `amount_override[i]` when the instance has an override, else the template's; `"0"` means the line is not written. Overrides are aligned with the template's lines on every write (422 naming `amounts`). Re-imports never overwrite `edited_by_owner` instances, nor the template amounts or pending overrides of a `template_owner_edited` definition (Task 17); an owner-edited pending period whose MOZE record turned past keeps the owner's choice and the record is not imported (Multica R-F1, Task 17 `covered_records` → `owner_pending`, Task 18). An installment with a total keeps Σ = total under a scoped amount edit: its last period takes the residual of the actual allocations (Multica R-A1, Task 12 `installment_residual`). A pending period's amounts can be edited for that period only (`scope = this`), for it and the later ones (`following`) or for every pending period (`all`) — Task 12, proposal decision 24.
- **Partial periods.** Deleting an entry listed by a posted instance deletes only that entry (with children and transfer pair); remaining entries keep the instance `posted` with `is_partial = true` and note `部分入帳記錄已於 YYYY-MM-DD 刪除`; none remaining → `skipped` (`入帳記錄已刪除`) when `acted_by = import`, else `pending` with `reopened_at = now()` and note `入帳記錄已於 YYYY-MM-DD 刪除`. A pending or partial result revives an `ended` definition. Partial periods sit in 待完成交易 and the bell until 重新入帳 (`repost`) or 保留部分 (`accept-partial`).
- **Adoption (re-import).** An instance without `moze_id` of the same definition whose `rule_date` (else `due_date`) equals a MOZE record's date is adopted (gets `moze_id`, `moze_record_ids`, and, when pending and not owner-edited, the override); a posted adopted instance keeps its entries and the MOZE record is not imported (`past_records_already_posted`). Instances posted by `auto` / `owner`, skipped by anyone but `import`, and owner-edited pending instances are never changed by an import (their past MOZE records are not imported: `past_records_already_posted` / `…_skipped` / `past_records_owner_pending`); `created_locally` definitions are never written by an import except the loan-link re-pointing.
- **Cutover lock.** While `ACCOUNTING_IMPORT_LOCKED` is not `true`: `PUT` / `DELETE` of a definition with a `moze_id`, and `repost` (or `reopen` of a posted period, whose entries are MOZE rows) of an `acted_by = import` instance, answer 409 `locked_until_cutover`; `schedule` entries are editable at all times; posting against a `moze_backup` loan bypasses D19 for that target only.
- **Scheduler.** `ACCOUNTING_SCHEDULER_ENABLED` (default `true`; `false`, `0` and `no` disable it) gates the in-process APScheduler; `tests/conftest.py` sets it `false` for every test (autouse). The standalone importer CLI has no scheduler: after its import releases the lock it runs the job inline (`schedule_job.run_after_cli_import`): generation always, posting only when this switch is on, else a logged count of due but unposted instances (Multica R-F4, Tasks 15, 18); the API path keeps `trigger_after_import`. Job lock key `SCHEDULE_JOB_LOCK_KEY = 0x53434844` (`"SCHD"`), session-level `pg_try_advisory_lock` on a dedicated connection.
- **Logs and reports** carry ids, counts and error classes only — never names, amounts or counterparties (the import report's `loan_remainder_check`, `amount_differs` and `past_records_amount_differs.lines` are the only owner-facing lists with amounts, as the spec requires; amount differences are reported per line, never as period totals — Multica R-F5).
- **Owner data rule.** Tests build synthetic rows only. `~/workspace/moze-backup/MOZE_4.0.zip` is read only by Task 28's acceptance script against the disposable database `accounting_schedules_verify`, printing aggregates; nothing from it is committed or printed row by row. No step touches the production `accounting_db`.
- **Frontend.** Every page usable at 390 px without horizontal page scroll; tone tokens only (`var(--tone-neg, var(--c-red))`, `var(--tone-pos, var(--c-green))`, `var(--app-state-warning-bg)`, `var(--app-text-muted)`, `var(--app-border)`, `var(--app-primary)`); keyboard/IME contract: every key handler starts with `isHandledKey(event)`, sheets use `sheetKeyAction` (Esc closes and is marked handled, ⏎ in a field confirms, ⏎ on a button clicks natively), inline confirmations close on Esc; every load that can be superseded carries a request id and drops older answers; a 409 `import_running` from any schedule action (and an entry delete that touches a period) shows the toast `匯入進行中，請稍後再試` and leaves the page unchanged.
- **Copy (verbatim).** `單次` `週期` `分期`; `每 N 天|週|月|年`; `起始日`; `結束` `無限期` `N 次` `日期`; `入帳方式` `自動入帳` `提醒入帳`; `總額` `期數` `首次還款日` `每期金額` `利息` `還款帳戶`; footers `週期：#1 / 無限期（每月 / 22號）`, `週期：#1 / 12（每月 / 22號）`, `分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）`; `排程不支援`; `編輯這一筆` `編輯整個排程`; `待完成交易` `已到期` `即將到來` `已逾期 N 天` `今天` `重試` `入帳` `略過` `略過這一期？剩餘不變` `補入帳至今天` `部分入帳` `重新入帳` `保留部分`; period amount scope sheet `套用範圖` with `僅這一期` `這一期與之後` `全部週期`; `週期／分期` `已結束` `下期 11/09` `已入帳 k / N` `剩餘` `自動` `提醒` `已暫停` `需檢查`; `暫停` `繼續` `略過期間的 N 期` `補入帳` `結束後未入帳的 N 期將刪除` `刪除`; `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09`; pills `週期 #k/N`, `週期 #k`, `分期 #k/N`, badge `部分`; delete notes `同期的 利息 −$620 會保留，此期標示為部分入帳`, `此期將回到待完成交易`, `此期將標示為略過`; toast `匯入進行中，請稍後再試`; bell aria-label `提醒中心，N 項`; create then catch-up (Task 22, Multica R-F2) `排程已建立，入帳未完成；按 ✓ 重試入帳`, toast `排程已建立；這一期入帳失敗，請到待完成交易處理`; import report (Task 27, R-A3) `待入帳金額與 MOZE 不同 N 期`, `保留待入帳 N 期`, item line `<名稱> 第 k 期 <日期>：HomeHub $<金額> / MOZE $<金額>`.
- Commit after every task with a Conventional Commits subject and the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. **Month-end anchor across February.** A monthly rule anchored on the 31st must give 2026-02-28 and come back to 2026-03-31 (never drift to the 28th), and a yearly 02-29 anchor must land on 02-28 in common years — pinned by `test_month_end_anchor_generates_feb_28_then_returns_to_31` (Task 6) and `test_month_end_and_leap_day_clamp_from_the_anchor` (Task 4).
2. **A job run twice in one day.** The 00:05 run plus a restart (startup run) or a run-now on the same day must post each due instance once and generate nothing twice — pinned by `test_job_run_twice_in_one_day_posts_each_instance_once` (Task 15).
3. **Re-import after a local post.** HomeHub posted (or skipped) a period during the mirror month; the next backup holds MOZE's record of it as a past record: the record must not become an entry, the `schedule` entries stay, the loan's 剩餘 is counted once, and the report counts `past_records_already_posted` — pinned by `test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record` (Task 18).
4. **A loan closed while an instance is pending.** The owner settles the rest of the loan by hand (open amount 0) or MOZE marks it closed while the next period waits: the next post must write nothing, skip that and every later pending period (`貸款已結清`), and end the definition — pinned by `test_loan_settled_by_hand_while_pending_skips_the_rest_and_ends` (Task 8).
5. **A definition edited with a pending instance due today.** 編輯整個排程 on the day a period is due must keep today's pending instance (it is overdue-or-today, not "on or after tomorrow"), realign its override to the new lines, and regenerate from tomorrow with the next seq — pinned by `test_edit_keeps_the_instance_due_today_and_regenerates_from_tomorrow` (Task 10).

## Known Spec Conflicts

- Owner amendment 4 (自動 catch up) is implemented as 補入帳至今天 on tap, per proposal decision 2 — owner to confirm.
- Lock strength: `specs/accounting-schedules/spec.md` now states the `FOR UPDATE` upgrade for a loan-line post and for the entry-delete path on an `ended` definition (D32 refinements); the plan follows that text.
- Importer refinements beyond the spec text (plan review of Tasks 16–17; each keeps the spec's outcome or makes it stricter): an `AHInstallment` with `times < 2` becomes `kind = recurring` (the spec says every installment becomes `installment`, but `ck_schedule_definition_installment` needs `times ≥ 2`); a future record whose `eventID` names no period or installment becomes a single definition with review reason `event_missing`; template lines are the first group's lines plus any record kind a later group carries (so a later package's interest is never dropped); a "live" `AHPeriod` is one with at least one enabled future record — a period without one is set `ended` with `review_reason = not_live` (a reason outside the spec's list), and a later backup with an enabled future record sets it back to `active`; the end itself deletes no pending row, so owner-edited and HomeHub-generated pending rows stay (D37), and only the import's own end is ever undone; a finite period's `times` is capped at its highest mapped seq (Task 28.5 checks MOZE pre-generates every finite series).
- Owner decision 2026-10-04: per-period amount edits with scope — see proposal decision 24.
- Test data (owner data rule): spec scenarios, tests and fixtures use the synthetic names `範例銀行` and `範例卡`; the scenarios' arithmetic is unchanged.
- Multica AGENT-59 rulings (2026-10-04, R-F1 … R-A3) are written into the spec files, `design.md` (D34, D35, D37) and Tasks 10, 12, 15, 17, 18, 20, 22, 27, 28; where they changed spec text (no implicit anchor rebase; job order by `seq` with a sequence barrier; the CLI's inline job; owner-edited pending periods win over a past MOZE record; per-line amount reports; the installment residual) the spec now says so. Interpretations recorded here: (a) R-F6's barrier is "an earlier `seq` pending and due *for the job* (`auto_eligible`), or failed (`last_error`)": a pre-`auto_post_from` backlog period or a reopened period without an error does not hold the series, because the spec scenarios "No silent backlog after an import" and "Switching to automatic posting" require the later period to post while the earlier one waits for the owner; (b) R-A1's residual is written onto a *generated* pending last period; a last period not generated yet (a local loan longer than the 13-month horizon) is later generated by the landed `schedule_generation.last_period_override` (`total − amount × (times − 1)`), which equals the residual only when every other period follows the template — the ruling keeps that helper for the generation path, so the gap is recorded for the final review; (c) R-A2's comparison runs for every retained owner-edited pending period, not only on `template_owner_edited` definitions; (d) R-F5's in-leg of a transfer is reported with `kind = "transfer_in"`. Deferred minor (no plan change): Task 19's race helper serializes whole operations rather than forcing interleavings — for the final review.

Implementation-time confirmations recorded by Task 28 in `design.md` "Implementation notes": the MOZE weekday numbering of `AHPeriod.days` for weekly periods (assumed 1 = Sunday … 7 = Saturday, Apple `Calendar`; a wrong assumption shows as `interval_mismatch` on every weekly period, never as wrong dates) and whether a MOZE posting-mode field exists (none is assumed; every imported definition is `auto`).

## File Structure

Backend — `services/accounting-service/`:

| Path | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | `apscheduler==3.11.0` |
| `app/models/schedule.py` | Create | `ScheduleDefinition`, `ScheduleInstance`, schedule enums |
| `app/models/ledger.py` | Modify | `ENTRY_SOURCES` gains `schedule`; `MozeSchedule`, `SCHEDULE_KINDS`, `schedule_kind_enum` removed |
| `app/models/__init__.py` | Modify | Exports |
| `alembic/versions/c4e8b2f1a7d3_schedule_tables.py` | Create | `schedule` enum value (autocommit block), enums, tables, indexes; drops `moze_schedule`; guarded downgrade |
| `app/services/schedule_rules.py` | Create | Pure date maths: occurrences, indexes, horizon, installment split |
| `app/services/schedule_locks.py` | Create | Import key (shared), job key, definition / instance row locks, `ImportRunningError` |
| `app/services/schedule_templates.py` | Create | Template normalisation and validation, overrides, references |
| `app/services/schedule_generation.py` | Create | Instance generation, last-period override, auto-end |
| `app/services/schedule_posting.py` | Create | `post_instance` / `post_locked`, failure recording, period entry deletion |
| `app/services/schedule_read.py` | Create | Definition / instance / queue shapes, loan summary, entry links |
| `app/services/schedule_service.py` | Create | Definition create / update / delete, state actions, instance actions, post sequences |
| `app/services/schedule_entry_hooks.py` | Create | Entry delete hook (D33), reference protection (D29) |
| `app/services/schedule_job.py` | Create | Daily job, dry run, APScheduler wiring, retry, CLI |
| `app/services/schedule_import_map.py` | Create | Pure MOZE mapping: record groups, rules, seqs, lines, amounts |
| `app/services/schedule_import.py` | Create | Importer DB side: row locks, link capture / restore, covered records, upsert / adoption, report |
| `app/services/entry_write_service.py` | Modify | `source=` on writes; delete hook; loan protection; lock-order comment |
| `app/services/transfer_service.py` | Modify | `source=`, `remember=` on `create_transfer` |
| `app/services/settlement_service.py` | Modify | `source=`, `check_cutover_lock=`, `name=` on `settle` |
| `app/services/split_service.py` | Modify | Scheduled-group guard on `update_split`; delete hook on `delete_split` |
| `app/services/settings_service.py` | Modify | Archive / delete refused while a definition references the row |
| `app/services/ledger_service.py` | Modify | `schedule` on entry rows; `loan_schedule` on the detail |
| `app/services/moze_import_service.py` | Modify | `import_lock` retries 30 s while shared holders finish; template usage in `delete_unused_rows` / account archiving |
| `app/services/moze_backup_json.py` | Modify | `AHPeriod` / `AHInstallment` fields; `TEXT_COLUMNS` on the new model |
| `app/services/moze_backup_import_service.py` | Modify | Schedule mapping in the full replace; covered records; re-pointing; report; job after import; `list_schedules` removed |
| `app/schemas/schedules.py` | Create | Request and response models of the schedule endpoints |
| `app/schemas/ledger.py` | Modify | `EntrySource` + `schedule`; `EntryOut.schedule`; `EntryDetailOut.loan_schedule` |
| `app/schemas/imports.py` | Modify | `ScheduleItemOut` removed |
| `app/routers/schedules.py` | Create | `/schedules/...` endpoints |
| `app/routers/imports.py` | Modify | `GET /imports/schedules` removed |
| `app/main.py` | Modify | Router; scheduler start / stop |
| `README.md` | Modify | Endpoints, job, CLI, env var |
| `tests/conftest.py` | Modify | `LEDGER_TABLES`; scheduler off; `today` fixture; `sched` builders; `period` / `installment` backup builders |
| `tests/unit/test_schedule_rules.py`, `test_schedule_import_map.py`, `test_schedule_job_scheduler.py` | Create | Pure tests |
| `tests/integration/test_schedule_models.py`, `test_schedule_templates.py`, `test_schedule_generation.py`, `test_schedule_sources.py`, `test_schedule_posting.py`, `test_schedule_read.py`, `test_schedule_definitions_api.py`, `test_schedule_state_api.py`, `test_schedule_instances_api.py`, `test_schedule_entry_hooks.py`, `test_schedule_links.py`, `test_schedule_job.py`, `test_schedule_import_apply.py`, `test_schedule_import_replace.py`, `test_schedule_lock_order.py` | Create | Postgres-backed tests |
| `tests/integration/test_migration.py`, `test_models_schema.py`, `test_backup_replace_and_report.py`, `test_backup_imports_api.py` | Modify | `moze_schedule` → schedule tables; removed endpoint; builders for periods / installments |

Frontend — `frontend/src/app/`:

| Path | Action | Responsibility |
|---|---|---|
| `models/accounting.model.ts` | Modify | Schedule DTOs; `schedule` / `loan_schedule` on entries; `ScheduleItem` removed |
| `services/accounting.service.ts` | Modify | Schedule calls (writes bump `entriesChanged`); `getSchedules` removed |
| `services/accounting-schedules.service.spec.ts` | Create | The schedule calls' paths, verbs, bodies and `entriesChanged` bumps |
| `components/accounting/schedule-math.ts` (+ spec) | Create | Occurrences, labels, MOZE footers, installment split, pills, `isImportRunning` |
| `components/accounting/accounting-toast.ts` (+ spec) | Create | `AccountingToastService`, `app-accounting-toast` |
| `components/accounting/accounting-layout/accounting-layout.{ts,html}` | Modify | Hosts the toast |
| `components/accounting/schedule-tabs/schedule-tabs.{ts,html,scss,spec.ts}` | Create | 進階 單次 / 週期 / 分期 block |
| `components/accounting/schedule-tabs/schedule-draft.ts` | Create | `ScheduleDraft`, `SCHEDULE_TABS`, `tabsFor`, `defaultDraft` |
| `components/accounting/entry-form/schedule-save.ts` (+ spec) | Create | Form state → definition input; definition → form state |
| `components/accounting/entry-form/entry-form.{ts,html,scss}` | Modify | Tabs, hidden fields, save as definition, definition mode |
| `components/accounting/entry-form/entry-form-schedule.spec.ts` | Create | Form scenarios |
| `components/accounting/transfer-panel/transfer-panel.{ts,html}` | Modify | `scheduled` input hides fee "+" |
| `components/accounting/reminders/schedule-queue.ts` (+ spec) | Create | Pure 待完成交易 grouping and row texts |
| `components/accounting/reminders/schedule-sheet/schedule-sheet.{ts,html,scss,spec.ts}` | Create | Manage sheet |
| `components/accounting/reminders/reminders.{ts,html,scss,spec.ts}` | Modify | `?tab=`, 待完成交易 tab, 週期／分期 section, sheet |
| `components/accounting/timeline/timeline.{ts,spec.ts}` | Modify | Bell count with queue; schedule pills |
| `components/accounting/account-entries/account-entries.{ts,html}` | Modify | Schedule pill |
| `components/accounting/entry-detail/entry-detail.{ts,html,scss,spec.ts}` | Modify | Loan line, pill, edit scope, repost sheet, partial actions, delete wording |
| `components/accounting/accounting-settings/accounting-settings.{ts,html,spec.ts}`, `import-report.{ts,spec.ts}` | Modify | 週期／分期 link; `schedules` report block |
| `components/accounting/testing/fixtures.ts` | Modify | `makeDefinition`, `makeInstance` |

Docs: `docs/deploy/accounting-schedules.md` (create), `openspec/changes/add-accounting-schedules/design.md` ("Implementation notes").

## Interface Contract

Every task's **Interfaces** block refers to these names. Implementers use them verbatim; reviewers reject deviations.

### Enums (Postgres enum names in parentheses)

- `ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule", "schedule")` (`entry_source`)
- `SCHEDULE_KINDS = ("recurring", "installment")` (`schedule_kind`) — the old `moze_schedule_kind` tuple of the same name is deleted
- `SCHEDULE_INTERVALS = ("day", "week", "month", "year")` (`schedule_interval`)
- `SCHEDULE_POSTING_MODES = ("auto", "confirm")` (`schedule_posting_mode`)
- `SCHEDULE_STATUSES = ("active", "paused", "ended")` (`schedule_status`)
- `SCHEDULE_INSTANCE_STATUSES = ("pending", "posted", "skipped")` (`schedule_instance_status`)
- `SCHEDULE_ACTORS = ("auto", "owner", "import")` (`schedule_actor`)

### Models (`app/models/schedule.py`)

```python
class ScheduleDefinition(Base, TimestampMixin):   # schedule_definition
    id; moze_id String(64) unique nullable; kind schedule_kind not null; name String(128) not null
    template JSONB not null; interval_unit schedule_interval not null; interval_n SmallInteger not null default 1
    anchor_date Date not null; day_of_month SmallInteger nullable; first_seq Integer not null default 1
    times Integer nullable; end_date Date nullable; total_amount Numeric(20,4) nullable
    posting_mode schedule_posting_mode not null default 'auto'; status schedule_status not null default 'active'
    auto_post_from Date not null; generated_until Date nullable; created_locally Boolean not null default true
    template_owner_edited Boolean not null default false   # set by a 這一期與之後 / 全部週期 amount edit (Task 12); the importer keeps the template amounts (Task 17)
    moze_payload JSONB nullable; review_reason String(64) nullable
    checks: ck_schedule_definition_interval_n, ck_schedule_definition_day_of_month, ck_schedule_definition_first_seq,
            ck_schedule_definition_times, ck_schedule_definition_end_date, ck_schedule_definition_total_amount,
            ck_schedule_definition_installment

class ScheduleInstance(Base, TimestampMixin):     # schedule_instance
    id; definition_id -> schedule_definition.id ON DELETE CASCADE not null; seq Integer not null
    rule_date Date not null; due_date Date not null; status schedule_instance_status not null default 'pending'
    posted_entry_ids JSONB not null default '[]'; is_partial Boolean not null default false
    acted_at TIMESTAMPTZ nullable; acted_by schedule_actor nullable; amount_override JSONB nullable
    edited_by_owner Boolean not null default false; last_error Text nullable; last_error_at TIMESTAMPTZ nullable
    reopened_at TIMESTAMPTZ nullable; note String(128) nullable; moze_id String(64) unique nullable
    moze_record_ids JSONB not null default '[]'; moze_payload JSONB nullable
    uq_schedule_instance_definition_seq (definition_id, seq); ux_schedule_instance_posted_day (definition_id, due_date) unique WHERE status = 'posted'
    ck_schedule_instance_posted_entries, ck_schedule_instance_acted, ck_schedule_instance_partial
    ix_schedule_instance_status_due (status, due_date); ix_schedule_instance_definition_id (definition_id)
    ix_schedule_instance_posted_entries GIN (posted_entry_ids jsonb_path_ops)
```

### Services (Python signatures)

```python
# app/services/schedule_rules.py  (pure)
def days_in_month(year: int, month: int) -> int
def add_months(day: date, months: int, day_of_month: int | None = None) -> date
def occurrence(anchor: date, unit: str, n: int, k: int, day_of_month: int | None = None) -> date
def occurrence_index(anchor: date, unit: str, n: int, day_of_month: int | None, day: date) -> int | None
def first_index_after(anchor: date, unit: str, n: int, day_of_month: int | None, after: date) -> int
def first_index_on_or_after(anchor: date, unit: str, n: int, day_of_month: int | None, on: date) -> int
def normalize_anchor(anchor: date, unit: str, n: int, day_of_month: int | None) -> date     # first occurrence ≥ anchor
def horizon(today: date) -> date
def last_period_amount(total: Decimal, per_period: Decimal, times: int) -> Decimal      # ValueError when ≤ 0
def plain(value: Decimal | str | int) -> str                                             # "8333", "0.5"

# app/services/schedule_locks.py
SCHEDULE_JOB_LOCK_KEY = 0x53434844
IMPORT_RUNNING = "import_running"
class ImportRunningError(ConflictError): ...                                             # str(exc) == "import_running"
def take_import_key_shared(db: Session) -> None
def import_key_free(engine: Engine) -> bool
def get_instance(db: Session, instance_id: int) -> ScheduleInstance                      # unlocked; NotFoundError
def lock_definition(db: Session, definition_id: int, *, share: bool = False) -> ScheduleDefinition   # NotFoundError
def lock_instance(db: Session, instance_id: int, *, skip_locked: bool = False) -> ScheduleInstance | None
def lock_instances(db: Session, definition_id: int) -> list[ScheduleInstance]            # every instance, ascending id

# app/services/schedule_templates.py
LINE_KINDS: tuple[str, ...]; LINE_KEYS: tuple[str, ...]; LEDGER_KINDS: dict[str, str]; LOAN_LINE_KINDS: dict[str, str]
def normalize_template(template: dict) -> dict
def validate_template(db: Session, template: dict) -> None                              # ValidationError("lines[i].<key>" | "lines")
def template_amounts(template: dict) -> list[str]
def resolved_amounts(definition: ScheduleDefinition, instance: ScheduleInstance) -> list[Decimal]
def check_amounts(template: dict, amounts: list | None) -> list[str] | None              # ValidationError("amounts")
def realign_override(old: dict, new: dict, override: list[str] | None) -> list[str] | None
def loan_line(template: dict) -> tuple[int, dict] | None
def referenced_ids(template: dict) -> dict[str, set[int]]                               # keys: account, category, counterparty, project, loan

# app/services/schedule_generation.py
def last_period_override(definition: ScheduleDefinition) -> list[str] | None
def generate(db: Session, definition: ScheduleDefinition, today: date) -> int
def series_complete(db: Session, definition: ScheduleDefinition) -> bool
def end_if_complete(db: Session, definition: ScheduleDefinition) -> bool
def generate_locked(db: Session, definition_id: int, today: date) -> int                # key + FOR UPDATE + generate + end_if_complete

# app/services/schedule_posting.py
@dataclass(frozen=True) class PostResult: instance_id: int; outcome: str; entry_ids: tuple[int, ...] = ()   # posted | loan_closed | skipped_locked | not_due (+ blocked, made only by schedule_job._post_job_one, R-F6)
def auto_eligible(definition: ScheduleDefinition, instance: ScheduleInstance, today: date) -> bool
def lock_definition_for_post(db: Session, definition_id: int) -> ScheduleDefinition       # FOR SHARE; FOR UPDATE with a loan line
def lock_later_pending(db: Session, definition: ScheduleDefinition, instance: ScheduleInstance) -> list[ScheduleInstance]   # seq > instance.seq, ascending id
def post_instance(db: Session, instance_id: int, *, actor: str, job: bool = False, today: date | None = None) -> PostResult
def post_locked(db: Session, definition: ScheduleDefinition, instance: ScheduleInstance, actor: str, *, locked_loan: LedgerEntry | None = None) -> PostResult
def failure_message(exc: Exception) -> str
def record_failure(db: Session, instance_id: int, message: str) -> None                  # rolls back, writes, commits
def delete_period_entries(db: Session, entry_ids: list[int], *, also_lock: int | None = None) -> LedgerEntry | None   # ledger lock order; one entry statement; cutover lock applies

# app/services/schedule_read.py
def signed_line_amount(kind: str, amount: Decimal) -> Decimal
def definition_out(db: Session, definition: ScheduleDefinition, *, today: date | None = None) -> dict
def list_definitions(db: Session, *, status: str | None = None, kind: str | None = None) -> list[dict]
def get_definition(db: Session, definition_id: int) -> dict                               # + "instances"; NotFoundError
def instance_out(db: Session, instance: ScheduleInstance, *, today: date | None = None) -> dict
def list_instances(db: Session, *, date_from: date | None = None, until: date | None = None, status: str | None = "pending", definition_id: int | None = None, queue: bool = False) -> list[dict]
def loan_summary(db: Session, definition: ScheduleDefinition) -> tuple[Decimal | None, Decimal | None]   # (remaining, repaid)
def schedule_links(db: Session, entry_ids: list[int]) -> dict[int, dict]
def loan_schedule_for(db: Session, entry_id: int) -> dict | None

# app/services/schedule_service.py
def create_definition(db: Session, payload: DefinitionIn) -> int
def update_definition(db: Session, definition_id: int, payload: DefinitionUpdateIn) -> None
def delete_definition(db: Session, definition_id: int) -> None
def pause(db: Session, definition_id: int) -> None
def resume(db: Session, definition_id: int, backlog: str) -> list[int]                   # ids to post after the commit
def end(db: Session, definition_id: int) -> None
def set_mode(db: Session, definition_id: int, posting_mode: str) -> None
def catch_up_ids(db: Session, definition_id: int) -> list[int]
def post_sequence(db: Session, instance_ids: list[int]) -> tuple[list[int], dict | None]   # commits each; (posted ids, {"instance_id", "error"} | None)
def update_instance(db: Session, instance_id: int, payload: InstanceUpdateIn) -> None             # payload.scope: this | following | all (decision 24)
def installment_residual(definition: ScheduleDefinition, rows: list[ScheduleInstance], *, old_amounts: list[str], new_amounts: list[str], overrides: dict[int, list[str] | None]) -> Decimal | None   # R-A1; pure; ValidationError("amounts")
def post_one(db: Session, instance_id: int) -> None                                       # acted_by owner; failure recorded, re-raised
def skip_instance(db: Session, instance_id: int) -> None
def reopen_instance(db: Session, instance_id: int) -> None
def repost_instance(db: Session, instance_id: int, amounts: list) -> None
def accept_partial(db: Session, instance_id: int) -> None

# app/services/schedule_entry_hooks.py
def lock_for_entry_delete(db: Session, entry_ids: list[int]) -> ScheduleInstance | None
def after_entries_deleted(db: Session, instance: ScheduleInstance, deleted_ids: set[int]) -> None
def definitions_referencing(db: Session, *, account_id=None, category_id=None, counterparty_id=None, project_id=None, loan_entry_id=None) -> list[ScheduleDefinition]   # status != ended
def assert_not_referenced(db: Session, **ids) -> None                                     # ConflictError naming the definition
def assert_group_not_scheduled(db: Session, group_id: int) -> None                        # ConflictError naming the instance

# app/services/schedule_job.py
TRIGGERS = ("cron", "startup", "retry", "import", "manual")
def due_instances(db: Session, today: date) -> list[tuple[int, int]]                       # (instance_id, definition_id), ordered definition_id, seq (R-F6)
def blocked_by_earlier(db: Session, definition: ScheduleDefinition, instance_id: int, today: date) -> bool   # R-F6, under the definition lock
def run(engine: Engine, trigger: str, *, today: date | None = None, dry_run: bool = False, post: bool = True) -> dict   # post=False: report["due_unposted"]
def run_after_cli_import(engine: Engine, *, today: date | None = None) -> dict                # R-F4: run(engine, "import", post=is_enabled())
def is_enabled() -> bool
def build_scheduler(engine: Engine) -> BackgroundScheduler
def start(engine: Engine) -> None
def stop() -> None
def trigger_after_import(engine: Engine) -> None
def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int

# app/services/schedule_import_map.py  (pure)
MOZE_LINE_KINDS: dict[int, str]; REWARD_TYPE = 14
def moze_weekday(day: date) -> int                                                         # Sunday 1 … Saturday 7
@dataclass class MappedLine: kind; account; to_account; to_amount; target; classification; project; amount; related; name; store
@dataclass class MappedInstance: moze_id; record_ids; day; seq; amounts; past; enabled; records
@dataclass class MappedDefinition: moze_id; kind; source; name; interval_unit; interval_n; anchor_date; day_of_month; times; total_amount; remainder; lines; review_reason; payload; instances
@dataclass class MapResult: definitions; records_mapped; rewards_ignored; unsupported_types; review; past_singles   # past_singles: past no-definition records as `record:<id>` singles
def record_groups(records: list[dict], data: BackupData) -> list[list[dict]]
def map_schedules(data: BackupData) -> MapResult

# app/services/schedule_import.py
@dataclass class CapturedLinks: template_links: list[tuple[int, int, str]]; settlement_links: list[tuple[int, str]]
@dataclass class Covered: instance_id: int; status: str; moze_lines: tuple[tuple[str, str | None], ...]; day: date; account_amounts: tuple[tuple[str, Decimal], ...] = ()   # status posted | skipped | owner_pending (R-F1); moze_lines per template line (R-F5)
def new_report(mapped: MapResult) -> dict
def lock_schedule_rows(session: Session) -> None
def merge_known_past_singles(session: Session, mapped: MapResult) -> None                  # past singles whose record:<id> definition exists join mapped.definitions
def capture_links(session: Session) -> CapturedLinks
def covered_records(session: Session, mapped: MapResult) -> dict[str, Covered]
def template_usage(session: Session) -> dict[str, set[int]]
def apply_schedules(session, data, mapped, settings, entries, covered, *, started_at: datetime, today: date) -> dict
def restore_links(session: Session, captured: CapturedLinks, report: dict) -> None
def loan_check(session: Session, mapped: MapResult, report: dict) -> None                 # after restore_links: fills loan_remainder_check
def stand_in_entry_ids(session: Session, covered: dict[str, Covered]) -> set[int]
def suppressed_amounts(covered: dict[str, Covered]) -> list[tuple[str, date, Decimal]]   # R-F1: (MOZE account id, date, signed total) for moze_part

# changed ledger signatures
entry_write_service.insert_prepared(db, prepared, *, group_id=None, remember=True, source="manual") -> int
entry_write_service.write_children(db, parent, fee, discount, *, source="manual") -> None
transfer_service.create_transfer(db, payload, *, source="manual", remember=True) -> UUID
settlement_service.settle(db, entry_id, payload, *, source="manual", check_cutover_lock=True, name=None) -> int
moze_import_service.import_lock(engine, *, wait_seconds=IMPORT_LOCK_WAIT_SEC)               # IMPORT_LOCK_WAIT_SEC = 30
moze_backup_import_service.run_backup_import(..., trigger_job=True)                          # the CLI passes False and runs schedule_job.run_after_cli_import (R-F4)
moze_import_service.delete_unused_rows(session, keep=None)                                  # template references count as usage
moze_import_service._archive_disappeared_accounts(session, named_in_file, keep_ids=frozenset())
```

### Pydantic schemas (`app/schemas/schedules.py`)

```python
ScheduleLineKind = Literal["expense", "income", "receivable", "payable", "transfer", "repayment", "collection", "interest"]
class TemplateLineIn(BaseModel):   # extra="forbid"
    kind: ScheduleLineKind; account_id: Int32; to_account_id: Int32 | None = None; to_amount: Money | None = None
    counterparty_id: Int32 | None = None; category_id: Int32 | None = None; project_id: Int32 | None = None
    amount: Money; currency: Currency; loan_entry_id: Int32 | None = None; name: ShortText | None = None; merchant: ShortText | None = None
class TemplateIn(BaseModel):       # extra="forbid"
    lines: list[TemplateLineIn] (1..10); description: str | None = None; tags: list[str] = []
class LoanIn(BaseModel): account_id: Int32; counterparty_id: Int32; category_id: Int32 | None = None; amount: Money; entry_date: date; name: ShortText | None = None
class DefinitionUpdateIn(BaseModel):
    name: ShortText (min 1); template: TemplateIn; interval_unit: Literal["day","week","month","year"]; interval_n: int (1..999) = 1
    anchor_date: date; day_of_month: int (1..31) | None = None; times: int (≥ 1) | None = None; end_date: date | None = None
    total_amount: Money | None = None; posting_mode: Literal["auto","confirm"] | None = None   # None keeps the stored mode
class DefinitionIn(DefinitionUpdateIn): kind: Literal["recurring","installment"]; loan: LoanIn | None = None; posting_mode: Literal["auto","confirm"] = "auto"
class ResumeIn(BaseModel): backlog: Literal["skip","post"] = "skip"
class ModeIn(BaseModel): posting_mode: Literal["auto","confirm"]
class InstanceUpdateIn(BaseModel): due_date: date | None = None; amounts: list[NonNegativeMoney] | None = None; scope: Literal["this","following","all"] = "this"   # scope added by Task 12
class RepostIn(BaseModel): amounts: list[NonNegativeMoney]
class TemplateLineOut(...): every LINE_KEYS key + account_name, to_account_name, category, counterparty
class DefinitionOut: id, kind, name, status, posting_mode, interval_unit, interval_n, anchor_date, day_of_month, first_seq, times,
    end_date, total_amount, auto_post_from, template {lines: [TemplateLineOut], description, tags}, created_locally, template_owner_edited, imported, locked,
    review_reason, generated_until, posted_count, skipped_count, pending_count, next_due_date, next_amount [{currency, amount}],
    remaining, repaid, loan_entry_id, needs_check, failing {instance_id, due_date, last_error} | None, category_icon, category_color
class DefinitionDetailOut(DefinitionOut): instances: list[InstanceOut]
class InstanceOut: id, definition_id, definition_name, kind, posting_mode, seq, times, due_date, rule_date, status, is_partial,
    overdue_days, lines [{kind, account_id, account_name, to_account_id, to_account_name, category, counterparty, amount, currency}],
    totals [{currency, amount}], amounts [unsigned strings aligned with the template], last_error, reopened, edited_by_owner, note,
    posted_entry_ids, acted_at, acted_by, category_icon, category_color
class CatchUpOut: posted: list[int]; failed: {instance_id, error} | None; definition: DefinitionOut
class RunReportOut: trigger, today, status, generated, posted: list[int], failed: list[int], stopped_definitions: list[int]
class EntryScheduleOut: definition_id, instance_id, kind, seq, times, name, is_partial, acted_by, posted_entry_ids   # (app/schemas/ledger.py)
class CurrencyAmountOut: currency, amount   # one model for every {currency, amount}: defined in schedules.py by Task 5, moved to app/schemas/ledger.py by Task 14 (schedules.py imports it)
class LoanScheduleOut: definition_id, name, status, posting_mode, posted_count, times, next_due_date, next_amount, remaining, repaid, needs_check
```

`locked` (= `imported` and `ACCOUNTING_IMPORT_LOCKED` is not `true`), `category_icon` / `category_color` (first line's category), the instance's `amounts` (unsigned, aligned with the template lines) and the link's `acted_by` / `posted_entry_ids` are additions the SPA needs (lock banner, item icon, repost bodies, delete wording); every spec field is present unchanged.

### Routers (in-service paths, `app/routers/schedules.py`, prefix `/schedules`; writes commit after the service call; `service_errors()` maps `ValidationError` → 422, `EditLockedError` → 409 `locked_until_cutover`, `ConflictError` (incl. `ImportRunningError`) → 409, `NotFoundError` → 404)

| Method and path | Body | Answer |
|---|---|---|
| `GET /schedules/definitions?status=&kind=` | — | `list[DefinitionOut]` |
| `GET /schedules/definitions/{id}` | — | `DefinitionDetailOut` |
| `POST /schedules/definitions` | `DefinitionIn` | 201 `DefinitionOut` |
| `PUT /schedules/definitions/{id}` | `DefinitionUpdateIn` | `DefinitionOut` |
| `DELETE /schedules/definitions/{id}` | — | 204 |
| `POST /schedules/definitions/{id}/pause` | — | `DefinitionOut` |
| `POST /schedules/definitions/{id}/resume` | `ResumeIn` (optional body) | `DefinitionOut` |
| `POST /schedules/definitions/{id}/end` | — | `DefinitionOut` |
| `PUT /schedules/definitions/{id}/mode` | `ModeIn` | `DefinitionOut` |
| `POST /schedules/definitions/{id}/catch-up` | — | `CatchUpOut` (200 even when one failed) |
| `GET /schedules/instances?from=&until=&status=&definition_id=&queue=` | — | `list[InstanceOut]` |
| `PUT /schedules/instances/{id}` | `InstanceUpdateIn` (`scope` `this` default · `following` · `all`; not `this` → `amounts` only, else 422 `scope`) | `InstanceOut` |
| `POST /schedules/instances/{id}/post` · `/skip` · `/reopen` · `/accept-partial` | — | `InstanceOut` |
| `POST /schedules/instances/{id}/repost` | `RepostIn` | `InstanceOut` |
| `POST /schedules/run-now` | — | `RunReportOut` (409 `import_running` when an import holds its key; 409 with message `schedule job already running` when the job lock is held — the SPA matches only `import_running`) |

### Import report (`summary["schedules"]`)

```json
{"definitions": {"recurring": {"created": 0, "updated": 0, "ended": 0, "deleted": 0}, "installment": {…}, "single": {…}},
 "instances": {"created": 0, "updated": 0, "kept": 0, "kept_owner_edited": 0, "adopted": 0, "deleted": 0, "posted_from_past": 0, "skipped_disabled": 0},
 "records_mapped": 0, "rewards_ignored": 0, "unsupported_types": {"7": 0},
 "past_records_already_posted": 0, "past_records_already_skipped": 0,
 "past_records_amount_differs": {"count": 0, "instance_ids": [], "lines": [<line item>]},
 "past_records_owner_pending": [{"definition_id": 0, "seq": 0, "date": "…"}],
 "relinked": {"templates": 0, "settlements": 0}, "review": [{"moze_id": "…", "reason": "…"}],
 "amount_differs": [<line item>],
 "loan_remainder_check": [{"definition": "…", "moze_remainder": "…", "open_amount": "…", "difference": "…"}]}
```

`<line item>` (Multica R-F5, per line, never period totals): `{"definition_id": 0, "name": "…", "seq": 0, "date": "…", "line": 0, "kind": "repayment" | "interest" | "expense" | … | "transfer_in", "amount": "8333", "moze_amount": "8400"}` — `amount` is HomeHub's (posted, or what the pending period will post), `moze_amount` the matching MOZE record's. `amount_differs` covers pending periods of `template_owner_edited` definitions and every retained owner-edited pending period (R-A2).

Review reasons: `interval_mismatch`, `loan_missing`, `same_date`, `seq_conflict`, `no_records`, `event_missing`, `account_missing`. The definition's `review_reason` can also be `not_live` (`schedule_import.NOT_LIVE`: a MOZE period the import ended for having no enabled future record; cleared when one reappears); it is not listed under `review`. `skipped_future` counts enabled future records only (disabled future records move to `disabled_skipped`), so `Σ skipped_future == records_mapped + rewards_ignored + Σ unsupported_types`.

### Frontend contract

`models/accounting.model.ts` exports (new): `ScheduleDefinitionKind`, `ScheduleIntervalUnit`, `SchedulePostingMode`, `ScheduleStatus`, `ScheduleInstanceStatus`, `ScheduleActor`, `ScheduleLineKind`, `ScheduleLineInput`, `ScheduleLine`, `ScheduleTemplateInput`, `ScheduleTemplate`, `CurrencyAmount`, `ScheduleFailing`, `ScheduleDefinition`, `ScheduleDefinitionDetail`, `ScheduleInstanceLine`, `ScheduleInstance`, `ScheduleLoanInput`, `ScheduleDefinitionInput`, `ScheduleDefinitionUpdate`, `ScheduleInstanceQuery`, `ScheduleCatchUpResult`, `ScheduleRunReport`, `EntryScheduleLink`, `LoanSchedule`, `ScheduleImportReport`; `EntrySource` gains `'schedule'`; `LedgerEntry.schedule?: EntryScheduleLink | null`; `EntryDetail.loan_schedule?: LoanSchedule | null`; `BackupImportReport.schedules?: ScheduleImportReport`; `ScheduleAmountDiffer` (one differing line of the import report, Task 20, R-A3); `ScheduleAmountScope = 'this' | 'following' | 'all'` (added by Task 24). `ScheduleKind` and `ScheduleItem` are removed.

`AccountingService` (new): `getScheduleDefinitions(query?: { status?: ScheduleStatus; kind?: ScheduleDefinitionKind })`, `getScheduleDefinition(id)`, `createScheduleDefinition(input)`, `updateScheduleDefinition(id, input)`, `deleteScheduleDefinition(id)`, `pauseSchedule(id)`, `resumeSchedule(id, backlog: 'skip' | 'post')`, `endSchedule(id)`, `setScheduleMode(id, mode)`, `catchUpSchedule(id)`, `getScheduleInstances(query)`, `updateScheduleInstance(id, body: { due_date?: string; amounts?: string[]; scope?: ScheduleAmountScope })` (`scope` added by Task 24), `postScheduleInstance(id)`, `skipScheduleInstance(id)`, `reopenScheduleInstance(id)`, `repostScheduleInstance(id, amounts: string[])`, `acceptPartialScheduleInstance(id)`, `runSchedulesNow()`. Every write bumps `entriesChanged`. `getSchedules` is removed.

`schedule-math.ts`: `IMPORT_RUNNING_TOAST`, `isImportRunning(err)`, `addMonthsIso(iso, months, dayOfMonth?)`, `occurrenceDate(rule, k)`, `nextOccurrences(rule, count, after?)`, `intervalLabel(unit, n)`, `ruleDayLabel(rule)`, `recurringFooter(rule, times)`, `splitInstallment(total, times, currency)`, `installmentFooter(total, times, firstDate, currency)`, `ruleSummary(definition)`, `scheduleProgress(definition)`, `schedulePill(link)`, interface `ScheduleRule { interval_unit; interval_n; anchor_date; day_of_month }`.

Components: `app-accounting-toast` (`AccountingToastService.show(text, ms = 3000)`, `message` signal); `app-schedule-tabs` (inputs `kind` (`FormKind`, required), `entryDate` (required), `amount` (number | null), `currency`, `accounts`, `accountId` (the form's account, the 還款帳戶 default), `disabled` (the form's locked / saving state), `definitionMode`, `errors` (field → message, for the 每期金額 / 利息 slots); `draft` model `ScheduleDraft` (required, from `schedule-tabs/schedule-draft.ts`); `<ng-content>` is the 單次 panel); `app-schedule-sheet` (`definitionId` input; `(closed)` output; 調整 on each listed period with the `套用範圖` scope sheet, Task 24). Routes: no new route; definition mode is `/accounting/entry?schedule=<id>`; the reminder centre reads `?tab=all|cards|debts|pending`, `?schedule=<id>` (opens the sheet) and the fragment `schedules`.

## Task Index

| # | Task | Model | Depends on |
|---|---|---|---|
| 1 | Rebase onto `main` after PRs #40–#44 and record baselines | sonnet | — |
| 2 | Schedule models and enums; `moze_schedule` model removed | sonnet | 1 |
| 3 | Alembic revision `c4e8b2f1a7d3` with guarded downgrade | opus | 2 |
| 4 | `schedule_rules`: occurrences, horizon, installment split | sonnet | 1 |
| 5 | `schedule_locks`, `schedule_templates`, request schemas | opus | 3 |
| 6 | `schedule_generation` | opus | 4, 5 |
| 7 | Ledger services gain `source`, `remember` and the cutover bypass | sonnet | 3 |
| 8 | `schedule_posting` | opus | 6, 7 |
| 9 | `schedule_read`: shapes, queue, loan summary | opus | 8 |
| 10 | Definition create / update / delete + router skeleton | opus | 9 |
| 11 | Definition state endpoints (pause, resume, end, mode, catch-up) | opus | 10 |
| 12 | Instance endpoints (list, edit, post, skip, reopen, repost, accept-partial) | opus | 11 |
| 13 | Entry delete hook and reference protection | opus | 12 |
| 14 | Entry rows `schedule` and detail `loan_schedule` | sonnet | 13 |
| 15 | Daily job, dry run, scheduler, CLI, run-now | opus | 12 |
| 16 | Backup JSON fields and pure mapping `schedule_import_map` | opus | 4 |
| 17 | Importer DB side `schedule_import` | opus | 16, 13 |
| 18 | Full-replace wiring: locks, covered records, re-pointing, report, job trigger; `/imports/schedules` 404 test | opus | 15, 17 |
| 19 | Lock-order race tests and README | opus | 18 |
| 20 | Frontend models, service, `schedule-math`, toast | opus | 12 (API shapes) |
| 21 | `app-schedule-tabs` | opus | 20 |
| 22 | Entry form: save as definition, hidden fields, definition mode | opus | 21 |
| 23 | 提醒中心 tabs by query and the 待完成交易 tab | opus | 20 |
| 24 | 週期／分期 section and manage sheet | opus | 23 |
| 25 | Bell count and schedule pills (timeline, passbook, reminder rows) | sonnet | 20 |
| 26 | Entry detail: loan line, pill, edit scope, repost, partial, delete wording | opus | 22, 24 |
| 27 | Settings link and import report `schedules` block | sonnet | 20 |
| 28 | Verify on preview data (disposable database) and owner checklist | opus | 19, 27 |
| 29 | Deploy runbook, design notes, superseded text | opus | 28 |

Execute in numeric order. Tasks 2 and 3 are committed back to back (between them the Alembic-built test database lacks the new tables). Frontend tasks 20–27 depend only on the API shapes of this contract and may start once Task 12 is committed. Full-suite counts are written as "N more than the previous task's count, 0 failed"; per-file counts are exact.

## 1. Rebase onto `main` after PRs #40–#44 and record baselines

**Model:** sonnet

**Files:** none changed by code; this plan file is committed in 1.8 if it is still untracked.

**Interfaces:**
- Consumes: the worktree `/home/opc/workspace/home-hub-schedules` on `feat/accounting-schedules` with the `.env` symlink; PRs #40 (calendar), #41–#43 (billing hints, reminder centre on `feat/accounting-reminders`) and #44 (settlement links, `is_closed`) merged into `main`.
- Produces: the branch rebased onto the merged `main`; confirmed names `LedgerEntry.is_closed`, `ledger_service.open_debt_filter`, `ledger_service._settled_sum`, `CounterpartyOut.open_count`, `settlement_service.open_amount` (currency-aware), `REMINDER_TABS`, `reminderCount`, `BillingService`, `daysBetween`; recorded baselines `B_back` (backend `N passed`) and `B_front` (Vitest `N passed`), each at least the numbers the suites printed when #44 merged.

All commands run from `/home/opc/workspace/home-hub-schedules` unless a step says otherwise. Never print `.env`.

- [ ] 1.1 Confirm the worktree, branch and `.env` link without printing the file.

```bash
cd /home/opc/workspace/home-hub-schedules
git branch --show-current
test -L .env && test -r .env && echo "env link ok"
git status --short
```

Expected: `feat/accounting-schedules`, `env link ok`; `git status --short` prints nothing or only `?? openspec/changes/add-accounting-schedules/tasks.md`.

- [ ] 1.2 Confirm PRs #40–#44 are merged. If any is not `MERGED`, stop and report; this change builds on all five.

```bash
for pr in 40 41 42 43 44; do gh pr view $pr --json number,state,mergedAt --jq '"\(.number) \(.state) \(.mergedAt)"'; done
```

Expected: five lines `4x MERGED 2026-…`.

- [ ] 1.3 Rebase onto the merged `main`.

```bash
git fetch origin
git rebase origin/main
git log --oneline -3
```

Expected: `Successfully rebased and updated refs/heads/feat/accounting-schedules` (or "is up to date"). On a conflict follow superpowers:resolving-merge-conflicts; the only branch-local files are the OpenSpec change and this plan, so a conflict means `main` touched them — keep `main`'s text and re-apply this change's files.

- [ ] 1.4 Confirm the names this plan builds on exist on the rebased tree (each `grep -c` must print a number ≥ 1).

```bash
cd services/accounting-service
grep -c "is_closed = Column" app/models/ledger.py
grep -c "def open_debt_filter" app/services/ledger_service.py
grep -c "def _settled_sum" app/services/ledger_service.py
grep -c "open_count" app/schemas/ledger.py
grep -c "LedgerEntry.currency == entry.currency" app/services/settlement_service.py
grep -c "is_closed" app/services/settlement_service.py
cd ../../frontend/src/app
grep -c "REMINDER_TABS" components/accounting/reminders/reminders.ts
grep -c "reminderCount" components/accounting/timeline/timeline.ts
grep -c "export function daysBetween" components/accounting/dates.ts
grep -c "class BillingService" components/accounting/billing/billing.service.ts
```

Expected: ten numbers, each ≥ 1. A `0` means a dependency did not land; stop and report which.

- [ ] 1.5 Create (or refresh) the service venv with runtime and test dependencies.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
test -x .venv/bin/python || uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt pytest==9.0.3 httpx==0.28.1
.venv/bin/python -c "import sqlalchemy, fastapi; print(sqlalchemy.__version__, fastapi.__version__)"
```

Expected: `2.0.49 0.129.0`.

- [ ] 1.6 Run the backend suite and record `B_back`.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests 2>&1 | tail -2
```

Expected: `N passed` with no failures, N at least the count printed when #44 merged. Write N down as `B_back`; later tasks state their full-suite expectation as "`B_back` + the tests added so far".

Then record the three per-file baselines that Tasks 3, 7 and 13 compare against (none of these files is edited before the task that reruns it, except `test_migration.py`, which Task 3 extends by exactly 3 tests):

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_migration.py 2>&1 | tail -1
.venv/bin/pytest -q -p no:warnings tests/integration/test_entry_writes.py tests/integration/test_transfers.py tests/integration/test_splits.py tests/integration/test_settlements.py tests/integration/test_edit_lock.py 2>&1 | tail -1
.venv/bin/pytest -q -p no:warnings tests/integration/test_entry_writes.py tests/integration/test_splits.py tests/integration/test_settings_crud_api.py tests/integration/test_settings_api.py 2>&1 | tail -1
```

Expected: three `N passed` lines, 0 failed. Write them down as `B_mig`, `B_ledger7` and `B_ledger13` (in the task notes, not in a committed file).

- [ ] 1.7 Install frontend dependencies, generate `environment.ts` (restoring `angular.json`, which `set-env.js` rewrites) and record `B_front`.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend
npm ci
npm run config
git checkout -- angular.json
npx ng test --watch=false 2>&1 | tail -5
```

Expected: `Tests  N passed` and no failed files, N at least the count printed when #44 merged; record it as `B_front`.

- [ ] 1.8 Commit the plan if it is untracked (nothing else changed).

```bash
cd /home/opc/workspace/home-hub-schedules
git status --short
git add openspec/changes/add-accounting-schedules/tasks.md
git commit -m "docs(openspec): implementation plan for add-accounting-schedules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Expected: one file committed (skip the commit when `git status --short` printed nothing).

## 2. Schedule models and enums; `moze_schedule` model removed

**Model:** sonnet

**Files:**
- Create: `services/accounting-service/app/models/schedule.py`
- Modify: `services/accounting-service/app/models/ledger.py` (`ENTRY_SOURCES`; delete `SCHEDULE_KINDS`, `schedule_kind_enum`, `MozeSchedule`)
- Modify: `services/accounting-service/app/models/__init__.py`
- Modify: `services/accounting-service/app/services/moze_backup_json.py` (`TEXT_COLUMNS` for `AHPeriod` / `AHInstallment`)
- Modify: `services/accounting-service/app/services/moze_backup_import_service.py` (delete `_replace_schedule`, `_schedule_due`, `list_schedules`; no `schedules` key until Task 18)
- Modify: `services/accounting-service/app/routers/imports.py`, `app/schemas/imports.py` (`GET /imports/schedules` and `ScheduleItemOut` removed)
- Modify: `services/accounting-service/tests/integration/test_models_schema.py`, `tests/integration/test_backup_replace_and_report.py`, `tests/integration/test_backup_imports_api.py`
- Test: `services/accounting-service/tests/integration/test_schedule_models.py` (create)

**Interfaces:**
- Consumes: `app.database.Base`, `TimestampMixin`; the conftest fixture `database_factory`.
- Produces: `app.models.schedule` with `SCHEDULE_KINDS`, `SCHEDULE_INTERVALS`, `SCHEDULE_POSTING_MODES`, `SCHEDULE_STATUSES`, `SCHEDULE_INSTANCE_STATUSES`, `SCHEDULE_ACTORS`, `ScheduleDefinition`, `ScheduleInstance`, all exported from `app.models`; `ENTRY_SOURCES` with `"schedule"`.

Between this task and Task 3 the Alembic-built test database still has `moze_schedule` and no schedule tables, so only the files named in 2.6 run here; Task 3.6 runs the full suite. Execute Tasks 2 and 3 back to back. `GET /imports/schedules` disappears here (the SPA's settings page still calls it until Task 27; backend and SPA deploy together in Task 29).

- [ ] 2.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_models.py`:

```python
"""The ORM models alone (Base.metadata.create_all) build the schedule tables (design D28).

test_migration.py checks that the Alembic revision produces the same schema.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ENTRY_SOURCES, Account, LedgerEntry, ScheduleDefinition, ScheduleInstance

NOW = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)
LINE = {
    "kind": "expense", "account_id": 1, "to_account_id": None, "to_amount": None, "counterparty_id": None,
    "category_id": None, "project_id": None, "amount": "390", "currency": "TWD", "loan_entry_id": None,
    "name": None, "merchant": None,
}
TEMPLATE = {"lines": [LINE], "description": None, "tags": []}


@pytest.fixture()
def model_engine(database_factory):
    engine = create_engine(database_factory())
    with engine.begin() as conn:
        conn.execute(text("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT"))
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def model_session(model_engine):
    with Session(model_engine, autoflush=False) as session:
        yield session


def _definition(session: Session, **fields) -> ScheduleDefinition:
    values = {
        "kind": "recurring", "name": "Netflix", "template": TEMPLATE, "interval_unit": "month",
        "anchor_date": date(2026, 10, 22), "auto_post_from": date(2026, 10, 3), **fields,
    }
    definition = ScheduleDefinition(**values)
    session.add(definition)
    session.flush()
    return definition


def _instance(session: Session, definition: ScheduleDefinition, seq: int, day: date, **fields) -> ScheduleInstance:
    instance = ScheduleInstance(definition_id=definition.id, seq=seq, rule_date=day, due_date=day, **fields)
    session.add(instance)
    session.flush()
    return instance


def test_schedule_tables_exist_and_entry_source_has_schedule(model_engine, model_session):
    tables = set(inspect(model_engine).get_table_names())
    assert {"schedule_definition", "schedule_instance"} <= tables
    assert "moze_schedule" not in tables
    assert "schedule" in ENTRY_SOURCES
    account = Account(name="錢包", currency="TWD")
    model_session.add(account)
    model_session.flush()
    entry = LedgerEntry(
        account_id=account.id, kind="expense", amount=Decimal("-390"), currency="TWD",
        entry_date=date(2026, 10, 22), source="schedule",
    )
    model_session.add(entry)
    model_session.commit()
    assert entry.source == "schedule"


def test_definition_defaults(model_session):
    # Spec "Local recurring definition": the columns a local monthly definition starts with.
    definition = _definition(model_session)
    model_session.commit()
    assert (
        definition.interval_n, definition.first_seq, definition.times, definition.posting_mode, definition.status,
        definition.created_locally, definition.moze_id, definition.generated_until, definition.review_reason,
        definition.template_owner_edited,
    ) == (1, 1, None, "auto", "active", True, None, None, None, False)


def test_instance_defaults(model_session):
    instance = _instance(model_session, _definition(model_session), 1, date(2026, 10, 22))
    model_session.commit()
    assert (
        instance.status, instance.posted_entry_ids, instance.is_partial, instance.edited_by_owner,
        instance.moze_record_ids, instance.acted_at, instance.acted_by, instance.amount_override,
    ) == ("pending", [], False, False, [], None, None, None)


def test_one_row_per_period(model_session):
    # Spec "One row per period".
    definition = _definition(model_session)
    _instance(model_session, definition, 3, date(2026, 12, 22))
    model_session.commit()
    model_session.add(
        ScheduleInstance(definition_id=definition.id, seq=3, rule_date=date(2027, 1, 22), due_date=date(2027, 1, 22))
    )
    with pytest.raises(IntegrityError, match="uq_schedule_instance_definition_seq"):
        model_session.commit()


def test_instance_status_checks(model_session):
    definition = _definition(model_session)
    model_session.commit()
    cases = [
        ({"status": "posted", "acted_at": NOW, "acted_by": "auto"}, "ck_schedule_instance_posted_entries"),
        ({"acted_at": NOW}, "ck_schedule_instance_acted"),
        ({"is_partial": True}, "ck_schedule_instance_partial"),
    ]
    for index, (fields, constraint) in enumerate(cases):
        day = date(2026, 11, 1 + index)
        model_session.add(ScheduleInstance(definition_id=definition.id, seq=10 + index, rule_date=day, due_date=day, **fields))
        with pytest.raises(IntegrityError, match=constraint):
            model_session.commit()
        model_session.rollback()


def test_a_day_is_posted_once(model_session):
    definition = _definition(model_session)
    day = date(2026, 10, 22)
    posted = {"status": "posted", "acted_at": NOW, "acted_by": "auto"}
    _instance(model_session, definition, 1, day, posted_entry_ids=[1], **posted)
    model_session.commit()
    model_session.add(
        ScheduleInstance(definition_id=definition.id, seq=2, rule_date=day, due_date=day, posted_entry_ids=[2], **posted)
    )
    with pytest.raises(IntegrityError, match="ux_schedule_instance_posted_day"):
        model_session.commit()
    model_session.rollback()
    _instance(model_session, definition, 3, day)  # a pending period on a posted day is allowed
    model_session.commit()


def test_definition_checks(model_session):
    cases = [
        ({"kind": "installment", "interval_unit": "week", "times": 3}, "ck_schedule_definition_installment"),
        ({"kind": "installment", "times": 1}, "ck_schedule_definition_installment"),
        ({"kind": "installment", "times": None}, "ck_schedule_definition_installment"),
        ({"interval_unit": "week", "day_of_month": 5}, "ck_schedule_definition_day_of_month"),
        ({"times": 0}, "ck_schedule_definition_times"),
        ({"end_date": date(2026, 10, 1)}, "ck_schedule_definition_end_date"),
        ({"total_amount": Decimal("0")}, "ck_schedule_definition_total_amount"),
        ({"interval_n": 0}, "ck_schedule_definition_interval_n"),
    ]
    for fields, constraint in cases:
        values = {
            "kind": "recurring", "name": "x", "template": TEMPLATE, "interval_unit": "month",
            "anchor_date": date(2026, 10, 22), "auto_post_from": date(2026, 10, 3), **fields,
        }
        model_session.add(ScheduleDefinition(**values))
        with pytest.raises(IntegrityError, match=constraint):
            model_session.commit()
        model_session.rollback()


def test_instances_cascade_with_their_definition(model_session):
    definition = _definition(model_session)
    _instance(model_session, definition, 1, date(2026, 10, 22))
    _instance(model_session, definition, 2, date(2026, 11, 22))
    model_session.commit()
    model_session.execute(delete(ScheduleDefinition).where(ScheduleDefinition.id == definition.id))
    model_session.commit()
    assert model_session.scalar(select(func.count()).select_from(ScheduleInstance)) == 0
```

- [ ] 2.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_models.py
```

Expected: collection error `ImportError: cannot import name 'ScheduleDefinition' from 'app.models'`.

- [ ] 2.3 Create `services/accounting-service/app/models/schedule.py`:

```python
"""Schedule definitions (the rule and its template) and instances (one period each); design D28.

Dates are naive Asia/Taipei dates (D41); timestamps are TIMESTAMPTZ. The migration c4e8b2f1a7d3 builds the same
tables, constraints and indexes.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ..database import Base, TimestampMixin

SCHEDULE_KINDS = ("recurring", "installment")
SCHEDULE_INTERVALS = ("day", "week", "month", "year")
SCHEDULE_POSTING_MODES = ("auto", "confirm")
SCHEDULE_STATUSES = ("active", "paused", "ended")
SCHEDULE_INSTANCE_STATUSES = ("pending", "posted", "skipped")
SCHEDULE_ACTORS = ("auto", "owner", "import")

schedule_kind_enum = Enum(*SCHEDULE_KINDS, name="schedule_kind")
schedule_interval_enum = Enum(*SCHEDULE_INTERVALS, name="schedule_interval")
schedule_posting_mode_enum = Enum(*SCHEDULE_POSTING_MODES, name="schedule_posting_mode")
schedule_status_enum = Enum(*SCHEDULE_STATUSES, name="schedule_status")
schedule_instance_status_enum = Enum(*SCHEDULE_INSTANCE_STATUSES, name="schedule_instance_status")
schedule_actor_enum = Enum(*SCHEDULE_ACTORS, name="schedule_actor")

# The migration uses the same texts (migrations do not import app code).
DEFINITION_CHECKS = {
    "ck_schedule_definition_interval_n": "interval_n >= 1",
    "ck_schedule_definition_day_of_month": (
        "day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 31 AND interval_unit IN ('month', 'year'))"
    ),
    "ck_schedule_definition_first_seq": "first_seq >= 1",
    "ck_schedule_definition_times": "times IS NULL OR times >= first_seq",
    "ck_schedule_definition_end_date": "end_date IS NULL OR end_date >= anchor_date",
    "ck_schedule_definition_total_amount": "total_amount IS NULL OR total_amount > 0",
    "ck_schedule_definition_installment": (
        "kind <> 'installment' OR (interval_unit = 'month' AND times IS NOT NULL AND times >= 2)"
    ),
}
INSTANCE_CHECKS = {
    "ck_schedule_instance_posted_entries": "(status = 'posted') = (jsonb_array_length(posted_entry_ids) > 0)",
    "ck_schedule_instance_acted": "(status = 'pending') = (acted_at IS NULL)",
    "ck_schedule_instance_partial": "is_partial = false OR status = 'posted'",
}
EMPTY_JSON_ARRAY = text("'[]'::jsonb")


class ScheduleDefinition(Base, TimestampMixin):
    __tablename__ = "schedule_definition"
    __table_args__ = tuple(CheckConstraint(sql, name=name) for name, sql in DEFINITION_CHECKS.items())

    id = Column(Integer, primary_key=True)
    # AHPeriod / AHInstallment identifier, or "record:<AHRecord identifier>" for a single imported record.
    moze_id = Column(String(64), nullable=True, unique=True)
    kind = Column(schedule_kind_enum, nullable=False)
    name = Column(String(128), nullable=False)
    template = Column(JSONB, nullable=False)
    interval_unit = Column(schedule_interval_enum, nullable=False)
    interval_n = Column(SmallInteger, nullable=False, server_default=text("1"))
    anchor_date = Column(Date, nullable=False)
    day_of_month = Column(SmallInteger, nullable=True)
    first_seq = Column(Integer, nullable=False, server_default=text("1"))
    times = Column(Integer, nullable=True)
    end_date = Column(Date, nullable=True)
    total_amount = Column(Numeric(20, 4), nullable=True)
    posting_mode = Column(schedule_posting_mode_enum, nullable=False, server_default=text("'auto'"))
    status = Column(schedule_status_enum, nullable=False, server_default=text("'active'"))
    auto_post_from = Column(Date, nullable=False)
    generated_until = Column(Date, nullable=True)
    created_locally = Column(Boolean, nullable=False, server_default=text("true"))
    # Proposal decision 24: the owner changed the template amounts with a 這一期與之後 / 全部週期 period edit;
    # re-imports then keep those amounts and write no MOZE override on pending periods (D37).
    template_owner_edited = Column(Boolean, nullable=False, server_default=text("false"))
    moze_payload = Column(JSONB, nullable=True)
    review_reason = Column(String(64), nullable=True)


class ScheduleInstance(Base, TimestampMixin):
    __tablename__ = "schedule_instance"
    __table_args__ = (
        UniqueConstraint("definition_id", "seq", name="uq_schedule_instance_definition_seq"),
        Index(
            "ux_schedule_instance_posted_day", "definition_id", "due_date", unique=True,
            postgresql_where=text("status = 'posted'"),
        ),
        Index("ix_schedule_instance_status_due", "status", "due_date"),
        Index("ix_schedule_instance_definition_id", "definition_id"),
        Index(
            "ix_schedule_instance_posted_entries", "posted_entry_ids", postgresql_using="gin",
            postgresql_ops={"posted_entry_ids": "jsonb_path_ops"},
        ),
        *(CheckConstraint(sql, name=name) for name, sql in INSTANCE_CHECKS.items()),
    )

    id = Column(Integer, primary_key=True)
    definition_id = Column(Integer, ForeignKey("schedule_definition.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    rule_date = Column(Date, nullable=False)  # the rule's occurrence; never changed by an instance edit
    due_date = Column(Date, nullable=False)  # = rule_date unless the owner moved the period
    status = Column(schedule_instance_status_enum, nullable=False, server_default=text("'pending'"))
    posted_entry_ids = Column(JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY)
    is_partial = Column(Boolean, nullable=False, server_default=text("false"))
    acted_at = Column(DateTime(timezone=True), nullable=True)
    acted_by = Column(schedule_actor_enum, nullable=True)
    amount_override = Column(JSONB, nullable=True)  # unsigned decimal strings aligned with template["lines"]
    edited_by_owner = Column(Boolean, nullable=False, server_default=text("false"))
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    reopened_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(128), nullable=True)
    moze_id = Column(String(64), nullable=True, unique=True)
    moze_record_ids = Column(JSONB, nullable=False, server_default=EMPTY_JSON_ARRAY)
    moze_payload = Column(JSONB, nullable=True)
```

- [ ] 2.4 In `services/accounting-service/app/models/ledger.py`:
  - replace `ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule")` with `ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule", "schedule")`;
  - delete the line `SCHEDULE_KINDS = ("period", "installment", "skipped_record")`;
  - delete the line `schedule_kind_enum = Enum(*SCHEDULE_KINDS, name="moze_schedule_kind")`;
  - delete the whole `class MozeSchedule(Base):` block at the end of the file (it is the last class; `JSONB` stays imported, nothing else in the file uses it, so also change `from sqlalchemy.dialects.postgresql import ARRAY, JSONB` to `from sqlalchemy.dialects.postgresql import ARRAY`).

  Replace `services/accounting-service/app/models/__init__.py` with:

```python
from .ledger import (
    COLOR_CONVENTIONS,
    DUE_RULES,
    ENTRY_GROUP_KINDS,
    ENTRY_KINDS,
    ENTRY_SOURCES,
    FX_SOURCES,
    KEYPAD_LAYOUTS,
    MOZE_SOURCES,
    REWARD_METHODS,
    REWARD_POSTINGS,
    REWARD_WINDOWS,
    ROUNDING_MODES,
    SYSTEM_KINDS,
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    LedgerEntry,
    Preference,
    Project,
    RewardRule,
)
from .schedule import (
    SCHEDULE_ACTORS,
    SCHEDULE_INSTANCE_STATUSES,
    SCHEDULE_INTERVALS,
    SCHEDULE_KINDS,
    SCHEDULE_POSTING_MODES,
    SCHEDULE_STATUSES,
    ScheduleDefinition,
    ScheduleInstance,
)
from .import_run import IMPORT_KINDS, IMPORT_STATUSES, ImportRun
from .fx_rate import FxRate
```

- [ ] 2.5 Remove the `moze_schedule` users.
  - `app/services/moze_backup_json.py`: in the `from ..models import (...)` block replace `MozeSchedule,` with `ScheduleDefinition,`; in `TEXT_COLUMNS` replace the two lines `"AHPeriod": {"identifier": MozeSchedule.moze_id},` and `"AHInstallment": {"identifier": MozeSchedule.moze_id},` with `"AHPeriod": {"identifier": ScheduleDefinition.moze_id},` and `"AHInstallment": {"identifier": ScheduleDefinition.moze_id},`.
  - `app/services/moze_backup_import_service.py`: delete `MozeSchedule,` from the `from ..models import (...)` block; delete the functions `_replace_schedule`, `_schedule_due` and `list_schedules` (their whole bodies); in `replace_ledger_from_backup` delete the line `schedules = _replace_schedule(session, data, entries.skipped_records, import_run_id)` and the dict entry `"schedules": schedules,` (Task 17 adds the new `schedules` block). `_jsonable` stays (Task 17 uses it).
  - `app/routers/imports.py`: replace `from ..schemas.imports import ImportReport, ScheduleItemOut` with `from ..schemas.imports import ImportReport`; replace `from ..services.moze_backup_import_service import list_schedules, run_backup_import` with `from ..services.moze_backup_import_service import run_backup_import`; delete `from typing import Literal`; delete the whole `@router.get("/schedules", ...)` endpoint `import_schedules`.
  - `app/schemas/imports.py`: replace the file with

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ImportReport(BaseModel):
    id: int | None
    kind: Literal["moze_csv", "moze_backup"] = "moze_csv"
    status: Literal["running", "succeeded", "failed", "dry_run"]
    started_at: datetime
    finished_at: datetime | None
    file_name: str
    file_sha256: str
    row_count: int | None
    exported_at: datetime | None = None
    summary: dict | None
```

  - `tests/integration/test_models_schema.py`: delete `MozeSchedule,` from the import block; in `PHASE_2A_TABLES` replace `"moze_schedule"` with `"schedule_definition", "schedule_instance"`; in `test_new_enum_values_are_accepted` replace `model_session.add_all([group, MozeSchedule(kind="period", moze_id="P-1", payload={"n": 1})])` with `model_session.add(group)`.
  - `tests/integration/test_backup_replace_and_report.py`: delete `MozeSchedule,` from the import block; delete the whole test `test_schedule_is_replaced_on_each_import`; in `_snapshot` replace `"entry_reward_rule", "moze_schedule", "import_run")` with `"entry_reward_rule", "schedule_definition", "schedule_instance", "import_run")`.
  - `tests/integration/test_backup_imports_api.py`: delete the whole test `test_skipped_rows_are_retrievable_as_schedules`.

- [ ] 2.6 Run the model tests (the Alembic-built database is not upgraded until Task 3).

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_models.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_models_schema.py tests/unit
```

Expected: `8 passed`; then `… passed` with 0 failed.

- [ ] 2.7 Commit (Task 3 follows immediately).

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/models services/accounting-service/app/services/moze_backup_json.py \
  services/accounting-service/app/services/moze_backup_import_service.py services/accounting-service/app/routers/imports.py \
  services/accounting-service/app/schemas/imports.py services/accounting-service/tests/integration/test_schedule_models.py \
  services/accounting-service/tests/integration/test_models_schema.py \
  services/accounting-service/tests/integration/test_backup_replace_and_report.py \
  services/accounting-service/tests/integration/test_backup_imports_api.py
git commit -m "feat(accounting): schedule definition and instance models; moze_schedule retired

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 3. Alembic revision `c4e8b2f1a7d3` with guarded downgrade

**Model:** opus

**Files:**
- Create: `services/accounting-service/alembic/versions/c4e8b2f1a7d3_schedule_tables.py`
- Modify: `services/accounting-service/tests/conftest.py` (`LEDGER_TABLES`)
- Modify: `services/accounting-service/tests/integration/test_migration.py`

**Interfaces:**
- Consumes: revision `7b1e4a2c9d05` (head after #44, which added `ledger_entry.is_closed` inside it); the model texts `DEFINITION_CHECKS`, `INSTANCE_CHECKS` (Task 2; copied, not imported).
- Produces: head `c4e8b2f1a7d3`; `SCHEDULES_HEAD = "c4e8b2f1a7d3"` in `test_migration.py`; conftest `LEDGER_TABLES` truncating `schedule_instance, schedule_definition`.

Rules: `ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'schedule'` runs inside `op.get_context().autocommit_block()` (D39) — nothing later in `upgrade()` uses the value, but the block keeps the revision correct when an operator runs it on a PostgreSQL that refuses `ADD VALUE` in a transaction block. The downgrade refuses (one `RuntimeError` naming every count) while any `schedule` entry exists or any instance has `acted_by` `auto` / `owner`; otherwise it deletes definitions and instances, drops the tables and enums, recreates `moze_schedule` empty and shrinks `entry_source` with the `_shrink_enum` pattern of `7b1e4a2c9d05`. Alembic runs one transaction per command here (`env.py`), so a refusal leaves the version at `c4e8b2f1a7d3`.

- [ ] 3.1 Write the failing tests. In `services/accounting-service/tests/integration/test_migration.py`:
  - in `LEDGER_TABLES` replace `"moze_schedule"` with `"schedule_definition", "schedule_instance"`;
  - below `PHASE_1_HEAD = "5d2e7c9a1b3f"` add `PHASE_2A_HEAD = "7b1e4a2c9d05"` and `SCHEDULES_HEAD = "c4e8b2f1a7d3"`;
  - in `test_downgrade_refuses_when_phase_2a_data_exists` and `test_downgrade_refusal_names_every_blocker` replace `assert _version(url) == "7b1e4a2c9d05"` with `assert _version(url) == SCHEDULES_HEAD` (the whole downgrade runs in one transaction, so the refusal in 7b1e rolls back this revision's downgrade too);
  - append:

```python
def _insert_schedule_rows(url, *, acted_by: str | None, schedule_entry: bool) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        definition_id = conn.execute(
            text(
                "INSERT INTO schedule_definition (kind, name, template, interval_unit, anchor_date, auto_post_from) "
                "VALUES ('recurring', 'Netflix', '{\"lines\": []}', 'month', '2026-10-22', '2026-10-03') RETURNING id"
            )
        ).scalar_one()
        entry_ids = "[]"
        if schedule_entry:
            entry_id = conn.execute(
                text(
                    "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, posted_date, source) "
                    "VALUES (:a, 'expense', -390, 'TWD', '2026-10-22', '2026-10-22', 'schedule') RETURNING id"
                ),
                {"a": account_id},
            ).scalar_one()
            entry_ids = f"[{entry_id}]"
        if acted_by is None:
            conn.execute(
                text(
                    "INSERT INTO schedule_instance (definition_id, seq, rule_date, due_date) "
                    "VALUES (:d, 1, '2026-10-22', '2026-10-22')"
                ),
                {"d": definition_id},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO schedule_instance (definition_id, seq, rule_date, due_date, status, posted_entry_ids, "
                    "acted_at, acted_by) VALUES (:d, 1, '2026-10-22', '2026-10-22', 'posted', CAST(:ids AS jsonb), "
                    "now(), CAST(:by AS schedule_actor))"
                ),
                {"d": definition_id, "ids": entry_ids if entry_ids != "[]" else "[999]", "by": acted_by},
            )
    engine.dispose()


def test_upgrade_adds_schedule_source_and_tables_and_drops_moze_schedule(database_factory, alembic_config):
    url = database_factory()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        definition_columns = {column["name"]: column for column in inspect(engine).get_columns("schedule_definition")}
        with engine.connect() as conn:
            sources = conn.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text")).scalars().all()
            kinds = conn.execute(text("SELECT count(*) FROM pg_type WHERE typname = 'moze_schedule_kind'")).scalar_one()
    finally:
        engine.dispose()
    assert {"schedule_definition", "schedule_instance"} <= tables
    owner_edited = definition_columns["template_owner_edited"]  # proposal decision 24
    assert (owner_edited["nullable"], owner_edited["default"]) == (False, "false")
    assert "moze_schedule" not in tables
    assert "schedule" in sources
    assert kinds == 0
    assert _version(url) == SCHEDULES_HEAD


def test_downgrade_recreates_an_empty_moze_schedule(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    _insert_schedule_rows(url, acted_by=None, schedule_entry=False)  # a pending period only: nothing HomeHub posted
    command.downgrade(config, PHASE_2A_HEAD)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT count(*) FROM moze_schedule")).scalar_one()
            sources = conn.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text")).scalars().all()
    finally:
        engine.dispose()
    assert "moze_schedule" in tables and rows == 0
    assert not {"schedule_definition", "schedule_instance"} & tables
    assert "schedule" not in sources
    assert _version(url) == PHASE_2A_HEAD


def test_downgrade_refuses_while_homehub_posted_data_exists(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    _insert_schedule_rows(url, acted_by="auto", schedule_entry=True)

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(config, PHASE_2A_HEAD)

    message = str(excinfo.value)
    assert message.startswith("refusing to downgrade c4e8b2f1a7d3: ")
    assert "1 ledger_entry rows with source = schedule" in message
    assert "1 schedule instances posted or skipped by HomeHub (acted_by auto or owner)" in message
    assert _version(url) == SCHEDULES_HEAD
```

  In `services/accounting-service/tests/conftest.py` replace the `LEDGER_TABLES` value with:

```python
LEDGER_TABLES = (
    "entry_reward_rule, schedule_instance, schedule_definition, ledger_entry, reward_rule, entry_group, "
    "counterparty, import_run, category, project, account, account_group, fx_rate, preference"
)
```

- [ ] 3.2 Run them.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_migration.py
```

Expected: failures, among them `test_head_schema_matches_the_models` (models have the schedule tables, the migration does not) and `test_upgrade_adds_schedule_source_and_tables_and_drops_moze_schedule` (`assert 'moze_schedule' not in tables`).

- [ ] 3.3 Create `services/accounting-service/alembic/versions/c4e8b2f1a7d3_schedule_tables.py`:

```python
"""accounting schedules: schedule_definition / schedule_instance, entry_source 'schedule'; moze_schedule dropped

Revision ID: c4e8b2f1a7d3
Revises: 7b1e4a2c9d05
Create Date: 2026-10-03 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4e8b2f1a7d3"
down_revision: Union[str, Sequence[str], None] = "7b1e4a2c9d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRE_SCHEDULE_ENTRY_SOURCES = ("moze_import", "moze_backup", "manual", "hermes", "rule")
NEW_ENUMS = {
    "schedule_kind": ("recurring", "installment"),
    "schedule_interval": ("day", "week", "month", "year"),
    "schedule_posting_mode": ("auto", "confirm"),
    "schedule_status": ("active", "paused", "ended"),
    "schedule_instance_status": ("pending", "posted", "skipped"),
    "schedule_actor": ("auto", "owner", "import"),
}
MOZE_SCHEDULE_KINDS = ("period", "installment", "skipped_record")

# Same texts as app.models.schedule (migrations do not import app code).
DEFINITION_CHECKS = {
    "ck_schedule_definition_interval_n": "interval_n >= 1",
    "ck_schedule_definition_day_of_month": (
        "day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 31 AND interval_unit IN ('month', 'year'))"
    ),
    "ck_schedule_definition_first_seq": "first_seq >= 1",
    "ck_schedule_definition_times": "times IS NULL OR times >= first_seq",
    "ck_schedule_definition_end_date": "end_date IS NULL OR end_date >= anchor_date",
    "ck_schedule_definition_total_amount": "total_amount IS NULL OR total_amount > 0",
    "ck_schedule_definition_installment": (
        "kind <> 'installment' OR (interval_unit = 'month' AND times IS NOT NULL AND times >= 2)"
    ),
}
INSTANCE_CHECKS = {
    "ck_schedule_instance_posted_entries": "(status = 'posted') = (jsonb_array_length(posted_entry_ids) > 0)",
    "ck_schedule_instance_acted": "(status = 'pending') = (acted_at IS NULL)",
    "ck_schedule_instance_partial": "is_partial = false OR status = 'posted'",
}
EMPTY_JSON_ARRAY = sa.text("'[]'::jsonb")

# Each count blocks the downgrade: the previous revision cannot hold what HomeHub posted.
DOWNGRADE_GUARDS = {
    "ledger_entry rows with source = schedule": "SELECT count(*) FROM ledger_entry WHERE source = 'schedule'",
    "schedule instances posted or skipped by HomeHub (acted_by auto or owner)":
        "SELECT count(*) FROM schedule_instance WHERE acted_by IN ('auto', 'owner')",
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*NEW_ENUMS[name], name=name, create_type=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # D39: the new entry_source value in its own committed step.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'schedule'")

    connection = op.get_bind()
    for name, values in NEW_ENUMS.items():
        postgresql.ENUM(*values, name=name).create(connection)

    op.create_table(
        "schedule_definition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.Column("kind", _enum("schedule_kind"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template", postgresql.JSONB(), nullable=False),
        sa.Column("interval_unit", _enum("schedule_interval"), nullable=False),
        sa.Column("interval_n", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=True),
        sa.Column("first_seq", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("times", sa.Integer(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("posting_mode", _enum("schedule_posting_mode"), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("status", _enum("schedule_status"), nullable=False, server_default=sa.text("'active'")),
        sa.Column("auto_post_from", sa.Date(), nullable=False),
        sa.Column("generated_until", sa.Date(), nullable=True),
        sa.Column("created_locally", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("template_owner_edited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("moze_payload", postgresql.JSONB(), nullable=True),
        sa.Column("review_reason", sa.String(64), nullable=True),
        *_timestamps(),
        *(sa.CheckConstraint(sql, name=name) for name, sql in DEFINITION_CHECKS.items()),
    )
    op.create_table(
        "schedule_instance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "definition_id", sa.Integer(), sa.ForeignKey("schedule_definition.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("rule_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", _enum("schedule_instance_status"), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("posted_entry_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acted_by", _enum("schedule_actor"), nullable=True),
        sa.Column("amount_override", postgresql.JSONB(), nullable=True),
        sa.Column("edited_by_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(128), nullable=True),
        sa.Column("moze_id", sa.String(64), nullable=True, unique=True),
        sa.Column("moze_record_ids", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_ARRAY),
        sa.Column("moze_payload", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("definition_id", "seq", name="uq_schedule_instance_definition_seq"),
        *(sa.CheckConstraint(sql, name=name) for name, sql in INSTANCE_CHECKS.items()),
    )
    op.create_index(
        "ux_schedule_instance_posted_day", "schedule_instance", ["definition_id", "due_date"], unique=True,
        postgresql_where=sa.text("status = 'posted'"),
    )
    op.create_index("ix_schedule_instance_status_due", "schedule_instance", ["status", "due_date"])
    op.create_index("ix_schedule_instance_definition_id", "schedule_instance", ["definition_id"])
    op.create_index(
        "ix_schedule_instance_posted_entries", "schedule_instance", ["posted_entry_ids"], postgresql_using="gin",
        postgresql_ops={"posted_entry_ids": "jsonb_path_ops"},
    )

    # D39: MOZE's rows are reproducible from the backup; the post-upgrade step is a backup import.
    op.drop_table("moze_schedule")
    op.execute("DROP TYPE moze_schedule_kind")


def _assert_downgradable(connection: sa.Connection) -> None:
    blockers = []
    for label, sql in DOWNGRADE_GUARDS.items():
        count = connection.execute(sa.text(sql)).scalar_one()
        if count:
            blockers.append(f"{count} {label}")
    if blockers:
        raise RuntimeError("refusing to downgrade c4e8b2f1a7d3: " + "; ".join(blockers))


def _shrink_enum(table: str, column: str, type_name: str, values: tuple[str, ...]) -> None:
    """PostgreSQL cannot drop enum values: recreate the type without 'schedule'."""
    op.execute(f"ALTER TYPE {type_name} RENAME TO {type_name}_sched")
    op.execute(f"CREATE TYPE {type_name} AS ENUM ({', '.join(repr(v) for v in values)})")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {type_name} USING {column}::text::{type_name}")
    op.execute(f"DROP TYPE {type_name}_sched")


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("ledger_entry", "schedule_definition", "schedule_instance"):
        op.execute(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE')
    _assert_downgradable(connection)

    op.drop_index("ix_schedule_instance_posted_entries", table_name="schedule_instance")
    op.drop_index("ix_schedule_instance_definition_id", table_name="schedule_instance")
    op.drop_index("ix_schedule_instance_status_due", table_name="schedule_instance")
    op.drop_index("ux_schedule_instance_posted_day", table_name="schedule_instance")
    op.drop_table("schedule_instance")
    op.drop_table("schedule_definition")
    for name in reversed(list(NEW_ENUMS)):
        op.execute(f"DROP TYPE {name}")

    # Recreated empty: the next phase 2a backup import refills it.
    postgresql.ENUM(*MOZE_SCHEDULE_KINDS, name="moze_schedule_kind").create(connection)
    op.create_table(
        "moze_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            postgresql.ENUM(*MOZE_SCHEDULE_KINDS, name="moze_schedule_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("moze_id", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), sa.ForeignKey("import_run.id"), nullable=True),
    )
    _shrink_enum("ledger_entry", "source", "entry_source", PRE_SCHEDULE_ENTRY_SOURCES)
```

- [ ] 3.4 Run the migration tests.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_migration.py
```

Expected: `B_mig + 3 passed` (the baseline recorded in 1.6, plus the 3 tests of 3.1), 0 failed.

- [ ] 3.5 Confirm the revision chain.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/alembic heads 2>&1 | tail -1
```

Expected: `c4e8b2f1a7d3 (head)`. (`alembic heads` reads only the scripts; it does not connect.)

- [ ] 3.6 Run the full backend suite.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests 2>&1 | tail -2
```

Expected: `B_back + 8 + 3 − 2` passed (8 model tests, 3 migration tests, the two deleted `moze_schedule` tests), 0 failed.

- [ ] 3.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/alembic/versions/c4e8b2f1a7d3_schedule_tables.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/integration/test_migration.py
git commit -m "feat(accounting): migration for schedule tables; entry_source gains schedule

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 4. `schedule_rules`: occurrences, horizon, installment split

**Model:** sonnet

**Files:**
- Create: `services/accounting-service/app/services/schedule_rules.py`
- Test: `services/accounting-service/tests/unit/test_schedule_rules.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces: `days_in_month`, `add_months`, `occurrence`, `occurrence_index`, `first_index_after`, `first_index_on_or_after`, `normalize_anchor`, `horizon`, `last_period_amount`, `plain`, `HORIZON_MONTHS = 13` exactly as in the Interface Contract.

Rules: occurrence k is always computed from the anchor (D30), so a 31st clamps to the 28th in February and returns to the 31st in March; `day_of_month = None` means the anchor's day; for `month` / `year`, occurrence 0 is the anchor's month on `day_of_month` (an anchor of 09-01 with `day_of_month = 15` starts on 09-15, as the edit scenario of Task 10 needs).

- [ ] 4.1 Write the failing test `services/accounting-service/tests/unit/test_schedule_rules.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from app.services import schedule_rules as rules


def test_month_end_and_leap_day_clamp_from_the_anchor():
    # Review Focus 1: always from the anchor, so February's 28th never sticks.
    anchor = date(2026, 1, 31)
    assert [rules.occurrence(anchor, "month", 1, k) for k in range(4)] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30),
    ]
    leap = date(2024, 2, 29)
    assert [rules.occurrence(leap, "year", 1, k) for k in range(5)] == [
        date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29),
    ]


def test_day_and_week_steps():
    anchor = date(2026, 10, 1)
    assert rules.occurrence(anchor, "day", 3, 2) == date(2026, 10, 7)
    assert rules.occurrence(anchor, "week", 2, 1) == date(2026, 10, 15)
    assert rules.occurrence(anchor, "month", 2, 3) == date(2027, 4, 1)


def test_day_of_month_overrides_the_anchor_day():
    anchor = date(2026, 9, 1)
    assert rules.occurrence(anchor, "month", 1, 0, 15) == date(2026, 9, 15)
    assert rules.occurrence(anchor, "month", 1, 1, 15) == date(2026, 10, 15)
    assert rules.occurrence(date(2027, 1, 31), "month", 1, 1, 31) == date(2027, 2, 28)


def test_occurrence_zero_before_the_anchor_day_is_indexed_and_normalized():
    # day_of_month earlier than the anchor's day: occurrence 0 falls before the anchor. occurrence_index still finds
    # it, and normalize_anchor (used on create / edit) moves the stored anchor to the first occurrence on or after it.
    anchor = date(2026, 9, 20)
    assert rules.occurrence(anchor, "month", 1, 0, 15) == date(2026, 9, 15)
    assert rules.occurrence_index(anchor, "month", 1, 15, date(2026, 9, 15)) == 0
    assert rules.occurrence_index(anchor, "month", 1, 15, date(2026, 9, 14)) is None
    assert rules.normalize_anchor(anchor, "month", 1, 15) == date(2026, 10, 15)
    assert rules.normalize_anchor(date(2026, 9, 1), "month", 1, 15) == date(2026, 9, 15)
    assert rules.normalize_anchor(date(2026, 10, 22), "month", 1, None) == date(2026, 10, 22)
    assert rules.normalize_anchor(date(2026, 9, 21), "week", 1, None) == date(2026, 9, 21)


def test_occurrence_index_and_first_indexes():
    anchor = date(2026, 10, 22)
    assert rules.occurrence_index(anchor, "month", 1, None, date(2027, 10, 22)) == 12
    assert rules.occurrence_index(anchor, "month", 1, None, date(2027, 10, 25)) is None
    assert rules.occurrence_index(anchor, "month", 1, None, date(2026, 10, 21)) is None
    assert rules.first_index_after(anchor, "month", 1, None, date(2027, 10, 25)) == 13
    assert rules.first_index_after(anchor, "month", 1, None, date(2027, 10, 15)) == 12
    assert rules.first_index_after(anchor, "month", 1, None, date(2026, 9, 1)) == 0
    assert rules.first_index_on_or_after(anchor, "month", 1, None, date(2026, 10, 22)) == 0
    assert rules.first_index_on_or_after(anchor, "month", 1, None, date(2026, 10, 23)) == 1


def test_week_index_follows_the_weekday():
    monday = date(2026, 9, 21)
    assert rules.occurrence_index(monday, "week", 1, None, date(2026, 10, 12)) == 3
    assert rules.occurrence_index(monday, "week", 1, None, date(2026, 10, 13)) is None


def test_horizon_is_thirteen_months_on_the_same_day_clamped():
    assert rules.horizon(date(2026, 10, 3)) == date(2027, 11, 3)
    assert rules.horizon(date(2026, 1, 31)) == date(2027, 2, 28)


def test_last_period_amount_carries_the_remainder():
    assert rules.last_period_amount(Decimal("300000"), Decimal("8333"), 36) == Decimal("8345")
    assert rules.last_period_amount(Decimal("10000"), Decimal("3333"), 3) == Decimal("3334")
    with pytest.raises(ValueError):
        rules.last_period_amount(Decimal("10000"), Decimal("5000"), 3)


def test_plain_decimal_strings():
    assert rules.plain("8333.0000") == "8333"
    assert rules.plain(Decimal("0.5000")) == "0.5"
    assert rules.plain(10) == "10"
    assert rules.plain("0.0000") == "0"
```

- [ ] 4.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_schedule_rules.py
```

Expected: `ImportError: cannot import name 'schedule_rules' from 'app.services'`.

- [ ] 4.3 Create `services/accounting-service/app/services/schedule_rules.py`:

```python
"""Pure date maths of schedule rules (design D30, D41).

Occurrence k is computed from the anchor every time, never from the previous occurrence, so a monthly rule on the
31st clamps to the end of February and returns to the 31st in March. Dates are naive Asia/Taipei dates.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

HORIZON_MONTHS = 13


def days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def add_months(day: date, months: int, day_of_month: int | None = None) -> date:
    """`day` moved by `months` calendar months, on `day_of_month` (default: day's own day), clamped to the month."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    target = day_of_month if day_of_month is not None else day.day
    return date(year, month, min(target, days_in_month(year, month)))


def _months_per_step(unit: str, n: int) -> int:
    return n if unit == "month" else 12 * n


def occurrence(anchor: date, unit: str, n: int, k: int, day_of_month: int | None = None) -> date:
    if k < 0:
        raise ValueError("occurrence index must be ≥ 0")
    if unit == "day":
        return anchor + timedelta(days=k * n)
    if unit == "week":
        return anchor + timedelta(days=7 * k * n)
    if unit in ("month", "year"):
        day = day_of_month if day_of_month is not None else anchor.day
        return add_months(anchor, k * _months_per_step(unit, n), day)
    raise ValueError(f"unknown interval unit {unit!r}")


def _estimate(anchor: date, unit: str, n: int, day: date) -> int:
    """A k whose occurrence is close to `day` (never negative); callers step from it."""
    if unit in ("day", "week"):
        step = n if unit == "day" else 7 * n
        return max((day - anchor).days // step, 0)
    months = (day.year - anchor.year) * 12 + day.month - anchor.month
    return max(months // _months_per_step(unit, n), 0)


def first_index_on_or_after(anchor: date, unit: str, n: int, day_of_month: int | None, on: date) -> int:
    """The smallest k ≥ 0 whose occurrence is on or after `on`."""
    k = _estimate(anchor, unit, n, on)
    while k > 0 and occurrence(anchor, unit, n, k - 1, day_of_month) >= on:
        k -= 1
    while occurrence(anchor, unit, n, k, day_of_month) < on:
        k += 1
    return k


def first_index_after(anchor: date, unit: str, n: int, day_of_month: int | None, after: date) -> int:
    """The smallest k ≥ 0 whose occurrence is strictly after `after`."""
    return first_index_on_or_after(anchor, unit, n, day_of_month, after + timedelta(days=1))


def normalize_anchor(anchor: date, unit: str, n: int, day_of_month: int | None) -> date:
    """The rule's first occurrence on or after `anchor`: the stored anchor_date is occurrence 0 (spec), so a
    day_of_month earlier than the anchor's day (anchor 09-20, day 15) starts on 10-15, never on 09-15."""
    return occurrence(anchor, unit, n, first_index_on_or_after(anchor, unit, n, day_of_month, anchor), day_of_month)


def occurrence_index(anchor: date, unit: str, n: int, day_of_month: int | None, day: date) -> int | None:
    """k when `day` is occurrence k of the rule, else None. Compared with occurrence 0, not the anchor: with a
    day_of_month earlier than the anchor's day, occurrence 0 precedes the anchor."""
    if day < occurrence(anchor, unit, n, 0, day_of_month):
        return None
    k = first_index_on_or_after(anchor, unit, n, day_of_month, day)
    return k if occurrence(anchor, unit, n, k, day_of_month) == day else None


def horizon(today: date) -> date:
    """today + 13 months, same day of month, clamped (spec "Instance generation")."""
    return add_months(today, HORIZON_MONTHS)


def last_period_amount(total: Decimal, per_period: Decimal, times: int) -> Decimal:
    """The last period of an installment: what the other periods leave of the total (MOZE 分期餘額納入 末期)."""
    last = Decimal(total) - Decimal(per_period) * (times - 1)
    if last <= 0:
        raise ValueError("the per-period amount times (times − 1) reaches the total")
    return last


def plain(value: Decimal | str | int) -> str:
    """A decimal as an unsigned-looking plain string without trailing zeros: '8333', '0.5', '10'."""
    normalized = Decimal(str(value)).normalize()
    return format(normalized, "f")
```

- [ ] 4.4 Run 4.2 again.

Expected: `9 passed`.

- [ ] 4.5 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_rules.py services/accounting-service/tests/unit/test_schedule_rules.py
git commit -m "feat(accounting): schedule rule date maths

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 5. `schedule_locks`, `schedule_templates`, request schemas

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_locks.py`
- Create: `services/accounting-service/app/services/schedule_templates.py`
- Create: `services/accounting-service/app/schemas/schedules.py`
- Modify: `services/accounting-service/tests/helpers.py` (`schedule_line`, `make_definition`, `make_instance`, `POSTED_AT`)
- Modify: `services/accounting-service/tests/conftest.py` (`today` fixture; `Seed.line`, `Seed.definition`, `Seed.instance`)
- Test: `services/accounting-service/tests/integration/test_schedule_templates.py`

**Interfaces:**
- Consumes: `IMPORT_LOCK_KEY` (`moze_import_service`), `ValidationError`, `ConflictError`, `NotFoundError` (`app.services.errors`), `plain` (Task 4), the models (Task 2), `Money`, `NonNegativeMoney`, `Int32`, `Currency`, `ShortText` (`app.schemas.writes`).
- Produces: everything listed for `schedule_locks.py`, `schedule_templates.py` and `app/schemas/schedules.py` in the Interface Contract; test helpers `schedule_line(kind, account, amount, **fields) -> dict`, `make_definition(session, lines, *, kind="recurring", name="Netflix", interval_unit="month", anchor=date(2026, 10, 22), auto_post_from=date(2026, 10, 3), description=None, tags=(), **columns)`, `make_instance(session, definition, seq, day, *, status="pending", entries=(), acted_by=None, rule_date=None, **columns)`, `POSTED_AT`; conftest `today(day) -> date` (patches `ledger_service._today`) and `seed.line / seed.definition / seed.instance`.

Rules: every validation error names `lines[i].<key>` (or `lines`, `amounts`) and carries a message without amounts or names (it ends up in `last_error`). `normalize_template` stores every key of `LINE_KEYS` (absent ones `null`) and amounts as plain strings. A template has at most one `repayment` / `collection` line.

- [ ] 5.1 Add the test helpers. Append to `services/accounting-service/tests/helpers.py` (and add `from datetime import datetime, timezone` next to its `from datetime import date`, plus `from app.models import ScheduleDefinition, ScheduleInstance` and `from app.services.schedule_templates import LINE_KEYS` to its imports):

```python
POSTED_AT = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)


def schedule_line(kind: str, account: Account, amount: str, **fields) -> dict:
    """A template line with every key (unused ones None), in the account's currency."""
    line = dict.fromkeys(LINE_KEYS)
    line.update(kind=kind, account_id=account.id, amount=str(amount), currency=account.currency)
    line.update(fields)
    return line


def make_definition(
    session,
    lines: list[dict],
    *,
    kind: str = "recurring",
    name: str = "Netflix",
    interval_unit: str = "month",
    anchor: date = date(2026, 10, 22),
    auto_post_from: date = date(2026, 10, 3),
    description: str | None = None,
    tags=(),
    **columns,
) -> ScheduleDefinition:
    """Insert a definition (flushed, not committed) without generating instances or validating the template."""
    definition = ScheduleDefinition(
        kind=kind, name=name, template={"lines": list(lines), "description": description, "tags": list(tags)},
        interval_unit=interval_unit, anchor_date=anchor, auto_post_from=auto_post_from, **columns,
    )
    session.add(definition)
    session.flush()
    return definition


def make_instance(
    session,
    definition: ScheduleDefinition,
    seq: int,
    day: date,
    *,
    status: str = "pending",
    entries=(),
    acted_by: str | None = None,
    rule_date: date | None = None,
    **columns,
) -> ScheduleInstance:
    """Insert one period; posted / skipped ones get acted_at (POSTED_AT unless given) and acted_by (auto unless given)."""
    acted = {} if status == "pending" else {"acted_at": columns.pop("acted_at", POSTED_AT), "acted_by": acted_by or "auto"}
    instance = ScheduleInstance(
        definition_id=definition.id, seq=seq, rule_date=rule_date or day, due_date=day, status=status,
        posted_entry_ids=[getattr(entry, "id", entry) for entry in entries], **acted, **columns,
    )
    session.add(instance)
    session.flush()
    return instance
```

  In `services/accounting-service/tests/conftest.py`: extend `from tests.helpers import make_account, make_entry` to `from tests.helpers import make_account, make_definition, make_entry, make_instance, schedule_line`; add `ScheduleDefinition, ScheduleInstance` to the `from app.models import (...)` line; add to `class Seed`:

```python
    def line(self, kind, account, amount, **fields) -> dict:
        return schedule_line(kind, account, amount, **fields)

    def definition(self, lines, **fields) -> ScheduleDefinition:
        return make_definition(self.db, lines, **fields)

    def instance(self, definition, seq, day, **fields) -> ScheduleInstance:
        return make_instance(self.db, definition, seq, day, **fields)
```

  and after the `seed` fixture:

```python
@pytest.fixture()
def today(monkeypatch):
    """today(date(2026, 10, 3)) fixes ledger_service._today(), the Taipei date every schedule service reads."""
    from app.services import ledger_service

    def set_today(day: date) -> date:
        monkeypatch.setattr(ledger_service, "_today", lambda: day)
        return day

    return set_today
```

- [ ] 5.2 Write the failing test `services/accounting-service/tests/integration/test_schedule_templates.py`:

```python
"""Template lines (spec "Template lines") and the schedule lock helpers (design D32)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services import schedule_locks as locks
from app.services import schedule_templates as templates
from app.services.errors import NotFoundError, ValidationError
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _template(*lines) -> dict:
    return {"lines": list(lines), "description": None, "tags": []}


def _field(exc_info) -> str:
    return exc_info.value.field


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸")
    return bank, lender, payable


def test_loan_repayment_template_is_accepted(db_session, seed, loan):
    # Spec "Loan repayment template".
    bank, _, payable = loan
    template = templates.normalize_template(
        _template(
            {"kind": "repayment", "account_id": bank.id, "amount": Decimal("8333"), "currency": "TWD", "loan_entry_id": payable.id},
            {"kind": "interest", "account_id": bank.id, "amount": Decimal("620"), "currency": "TWD"},
        )
    )
    templates.validate_template(db_session, template)
    assert templates.loan_line(template) == (0, template["lines"][0])
    assert templates.template_amounts(template) == ["8333", "620"]


def test_currency_mismatch_names_the_line(db_session, seed):
    # Spec "Currency mismatch refused".
    yen = seed.account("日幣現金", currency="JPY")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("expense", yen, "1000", currency="TWD")))
    assert _field(exc) == "lines[0].currency"


def test_missing_or_archived_account_refused(db_session, seed):
    old = seed.account("舊卡", is_archived=True)
    wallet = seed.account()
    for line in (seed.line("expense", wallet, "1", account_id=9999), seed.line("expense", old, "1")):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(seed.line("income", wallet, "5"), line))
        assert _field(exc) == "lines[1].account_id"


def test_transfer_lines(db_session, seed):
    pay, broker, yen = seed.account("薪轉"), seed.account("交割"), seed.account("日幣", currency="JPY")
    cases = [
        (seed.line("transfer", pay, "15000"), "lines[0].to_account_id"),
        (seed.line("transfer", pay, "15000", to_account_id=pay.id), "lines[0].to_account_id"),
        (seed.line("transfer", pay, "15000", to_account_id=yen.id), "lines[0].to_amount"),
        (seed.line("transfer", pay, "15000", to_account_id=broker.id, to_amount="15000"), "lines[0].to_amount"),
        (seed.line("expense", pay, "15000", to_account_id=broker.id), "lines[0].to_account_id"),
    ]
    for line, field in cases:
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(line))
        assert _field(exc) == field
    templates.validate_template(db_session, _template(seed.line("transfer", pay, "15000", to_account_id=broker.id)))
    templates.validate_template(db_session, _template(seed.line("transfer", pay, "15000", to_account_id=yen.id, to_amount="70000")))


def test_receivable_and_payable_lines_need_a_counterparty(db_session, seed):
    wallet = seed.account()
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("receivable", wallet, "100")))
    assert _field(exc) == "lines[0].counterparty_id"
    alan = seed.counterparty("Alan")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("expense", wallet, "100", counterparty_id=alan.id)))
    assert _field(exc) == "lines[0].counterparty_id"
    templates.validate_template(db_session, _template(seed.line("receivable", wallet, "100", counterparty_id=alan.id)))


def test_repayment_needs_a_payable_original_in_its_currency(db_session, seed, loan):
    bank, lender, payable = loan
    receivable = seed.entry(bank, "-500", kind="receivable", counterparty_id=lender.id)
    repayment = seed.entry(bank, "-100", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id, is_settlement=True)
    yen = seed.account("日幣", currency="JPY")
    for line in (
        seed.line("repayment", bank, "8333"),
        seed.line("repayment", bank, "8333", loan_entry_id=receivable.id),
        seed.line("repayment", bank, "8333", loan_entry_id=repayment.id),
        seed.line("repayment", yen, "8333", loan_entry_id=payable.id),
        seed.line("collection", bank, "8333", loan_entry_id=payable.id),
    ):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(line))
        assert _field(exc) == "lines[0].loan_entry_id"
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(
            db_session,
            _template(
                seed.line("repayment", bank, "1", loan_entry_id=payable.id),
                seed.line("repayment", bank, "1", loan_entry_id=payable.id),
            ),
        )
    assert _field(exc) == "lines[1].kind"


def test_category_must_belong_to_the_line_kind(db_session, seed, loan):
    bank, _, payable = loan
    food = seed.category("飲食")
    loans = seed.category("貸款", kind="payable")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(
            db_session, _template(seed.line("repayment", bank, "8333", loan_entry_id=payable.id, category_id=food.id))
        )
    assert _field(exc) == "lines[0].category_id"
    templates.validate_template(
        db_session, _template(seed.line("repayment", bank, "8333", loan_entry_id=payable.id, category_id=loans.id))
    )


def test_line_count_and_amount_format(db_session, seed):
    wallet = seed.account()
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template())
    assert _field(exc) == "lines"
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(*[seed.line("expense", wallet, "1")] * 11))
    assert _field(exc) == "lines"
    for amount in ("0", "-5", "1.23456", "abc"):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(seed.line("expense", wallet, amount)))
        assert _field(exc) == "lines[0].amount"


def test_normalize_fills_every_key_with_plain_amounts():
    template = templates.normalize_template(
        {"lines": [{"kind": "expense", "account_id": 1, "amount": Decimal("390.0000"), "currency": "TWD"}], "tags": ["訂閱"]}
    )
    assert set(template["lines"][0]) == set(templates.LINE_KEYS)
    assert template["lines"][0]["amount"] == "390"
    assert (template["description"], template["tags"]) == (None, ["訂閱"])


def test_check_amounts_aligns_with_the_lines():
    # Spec "Override length checked".
    template = {"lines": [{"kind": "repayment"}, {"kind": "interest"}], "description": None, "tags": []}
    for amounts in (["8333"], ["8333", "-1"], ["0", "0"], ["8333", "1.00001"]):
        with pytest.raises(ValidationError) as exc:
            templates.check_amounts(template, amounts)
        assert _field(exc) == "amounts"
    assert templates.check_amounts(template, [Decimal("8333.0000"), "0"]) == ["8333", "0"]
    assert templates.check_amounts(template, None) is None


def test_realign_keeps_same_kind_at_the_same_index():
    old = {"lines": [{"kind": "repayment", "amount": "8333"}, {"kind": "interest", "amount": "620"}]}
    same = {"lines": [{"kind": "repayment", "amount": "9000"}, {"kind": "interest", "amount": "600"}]}
    swapped = {"lines": [{"kind": "expense", "amount": "100"}, {"kind": "interest", "amount": "600"}]}
    single = {"lines": [{"kind": "expense", "amount": "100"}]}
    assert templates.realign_override(old, same, ["8345", "598"]) == ["8345", "598"]
    assert templates.realign_override(old, swapped, ["8345", "598"]) == ["100", "598"]
    assert templates.realign_override(old, single, ["8345", "598"]) is None
    assert templates.realign_override(old, same, None) is None


def test_import_key_is_refused_while_an_import_holds_it(db_session, pg_engine):
    assert locks.import_key_free(pg_engine) is True
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            with pytest.raises(locks.ImportRunningError) as exc:
                locks.take_import_key_shared(db_session)
            assert str(exc.value) == "import_running"
            assert locks.import_key_free(pg_engine) is False
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    db_session.rollback()
    locks.take_import_key_shared(db_session)
    assert locks.import_key_free(pg_engine) is True


def test_unknown_rows_are_not_found(db_session):
    with pytest.raises(NotFoundError):
        locks.lock_definition(db_session, 424242)
    with pytest.raises(NotFoundError):
        locks.get_instance(db_session, 424242)
    assert locks.lock_instance(db_session, 424242) is None
```

- [ ] 5.3 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_templates.py
```

Expected: collection error `ModuleNotFoundError: No module named 'app.services.schedule_templates'` (raised from `tests/helpers.py`).

- [ ] 5.4 Create `services/accounting-service/app/services/schedule_locks.py`:

```python
"""Locks of every schedule write path (design D32).

Order: the shared import advisory key (transaction-scoped) → the schedule_definition row (FOR SHARE when posting or
acting on one instance, FOR UPDATE when editing the definition) → schedule_instance row(s) (FOR UPDATE, ascending
id) → the ledger's own order (entry_group rows ascending → target entries with their transfer legs in one
statement). No path that holds an entry or group lock ever locks a schedule row. The backup importer holds the
import key exclusively, so `take_import_key_shared` answers 409 import_running for the seconds an import runs.
"""

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from .errors import ConflictError, NotFoundError
from .moze_import_service import IMPORT_LOCK_KEY

SCHEDULE_JOB_LOCK_KEY = 0x53434844  # "SCHD"; held by one job run at a time (session-level, dedicated connection)
IMPORT_RUNNING = "import_running"


class ImportRunningError(ConflictError):
    """A backup or CSV import holds the import key exclusively; HTTP 409 with message import_running."""

    def __init__(self) -> None:
        super().__init__(IMPORT_RUNNING)


def take_import_key_shared(db: Session) -> None:
    """Share the import advisory key until this transaction ends; raise ImportRunningError while an import runs."""
    acquired = db.execute(
        text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY}
    ).scalar_one()
    if not acquired:
        raise ImportRunningError()


def import_key_free(engine: Engine) -> bool:
    """True when no import holds the key right now (run-now checks this before starting)."""
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY}).scalar_one()
        if acquired:
            conn.execute(text("SELECT pg_advisory_unlock_shared(:key)"), {"key": IMPORT_LOCK_KEY})
        conn.commit()
    return bool(acquired)


def get_instance(db: Session, instance_id: int) -> ScheduleInstance:
    """Unlocked read (to learn definition_id, which never changes, before locking the definition first)."""
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    return instance


def lock_definition(db: Session, definition_id: int, *, share: bool = False) -> ScheduleDefinition:
    definition = db.execute(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.id == definition_id)
        .with_for_update(read=share)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if definition is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    return definition


def lock_instance(db: Session, instance_id: int, *, skip_locked: bool = False) -> ScheduleInstance | None:
    """The instance FOR UPDATE (SKIP LOCKED for the job: an instance the owner is acting on is left to the owner)."""
    return db.execute(
        select(ScheduleInstance)
        .where(ScheduleInstance.id == instance_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_instances(db: Session, definition_id: int) -> list[ScheduleInstance]:
    """Every instance of the definition, FOR UPDATE in ascending id order."""
    return list(
        db.scalars(
            select(ScheduleInstance)
            .where(ScheduleInstance.definition_id == definition_id)
            .order_by(ScheduleInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
```

- [ ] 5.5 Create `services/accounting-service/app/services/schedule_templates.py`:

```python
"""Template lines of a schedule definition (design D29): normalisation, validation, overrides, references.

Messages never carry amounts or names: a validation error raised while posting becomes the instance's last_error.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, LedgerEntry, Project
from .errors import ValidationError
from .schedule_rules import plain

LINE_KINDS = ("expense", "income", "receivable", "payable", "transfer", "repayment", "collection", "interest")
LINE_KEYS = (
    "kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount",
    "currency", "loan_entry_id", "name", "merchant",
)
# Line kind → the ledger kind of the entry it writes (the category must be of this kind).
LEDGER_KINDS = {
    "expense": "expense", "income": "income", "receivable": "receivable", "payable": "payable",
    "transfer": "transfer_out", "repayment": "payable", "collection": "receivable", "interest": "interest",
}
LOAN_LINE_KINDS = {"repayment": "payable", "collection": "receivable"}
COUNTERPARTY_LINE_KINDS = ("receivable", "payable")
NO_COUNTERPARTY_LINE_KINDS = ("expense", "income", "transfer")
MAX_LINES = 10


def _decimal(value, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(field, "金額格式錯誤") from exc
    if not number.is_finite() or number.normalize().as_tuple().exponent < -4:
        raise ValidationError(field, "金額格式錯誤")
    if number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(field, "金額須大於 0")
    return number


def normalize_template(template: dict) -> dict:
    """Every line with exactly LINE_KEYS (absent keys None), amounts as plain strings."""
    lines = []
    for raw in template.get("lines") or []:
        line = {key: raw.get(key) for key in LINE_KEYS}
        for key in ("amount", "to_amount"):
            if line[key] is not None:
                line[key] = plain(line[key])
        lines.append(line)
    return {"lines": lines, "description": template.get("description"), "tags": list(template.get("tags") or [])}


def _open_account(db: Session, account_id, field: str) -> Account:
    account = db.get(Account, account_id) if account_id is not None else None
    if account is None:
        raise ValidationError(field, "找不到帳戶")
    if account.is_archived:
        raise ValidationError(field, "帳戶已封存")
    return account


def _check_loan(db: Session, line: dict, kind: str, field: str) -> None:
    loan_id = line["loan_entry_id"]
    loan = db.get(LedgerEntry, loan_id) if loan_id is not None else None
    if (
        loan is None
        or loan.kind != LOAN_LINE_KINDS[kind]
        or loan.is_settlement
        or loan.settles_entry_id is not None
        or loan.parent_entry_id is not None
    ):
        raise ValidationError(field, "貸款記錄不存在或類型不符")
    if loan.currency != line["currency"]:
        raise ValidationError(field, "貸款幣別與這一行不同")


def validate_template(db: Session, template: dict) -> None:
    """Spec "Template lines": every rule, at save time and again at posting time (archived rows fail then)."""
    lines = template.get("lines") or []
    if not 1 <= len(lines) <= MAX_LINES:
        raise ValidationError("lines", f"需要 1 到 {MAX_LINES} 行")
    loan_lines = 0
    for index, line in enumerate(lines):
        def field(key: str) -> str:
            return f"lines[{index}].{key}"

        kind = line.get("kind")
        if kind not in LINE_KINDS:
            raise ValidationError(field("kind"), "不支援的類型")
        account = _open_account(db, line.get("account_id"), field("account_id"))
        if line.get("currency") != account.currency:
            raise ValidationError(field("currency"), "幣別須與帳戶相同")
        _decimal(line.get("amount"), field("amount"))
        if kind == "transfer":
            if line.get("to_account_id") is None:
                raise ValidationError(field("to_account_id"), "轉帳需要轉入帳戶")
            if line["to_account_id"] == account.id:
                raise ValidationError(field("to_account_id"), "轉入帳戶不可與轉出帳戶相同")
            target = _open_account(db, line["to_account_id"], field("to_account_id"))
            if target.currency != account.currency:
                if line.get("to_amount") is None:
                    raise ValidationError(field("to_amount"), "跨幣別轉帳需要轉入金額")
                _decimal(line["to_amount"], field("to_amount"))
            elif line.get("to_amount") is not None:
                raise ValidationError(field("to_amount"), "同幣別轉帳不填轉入金額")
        else:
            for key in ("to_account_id", "to_amount"):
                if line.get(key) is not None:
                    raise ValidationError(field(key), "只有轉帳使用")
        if kind in COUNTERPARTY_LINE_KINDS:
            counterparty_id = line.get("counterparty_id")
            if counterparty_id is None or db.get(Counterparty, counterparty_id) is None:
                raise ValidationError(field("counterparty_id"), "應收應付需要對象")
        elif kind in NO_COUNTERPARTY_LINE_KINDS and line.get("counterparty_id") is not None:
            raise ValidationError(field("counterparty_id"), "這個類型不設對象")
        if kind in LOAN_LINE_KINDS:
            loan_lines += 1
            if loan_lines > 1:
                raise ValidationError(field("kind"), "一個排程只能有一行還款或收款")
            _check_loan(db, line, kind, field("loan_entry_id"))
        elif line.get("loan_entry_id") is not None:
            raise ValidationError(field("loan_entry_id"), "只有還款或收款使用")
        if line.get("category_id") is not None:
            category = db.get(Category, line["category_id"])
            if category is None or category.kind != LEDGER_KINDS[kind]:
                raise ValidationError(field("category_id"), "類別與類型不符")
        if line.get("project_id") is not None and db.get(Project, line["project_id"]) is None:
            raise ValidationError(field("project_id"), "找不到專案")


def template_amounts(template: dict) -> list[str]:
    return [line["amount"] for line in template["lines"]]


def resolved_amounts(definition, instance) -> list[Decimal]:
    """D29 precedence: the instance's override (owner edit or imported amounts) when set, else the template's."""
    amounts = instance.amount_override if instance.amount_override is not None else template_amounts(definition.template)
    return [Decimal(str(value)) for value in amounts]


def check_amounts(template: dict, amounts: list | None) -> list[str] | None:
    """An override aligned with the template's lines (HTTP 422 naming `amounts` otherwise), as plain strings."""
    if amounts is None:
        return None
    if len(amounts) != len(template["lines"]):
        raise ValidationError("amounts", f"需要 {len(template['lines'])} 個金額，每一行一個")
    values = [_decimal(value, "amounts", allow_zero=True) for value in amounts]
    if all(value == 0 for value in values):
        raise ValidationError("amounts", "至少一行金額須大於 0")
    return [plain(value) for value in values]


def realign_override(old: dict, new: dict, override: list[str] | None) -> list[str] | None:
    """D35: keep an override for a line still at the same index with the same kind; a changed line takes the new
    template amount. None when nothing is kept."""
    if override is None:
        return None
    old_lines, result, kept = old["lines"], [], False
    for index, line in enumerate(new["lines"]):
        if index < len(old_lines) and index < len(override) and old_lines[index]["kind"] == line["kind"]:
            result.append(override[index])
            kept = True
        else:
            result.append(line["amount"])
    return result if kept else None


def loan_line(template: dict) -> tuple[int, dict] | None:
    for index, line in enumerate(template["lines"]):
        if line.get("kind") in LOAN_LINE_KINDS:
            return index, line
    return None


def referenced_ids(template: dict) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {"account": set(), "category": set(), "counterparty": set(), "project": set(), "loan": set()}
    for line in template["lines"]:
        for key, bucket in (
            ("account_id", "account"), ("to_account_id", "account"), ("category_id", "category"),
            ("counterparty_id", "counterparty"), ("project_id", "project"), ("loan_entry_id", "loan"),
        ):
            if line.get(key) is not None:
                found[bucket].add(int(line[key]))
    return found
```

- [ ] 5.6 Create `services/accounting-service/app/schemas/schedules.py`:

```python
"""Request and response bodies of /schedules (spec "Definition endpoints", "Instance endpoints")."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .writes import Currency, Int32, Money, NonNegativeMoney, ShortText

ScheduleLineKind = Literal["expense", "income", "receivable", "payable", "transfer", "repayment", "collection", "interest"]
IntervalUnit = Literal["day", "week", "month", "year"]
PostingMode = Literal["auto", "confirm"]
DefinitionKind = Literal["recurring", "installment"]
DefinitionStatus = Literal["active", "paused", "ended"]
InstanceStatus = Literal["pending", "posted", "skipped"]


class TemplateLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ScheduleLineKind
    account_id: Int32
    to_account_id: Int32 | None = None
    to_amount: Money | None = None
    counterparty_id: Int32 | None = None
    category_id: Int32 | None = None
    project_id: Int32 | None = None
    amount: Money
    currency: Currency
    loan_entry_id: Int32 | None = None
    name: ShortText | None = None
    merchant: ShortText | None = None


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[TemplateLineIn] = Field(min_length=1, max_length=10)
    description: str | None = None
    tags: list[Annotated[str, Field(max_length=64)]] = []


class LoanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: Int32
    counterparty_id: Int32
    category_id: Int32 | None = None
    amount: Money
    entry_date: date
    name: ShortText | None = None


class DefinitionUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    template: TemplateIn
    interval_unit: IntervalUnit
    interval_n: Annotated[int, Field(ge=1, le=999)] = 1
    anchor_date: date
    day_of_month: Annotated[int, Field(ge=1, le=31)] | None = None
    times: Annotated[int, Field(ge=1, le=9999)] | None = None
    end_date: date | None = None
    total_amount: Money | None = None
    posting_mode: PostingMode | None = None  # None (left out) keeps the stored mode; a PUT never switches it silently


class DefinitionIn(DefinitionUpdateIn):
    kind: DefinitionKind
    loan: LoanIn | None = None
    posting_mode: PostingMode = "auto"


class ResumeIn(BaseModel):
    backlog: Literal["skip", "post"] = "skip"


class ModeIn(BaseModel):
    posting_mode: PostingMode


class InstanceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    amounts: list[NonNegativeMoney] | None = None


class RepostIn(BaseModel):
    amounts: list[NonNegativeMoney] = Field(min_length=1)


class TemplateLineOut(BaseModel):
    kind: ScheduleLineKind
    account_id: int
    to_account_id: int | None
    to_amount: str | None
    counterparty_id: int | None
    category_id: int | None
    project_id: int | None
    amount: str
    currency: str
    loan_entry_id: int | None
    name: str | None
    merchant: str | None
    account_name: str | None
    to_account_name: str | None
    category: str | None
    counterparty: str | None


class TemplateOut(BaseModel):
    lines: list[TemplateLineOut]
    description: str | None
    tags: list[str]


class CurrencyAmountOut(BaseModel):
    currency: str
    amount: Decimal


class FailingOut(BaseModel):
    instance_id: int
    due_date: date
    last_error: str


class DefinitionOut(BaseModel):
    id: int
    kind: DefinitionKind
    name: str
    status: DefinitionStatus
    posting_mode: PostingMode
    interval_unit: IntervalUnit
    interval_n: int
    anchor_date: date
    day_of_month: int | None
    first_seq: int
    times: int | None
    end_date: date | None
    total_amount: Decimal | None
    auto_post_from: date
    template: TemplateOut
    created_locally: bool
    imported: bool
    locked: bool
    review_reason: str | None
    generated_until: date | None
    posted_count: int
    skipped_count: int
    pending_count: int
    next_due_date: date | None
    next_amount: list[CurrencyAmountOut]
    remaining: Decimal | None
    repaid: Decimal | None
    loan_entry_id: int | None
    needs_check: bool
    failing: FailingOut | None
    category_icon: str | None
    category_color: str | None


class InstanceLineOut(BaseModel):
    kind: ScheduleLineKind
    account_id: int
    account_name: str | None
    to_account_id: int | None
    to_account_name: str | None
    category: str | None
    counterparty: str | None
    amount: Decimal
    currency: str


class InstanceOut(BaseModel):
    id: int
    definition_id: int
    definition_name: str
    kind: DefinitionKind
    posting_mode: PostingMode
    seq: int
    times: int | None
    due_date: date
    rule_date: date
    status: InstanceStatus
    is_partial: bool
    overdue_days: int
    lines: list[InstanceLineOut]
    totals: list[CurrencyAmountOut]
    amounts: list[str]
    last_error: str | None
    reopened: bool
    edited_by_owner: bool
    note: str | None
    posted_entry_ids: list[int]
    acted_at: datetime | None
    acted_by: Literal["auto", "owner", "import"] | None
    category_icon: str | None
    category_color: str | None


class DefinitionDetailOut(DefinitionOut):
    instances: list[InstanceOut]


class FailedOut(BaseModel):
    instance_id: int
    error: str


class CatchUpOut(BaseModel):
    posted: list[int]
    failed: FailedOut | None
    definition: DefinitionOut


class RunReportOut(BaseModel):
    trigger: str
    today: date
    status: str
    generated: int
    posted: list[int]
    failed: list[int]
    stopped_definitions: list[int]
```

- [ ] 5.7 Run 5.3 again.

Expected: `13 passed`.

- [ ] 5.8 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_locks.py services/accounting-service/app/services/schedule_templates.py \
  services/accounting-service/app/schemas/schedules.py services/accounting-service/tests/helpers.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/integration/test_schedule_templates.py
git commit -m "feat(accounting): schedule locks, template validation and schemas

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 6. `schedule_generation`

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_generation.py`
- Test: `services/accounting-service/tests/integration/test_schedule_generation.py`

**Interfaces:**
- Consumes: `schedule_rules` (Task 4); `take_import_key_shared`, `lock_definition` (Task 5); `template_amounts` (Task 5); conftest `seed`, `today`, `pg_engine`; helper `POSTED_AT`.
- Produces: `last_period_override(definition) -> list[str] | None`, `generate(db, definition, today) -> int`, `series_complete(db, definition) -> bool`, `end_if_complete(db, definition) -> bool`, `generate_locked(db, definition_id, today) -> int`.

Rules (D30): generation finds the latest instance by `rule_date` (any status) and inserts the next occurrences strictly after it (occurrence 0 when none), `seq` = previous maximum + 1 (`first_seq` for the first), while `seq ≤ times`, `date ≤ end_date`, `date ≤ horizon(today)`; an occurrence equal to a **posted** instance's `due_date` is passed over without consuming a seq; nothing existing is deleted, moved or re-dated; `generated_until = horizon`. `active` and `paused` definitions generate, `ended` ones do not, and neither does a definition with `review_reason = interval_mismatch` (an imported period whose rule did not match MOZE's dates carries an invented rule; resuming it must not post invented dates). A local installment with `total_amount` gets `amount_override = last_period_override(...)` on the instance whose `seq == times`, whenever it is generated (a 36-month loan reaches it after two years). A definition ends when its series is complete and no instance is pending **or partial** (a partial period on an ended definition revives it, D33, so the job must not end it again). Callers hold the definition lock; `generate_locked` takes the shared import key and `FOR UPDATE` itself.

- [ ] 6.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_generation.py`:

```python
"""Instance generation (spec "Instance generation", design D30)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import ScheduleInstance
from app.services import schedule_generation as generation
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import ImportRunningError
from tests.helpers import POSTED_AT


def _instances(db, definition) -> list[ScheduleInstance]:
    db.flush()
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


def _dues(db, definition) -> list[date]:
    return [row.due_date for row in _instances(db, definition)]


@pytest.fixture()
def netflix(seed):
    card = seed.account("範例卡")
    return seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22))


def test_thirteen_month_horizon(db_session, netflix):
    # Spec "13-month horizon".
    assert generation.generate(db_session, netflix, date(2026, 10, 3)) == 13
    rows = _instances(db_session, netflix)
    assert [row.seq for row in rows] == list(range(1, 14))
    assert (rows[0].due_date, rows[-1].due_date) == (date(2026, 10, 22), date(2027, 10, 22))
    assert all(row.rule_date == row.due_date and row.status == "pending" for row in rows)
    assert netflix.generated_until == date(2027, 11, 3)


def test_month_end_anchor_generates_feb_28_then_returns_to_31(db_session, seed):
    # Spec "Month-end anchor clamps and returns"; Review Focus 1.
    wallet = seed.account()
    rent = seed.definition([seed.line("expense", wallet, "100")], anchor=date(2026, 1, 31), auto_post_from=date(2026, 1, 10))
    generation.generate(db_session, rent, date(2026, 1, 10))
    dues = _dues(db_session, rent)
    assert dues[:4] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert date(2026, 10, 31) in dues and date(2026, 11, 30) in dues


def test_finite_run(db_session, seed):
    # Spec "Finite run".
    wallet = seed.account()
    finite = seed.definition([seed.line("expense", wallet, "100")], anchor=date(2026, 11, 9), times=3)
    assert generation.generate(db_session, finite, date(2026, 10, 3)) == 3
    assert _dues(db_session, finite) == [date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)]


def test_end_date_stops_the_run(db_session, seed):
    # Spec "End date stops the run".
    wallet = seed.account()
    weekly = seed.definition(
        [seed.line("expense", wallet, "100")], interval_unit="week", anchor=date(2026, 10, 5), end_date=date(2026, 10, 26)
    )
    generation.generate(db_session, weekly, date(2026, 10, 3))
    assert _dues(db_session, weekly) == [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]


def test_rolling_forward(db_session, netflix):
    # Spec "Rolling forward".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    assert generation.generate(db_session, netflix, date(2026, 10, 21)) == 0
    assert max(row.seq for row in _instances(db_session, netflix)) == 13
    assert generation.generate(db_session, netflix, date(2026, 10, 22)) == 1
    last = _instances(db_session, netflix)[-1]
    assert (last.seq, last.due_date) == (14, date(2027, 11, 22))


def test_a_period_moved_later_does_not_shift_the_series(db_session, netflix):
    # Spec "A period moved later does not shift the series".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    thirteen = _instances(db_session, netflix)[-1]
    thirteen.due_date = date(2027, 10, 25)
    db_session.flush()
    generation.generate(db_session, netflix, date(2026, 10, 22))
    rows = _instances(db_session, netflix)
    assert (rows[-1].seq, rows[-1].rule_date, rows[-1].due_date) == (14, date(2027, 11, 22), date(2027, 11, 22))
    assert (rows[12].rule_date, rows[12].due_date) == (date(2027, 10, 22), date(2027, 10, 25))


def test_a_period_moved_earlier_does_not_repeat_the_series(db_session, netflix):
    # Spec "A period moved earlier does not repeat the series".
    generation.generate(db_session, netflix, date(2026, 10, 3))
    thirteen = _instances(db_session, netflix)[-1]
    thirteen.due_date = date(2027, 10, 15)
    db_session.flush()
    generation.generate(db_session, netflix, date(2026, 10, 22))
    rows = _instances(db_session, netflix)
    assert (rows[-1].seq, rows[-1].due_date) == (14, date(2027, 11, 22))
    assert date(2027, 10, 22) not in [row.due_date for row in rows]


def test_a_posted_day_is_passed_over_without_a_seq(db_session, seed):
    wallet = seed.account()
    entry = seed.entry(wallet, "-390", day=date(2026, 11, 22), source="schedule")
    definition = seed.definition([seed.line("expense", wallet, "390")], anchor=date(2026, 10, 22), times=3)
    seed.instance(definition, 1, date(2026, 11, 22), rule_date=date(2026, 10, 22), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2026, 10, 3))
    rows = _instances(db_session, definition)
    assert [(row.seq, row.rule_date, row.due_date) for row in rows] == [
        (1, date(2026, 10, 22), date(2026, 11, 22)),
        (2, date(2026, 12, 22), date(2026, 12, 22)),
        (3, date(2027, 1, 22), date(2027, 1, 22)),
    ]


def test_paused_definitions_generate_and_ended_ones_do_not(db_session, seed):
    wallet = seed.account()
    paused = seed.definition([seed.line("expense", wallet, "1")], status="paused")
    ended = seed.definition([seed.line("expense", wallet, "1")], name="舊訂閱", status="ended")
    assert generation.generate(db_session, paused, date(2026, 10, 3)) == 13
    assert generation.generate(db_session, ended, date(2026, 10, 3)) == 0
    assert ended.generated_until is None


def test_installment_last_period_gets_the_remainder(db_session, seed):
    # Spec "Card installment remainder on the last period" (override set at generation, whenever seq == times).
    card, bank = seed.account("範例卡"), seed.account("薪轉")
    phone = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"),
    )
    generation.generate(db_session, phone, date(2026, 10, 3))
    assert [row.amount_override for row in _instances(db_session, phone)] == [None, None, ["3334"]]

    loan = seed.definition(
        [seed.line("repayment", bank, "8333"), seed.line("interest", bank, "620")], kind="installment",
        name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    assert generation.generate(db_session, loan, date(2026, 10, 3)) == 12
    assert all(row.amount_override is None for row in _instances(db_session, loan))
    assert generation.generate(db_session, loan, date(2029, 1, 1)) == 24
    assert _instances(db_session, loan)[-1].amount_override == ["8345", "620"]

    imported = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", name="MOZE 分期", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"), created_locally=False, moze_id="I-1",
    )
    generation.generate(db_session, imported, date(2026, 10, 3))
    assert _instances(db_session, imported)[-1].amount_override is None


def test_definition_ends_when_its_last_period_exists_and_nothing_is_open(db_session, seed):
    wallet = seed.account()
    twice = seed.definition([seed.line("expense", wallet, "1")], times=2)
    generation.generate(db_session, twice, date(2026, 10, 3))
    assert generation.end_if_complete(db_session, twice) is False
    for row in _instances(db_session, twice):
        row.status, row.acted_at, row.acted_by = "skipped", POSTED_AT, "owner"
    db_session.flush()
    assert generation.end_if_complete(db_session, twice) is True
    assert twice.status == "ended"

    once = seed.definition([seed.line("expense", wallet, "1")], name="一次", times=1)
    entry = seed.entry(wallet, "-1", day=date(2026, 10, 22), source="schedule")
    seed.instance(once, 1, date(2026, 10, 22), status="posted", entries=[entry], is_partial=True)
    assert generation.end_if_complete(db_session, once) is False


def test_generate_locked_takes_the_shared_import_key(db_session, seed, pg_engine):
    wallet = seed.account()
    definition = seed.definition([seed.line("expense", wallet, "1")])
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            with pytest.raises(ImportRunningError):
                generation.generate_locked(db_session, definition.id, date(2026, 10, 3))
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    db_session.rollback()
    assert generation.generate_locked(db_session, definition.id, date(2026, 10, 3)) == 13
```

- [ ] 6.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_generation.py
```

Expected: `ImportError: cannot import name 'schedule_generation' from 'app.services'`.

- [ ] 6.3 Create `services/accounting-service/app/services/schedule_generation.py`:

```python
"""Instance generation (design D30): the next occurrences after the latest instance, up to a 13-month horizon.

Generation never deletes, moves or re-dates an existing instance, so an owner's date edit on one period cannot shift
later ones: it continues strictly after the latest instance's rule_date. Callers hold the definition lock.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from . import schedule_rules as rules
from .schedule_locks import lock_definition, take_import_key_shared
from .schedule_templates import template_amounts


def last_period_override(definition: ScheduleDefinition) -> list[str] | None:
    """A local installment with a total: the last period takes the first line's remainder (MOZE 分期餘額納入 末期) and
    the template amounts of the other lines, e.g. ["8345", "620"]. Imported installments carry MOZE's own amounts."""
    if (
        definition.kind != "installment"
        or definition.total_amount is None
        or definition.times is None
        or not definition.created_locally
    ):
        return None
    amounts = template_amounts(definition.template)
    last = rules.last_period_amount(Decimal(definition.total_amount), Decimal(amounts[0]), definition.times)
    return [rules.plain(last), *amounts[1:]]


def _latest(db: Session, definition_id: int) -> ScheduleInstance | None:
    return db.scalar(
        select(ScheduleInstance)
        .where(ScheduleInstance.definition_id == definition_id)
        .order_by(ScheduleInstance.rule_date.desc(), ScheduleInstance.seq.desc())
        .limit(1)
    )


def _max_seq(db: Session, definition_id: int) -> int | None:
    return db.scalar(select(func.max(ScheduleInstance.seq)).where(ScheduleInstance.definition_id == definition_id))


def _rule(definition: ScheduleDefinition) -> tuple:
    return definition.anchor_date, definition.interval_unit, definition.interval_n, definition.day_of_month


def generate(db: Session, definition: ScheduleDefinition, today: date) -> int:
    """Insert the next occurrences up to horizon(today); returns how many instances were created."""
    if definition.status == "ended" or definition.review_reason == "interval_mismatch":
        return 0  # an interval_mismatch import carries an invented rule (seqs by position): never roll it forward
    limit = rules.horizon(today)
    anchor, unit, n, day_of_month = _rule(definition)
    latest = _latest(db, definition.id)
    max_seq = _max_seq(db, definition.id)
    posted_days = set(
        db.scalars(
            select(ScheduleInstance.due_date).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "posted"
            )
        )
    )
    k = 0 if latest is None else rules.first_index_after(anchor, unit, n, day_of_month, latest.rule_date)
    seq = definition.first_seq if max_seq is None else max(max_seq + 1, definition.first_seq)
    last_override = last_period_override(definition)
    created = 0
    while definition.times is None or seq <= definition.times:
        day = rules.occurrence(anchor, unit, n, k, day_of_month)
        if (definition.end_date is not None and day > definition.end_date) or day > limit:
            break
        k += 1
        if day in posted_days:
            continue  # this date is already posted for the definition: no second period, no seq consumed
        instance = ScheduleInstance(definition_id=definition.id, seq=seq, rule_date=day, due_date=day)
        if last_override is not None and seq == definition.times:
            instance.amount_override = last_override
        db.add(instance)
        created += 1
        seq += 1
    definition.generated_until = limit
    db.flush()
    return created


def series_complete(db: Session, definition: ScheduleDefinition) -> bool:
    """The last occurrence (by times or end_date) exists."""
    latest = _latest(db, definition.id)
    if latest is None:
        return False
    if definition.times is not None and (_max_seq(db, definition.id) or 0) >= definition.times:
        return True
    if definition.end_date is not None:
        anchor, unit, n, day_of_month = _rule(definition)
        k = rules.first_index_after(anchor, unit, n, day_of_month, latest.rule_date)
        return rules.occurrence(anchor, unit, n, k, day_of_month) > definition.end_date
    return False


def end_if_complete(db: Session, definition: ScheduleDefinition) -> bool:
    """End a definition whose series is complete and which has nothing pending or partial; True when it ended now."""
    if definition.status == "ended" or not series_complete(db, definition):
        return False
    open_instance = db.scalar(
        select(ScheduleInstance.id)
        .where(
            ScheduleInstance.definition_id == definition.id,
            (ScheduleInstance.status == "pending") | ScheduleInstance.is_partial.is_(True),
        )
        .limit(1)
    )
    if open_instance is not None:
        return False
    definition.status = "ended"
    db.flush()
    return True


def generate_locked(db: Session, definition_id: int, today: date) -> int:
    """The job's per-definition step: shared import key → definition FOR UPDATE → generate → maybe end."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    created = generate(db, definition, today)
    end_if_complete(db, definition)
    return created
```

- [ ] 6.4 Run 6.2 again.

Expected: `12 passed`.

- [ ] 6.5 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_generation.py services/accounting-service/tests/integration/test_schedule_generation.py
git commit -m "feat(accounting): schedule instance generation with a 13-month horizon

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 7. Ledger services gain `source`, `remember` and the cutover bypass

**Model:** sonnet

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (`_apply`, `write_children`, `insert_prepared`, `update_entry`)
- Modify: `services/accounting-service/app/services/transfer_service.py` (`_leg_values`, `_write_leg_extras`, `create_transfer`, `update_transfer`)
- Modify: `services/accounting-service/app/services/settlement_service.py` (`settle`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (`EntrySource`)
- Test: `services/accounting-service/tests/integration/test_schedule_sources.py`

**Interfaces:**
- Consumes: the phase 2a services as they are on `main` after #44 (`settle` already refuses `is_closed` targets with 422 `is_closed`).
- Produces: `insert_prepared(db, prepared, *, group_id=None, remember=True, source="manual")`, `write_children(db, parent, fee, discount, *, source="manual")`, `create_transfer(db, payload, *, source="manual", remember=True)`, `settle(db, entry_id, payload, *, source="manual", check_cutover_lock=True, name=None)`; `EntrySource` includes `"schedule"`. Edits keep `source = 'schedule'` on a schedule row (`PUT /entries`, `PUT /transfers`).

Rules: defaults keep every current caller's behaviour unchanged. `check_cutover_lock=False` is used only by `schedule_posting` (D31/D36: posting against a `moze_backup` loan bypasses D19 for that target only). Schedule rows stay editable at all times (the edit lock only looks at MOZE sources and `moze_id`, which schedule rows never have).

- [ ] 7.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_sources.py`:

```python
"""Schedule-sourced writes (spec "Schedule-sourced entries"): source = 'schedule', no category defaults, D19 bypass."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry
from app.schemas.writes import ChildIn, EntryIn, SettleIn, TransferIn
from app.services import entry_write_service as ews
from app.services import settlement_service, transfer_service
from app.services.edit_lock import EditLockedError


def test_insert_prepared_with_schedule_source_does_not_remember_defaults(db_session, seed):
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    prepared = ews.prepare_entry(
        db_session,
        EntryIn(
            account_id=card.id, kind="expense", amount=Decimal("390"), entry_date=date(2026, 10, 22),
            category_id=streaming.id, fee=ChildIn(amount=Decimal("5")),
        ),
    )
    entry_id = ews.insert_prepared(db_session, prepared, remember=False, source="schedule")
    entry = db_session.get(LedgerEntry, entry_id)
    child = db_session.scalar(select(LedgerEntry).where(LedgerEntry.parent_entry_id == entry_id))
    assert (entry.source, child.source) == ("schedule", "schedule")
    db_session.refresh(streaming)
    assert streaming.default_account_id is None


def test_create_transfer_with_schedule_source(db_session, seed):
    pay, broker = seed.account("薪轉"), seed.account("交割")
    invest = seed.category("投資", kind="transfer_out")
    group = transfer_service.create_transfer(
        db_session,
        TransferIn(
            from_account_id=pay.id, to_account_id=broker.id, out_amount=Decimal("15000"), entry_date=date(2026, 10, 5),
            category_id=invest.id,
        ),
        source="schedule",
        remember=False,
    )
    out_leg, in_leg = transfer_service.transfer_legs(db_session, group)
    assert (out_leg.source, in_leg.source, out_leg.amount, in_leg.amount) == (
        "schedule", "schedule", Decimal("-15000"), Decimal("15000"),
    )
    db_session.refresh(invest)
    assert invest.default_account_id is None


def test_settle_bypasses_the_cutover_lock_only_when_asked(db_session, seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    loan = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, source="moze_backup", moze_id="R-LOAN")
    body = SettleIn(account_id=bank.id, amount=Decimal("8333"), entry_date=date(2026, 11, 9))
    with pytest.raises(EditLockedError):
        settlement_service.settle(db_session, loan.id, body)
    entry_id = settlement_service.settle(
        db_session, loan.id, body, source="schedule", check_cutover_lock=False, name="信貸 每月還款"
    )
    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.source, entry.name, entry.amount, entry.settles_entry_id, entry.counterparty_id) == (
        "schedule", "信貸 每月還款", Decimal("-8333"), loan.id, lender.id,
    )


def test_schedule_entry_editable_before_cutover_and_keeps_its_source(client, db_session, seed):
    # Spec "Schedule entry editable before cutover".
    card = seed.account("範例卡")
    entry = seed.entry(card, "-390", source="schedule", day=date(2026, 10, 22))
    db_session.commit()
    response = client.put(
        f"/entries/{entry.id}",
        json={"account_id": card.id, "kind": "expense", "amount": "390", "entry_date": "2026-10-22", "name": "Netflix 家庭"},
    )
    assert response.status_code == 200
    assert (response.json()["name"], response.json()["source"]) == ("Netflix 家庭", "schedule")
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry.id).source == "schedule"


def test_scheduled_transfer_update_keeps_its_source(client, db_session, seed):
    pay, broker = seed.account("薪轉"), seed.account("交割")
    group = transfer_service.create_transfer(
        db_session,
        TransferIn(from_account_id=pay.id, to_account_id=broker.id, out_amount=Decimal("15000"), entry_date=date(2026, 10, 5)),
        source="schedule",
        remember=False,
    )
    db_session.commit()
    response = client.put(
        f"/transfers/{group}",
        json={"from_account_id": pay.id, "to_account_id": broker.id, "out_amount": "16000", "entry_date": "2026-10-05"},
    )
    assert response.status_code == 200
    db_session.expire_all()
    legs = transfer_service.transfer_legs(db_session, group)
    assert [leg.source for leg in legs] == ["schedule", "schedule"]
    assert legs[0].amount == Decimal("-16000")
```

- [ ] 7.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_sources.py
```

Expected: 5 failures, e.g. `TypeError: insert_prepared() got an unexpected keyword argument 'source'`.

- [ ] 7.3 Edit `services/accounting-service/app/schemas/ledger.py`: replace `EntrySource = Literal["moze_import", "moze_backup", "manual", "hermes", "rule"]` with `EntrySource = Literal["moze_import", "moze_backup", "manual", "hermes", "rule", "schedule"]`.

- [ ] 7.4 Edit `services/accounting-service/app/services/entry_write_service.py`:
  - change the signature `def _apply(entry: LedgerEntry, prepared: PreparedEntry) -> None:` to `def _apply(entry: LedgerEntry, prepared: PreparedEntry, source: str = "manual") -> None:` and its last line `entry.source = "manual"` to `entry.source = source`;
  - replace `write_children` with

```python
def write_children(
    db: Session, parent: LedgerEntry, fee: ChildIn | None, discount: ChildIn | None, *, source: str = "manual"
) -> None:
    """Fee stored negative, discount positive; same account, date, time, posting date and source as the parent."""
    for kind, child in (("fee", fee), ("discount", discount)):
        if child is None:
            continue
        db.add(
            LedgerEntry(
                account_id=parent.account_id,
                currency=parent.currency,
                kind=kind,
                amount=-child.amount if kind == "fee" else child.amount,
                entry_date=parent.entry_date,
                entry_time=parent.entry_time,
                posted_date=parent.posted_date,
                category_id=system_category_id(db, kind),
                name=child.name or CHILD_DEFAULT_NAMES[kind],
                parent_entry_id=parent.id,
                source=source,
            )
        )
    db.flush()
```

  - replace `insert_prepared` with

```python
def insert_prepared(
    db: Session,
    prepared: PreparedEntry,
    *,
    group_id: int | None = None,
    remember: bool = True,
    source: str = "manual",
) -> int:
    """Insert one prepared entry with its children and rule links; `remember=False` leaves the category defaults
    alone (a split remembers them all at once; a schedule never moves them, D31). `source='schedule'` only from
    schedule_posting."""
    entry = LedgerEntry(group_id=group_id)
    _apply(entry, prepared, source)
    db.add(entry)
    db.flush()
    write_children(db, entry, prepared.payload.fee, prepared.payload.discount, source=source)
    write_rule_links(db, entry.id, prepared.payload.reward_rule_ids)
    if remember:
        remember_defaults(db, prepared.payload.category_id, prepared.account.id, prepared.payload.project_id)
    return entry.id
```

  - in `update_entry` replace the three lines

```python
    _apply(entry, prepared)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id))
    db.flush()
    write_children(db, entry, payload.fee, payload.discount)
```

  with

```python
    source = "schedule" if entry.source == "schedule" else "manual"  # a posted period's entry stays a schedule row
    _apply(entry, prepared, source)
    db.execute(delete(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id))
    db.flush()
    write_children(db, entry, payload.fee, payload.discount, source=source)
```

- [ ] 7.5 Edit `services/accounting-service/app/services/transfer_service.py`:
  - change `def _leg_values(db: Session, payload: TransferIn, attached_rules: frozenset[int] = frozenset()) -> tuple[dict, dict]:` to `def _leg_values(db: Session, payload: TransferIn, attached_rules: frozenset[int] = frozenset(), entry_source: str = "manual") -> tuple[dict, dict]:` and, inside it, `"source": "manual",` to `"source": entry_source,`. **Do not name this parameter `source`:** `_leg_values` already has a local `source = _account(db, payload.from_account_id, "from_account_id")` (the from-account, used by `source.id`, `source.currency` and `check_rules(..., source.id, ...)`), which would overwrite the parameter and write an `Account` object into the leg's `source` column. Leave that local and its uses unchanged;
  - replace `_write_leg_extras` and `create_transfer` with

```python
def _write_leg_extras(
    db: Session, out_leg: LedgerEntry, in_leg: LedgerEntry, payload: TransferIn, *, source: str = "manual",
    remember: bool = True,
) -> None:
    write_children(db, out_leg, payload.out_fee, payload.out_discount, source=source)
    write_children(db, in_leg, payload.in_fee, payload.in_discount, source=source)
    write_rule_links(db, out_leg.id, payload.reward_rule_ids)
    write_rule_links(db, in_leg.id, [])
    if remember:
        remember_defaults(db, payload.category_id, payload.from_account_id, payload.project_id)


def create_transfer(db: Session, payload: TransferIn, *, source: str = "manual", remember: bool = True) -> UUID:
    out_values, in_values = _leg_values(db, payload, entry_source=source)
    group_id = uuid4()
    out_leg = LedgerEntry(transfer_group_id=group_id, **out_values)
    in_leg = LedgerEntry(transfer_group_id=group_id, **in_values)
    db.add_all([out_leg, in_leg])
    db.flush()
    _write_leg_extras(db, out_leg, in_leg, payload, source=source, remember=remember)
    return group_id
```

  - in `update_transfer` replace

```python
    out_values, in_values = _leg_values(db, payload, frozenset(attached_rule_ids(db, out_leg.id)))
```

  with

```python
    source = "schedule" if out_leg.source == "schedule" else "manual"
    out_values, in_values = _leg_values(
        db, payload, frozenset(attached_rule_ids(db, out_leg.id)), entry_source=source
    )
```

  and its last line `_write_leg_extras(db, out_leg, in_leg, payload)` with `_write_leg_extras(db, out_leg, in_leg, payload, source=source)`.

- [ ] 7.6 Edit `services/accounting-service/app/services/settlement_service.py`: replace the signature and the first two body lines of `settle`

```python
def settle(db: Session, entry_id: int, payload: SettleIn) -> int:
```

```python
    target = locked_entry(db, entry_id)
    assert_entry_editable(db, target)
```

  with

```python
def settle(
    db: Session,
    entry_id: int,
    payload: SettleIn,
    *,
    source: str = "manual",
    check_cutover_lock: bool = True,
    name: str | None = None,
) -> int:
```

```python
    target = locked_entry(db, entry_id)
    if check_cutover_lock:  # False only from schedule_posting: a schedule may repay an imported loan (D36)
        assert_entry_editable(db, target)
```

  and in the `LedgerEntry(...)` it builds replace `name=SETTLE_NAMES[target.kind],` with `name=name or SETTLE_NAMES[target.kind],` and `source="manual",` with `source=source,`.

- [ ] 7.7 Run 7.2 again, then the suites the edit touches.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_sources.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_entry_writes.py tests/integration/test_transfers.py tests/integration/test_splits.py tests/integration/test_settlements.py tests/integration/test_edit_lock.py 2>&1 | tail -1
```

Expected: `5 passed`; then the second run reports exactly `B_ledger7 passed` (recorded in 1.6; this task adds no test to those files), 0 failed.

- [ ] 7.8 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/entry_write_service.py services/accounting-service/app/services/transfer_service.py \
  services/accounting-service/app/services/settlement_service.py services/accounting-service/app/schemas/ledger.py \
  services/accounting-service/tests/integration/test_schedule_sources.py
git commit -m "feat(accounting): ledger writes take a source; schedule rows stay schedule rows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 8. `schedule_posting`

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_posting.py`
- Test: `services/accounting-service/tests/integration/test_schedule_posting.py`

**Interfaces:**
- Consumes: Task 5 (`take_import_key_shared`, `get_instance`, `lock_definition`, `lock_instance`, `validate_template`, `resolved_amounts`, `loan_line`, `LOAN_LINE_KINDS`); Task 7 (`insert_prepared(..., source=)`, `create_transfer(..., source=, remember=)`, `settle(..., source=, check_cutover_lock=, name=)`); `entry_write_service.prepare_entry`, `locked_entry`, `lock_group`, `assert_entry_editable`, `delete_entries_cascade`, `system_category_id`; `transfer_service.transfer_legs`; `settlement_service.open_amount`; `ledger_service._today`.
- Produces: `PostResult`, `auto_eligible`, `lock_definition_for_post`, `lock_later_pending`, `post_instance`, `post_locked`, `failure_message`, `record_failure`, `delete_period_entries`, constants `SOURCE = "schedule"`, `LOAN_CLOSED_NOTE = "貸款已結清"`.

Rules (D31, spec "Posting an instance"):
- `post_instance` = shared import key → definition via `lock_definition_for_post` (`FOR SHARE`, but `FOR UPDATE` when the template has a loan line, because that post may end the definition in `_close_out`; two `FOR SHARE` holders that both upgrade would deadlock) → instance `FOR UPDATE` (`SKIP LOCKED` when `job=True`; a locked row gives `skipped_locked`) → 409 `already_posted` / `skipped` unless `pending` → for the job, the eligibility of D34 is re-checked under the lock (`not_due`) → `post_locked`.
- `post_locked`: re-validate the template; resolve amounts; for a loan line first lock the definition's later pending instances (`lock_later_pending`: `seq > instance.seq`, ascending id) — **before** the loan entry, because no path that holds an entry lock may lock a schedule row (D32) — then lock the loan entry (`locked_entry`, the ledger's "target entries" step; a caller that already locked it in its single entry statement passes it as `locked_loan`) — closed or open 0 → this and every later pending instance `skipped` (`acted_by` = actor, note `貸款已結清`), definition `ended`, nothing written; otherwise clamp the repayment to the open amount (interest never). Lines with amount 0 are not written. Each line goes through the ledger services with `entry_date = posted_date = due_date`, `entry_time = NULL`, `source = 'schedule'`, `remember = False`, name `line.name or definition.name`, the template's description and tags. Two or more non-transfer entries share a new `entry_group` (`installment` for an installment definition, else `split`) named `<name> #k/N` (`#k` without `times`). The instance becomes `posted` with every top-level id written.
- A `ValidationError` raised by a ledger service while writing line i is re-raised as `lines[i].<field>` with the message `無法入帳` (service messages may contain amounts; `last_error` never does).
- Callers own the transaction. `record_failure` rolls back, writes `last_error` / `last_error_at` on a still-pending instance and commits (the "second short transaction").

- [ ] 8.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_posting.py`:

```python
"""Posting an instance (spec "Posting an instance", design D31)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import EntryGroup, LedgerEntry, ScheduleInstance
from app.services import entry_write_service as ews
from app.services import ledger_service, settlement_service
from app.services import schedule_locks as locks
from app.services import schedule_posting as posting
from app.services.edit_lock import EditLockedError
from app.services.errors import ConflictError, ValidationError


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    return SimpleNamespace(bank=bank, lender=lender, payable=payable, definition=definition)


def _balance(db, account) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account.id)


def _open(db, entry) -> Decimal:
    db.expire_all()
    return settlement_service.open_amount(db, db.get(LedgerEntry, entry.id))


def _schedule_entries(db) -> int:
    return db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule"))


def test_repayment_with_interest(db_session, seed, loan, today):
    # Spec "Repayment with interest" and the ledger scenario "Loan interest names the lender".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    before = _balance(db_session, loan.bank)

    result = posting.post_instance(db_session, instance.id, actor="owner")
    db_session.commit()

    assert result.outcome == "posted"
    assert _balance(db_session, loan.bank) - before == Decimal("-8953")
    repayment, interest = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (repayment.kind, repayment.amount, repayment.settles_entry_id, repayment.is_settlement) == (
        "payable", Decimal("-8333"), loan.payable.id, True,
    )
    assert (repayment.entry_date, repayment.posted_date, repayment.entry_time) == (date(2026, 11, 9), date(2026, 11, 9), None)
    assert (interest.kind, interest.amount, interest.counterparty_id) == ("interest", Decimal("-620"), loan.lender.id)
    assert interest.category_id == ews.system_category_id(db_session, "interest")
    assert (repayment.source, interest.source) == ("schedule", "schedule")
    group = db_session.get(EntryGroup, repayment.group_id)
    assert (group.kind, group.name, interest.group_id) == ("installment", "信貸 每月還款 #1/36", group.id)
    assert _open(db_session, loan.payable) == Decimal("291667")
    db_session.refresh(instance)
    assert (instance.status, instance.acted_by, instance.posted_entry_ids, instance.is_partial) == (
        "posted", "owner", list(result.entry_ids), False,
    )


def test_last_repayment_clamped_to_the_open_amount(db_session, seed, loan, today):
    # Spec "Last repayment clamped to the open amount".
    for amount in ("-291655", "-100"):
        seed.entry(
            loan.bank, amount, kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
            is_settlement=True,
        )
    assert _open(db_session, loan.payable) == Decimal("8245")
    today(date(2029, 10, 9))
    instance = seed.instance(loan.definition, 36, date(2029, 10, 9), amount_override=["8345", "620"])

    result = posting.post_instance(db_session, instance.id, actor="auto")

    repayment, interest = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (repayment.amount, interest.amount) == (Decimal("-8245"), Decimal("-620"))
    assert _open(db_session, loan.payable) == 0


def test_posting_twice_is_refused(db_session, seed, loan, today):
    # Spec "Posting twice is refused".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    posting.post_instance(db_session, instance.id, actor="owner")
    db_session.commit()
    written = _schedule_entries(db_session)
    with pytest.raises(ConflictError, match="already_posted"):
        posting.post_instance(db_session, instance.id, actor="owner")
    db_session.rollback()
    assert _schedule_entries(db_session) == written


def test_closed_loan_ends_the_schedule(db_session, seed, loan, today):
    # Spec "Closed loan ends the schedule".
    loan.payable.is_closed = True
    days = [date(2026, 12, 9)] + [date(2027, month, 9) for month in range(1, 11)]
    instances = [seed.instance(loan.definition, seq, day) for seq, day in enumerate(days, start=2)]
    today(date(2026, 12, 9))

    result = posting.post_instance(db_session, instances[0].id, actor="auto", job=True, today=date(2026, 12, 9))
    db_session.commit()

    assert result.outcome == "loan_closed"
    assert _schedule_entries(db_session) == 0
    db_session.expire_all()
    for instance in instances:
        row = db_session.get(ScheduleInstance, instance.id)
        assert (row.status, row.acted_by, row.note) == ("skipped", "auto", "貸款已結清")
    assert loan.definition.status == "ended"


def test_loan_settled_by_hand_while_pending_skips_the_rest_and_ends(db_session, seed, loan, today):
    # Review Focus 4: the owner repaid the rest by hand while the next period waited.
    seed.entry(
        loan.bank, "-300000", kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
        is_settlement=True,
    )
    first = seed.instance(loan.definition, 1, date(2026, 11, 9))
    second = seed.instance(loan.definition, 2, date(2026, 12, 9))
    today(date(2026, 11, 9))

    result = posting.post_instance(db_session, first.id, actor="owner")
    db_session.commit()

    assert result.outcome == "loan_closed"
    db_session.expire_all()
    assert [db_session.get(ScheduleInstance, row.id).status for row in (first, second)] == ["skipped", "skipped"]
    assert db_session.get(ScheduleInstance, first.id).acted_by == "owner"
    assert loan.definition.status == "ended"
    assert _schedule_entries(db_session) == 0


def test_archived_account_found_at_posting_time_names_the_line(db_session, seed, today):
    # Spec "Archived account found at posting time" (the reopen half is in Task 12).
    card, old = seed.account("範例卡"), seed.account("舊帳戶")
    definition = seed.definition([seed.line("expense", card, "390"), seed.line("expense", old, "620")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    old.is_archived = True
    db_session.commit()
    today(date(2026, 10, 22))

    with pytest.raises(ValidationError) as exc:
        posting.post_instance(db_session, instance.id, actor="owner")
    assert exc.value.field == "lines[1].account_id"
    posting.record_failure(db_session, instance.id, posting.failure_message(exc.value))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert row.status == "pending"
    assert row.last_error.startswith("lines[1].account_id")
    assert "620" not in row.last_error and row.last_error_at is not None
    assert _schedule_entries(db_session) == 0


def test_zero_override_leaves_a_line_out(db_session, seed, loan, today):
    # Spec "Zero override leaves a line out".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9), amount_override=["8333", "0"])
    result = posting.post_instance(db_session, instance.id, actor="owner")
    assert len(result.entry_ids) == 1
    repayment = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (repayment.amount, repayment.group_id) == (Decimal("-8333"), None)


def test_recurring_transfer(db_session, seed, today):
    # Spec "Recurring transfer".
    pay, broker = seed.account("薪轉"), seed.account("交割")
    definition = seed.definition(
        [seed.line("transfer", pay, "15000", to_account_id=broker.id)], name="定期轉交割", anchor=date(2026, 10, 5)
    )
    instance = seed.instance(definition, 1, date(2026, 10, 5))
    today(date(2026, 10, 5))

    result = posting.post_instance(db_session, instance.id, actor="auto")

    out_leg, in_leg = (db_session.get(LedgerEntry, entry_id) for entry_id in result.entry_ids)
    assert (out_leg.kind, out_leg.amount, in_leg.kind, in_leg.amount) == (
        "transfer_out", Decimal("-15000"), "transfer_in", Decimal("15000"),
    )
    assert out_leg.transfer_group_id == in_leg.transfer_group_id is not None
    assert out_leg.entry_date == in_leg.entry_date == date(2026, 10, 5)
    assert out_leg.group_id is None


def test_expense_line_takes_the_definition_name_and_never_moves_category_defaults(db_session, seed, today):
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    definition = seed.definition(
        [seed.line("expense", card, "390", category_id=streaming.id)], description="家庭方案", tags=["訂閱"]
    )
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    today(date(2026, 10, 22))

    result = posting.post_instance(db_session, instance.id, actor="auto")

    entry = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (entry.name, entry.description, entry.tags, entry.amount, entry.source) == (
        "Netflix", "家庭方案", ["訂閱"], Decimal("-390"), "schedule",
    )
    db_session.refresh(streaming)
    assert streaming.default_account_id is None


def test_imported_period_posts_against_an_imported_loan(db_session, seed, today, monkeypatch):
    # Spec "Imported period posts against an imported loan".
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, source="moze_backup", moze_id="R-LOAN")
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸",
        anchor=date(2026, 2, 9), times=36, created_locally=False, moze_id="I-1",
    )
    instance = seed.instance(definition, 9, date(2026, 10, 9), moze_id="R-9")
    today(date(2026, 10, 9))

    result = posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9))

    settlement = db_session.get(LedgerEntry, result.entry_ids[0])
    assert (settlement.source, settlement.settles_entry_id, settlement.amount) == ("schedule", payable.id, Decimal("-8333"))


def test_the_job_leaves_an_instance_the_owner_is_acting_on(db_session, seed, pg_engine):
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    owner, job = factory(), factory()
    try:
        assert locks.lock_instance(owner, instance.id) is not None
        result = posting.post_instance(job, instance.id, actor="auto", job=True, today=date(2026, 10, 22))
        assert result.outcome == "skipped_locked"
    finally:
        owner.rollback()
        job.rollback()
        owner.close()
        job.close()


def test_the_job_rechecks_eligibility_under_the_lock(db_session, seed):
    card = seed.account("範例卡")
    auto = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 10, 3))
    confirm = seed.definition([seed.line("expense", card, "390")], name="房租", posting_mode="confirm")
    backlog = seed.instance(auto, 1, date(2026, 10, 1))
    reopened = seed.instance(auto, 2, date(2026, 10, 3), reopened_at=locks_now())
    confirmed = seed.instance(confirm, 1, date(2026, 10, 3))
    for instance in (backlog, reopened, confirmed):
        result = posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 3))
        assert result.outcome == "not_due"
    assert _schedule_entries(db_session) == 0


def test_delete_period_entries_takes_both_legs_empties_the_group_and_respects_the_cutover_lock(db_session, seed, loan, today):
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="owner")
    group_id = db_session.get(LedgerEntry, result.entry_ids[0]).group_id
    posting.delete_period_entries(db_session, list(result.entry_ids))
    assert _schedule_entries(db_session) == 0
    assert db_session.get(EntryGroup, group_id) is None

    imported = seed.entry(loan.bank, "-390", source="moze_backup", moze_id="R-X")
    with pytest.raises(EditLockedError):
        posting.delete_period_entries(db_session, [imported.id])


def test_delete_period_entries_locks_the_loan_in_the_same_statement_and_keeps_it(db_session, seed, loan, today):
    # Repost: the loan joins the period's single entry-lock statement and is handed back, never deleted.
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="owner")

    kept = posting.delete_period_entries(db_session, list(result.entry_ids), also_lock=loan.payable.id)

    assert kept is not None and kept.id == loan.payable.id
    assert _schedule_entries(db_session) == 0
    assert db_session.get(LedgerEntry, loan.payable.id) is not None
    assert _open(db_session, loan.payable) == Decimal("300000")


def test_posting_a_loan_period_holds_the_definition_for_update_and_locks_later_periods(db_session, seed, loan, pg_engine):
    # D32: a loan post may end the definition, so it takes FOR UPDATE (not FOR SHARE), and it locks the later
    # pending periods before the loan entry.
    first = seed.instance(loan.definition, 1, date(2026, 11, 9))
    second = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    owner, probe = factory(), factory()
    try:
        definition = posting.lock_definition_for_post(owner, loan.definition.id)
        instance = locks.lock_instance(owner, first.id)
        posting.post_locked(owner, definition, instance, "owner")
        probe.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            probe.execute(
                text("SELECT id FROM schedule_definition WHERE id = :id FOR SHARE"), {"id": loan.definition.id}
            )
        probe.rollback()
        probe.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            probe.execute(text("SELECT id FROM schedule_instance WHERE id = :id FOR UPDATE"), {"id": second.id})
    finally:
        owner.rollback()
        probe.rollback()
        owner.close()
        probe.close()


def locks_now():
    from datetime import datetime, timezone

    return datetime(2026, 10, 2, tzinfo=timezone.utc)
```

- [ ] 8.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_posting.py
```

Expected: `ImportError: cannot import name 'schedule_posting' from 'app.services'`.

- [ ] 8.3 Create `services/accounting-service/app/services/schedule_posting.py`:

```python
"""Posting one schedule instance (design D31): one transaction, the existing ledger services, source = 'schedule'.

Lock order (D32): shared import key → definition via lock_definition_for_post (FOR SHARE; FOR UPDATE with a loan
line) → instance FOR UPDATE (SKIP LOCKED for the job) → later pending instances (loan) → the ledger's order (the loan
entry via locked_entry is the "target entries" step; new rows take no further locks).
Callers own the transaction; record_failure is the "second short transaction" that stores last_error.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..models import Account, EntryGroup, LedgerEntry, ScheduleDefinition, ScheduleInstance
from ..schemas.writes import EntryIn, SettleIn, TransferIn
from . import entry_write_service, ledger_service, settlement_service, transfer_service
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import get_instance, lock_definition, lock_instance, take_import_key_shared
from .schedule_templates import LOAN_LINE_KINDS, loan_line, resolved_amounts, validate_template

SOURCE = "schedule"
LOAN_CLOSED_NOTE = "貸款已結清"
ENTRY_LINE_KINDS = ("expense", "income", "receivable", "payable")
MAX_ERROR_LENGTH = 500
GROUP_NAME_LENGTH = 128


@dataclass(frozen=True)
class PostResult:
    instance_id: int
    outcome: str  # posted | loan_closed | skipped_locked | not_due
    entry_ids: tuple[int, ...] = ()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def auto_eligible(definition: ScheduleDefinition, instance: ScheduleInstance, today: date) -> bool:
    """D34: what the job may post. Reopened, confirm, paused, ended and pre-auto_post_from periods wait for the owner."""
    return (
        instance.status == "pending"
        and instance.reopened_at is None
        and definition.auto_post_from <= instance.due_date <= today
        and definition.status == "active"
        and definition.posting_mode == "auto"
    )


def lock_definition_for_post(db: Session, definition_id: int) -> ScheduleDefinition:
    """FOR SHARE, or FOR UPDATE when the template has a loan line: such a post may end the definition
    (_close_out), and two FOR SHARE holders that both upgrade deadlock (D32)."""
    peek = db.get(ScheduleDefinition, definition_id)
    if peek is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    share = loan_line(peek.template) is None
    definition = lock_definition(db, definition_id, share=share)
    if share and loan_line(definition.template) is not None:
        raise ConflictError("definition_changed")  # the template gained a loan line meanwhile; the caller retries
    return definition


def lock_later_pending(
    db: Session, definition: ScheduleDefinition, instance: ScheduleInstance
) -> list[ScheduleInstance]:
    """Lock the definition's pending instances after this one (ascending id). Called before any entry lock, so a
    loan close-out never locks a schedule row while it holds the loan entry (D32)."""
    return list(
        db.scalars(
            select(ScheduleInstance)
            .where(
                ScheduleInstance.definition_id == definition.id,
                ScheduleInstance.status == "pending",
                ScheduleInstance.seq > instance.seq,
            )
            .order_by(ScheduleInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def post_instance(
    db: Session, instance_id: int, *, actor: str, job: bool = False, today: date | None = None
) -> PostResult:
    take_import_key_shared(db)
    definition = lock_definition_for_post(db, get_instance(db, instance_id).definition_id)
    instance = lock_instance(db, instance_id, skip_locked=job)
    if instance is None:
        if job:
            return PostResult(instance_id, "skipped_locked")
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "pending":
        raise ConflictError("already_posted" if instance.status == "posted" else "skipped")
    if job and not auto_eligible(definition, instance, today or ledger_service._today()):
        return PostResult(instance_id, "not_due")
    return post_locked(db, definition, instance, actor)


def _close_out(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    later: list[ScheduleInstance],
    actor: str,
) -> None:
    """The loan is closed or fully repaid: this and every later pending period are skipped; the definition ends.
    `later` was locked by lock_later_pending before the loan entry; the definition is held FOR UPDATE
    (lock_definition_for_post). No schedule row is locked here."""
    now = _now()
    for item in [instance, *later]:
        item.status, item.acted_at, item.acted_by = "skipped", now, actor
        item.note, item.last_error, item.last_error_at = LOAN_CLOSED_NOTE, None, None
    definition.status = "ended"
    db.flush()


def _write_line(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    line: dict,
    amount: Decimal,
    loan: LedgerEntry | None,
    open_amount: Decimal,
) -> tuple[list[int], bool]:
    """Write one line; returns (top-level entry ids, whether they join the period's group)."""
    template = definition.template
    due = instance.due_date
    name = line["name"] or definition.name
    description, tags = template.get("description"), list(template.get("tags") or [])
    kind = line["kind"]
    if kind in ENTRY_LINE_KINDS:
        payload = EntryIn(
            account_id=line["account_id"], kind=kind, amount=amount, entry_date=due, entry_time=None, posted_date=due,
            category_id=line["category_id"], project_id=line["project_id"], name=name, merchant=line["merchant"],
            counterparty_id=line["counterparty_id"] if kind in ("receivable", "payable") else None,
            description=description, tags=tags,
        )
        prepared = entry_write_service.prepare_entry(db, payload)
        return [entry_write_service.insert_prepared(db, prepared, remember=False, source=SOURCE)], True
    if kind == "transfer":
        payload = TransferIn(
            from_account_id=line["account_id"], to_account_id=line["to_account_id"], out_amount=amount,
            in_amount=Decimal(line["to_amount"]) if line["to_amount"] is not None else None, entry_date=due,
            entry_time=None, posted_date=due, category_id=line["category_id"], name=name, merchant=line["merchant"],
            description=description, project_id=line["project_id"], tags=tags,
        )
        group = transfer_service.create_transfer(db, payload, source=SOURCE, remember=False)
        out_leg, in_leg = transfer_service.transfer_legs(db, group)
        return [out_leg.id, in_leg.id], False
    if kind in LOAN_LINE_KINDS:
        value = min(amount, open_amount)  # the last period absorbs rounding and hand repayments (D35, D38)
        entry_id = settlement_service.settle(
            db, loan.id, SettleIn(account_id=line["account_id"], amount=value, entry_date=due, description=description),
            source=SOURCE, check_cutover_lock=False, name=name,
        )
        entry = db.get(LedgerEntry, entry_id)
        entry.category_id, entry.project_id = line["category_id"], line["project_id"]
        entry.merchant, entry.tags = line["merchant"], tags
        db.flush()
        return [entry_id], True
    account = db.get(Account, line["account_id"])
    entry = LedgerEntry(
        account_id=account.id, currency=account.currency, kind="interest", amount=-amount, entry_date=due,
        entry_time=None, posted_date=due,
        category_id=line["category_id"] or entry_write_service.system_category_id(db, "interest"),
        project_id=line["project_id"], name=name, merchant=line["merchant"],
        counterparty_id=loan.counterparty_id if loan is not None else None,  # interest names the lender (D29)
        description=description, tags=tags, source=SOURCE,
    )
    db.add(entry)
    db.flush()
    return [entry.id], True


def _group(db: Session, definition: ScheduleDefinition, instance: ScheduleInstance, entry_ids: list[int]) -> None:
    label = f"#{instance.seq}/{definition.times}" if definition.times is not None else f"#{instance.seq}"
    group = EntryGroup(
        kind="installment" if definition.kind == "installment" else "split",
        name=f"{definition.name} {label}"[:GROUP_NAME_LENGTH],
    )
    db.add(group)
    db.flush()
    db.execute(
        update(LedgerEntry)
        .where(LedgerEntry.id.in_(entry_ids))
        .values(group_id=group.id)
        .execution_options(synchronize_session=False)
    )
    db.flush()


def post_locked(
    db: Session,
    definition: ScheduleDefinition,
    instance: ScheduleInstance,
    actor: str,
    *,
    locked_loan: LedgerEntry | None = None,
) -> PostResult:
    """Write the period; the caller holds the definition (lock_definition_for_post) and instance locks and owns the
    transaction. `locked_loan`: the loan entry the caller already locked in its single entry statement (repost);
    such a caller has called lock_later_pending before taking any entry lock."""
    template = definition.template
    validate_template(db, template)
    amounts = resolved_amounts(definition, instance)
    loan, open_amount = None, Decimal(0)
    reference = loan_line(template)
    if reference is not None:
        index, line = reference
        if locked_loan is not None and locked_loan.id == line["loan_entry_id"]:
            later = list(
                db.scalars(  # already locked by the caller (lock_later_pending); plain read, no schedule lock here
                    select(ScheduleInstance)
                    .where(
                        ScheduleInstance.definition_id == definition.id,
                        ScheduleInstance.status == "pending",
                        ScheduleInstance.seq > instance.seq,
                    )
                    .order_by(ScheduleInstance.id)
                )
            )
            loan = locked_loan
        else:
            later = lock_later_pending(db, definition, instance)  # schedule rows first, then the loan entry (D32)
            try:
                loan = entry_write_service.locked_entry(db, line["loan_entry_id"])
            except NotFoundError as exc:
                raise ValidationError(f"lines[{index}].loan_entry_id", "貸款記錄不存在") from exc
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(db, loan)
        if open_amount == 0:
            _close_out(db, definition, instance, later, actor)
            return PostResult(instance.id, "loan_closed")

    written: list[int] = []
    grouped: list[int] = []
    for index, (line, amount) in enumerate(zip(template["lines"], amounts)):
        if amount == 0:
            continue
        try:
            ids, groupable = _write_line(db, definition, instance, line, amount, loan, open_amount)
        except ValidationError as exc:
            if exc.field.startswith("lines["):
                raise
            raise ValidationError(f"lines[{index}].{exc.field}", "無法入帳") from exc
        written += ids
        if groupable:
            grouped += ids
    if not written:
        raise ValidationError("amounts", "這一期沒有要入帳的金額")
    if len(grouped) >= 2:
        _group(db, definition, instance, grouped)

    instance.status, instance.posted_entry_ids = "posted", written
    instance.acted_at, instance.acted_by, instance.is_partial = _now(), actor, False
    instance.last_error, instance.last_error_at, instance.reopened_at = None, None, None
    db.flush()
    return PostResult(instance.id, "posted", tuple(written))


def failure_message(exc: Exception) -> str:
    """last_error text: the field and the amount-free message; other errors by class name only."""
    if isinstance(exc, ValidationError):
        return f"{exc.field}: {exc.message}"[:MAX_ERROR_LENGTH]
    return exc.__class__.__name__


def record_failure(db: Session, instance_id: int, message: str) -> None:
    """Roll the failed posting back, then store last_error on the still-pending instance in its own transaction."""
    db.rollback()
    db.execute(
        update(ScheduleInstance)
        .where(ScheduleInstance.id == instance_id, ScheduleInstance.status == "pending")
        .values(last_error=message[:MAX_ERROR_LENGTH], last_error_at=_now())
        .execution_options(synchronize_session=False)
    )
    db.commit()


def delete_period_entries(
    db: Session, entry_ids: list[int], *, also_lock: int | None = None
) -> LedgerEntry | None:
    """Delete a period's entries (reopen, repost) in the ledger's lock order: their entry_group rows ascending →
    the entries with their transfer legs in one statement. The cutover lock applies to every row (a MOZE-booked
    period's entries are MOZE rows until cutover). `also_lock`: an entry (the loan of a repost) locked in that same
    single statement and not deleted; it is returned so post_locked reuses the row instead of a second entry-lock
    statement."""
    ids = sorted(set(entry_ids))
    if not ids and also_lock is None:
        return None
    transfer_groups = {
        row for row in db.scalars(select(LedgerEntry.transfer_group_id).where(LedgerEntry.id.in_(ids))) if row is not None
    }
    condition = LedgerEntry.id.in_(ids)
    if transfer_groups:
        condition = or_(
            condition,
            and_(LedgerEntry.transfer_group_id.in_(transfer_groups), LedgerEntry.parent_entry_id.is_(None)),
        )
    group_ids = sorted(
        {row for row in db.scalars(select(LedgerEntry.group_id).where(condition)) if row is not None}
    )
    for group_id in group_ids:
        entry_write_service.lock_group(db, group_id)
    lock_condition = condition if also_lock is None else or_(condition, LedgerEntry.id == also_lock)
    locked = list(
        db.scalars(
            select(LedgerEntry)
            .where(lock_condition)
            .order_by(LedgerEntry.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    extra = next((entry for entry in locked if entry.id == also_lock), None)
    doomed = [entry for entry in locked if entry.id != also_lock]
    for entry in doomed:
        entry_write_service.assert_entry_editable(db, entry)
    if doomed:
        entry_write_service.delete_entries_cascade(db, [entry.id for entry in doomed])
    for group_id in group_ids:
        remaining = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.group_id == group_id))
        if remaining == 0:
            db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    db.flush()
    return extra
```

- [ ] 8.4 Run 8.2 again.

Expected: `15 passed`.

- [ ] 8.5 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_posting.py services/accounting-service/tests/integration/test_schedule_posting.py
git commit -m "feat(accounting): post a schedule instance through the ledger services

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 9. `schedule_read`: shapes, queue, loan summary

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_read.py`
- Test: `services/accounting-service/tests/integration/test_schedule_read.py`

**Interfaces:**
- Consumes: models; `resolved_amounts`, `loan_line`, `LEDGER_KINDS` (Task 5); `settlement_service.open_amount`; `edit_lock.import_locked`; `ledger_service._today`.
- Produces: `signed_line_amount`, `definition_out`, `list_definitions`, `get_definition`, `instance_out`, `list_instances`, `loan_summary`, `schedule_links`, `loan_schedule_for` with the dict shapes of `DefinitionOut`, `DefinitionDetailOut`, `InstanceOut`, `EntryScheduleOut`, `LoanScheduleOut` (Task 5 schemas).

Rules:
- Signed line amounts: `income`, `payable`, `collection` positive; every other kind (`expense`, `receivable`, `repayment`, `interest`, `transfer` — the money leaving the paying account) negative. `totals` and `next_amount` are signed sums per currency, sorted by currency; lines with amount 0 are left out.
- Queue (`queue=True`, definitions `active` only): pending instances due ≤ `until` (default today + 30) that belong to a `confirm` definition, or are reopened, or carry `last_error`, or are due before `auto_post_from`; plus every posted `is_partial` instance whatever its date. Order `due_date, definition_id, seq`. Without `queue`: `status` (default `pending`; `None` = all), `due_date ≤ until`, `due_date ≥ from`, `definition_id`.
- Loan summary (spec): loan definitions → `remaining` = open amount signed like the loan (payable negative, 0 when closed) and `repaid` = Σ |amount| of entries settling it; non-loan installment with a total → `total_amount − Σ |amount|` of the first matching entry (the first line's account and ledger kind) of each posted instance, `repaid` null; others null.
- `needs_check` = `review_reason` set, or an `ended` loan definition whose loan is still open.
- `locked` = imported (`moze_id` set) and `ACCOUNTING_IMPORT_LOCKED` not true.

- [ ] 9.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_read.py`:

```python
"""Response shapes (spec "Instance endpoints" queue, "Loan summary", "Schedule links on entries")."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services import schedule_read as read
from tests.helpers import POSTED_AT

REOPENED = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _ids(items) -> list[int]:
    return [item["id"] for item in items]


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_queue_lists_confirm_failing_and_overdue_items(db_session, seed, card, today):
    # Spec "待完成交易 queue".
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", times=12)
    netflix = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 9, 1))
    paused = seed.definition([seed.line("expense", card, "100")], name="健身房", posting_mode="confirm", status="paused")
    overdue = seed.instance(rent, 4, date(2026, 9, 30))
    later = seed.instance(rent, 5, date(2026, 10, 20))
    seed.instance(netflix, 1, date(2026, 10, 5))
    failing = seed.instance(netflix, 2, date(2026, 10, 1), last_error="lines[0].account_id: 帳戶已封存")
    seed.instance(paused, 1, date(2026, 10, 2))

    items = read.list_instances(db_session, queue=True)

    assert _ids(items) == [overdue.id, failing.id, later.id]
    assert [item["overdue_days"] for item in items] == [3, 2, 0]
    first = items[0]
    assert (first["definition_name"], first["seq"], first["times"], first["posting_mode"]) == ("房租", 4, 12, "confirm")
    assert [(line["account_name"], line["amount"]) for line in first["lines"]] == [("範例卡", Decimal("-18000"))]
    assert first["totals"] == [{"currency": "TWD", "amount": Decimal("-18000")}]


def test_queue_adds_backlog_reopened_and_partial_items(db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], auto_post_from=date(2026, 10, 3))
    ended = seed.definition([seed.line("expense", card, "1")], name="舊", status="ended")
    backlog = seed.instance(netflix, 1, date(2026, 9, 22))
    reopened = seed.instance(netflix, 2, date(2026, 10, 3), reopened_at=REOPENED)
    entry = seed.entry(card, "-390", day=date(2026, 12, 22), source="schedule")
    partial = seed.instance(netflix, 4, date(2026, 12, 22), status="posted", entries=[entry], is_partial=True)
    old_entry = seed.entry(card, "-1", day=date(2026, 9, 1), source="schedule")
    seed.instance(ended, 1, date(2026, 9, 1), status="posted", entries=[old_entry], is_partial=True)
    seed.instance(netflix, 3, date(2026, 11, 22))  # auto, after auto_post_from: the job's, not the queue's

    items = read.list_instances(db_session, queue=True)

    assert _ids(items) == [backlog.id, reopened.id, partial.id]
    assert items[1]["reopened"] is True
    assert (items[2]["status"], items[2]["is_partial"], items[2]["overdue_days"]) == ("posted", True, 0)


def test_instance_lines_are_signed_with_totals_per_currency(db_session, seed, card, today):
    today(date(2026, 10, 3))
    pay, broker, yen = seed.account("薪轉"), seed.account("交割"), seed.account("日幣", currency="JPY")
    definition = seed.definition(
        [
            seed.line("expense", card, "390"),
            seed.line("transfer", pay, "15000", to_account_id=broker.id),
            seed.line("income", yen, "5000"),
            seed.line("expense", card, "50"),
        ]
    )
    instance = seed.instance(definition, 1, date(2026, 10, 5), amount_override=["390", "15000", "5000", "0"])

    out = read.instance_out(db_session, instance)

    assert [(line["kind"], line["amount"], line["currency"]) for line in out["lines"]] == [
        ("expense", Decimal("-390"), "TWD"), ("transfer", Decimal("-15000"), "TWD"), ("income", Decimal("5000"), "JPY"),
    ]
    assert out["lines"][1]["to_account_name"] == "交割"
    assert out["totals"] == [{"currency": "JPY", "amount": Decimal("5000")}, {"currency": "TWD", "amount": Decimal("-15390")}]


def test_loan_detail_after_three_periods(db_session, seed, today):
    # Spec "Loan detail after three periods" (shape; the entry endpoint is wired in Task 14).
    today(date(2027, 1, 20))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    for seq, day in enumerate((date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)), start=1):
        repayment = seed.entry(
            bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id, is_settlement=True,
            day=day, source="schedule",
        )
        seed.instance(loan, seq, day, status="posted", entries=[repayment])
    seed.instance(loan, 4, date(2027, 2, 9))

    out = read.definition_out(db_session, loan)
    summary = read.loan_schedule_for(db_session, payable.id)

    assert (out["remaining"], out["repaid"], out["posted_count"], out["times"]) == (
        Decimal("-275001"), Decimal("24999"), 3, 36,
    )
    assert (out["next_due_date"], out["loan_entry_id"], out["needs_check"]) == (date(2027, 2, 9), payable.id, False)
    assert out["next_amount"] == [{"currency": "TWD", "amount": Decimal("-8953")}]
    assert (summary["remaining"], summary["repaid"], summary["posted_count"], summary["next_due_date"]) == (
        Decimal("-275001"), Decimal("24999"), 3, date(2027, 2, 9),
    )
    assert read.loan_schedule_for(db_session, payable.id + 1000) is None


def test_card_installment_remaining_from_posted_entries(db_session, seed, card, today):
    # Spec "Card installment remaining from posted entries".
    today(date(2026, 10, 20))
    phone = seed.definition(
        [seed.line("expense", card, "3333")], kind="installment", name="iPhone", anchor=date(2026, 10, 15), times=3,
        total_amount=Decimal("10000"),
    )
    edited = seed.entry(card, "-3300", day=date(2026, 10, 15), source="schedule")
    seed.instance(phone, 1, date(2026, 10, 15), status="posted", entries=[edited])
    seed.instance(phone, 2, date(2026, 11, 15))
    assert read.loan_summary(db_session, phone) == (Decimal("6700"), None)
    assert read.definition_out(db_session, phone)["remaining"] == Decimal("6700")


def test_needs_check_and_failing(db_session, seed, today):
    today(date(2026, 10, 3))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "8333", kind="payable", counterparty_id=lender.id)
    ended = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="舊貸款",
        times=2, status="ended",
    )
    reviewed = seed.definition([seed.line("expense", bank, "1")], name="待檢查", review_reason="interval_mismatch")
    failing = seed.definition([seed.line("expense", bank, "1")], name="失敗")
    first = seed.instance(failing, 1, date(2026, 10, 1), last_error="lines[0].account_id: 帳戶已封存")
    seed.instance(failing, 2, date(2026, 11, 1), last_error="lines[0].account_id: 帳戶已封存")
    assert read.definition_out(db_session, ended)["needs_check"] is True
    assert read.definition_out(db_session, reviewed)["needs_check"] is True
    out = read.definition_out(db_session, failing)
    assert (out["needs_check"], out["failing"]) == (
        False, {"instance_id": first.id, "due_date": date(2026, 10, 1), "last_error": "lines[0].account_id: 帳戶已封存"},
    )


def test_definitions_are_ordered_by_next_due_date_and_filtered(db_session, seed, card, today, monkeypatch):
    today(date(2026, 10, 3))
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)
    late = seed.definition([seed.line("expense", card, "1")], name="晚")
    early = seed.definition([seed.line("expense", card, "1")], name="早", kind="installment", times=2, created_locally=False, moze_id="I-9")
    none = seed.definition([seed.line("expense", card, "1")], name="沒有下期", status="ended")
    seed.instance(late, 1, date(2026, 11, 1))
    seed.instance(early, 1, date(2026, 10, 10))
    assert [item["name"] for item in read.list_definitions(db_session)] == ["早", "晚", "沒有下期"]
    assert [item["name"] for item in read.list_definitions(db_session, status="ended")] == ["沒有下期"]
    assert [item["name"] for item in read.list_definitions(db_session, kind="installment")] == ["早"]
    imported = read.list_definitions(db_session, kind="installment")[0]
    assert (imported["imported"], imported["locked"], imported["created_locally"]) == (True, True, False)
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert read.list_definitions(db_session, kind="installment")[0]["locked"] is False


def test_schedule_links_map_every_posted_entry(db_session, seed, card):
    # Spec "Pill data on a posted entry".
    phone = seed.definition(
        [seed.line("expense", card, "1000"), seed.line("interest", card, "10")], kind="installment", name="分期",
        times=36,
    )
    expense = seed.entry(card, "-1000", source="schedule")
    interest = seed.entry(card, "-10", kind="interest", source="schedule")
    other = seed.entry(card, "-5")
    fifth = seed.instance(phone, 5, date(2027, 2, 22), status="posted", entries=[expense, interest])

    links = read.schedule_links(db_session, [expense.id, interest.id, other.id])

    assert set(links) == {expense.id, interest.id}
    assert links[expense.id] == {
        "definition_id": phone.id, "instance_id": fifth.id, "kind": "installment", "seq": 5, "times": 36,
        "name": "分期", "is_partial": False, "acted_by": "auto", "posted_entry_ids": [expense.id, interest.id],
    }
    assert read.schedule_links(db_session, []) == {}


def test_plain_listing_defaults_to_pending_within_thirty_days(db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    soon = seed.instance(netflix, 1, date(2026, 10, 22))
    seed.instance(netflix, 2, date(2026, 11, 22))
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    posted = seed.instance(netflix, 3, date(2026, 9, 22), status="posted", entries=[entry])
    assert _ids(read.list_instances(db_session)) == [soon.id]
    assert _ids(read.list_instances(db_session, status="posted", date_from=date(2026, 9, 1))) == [posted.id]
    assert len(read.list_instances(db_session, status=None, until=date(2026, 12, 31), definition_id=netflix.id)) == 3
    assert POSTED_AT.tzinfo is not None
```

- [ ] 9.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_read.py
```

Expected: `ImportError: cannot import name 'schedule_read' from 'app.services'`.

- [ ] 9.3 Create `services/accounting-service/app/services/schedule_read.py`:

```python
"""Response shapes of /schedules and the schedule links on entries (spec "Definition endpoints", "Instance
endpoints", "Loan summary", "Schedule links on entries")."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import ledger_service, settlement_service
from .edit_lock import import_locked
from .errors import NotFoundError
from .schedule_rules import plain
from .schedule_templates import LEDGER_KINDS, loan_line, resolved_amounts

POSITIVE_LINE_KINDS = ("income", "payable", "collection")
QUEUE_DAYS = 30
LOAN_SCHEDULE_KEYS = (
    "definition_id", "name", "status", "posting_mode", "posted_count", "times", "next_due_date", "next_amount",
    "remaining", "repaid", "needs_check",
)


def signed_line_amount(kind: str, amount: Decimal) -> Decimal:
    return amount if kind in POSITIVE_LINE_KINDS else -amount


class _Names:
    """Account, category (path, icon, colour) and counterparty names, read once per response."""

    def __init__(self, db: Session):
        self.accounts = {account.id: account.name for account in db.scalars(select(Account))}
        rows = {category.id: category for category in db.scalars(select(Category))}
        self.categories: dict[int, tuple[str, str | None, str | None]] = {}
        for category in rows.values():
            parent = rows.get(category.parent_id) if category.parent_id is not None else None
            path = f"{parent.name}/{category.name}" if parent else category.name
            icon = category.icon or (parent.icon if parent else None)
            color = category.color or (parent.color if parent else None)
            self.categories[category.id] = (path, icon, color)
        self.counterparties = {row.id: row.name for row in db.scalars(select(Counterparty))}

    def category(self, category_id: int | None) -> tuple[str | None, str | None, str | None]:
        return self.categories.get(category_id, (None, None, None)) if category_id is not None else (None, None, None)

    def account(self, account_id: int | None) -> str | None:
        return self.accounts.get(account_id) if account_id is not None else None


def _totals(pairs: dict[str, Decimal]) -> list[dict]:
    return [{"currency": currency, "amount": amount} for currency, amount in sorted(pairs.items())]


def instance_out(
    db: Session,
    instance: ScheduleInstance,
    *,
    today: date | None = None,
    definition: ScheduleDefinition | None = None,
    names: _Names | None = None,
) -> dict:
    today = today or ledger_service._today()
    definition = definition or db.get(ScheduleDefinition, instance.definition_id)
    names = names or _Names(db)
    lines, totals = [], defaultdict(Decimal)
    resolved = resolved_amounts(definition, instance)
    for line, amount in zip(definition.template["lines"], resolved):
        if amount == 0:
            continue
        signed = signed_line_amount(line["kind"], amount)
        lines.append(
            {
                "kind": line["kind"], "account_id": line["account_id"], "account_name": names.account(line["account_id"]),
                "to_account_id": line["to_account_id"], "to_account_name": names.account(line["to_account_id"]),
                "category": names.category(line["category_id"])[0],
                "counterparty": names.counterparties.get(line["counterparty_id"]),
                "amount": signed, "currency": line["currency"],
            }
        )
        totals[line["currency"]] += signed
    _, icon, color = names.category(definition.template["lines"][0].get("category_id"))
    overdue = (today - instance.due_date).days if instance.status == "pending" and instance.due_date < today else 0
    return {
        "id": instance.id, "definition_id": definition.id, "definition_name": definition.name, "kind": definition.kind,
        "posting_mode": definition.posting_mode, "seq": instance.seq, "times": definition.times,
        "due_date": instance.due_date, "rule_date": instance.rule_date, "status": instance.status,
        "is_partial": instance.is_partial, "overdue_days": overdue, "lines": lines, "totals": _totals(totals),
        "amounts": [plain(value) for value in resolved],  # unsigned, aligned with the template (repost bodies)
        "last_error": instance.last_error, "reopened": instance.reopened_at is not None,
        "edited_by_owner": instance.edited_by_owner, "note": instance.note,
        "posted_entry_ids": list(instance.posted_entry_ids), "acted_at": instance.acted_at,
        "acted_by": instance.acted_by, "category_icon": icon, "category_color": color,
    }


def loan_summary(db: Session, definition: ScheduleDefinition) -> tuple[Decimal | None, Decimal | None]:
    """(remaining, repaid) per spec "Loan summary"."""
    reference = loan_line(definition.template)
    if reference is not None:
        loan_id = reference[1].get("loan_entry_id")
        loan = db.get(LedgerEntry, loan_id) if loan_id is not None else None
        if loan is None:
            return None, None
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(db, loan)
        remaining = -open_amount if loan.kind == "payable" and open_amount != 0 else open_amount
        repaid = db.scalar(
            select(func.coalesce(func.sum(func.abs(LedgerEntry.amount)), 0)).where(LedgerEntry.settles_entry_id == loan.id)
        )
        return remaining, Decimal(repaid)
    if definition.kind == "installment" and definition.total_amount is not None:
        first = definition.template["lines"][0]
        posted = db.scalars(
            select(ScheduleInstance.posted_entry_ids).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "posted"
            )
        ).all()
        ids = [entry_id for entry_ids in posted for entry_id in entry_ids]
        entries = {
            entry.id: entry
            for entry in db.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.id.in_(ids) if ids else false(),
                    LedgerEntry.account_id == first["account_id"],
                    LedgerEntry.kind == LEDGER_KINDS[first["kind"]],
                )
            )
        }
        used = Decimal(0)
        for entry_ids in posted:
            match = next((entries[entry_id] for entry_id in entry_ids if entry_id in entries), None)
            if match is not None:
                used += abs(Decimal(match.amount))
        return Decimal(definition.total_amount) - used, None
    return None, None


def _line_out(line: dict, names: _Names) -> dict:
    return {
        **line,
        "account_name": names.account(line["account_id"]),
        "to_account_name": names.account(line["to_account_id"]),
        "category": names.category(line["category_id"])[0],
        "counterparty": names.counterparties.get(line["counterparty_id"]),
    }


def definition_out(
    db: Session, definition: ScheduleDefinition, *, today: date | None = None, names: _Names | None = None
) -> dict:
    today = today or ledger_service._today()
    names = names or _Names(db)
    counts = dict(
        db.execute(
            select(ScheduleInstance.status, func.count())
            .where(ScheduleInstance.definition_id == definition.id)
            .group_by(ScheduleInstance.status)
        ).all()
    )
    pending = select(ScheduleInstance).where(
        ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "pending"
    )
    next_instance = db.scalar(pending.order_by(ScheduleInstance.due_date, ScheduleInstance.seq).limit(1))
    failing = db.scalar(
        pending.where(ScheduleInstance.last_error.is_not(None))
        .order_by(ScheduleInstance.due_date, ScheduleInstance.seq)
        .limit(1)
    )
    remaining, repaid = loan_summary(db, definition)
    reference = loan_line(definition.template)
    loan_open = reference is not None and remaining is not None and remaining != 0
    _, icon, color = names.category(definition.template["lines"][0].get("category_id"))
    next_amount = (
        instance_out(db, next_instance, today=today, definition=definition, names=names)["totals"] if next_instance else []
    )
    return {
        "id": definition.id, "kind": definition.kind, "name": definition.name, "status": definition.status,
        "posting_mode": definition.posting_mode, "interval_unit": definition.interval_unit,
        "interval_n": definition.interval_n, "anchor_date": definition.anchor_date,
        "day_of_month": definition.day_of_month, "first_seq": definition.first_seq, "times": definition.times,
        "end_date": definition.end_date, "total_amount": definition.total_amount,
        "auto_post_from": definition.auto_post_from,
        "template": {
            "lines": [_line_out(line, names) for line in definition.template["lines"]],
            "description": definition.template.get("description"),
            "tags": list(definition.template.get("tags") or []),
        },
        "created_locally": definition.created_locally, "imported": definition.moze_id is not None,
        "locked": definition.moze_id is not None and not import_locked(),
        "review_reason": definition.review_reason, "generated_until": definition.generated_until,
        "posted_count": counts.get("posted", 0), "skipped_count": counts.get("skipped", 0),
        "pending_count": counts.get("pending", 0),
        "next_due_date": next_instance.due_date if next_instance else None, "next_amount": next_amount,
        "remaining": remaining, "repaid": repaid,
        "loan_entry_id": reference[1].get("loan_entry_id") if reference else None,
        "needs_check": definition.review_reason is not None or (definition.status == "ended" and loan_open),
        "failing": (
            {"instance_id": failing.id, "due_date": failing.due_date, "last_error": failing.last_error}
            if failing is not None else None
        ),
        "category_icon": icon, "category_color": color,
    }


def list_definitions(db: Session, *, status: str | None = None, kind: str | None = None) -> list[dict]:
    query = select(ScheduleDefinition)
    if status is not None:
        query = query.where(ScheduleDefinition.status == status)
    if kind is not None:
        query = query.where(ScheduleDefinition.kind == kind)
    names, today = _Names(db), ledger_service._today()
    items = [definition_out(db, row, today=today, names=names) for row in db.scalars(query.order_by(ScheduleDefinition.id))]
    return sorted(items, key=lambda item: (item["next_due_date"] is None, item["next_due_date"] or date.max, item["id"]))


def get_definition(db: Session, definition_id: int) -> dict:
    definition = db.get(ScheduleDefinition, definition_id)
    if definition is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    names, today = _Names(db), ledger_service._today()
    out = definition_out(db, definition, today=today, names=names)
    rows = db.scalars(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition_id).order_by(ScheduleInstance.seq)
    )
    out["instances"] = [instance_out(db, row, today=today, definition=definition, names=names) for row in rows]
    return out


def list_instances(
    db: Session,
    *,
    date_from: date | None = None,
    until: date | None = None,
    status: str | None = "pending",
    definition_id: int | None = None,
    queue: bool = False,
) -> list[dict]:
    today = ledger_service._today()
    until = until or today + timedelta(days=QUEUE_DAYS)
    query = select(ScheduleInstance, ScheduleDefinition).join(
        ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id
    )
    if queue:
        waiting = and_(
            ScheduleInstance.status == "pending",
            ScheduleInstance.due_date <= until,
            or_(
                ScheduleDefinition.posting_mode == "confirm",
                ScheduleInstance.reopened_at.is_not(None),
                ScheduleInstance.last_error.is_not(None),
                ScheduleInstance.due_date < ScheduleDefinition.auto_post_from,
            ),
        )
        partial = and_(ScheduleInstance.status == "posted", ScheduleInstance.is_partial.is_(True))
        query = query.where(ScheduleDefinition.status == "active", or_(waiting, partial))
    else:
        query = query.where(ScheduleInstance.due_date <= until)
        if status is not None:
            query = query.where(ScheduleInstance.status == status)
        if date_from is not None:
            query = query.where(ScheduleInstance.due_date >= date_from)
    if definition_id is not None:
        query = query.where(ScheduleInstance.definition_id == definition_id)
    query = query.order_by(ScheduleInstance.due_date, ScheduleInstance.definition_id, ScheduleInstance.seq)
    names = _Names(db)
    return [
        instance_out(db, instance, today=today, definition=definition, names=names)
        for instance, definition in db.execute(query).all()
    ]


def schedule_links(db: Session, entry_ids: list[int]) -> dict[int, dict]:
    """entry id → the `schedule` object of its posted instance (one @> lookup per id through the GIN index)."""
    wanted = set(entry_ids)
    if not wanted:
        return {}
    rows = db.execute(
        select(ScheduleInstance, ScheduleDefinition)
        .join(ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id)
        .where(or_(*[ScheduleInstance.posted_entry_ids.contains([entry_id]) for entry_id in sorted(wanted)]))
    ).all()
    links: dict[int, dict] = {}
    for instance, definition in rows:
        link = {
            "definition_id": definition.id, "instance_id": instance.id, "kind": definition.kind, "seq": instance.seq,
            "times": definition.times, "name": definition.name, "is_partial": instance.is_partial,
            "acted_by": instance.acted_by, "posted_entry_ids": list(instance.posted_entry_ids),
        }
        for entry_id in instance.posted_entry_ids:
            if entry_id in wanted:
                links[entry_id] = link
    return links


def loan_schedule_for(db: Session, entry_id: int) -> dict | None:
    """`loan_schedule` of a payable / receivable original that a definition's line references (a live one first)."""
    definition = db.scalar(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.template["lines"].contains([{"loan_entry_id": entry_id}]))
        .order_by(case((ScheduleDefinition.status == "ended", 1), else_=0), ScheduleDefinition.id.desc())
        .limit(1)
    )
    if definition is None:
        return None
    out = definition_out(db, definition)
    out["definition_id"] = out["id"]
    return {key: out[key] for key in LOAN_SCHEDULE_KEYS}
```

- [ ] 9.4 Run 9.2 again.

Expected: `9 passed`.

- [ ] 9.5 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_read.py services/accounting-service/tests/integration/test_schedule_read.py
git commit -m "feat(accounting): schedule response shapes, 待完成交易 queue and loan summary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 10. Definition create / update / delete + router skeleton

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_service.py`
- Create: `services/accounting-service/app/routers/schedules.py`
- Modify: `services/accounting-service/app/main.py` (register `schedules.router`)
- Test: `services/accounting-service/tests/integration/test_schedule_definitions_api.py`

**Interfaces:**
- Consumes: Tasks 4–9; `entry_write_service.prepare_entry`, `insert_prepared`; `edit_lock.EditLockedError`, `import_locked`; `app.routers.errors.service_errors`.
- Produces: `schedule_service.create_definition`, `update_definition`, `delete_definition` (signatures in the contract) and the private helpers `_today()`, `_now()`, `_check_rule(kind, payload)`, `_check_total(kind, template, times, total)`, `_set_mode(definition, mode, today)` that Tasks 11–12 reuse; router `app.routers.schedules.router` (prefix `/schedules`) with `GET/POST /definitions`, `GET/PUT/DELETE /definitions/{definition_id}` and the helpers `_definition_out(db, definition_id)` and `_instance_out(db, instance_id)` used by later endpoints.

Rules:
- Create (spec "Definition endpoints"): shared import key; rule checks (installment → `interval_unit = month`, else 422 `interval_unit`; `times ≥ 2`, else 422 `times`; `day_of_month` only for month / year; `end_date ≥ anchor_date`; `total_amount` only on installments); `loan` (installments only) creates a `payable` of `+amount` (`source = 'manual'`, `remember=False`) in the same transaction, sets `total_amount = loan.amount` and fills `loan_entry_id` on repayment lines that have none; template validation; for an installment with a total, `amount × (times − 1) ≥ total_amount` → 422 `total_amount`; `auto_post_from` = today; generation (Task 6 puts the last-period override on `seq == times`); nothing posts.
- Update (編輯整個排程, D35): shared key → definition `FOR UPDATE` → every instance `FOR UPDATE`; imported definitions before cutover → 409 `locked_until_cutover`; delete pending instances due on or after tomorrow (a period due **today** stays — Review Focus 5); realign the remaining pending overrides; `times` below the remaining maximum seq → 422 `times`; the first new instance is the new rule's first occurrence on or after tomorrow and strictly after the latest remaining `max(rule_date, due_date)` (the bound `generate` uses is the latest `rule_date`; a moved period can make either one later), with `seq` = remaining maximum + 1 — inserted by `update_definition` itself (with the last-period override when that seq is `times`), after which `generate` continues strictly after its `rule_date` (only while that seq fits `times` and the date fits `end_date`; otherwise nothing regenerates); ★the rule anchor is never rebased implicitly (Multica R-F3, Global Constraints "Occurrence k"): `anchor_date` and `first_seq` keep their stored values unless the owner sends a different `anchor_date`; a new `anchor_date` is stored as occurrence 0 of the new rule (`rules.normalize_anchor`) and, for a `month` / `year` rule sent without `day_of_month`, `day_of_month` is set to the new anchor's day; an unchanged anchor keeps `day_of_month` as sent (`None` = the anchor's own day), so a Jan-31 monthly rule edited on Feb-1 still gives Feb-28 then Mar-31, and a Feb-29 yearly rule returns to Feb-29 in leap years; `posting_mode` follows `_set_mode` (a switch to `auto` moves `auto_post_from` to today); a missing `total_amount` on an installment keeps the stored one; regenerate.
- Delete: shared key → definition `FOR UPDATE`; imported before cutover → 409 `locked_until_cutover`; any posted instance → 409 with a message pointing to 結束; otherwise the definition and (by cascade) its instances go.
- `anchor_date` is stored as occurrence 0 of the rule (`rules.normalize_anchor`): a `day_of_month` earlier than the sent day starts the next period (起始日 09-20 with day 15 → 10-15). `DefinitionUpdateIn.posting_mode` is optional: left out (`None`), a `PUT` keeps the stored mode and `auto_post_from`; `DefinitionIn` keeps the `"auto"` default.
- The token auth of #42 (`ApiTokenMiddleware`, `ACCOUNTING_API_TOKENS`) is app-wide, so the new routes need no auth code; one test pins the 401 without a bearer token.
- `test_loan_and_schedule_in_one_call` fixes today at 2026-10-09 on purpose: the spec scenario's 13 instances assume the horizon reaches 2027-11-09, i.e. a creation day on or after 2026-10-09 (created on 2026-10-03 the horizon holds 12 periods). The date is test setup, not a spec change.

- [ ] 10.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_definitions_api.py`:

```python
"""Definition endpoints (spec "Definition endpoints", "Schedule definition model", "Imported definitions before cutover")."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import ledger_service
from app.services import schedule_generation as generation
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _line(kind, account, amount, **fields) -> dict:
    return {"kind": kind, "account_id": account.id, "amount": str(amount), "currency": account.currency, **fields}


def _body(lines, **fields) -> dict:
    return {
        "kind": "recurring", "name": "Netflix", "template": {"lines": lines}, "interval_unit": "month",
        "anchor_date": "2026-10-22", **fields,
    }


def _fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _instances(db, definition_id) -> list[ScheduleInstance]:
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition_id).order_by(ScheduleInstance.seq))
    )


def test_local_recurring_definition(client, db_session, seed, today):
    # Spec "Local recurring definition".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()

    response = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["interval_unit"], body["interval_n"], body["anchor_date"], body["times"]) == (
        "recurring", "month", 1, "2026-10-22", None,
    )
    assert (body["posting_mode"], body["status"], body["auto_post_from"], body["created_locally"], body["imported"]) == (
        "auto", "active", "2026-10-03", True, False,
    )
    assert db_session.get(ScheduleDefinition, body["id"]).moze_id is None
    rows = _instances(db_session, body["id"])
    assert len(rows) == 13 and {row.status for row in rows} == {"pending"}
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert body["template"]["lines"][0]["account_name"] == "範例卡"


def test_installment_must_be_monthly_and_have_two_periods(client, db_session, seed, today):
    # Spec "Installment must be monthly" and "Installment needs two periods".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    weekly = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "3333")], kind="installment", interval_unit="week", times=3)
    )
    assert weekly.status_code == 422 and _fields(weekly) == {"interval_unit"}
    once = client.post("/schedules/definitions", json=_body([_line("expense", card, "3333")], kind="installment", times=1))
    assert once.status_code == 422 and _fields(once) == {"times"}


def test_template_errors_name_the_line(client, db_session, seed, today):
    # Spec "Currency mismatch refused".
    today(date(2026, 10, 3))
    yen = seed.account("日幣現金", currency="JPY")
    db_session.commit()
    response = client.post("/schedules/definitions", json=_body([{**_line("expense", yen, "1000"), "currency": "TWD"}]))
    assert response.status_code == 422 and _fields(response) == {"lines[0].currency"}
    unknown = client.post("/schedules/definitions", json=_body([{**_line("expense", yen, "1000"), "fx_rate": "0.2"}]))
    assert unknown.status_code == 422


def test_card_installment_remainder_on_the_last_period(client, db_session, seed, today):
    # Spec "Card installment remainder on the last period".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    response = client.post(
        "/schedules/definitions",
        json=_body([_line("expense", card, "3333")], kind="installment", name="iPhone", times=3, total_amount="10000",
                   anchor_date="2026-10-15"),
    )
    assert response.status_code == 201
    rows = _instances(db_session, response.json()["id"])
    assert [row.due_date for row in rows] == [date(2026, 10, 15), date(2026, 11, 15), date(2026, 12, 15)]
    assert rows[-1].amount_override == ["3334"]
    too_much = client.post(
        "/schedules/definitions",
        json=_body([_line("expense", card, "5000")], kind="installment", times=3, total_amount="10000"),
    )
    assert too_much.status_code == 422 and _fields(too_much) == {"total_amount"}


def test_loan_and_schedule_in_one_call(client, db_session, seed, today):
    # Spec "Loan and schedule in one call". The scenario's 13 instances need a horizon reaching 2027-11-09, i.e.
    # today ≥ 2026-10-09 (on 2026-10-03 the horizon 2027-11-03 holds 12 periods from 2026-11-09).
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    db_session.commit()
    before = ledger_service.account_balance(db_session, bank.id)

    response = client.post(
        "/schedules/definitions",
        json=_body(
            [_line("repayment", bank, "8333"), _line("interest", bank, "620")],
            kind="installment", name="信貸 每月還款", times=36, anchor_date="2026-11-09",
            loan={"account_id": bank.id, "counterparty_id": lender.id, "amount": "300000", "entry_date": "2026-10-03", "name": "信貸"},
        ),
    )

    assert response.status_code == 201
    body = response.json()
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, bank.id) - before == Decimal("300000")
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert (payable.amount, payable.source, payable.counterparty_id, payable.name) == (Decimal("300000"), "manual", lender.id, "信貸")
    assert Decimal(body["total_amount"]) == Decimal("300000")
    assert body["template"]["lines"][0]["loan_entry_id"] == payable.id == body["loan_entry_id"]
    rows = _instances(db_session, body["id"])
    assert len(rows) == 13 and {row.status for row in rows} == {"pending"}


def test_edit_regenerates_only_future_pending_periods_then_rolls_forward(client, db_session, seed, today):
    # Spec "Edit regenerates only future pending periods, then rolls forward".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "1000")], name="訂閱", anchor=date(2026, 9, 1))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 1), source="schedule")
    seed.instance(definition, 1, date(2026, 9, 1), status="posted", entries=[entry])
    seed.instance(definition, 2, date(2026, 10, 1))
    generation.generate(db_session, definition, date(2026, 9, 30))  # seq 3–14 from 2026-11-01, as the scenario's GIVEN
    db_session.commit()
    assert [row.seq for row in _instances(db_session, definition.id)] == list(range(1, 15))

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "訂閱", "template": {"lines": [_line("expense", card, "1200")]}, "interval_unit": "month",
              "anchor_date": "2026-09-01", "day_of_month": 15},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date, row.status) for row in rows[:2]] == [
        (1, date(2026, 9, 1), "posted"), (2, date(2026, 10, 1), "pending"),
    ]
    assert (rows[2].seq, rows[2].due_date) == (3, date(2026, 10, 15))
    db_session.refresh(definition)
    # R-F3: the unchanged anchor is not rebased to the first regenerated period; it is only normalized to occurrence 0
    # of the new rule (day 15 → 2026-09-15), and first_seq stays 1.
    assert (definition.anchor_date, definition.first_seq, definition.day_of_month, definition.template["lines"][0]["amount"]) == (
        date(2026, 9, 15), 1, 15, "1200",
    )
    # Rolling forward one month later (horizon 2027-11-15) adds exactly the next period after 2027-10-15.
    created = generation.generate_locked(db_session, definition.id, date(2026, 10, 15))
    db_session.commit()
    last = _instances(db_session, definition.id)[-1]
    assert rows[-1].due_date == date(2027, 10, 15)
    assert (created, last.due_date, last.seq) == (1, date(2027, 11, 15), rows[-1].seq + 1)


def test_edit_keeps_the_instance_due_today_and_regenerates_from_tomorrow(client, db_session, seed, today):
    # Review Focus 5.
    today(date(2026, 10, 3))
    card, wallet = seed.account("範例卡"), seed.account("錢包")
    definition = seed.definition([seed.line("expense", card, "1000")], name="訂閱", anchor=date(2026, 9, 3))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 3), source="schedule")
    seed.instance(definition, 1, date(2026, 9, 3), status="posted", entries=[entry])
    due_today = seed.instance(definition, 2, date(2026, 10, 3), amount_override=["1100"])
    generation.generate(db_session, definition, date(2026, 10, 3))
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "訂閱", "template": {"lines": [_line("expense", card, "1200"), _line("expense", wallet, "50")]},
              "interval_unit": "month", "anchor_date": "2026-09-03"},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    kept = next(row for row in rows if row.id == due_today.id)
    assert (kept.seq, kept.due_date, kept.status, kept.amount_override) == (2, date(2026, 10, 3), "pending", ["1100", "50"])
    assert [(row.seq, row.due_date) for row in rows[2:4]] == [(3, date(2026, 11, 3)), (4, date(2026, 12, 3))]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.first_seq) == (date(2026, 9, 3), 1)  # R-F3: never rebased implicitly


def _month_end_definition(db_session, seed, card, **columns):
    """A monthly rule anchored on 2026-01-31: seq 1 posted, seq 2… generated on 2026-02-01 (Feb-28, Mar-31, …)."""
    definition = seed.definition([seed.line("expense", card, "1000")], name="月底", anchor=date(2026, 1, 31), **columns)
    entry = seed.entry(card, "-1000", day=date(2026, 1, 31), source="schedule")
    seed.instance(definition, 1, date(2026, 1, 31), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2026, 2, 1))
    db_session.commit()
    return definition


@pytest.mark.parametrize("day_of_month", [None, 31], ids=["implicit_day", "explicit_day"])
def test_edit_keeps_a_month_end_anchor_then_rolls_forward_on_the_31st(client, db_session, seed, today, day_of_month):
    # Multica R-F3: an edit on Feb-1 must not rebase the Jan-31 anchor to Feb-28 (which would give Mar-28 next).
    today(date(2026, 2, 1))
    card = seed.account("範例卡")
    definition = _month_end_definition(db_session, seed, card, day_of_month=day_of_month)

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "月底", "template": {"lines": [_line("expense", card, "1200")]}, "interval_unit": "month",
              "anchor_date": "2026-01-31", "day_of_month": day_of_month},
    )

    assert response.status_code == 200
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date) for row in rows[1:4]] == [
        (2, date(2026, 2, 28)), (3, date(2026, 3, 31)), (4, date(2026, 4, 30)),
    ]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month, definition.first_seq) == (date(2026, 1, 31), day_of_month, 1)
    assert rows[-1].due_date == date(2027, 2, 28)  # horizon(2026-02-01) = 2027-03-01
    created = generation.generate_locked(db_session, definition.id, date(2026, 3, 1))  # horizon 2027-04-01
    db_session.commit()
    last = _instances(db_session, definition.id)[-1]
    assert (created, last.due_date, last.seq) == (1, date(2027, 3, 31), rows[-1].seq + 1)


def test_edit_keeps_a_leap_day_yearly_anchor_then_returns_to_feb_29(client, db_session, seed, today):
    # Multica R-F3: a Feb-29 yearly rule edited in a common year still lands on Feb-29 in the next leap year.
    today(date(2028, 3, 1))
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, "990")], name="年費", interval_unit="year", anchor=date(2028, 2, 29),
        auto_post_from=date(2028, 2, 1),
    )
    entry = seed.entry(card, "-990", day=date(2028, 2, 29), source="schedule")
    seed.instance(definition, 1, date(2028, 2, 29), status="posted", entries=[entry])
    generation.generate(db_session, definition, date(2028, 3, 1))  # seq 2 on 2029-02-28
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "年費", "template": {"lines": [_line("expense", card, "1090")]}, "interval_unit": "year",
              "anchor_date": "2028-02-29"},
    )

    assert response.status_code == 200
    assert [(row.seq, row.due_date) for row in _instances(db_session, definition.id)] == [
        (1, date(2028, 2, 29)), (2, date(2029, 2, 28)),
    ]
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month) == (date(2028, 2, 29), None)
    generation.generate_locked(db_session, definition.id, date(2031, 3, 1))  # horizon 2032-04-01
    db_session.commit()
    assert [row.due_date for row in _instances(db_session, definition.id)][2:] == [
        date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29),
    ]


@pytest.mark.parametrize(
    ("day_of_month", "anchor", "first"),
    [(None, date(2026, 2, 10), date(2026, 2, 10)), (25, date(2026, 2, 25), date(2026, 2, 25))],
    ids=["implicit_day", "explicit_day"],
)
def test_edit_with_a_new_anchor_takes_its_day(client, db_session, seed, today, day_of_month, anchor, first):
    # Multica R-F3: only an explicit new anchor_date moves the anchor; without day_of_month its day becomes the rule's.
    today(date(2026, 2, 1))
    card = seed.account("範例卡")
    definition = _month_end_definition(db_session, seed, card)

    response = client.put(
        f"/schedules/definitions/{definition.id}",
        json={"name": "月底", "template": {"lines": [_line("expense", card, "1000")]}, "interval_unit": "month",
              "anchor_date": "2026-02-10", "day_of_month": day_of_month},
    )

    assert response.status_code == 200
    db_session.refresh(definition)
    assert (definition.anchor_date, definition.day_of_month) == (anchor, first.day)
    rows = _instances(db_session, definition.id)
    assert [(row.seq, row.due_date) for row in rows[1:3]] == [(2, first), (3, first.replace(month=3))]


def test_delete_refused_after_posting(client, db_session, seed, today):
    # Spec "Delete refused after posting".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    posted = seed.definition([seed.line("expense", card, "390")])
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    seed.instance(posted, 1, date(2026, 9, 22), status="posted", entries=[entry])
    fresh = seed.definition([seed.line("expense", card, "390")], name="新")
    seed.instance(fresh, 1, date(2026, 10, 22))
    db_session.commit()

    refused = client.delete(f"/schedules/definitions/{posted.id}")
    assert refused.status_code == 409 and "結束" in refused.json()["message"]
    assert client.delete(f"/schedules/definitions/{fresh.id}").status_code == 204
    assert _instances(db_session, fresh.id) == []
    assert db_session.get(ScheduleDefinition, fresh.id) is None


def test_template_edit_refused_during_the_mirror_period(client, db_session, seed, today, monkeypatch):
    # Spec "Template edit refused during the mirror period".
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    bank = seed.account("薪轉")
    imported = seed.definition(
        [seed.line("expense", bank, "8333")], kind="installment", name="信貸", times=36, created_locally=False, moze_id="I-1"
    )
    db_session.commit()
    body = {"name": "信貸", "template": {"lines": [_line("expense", bank, "9000")]}, "interval_unit": "month",
            "anchor_date": "2026-10-22", "times": 36}
    for response in (client.put(f"/schedules/definitions/{imported.id}", json=body),
                     client.delete(f"/schedules/definitions/{imported.id}")):
        assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert client.put(f"/schedules/definitions/{imported.id}", json=body).status_code == 200


def test_writes_are_refused_while_an_import_runs(client, db_session, seed, today, pg_engine):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_get_definition_lists_its_instances_and_unknown_ids_are_404(client, db_session, seed, today):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    created = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")])).json()
    detail = client.get(f"/schedules/definitions/{created['id']}").json()
    assert [item["seq"] for item in detail["instances"]] == list(range(1, 14))
    assert client.get("/schedules/definitions").json()[0]["id"] == created["id"]
    for response in (client.get("/schedules/definitions/9999"), client.delete("/schedules/definitions/9999")):
        assert response.status_code == 404


def test_put_without_posting_mode_keeps_the_stored_mode(client, db_session, seed, today):
    # DefinitionUpdateIn.posting_mode = None keeps the mode: a PUT never switches 提醒入帳 to 自動入帳 silently.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    rent = seed.definition(
        [seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", anchor=date(2026, 10, 25),
        auto_post_from=date(2026, 9, 1),
    )
    db_session.commit()

    response = client.put(
        f"/schedules/definitions/{rent.id}",
        json={"name": "房租", "template": {"lines": [_line("expense", card, "18500")]}, "interval_unit": "month",
              "anchor_date": "2026-10-25"},
    )

    assert response.status_code == 200
    assert (response.json()["posting_mode"], response.json()["auto_post_from"]) == ("confirm", "2026-09-01")


def test_day_of_month_before_the_start_day_starts_next_month(client, db_session, seed, today):
    # Spec "anchor_date … occurrence 0 of the rule": 起始日 09-20 with day 15 stores 10-15 as the anchor.
    today(date(2026, 9, 18))
    card = seed.account("範例卡")
    db_session.commit()

    response = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "390")], anchor_date="2026-09-20", day_of_month=15)
    )

    assert response.status_code == 201
    assert response.json()["anchor_date"] == "2026-10-15"
    assert _instances(db_session, response.json()["id"])[0].due_date == date(2026, 10, 15)


def test_schedule_writes_need_the_bearer_token_when_api_tokens_are_set(client, db_session, seed, today, monkeypatch):
    # #42: ApiTokenMiddleware covers the new routes with no extra code (design D42).
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    db_session.commit()
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", "test:synthetic-token-1")

    refused = client.post("/schedules/definitions", json=_body([_line("expense", card, "390")]))
    allowed = client.post(
        "/schedules/definitions", json=_body([_line("expense", card, "390")]),
        headers={"Authorization": "Bearer synthetic-token-1"},
    )

    assert refused.status_code == 401
    assert allowed.status_code == 201
```

- [ ] 10.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_definitions_api.py
```

Expected: 19 failures with `404 Not Found` (no `/schedules` routes yet; with the token set, the unauthenticated call is already 401, so `test_schedule_writes_need_the_bearer_token…` fails on the `201` assertion); `test_edit_…` tests fail the same way.

- [ ] 10.3 Create `services/accounting-service/app/services/schedule_service.py`:

```python
"""Definition and instance actions of /schedules (design D35, D36; spec "Definition endpoints", "Definition state
endpoints", "Instance endpoints").

Every write first shares the import advisory key (409 import_running while an import runs), then locks the definition
and its instances (D32). Services never commit, except post_sequence, which commits one transaction per instance.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from ..schemas.schedules import DefinitionIn, DefinitionUpdateIn, InstanceUpdateIn
from ..schemas.writes import EntryIn
from . import entry_write_service, ledger_service
from . import schedule_generation as generation
from . import schedule_posting as posting
from . import schedule_rules as rules
from .edit_lock import EditLockedError, import_locked
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import (
    ImportRunningError,
    get_instance,
    lock_definition,
    lock_instance,
    lock_instances,
    take_import_key_shared,
)
from .schedule_templates import check_amounts, loan_line, normalize_template, realign_override, validate_template


def _today() -> date:
    return ledger_service._today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_rule(kind: str, payload: DefinitionUpdateIn) -> None:
    if kind == "installment":
        if payload.interval_unit != "month":
            raise ValidationError("interval_unit", "分期只能每月一期")
        if payload.times is None or payload.times < 2:
            raise ValidationError("times", "分期至少 2 期")
    if payload.day_of_month is not None and payload.interval_unit not in ("month", "year"):
        raise ValidationError("day_of_month", "只有每月或每年可以指定日期")
    if payload.end_date is not None and payload.end_date < payload.anchor_date:
        raise ValidationError("end_date", "結束日不可早於起始日")
    if payload.total_amount is not None and kind != "installment":
        raise ValidationError("total_amount", "只有分期有總額")


def _check_total(kind: str, template: dict, times: int | None, total: Decimal | None) -> None:
    if kind == "installment" and total is not None and times is not None:
        per_period = Decimal(template["lines"][0]["amount"])
        if per_period * (times - 1) >= Decimal(total):
            raise ValidationError("total_amount", "每期金額乘以期數已達總額")


def _set_mode(definition: ScheduleDefinition, mode: str, today: date) -> None:
    """D35: a switch to auto never posts a backlog, so auto_post_from moves to today."""
    if mode == "auto" and definition.posting_mode != "auto":
        definition.auto_post_from = today
    definition.posting_mode = mode


def create_definition(db: Session, payload: DefinitionIn) -> int:
    take_import_key_shared(db)
    today = _today()
    _check_rule(payload.kind, payload)
    template = normalize_template(payload.template.model_dump())
    total_amount = payload.total_amount
    if payload.loan is not None:
        if payload.kind != "installment":
            raise ValidationError("loan", "只有分期可以同時建立貸款")
        loan = payload.loan
        try:
            prepared = entry_write_service.prepare_entry(
                db,
                EntryIn(
                    account_id=loan.account_id, kind="payable", amount=loan.amount, entry_date=loan.entry_date,
                    category_id=loan.category_id, counterparty_id=loan.counterparty_id, name=loan.name,
                ),
            )
        except ValidationError as exc:
            raise ValidationError(f"loan.{exc.field}", exc.message) from exc
        loan_id = entry_write_service.insert_prepared(db, prepared, remember=False)
        for line in template["lines"]:
            if line["kind"] == "repayment" and line["loan_entry_id"] is None:
                line["loan_entry_id"] = loan_id
        total_amount = loan.amount
    validate_template(db, template)
    _check_total(payload.kind, template, payload.times, total_amount)
    # anchor_date is occurrence 0: a day_of_month earlier than the sent day starts next month (rules.normalize_anchor)
    anchor = rules.normalize_anchor(payload.anchor_date, payload.interval_unit, payload.interval_n, payload.day_of_month)
    if payload.end_date is not None and payload.end_date < anchor:
        raise ValidationError("end_date", "結束日期不可早於第一期")
    definition = ScheduleDefinition(
        kind=payload.kind, name=payload.name, template=template, interval_unit=payload.interval_unit,
        interval_n=payload.interval_n, anchor_date=anchor, day_of_month=payload.day_of_month,
        times=payload.times, end_date=payload.end_date, total_amount=total_amount, posting_mode=payload.posting_mode,
        status="active", auto_post_from=today, created_locally=True,
    )
    db.add(definition)
    db.flush()
    generation.generate(db, definition, today)
    generation.end_if_complete(db, definition)
    return definition.id


def update_definition(db: Session, definition_id: int, payload: DefinitionUpdateIn) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.moze_id is not None and not import_locked():
        raise EditLockedError()  # D36: the next import would overwrite a template edit
    instances = lock_instances(db, definition_id)
    today = _today()
    tomorrow = today + timedelta(days=1)
    _check_rule(definition.kind, payload)
    template = normalize_template(payload.template.model_dump())
    validate_template(db, template)
    total = payload.total_amount if payload.total_amount is not None else definition.total_amount
    if definition.kind != "installment":
        total = None
    _check_total(definition.kind, template, payload.times, total)

    doomed = [row for row in instances if row.status == "pending" and row.due_date >= tomorrow]
    remaining = [row for row in instances if row not in doomed]
    max_seq = max((row.seq for row in remaining), default=0)
    if payload.times is not None and payload.times < max_seq:
        raise ValidationError("times", "期數不可少於已有的期別")
    for row in doomed:
        db.delete(row)
    db.flush()
    old_template = definition.template
    for row in remaining:
        if row.status == "pending":
            row.amount_override = realign_override(old_template, template, row.amount_override)

    definition.name, definition.template = payload.name, template
    definition.interval_unit, definition.interval_n = payload.interval_unit, payload.interval_n
    definition.day_of_month, definition.times, definition.end_date = payload.day_of_month, payload.times, payload.end_date
    definition.total_amount = total
    if payload.posting_mode is not None:
        _set_mode(definition, payload.posting_mode, today)

    # R-F3: occurrences are always computed from the rule anchor, which an edit never rebases implicitly. Only a
    # different anchor_date moves it; a month / year rule sent without day_of_month then takes the new anchor's day.
    unit, n, day_of_month = payload.interval_unit, payload.interval_n, payload.day_of_month
    anchor_changed = payload.anchor_date != definition.anchor_date
    if anchor_changed and day_of_month is None and unit in ("month", "year"):
        day_of_month = payload.anchor_date.day
    base = payload.anchor_date if anchor_changed else definition.anchor_date
    anchor = rules.normalize_anchor(base, unit, n, day_of_month)  # occurrence 0; keeps a 31st / 02-29 anchor as is
    definition.anchor_date, definition.day_of_month = anchor, day_of_month  # first_seq is kept
    k = rules.first_index_on_or_after(anchor, unit, n, day_of_month, tomorrow)
    # The same bound generation uses: generate() continues strictly after the latest rule_date, and an owner may
    # have moved a period (due_date ≠ rule_date) either way — start after the later of the two.
    latest = max((max(row.rule_date, row.due_date) for row in remaining), default=None)
    if latest is not None:
        k = max(k, rules.first_index_after(anchor, unit, n, day_of_month, latest))
    first = rules.occurrence(anchor, unit, n, k, day_of_month)
    next_seq = max(max_seq + 1, definition.first_seq)
    fits = (payload.times is None or next_seq <= payload.times) and (payload.end_date is None or first <= payload.end_date)
    db.flush()
    if fits:
        # The first regenerated period is inserted here, so generate() (strictly after the latest rule_date) continues
        # from it without the anchor being moved; it carries the last-period override when it is the last seq.
        last_override = generation.last_period_override(definition)
        db.add(ScheduleInstance(
            definition_id=definition.id, seq=next_seq, rule_date=first, due_date=first,
            amount_override=last_override if last_override is not None and next_seq == definition.times else None,
        ))
        db.flush()
        generation.generate(db, definition, today)
    generation.end_if_complete(db, definition)


def delete_definition(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.moze_id is not None and not import_locked():
        raise EditLockedError()
    posted = db.scalar(
        select(ScheduleInstance.id)
        .where(ScheduleInstance.definition_id == definition_id, ScheduleInstance.status == "posted")
        .limit(1)
    )
    if posted is not None:
        raise ConflictError("排程已有入帳的期別，不能刪除；請改用結束（end）")
    db.delete(definition)  # instances go by ON DELETE CASCADE
    db.flush()
```

- [ ] 10.4 Create `services/accounting-service/app/routers/schedules.py`:

```python
"""/schedules endpoints (design D40). In-service paths: Caddy and the dev proxy strip /api/accounting.

Writes commit after the service call; service_errors maps ValidationError → 422 naming the field, EditLockedError →
409 locked_until_cutover, ConflictError (ImportRunningError included) → 409, NotFoundError → 404.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..database import get_db, get_engine
from ..models import ScheduleDefinition, ScheduleInstance
from ..schemas.schedules import (
    CatchUpOut,
    DefinitionDetailOut,
    DefinitionIn,
    DefinitionKind,
    DefinitionOut,
    DefinitionStatus,
    DefinitionUpdateIn,
    InstanceOut,
    InstanceUpdateIn,
    ModeIn,
    RepostIn,
    ResumeIn,
    RunReportOut,
)
from ..services import schedule_read, schedule_service
from .errors import service_errors

router = APIRouter(prefix="/schedules", tags=["Schedules"])


def _definition_out(db: Session, definition_id: int) -> dict:
    db.expire_all()
    definition = db.get(ScheduleDefinition, definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="schedule definition not found")
    return schedule_read.definition_out(db, definition)


def _instance_out(db: Session, instance_id: int) -> dict:
    db.expire_all()
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="schedule instance not found")
    return schedule_read.instance_out(db, instance)


@router.get("/definitions", response_model=list[DefinitionOut])
def list_definitions(
    status: DefinitionStatus | None = None, kind: DefinitionKind | None = None, db: Session = Depends(get_db)
):
    return schedule_read.list_definitions(db, status=status, kind=kind)


@router.get("/definitions/{definition_id}", response_model=DefinitionDetailOut)
def get_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        return schedule_read.get_definition(db, definition_id)


@router.post("/definitions", response_model=DefinitionOut, status_code=201)
def post_definition(payload: DefinitionIn, db: Session = Depends(get_db)):
    with service_errors():
        definition_id = schedule_service.create_definition(db, payload)
    db.commit()
    return _definition_out(db, definition_id)


@router.put("/definitions/{definition_id}", response_model=DefinitionOut)
def put_definition(definition_id: int, payload: DefinitionUpdateIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.update_definition(db, definition_id, payload)
    db.commit()
    return _definition_out(db, definition_id)


@router.delete("/definitions/{definition_id}", status_code=204)
def delete_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.delete_definition(db, definition_id)
    db.commit()
    return Response(status_code=204)
```

  (`date`, `Literal`, `Query`, `Engine`, `get_engine`, `CatchUpOut`, `InstanceOut`, `InstanceUpdateIn`, `ModeIn`, `RepostIn`, `ResumeIn`, `RunReportOut` are imported now for the endpoints Tasks 11, 12 and 15 append.)

- [ ] 10.5 Register the router in `services/accounting-service/app/main.py`: replace `from .routers import accounts, balance_adjustments, entries, imports, settings, splits, transfers` with `from .routers import accounts, balance_adjustments, entries, imports, schedules, settings, splits, transfers` and the `routers=[...]` value with

```python
    routers=[
        accounts.router, entries.router, imports.router, settings.router,
        transfers.router, splits.router, balance_adjustments.router, schedules.router,
    ],
```

- [ ] 10.6 Run 10.2 again.

Expected: `19 passed`.

- [ ] 10.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_service.py services/accounting-service/app/routers/schedules.py \
  services/accounting-service/app/main.py services/accounting-service/tests/integration/test_schedule_definitions_api.py
git commit -m "feat(accounting): schedule definition create, edit and delete endpoints

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 11. Definition state endpoints (pause, resume, end, mode, catch-up)

**Model:** opus

**Files:**
- Modify: `services/accounting-service/app/services/schedule_service.py` (append `pause`, `resume`, `end`, `set_mode`, `catch_up_ids`, `post_sequence`)
- Modify: `services/accounting-service/app/routers/schedules.py` (append five endpoints)
- Test: `services/accounting-service/tests/integration/test_schedule_state_api.py`

**Interfaces:**
- Consumes: Task 10 helpers (`_today`, `_now`, `_set_mode`, `_definition_out`); `posting.post_instance`, `failure_message`, `record_failure`, `auto_eligible` (Task 8); `schedule_read.list_instances` (Task 9).
- Produces: `pause(db, definition_id)`, `resume(db, definition_id, backlog) -> list[int]`, `end(db, definition_id)`, `set_mode(db, definition_id, posting_mode)`, `catch_up_ids(db, definition_id) -> list[int]`, `post_sequence(db, instance_ids) -> tuple[list[int], dict | None]`; routes `POST /definitions/{id}/pause`, `/resume` (optional body `{"backlog"}`), `/end`, `PUT /definitions/{id}/mode`, `POST /definitions/{id}/catch-up` → `CatchUpOut`.

Rules (spec "Definition state endpoints", D35): every action shares the import key, then locks the definition `FOR UPDATE` before its instances (`catch_up_ids` takes `FOR SHARE`: it only reads, then each post locks as usual). `pause` 409 unless `active`; `resume` 409 unless `paused`, default `backlog = skip` skips pending instances due ≤ today (`acted_by = owner`), `post` posts them after the status change commits; `end` 409 when already `ended`, deletes every pending instance, and clears a `review_reason` of `not_live` (Task 17: the import revives only its own end, never the owner's); `catch-up` 409 while paused, posts every pending instance due ≤ today in seq order (whatever the mode, `auto_post_from` or `reopened_at`), `acted_by = owner`, one transaction each, stopping at the first failure, answering 200 with `failed` set. `post_sequence` treats a `ConflictError` other than `import_running` (someone posted or skipped it meanwhile) as "not mine" and moves on; `import_running` stops it with `failed = {"instance_id", "error": "import_running"}`; any other exception (a `ValidationError`, an `IntegrityError`, a deadlock's `OperationalError`, `EditLockedError`) is stored by `record_failure(failure_message(exc))` — the class name for non-validation errors — and stops it with `failed` set. All state actions are allowed on imported definitions before cutover (spec "Imported definitions before cutover").

- [ ] 11.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_state_api.py`:

```python
"""Definition state endpoints (spec "Definition state endpoints")."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_posting as posting
from app.services import schedule_read
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _rows(db, definition) -> list[ScheduleInstance]:
    db.expire_all()
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_resume_skips_the_paused_months_by_default(client, db_session, seed, card, today):
    # Spec "Resume skips the paused months by default".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], status="paused")
    for seq, day in enumerate((date(2026, 7, 22), date(2026, 8, 22), date(2026, 9, 22), date(2026, 10, 22)), start=1):
        seed.instance(netflix, seq, day)
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/resume")

    assert response.status_code == 200 and response.json()["status"] == "active"
    rows = _rows(db_session, netflix)
    assert [(row.status, row.acted_by) for row in rows[:3]] == [("skipped", "owner")] * 3
    assert rows[3].status == "pending"
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_resume_with_backlog_post_posts_in_seq_order(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")], status="paused")
    first = seed.instance(netflix, 1, date(2026, 8, 22))
    second = seed.instance(netflix, 2, date(2026, 9, 22))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/resume", json={"backlog": "post"})

    assert response.status_code == 200
    rows = _rows(db_session, netflix)
    assert [(row.status, row.acted_by) for row in rows] == [("posted", "owner"), ("posted", "owner")]
    assert rows[0].acted_at <= rows[1].acted_at
    assert {first.id, second.id} == {row.id for row in rows}


def test_pause_and_resume_need_the_right_status(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    db_session.commit()
    assert client.post(f"/schedules/definitions/{netflix.id}/resume").status_code == 409
    paused = client.post(f"/schedules/definitions/{netflix.id}/pause")
    assert (paused.status_code, paused.json()["status"]) == (200, "paused")
    assert client.post(f"/schedules/definitions/{netflix.id}/pause").status_code == 409
    assert client.post("/schedules/definitions/9999/pause").status_code == 404


def test_end_removes_pending_periods(client, db_session, seed, card, today):
    # Spec "End removes pending periods".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    for seq in (1, 2):
        entry = seed.entry(card, "-390", day=date(2026, 7 + seq, 22), source="schedule")
        seed.instance(netflix, seq, date(2026, 7 + seq, 22), status="posted", entries=[entry])
    for seq in range(3, 14):
        seed.instance(netflix, seq, date(2026, 10, 22) if seq == 3 else date(2027, seq - 3, 22))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{netflix.id}/end")

    assert (response.status_code, response.json()["status"]) == (200, "ended")
    assert [row.status for row in _rows(db_session, netflix)] == ["posted", "posted"]


def test_ending_an_ended_definition_is_refused(client, db_session, seed, card, today):
    # Spec "Ending an ended definition".
    today(date(2026, 10, 3))
    ended = seed.definition([seed.line("expense", card, "390")], status="ended")
    db_session.commit()
    assert client.post(f"/schedules/definitions/{ended.id}/end").status_code == 409


def test_catch_up_posts_the_backlog_in_order(client, db_session, seed, card, today):
    # Spec "Catch-up posts the backlog in order".
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    early = seed.instance(rent, 1, date(2026, 9, 22))
    late = seed.instance(rent, 2, date(2026, 10, 1))
    seed.instance(rent, 3, date(2026, 11, 1))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    body = response.json()
    assert (body["posted"], body["failed"], body["definition"]["posted_count"]) == ([early.id, late.id], None, 2)
    rows = _rows(db_session, rent)
    assert [(row.status, row.acted_by) for row in rows] == [("posted", "owner"), ("posted", "owner"), ("pending", None)]


def test_catch_up_refused_while_paused(client, db_session, seed, card, today):
    # Spec "Catch-up refused while paused".
    today(date(2026, 10, 3))
    paused = seed.definition([seed.line("expense", card, "390")], status="paused")
    seed.instance(paused, 1, date(2026, 9, 22))
    db_session.commit()
    assert client.post(f"/schedules/definitions/{paused.id}/catch-up").status_code == 409


def test_catch_up_stops_at_the_first_failure_and_still_answers_200(client, db_session, seed, card, today):
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    broken = seed.instance(rent, 1, date(2026, 9, 22), amount_override=["0"])
    waiting = seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()

    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    body = response.json()
    assert body["posted"] == [] and body["failed"]["instance_id"] == broken.id
    assert body["failed"]["error"].startswith("amounts")
    rows = _rows(db_session, rent)
    assert (rows[0].status, rows[0].last_error is not None) == ("pending", True)
    assert (rows[1].id, rows[1].status, rows[1].last_error) == (waiting.id, "pending", None)


def test_catch_up_records_any_error_by_class_and_stops(client, db_session, seed, card, today, monkeypatch):
    # Spec "On any error … a separate transaction SHALL store the error": not only ValidationError.
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm")
    first = seed.instance(rent, 1, date(2026, 9, 22))
    seed.instance(rent, 2, date(2026, 10, 1))
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(posting, "post_locked", boom)
    response = client.post(f"/schedules/definitions/{rent.id}/catch-up")

    assert response.status_code == 200
    assert response.json()["failed"] == {"instance_id": first.id, "error": "RuntimeError"}
    rows = _rows(db_session, rent)
    assert [(row.status, row.last_error) for row in rows] == [("pending", "RuntimeError"), ("pending", None)]


def test_switching_to_automatic_posting(client, db_session, seed, card, today):
    # Spec "Switching to automatic posting" (the job half runs in Task 15).
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm",
                           auto_post_from=date(2026, 9, 1))
    backlog = seed.instance(rent, 1, date(2026, 9, 22))
    due = seed.instance(rent, 2, date(2026, 10, 3))
    db_session.commit()

    response = client.put(f"/schedules/definitions/{rent.id}/mode", json={"posting_mode": "auto"})

    assert (response.status_code, response.json()["auto_post_from"], response.json()["posting_mode"]) == (200, "2026-10-03", "auto")
    db_session.expire_all()
    definition = db_session.get(ScheduleDefinition, rent.id)
    rows = {row.id: row for row in _rows(db_session, rent)}
    assert posting.auto_eligible(definition, rows[due.id], date(2026, 10, 3)) is True
    assert posting.auto_eligible(definition, rows[backlog.id], date(2026, 10, 3)) is False
    assert backlog.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_write_during_an_import(client, db_session, seed, card, today, pg_engine):
    # Spec "Write during an import".
    today(date(2026, 10, 3))
    netflix = seed.definition([seed.line("expense", card, "390")])
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.post(f"/schedules/definitions/{netflix.id}/pause")
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_state_actions_work_on_imported_definitions_before_cutover(client, db_session, seed, card, today, monkeypatch):
    # Spec "Imported definitions before cutover": pause, resume, end and mode are allowed.
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    db_session.commit()
    for call in (
        lambda: client.post(f"/schedules/definitions/{imported.id}/pause"),
        lambda: client.post(f"/schedules/definitions/{imported.id}/resume"),
        lambda: client.put(f"/schedules/definitions/{imported.id}/mode", json={"posting_mode": "confirm"}),
        lambda: client.post(f"/schedules/definitions/{imported.id}/end"),
    ):
        assert call().status_code == 200
```

- [ ] 11.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_state_api.py
```

Expected: 12 failures (404 or 405 on the new paths).

- [ ] 11.3 Append to `services/accounting-service/app/services/schedule_service.py`:

```python
def pause(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status != "active":
        raise ConflictError(f"只有進行中的排程可以暫停（目前 {definition.status}）")
    definition.status = "paused"
    db.flush()


def _due_pending(db: Session, definition_id: int, today: date):
    return select(ScheduleInstance).where(
        ScheduleInstance.definition_id == definition_id,
        ScheduleInstance.status == "pending",
        ScheduleInstance.due_date <= today,
    )


def resume(db: Session, definition_id: int, backlog: str) -> list[int]:
    """Active again; the paused months are skipped (default) or returned to be posted after this commits."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status != "paused":
        raise ConflictError(f"只有暫停中的排程可以繼續（目前 {definition.status}）")
    lock_instances(db, definition_id)
    definition.status = "active"
    due = list(db.scalars(_due_pending(db, definition_id, _today()).order_by(ScheduleInstance.seq)))
    if backlog == "post":
        db.flush()
        return [row.id for row in due]
    now = _now()
    for row in due:
        row.status, row.acted_at, row.acted_by = "skipped", now, "owner"
    db.flush()
    return []


def end(db: Session, definition_id: int) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    if definition.status == "ended":
        raise ConflictError("排程已結束")
    for row in lock_instances(db, definition_id):
        if row.status == "pending":
            db.delete(row)
    definition.status = "ended"
    if definition.review_reason == "not_live":
        definition.review_reason = None  # the owner's end: a later backup import must not revive it (Task 17)
    db.flush()


def set_mode(db: Session, definition_id: int, posting_mode: str) -> None:
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id)
    _set_mode(definition, posting_mode, _today())
    db.flush()


def catch_up_ids(db: Session, definition_id: int) -> list[int]:
    """補入帳至今天: the pending periods due today or earlier, in seq order; refused while paused."""
    take_import_key_shared(db)
    definition = lock_definition(db, definition_id, share=True)
    if definition.status == "paused":
        raise ConflictError("排程已暫停，請先繼續")
    return list(db.scalars(_due_pending(db, definition_id, _today()).with_only_columns(ScheduleInstance.id).order_by(ScheduleInstance.seq)))


def post_sequence(db: Session, instance_ids: list[int]) -> tuple[list[int], dict | None]:
    """Post each instance in its own transaction (acted_by owner), stopping at the first failure (D31)."""
    posted: list[int] = []
    for instance_id in instance_ids:
        try:
            result = posting.post_instance(db, instance_id, actor="owner")
            db.commit()
        except ImportRunningError:
            db.rollback()
            return posted, {"instance_id": instance_id, "error": "import_running"}
        except ValidationError as exc:
            message = posting.failure_message(exc)
            posting.record_failure(db, instance_id, message)
            return posted, {"instance_id": instance_id, "error": message}
        except (ConflictError, NotFoundError):
            db.rollback()  # posted, skipped or deleted by someone else meanwhile
            continue
        except Exception as exc:  # noqa: BLE001 — IntegrityError, a deadlock's OperationalError, EditLockedError …
            # Spec: on any error a second transaction stores last_error; failure_message keeps only the class name.
            message = posting.failure_message(exc)
            posting.record_failure(db, instance_id, message)
            return posted, {"instance_id": instance_id, "error": message}
        if result.outcome == "posted":
            posted.append(instance_id)
        elif result.outcome == "loan_closed":
            break  # the later periods were skipped with it
    return posted, None
```

- [ ] 11.4 Append to `services/accounting-service/app/routers/schedules.py`:

```python
@router.post("/definitions/{definition_id}/pause", response_model=DefinitionOut)
def pause_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.pause(db, definition_id)
    db.commit()
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/resume", response_model=DefinitionOut)
def resume_definition(definition_id: int, payload: ResumeIn | None = None, db: Session = Depends(get_db)):
    with service_errors():
        to_post = schedule_service.resume(db, definition_id, payload.backlog if payload else "skip")
    db.commit()
    if to_post:
        schedule_service.post_sequence(db, to_post)
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/end", response_model=DefinitionOut)
def end_definition(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.end(db, definition_id)
    db.commit()
    return _definition_out(db, definition_id)


@router.put("/definitions/{definition_id}/mode", response_model=DefinitionOut)
def put_mode(definition_id: int, payload: ModeIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.set_mode(db, definition_id, payload.posting_mode)
    db.commit()
    return _definition_out(db, definition_id)


@router.post("/definitions/{definition_id}/catch-up", response_model=CatchUpOut)
def catch_up(definition_id: int, db: Session = Depends(get_db)):
    with service_errors():
        instance_ids = schedule_service.catch_up_ids(db, definition_id)
    db.commit()
    posted, failed = schedule_service.post_sequence(db, instance_ids)
    return {"posted": posted, "failed": failed, "definition": _definition_out(db, definition_id)}
```

- [ ] 11.5 Run 11.2 again.

Expected: `12 passed`.

- [ ] 11.6 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_service.py services/accounting-service/app/routers/schedules.py \
  services/accounting-service/tests/integration/test_schedule_state_api.py
git commit -m "feat(accounting): pause, resume, end, mode and catch-up for schedules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 12. Instance endpoints (list, edit, post, skip, reopen, repost, accept-partial)

**Model:** opus

**Files:**
- Modify: `services/accounting-service/app/services/schedule_service.py` (append the instance actions, the amount edit scope included)
- Modify: `services/accounting-service/app/schemas/schedules.py` (`InstanceUpdateIn.scope`, `DefinitionOut.template_owner_edited`)
- Modify: `services/accounting-service/app/services/schedule_read.py` (`definition_out` returns `template_owner_edited`)
- Modify: `services/accounting-service/app/routers/schedules.py` (append seven endpoints)
- Test: `services/accounting-service/tests/integration/test_schedule_instances_api.py`

**Interfaces:**
- Consumes: Task 8 (`post_instance`, `post_locked(locked_loan=)`, `lock_definition_for_post`, `lock_later_pending`, `record_failure`, `failure_message`, `delete_period_entries(also_lock=)`); Task 9 (`list_instances`, `instance_out`, `definition_out`); Task 10 helpers (`_instance_out`); Task 5 (`lock_instances`, `check_amounts`, `template_amounts`); Task 4 (`rules.plain`); Task 2 (`ScheduleDefinition.template_owner_edited`).
- Produces: `update_instance` (with `scope`), `post_one`, `skip_instance`, `reopen_instance`, `repost_instance`, `accept_partial`, `installment_residual(definition, rows, *, old_amounts, new_amounts, overrides) -> Decimal | None` (R-A1) and the private `_instance_locked(db, instance_id, *, share=True) -> (definition, instance)` and `_apply_amount_scope(db, instance_id, payload)`; `InstanceUpdateIn.scope`; `DefinitionOut.template_owner_edited`; routes `GET /instances`, `PUT /instances/{id}`, `POST /instances/{id}/post|skip|reopen|repost|accept-partial`.

Rules (spec "Instance endpoints", D33, D35): shared key → definition (`FOR SHARE`; `reopen` takes `FOR UPDATE` because it may revive the definition; `post` and `repost` go through `posting.lock_definition_for_post`, `FOR UPDATE` with a loan line) → instance `FOR UPDATE`. `PUT` only on pending (409), `amounts` aligned (422 `amounts`), a `due_date` equal to the date of another non-skipped (posted or pending) instance of the same definition → 422 `due_date` (two pending periods on one day would collide on `ux_schedule_instance_posted_day`), `rule_date` never changes, `edited_by_owner = true`. `post`: `acted_by = owner`; any failure is stored in `last_error` (second transaction): a validation failure is answered 422 with the field, `ConflictError` / `NotFoundError` / `EditLockedError` pass through unrecorded, any other exception is recorded by class name and answered 409. `skip`: pending only, writes nothing. `reopen`: 409 when already pending; posted → its entries are deleted (ledger lock order, cutover lock applies) with note `入帳記錄已於 YYYY-MM-DD 刪除`; skipped → pending; both set `reopened_at`; an ended definition becomes active; allowed even when the template references an archived row. `repost`: posted only (409), refused with 409 `locked_until_cutover` before cutover for `acted_by = import`; deletes the period's entries and posts again with the new override in one transaction; the definition is locked through `posting.lock_definition_for_post` (`FOR UPDATE` with a loan line, since the repost may end it), the later pending instances through `posting.lock_later_pending` before any entry lock, and the loan entry joins the period's single entry-lock statement (`delete_period_entries(also_lock=loan_id)`), which `post_locked(locked_loan=…)` reuses. `accept-partial`: posted and partial only (409); `is_partial = false`, note kept. `GET /instances?from=&until=&status=&definition_id=&queue=` with `status` `pending | posted | skipped | all` (default `pending`).

Amount edit scope (spec "Instance amount edit scope", D35, proposal decision 24): `PUT /instances/{id}` takes `scope` — `this` (default; the rules above), `following`, `all`. A scope other than `this` needs `amounts` and no `due_date` (422 `scope`), amounts aligned (422 `amounts`) and each > 0 (422 `amounts`: they become template amounts), a pending instance (409), and for an installment with `total_amount` and `times` a residual above 0 (below). Lock order: key → definition `FOR UPDATE` → `lock_instances` (every instance, ascending id). One transaction: `following` first gives each earlier pending instance (`seq` below this one) without an override the template amounts before the edit; then the template's line amounts become the new ones and `template_owner_edited = true`; every pending instance from this `seq` on (`following`) or every pending instance (`all`) loses its override unless it is owner-edited; this instance follows the template, or, when it was already owner-edited, takes the new amounts as its override (it stays owner-edited). ★Installment total (Multica R-A1, D35): for an installment with `total_amount` and `times`, the pending last period (`seq == times`) gets `[residual, *other line amounts]`, where `residual = installment_residual(definition, rows, old_amounts=…, new_amounts=…, overrides=…)` (pure, computed before anything is written) = total − Σ of the first line's effective amount of every other period — posted (its override, else the template before this edit), skipped excluded, pending after the scope step (preserved owner overrides, the old price `following` gives earlier periods, MOZE's amounts on imported rows, else the new template), ungenerated (the new template); a residual ≤ 0 (the others already reach the total) → 422 `amounts` with a message carrying no amount, and nothing changes. This applies to local and imported installments alike and replaces the earlier `last_period_override` reuse: `generation.last_period_override` (landed in Task 6) is not used for scoped edits — it stays for the generation path, so a last period that is not generated yet (a loan longer than the 13-month horizon) is written later by generation with `total − amount × (times − 1)` (recorded under Known Spec Conflicts). Posted and skipped instances never change. No cutover check: amounts are not rule fields, so imported definitions accept the edit before cutover (Task 10's `PUT` / `DELETE` lock stays).

- [ ] 12.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_instances_api.py`:

```python
"""Instance endpoints (spec "Instance endpoints", "Imported definitions before cutover")."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_posting as posting
from app.services import settlement_service
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def _open(db, entry_id) -> Decimal:
    db.expire_all()
    return settlement_service.open_amount(db, db.get(LedgerEntry, entry_id))


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    return SimpleNamespace(bank=bank, lender=lender, payable=payable, definition=definition)


def test_queue_endpoint(client, db_session, seed, today):
    # Spec "待完成交易 queue" through HTTP.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm", times=12)
    overdue = seed.instance(rent, 4, date(2026, 9, 30))
    later = seed.instance(rent, 5, date(2026, 10, 20))
    db_session.commit()
    items = client.get("/schedules/instances", params={"queue": "true"}).json()
    assert [(item["id"], item["overdue_days"]) for item in items] == [(overdue.id, 3), (later.id, 0)]
    assert Decimal(items[0]["lines"][0]["amount"]) == Decimal("-18000")
    assert client.get("/schedules/instances", params={"status": "all", "until": "2026-12-31"}).status_code == 200


def test_override_length_checked(client, db_session, seed, loan, today):
    # Spec "Override length checked".
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    response = client.put(f"/schedules/instances/{instance.id}", json={"amounts": ["8333"]})
    assert response.status_code == 422 and _fields(response) == {"amounts"}


def test_instance_edit_moves_the_date_and_marks_the_owner(client, db_session, seed, loan, today):
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    response = client.put(f"/schedules/instances/{instance.id}", json={"due_date": "2026-11-12", "amounts": ["9000", "600"]})
    assert response.status_code == 200
    row = _row(db_session, instance.id)
    assert (row.due_date, row.rule_date, row.amount_override, row.edited_by_owner) == (
        date(2026, 11, 12), date(2026, 11, 9), ["9000", "600"], True,
    )


def test_date_edit_onto_a_posted_day_refused(client, db_session, seed, today):
    # Spec "Date edit onto a posted day refused".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    weekly = seed.definition([seed.line("expense", card, "100")], interval_unit="week", anchor=date(2026, 10, 5))
    entry = seed.entry(card, "-100", day=date(2026, 10, 5), source="schedule")
    seed.instance(weekly, 1, date(2026, 10, 5), status="posted", entries=[entry])
    pending = seed.instance(weekly, 2, date(2026, 10, 12))
    db_session.commit()
    response = client.put(f"/schedules/instances/{pending.id}", json={"due_date": "2026-10-05"})
    assert response.status_code == 422 and _fields(response) == {"due_date"}


def test_post_endpoint_posts_and_a_second_post_is_409(client, db_session, seed, loan, today):
    # Spec "Posting twice is refused" through HTTP.
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    first = client.post(f"/schedules/instances/{instance.id}/post")
    assert (first.status_code, first.json()["status"], first.json()["acted_by"]) == (200, "posted", "owner")
    count = db_session.scalar(select(func.count()).select_from(LedgerEntry))
    second = client.post(f"/schedules/instances/{instance.id}/post")
    assert (second.status_code, second.json()["message"]) == (409, "already_posted")
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == count


def test_skip_leaves_the_loan_open(client, db_session, seed, loan, today):
    # Spec "Skip leaves the loan open".
    today(date(2026, 12, 1))
    seed.entry(loan.bank, "-8333", kind="payable", counterparty_id=loan.lender.id, settles_entry_id=loan.payable.id,
               is_settlement=True)
    instance = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    assert _open(db_session, loan.payable.id) == Decimal("291667")
    response = client.post(f"/schedules/instances/{instance.id}/skip")
    assert (response.status_code, response.json()["status"], response.json()["acted_by"]) == (200, "skipped", "owner")
    assert _open(db_session, loan.payable.id) == Decimal("291667")
    assert client.post(f"/schedules/instances/{instance.id}/skip").status_code == 409


def test_reopen_revives_an_ended_definition(client, db_session, seed, today):
    # Spec "Reopen revives an ended definition".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    ended = seed.definition([seed.line("expense", card, "390")], status="ended", times=1)
    last = seed.instance(ended, 1, date(2026, 9, 22), status="skipped", acted_by="owner")
    db_session.commit()
    response = client.post(f"/schedules/instances/{last.id}/reopen")
    assert (response.status_code, response.json()["status"], response.json()["reopened"]) == (200, "pending", True)
    db_session.expire_all()
    assert db_session.get(ScheduleDefinition, ended.id).status == "active"
    assert client.post(f"/schedules/instances/{last.id}/reopen").status_code == 409


def test_archived_account_found_at_posting_time(client, db_session, seed, today):
    # Spec "Archived account found at posting time".
    today(date(2026, 10, 3))
    card, old = seed.account("範例卡"), seed.account("舊帳戶")
    ended = seed.definition([seed.line("expense", card, "390"), seed.line("expense", old, "20")], status="ended")
    skipped = seed.instance(ended, 1, date(2026, 9, 22), status="skipped", acted_by="owner")
    old.is_archived = True
    db_session.commit()

    assert client.post(f"/schedules/instances/{skipped.id}/reopen").status_code == 200
    posted = client.post(f"/schedules/instances/{skipped.id}/post")

    assert posted.status_code == 422 and _fields(posted) == {"lines[1].account_id"}
    row = _row(db_session, skipped.id)
    assert row.status == "pending" and row.last_error.startswith("lines[1].account_id")
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule")) == 0


def test_reopen_of_a_posted_period_deletes_its_entries(client, db_session, seed, loan, today):
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    client.post(f"/schedules/instances/{instance.id}/post")
    response = client.post(f"/schedules/instances/{instance.id}/reopen")
    assert (response.status_code, response.json()["status"], response.json()["note"]) == (
        200, "pending", "入帳記錄已於 2026-11-09 刪除",
    )
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "schedule")) == 0
    assert _open(db_session, loan.payable.id) == Decimal("300000")


def test_repost_a_repayment_with_a_corrected_amount(client, db_session, seed, loan, today):
    # Spec "Repost a repayment with a corrected amount".
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db_session, instance.id, actor="auto")
    db_session.commit()

    response = client.post(f"/schedules/instances/{instance.id}/repost", json={"amounts": ["8333", "598"]})

    assert (response.status_code, response.json()["status"]) == (200, "posted")
    db_session.expire_all()
    assert all(db_session.get(LedgerEntry, entry_id) is None for entry_id in result.entry_ids)
    new_ids = response.json()["posted_entry_ids"]
    assert sorted(db_session.get(LedgerEntry, entry_id).amount for entry_id in new_ids) == [Decimal("-8333"), Decimal("-598")]
    assert response.json()["edited_by_owner"] is True
    other = seed.instance(loan.definition, 2, date(2026, 12, 9))
    db_session.commit()
    assert client.post(f"/schedules/instances/{other.id}/repost", json={"amounts": ["1", "1"]}).status_code == 409


def test_repost_of_a_moze_booked_period_refused_before_cutover(client, db_session, seed, today, monkeypatch):
    # Spec "Repost of a MOZE-booked period refused before cutover".
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    card = seed.account("範例卡")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="moze_backup", moze_id="R-9")
    booked = seed.instance(imported, 1, date(2026, 9, 22), status="posted", entries=[entry], acted_by="import", moze_id="R-9")
    db_session.commit()
    response = client.post(f"/schedules/instances/{booked.id}/repost", json={"amounts": ["400"]})
    assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")


def test_accept_a_partial_period(client, db_session, seed, loan, today):
    # Spec "Accept a partial period".
    today(date(2026, 11, 10))
    repayment = seed.entry(loan.bank, "-8333", kind="payable", counterparty_id=loan.lender.id,
                           settles_entry_id=loan.payable.id, is_settlement=True, day=date(2026, 11, 9), source="schedule")
    partial = seed.instance(loan.definition, 1, date(2026, 11, 9), status="posted", entries=[repayment],
                            is_partial=True, note="部分入帳記錄已於 2026-11-10 刪除")
    db_session.commit()
    assert partial.id in [item["id"] for item in client.get("/schedules/instances", params={"queue": "true"}).json()]

    response = client.post(f"/schedules/instances/{partial.id}/accept-partial")

    assert (response.status_code, response.json()["is_partial"], response.json()["note"]) == (
        200, False, "部分入帳記錄已於 2026-11-10 刪除",
    )
    assert db_session.get(LedgerEntry, repayment.id) is not None
    assert partial.id not in [item["id"] for item in client.get("/schedules/instances", params={"queue": "true"}).json()]
    assert client.post(f"/schedules/instances/{partial.id}/accept-partial").status_code == 409


def test_instance_writes_are_refused_while_an_import_runs(client, db_session, seed, loan, today, pg_engine):
    today(date(2026, 10, 3))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            responses = [client.post(f"/schedules/instances/{instance.id}/{action}") for action in ("skip", "post")]
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert [(response.status_code, response.json()["message"]) for response in responses] == [(409, "import_running")] * 2
    assert _row(db_session, instance.id).status == "pending"


def test_date_edit_onto_another_pending_period_refused(client, db_session, seed, today):
    # Two pending periods on one day would collide on ux_schedule_instance_posted_day when the second posts.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    weekly = seed.definition([seed.line("expense", card, "100")], interval_unit="week", anchor=date(2026, 10, 5))
    seed.instance(weekly, 1, date(2026, 10, 5))
    second = seed.instance(weekly, 2, date(2026, 10, 12))
    db_session.commit()
    response = client.put(f"/schedules/instances/{second.id}", json={"due_date": "2026-10-05"})
    assert response.status_code == 422 and _fields(response) == {"due_date"}
    assert _row(db_session, second.id).due_date == date(2026, 10, 12)


def test_post_records_any_error_and_answers_409(client, db_session, seed, today, monkeypatch):
    # Spec "On any error … a separate transaction SHALL store the error": a non-validation error is stored by class.
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    netflix = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(posting, "post_locked", boom)
    response = client.post(f"/schedules/instances/{instance.id}/post")

    assert (response.status_code, response.json()["message"]) == (409, "RuntimeError")
    row = _row(db_session, instance.id)
    assert (row.status, row.last_error) == ("pending", "RuntimeError")


# --- Amount edit scope (spec "Instance amount edit scope", proposal decision 24) ---


@pytest.fixture()
def netflix(seed):
    """Monthly 390: seq 1 posted, seq 2–4 pending without overrides."""
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 9, 22))
    entry = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    first = seed.instance(definition, 1, date(2026, 9, 22), status="posted", entries=[entry])
    rows = [seed.instance(definition, seq, day) for seq, day in (
        (2, date(2026, 10, 22)), (3, date(2026, 11, 22)), (4, date(2026, 12, 22)),
    )]
    return SimpleNamespace(card=card, definition=definition, first=first, rows=rows)


def _template(db, definition_id) -> tuple[list[str], bool]:
    db.expire_all()
    definition = db.get(ScheduleDefinition, definition_id)
    return [line["amount"] for line in definition.template["lines"]], definition.template_owner_edited


def _overrides(db, rows) -> list[tuple]:
    return [(_row(db, row.id).amount_override, _row(db, row.id).edited_by_owner) for row in rows]


def test_only_this_period_by_default(client, db_session, netflix, today):
    # Spec "Only this period by default".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.put(f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"]})
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(None, False), (["420"], True), (None, False)]
    assert _template(db_session, netflix.definition.id) == (["390"], False)


def test_this_period_and_the_following_ones_keep_the_old_price_before(client, db_session, netflix, today):
    # Spec "This period and the following ones keep the old price before".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.put(
        f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"], "scope": "following"}
    )
    assert (response.status_code, response.json()["amounts"]) == (200, ["420"])
    assert _overrides(db_session, netflix.rows) == [(["390"], False), (None, False), (None, False)]
    assert _template(db_session, netflix.definition.id) == (["420"], True)
    first = _row(db_session, netflix.first.id)
    assert (first.status, first.amount_override) == ("posted", None)
    detail = client.get(f"/schedules/definitions/{netflix.definition.id}").json()
    assert detail["template_owner_edited"] is True
    pending = [item["amounts"] for item in detail["instances"] if item["status"] == "pending"]
    assert pending == [["390"], ["420"], ["420"]]  # seq 2 keeps the old price; seq 3 and 4 follow the template


def test_following_replaces_the_edited_periods_own_override_and_keeps_later_owner_edits(
    client, db_session, netflix, today
):
    today(date(2026, 10, 3))
    netflix.rows[1].amount_override, netflix.rows[1].edited_by_owner = ["405"], True
    netflix.rows[2].amount_override, netflix.rows[2].edited_by_owner = ["400"], True
    db_session.commit()
    response = client.put(
        f"/schedules/instances/{netflix.rows[1].id}", json={"amounts": ["420"], "scope": "following"}
    )
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(["390"], False), (["420"], True), (["400"], True)]


def test_all_periods_keep_other_owner_edits(client, db_session, netflix, today):
    # Spec "All periods keep other owner edits".
    today(date(2026, 10, 3))
    netflix.rows[2].amount_override, netflix.rows[2].edited_by_owner = ["400"], True
    db_session.commit()
    response = client.put(f"/schedules/instances/{netflix.rows[0].id}", json={"amounts": ["420"], "scope": "all"})
    assert response.status_code == 200
    assert _overrides(db_session, netflix.rows) == [(None, False), (None, False), (["400"], True)]
    assert _template(db_session, netflix.definition.id) == (["420"], True)
    assert _row(db_session, netflix.first.id).status == "posted"


def test_a_scope_with_another_field_is_refused(client, db_session, netflix, today):
    # Spec "A scope with another field is refused"; amounts become template amounts, so "0" is refused too.
    today(date(2026, 10, 3))
    db_session.commit()
    target = netflix.rows[0].id
    for body in (
        {"amounts": ["420"], "due_date": "2026-10-23", "scope": "following"},
        {"due_date": "2026-10-23", "scope": "all"},
    ):
        response = client.put(f"/schedules/instances/{target}", json=body)
        assert response.status_code == 422 and _fields(response) == {"scope"}
    zero = client.put(f"/schedules/instances/{target}", json={"amounts": ["0"], "scope": "all"})
    assert zero.status_code == 422 and _fields(zero) == {"amounts"}
    posted = client.put(f"/schedules/instances/{netflix.first.id}", json={"amounts": ["420"], "scope": "all"})
    assert posted.status_code == 409
    assert _template(db_session, netflix.definition.id) == (["390"], False)
    row = _row(db_session, target)
    assert (row.due_date, row.amount_override, row.edited_by_owner) == (date(2026, 10, 22), None, False)


def test_scope_edit_allowed_on_an_imported_definition_before_cutover(client, db_session, seed, today, monkeypatch):
    # Spec "Imported definition before cutover": amounts are not rule fields.
    today(date(2026, 10, 3))
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "false")
    card = seed.account("範例卡")
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1")
    pending = seed.instance(imported, 1, date(2026, 10, 22), moze_id="R-1", amount_override=["390"])
    db_session.commit()
    response = client.put(f"/schedules/instances/{pending.id}", json={"amounts": ["420"], "scope": "all"})
    assert response.status_code == 200
    assert _template(db_session, imported.id) == (["420"], True)
    assert _row(db_session, pending.id).amount_override is None
    refused = client.delete(f"/schedules/definitions/{imported.id}")
    assert (refused.status_code, refused.json()["message"]) == (409, "locked_until_cutover")


def test_installment_scope_keeps_the_last_period_remainder(client, db_session, seed, loan, today):
    today(date(2026, 10, 3))
    before_last = seed.instance(loan.definition, 35, date(2029, 9, 9))
    last = seed.instance(loan.definition, 36, date(2029, 10, 9), amount_override=["8345", "620"])
    db_session.commit()
    too_much = client.put(
        f"/schedules/instances/{before_last.id}", json={"amounts": ["9000", "598"], "scope": "following"}
    )
    assert too_much.status_code == 422 and _fields(too_much) == {"amounts"}  # 9000 × 35 ≥ 300000
    response = client.put(
        f"/schedules/instances/{before_last.id}", json={"amounts": ["8333", "598"], "scope": "following"}
    )
    assert response.status_code == 200
    assert _template(db_session, loan.definition.id) == (["8333", "598"], True)
    assert _row(db_session, before_last.id).amount_override is None
    assert _row(db_session, last.id).amount_override == ["8345", "598"]  # remainder of 300000 − 35 × 8333, new interest


def _card_installment(seed, times, amount, total, *, posted=()):
    """A local expense installment; seqs in `posted` are posted at the template amount, the others pending."""
    card = seed.account("範例卡")
    definition = seed.definition(
        [seed.line("expense", card, amount)], kind="installment", name="分期", anchor=date(2026, 9, 15), times=times,
        total_amount=Decimal(total),
    )
    rows = []
    for seq in range(1, times + 1):
        day = date(2026, 8 + seq, 15) if 8 + seq <= 12 else date(2027, 8 + seq - 12, 15)
        if seq in posted:
            entry = seed.entry(card, f"-{amount}", day=day, source="schedule")
            rows.append(seed.instance(definition, seq, day, status="posted", entries=[entry]))
        else:
            rows.append(seed.instance(definition, seq, day))
    return SimpleNamespace(definition=definition, rows=rows)


def _first_amounts(db, rows) -> list[str]:
    db.expire_all()
    definition = db.get(ScheduleDefinition, rows[0].definition_id)
    template = definition.template["lines"][0]["amount"]
    return [
        (_row(db, row.id).amount_override or [template])[0] for row in rows
    ]


def test_installment_following_keeps_the_total_against_the_actual_allocations(client, db_session, seed, today):
    # Multica R-A1: 10,000 over 3 (3,333 / 3,333 / 3,334); 這一期與之後 from the second to 3,000 must give
    # 3,333 / 3,000 / 3,667 = 10,000 — never 3,333 / 3,000 / 4,000.
    today(date(2026, 9, 1))
    plan = _card_installment(seed, 3, "3333", "10000")
    plan.rows[2].amount_override = ["3334"]
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[1].id}", json={"amounts": ["3000"], "scope": "following"})
    assert response.status_code == 200
    amounts = _first_amounts(db_session, plan.rows)
    assert amounts == ["3333", "3000", "3667"] and sum(Decimal(value) for value in amounts) == Decimal("10000")


def test_installment_scope_counts_posted_and_owner_edited_periods(client, db_session, seed, today):
    # Multica R-A1: a principal change after a posted and an owner-edited period; 全部週期 from the third.
    today(date(2026, 10, 1))
    plan = _card_installment(seed, 4, "2500", "10000", posted={1})
    plan.rows[1].amount_override, plan.rows[1].edited_by_owner = ["2000"], True
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[2].id}", json={"amounts": ["2600"], "scope": "all"})
    assert response.status_code == 200
    # seq 1 posted at 2,500 (no override); 2,500 + 2,000 + 2,600 + 2,900 = 10,000
    assert [_row(db_session, row.id).amount_override for row in plan.rows] == [None, ["2000"], None, ["2900"]]
    assert _template(db_session, plan.definition.id) == (["2600"], True)


def test_installment_scope_that_would_overbook_is_refused(client, db_session, seed, today):
    # Multica R-A1: the old per-period check (4,000 × 2 < 10,000) passes, but the posted 6,000 makes Σ reach the total.
    today(date(2026, 10, 1))
    plan = _card_installment(seed, 3, "3333", "10000", posted={1})
    plan.rows[0].amount_override = ["6000"]
    db_session.commit()
    response = client.put(f"/schedules/instances/{plan.rows[1].id}", json={"amounts": ["4000"], "scope": "following"})
    assert response.status_code == 422 and _fields(response) == {"amounts"}
    assert "4000" not in response.text and "6000" not in response.text
    assert _template(db_session, plan.definition.id) == (["3333"], False)
```

- [ ] 12.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_instances_api.py
```

Expected: 25 failures (404 / 405 on `/schedules/instances…`).

- [ ] 12.3 Schema and read shape, then the service.

  In `services/accounting-service/app/schemas/schedules.py`, replace the `InstanceUpdateIn` class with

```python
class InstanceUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date | None = None
    amounts: list[NonNegativeMoney] | None = None
    # 套用範圖 (proposal decision 24): 僅這一期 / 這一期與之後 / 全部週期
    scope: Literal["this", "following", "all"] = "this"
```

  and in `DefinitionOut` add the field `template_owner_edited: bool` directly below `created_locally: bool`. In `services/accounting-service/app/services/schedule_read.py` (`definition_out`), replace

```python
        "created_locally": definition.created_locally, "imported": definition.moze_id is not None,
```

  with

```python
        "created_locally": definition.created_locally, "template_owner_edited": definition.template_owner_edited,
        "imported": definition.moze_id is not None,
```

  In `services/accounting-service/app/services/schedule_service.py`, add `import copy` above `from datetime import …` and extend the templates import to `from .schedule_templates import check_amounts, loan_line, normalize_template, realign_override, template_amounts, validate_template`. Then append:

```python
def _instance_locked(db: Session, instance_id: int, *, share: bool = True) -> tuple[ScheduleDefinition, ScheduleInstance]:
    """Shared import key → definition (FOR SHARE, or FOR UPDATE when the action may change it) → instance FOR UPDATE."""
    take_import_key_shared(db)
    definition = lock_definition(db, get_instance(db, instance_id).definition_id, share=share)
    instance = lock_instance(db, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    return definition, instance


def update_instance(db: Session, instance_id: int, payload: InstanceUpdateIn) -> None:
    """編輯這一筆 on a pending period: date and / or amounts; rule_date never changes. A scope other than `this`
    (這一期與之後 / 全部週期, proposal decision 24) is the amounts-only path of _apply_amount_scope."""
    if payload.scope != "this":
        _apply_amount_scope(db, instance_id, payload)
        return
    definition, instance = _instance_locked(db, instance_id)
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以修改")
    if payload.amounts is not None:
        instance.amount_override = check_amounts(definition.template, payload.amounts)
    if payload.due_date is not None:
        taken = db.scalar(
            select(ScheduleInstance.status)
            .where(
                ScheduleInstance.definition_id == definition.id,
                ScheduleInstance.id != instance.id,
                ScheduleInstance.status != "skipped",
                ScheduleInstance.due_date == payload.due_date,
            )
            .order_by(ScheduleInstance.status.desc())  # 'posted' first
            .limit(1)
        )
        if taken == "posted":
            raise ValidationError("due_date", "這一天已有入帳的期別")
        if taken is not None:
            # two pending periods on one day would collide on ux_schedule_instance_posted_day when both post
            raise ValidationError("due_date", "這一天已有另一期待入帳")
        instance.due_date = payload.due_date
    instance.edited_by_owner = True
    db.flush()


def installment_residual(
    definition: ScheduleDefinition,
    rows: list[ScheduleInstance],
    *,
    old_amounts: list[str],
    new_amounts: list[str],
    overrides: dict[int, list[str] | None],
) -> Decimal | None:
    """Multica R-A1 (D35): the first line's amount of the last period (seq == times) of an installment with a total,
    computed against the ACTUAL allocations of every other period, so a scoped edit keeps Σ = total_amount:

    - posted: the amount it was posted with as the schedule records it (its override, else the template amount
      before this edit, `old_amounts`);
    - skipped: excluded (nothing was written);
    - pending: its override after the edit (`overrides[row.id]` when the edit sets it, else the stored one —
      preserved owner overrides, the old price that `following` gives the earlier periods, MOZE's amounts on imported
      rows), else the new template amount (`new_amounts`);
    - ungenerated (no row for that seq): the new template amount.

    Pure over the given rows (nothing is written), so a refusal leaves the session untouched. None when the
    definition is not an installment with a total and times. When the others already reach the total the edit is
    refused: ValidationError("amounts") — the message carries no amount.
    `generation.last_period_override` (Task 6) is NOT used for scoped edits; it stays for the generation path."""
    if definition.kind != "installment" or definition.total_amount is None or definition.times is None:
        return None
    new_first = Decimal(new_amounts[0])
    by_seq = {row.seq: row for row in rows}
    spent = Decimal(0)
    for seq in range(1, definition.times):
        row = by_seq.get(seq)
        if row is None:
            spent += new_first
            continue
        if row.status == "skipped":
            continue
        override = overrides[row.id] if row.id in overrides else row.amount_override
        if override is not None:
            spent += Decimal(override[0])
        elif row.status == "posted":
            spent += Decimal(old_amounts[0])
        else:
            spent += new_first
    last = Decimal(definition.total_amount) - spent
    if last <= 0:
        raise ValidationError("amounts", "其他期別的金額已達總額")
    return last


def _apply_amount_scope(db: Session, instance_id: int, payload: InstanceUpdateIn) -> None:
    """這一期與之後 (`following`) / 全部週期 (`all`), spec "Instance amount edit scope".

    The new amounts become the template's (template_owner_edited, so re-imports keep them, D37); every pending period
    from this one (following) or every pending period (all) follows the template, except owner-edited ones other
    than this; with `following` the earlier pending periods keep the old price as an override. Posted and skipped
    periods never change. An installment with a total gives its pending last period the residual of
    installment_residual (R-A1). Lock order (D32): key → definition FOR UPDATE → every instance FOR UPDATE,
    ascending id. No cutover check: amounts are not rule fields (update_definition / delete_definition keep their
    lock)."""
    if payload.amounts is None or payload.due_date is not None:
        raise ValidationError("scope", "套用到其他期別時只能修改金額")
    take_import_key_shared(db)
    definition = lock_definition(db, get_instance(db, instance_id).definition_id)
    rows = lock_instances(db, definition.id)
    instance = next((row for row in rows if row.id == instance_id), None)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以修改")
    amounts = check_amounts(definition.template, payload.amounts)
    if any(Decimal(value) == 0 for value in amounts):
        raise ValidationError("amounts", "套用到其他期別時每一行金額須大於 0")
    pending = [row for row in rows if row.status == "pending"]
    old_amounts = template_amounts(definition.template)
    planned: dict[int, list[str] | None] = {}  # the overrides this edit writes; computed before anything changes
    for row in pending:
        if payload.scope == "following" and row.seq < instance.seq:
            if row.amount_override is None:
                planned[row.id] = list(old_amounts)  # the earlier periods keep the old price
        elif row.id == instance.id:
            planned[row.id] = list(amounts) if row.edited_by_owner else None
        elif not row.edited_by_owner:
            planned[row.id] = None
    residual = installment_residual(  # 422 `amounts` when the others reach the total; nothing written yet
        definition, rows, old_amounts=old_amounts, new_amounts=amounts, overrides=planned
    )
    for row in pending:
        if row.id in planned:
            row.amount_override = planned[row.id]
    template = copy.deepcopy(definition.template)
    for line, amount in zip(template["lines"], amounts):
        line["amount"] = amount
    definition.template, definition.template_owner_edited = template, True
    last = next((row for row in pending if definition.times is not None and row.seq == definition.times), None)
    if residual is not None and last is not None:
        # The last pending period always closes the total (local or imported); its other lines keep a preserved
        # owner override, else take the new amounts. An ungenerated last period is written later by generation.
        keep = last.edited_by_owner and last.id != instance.id and last.amount_override is not None
        rest = last.amount_override[1:] if keep else amounts[1:]
        last.amount_override = [rules.plain(residual), *rest]
    db.flush()


def post_one(db: Session, instance_id: int) -> None:
    """[入帳]: acted_by owner. Any failure is stored as last_error (own transaction): a validation failure is
    re-raised (422); conflicts, a missing row and the cutover lock pass through unrecorded; any other error
    (IntegrityError, a deadlock's OperationalError) is recorded by class name and answered 409."""
    try:
        posting.post_instance(db, instance_id, actor="owner")
    except ValidationError as exc:
        posting.record_failure(db, instance_id, posting.failure_message(exc))
        raise
    except (ConflictError, NotFoundError, EditLockedError):
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 — spec: on any error a separate transaction stores last_error
        message = posting.failure_message(exc)
        posting.record_failure(db, instance_id, message)
        raise ConflictError(message) from exc


def skip_instance(db: Session, instance_id: int) -> None:
    """[略過]: no entry is written, so a loan's open amount stays (略過這一期？剩餘不變)."""
    _, instance = _instance_locked(db, instance_id)
    if instance.status != "pending":
        raise ConflictError("只有待入帳的期別可以略過")
    instance.status, instance.acted_at, instance.acted_by = "skipped", _now(), "owner"
    db.flush()


def reopen_instance(db: Session, instance_id: int) -> None:
    definition, instance = _instance_locked(db, instance_id, share=False)
    if instance.status == "pending":
        raise ConflictError("這一期已是待入帳")
    if instance.status == "posted":
        posting.delete_period_entries(db, list(instance.posted_entry_ids))
        instance.note = f"入帳記錄已於 {_today().isoformat()} 刪除"
    instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
    instance.acted_at, instance.acted_by, instance.reopened_at = None, None, _now()
    if definition.status == "ended":
        definition.status = "active"
    db.flush()


def repost_instance(db: Session, instance_id: int, amounts: list) -> None:
    """編輯這一筆 on a posted (or partial) period: delete its entries and post again with new amounts, atomically.

    Lock order (D32): key → definition (lock_definition_for_post: FOR UPDATE with a loan line, since the repost may
    end it) → instance → the later pending instances (loan templates) → groups → the period's entries **and the
    loan** in one statement (delete_period_entries(also_lock=…)); post_locked reuses that loan row, so no second
    entry-lock statement runs."""
    take_import_key_shared(db)
    definition = posting.lock_definition_for_post(db, get_instance(db, instance_id).definition_id)
    instance = lock_instance(db, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    if instance.status != "posted":
        raise ConflictError("只有已入帳的期別可以重新入帳")
    if instance.acted_by == "import" and not import_locked():
        raise EditLockedError()
    override = check_amounts(definition.template, amounts)
    reference = loan_line(definition.template)
    loan_id = None
    if reference is not None:
        posting.lock_later_pending(db, definition, instance)  # schedule rows before any entry lock
        loan_id = reference[1]["loan_entry_id"]
    loan = posting.delete_period_entries(db, list(instance.posted_entry_ids), also_lock=loan_id)
    instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
    instance.acted_at, instance.acted_by = None, None
    instance.amount_override, instance.edited_by_owner = override, True
    db.flush()
    posting.post_locked(db, definition, instance, "owner", locked_loan=loan)


def accept_partial(db: Session, instance_id: int) -> None:
    """保留部分: keep what is left of the period; the note stays."""
    _, instance = _instance_locked(db, instance_id)
    if instance.status != "posted" or not instance.is_partial:
        raise ConflictError("只有部分入帳的期別可以保留部分")
    instance.is_partial = False
    db.flush()
```

- [ ] 12.4 Append to `services/accounting-service/app/routers/schedules.py`:

```python
@router.get("/instances", response_model=list[InstanceOut])
def list_instances(
    date_from: date | None = Query(default=None, alias="from"),
    until: date | None = None,
    status: Literal["pending", "posted", "skipped", "all"] = "pending",
    definition_id: int | None = None,
    queue: bool = False,
    db: Session = Depends(get_db),
):
    return schedule_read.list_instances(
        db, date_from=date_from, until=until, status=None if status == "all" else status,
        definition_id=definition_id, queue=queue,
    )


@router.put("/instances/{instance_id}", response_model=InstanceOut)
def put_instance(instance_id: int, payload: InstanceUpdateIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.update_instance(db, instance_id, payload)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/post", response_model=InstanceOut)
def post_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.post_one(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/skip", response_model=InstanceOut)
def skip_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.skip_instance(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/reopen", response_model=InstanceOut)
def reopen_instance(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.reopen_instance(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/repost", response_model=InstanceOut)
def repost_instance(instance_id: int, payload: RepostIn, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.repost_instance(db, instance_id, payload.amounts)
    db.commit()
    return _instance_out(db, instance_id)


@router.post("/instances/{instance_id}/accept-partial", response_model=InstanceOut)
def accept_partial(instance_id: int, db: Session = Depends(get_db)):
    with service_errors():
        schedule_service.accept_partial(db, instance_id)
    db.commit()
    return _instance_out(db, instance_id)
```

- [ ] 12.5 Run 12.2 again, then every schedule file so far.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_instances_api.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_definitions_api.py tests/integration/test_schedule_state_api.py tests/integration/test_schedule_read.py 2>&1 | tail -1
```

Expected: `25 passed`; then `40 passed` (19 definitions + 12 state + 9 read).

- [ ] 12.6 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_service.py services/accounting-service/app/routers/schedules.py \
  services/accounting-service/app/schemas/schedules.py services/accounting-service/app/services/schedule_read.py \
  services/accounting-service/tests/integration/test_schedule_instances_api.py
git commit -m "feat(accounting): schedule instance endpoints: edit (with scope), post, skip, reopen, repost, accept-partial

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 13. Entry delete hook and reference protection

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_entry_hooks.py`
- Modify: `services/accounting-service/app/services/entry_write_service.py` (lock-order comment; `delete_entry`)
- Modify: `services/accounting-service/app/services/split_service.py` (`update_split`, `delete_split`)
- Modify: `services/accounting-service/app/services/settings_service.py` (archive / delete guards)
- Test: `services/accounting-service/tests/integration/test_schedule_entry_hooks.py`

**Interfaces:**
- Consumes: `take_import_key_shared`, `lock_definition`, `lock_instance` (Task 5); `ledger_service._today`; `schedule_posting.auto_eligible` (tests); `schedule_read.list_instances` (tests).
- Produces: `lock_for_entry_delete(db, entry_ids) -> ScheduleInstance | None`, `after_entries_deleted(db, instance, deleted_ids)`, `definitions_referencing(db, **ids)`, `assert_not_referenced(db, **ids)`, `assert_group_not_scheduled(db, group_id)`; the rewritten lock-order comment above `locked_with_legs`.

Rules (D33, spec "Deleting entries of a posted period", "Rows referenced by definitions", ledger "Schedule-sourced entries"):
- `delete_entry` / `delete_split` first read (unlocked) the posted instance listing the entry, then take the shared import key, the definition `FOR SHARE` and the instance `FOR UPDATE`, re-check containment, and only then take their existing group → entries locks. A definition that ended between the unlocked read and the `FOR SHARE` lock (a loan close-out committed first) → 409 with the message `排程剛結束，請重試刪除` (`DEFINITION_ENDED_RETRY`; the SPA shows a delete's 409 message as is); the retry takes the definition `FOR UPDATE` and revives it. After the delete, `after_entries_deleted` removes the deleted top-level ids (both legs of a transfer) from `posted_entry_ids`: entries left → `is_partial = true`, note `部分入帳記錄已於 YYYY-MM-DD 刪除`; none left → `acted_by = import` becomes `skipped` (`入帳記錄已刪除`), any other becomes `pending` with `acted_at` / `acted_by` cleared, `reopened_at = now()`, note `入帳記錄已於 YYYY-MM-DD 刪除`; a pending or partial result revives an `ended` definition.
- While a definition that is not `ended` references a row in its template: archiving an account (`is_archived` false → true), hiding a category, archiving a project, deleting any of them or a counterparty, and `DELETE /entries/{id}` of a loan entry → 409 naming the definition (`排程「<name>」仍在使用，請先結束排程`). `PUT /splits/{group_id}` on a group whose members a posted instance lists → 409 naming the instance (`排程期別 <id>（<name> #<seq>）使用這組記錄，請用編輯這一筆`).

- [ ] 13.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_entry_hooks.py`:

```python
"""The ledger's side of schedules (spec "Deleting entries of a posted period", "Rows referenced by definitions",
ledger "Schedule-sourced entries")."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_posting as posting
from app.services import schedule_read, settlement_service
from app.services.moze_import_service import IMPORT_LOCK_KEY


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    return SimpleNamespace(bank=bank, lender=lender, payable=payable, definition=definition)


def _posted_period(db, seed, loan, today) -> tuple[ScheduleInstance, int, int]:
    today(date(2026, 11, 9))
    instance = seed.instance(loan.definition, 1, date(2026, 11, 9))
    result = posting.post_instance(db, instance.id, actor="auto")
    db.commit()
    repayment_id, interest_id = result.entry_ids
    return instance, repayment_id, interest_id


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def test_deleting_the_interest_leaves_a_partial_period(client, db_session, seed, loan, today):
    # Spec "Deleting the interest leaves a partial period".
    instance, repayment_id, interest_id = _posted_period(db_session, seed, loan, today)
    today(date(2026, 11, 10))

    assert client.delete(f"/entries/{interest_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.is_partial, row.posted_entry_ids, row.note) == (
        "posted", True, [repayment_id], "部分入帳記錄已於 2026-11-10 刪除",
    )
    assert db_session.get(LedgerEntry, repayment_id) is not None
    assert instance.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_deleting_the_last_entry_reopens_the_period(client, db_session, seed, loan, today):
    # Spec "Deleting the last entry reopens the period".
    instance, repayment_id, interest_id = _posted_period(db_session, seed, loan, today)
    today(date(2026, 11, 10))
    client.delete(f"/entries/{interest_id}")

    assert client.delete(f"/entries/{repayment_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.posted_entry_ids, row.acted_at, row.acted_by, row.is_partial) == ("pending", [], None, None, False)
    assert row.reopened_at is not None and row.note == "入帳記錄已於 2026-11-10 刪除"
    assert settlement_service.open_amount(db_session, db_session.get(LedgerEntry, loan.payable.id)) == Decimal("300000")
    definition = db_session.get(ScheduleDefinition, loan.definition.id)
    assert posting.auto_eligible(definition, row, date(2026, 11, 10)) is False


def test_deleting_a_moze_booked_period(client, db_session, seed, today, monkeypatch):
    # Spec "Deleting a MOZE-booked period".
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    today(date(2026, 10, 6))
    pay, broker = seed.account("薪轉"), seed.account("交割")
    pair = uuid.uuid4()
    out_leg = seed.entry(pay, "-15000", kind="transfer_out", transfer_group_id=pair, source="moze_backup", moze_id="R-OUT",
                         day=date(2026, 10, 5))
    in_leg = seed.entry(broker, "15000", kind="transfer_in", transfer_group_id=pair, source="moze_backup", moze_id="R-IN",
                        day=date(2026, 10, 5))
    definition = seed.definition([seed.line("transfer", pay, "15000", to_account_id=broker.id)], created_locally=False,
                                 moze_id="P-1", anchor=date(2026, 10, 5))
    booked = seed.instance(definition, 1, date(2026, 10, 5), status="posted", entries=[out_leg, in_leg], acted_by="import",
                           moze_id="R-OUT")
    db_session.commit()

    assert client.delete(f"/entries/{in_leg.id}").status_code == 204

    row = _row(db_session, booked.id)
    assert (row.status, row.note, row.posted_entry_ids) == ("skipped", "入帳記錄已刪除", [])
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 0


def test_deleting_from_an_ended_definition_revives_it(client, db_session, seed, today):
    # Spec "Deleting from an ended definition revives it".
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    ended = seed.definition([seed.line("expense", card, "390")], status="ended", times=1)
    expense = seed.entry(card, "-390", day=date(2026, 9, 22), source="schedule")
    last = seed.instance(ended, 1, date(2026, 9, 22), status="posted", entries=[expense])
    db_session.commit()

    assert client.delete(f"/entries/{expense.id}").status_code == 204

    assert _row(db_session, last.id).status == "pending"
    assert db_session.get(ScheduleDefinition, ended.id).status == "active"


def test_deleting_one_leg_of_a_scheduled_transfer(client, db_session, seed, today):
    # Ledger spec "Deleting one leg of a scheduled transfer".
    today(date(2026, 10, 5))
    pay, broker = seed.account("薪轉"), seed.account("交割")
    definition = seed.definition([seed.line("transfer", pay, "15000", to_account_id=broker.id)], anchor=date(2026, 10, 5))
    instance = seed.instance(definition, 1, date(2026, 10, 5))
    out_id, in_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()

    assert client.delete(f"/entries/{in_id}").status_code == 204

    row = _row(db_session, instance.id)
    assert (row.status, row.reopened_at is not None) == ("pending", True)
    assert db_session.get(LedgerEntry, out_id) is None and db_session.get(LedgerEntry, in_id) is None


def test_archiving_a_paying_account_is_refused(client, db_session, seed, loan, today):
    # Spec "Archiving a paying account".
    today(date(2026, 10, 3))
    db_session.commit()
    archive = client.put(f"/accounts/{loan.bank.id}", json={"name": "薪轉", "currency": "TWD", "is_archived": True})
    assert archive.status_code == 409 and "信貸 每月還款" in archive.json()["message"]
    remove = client.delete(f"/accounts/{loan.bank.id}")
    assert remove.status_code == 409 and "信貸 每月還款" in remove.json()["message"]


def test_deleting_a_loan_with_an_active_schedule_is_refused(client, db_session, seed, loan, today):
    # Spec "Deleting a loan with an active schedule".
    today(date(2026, 10, 3))
    db_session.commit()
    response = client.delete(f"/entries/{loan.payable.id}")
    assert response.status_code == 409 and "信貸 每月還款" in response.json()["message"]


def test_categories_projects_and_counterparties_are_protected_until_the_schedule_ends(client, db_session, seed, today):
    today(date(2026, 10, 3))
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    life = seed.project("生活")
    alan = seed.counterparty("Alan")
    netflix = seed.definition([seed.line("expense", card, "390", category_id=streaming.id, project_id=life.id)])
    lend = seed.definition([seed.line("receivable", card, "500", counterparty_id=alan.id)], name="代墊")
    db_session.commit()

    for response in (
        client.put(f"/categories/{streaming.id}", json={"kind": "expense", "name": "串流", "is_hidden": True}),
        client.delete(f"/categories/{streaming.id}"),
        client.put(f"/projects/{life.id}", json={"name": "生活", "is_archived": True}),
        client.delete(f"/projects/{life.id}"),
        client.delete(f"/counterparties/{alan.id}"),
    ):
        assert response.status_code == 409

    client.post(f"/schedules/definitions/{netflix.id}/end")
    client.post(f"/schedules/definitions/{lend.id}/end")
    assert client.put(f"/categories/{streaming.id}", json={"kind": "expense", "name": "串流", "is_hidden": True}).status_code == 200
    assert client.delete(f"/counterparties/{alan.id}").status_code == 204


def test_split_edit_on_a_scheduled_installment_group_refused(client, db_session, seed, loan, today):
    # Ledger spec "Split edit on a scheduled group refused", as written: the installment group 信貸 每月還款 #1/36.
    instance, repayment_id, _ = _posted_period(db_session, seed, loan, today)
    group_id = db_session.get(LedgerEntry, repayment_id).group_id

    response = client.put(
        f"/splits/{group_id}",
        json={"entry_date": "2026-11-09", "members": [{"account_id": loan.bank.id, "kind": "expense", "amount": "100"}]},
    )

    assert response.status_code == 409 and str(instance.id) in response.json()["message"]
    assert db_session.get(LedgerEntry, repayment_id) is not None


def test_split_edit_on_a_scheduled_group_refused(client, db_session, seed, today):
    # The same rule for a split-kind schedule group (two plain lines).
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, _ = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    group_id = db_session.get(LedgerEntry, first_id).group_id

    response = client.put(
        f"/splits/{group_id}",
        json={"entry_date": "2026-10-22", "members": [{"account_id": card.id, "kind": "expense", "amount": "100"}]},
    )

    assert response.status_code == 409 and str(instance.id) in response.json()["message"]


def test_deleting_a_scheduled_split_reopens_the_period(client, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    bundle = seed.definition([seed.line("expense", card, "390"), seed.line("expense", card, "149")], name="串流組合")
    instance = seed.instance(bundle, 1, date(2026, 10, 22))
    first_id, _ = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    group_id = db_session.get(LedgerEntry, first_id).group_id

    assert client.delete(f"/splits/{group_id}").status_code == 204

    assert _row(db_session, instance.id).status == "pending"


def test_entry_delete_touching_a_period_is_refused_during_an_import(client, db_session, seed, loan, today, pg_engine):
    _, _, interest_id = _posted_period(db_session, seed, loan, today)
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            response = client.delete(f"/entries/{interest_id}")
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    assert (response.status_code, response.json()["message"]) == (409, "import_running")
    assert db_session.get(LedgerEntry, interest_id) is not None
```

- [ ] 13.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_entry_hooks.py
```

Expected: failures — e.g. the partial test sees `status == "posted"` with both ids still listed, the protection tests get 200 / 204.

- [ ] 13.3 Create `services/accounting-service/app/services/schedule_entry_hooks.py`:

```python
"""The ledger's side of schedules: deleting an entry of a posted period (design D33) and the rows a definition's
template references (D29). Imported by entry_write_service, split_service and settings_service; it imports none of
them, so there is no import cycle."""

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import ledger_service
from .errors import ConflictError
from .schedule_locks import lock_definition, lock_instance, take_import_key_shared


def _instance_listing(db: Session, entry_ids: list[int]) -> ScheduleInstance | None:
    """The posted instance whose posted_entry_ids contains one of the entries (unlocked; GIN @> lookups)."""
    if not entry_ids:
        return None
    return db.scalar(
        select(ScheduleInstance)
        .where(
            ScheduleInstance.status == "posted",
            or_(*[ScheduleInstance.posted_entry_ids.contains([entry_id]) for entry_id in entry_ids]),
        )
        .order_by(ScheduleInstance.id)
        .limit(1)
    )


DEFINITION_ENDED_RETRY = "排程剛結束，請重試刪除"


def lock_for_entry_delete(db: Session, entry_ids: list[int]) -> ScheduleInstance | None:
    """D32 for the entry-delete path: read without a lock, then the shared import key → definition FOR SHARE
    (FOR UPDATE when it is ended: after_entries_deleted may revive it, and two FOR SHARE holders that both upgrade
    deadlock) → instance FOR UPDATE, and re-check that it still lists one of the entries. Call before any group /
    entry lock."""
    ids = sorted(set(entry_ids))
    found = _instance_listing(db, ids)
    if found is None:
        return None
    take_import_key_shared(db)
    peek = db.get(ScheduleDefinition, found.definition_id)
    share = peek is None or peek.status != "ended"
    definition = lock_definition(db, found.definition_id, share=share)
    if share and definition.status == "ended":
        # Ended meanwhile (a loan close-out committed first). The SPA shows the message as is, so it is the owner's
        # text, not a code: retrying takes the definition FOR UPDATE and may revive it.
        raise ConflictError(DEFINITION_ENDED_RETRY)
    instance = lock_instance(db, found.id)
    if instance is None or instance.status != "posted" or not set(ids) & set(instance.posted_entry_ids):
        return None
    return instance


def after_entries_deleted(db: Session, instance: ScheduleInstance, deleted_ids: set[int]) -> None:
    """D33: the period becomes partial, or pending (reopened) / skipped (MOZE-booked) when nothing is left."""
    today = ledger_service._today().isoformat()
    remaining = [entry_id for entry_id in instance.posted_entry_ids if entry_id not in deleted_ids]
    if remaining:
        instance.posted_entry_ids, instance.is_partial = remaining, True
        instance.note = f"部分入帳記錄已於 {today} 刪除"
    elif instance.acted_by == "import":
        instance.status, instance.posted_entry_ids, instance.is_partial = "skipped", [], False
        instance.note = "入帳記錄已刪除"  # MOZE booked it: never re-posted
    else:
        instance.status, instance.posted_entry_ids, instance.is_partial = "pending", [], False
        instance.acted_at, instance.acted_by = None, None
        instance.reopened_at = datetime.now(timezone.utc)  # never auto-posted again; waits in 待完成交易
        instance.note = f"入帳記錄已於 {today} 刪除"
    if instance.status == "pending" or instance.is_partial:
        definition = db.get(ScheduleDefinition, instance.definition_id)
        if definition.status == "ended":
            definition.status = "active"
    db.flush()


def definitions_referencing(
    db: Session,
    *,
    account_id: int | None = None,
    category_id: int | None = None,
    counterparty_id: int | None = None,
    project_id: int | None = None,
    loan_entry_id: int | None = None,
) -> list[ScheduleDefinition]:
    """Definitions that are not ended and whose template names the row (JSONB containment on template.lines)."""
    lines = ScheduleDefinition.template["lines"]
    keys = {
        "account_id": account_id, "to_account_id": account_id, "category_id": category_id,
        "counterparty_id": counterparty_id, "project_id": project_id, "loan_entry_id": loan_entry_id,
    }
    conditions = [lines.contains([{key: value}]) for key, value in keys.items() if value is not None]
    if not conditions:
        return []
    return list(
        db.scalars(
            select(ScheduleDefinition)
            .where(ScheduleDefinition.status != "ended", or_(*conditions))
            .order_by(ScheduleDefinition.id)
        )
    )


def assert_not_referenced(db: Session, **ids) -> None:
    found = definitions_referencing(db, **ids)
    if found:
        raise ConflictError(f"排程「{found[0].name}」仍在使用，請先結束排程")


def assert_group_not_scheduled(db: Session, group_id: int) -> None:
    """A split PUT would replace the members a posted period lists; the owner edits one period instead (D29)."""
    member_ids = list(db.scalars(select(LedgerEntry.id).where(LedgerEntry.group_id == group_id)))
    instance = _instance_listing(db, member_ids)
    if instance is not None:
        definition = db.get(ScheduleDefinition, instance.definition_id)
        raise ConflictError(f"排程期別 {instance.id}（{definition.name} #{instance.seq}）使用這組記錄，請用編輯這一筆")
```

- [ ] 13.4 Edit `services/accounting-service/app/services/entry_write_service.py`:
  - add `from . import schedule_entry_hooks` after `from . import fx_rate_service, ledger_service`;
  - replace the comment block that starts with `# Lock order for every write that touches a group member` and ends with `# the adjustment path takes no entry lock afterwards.` (the block right above `def locked_with_legs`) with

```python
# Lock order (design D32), every write path: the shared import advisory key (schedule paths:
# schedule_locks.take_import_key_shared) → the schedule_definition row (FOR SHARE when posting or acting on one
# instance, FOR UPDATE when editing the definition) → schedule_instance row(s) (FOR UPDATE, ascending id) → the
# entry_group row(s) (lock_group, ascending id) → the target entries together with their transfer legs, in ONE
# statement ordered by ascending id (locked_with_legs; locked_entry for a single target, e.g. a schedule's loan) →
# nothing else. No path that holds an entry or group lock ever locks a schedule row: delete_entry / delete_split read
# the posted instance listing the entry without a lock, then take the key, the definition and the instance
# (schedule_entry_hooks.lock_for_entry_delete), re-check, and only then lock groups and entries. The backup importer
# holds the import key exclusively and locks every schedule definition, then every instance (ascending id), before it
# deletes any entry. Taking the group last (after a member) deadlocks against a split PUT that holds the group and
# waits for that member (plan review round 5); locking the target first and its other leg in a second statement
# deadlocks two concurrent deletes of a transfer's two legs (Task 12 review).
# The account row is a separate lock class taken only by create_balance_adjustment (account FOR UPDATE, then the
# balance read and the insert of a new entry); it is taken before any entry lock and no path that holds an entry
# or group lock ever locks an account row, so it cannot invert the order above. Entry inserts do take implicit
# foreign-key share locks on the account row, which this exclusive lock serialises with; that is harmless because
# the adjustment path takes no entry lock afterwards.
```

  - in `delete_entry` replace its first statement `group_ids, transfer_group_id = _group_ids_to_lock(db, entry_id)` with

```python
    # D29: a loan a live schedule repays cannot be deleted; D32/D33: the period's schedule rows are locked first.
    schedule_entry_hooks.assert_not_referenced(db, loan_entry_id=entry_id)
    instance = schedule_entry_hooks.lock_for_entry_delete(db, [entry_id])
    group_ids, transfer_group_id = _group_ids_to_lock(db, entry_id)
```

  and append at the end of `delete_entry` (after the `for group_id in group_ids:` loop):

```python
    if instance is not None:
        schedule_entry_hooks.after_entries_deleted(db, instance, {target.id for target in targets})
```

- [ ] 13.5 Edit `services/accounting-service/app/services/split_service.py`: add `from . import schedule_entry_hooks` to the imports; make the first line of `update_split` `schedule_entry_hooks.assert_group_not_scheduled(db, group_id)`; replace `delete_split` with

```python
def delete_split(db: Session, group_id: int) -> None:
    """Same lock order as update_split (group → members), after the schedule rows of a posted period that lists the
    members (D32). Groups holding transfer legs or non-editable kinds are refused under the member locks; settled
    members may be deleted (links are cleared)."""
    instance = schedule_entry_hooks.lock_for_entry_delete(db, member_ids(db, group_id))
    group = _locked_split(db, group_id)
    assert_editable(group)
    members = _locked_members(db, group_id)
    _assert_no_transfers_or_system_entries(members)
    delete_entries_cascade(db, [member.id for member in members])
    db.execute(delete(EntryGroup).where(EntryGroup.id == group_id))
    if instance is not None:
        schedule_entry_hooks.after_entries_deleted(db, instance, {member.id for member in members})
```

- [ ] 13.6 Edit `services/accounting-service/app/services/settings_service.py`: add `from . import schedule_entry_hooks` to the imports, then
  - in `update_account`, right after `_validate_account(db, payload, account)`, insert

```python
    if payload.is_archived and not account.is_archived:
        schedule_entry_hooks.assert_not_referenced(db, account_id=account_id)
```

  - in `delete_account`, right after `account = _get(db, Account, account_id, "account")`, insert `schedule_entry_hooks.assert_not_referenced(db, account_id=account_id)`;
  - in `update_category`, right after `_validate_category(db, payload, category)`, insert

```python
    if payload.is_hidden and not category.is_hidden:
        schedule_entry_hooks.assert_not_referenced(db, category_id=category_id)
```

  - in `delete_category`, right after `category = _get(db, Category, category_id, "category")`, insert `schedule_entry_hooks.assert_not_referenced(db, category_id=category_id)`;
  - in `update_project`, right after `_unique_name(db, Project, payload.name, project_id)`, insert

```python
    if payload.is_archived and not project.is_archived:
        schedule_entry_hooks.assert_not_referenced(db, project_id=project_id)
```

  - in `delete_project`, right after `project = _get(db, Project, project_id, "project")`, insert `schedule_entry_hooks.assert_not_referenced(db, project_id=project_id)`;
  - in `delete_counterparty`, right after `counterparty = _get(db, Counterparty, counterparty_id, "counterparty")`, insert `schedule_entry_hooks.assert_not_referenced(db, counterparty_id=counterparty_id)`.

- [ ] 13.7 Run 13.2 again, then the ledger suites the hooks touch.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_entry_hooks.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_entry_writes.py tests/integration/test_splits.py tests/integration/test_settings_crud_api.py tests/integration/test_settings_api.py 2>&1 | tail -1
```

Expected: `12 passed`; then the second run reports exactly `B_ledger13 passed` (recorded in 1.6; Task 13 adds no test to those files), 0 failed.

- [ ] 13.8 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_entry_hooks.py services/accounting-service/app/services/entry_write_service.py \
  services/accounting-service/app/services/split_service.py services/accounting-service/app/services/settings_service.py \
  services/accounting-service/tests/integration/test_schedule_entry_hooks.py
git commit -m "feat(accounting): deleting a period's entry updates the period; scheduled rows are protected

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 14. Entry rows `schedule` and detail `loan_schedule`

**Model:** sonnet

**Files:**
- Modify: `services/accounting-service/app/schemas/ledger.py` (`EntryScheduleOut`, `CurrencyAmountOut` moved here from `schedules.py`, `LoanScheduleOut`; `EntryOut.schedule`; `EntryDetailOut.loan_schedule`)
- Modify: `services/accounting-service/app/schemas/schedules.py` (imports `CurrencyAmountOut` from `.ledger` instead of defining it)
- Modify: `services/accounting-service/app/services/ledger_service.py` (`_entry_rows`, `get_entry_detail`)
- Test: `services/accounting-service/tests/integration/test_schedule_links.py`

**Interfaces:**
- Consumes: `schedule_read.schedule_links`, `schedule_read.loan_schedule_for` (Task 9).
- Produces: every `EntryOut` (lists, passbook, detail, write responses) carries `schedule` (`EntryScheduleOut | None`); `EntryDetailOut.loan_schedule` (`LoanScheduleOut | None`) for receivable / payable originals (rows with `open_amount` not null).

Rules: `schedule_read` imports `ledger_service`, so `ledger_service` imports it inside the two functions (no module-level cycle). One `@>` query per `_entry_rows` call (D40). `get_entry_detail` calls `_entry_rows` for the entry itself and for each nested list (children, group members, settles, settled_by, refunds, rewards …), so one detail issues about nine small `schedule_links` lookups, each an indexed `posted_entry_ids @> …` probe on the GIN index; this is accepted (a detail is one entry, not a page) rather than threading one combined lookup through every nested call. One amount model: `CurrencyAmountOut` lives in `schemas/ledger.py` (`schemas/writes.py` imports `ledger.py` and `schedules.py` imports `writes.py`, so `ledger.py` cannot import `schedules.py`) and `schedules.py` imports it; no second `{currency, amount}` model.

- [ ] 14.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_links.py`:

```python
"""Schedule links on entries (spec "Schedule links on entries", "Loan summary" through GET /entries/{id})."""

from datetime import date
from decimal import Decimal


def test_entry_rows_carry_the_schedule_link(client, db_session, seed, today):
    # Spec "Pill data on a posted entry".
    today(date(2027, 3, 1))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸 每月還款",
        times=36,
    )
    repayment = seed.entry(bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id,
                           is_settlement=True, day=date(2027, 2, 9), source="schedule")
    fifth = seed.instance(loan, 5, date(2027, 2, 9), status="posted", entries=[repayment])
    db_session.commit()

    rows = {row["id"]: row for row in client.get("/entries").json()["items"]}

    link = rows[repayment.id]["schedule"]
    assert (link["kind"], link["seq"], link["times"], link["is_partial"], link["instance_id"], link["definition_id"]) == (
        "installment", 5, 36, False, fifth.id, loan.id,
    )
    assert rows[payable.id]["schedule"] is None


def test_passbook_and_detail_carry_the_link(client, db_session, seed, today):
    # Spec "Unlimited recurring pill" data: times null.
    today(date(2026, 10, 30))
    card = seed.account("範例卡")
    netflix = seed.definition([seed.line("expense", card, "390")])
    expense = seed.entry(card, "-390", day=date(2026, 10, 22), source="schedule")
    seed.instance(netflix, 25, date(2026, 10, 22), status="posted", entries=[expense])
    db_session.commit()

    passbook = client.get(f"/accounts/{card.id}/entries").json()["items"][0]["schedule"]
    detail = client.get(f"/entries/{expense.id}").json()

    assert (passbook["kind"], passbook["seq"], passbook["times"]) == ("recurring", 25, None)
    assert detail["schedule"]["seq"] == 25 and detail["loan_schedule"] is None


def test_loan_detail_after_three_periods(client, db_session, seed, today):
    # Spec "Loan detail after three periods".
    today(date(2027, 1, 20))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    loan = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id)], kind="installment", name="信貸 每月還款",
        anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    for seq, day in enumerate((date(2026, 11, 9), date(2026, 12, 9), date(2027, 1, 9)), start=1):
        repayment = seed.entry(bank, "-8333", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id,
                               is_settlement=True, day=day, source="schedule")
        seed.instance(loan, seq, day, status="posted", entries=[repayment])
    seed.instance(loan, 4, date(2027, 2, 9))
    db_session.commit()

    body = client.get(f"/entries/{payable.id}").json()["loan_schedule"]

    assert Decimal(body["remaining"]) == Decimal("-275001") and Decimal(body["repaid"]) == Decimal("24999")
    assert (body["posted_count"], body["times"], body["next_due_date"], body["definition_id"]) == (3, 36, "2027-02-09", loan.id)
    assert (body["name"], body["status"], body["posting_mode"], body["needs_check"]) == ("信貸 每月還款", "active", "auto", False)


def test_loan_schedule_is_null_without_a_definition(client, db_session, seed, today):
    today(date(2026, 10, 3))
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    receivable = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    assert client.get(f"/entries/{receivable.id}").json()["loan_schedule"] is None
```

- [ ] 14.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_links.py
```

Expected: 4 failures with `KeyError: 'schedule'` / `KeyError: 'loan_schedule'`.

- [ ] 14.3 Edit `services/accounting-service/app/schemas/ledger.py`: add above `class EntryOut(BaseModel):`

```python
class EntryScheduleOut(BaseModel):
    """The posted schedule period that wrote this entry (the 週期 #k/N / 分期 #k/N pill)."""

    definition_id: int
    instance_id: int
    kind: Literal["recurring", "installment"]
    seq: int
    times: int | None
    name: str
    is_partial: bool
    acted_by: Literal["auto", "owner", "import"] | None
    posted_entry_ids: list[int]


class CurrencyAmountOut(BaseModel):
    currency: str
    amount: Decimal


class LoanScheduleOut(BaseModel):
    definition_id: int
    name: str
    status: Literal["active", "paused", "ended"]
    posting_mode: Literal["auto", "confirm"]
    posted_count: int
    times: int | None
    next_due_date: date | None
    next_amount: list[CurrencyAmountOut]
    remaining: Decimal | None
    repaid: Decimal | None
    needs_check: bool
```

  In `services/accounting-service/app/schemas/schedules.py` delete the `class CurrencyAmountOut(BaseModel): …` block (Task 5) and add `from .ledger import CurrencyAmountOut` below `from .writes import …` (the name and fields are unchanged, so `DefinitionOut.next_amount` and `InstanceOut.totals` keep their shape).

  add as the last field of `EntryOut`: `schedule: EntryScheduleOut | None = None`; add as the last field of `EntryDetailOut`: `loan_schedule: LoanScheduleOut | None = None`.

- [ ] 14.4 Edit `services/accounting-service/app/services/ledger_service.py`:
  - at the end of `_entry_rows`, replace the final `return page` with

```python
    from .schedule_read import schedule_links  # schedule_read imports this module: no module-level import

    links = schedule_links(db, [item["id"] for item in page])
    for item in page:
        item["schedule"] = links.get(item["id"])
    return page
```

  - at the end of `get_entry_detail`, replace the final `return detail` with

```python
    from .schedule_read import loan_schedule_for

    # Only receivable / payable originals carry open_amount; only they can be a schedule's loan.
    detail["loan_schedule"] = loan_schedule_for(db, entry.id) if detail["open_amount"] is not None else None
    return detail
```

- [ ] 14.5 Run 14.2 again, then the entry and account read suites.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_links.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_entries_api.py tests/integration/test_accounts_api.py 2>&1 | tail -1
```

Expected: `4 passed`; then the same pass count as before, 0 failed.

- [ ] 14.6 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/schemas/ledger.py services/accounting-service/app/schemas/schedules.py services/accounting-service/app/services/ledger_service.py \
  services/accounting-service/tests/integration/test_schedule_links.py
git commit -m "feat(accounting): entries carry their schedule period; loans carry their schedule summary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 15. Daily job, dry run, scheduler, CLI, run-now

**Model:** opus

**Files:**
- Modify: `services/accounting-service/requirements.txt` (`apscheduler==3.11.0`)
- Create: `services/accounting-service/app/services/schedule_job.py`
- Modify: `services/accounting-service/app/main.py` (startup / shutdown hooks)
- Modify: `services/accounting-service/app/routers/schedules.py` (append `POST /run-now`)
- Modify: `services/accounting-service/tests/conftest.py` (autouse `no_scheduler`)
- Test: `services/accounting-service/tests/integration/test_schedule_job.py`, `services/accounting-service/tests/unit/test_schedule_job_scheduler.py`

**Interfaces:**
- Consumes: `generation.generate_locked` (Task 6); `posting.post_instance`, `record_failure`, `failure_message` (Task 8) — called through the module attribute `posting.post_instance` so a test can wrap it; `SCHEDULE_JOB_LOCK_KEY`, `ImportRunningError`, `import_key_free` (Task 5); `schedule_service.create_definition`, `set_mode` (Tasks 10–11, tests).
- Produces: `schedule_job.TRIGGERS`, `due_instances`, `blocked_by_earlier(db, definition, instance_id, today) -> bool` (R-F6), `run(…, post=True)`, `run_after_cli_import(engine, *, today=None) -> dict` (R-F4, called by Task 18's importer CLI), `is_enabled`, `build_scheduler`, `start`, `stop`, `trigger_after_import`, `main`, `_post_job_one(db, instance_id, definition_id, today) -> PostResult`, `_after_run(engine, report, scheduler=None)`, `_retry(engine, day)`, job ids `schedule_daily`, `schedule_startup`, `schedule_retry`, `schedule_import`; route `POST /schedules/run-now` → `RunReportOut`.

Rules (D34, spec "Daily schedule job", "Run-now endpoint"):
- `run(engine, trigger, *, today=None, dry_run=False)`: `pg_try_advisory_lock(SCHEDULE_JOB_LOCK_KEY)` on a dedicated connection (held → `status = "busy"`, nothing done; released in `finally`); generation, one transaction per non-ended definition (each takes the shared import key; refused → stop with `import_running`); then post `due_instances` (pending, `due_date ≤ today`, `due_date ≥ auto_post_from`, `reopened_at IS NULL`, definition `active` and `auto`, ordered ★`definition_id, seq` — D31's sequence order, never `due_date` order, Multica R-F6), each in its own transaction through `_post_job_one`: shared import key → `posting.lock_definition_for_post` (the same strength `post_instance` takes next, so no lock upgrade) → ★`blocked_by_earlier` under that lock — a period is not eligible while an earlier `seq` of the same definition is `pending` and either due for the job (`posting.auto_eligible`) or failed (`last_error` set), even when the owner moved the earlier period's `due_date` past this one's; pre-`auto_post_from` and reopened periods are the owner's (D34) and do not hold the series; a held period is passed over without recording anything and its definition is listed under `stopped_definitions` — then `posting.post_instance(…, actor = "auto", job=True)` (SKIP LOCKED and eligibility re-checked under the lock; an earlier period the owner is acting on is still `pending` to this read, so the later one waits for the next run); `import_running` stops the run; a `ValidationError` (or any unexpected error, logged by class) records `last_error` and stops that definition's later instances for this run; a `ConflictError` (someone else posted / skipped it) or a `NotFoundError` (the period was deleted by `end` or a definition `PUT` after `due_instances` read it) moves on without recording anything; `EditLockedError` is treated as a failure (recorded, stops the definition). Report `{trigger, today (ISO), status (completed | busy | import_running | dry_run), generated, posted [ids], failed [ids], stopped_definitions [ids]}` — logged as counts and ids.
- `--dry-run`: one session, generation and posting inside savepoints, everything rolled back; status `dry_run`; prints the report.
- ★`run(…, post=False)` (Multica R-F4): generation only; the run then counts the instances that are due for the job (`due_instances`) but were not posted, as `report["due_unposted"]`, and logs `schedule_job.due_unposted` with that count. `run_after_cli_import(engine)` is the importer CLI's inline job (the standalone CLI process has no scheduler, so `trigger_after_import` would do nothing there): `run(engine, "import", post=is_enabled())` — generation always; posting only when `ACCOUNTING_SCHEDULER_ENABLED` is truthy, parsed exactly as the scheduler switch.
- Scheduler: `BackgroundScheduler(timezone="Asia/Taipei")`, `CronTrigger(hour=0, minute=5)` with `coalesce=True`, `max_instances=1`, `misfire_grace_time=3600`; a one-off run 10 s after start; after a run ending `busy` / `import_running` (or crashing), an `IntervalTrigger(minutes=10)` retry job that removes itself once a run completes or the Taipei day changes; `trigger_after_import` adds a one-off run now (the importer calls it after releasing its lock, Task 18). Started from `app.main` on startup when `is_enabled()`, stopped on shutdown.
- `run-now`: 409 `import_running` when an import holds the key, 409 when the job lock is held (`busy`); otherwise HTTP 200 with the report (an import that starts mid-run ends it with `status = import_running` and the partial counts).

- [ ] 15.1 Add `apscheduler==3.11.0` as the last line of `services/accounting-service/requirements.txt` and install it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
printf 'apscheduler==3.11.0\n' >> requirements.txt
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -c "import apscheduler; print(apscheduler.__version__)"
```

Expected: `3.11.0`.

- [ ] 15.2 Add to `services/accounting-service/tests/conftest.py`, below the `no_import_lock_from_env` fixture:

```python
@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch):
    """The in-process schedule job never starts inside the test process (TestClient runs startup hooks)."""
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", "false")
```

- [ ] 15.3 Write the failing unit test `services/accounting-service/tests/unit/test_schedule_job_scheduler.py`:

```python
"""Scheduler wiring of the schedule job (design D34); no database."""

from datetime import date

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services import ledger_service
from app.services import schedule_job


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.removed: list[str] = []

    def add_job(self, func, trigger, id, replace_existing=False, **kwargs):  # noqa: A002 (APScheduler's keyword)
        self.jobs[id] = {"func": func, "trigger": trigger, **kwargs}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)


def test_scheduler_switch(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_SCHEDULER_ENABLED", raising=False)
    assert schedule_job.is_enabled() is True
    for value in ("false", "0", "no", " FALSE "):
        monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", value)
        assert schedule_job.is_enabled() is False


def test_build_scheduler_registers_the_daily_and_startup_runs():
    scheduler = schedule_job.build_scheduler(engine=object())
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"schedule_daily", "schedule_startup"}
    daily = jobs["schedule_daily"]
    assert isinstance(daily.trigger, CronTrigger)
    assert str(daily.trigger) == "cron[hour='0', minute='5']"
    assert str(daily.trigger.timezone) == "Asia/Taipei"
    assert (daily.coalesce, daily.max_instances, daily.misfire_grace_time) == (True, 1, 3600)
    assert daily.kwargs["trigger"] == "cron" and jobs["schedule_startup"].kwargs["trigger"] == "startup"


def test_after_run_adds_and_clears_the_retry():
    fake = FakeScheduler()
    schedule_job._after_run(object(), {"status": "import_running", "today": "2026-10-09"}, scheduler=fake)
    retry = fake.jobs["schedule_retry"]
    assert isinstance(retry["trigger"], IntervalTrigger) and retry["trigger"].interval.total_seconds() == 600
    assert retry["kwargs"]["day"] == "2026-10-09"
    schedule_job._after_run(object(), {"status": "completed", "today": "2026-10-09"}, scheduler=fake)
    assert fake.removed == ["schedule_retry"]


def test_retry_stops_on_a_new_taipei_day(monkeypatch):
    fake = FakeScheduler()
    fake.jobs["schedule_retry"] = {}
    monkeypatch.setattr(schedule_job, "_scheduler", fake)
    monkeypatch.setattr(ledger_service, "_today", lambda: date(2026, 10, 10))
    monkeypatch.setattr(schedule_job, "run", lambda *args, **kwargs: pytest.fail("no run on a new day"))
    schedule_job._retry(object(), "2026-10-09")
    assert fake.removed == ["schedule_retry"]


def test_trigger_after_import_needs_a_running_scheduler(monkeypatch):
    monkeypatch.setattr(schedule_job, "_scheduler", None)
    schedule_job.trigger_after_import(object())  # no scheduler (tests, disabled): nothing happens
    fake = FakeScheduler()
    monkeypatch.setattr(schedule_job, "_scheduler", fake)
    schedule_job.trigger_after_import(object())
    assert fake.jobs["schedule_import"]["kwargs"]["trigger"] == "import"


@pytest.mark.parametrize(("value", "post"), [(None, True), ("true", True), ("false", False), ("0", False), ("no", False)])
def test_cli_import_job_posts_only_when_the_scheduler_switch_is_on(monkeypatch, value, post):
    # Multica R-F4: the importer CLI runs the job inline; posting follows ACCOUNTING_SCHEDULER_ENABLED (same parsing).
    if value is None:
        monkeypatch.delenv("ACCOUNTING_SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", value)
    calls = []
    monkeypatch.setattr(schedule_job, "run", lambda engine, trigger, **kwargs: calls.append((trigger, kwargs)) or {})
    schedule_job.run_after_cli_import(object(), today=date(2026, 10, 9))
    assert calls == [("import", {"today": date(2026, 10, 9), "post": post})]
```

- [ ] 15.4 Write the failing integration test `services/accounting-service/tests/integration/test_schedule_job.py`:

```python
"""The daily job (spec "Daily schedule job", "Run-now endpoint")."""

import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.schemas.schedules import DefinitionIn
from app.services import schedule_job, schedule_read, schedule_service
from app.services import schedule_posting as posting
from app.services.errors import NotFoundError
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import SCHEDULE_JOB_LOCK_KEY


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def _entries(db) -> int:
    return db.scalar(select(func.count()).select_from(LedgerEntry))


class Held:
    """Hold an advisory lock on its own connection: Held(pg_engine, KEY) as a context manager."""

    def __init__(self, engine, key):
        self.engine, self.key = engine, key

    def __enter__(self):
        self.conn = self.engine.connect()
        self.conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": self.key})
        return self

    def __exit__(self, *exc):
        self.conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
        self.conn.commit()
        self.conn.close()


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_no_silent_backlog_after_an_import(db_session, seed, card, pg_engine, today):
    # Spec "No silent backlog after an import".
    today(date(2026, 10, 3))
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1",
                               auto_post_from=date(2026, 10, 3), anchor=date(2026, 10, 1))
    early = seed.instance(imported, 1, date(2026, 10, 1), moze_id="R-1")
    due = seed.instance(imported, 2, date(2026, 10, 3), moze_id="R-2")
    db_session.commit()

    report = schedule_job.run(pg_engine, "import", today=date(2026, 10, 3))

    assert report["status"] == "completed" and report["posted"] == [due.id]
    assert (_row(db_session, due.id).status, _row(db_session, due.id).acted_by) == ("posted", "auto")
    assert _row(db_session, early.id).status == "pending"
    assert early.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_back_dated_local_definition(db_session, seed, card, pg_engine, today):
    # Spec "Back-dated local definition".
    today(date(2026, 10, 3))
    definition_id = schedule_service.create_definition(
        db_session,
        DefinitionIn(kind="recurring", name="管理費", template={"lines": [{"kind": "expense", "account_id": card.id,
                     "amount": "2000", "currency": "TWD"}]}, interval_unit="month", anchor_date=date(2026, 8, 15)),
    )
    db_session.commit()

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 3))

    assert report["posted"] == [] and _entries(db_session) == 0
    queue = [(item["definition_id"], item["due_date"]) for item in schedule_read.list_instances(db_session, queue=True)]
    assert queue == [(definition_id, date(2026, 8, 15)), (definition_id, date(2026, 9, 15))]


def test_only_one_runner(db_session, seed, card, pg_engine):
    # Spec "Only one runner".
    netflix = seed.definition([seed.line("expense", card, "390")])
    seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()
    with Held(pg_engine, SCHEDULE_JOB_LOCK_KEY):
        report = schedule_job.run(pg_engine, "startup", today=date(2026, 10, 3))
    assert (report["status"], report["posted"], report["generated"]) == ("busy", [], 0)
    assert _entries(db_session) == 0


def test_retry_after_an_import(db_session, seed, card, pg_engine):
    # Spec "Retry after an import": the 00:05 run stops, the run at the end of the import posts.
    bank = seed.account("薪轉")
    loan_day = seed.definition([seed.line("expense", bank, "8333")], name="信貸", anchor=date(2026, 10, 9))
    instance = seed.instance(loan_day, 1, date(2026, 10, 9))
    db_session.commit()
    with Held(pg_engine, IMPORT_LOCK_KEY):
        stopped = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 9))
    assert (stopped["status"], stopped["posted"]) == ("import_running", [])
    after = schedule_job.run(pg_engine, "import", today=date(2026, 10, 9))
    assert (after["status"], after["posted"]) == ("completed", [instance.id])


def test_moved_earlier_seq_is_attempted_before_the_next_one(db_session, seed, card, pg_engine, monkeypatch):
    # Multica R-F6 (D31 wins over due-date order): seq 1 moved to Nov-10, seq 2 on Nov-9; run on Nov-10.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    moved = seed.instance(netflix, 1, date(2026, 11, 10), rule_date=date(2026, 10, 9), edited_by_owner=True)
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()
    order: list[int] = []
    real_post = posting.post_instance
    monkeypatch.setattr(posting, "post_instance", lambda db, instance_id, **kwargs: order.append(instance_id) or real_post(db, instance_id, **kwargs))

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))

    assert order == [moved.id, second.id] and report["posted"] == [moved.id, second.id]


def test_failed_moved_earlier_seq_holds_the_next_one_until_the_owner_posts_it(db_session, seed, card, pg_engine):
    # Multica R-F6: seq 1 (moved later) fails → seq 2 is not posted, in this run and the next; the owner posts seq 1
    # → seq 2 posts on the next run.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    moved = seed.instance(
        netflix, 1, date(2026, 11, 10), rule_date=date(2026, 10, 9), edited_by_owner=True, amount_override=["0"]
    )
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()

    first_run = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))
    assert (first_run["failed"], first_run["posted"], first_run["stopped_definitions"]) == ([moved.id], [], [netflix.id])
    assert _row(db_session, second.id).status == "pending"
    again = schedule_job.run(pg_engine, "retry", today=date(2026, 11, 10))
    assert (again["posted"], _row(db_session, second.id).status) == ([], "pending")

    row = _row(db_session, moved.id)
    row.amount_override = ["390"]
    db_session.commit()
    posting.post_instance(db_session, moved.id, actor="owner")
    db_session.commit()
    after = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 11))
    assert after["posted"] == [second.id] and _row(db_session, second.id).acted_by == "auto"


def test_a_failed_earlier_period_the_job_does_not_own_still_holds_the_series(db_session, seed, card, pg_engine):
    # Multica R-F6 "(or failed)": a reopened seq 1 whose owner post failed (last_error) keeps seq 2 waiting; a reopened
    # seq 1 without an error does not (the owner deleted it on purpose, D33).
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    reopened_at = datetime(2026, 10, 10, tzinfo=timezone.utc)
    first = seed.instance(netflix, 1, date(2026, 10, 9), reopened_at=reopened_at, last_error="lines[0].account_id: 帳戶已封存")
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()
    held = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 9))
    assert (held["posted"], held["failed"], held["stopped_definitions"]) == ([], [], [netflix.id])

    row = _row(db_session, first.id)
    row.last_error = None
    db_session.commit()
    assert schedule_job.run(pg_engine, "cron", today=date(2026, 11, 9))["posted"] == [second.id]


def test_due_but_unposted_are_counted_when_posting_is_off(db_session, seed, card, pg_engine):
    # Multica R-F4: run(post=False) generates and counts what the job would post, posting nothing.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22), auto_post_from=date(2026, 10, 1))
    db_session.commit()
    report = schedule_job.run(pg_engine, "import", today=date(2026, 10, 22), post=False)
    assert (report["status"], report["posted"], report["due_unposted"]) == ("completed", [], 1)
    assert report["generated"] == 14 and _entries(db_session) == 0


def test_failure_stops_the_loan_not_the_others(db_session, seed, card, pg_engine):
    # Spec "Failure stops the loan, not the others".
    bank = seed.account("薪轉")
    loan = seed.definition([seed.line("expense", bank, "8333")], name="L", anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    other = seed.definition([seed.line("expense", card, "390")], name="N", anchor=date(2026, 11, 1), auto_post_from=date(2026, 10, 1))
    failing = seed.instance(loan, 1, date(2026, 10, 9), amount_override=["0"])
    waiting = seed.instance(loan, 2, date(2026, 11, 9))
    fine = seed.instance(other, 1, date(2026, 11, 1))
    db_session.commit()

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))

    assert (report["failed"], report["posted"], report["stopped_definitions"]) == ([failing.id], [fine.id], [loan.id])
    assert _row(db_session, failing.id).last_error.startswith("amounts")
    assert (_row(db_session, waiting.id).status, _row(db_session, waiting.id).last_error) == ("pending", None)


def test_paused_definition(db_session, seed, card, pg_engine):
    # Spec "Paused definition".
    paused = seed.definition([seed.line("expense", card, "390")], status="paused")
    instance = seed.instance(paused, 1, date(2026, 10, 22))
    db_session.commit()
    schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))
    assert _row(db_session, instance.id).status == "pending"


def test_dry_run(db_session, seed, card, pg_engine, today, capsys):
    # Spec "Dry run".
    today(date(2026, 10, 22))
    netflix = seed.definition([seed.line("expense", card, "390")])
    spotify = seed.definition([seed.line("expense", card, "149")], name="Spotify")
    first = seed.instance(netflix, 1, date(2026, 10, 22))
    second = seed.instance(spotify, 1, date(2026, 10, 22))
    db_session.commit()

    assert schedule_job.main(["--dry-run"], engine=pg_engine) == 0

    printed = json.loads(capsys.readouterr().out)
    assert (printed["status"], sorted(printed["posted"])) == ("dry_run", sorted([first.id, second.id]))
    assert _entries(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleInstance)) == 2
    assert {_row(db_session, first.id).status, _row(db_session, second.id).status} == {"pending"}
    assert db_session.get(ScheduleDefinition, netflix.id).generated_until is None


def test_job_run_twice_in_one_day_posts_each_instance_once(db_session, seed, card, pg_engine):
    # Review Focus 2: the 00:05 run, then a restart's startup run on the same day.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22))
    db_session.commit()

    first = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))
    entries = _entries(db_session)
    second = schedule_job.run(pg_engine, "startup", today=date(2026, 10, 22))

    assert (first["generated"], len(first["posted"])) == (14, 1)  # 2026-10-22 … 2027-11-22 (horizon 2027-11-22)
    assert (second["generated"], second["posted"], second["failed"]) == (0, [], [])
    assert _entries(db_session) == entries == 1
    posted = db_session.scalars(select(ScheduleInstance).where(ScheduleInstance.status == "posted")).all()
    assert [(row.definition_id, row.due_date) for row in posted] == [(netflix.id, date(2026, 10, 22))]


def test_mode_switch_then_the_job_posts_only_today(client, db_session, seed, card, pg_engine, today):
    # Spec "Switching to automatic posting", the job half.
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm",
                           auto_post_from=date(2026, 9, 1))
    backlog = seed.instance(rent, 1, date(2026, 9, 22))
    due = seed.instance(rent, 2, date(2026, 10, 3))
    db_session.commit()
    assert client.put(f"/schedules/definitions/{rent.id}/mode", json={"posting_mode": "auto"}).status_code == 200

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 3))

    assert report["posted"] == [due.id]
    assert _row(db_session, backlog.id).status == "pending"


def test_reopened_instance_is_never_posted_by_the_job(db_session, seed, card, pg_engine):
    netflix = seed.definition([seed.line("expense", card, "390")])
    reopened = seed.instance(netflix, 1, date(2026, 10, 22), reopened_at=datetime(2026, 10, 22, tzinfo=timezone.utc))
    db_session.commit()
    assert schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))["posted"] == []
    assert _row(db_session, reopened.id).status == "pending"


def test_run_now_refused_while_the_job_runs(client, db_session, seed, card, pg_engine):
    # Spec "Manual run while the job runs".
    netflix = seed.definition([seed.line("expense", card, "390")])
    seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()
    with Held(pg_engine, SCHEDULE_JOB_LOCK_KEY):
        response = client.post("/schedules/run-now")
    assert response.status_code == 409
    assert _entries(db_session) == 0


def test_run_now_refused_while_an_import_runs(client, db_session, pg_engine):
    with Held(pg_engine, IMPORT_LOCK_KEY):
        response = client.post("/schedules/run-now")
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_a_period_deleted_mid_run_is_passed_over(db_session, seed, card, pg_engine, monkeypatch):
    # end / a definition PUT may delete a period between due_instances and its post: not a failure.
    netflix = seed.definition([seed.line("expense", card, "390")])
    gone = seed.instance(netflix, 1, date(2026, 10, 22))
    db_session.commit()

    def deleted_meanwhile(db, instance_id, **kwargs):
        raise NotFoundError(f"schedule instance {instance_id} not found")

    monkeypatch.setattr(posting, "post_instance", deleted_meanwhile)
    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert (report["failed"], report["stopped_definitions"]) == ([], [])
    assert _row(db_session, gone.id).last_error is None


def test_run_now_needs_the_bearer_token_when_api_tokens_are_set(client, db_session, monkeypatch):
    # #42 / design D42: run-now sits behind ApiTokenMiddleware like every other route.
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", "test:synthetic-token-1")
    assert client.post("/schedules/run-now").status_code == 401
    allowed = client.post("/schedules/run-now", headers={"Authorization": "Bearer synthetic-token-1"})
    assert allowed.status_code == 200


def test_import_starts_mid_run(client, db_session, seed, card, pg_engine, today, monkeypatch):
    # Spec "Import starts mid-run".
    today(date(2026, 10, 22))
    ids = []
    for name in ("A", "B", "C"):
        definition = seed.definition([seed.line("expense", card, "100")], name=name)
        ids.append(seed.instance(definition, 1, date(2026, 10, 22)).id)
    db_session.commit()
    holder = pg_engine.connect()
    real_post = schedule_job._post_job_one
    calls: list[int] = []

    def post_then_import(db, instance_id, definition_id, today):
        # Wraps _post_job_one, before it shares the import key (wrapping post_instance would hold the key already).
        if len(calls) == 2:
            holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        calls.append(instance_id)
        return real_post(db, instance_id, definition_id, today)

    monkeypatch.setattr(schedule_job, "_post_job_one", post_then_import)
    try:
        response = client.post("/schedules/run-now")
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        holder.close()

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["posted"], body["trigger"]) == ("import_running", ids[:2], "manual")
```

- [ ] 15.5 Run both.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_schedule_job_scheduler.py tests/integration/test_schedule_job.py
```

Expected: collection errors `ImportError: cannot import name 'schedule_job' from 'app.services'`.

- [ ] 15.6 Create `services/accounting-service/app/services/schedule_job.py`:

```python
"""The daily schedule job (design D34) and its in-process scheduler.

run() takes the job advisory lock on a dedicated connection (a second runner returns busy; the lock, not the
scheduler, guarantees a single runner, so the CLI or a second process is safe), generates instances for every
definition in its own transaction, then posts each due auto instance in its own transaction (acted_by auto).
Logs carry ids, counts and error classes only.

CLI: python -m app.services.schedule_job [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..models import ScheduleDefinition, ScheduleInstance
from . import ledger_service
from . import schedule_generation as generation
from . import schedule_posting as posting
from .errors import ConflictError, NotFoundError, ValidationError
from .schedule_locks import SCHEDULE_JOB_LOCK_KEY, ImportRunningError, take_import_key_shared

logger = logging.getLogger(__name__)

TZ_NAME = "Asia/Taipei"
TRIGGERS = ("cron", "startup", "retry", "import", "manual")
DAILY_JOB_ID = "schedule_daily"
STARTUP_JOB_ID = "schedule_startup"
RETRY_JOB_ID = "schedule_retry"
IMPORT_JOB_ID = "schedule_import"
STARTUP_DELAY_SEC = 10
RETRY_MINUTES = 10
RETRY_STATUSES = ("busy", "import_running", "crashed")

_scheduler: BackgroundScheduler | None = None


def is_enabled() -> bool:
    return os.getenv("ACCOUNTING_SCHEDULER_ENABLED", "true").strip().lower() not in {"false", "0", "no"}


def due_instances(db: Session, today: date) -> list[tuple[int, int]]:
    """(instance id, definition id) the job may post today, in posting order: per definition, in seq order (D31,
    R-F6) — a period the owner moved later is still attempted before the next seq."""
    rows = db.execute(
        select(ScheduleInstance.id, ScheduleInstance.definition_id)
        .join(ScheduleDefinition, ScheduleDefinition.id == ScheduleInstance.definition_id)
        .where(
            ScheduleInstance.status == "pending",
            ScheduleInstance.due_date <= today,
            ScheduleInstance.due_date >= ScheduleDefinition.auto_post_from,
            ScheduleInstance.reopened_at.is_(None),
            ScheduleDefinition.status == "active",
            ScheduleDefinition.posting_mode == "auto",
        )
        .order_by(ScheduleInstance.definition_id, ScheduleInstance.seq)
    )
    return [(instance_id, definition_id) for instance_id, definition_id in rows]


def blocked_by_earlier(db: Session, definition: ScheduleDefinition, instance_id: int, today: date) -> bool:
    """D31 / Multica R-F6: within one definition periods are attempted in seq order. A period waits while an earlier
    seq of the same definition is pending and either due for the job (auto_eligible) or failed (last_error) — also
    when the owner moved that earlier period's due_date past this one's. Pre-auto_post_from and reopened periods are
    the owner's (D34) and do not hold the series. Called under the definition lock (lock_definition_for_post): an
    owner action on the earlier period is either committed (seen) or not yet (seen as pending: this one waits)."""
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        return False  # deleted meanwhile: post_instance answers NotFoundError
    earlier = db.scalars(
        select(ScheduleInstance).where(
            ScheduleInstance.definition_id == definition.id,
            ScheduleInstance.status == "pending",
            ScheduleInstance.seq < instance.seq,
        )
    )
    return any(posting.auto_eligible(definition, row, today) or row.last_error is not None for row in earlier)


def _post_job_one(db: Session, instance_id: int, definition_id: int, today: date) -> posting.PostResult:
    """One job posting transaction: shared key → definition (lock_definition_for_post, the strength post_instance
    takes next) → the sequence barrier → post_instance (instance FOR UPDATE SKIP LOCKED, eligibility re-checked)."""
    take_import_key_shared(db)
    definition = posting.lock_definition_for_post(db, definition_id)
    if blocked_by_earlier(db, definition, instance_id, today):
        return posting.PostResult(instance_id, "blocked")
    return posting.post_instance(db, instance_id, actor="auto", job=True, today=today)


def _new_report(trigger: str, today: date) -> dict:
    return {
        "trigger": trigger, "today": today.isoformat(), "status": "completed", "generated": 0, "posted": [],
        "failed": [], "stopped_definitions": [],
    }


def _definition_ids(db: Session) -> list[int]:
    return list(
        db.scalars(select(ScheduleDefinition.id).where(ScheduleDefinition.status != "ended").order_by(ScheduleDefinition.id))
    )


def _generate_all(factory, today: date, report: dict) -> bool:
    """One transaction per definition; False when an import stopped the run."""
    with factory() as db:
        definition_ids = _definition_ids(db)
    for definition_id in definition_ids:
        with factory() as db:
            try:
                report["generated"] += generation.generate_locked(db, definition_id, today)
                db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                return False
            except NotFoundError:
                db.rollback()  # deleted since the id list was read
    return True


def _post_due(factory, today: date, report: dict) -> None:
    with factory() as db:
        due = due_instances(db, today)
    stopped: set[int] = set()
    for instance_id, definition_id in due:
        if definition_id in stopped:
            continue  # a loan's periods post in order: a failure stops the later ones for this run
        with factory() as db:
            try:
                result = _post_job_one(db, instance_id, definition_id, today)
                db.commit()
            except ImportRunningError:
                db.rollback()
                report["status"] = "import_running"
                break
            except ValidationError as exc:
                posting.record_failure(db, instance_id, posting.failure_message(exc))
                report["failed"].append(instance_id)
                stopped.add(definition_id)
                continue
            except (ConflictError, NotFoundError):
                db.rollback()  # the owner posted or skipped it meanwhile, or end / a definition PUT deleted it
                continue
            except Exception as exc:  # noqa: BLE001 — one broken period must not stop the other schedules
                # EditLockedError lands here on purpose: the job posts with check_cutover_lock=False, so a cutover
                # refusal means a period entry is a MOZE row — recorded as a failure that stops the definition.
                db.rollback()
                logger.exception(
                    "schedule_job.post_error", extra={"instance_id": instance_id, "error_class": exc.__class__.__name__}
                )
                posting.record_failure(db, instance_id, posting.failure_message(exc))
                report["failed"].append(instance_id)
                stopped.add(definition_id)
                continue
            if result.outcome == "posted":
                report["posted"].append(instance_id)
            elif result.outcome == "blocked":
                stopped.add(definition_id)  # an earlier seq is pending and due, or failed: nothing recorded
    report["stopped_definitions"] = sorted(stopped)


def _dry_run(factory, today: date, report: dict) -> None:
    """The same steps inside savepoints of one session, rolled back at the end: nothing is written."""
    with factory() as db:
        try:
            for definition_id in _definition_ids(db):
                savepoint = db.begin_nested()
                try:
                    report["generated"] += generation.generate_locked(db, definition_id, today)
                    savepoint.commit()
                except ImportRunningError:
                    savepoint.rollback()
                    report["status"] = "import_running"
                    return
            stopped: set[int] = set()
            for instance_id, definition_id in due_instances(db, today):
                if definition_id in stopped:
                    continue
                savepoint = db.begin_nested()
                try:
                    result = _post_job_one(db, instance_id, definition_id, today)
                    savepoint.commit()
                except ImportRunningError:
                    savepoint.rollback()
                    report["status"] = "import_running"
                    break
                except (ValidationError, ConflictError):
                    savepoint.rollback()
                    report["failed"].append(instance_id)
                    stopped.add(definition_id)
                    continue
                if result.outcome == "posted":
                    report["posted"].append(instance_id)
                elif result.outcome == "blocked":
                    stopped.add(definition_id)
            report["stopped_definitions"] = sorted(stopped)
        finally:
            db.rollback()
    if report["status"] == "completed":
        report["status"] = "dry_run"


def run(engine: Engine, trigger: str, *, today: date | None = None, dry_run: bool = False, post: bool = True) -> dict:
    """post=False (R-F4, the importer CLI with the scheduler switched off): generation only, then the count of due
    but unposted instances (report["due_unposted"], logged)."""
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    today = today or ledger_service._today()
    report = _new_report(trigger, today)
    with engine.connect() as lock_conn:
        acquired = lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULE_JOB_LOCK_KEY}).scalar_one()
        lock_conn.commit()
        if not acquired:
            report["status"] = "busy"
        else:
            try:
                factory = sessionmaker(bind=engine, autoflush=False)
                if dry_run:
                    _dry_run(factory, today, report)
                elif _generate_all(factory, today, report):
                    if post:
                        _post_due(factory, today, report)
                    else:
                        with factory() as db:
                            report["due_unposted"] = len(due_instances(db, today))
                        logger.info("schedule_job.due_unposted", extra={"count": report["due_unposted"]})
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SCHEDULE_JOB_LOCK_KEY})
                lock_conn.commit()
    logger.info(
        "schedule_job.run",
        extra={
            "trigger": trigger, "run_status": report["status"], "generated": report["generated"],
            "posted_ids": report["posted"], "failed_ids": report["failed"],
            "stopped_definitions": report["stopped_definitions"],
        },
    )
    return report


def _job(engine: Engine, trigger: str) -> None:
    try:
        report = run(engine, trigger)
    except Exception as exc:  # noqa: BLE001 — the scheduler thread must survive
        logger.exception("schedule_job.crashed", extra={"trigger": trigger, "error_class": exc.__class__.__name__})
        report = {"status": "crashed", "today": ledger_service._today().isoformat()}
    _after_run(engine, report)


def _after_run(engine: Engine, report: dict, scheduler=None) -> None:
    """busy / import_running / crashed → retry every 10 minutes that Taipei day; a completed run clears the retry."""
    scheduler = scheduler if scheduler is not None else _scheduler
    if scheduler is None:
        return
    if report["status"] in RETRY_STATUSES:
        scheduler.add_job(
            _retry, IntervalTrigger(minutes=RETRY_MINUTES, timezone=TZ_NAME), id=RETRY_JOB_ID, replace_existing=True,
            kwargs={"engine": engine, "day": report["today"]}, coalesce=True, max_instances=1,
        )
    elif scheduler.get_job(RETRY_JOB_ID) is not None:
        scheduler.remove_job(RETRY_JOB_ID)


def _retry(engine: Engine, day: str) -> None:
    if ledger_service._today().isoformat() != day:  # a new Taipei day: the 00:05 run takes over
        if _scheduler is not None and _scheduler.get_job(RETRY_JOB_ID) is not None:
            _scheduler.remove_job(RETRY_JOB_ID)
        return
    _job(engine, "retry")


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TZ_NAME)
    scheduler.add_job(
        _job, CronTrigger(hour=0, minute=5, timezone=TZ_NAME), id=DAILY_JOB_ID, replace_existing=True,
        kwargs={"engine": engine, "trigger": "cron"}, coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _job, DateTrigger(run_date=datetime.now(ZoneInfo(TZ_NAME)) + timedelta(seconds=STARTUP_DELAY_SEC)),
        id=STARTUP_JOB_ID, replace_existing=True, kwargs={"engine": engine, "trigger": "startup"},
    )
    return scheduler


def start(engine: Engine) -> None:
    global _scheduler
    if not is_enabled():
        logger.info("schedule_job.scheduler_disabled")
        return
    if _scheduler is not None:
        return
    try:
        _scheduler = build_scheduler(engine)
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001 — the API stays up without the job; run-now still works
        _scheduler = None
        logger.exception("schedule_job.scheduler_failed", extra={"error_class": exc.__class__.__name__})
        return
    logger.info("schedule_job.scheduler_started", extra={"jobs": [job.id for job in _scheduler.get_jobs()]})


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("schedule_job.scheduler_stopped")


def trigger_after_import(engine: Engine) -> None:
    """A run right after a real backup import, once the import has released its advisory lock (D34)."""
    if _scheduler is None:
        return
    _scheduler.add_job(
        _job, DateTrigger(run_date=datetime.now(ZoneInfo(TZ_NAME))), id=IMPORT_JOB_ID, replace_existing=True,
        kwargs={"engine": engine, "trigger": "import"},
    )


def run_after_cli_import(engine: Engine, *, today: date | None = None) -> dict:
    """Multica R-F4: the standalone importer CLI has no scheduler, so trigger_after_import would do nothing. Once the
    CLI import has released the import lock it runs the job inline in its own process: generation always; posting
    only when ACCOUNTING_SCHEDULER_ENABLED is truthy (is_enabled, the scheduler's own parsing); otherwise the run logs
    how many instances are due but unposted. The API path keeps trigger_after_import."""
    return run(engine, "import", today=today, post=is_enabled())


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.schedule_job")
    parser.add_argument("--dry-run", action="store_true", help="print what would be generated and posted; write nothing")
    args = parser.parse_args(argv)
    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    report = run(engine, "manual", dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in ("completed", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] 15.7 Edit `services/accounting-service/app/main.py`: add `from .services import schedule_job` below the routers import and append

```python


@app.on_event("startup")
def _start_schedule_job() -> None:
    schedule_job.start(engine)


@app.on_event("shutdown")
def _stop_schedule_job() -> None:
    schedule_job.stop()
```

- [ ] 15.8 Append to `services/accounting-service/app/routers/schedules.py` (and add `schedule_job, schedule_locks` to its `from ..services import schedule_read, schedule_service` line):

```python
@router.post("/run-now", response_model=RunReportOut)
def run_now(engine: Engine = Depends(get_engine)):
    """立即執行: the daily job, now (D40, D42: same perimeter as every endpoint; the advisory locks make a repeat safe)."""
    if not schedule_locks.import_key_free(engine):
        raise HTTPException(status_code=409, detail="import_running")
    report = schedule_job.run(engine, "manual")
    if report["status"] == "busy":
        raise HTTPException(status_code=409, detail="schedule job already running")
    return report
```

- [ ] 15.9 Run 15.5 again.

Expected: `10 passed` (unit) and `19 passed` (integration), 29 in all.

- [ ] 15.10 Confirm the CLI parses (no database call with `--help`).

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/python -m app.services.schedule_job --help | head -3
```

Expected: `usage: python -m app.services.schedule_job [-h] [--dry-run]`.

- [ ] 15.11 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/requirements.txt services/accounting-service/app/services/schedule_job.py \
  services/accounting-service/app/main.py services/accounting-service/app/routers/schedules.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/unit/test_schedule_job_scheduler.py \
  services/accounting-service/tests/integration/test_schedule_job.py
git commit -m "feat(accounting): daily schedule job with retries, dry run and run-now

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 16. Backup JSON fields and pure mapping `schedule_import_map`

**Model:** opus

**Files:**
- Modify: `services/accounting-service/app/services/moze_backup_json.py` (`AHPeriod` / `AHInstallment` fields; `DATES` kind)
- Create: `services/accounting-service/app/services/schedule_import_map.py`
- Modify: `services/accounting-service/tests/conftest.py` (`backup.period`, `backup.installment` builders)
- Modify: `services/accounting-service/tests/integration/test_backup_replace_and_report.py` (`_doc` uses the builders)
- Test: `services/accounting-service/tests/unit/test_schedule_import_map.py`

**Interfaces:**
- Consumes: `BackupData`, `category_name` (`moze_backup_json`); `schedule_rules` (Task 4).
- Produces: `moze_backup_json.DATES`; validated fields `AHPeriod {identifier, unit, days, times, type, startDate}` and `AHInstallment {identifier, dayOfMonth, dateInfo, times, total, remainder}` (`dateInfo` → a list of `datetime`, a dict being ordered by its integer keys); `schedule_import_map.MOZE_LINE_KINDS`, `REWARD_TYPE`, `moze_weekday`, `MappedLine`, `MappedInstance`, `MappedDefinition`, `MapResult`, `record_groups(records, data)`, `map_schedules(data)`; conftest builders `backup.period(identifier="PER-1", *, unit=2, days=21, times=0, type_=0, start="2026-10-21T00:00:00", **fields)` and `backup.installment(identifier="INS-1", *, day_of_month=9, dates=(), times=36, total=300000, remainder=0, **fields)`.

Rules (D37, spec "Schedule definitions and instances from the backup"; this module touches no database):
- Records of type 0–6 and 15 whose `eventID` names an `AHPeriod` / `AHInstallment` belong to it (past and future). An **enabled future** record of those types without a known `eventID` becomes a single definition (`moze_id = "record:" + identifier`, `times = 1`); an unknown `eventID` is also listed under `review` as `event_missing`. Enabled future type-14 records count as `rewards_ignored`; enabled future records of any other type count under `unsupported_types` (by type, as a string) and are ignored. `records_mapped` counts every enabled future record assigned to a definition (both legs of a transfer, repayment and interest), so `records_mapped + rewards_ignored + Σ unsupported_types` = the enabled future records.
- `record_groups`: a transfer's in-leg joins its out-leg (by `AHTransfer`), a type-15 record joins the type-5/6 record of the same `packageID` on the same date; the group's first record is the primary (`moze_id` of the instance).
- `AHPeriod`: `anchor_date` = the earliest group's date, `first_seq` 1; `unit` 1 → `week` (`days` must equal `moze_weekday(anchor)`, 1 = Sunday … 7 = Saturday), `unit` 2 → `month` with `day_of_month = days` (the anchor's day must equal `days` clamped to its month); `times` 0 → `None`; `startDate` must be an occurrence; `type` 1 must come with a transfer primary record and `type` 0 with a non-transfer one; any other unit or failed check → `review_reason = interval_mismatch` (and the definition goes under `review`). Seq = 1 + occurrence index from the anchor; for a mismatched definition every instance takes its position (1, 2, …) instead (Task 6 never generates for an `interval_mismatch` definition, so that invented rule never rolls forward). A finite period's `times` is capped at its highest mapped seq: MOZE pre-generates a finite series, and when MOZE deleted early records the seqs (counted from the earliest record left) end below `times`, so HomeHub must not generate periods past MOZE's last record (Task 28.5 confirms the record counts).
- `AHInstallment`: `kind = installment` (`recurring` when `times < 2`), `interval_unit = month`, `day_of_month = dayOfMonth`, `anchor_date = dateInfo[0]`, `times`, `total_amount = |total|` (None when 0), `remainder = |remainder|`; `dateInfo[k]` must equal `add_months(anchor, k, dayOfMonth)`, else `interval_mismatch`; lines from the earliest group whose primary is type 6 or 0; seq = 1-based position of the date in `dateInfo` (a date outside it → `review` `interval_mismatch`, not mapped).
- Template lines come from the first group, plus one line for each record kind a later group carries more often (`_union_lines`; a first package without its interest record still yields an interest line, and that period's interest amount is `"0"`); amounts stay per instance, so no later amount is dropped. (The template's per-line amount stays the first group's, as D37 says; the owner may prefer the latest price — listed in Known Spec Conflicts.)
- `past_singles`: every past record of a supported type without a known definition is also returned as a `record:<id>` single (past instance); it is an ordinary entry unless an earlier import created that `record:<id>` definition, which the importer checks (Task 17 `merge_known_past_singles`). Cost accepted: this maps every past record without a definition on each import (one `record_groups` pass plus `_map_single` per group) although `merge_known_past_singles` keeps only the few with a known `record:<id>` definition. The step is pure, linear in the backup's record count, touches no database and runs once per import, and mapping lazily would need the known ids from the database inside the pure module; revisit only if Task 28's dry run shows the mapping step dominating the import time.
- Lines: transfer group → one `transfer` line (out-leg account and `|total|`, in-leg account and `|total|` as `to_account` / `to_amount`); otherwise one line per record (`MOZE_LINE_KINDS`), `related` = `relatedID` on repayment / collection lines. Instance amounts: `|total|` per line matched by kind, `"0"` for a missing member. Name: the primary's `name`, else its classification's (or category's) name, else `週期` / `分期` / `單筆`. Two groups of one definition on the same date → the second is listed under `review` as `same_date` and not mapped.

- [ ] 16.1 Add the builders. In `services/accounting-service/tests/conftest.py`, below `_bk_package`, add

```python
def _bk_period(identifier="PER-1", *, unit=2, days=21, times=0, type_=0, start="2026-10-21T00:00:00", **fields) -> dict:
    return {
        "identifier": identifier, "unit": unit, "days": days, "times": times, "type": type_, "startDate": start,
        "count": 1, "startIndex": 1, **fields,
    }


def _bk_installment(identifier="INS-1", *, day_of_month=9, dates=(), times=36, total=300000, remainder=0, **fields) -> dict:
    return {
        "identifier": identifier, "account": "A-WALLET", "dayOfMonth": day_of_month,
        "dateInfo": {str(index): day for index, day in enumerate(dates)}, "times": times, "total": total,
        "installment": 0, "remainder": remainder, "startDate": dates[0] if dates else "2026-01-01T00:00:00",
        "interestType": 0, "interestRate": 0, **fields,
    }
```

  and add `period=_bk_period, installment=_bk_installment,` to the `SimpleNamespace(...)` the `backup` fixture returns.

  In `services/accounting-service/tests/integration/test_backup_replace_and_report.py`, inside `_doc`, replace

```python
        periods=[{"identifier": "PER-1", "startDate": "2026-01-05T00:00:00", "unit": 2}],
        installments=[{"identifier": "INS-1", "startDate": "2026-03-09T00:00:00", "installment": 3000.5,
                       "dateInfo": {"0": "2026-11-09T00:00:00"}}],
```

  with

```python
        periods=[backup.period("PER-1", unit=2, days=5, start="2026-01-05T00:00:00")],
        installments=[backup.installment("INS-1", dates=("2026-11-09T00:00:00", "2026-12-09T00:00:00"), times=2, total=6001)],
```

- [ ] 16.2 Write the failing test `services/accounting-service/tests/unit/test_schedule_import_map.py`:

```python
"""Pure mapping of MOZE scheduled data (spec "Schedule definitions and instances from the backup", D37)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services import schedule_rules as rules
from app.services.moze_backup_json import parse_backup_doc
from app.services.moze_csv import MozeImportError
from app.services.schedule_import_map import map_schedules, moze_weekday


def _accounts(backup):
    return [backup.account("A-WALLET", "錢包"), backup.account("A-BANK", "銀行")]


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def test_moze_weekday_numbering():
    assert [moze_weekday(date(2026, 9, 20 + offset)) for offset in range(7)] == [1, 2, 3, 4, 5, 6, 7]  # Sun … Sat


def test_weekly_period_keyed_by_weekday(backup):
    # Spec "Weekly period keyed by weekday" (exported 2026-10-01).
    days = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-W", unit=1, days=2, times=0, start="2026-10-05T00:00:00")],
        records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in days],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert (
        definition.kind, definition.source, definition.interval_unit, definition.interval_n, definition.anchor_date,
        definition.times, definition.day_of_month, definition.review_reason,
    ) == ("recurring", "period", "week", 1, date(2026, 9, 21), None, None, None)
    assert [(item.seq, item.day, item.past, item.enabled) for item in definition.instances] == [
        (1, date(2026, 9, 21), True, True), (2, date(2026, 9, 28), True, True),
        (3, date(2026, 10, 5), False, True), (4, date(2026, 10, 12), False, True),
    ]
    assert result.records_mapped == 2


def test_monthly_transfer_period_keyed_by_day_of_month(backup):
    # Spec "Monthly period keyed by day of month".
    days = [rules.add_months(date(2026, 8, 21), k) for k in range(12)]
    records, transfers = [], []
    for k, day in enumerate(days):
        records += [
            _rec(backup, f"O-{k}", day.isoformat(), type_=2, price=-15000, eventID="PER-M"),
            _rec(backup, f"I-{k}", day.isoformat(), type_=2, price=15000, account="A-BANK", eventID="PER-M"),
        ]
        transfers.append(backup.transfer(f"X-{k}", f"O-{k}", f"I-{k}"))
    data = backup.data(
        accounts=_accounts(backup), records=records, transfers=transfers,
        periods=[backup.period("PER-M", unit=2, days=21, times=12, type_=1, start="2026-10-21T00:00:00")],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert (definition.interval_unit, definition.day_of_month, definition.anchor_date, definition.times) == (
        "month", 21, date(2026, 8, 21), 12,
    )
    [line] = definition.lines
    assert (line.kind, line.account, line.to_account, line.amount, line.to_amount) == (
        "transfer", "A-WALLET", "A-BANK", "15000", "15000",
    )
    assert len(definition.instances) == 12
    assert definition.instances[0].record_ids == ["O-0", "I-0"] and definition.instances[0].amounts == ["15000"]
    assert result.records_mapped == 20


def test_day_of_month_clamped(backup):
    # Spec "Day of month clamped".
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-31", unit=2, days=31, start="2027-01-31T00:00:00")],
        records=[_rec(backup, "R-JAN", "2027-01-31", eventID="PER-31"), _rec(backup, "R-FEB", "2027-02-28", eventID="PER-31")],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.day_of_month, definition.review_reason) == (31, None)
    assert [item.seq for item in definition.instances] == [1, 2]


def test_weekday_mismatch_flagged(backup):
    # Spec "Weekday mismatch flagged".
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-T", unit=1, days=3, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "R-1", "2026-09-21", eventID="PER-T"), _rec(backup, "R-2", "2026-09-28", eventID="PER-T")],
    )
    result = map_schedules(data)
    assert result.definitions[0].review_reason == "interval_mismatch"
    assert {"moze_id": "PER-T", "reason": "interval_mismatch"} in result.review
    assert [item.seq for item in result.definitions[0].instances] == [1, 2]


def test_installment_anchored_on_its_first_period(backup):
    # Spec "Installment anchored on its first period".
    dates = [f"{rules.add_months(date(2026, 2, 9), k).isoformat()}T00:00:00" for k in range(36)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", day_of_month=9, dates=dates, times=36, total=300000, remainder=150000)],
        records=[_rec(backup, "R-NOV", "2026-11-09", type_=6, price=-8333, eventID="INS-1", relatedID="R-LOAN")],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.kind, definition.anchor_date, definition.day_of_month, definition.times) == (
        "installment", date(2026, 2, 9), 9, 36,
    )
    assert (definition.total_amount, definition.remainder) == (Decimal("300000"), Decimal("150000"))
    assert [(line.kind, line.related) for line in definition.lines] == [("repayment", "R-LOAN")]
    assert [(item.seq, item.day) for item in definition.instances] == [(10, date(2026, 11, 9))]


def test_repayment_and_interest_are_one_period(backup):
    # Spec "Repayment and interest are one period".
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)],
        records=[
            _rec(backup, "R-REP", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-9", relatedID="R-LOAN"),
            _rec(backup, "R-INT", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-9"),
        ],
    )
    [definition] = map_schedules(data).definitions
    [instance] = definition.instances
    assert [line.kind for line in definition.lines] == ["repayment", "interest"]
    assert (instance.moze_id, instance.record_ids, instance.amounts, instance.seq) == (
        "R-REP", ["R-REP", "R-INT"], ["8333", "612"], 2,
    )


def test_disabled_future_period(backup):
    # Spec "Disabled future period" (mapping side: the instance is not enabled; Task 17 makes it skipped).
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-D", unit=2, days=3, start="2026-11-03T00:00:00")],
        records=[
            _rec(backup, "R-NOV", "2026-11-03", eventID="PER-D"),
            _rec(backup, "R-DEC", "2026-12-03", eventID="PER-D", isEnabled=False),
        ],
    )
    result = map_schedules(data)
    assert [(item.day, item.enabled) for item in result.definitions[0].instances] == [
        (date(2026, 11, 3), True), (date(2026, 12, 3), False),
    ]
    assert result.records_mapped == 1


def test_single_record_keyed_apart_from_definitions(backup):
    # Spec "Single record keyed apart from definitions".
    data = backup.data(accounts=_accounts(backup), records=[_rec(backup, "X1", "2026-11-20", name="年費")])
    [definition] = map_schedules(data).definitions
    assert (definition.moze_id, definition.kind, definition.source, definition.times, definition.anchor_date, definition.name) == (
        "record:X1", "recurring", "single", 1, date(2026, 11, 20), "年費",
    )
    assert [(item.seq, item.moze_id) for item in definition.instances] == [(1, "X1")]


def test_counts_add_up_to_the_enabled_future_records(backup):
    data = backup.data(
        accounts=_accounts(backup),
        records=[
            _rec(backup, "R-RW", "2026-11-01", type_=14, price=30),
            _rec(backup, "R-ADJ", "2026-11-01", type_=7, price=100),
            _rec(backup, "R-OFF", "2026-11-01", isEnabled=False),
            _rec(backup, "R-GONE", "2026-11-02", eventID="GONE"),
            _rec(backup, "R-PAST", "2026-09-01"),
        ],
    )
    result = map_schedules(data)
    assert (result.records_mapped, result.rewards_ignored, dict(result.unsupported_types)) == (1, 1, {"7": 1})
    assert [definition.moze_id for definition in result.definitions] == ["record:R-GONE"]
    assert {"moze_id": "R-GONE", "reason": "event_missing"} in result.review
    # The past record stays an ordinary entry, but is offered as a past single (the importer keeps it only when an
    # earlier import made it a record:R-PAST definition); the disabled future one is neither.
    assert [(definition.moze_id, definition.instances[0].past) for definition in result.past_singles] == [
        ("record:R-PAST", True),
    ]


def test_a_later_package_kind_joins_the_template(backup):
    # The first package lacks its interest record: the template still gets an interest line, so the later
    # interest amounts are posted, and the first period's interest is "0".
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)],
        records=[
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1", relatedID="R-LOAN"),
            _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN"),
            _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-612, eventID="INS-1", packageID="PK-2"),
        ],
    )
    [definition] = map_schedules(data).definitions
    assert [line.kind for line in definition.lines] == ["repayment", "interest"]
    assert [item.amounts for item in definition.instances] == [["8333", "0"], ["8333", "612"]]


def test_period_type_must_match_its_records(backup):
    # Spec: an AHPeriod of type 1 generates transfers, type 0 ordinary records; a disagreement is reviewed.
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-X", unit=2, days=3, type_=1, start="2026-11-03T00:00:00")],
        records=[_rec(backup, "R-NOV", "2026-11-03", eventID="PER-X")],
    )
    result = map_schedules(data)
    assert result.definitions[0].review_reason == "interval_mismatch"
    assert {"moze_id": "PER-X", "reason": "interval_mismatch"} in result.review


def test_finite_period_never_runs_past_its_last_record(backup):
    # MOZE deleted the first three records of a 12-time series: the 9 left are seqs 1–9, so times becomes 9 and
    # HomeHub never generates the three phantom periods after MOZE's real end.
    days = [rules.add_months(date(2026, 11, 3), k) for k in range(9)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-F", unit=2, days=3, times=12, start="2026-11-03T00:00:00")],
        records=[_rec(backup, f"R-{k}", day.isoformat(), eventID="PER-F") for k, day in enumerate(days)],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.times, [item.seq for item in definition.instances][-1]) == (9, 9)


def test_same_date_records_are_reviewed(backup):
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-2", unit=2, days=5, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "R-A", "2026-10-05", eventID="PER-2"), _rec(backup, "R-B", "2026-10-05", eventID="PER-2")],
    )
    result = map_schedules(data)
    assert len(result.definitions[0].instances) == 1
    assert {"moze_id": "R-B", "reason": "same_date"} in result.review


def test_period_and_installment_fields_are_validated(backup):
    doc = backup.doc(accounts=_accounts(backup), periods=[{"identifier": "PER-X", "unit": 2}])
    with pytest.raises(MozeImportError, match="AHPeriod 'PER-X': missing field 'days'"):
        parse_backup_doc(doc)
    as_list = backup.installment("INS-L", dates=())
    as_list["dateInfo"] = ["2026-11-09T00:00:00", "2026-12-09T00:00:00"]
    as_dict = backup.installment("INS-D", dates=())
    as_dict["dateInfo"] = {str(k): f"2027-{k + 1:02d}-09T00:00:00" for k in range(11)}
    data = parse_backup_doc(backup.doc(accounts=_accounts(backup), installments=[as_list, as_dict]))
    assert data.installments[0]["dateInfo"] == [datetime(2026, 11, 9), datetime(2026, 12, 9)]
    assert data.installments[1]["dateInfo"][9:] == [datetime(2027, 10, 9), datetime(2027, 11, 9)]
```

- [ ] 16.3 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/unit/test_schedule_import_map.py
```

Expected: `ModuleNotFoundError: No module named 'app.services.schedule_import_map'`.

- [ ] 16.4 Edit `services/accounting-service/app/services/moze_backup_json.py`:
  - replace `S, SN, NUM, INT, BOOL, DT, DTN, LIST, DICT = "str", "str?", "num", "int", "bool", "dt", "dt?", "list", "dict"` with

```python
S, SN, NUM, INT, BOOL, DT, DTN, LIST, DICT = "str", "str?", "num", "int", "bool", "dt", "dt?", "list", "dict"
DATES = "dates"  # AHInstallment.dateInfo: a list, or an object keyed "0", "1", … — normalised to a list of datetimes
```

  - in `CLASS_FIELDS` replace `"AHPeriod": {"identifier": S},` and `"AHInstallment": {"identifier": S},` with

```python
    "AHPeriod": {"identifier": S, "unit": INT, "days": INT, "times": INT, "type": INT, "startDate": DTN},
    "AHInstallment": {
        "identifier": S, "dayOfMonth": INT, "dateInfo": DATES, "times": INT, "total": NUM, "remainder": NUM,
    },
```

  - in `_normalise`, insert before the `expected = {...}` line

```python
    if base == "dates":
        items = value
        if isinstance(value, dict):
            try:
                items = [value[key] for key in sorted(value, key=int)]
            except ValueError:
                items = None
        if isinstance(items, list):
            parsed_items = [_datetime(item) for item in items]
            if all(item is not None for item in parsed_items):
                return parsed_items
```

  and add `"dates": "a list of ISO date-times",` to the `expected` dict.

- [ ] 16.5 Create `services/accounting-service/app/services/schedule_import_map.py`:

```python
"""Pure mapping of MOZE's scheduled data to schedule definitions and instances (design D37).

No database here: MOZE identifiers stay as they are; schedule_import resolves them to HomeHub rows inside the
importer's ledger transaction.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import schedule_rules as rules
from .moze_backup_json import BackupData, category_name

MOZE_LINE_KINDS: dict[int, str] = {
    0: "expense", 1: "income", 2: "transfer", 3: "receivable", 4: "payable", 5: "collection", 6: "repayment",
    15: "interest",
}
REWARD_TYPE = 14
TRANSFER_TYPE = 2
INTEREST_TYPE = 15
SETTLING_TYPES = (5, 6)
INSTALLMENT_PRIMARY_TYPES = (6, 0)


def moze_weekday(day: date) -> int:
    """MOZE's (Apple Calendar's) weekday numbering: Sunday 1 … Saturday 7. Confirmed by Task 28 on the real backup."""
    return day.isoweekday() % 7 + 1


@dataclass
class MappedLine:
    kind: str
    account: str | None
    amount: str
    to_account: str | None = None
    to_amount: str | None = None
    target: str | None = None
    classification: str | None = None
    project: str | None = None
    related: str | None = None
    name: str | None = None
    store: str | None = None


@dataclass
class MappedInstance:
    moze_id: str
    record_ids: list[str]
    day: date
    seq: int | None
    amounts: list[str]
    past: bool
    enabled: bool
    records: list[dict]


@dataclass
class MappedDefinition:
    moze_id: str
    kind: str
    source: str  # period | installment | single
    name: str
    interval_unit: str
    interval_n: int
    anchor_date: date
    day_of_month: int | None
    times: int | None
    total_amount: Decimal | None
    remainder: Decimal | None
    lines: list[MappedLine]
    review_reason: str | None
    payload: dict | None
    instances: list[MappedInstance] = field(default_factory=list)


@dataclass
class MapResult:
    definitions: list[MappedDefinition] = field(default_factory=list)
    records_mapped: int = 0
    rewards_ignored: int = 0
    unsupported_types: Counter = field(default_factory=Counter)
    review: list[dict] = field(default_factory=list)
    # Past records without a definition, as `record:<id>` singles. Ordinary entries — unless HomeHub already holds
    # that record:<id> definition (a single future record of an earlier import that has turned past); the importer
    # keeps only those (schedule_import.merge_known_past_singles).
    past_singles: list[MappedDefinition] = field(default_factory=list)


def _abs(value) -> str:
    return rules.plain(abs(Decimal(str(value))))


def _text(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _day(record: dict) -> date:
    return record["date"].date()


def record_groups(records: list[dict], data: BackupData) -> list[list[dict]]:
    """Records of one definition merged into periods, earliest first: a transfer's in-leg joins its out-leg; a
    type-15 record joins the type-5/6 record of the same package on the same date. The first record is the primary."""
    by_id = {record["identifier"]: record for record in records}
    in_of_out = {
        transfer["outRecord"]: transfer["inRecord"]
        for transfer in data.transfers
        if transfer["outRecord"] in by_id and transfer["inRecord"] in by_id
    }
    merged_ins = set(in_of_out.values())
    settling = {}
    for record in records:
        if record["type"] in SETTLING_TYPES and record["packageID"]:
            settling.setdefault((record["packageID"], _day(record)), record["identifier"])

    def attached_interest(record: dict) -> str | None:
        if record["type"] != INTEREST_TYPE or not record["packageID"]:
            return None
        return settling.get((record["packageID"], _day(record)))

    groups: dict[str, list[dict]] = {}
    for record in sorted(records, key=lambda item: (item["date"], item["identifier"])):
        identifier = record["identifier"]
        if identifier in merged_ins or attached_interest(record) is not None:
            continue
        groups[identifier] = [record]
        if identifier in in_of_out:
            groups[identifier].append(by_id[in_of_out[identifier]])
    for record in sorted(records, key=lambda item: item["identifier"]):
        primary = attached_interest(record)
        if primary is not None and primary in groups:
            groups[primary].append(record)
    return list(groups.values())


def _lines(group: list[dict]) -> list[MappedLine]:
    primary = group[0]
    if primary["type"] == TRANSFER_TYPE:
        in_leg = group[1] if len(group) > 1 else None
        return [
            MappedLine(
                kind="transfer", account=primary["account"], amount=_abs(primary["total"]),
                to_account=in_leg["account"] if in_leg else None, to_amount=_abs(in_leg["total"]) if in_leg else None,
                classification=primary["classification"], project=primary["project"], name=_text(primary["name"]),
                store=_text(primary["store"]),
            )
        ]
    return [
        MappedLine(
            kind=MOZE_LINE_KINDS[record["type"]], account=record["account"], amount=_abs(record["total"]),
            target=record["target"], classification=record["classification"], project=record["project"],
            related=record["relatedID"] if record["type"] in SETTLING_TYPES else None, name=_text(record["name"]),
            store=_text(record["store"]),
        )
        for record in group
    ]


def _union_lines(groups: list[list[dict]]) -> list[MappedLine]:
    """Template lines of the first group, plus a line for each record kind a later group carries more often than the
    template (a first package without its interest record): no later amount is dropped by _amounts. A first transfer
    without its in-leg takes the in-leg account of a later group."""
    lines = _lines(groups[0])
    for group in groups[1:]:
        candidates = _lines(group)
        for candidate in candidates:
            if candidate.kind == "transfer":
                first = next(line for line in lines if line.kind == "transfer")
                if first.to_account is None and candidate.to_account is not None:
                    first.to_account, first.to_amount = candidate.to_account, candidate.to_amount
                continue
            have = sum(1 for line in lines if line.kind == candidate.kind)
            need = sum(1 for line in candidates if line.kind == candidate.kind)
            if have < need:
                lines.append(candidate)
    return lines


def _amounts(lines: list[MappedLine], group: list[dict]) -> list[str]:
    """|total| per template line, matched by kind; "0" for a line whose package member is missing (D37)."""
    used: set[str] = set()
    amounts = []
    for line in lines:
        match = None
        for record in group:
            kind = "transfer" if record["type"] == TRANSFER_TYPE else MOZE_LINE_KINDS.get(record["type"])
            if record["identifier"] not in used and kind == line.kind:
                match = record
                break
        if match is None:
            amounts.append("0")
        else:
            used.add(match["identifier"])
            amounts.append(_abs(match["total"]))
            if line.kind == "transfer":
                used.update(record["identifier"] for record in group)  # the in-leg belongs to this line
    return amounts


def _name(group: list[dict], data: BackupData, default: str) -> str:
    primary = group[0]
    if _text(primary["name"]):
        return _text(primary["name"])[:128]
    names = {row["identifier"]: row["name"] for row in data.classifications}
    names.update({row["identifier"]: category_name(row["name"]) for row in data.categories})
    return (names.get(primary["classification"]) or default)[:128]


def _instance(group: list[dict], lines: list[MappedLine], seq: int | None, cutoff: date) -> MappedInstance:
    primary = group[0]
    return MappedInstance(
        moze_id=primary["identifier"], record_ids=[record["identifier"] for record in group], day=_day(primary),
        seq=seq, amounts=_amounts(lines, group), past=_day(primary) <= cutoff,
        enabled=all(record["isEnabled"] for record in group), records=group,
    )


def _add_instances(definition: MappedDefinition, groups, seq_of, cutoff: date, result: MapResult) -> None:
    seen: set[date] = set()
    for group in groups:
        day = _day(group[0])
        seq = seq_of(day)
        if seq is None and definition.source == "installment":
            result.review.append({"moze_id": group[0]["identifier"], "reason": "interval_mismatch"})
            continue
        if seq is None:
            definition.review_reason = "interval_mismatch"
        if day in seen:
            result.review.append({"moze_id": group[0]["identifier"], "reason": "same_date"})
            continue
        seen.add(day)
        definition.instances.append(_instance(group, definition.lines, seq, cutoff))
    if definition.review_reason == "interval_mismatch" and definition.source == "period":
        for position, item in enumerate(definition.instances, start=1):
            item.seq = position  # off-rule dates: keep every period, numbered by position (the definition is paused)


def _map_period(period: dict, records: list[dict], data: BackupData, cutoff: date, result: MapResult) -> MappedDefinition | None:
    groups = record_groups(records, data)
    if not groups:
        result.review.append({"moze_id": period["identifier"], "reason": "no_records"})
        return None
    anchor = _day(groups[0][0])
    review, unit, day_of_month = None, {1: "week", 2: "month"}.get(period["unit"]), None
    if unit is None:
        unit, review = "month", "interval_mismatch"
    elif unit == "week":
        if period["days"] != moze_weekday(anchor):
            review = "interval_mismatch"
    else:
        day_of_month = period["days"] if 1 <= period["days"] <= 31 else None
        if day_of_month is None or anchor.day != min(day_of_month, rules.days_in_month(anchor.year, anchor.month)):
            review = "interval_mismatch"
    start = period["startDate"].date() if period["startDate"] else None
    if start is not None and rules.occurrence_index(anchor, unit, 1, day_of_month, start) is None:
        review = "interval_mismatch"
    primary_is_transfer = groups[0][0]["type"] == TRANSFER_TYPE
    if period["type"] in (0, 1) and (period["type"] == 1) != primary_is_transfer:
        review = "interval_mismatch"  # spec: type 1 periods generate transfers, type 0 ordinary records
    lines = _union_lines(groups)
    definition = MappedDefinition(
        moze_id=period["identifier"], kind="recurring", source="period", name=_name(groups[0], data, "週期"),
        interval_unit=unit, interval_n=1, anchor_date=anchor, day_of_month=day_of_month,
        times=None if period["times"] == 0 else period["times"], total_amount=None, remainder=None, lines=lines,
        review_reason=review, payload=period,
    )

    def seq_of(day: date) -> int | None:
        index = rules.occurrence_index(anchor, unit, 1, day_of_month, day)
        return None if index is None else index + 1

    _add_instances(definition, groups, seq_of, cutoff, result)
    if definition.times is not None and definition.instances and definition.review_reason is None:
        # MOZE pre-generates a finite series. Seqs count from the earliest record still in the backup, so when MOZE
        # deleted early records the rebased seqs end below `times`: never let HomeHub generate past MOZE's last
        # record (Task 28.5 checks every finite period's record count against its `times`).
        definition.times = min(definition.times, max(item.seq for item in definition.instances))
    if definition.review_reason is not None:
        result.review.append({"moze_id": definition.moze_id, "reason": definition.review_reason})
    return definition


def _map_installment(
    installment: dict, records: list[dict], data: BackupData, cutoff: date, result: MapResult
) -> MappedDefinition | None:
    groups = record_groups(records, data)
    dates = [value.date() for value in installment["dateInfo"]]
    if not groups or not dates:
        result.review.append({"moze_id": installment["identifier"], "reason": "no_records"})
        return None
    anchor = dates[0]
    day_of_month = installment["dayOfMonth"] if 1 <= installment["dayOfMonth"] <= 31 else None
    review = None
    for k, day in enumerate(dates):
        if day != rules.add_months(anchor, k, day_of_month):
            review = "interval_mismatch"
    first = next((group for group in groups if group[0]["type"] in INSTALLMENT_PRIMARY_TYPES), groups[0])
    total = abs(Decimal(str(installment["total"])))
    definition = MappedDefinition(
        moze_id=installment["identifier"], kind="installment" if installment["times"] >= 2 else "recurring",
        source="installment", name=_name(first, data, "分期"), interval_unit="month", interval_n=1,
        anchor_date=anchor, day_of_month=day_of_month, times=installment["times"],
        total_amount=total if total > 0 else None, remainder=abs(Decimal(str(installment["remainder"]))),
        lines=_union_lines([first, *(group for group in groups if group is not first)]), review_reason=review,
        payload=installment,
    )
    positions = {day: index + 1 for index, day in reversed(list(enumerate(dates)))}
    _add_instances(definition, groups, positions.get, cutoff, result)
    if review is not None:
        result.review.append({"moze_id": definition.moze_id, "reason": review})
    return definition


def _map_single(group: list[dict], data: BackupData, cutoff: date) -> MappedDefinition:
    primary = group[0]
    lines = _lines(group)
    definition = MappedDefinition(
        moze_id=f"record:{primary['identifier']}", kind="recurring", source="single", name=_name(group, data, "單筆"),
        interval_unit="month", interval_n=1, anchor_date=_day(primary), day_of_month=None, times=1,
        total_amount=None, remainder=None, lines=lines, review_reason=None, payload=None,
    )
    definition.instances.append(_instance(group, lines, 1, cutoff))
    return definition


def map_schedules(data: BackupData) -> MapResult:
    cutoff = data.exported_at.date()
    result = MapResult()
    periods = {row["identifier"]: row for row in data.periods}
    installments = {row["identifier"]: row for row in data.installments}
    by_event: dict[str, list[dict]] = defaultdict(list)
    singles: list[dict] = []
    past_singles: list[dict] = []
    for record in data.records:
        enabled_future = _day(record) > cutoff and record["isEnabled"]
        if record["type"] == REWARD_TYPE:
            result.rewards_ignored += enabled_future
            continue
        if record["type"] not in MOZE_LINE_KINDS:
            if enabled_future:
                result.unsupported_types[str(record["type"])] += 1
            continue
        event = record["eventID"]
        if event and (event in periods or event in installments):
            by_event[event].append(record)
            result.records_mapped += enabled_future
            continue
        if not enabled_future:
            if _day(record) <= cutoff:
                past_singles.append(record)  # an ordinary entry, unless HomeHub holds its record:<id> definition
            continue
        if event:
            result.review.append({"moze_id": record["identifier"], "reason": "event_missing"})
        singles.append(record)
        result.records_mapped += 1
    for identifier, period in periods.items():
        mapped = _map_period(period, by_event.get(identifier, []), data, cutoff, result)
        if mapped is not None:
            result.definitions.append(mapped)
    for identifier, installment in installments.items():
        mapped = _map_installment(installment, by_event.get(identifier, []), data, cutoff, result)
        if mapped is not None:
            result.definitions.append(mapped)
    for group in record_groups(singles, data):
        result.definitions.append(_map_single(group, data, cutoff))
    result.past_singles = [_map_single(group, data, cutoff) for group in record_groups(past_singles, data)]
    return result
```

- [ ] 16.6 Run 16.3 again, then the backup JSON and import suites.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/unit/test_schedule_import_map.py
.venv/bin/pytest -q -p no:warnings tests/unit/test_moze_backup_json.py tests/integration/test_backup_replace_and_report.py tests/integration/test_backup_entries_import.py 2>&1 | tail -1
```

Expected: `15 passed`; then the same pass counts as before this task, 0 failed.

- [ ] 16.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/moze_backup_json.py services/accounting-service/app/services/schedule_import_map.py \
  services/accounting-service/tests/conftest.py services/accounting-service/tests/unit/test_schedule_import_map.py \
  services/accounting-service/tests/integration/test_backup_replace_and_report.py
git commit -m "feat(accounting): map MOZE periods, installments and future records to schedules

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 17. Importer DB side `schedule_import`

**Model:** opus

**Files:**
- Create: `services/accounting-service/app/services/schedule_import.py`
- Modify: `services/accounting-service/app/services/moze_backup_import_service.py` (`insert_entries` counts disabled future rows as `disabled_skipped`; `replace_ledger_from_backup` maps, locks schedule rows, applies schedules and reports them)
- Test: `services/accounting-service/tests/integration/test_schedule_import_apply.py`

**Interfaces:**
- Consumes: `map_schedules`, `MapResult`, `MappedDefinition`, `MappedInstance` (Task 16); `SettingsResult`, `EntryResult` (phase 2a importer); `LINE_KEYS`, `LEDGER_KINDS`, `LOAN_LINE_KINDS`, `loan_line`, `referenced_ids` (Task 5); `settlement_service.open_amount`; `generation.generate` (tests).
- Produces: everything listed for `schedule_import.py` in the Interface Contract (`CapturedLinks`, `Covered`, `lock_schedule_rows`, `capture_links`, `covered_records`, `template_usage`, `apply_schedules`, `restore_links`, `stand_in_entry_ids`, `new_report(mapped) -> dict`, `merge_known_past_singles`, `loan_check`); the summary key `schedules` of the backup import (shape in the contract). Task 18 wires `capture_links`, `covered_records`, `template_usage`, `restore_links` and `stand_in_entry_ids` and moves `loan_check` after `restore_links`; this task calls `lock_schedule_rows`, `merge_known_past_singles`, `apply_schedules` with no covered records, then `loan_check`.

Rules (D37, spec "Schedule definitions and instances from the backup", "Transactional full replace and report"):
- New imported definitions: `created_locally = false`, `status = active` (`paused` with a `review_reason`), `posting_mode = auto`, `auto_post_from` = the import's Taipei date (`ledger_service._today()`), `first_seq = 1`, `moze_payload` = the raw row. Lines resolve MOZE ids through `SettingsResult` (accounts, categories — kept only when of the line's ledger kind —, projects, counterparties) and the inserted entries (`loan_entry_id` from the repayment's `relatedID`; missing → `review_reason = loan_missing`, listed under `review`); `to_amount` only for cross-currency transfers; an unknown account → `account_missing` under `review`, definition not written.
- Re-import: upsert by `moze_id`; rule, template, name, times, total and payload refreshed; `posting_mode`, `auto_post_from`, `first_seq` kept; a review reason new in this import pauses (unless ended), a resolved one is cleared without touching `status`.
- Instances: matched by `moze_id` (over every row first), then by any of `moze_record_ids` (MOZE changed the package's primary record: the row takes the new `moze_id`), then adopted (no `moze_id`, `rule_date` = the record date, else `due_date`): an adopted row gets `moze_id` and `moze_record_ids`, and its override when pending and not owner-edited. New rows: disabled → `skipped` (`acted_by = import`, `acted_at` = import start); past with imported entries → `posted` (`acted_by = import`); past without entries → `skipped`; future → `pending` (not created for an `ended` definition); a seq already used → `review` `seq_conflict`, record not mapped. Existing pending rows: owner-edited → kept (`kept_owner_edited`); record now past or disabled → recomputed as above; else the override is refreshed and, for a row matched by its `moze_id` (not adopted in this import), `rule_date` / `due_date` follow MOZE's date unless another posted or pending (any non-skipped) instance already holds that day — Task 12 refuses the same move with 422 `due_date`, and two pending periods on one day would collide on `ux_schedule_instance_posted_day` (`updated` when anything changed, else `kept`); a row adopted in this import keeps its dates (spec "Moved period adopted"; an owner-moved row is owner-edited anyway). A status leaving `pending` clears `last_error`, `last_error_at`, `reopened_at` and `note`. `acted_by = import` rows are recomputed from the new entries — also those whose record left the backup (no entries left → `skipped`, `acted_by = import`), in a present definition and in an absent one. When the template changes, every instance's `amount_override` is realigned with `realign_override` (owner-edited and posted rows included). A definition whose lines cannot resolve (`account_missing`) keeps its template untouched but its instances are still recomputed, MOZE's per-period amounts realigned from the mapped lines to the kept template's lines (`realign_override`), so no override goes out of line with the template. A MOZE period with no enabled future record left (not live: cancelled or finished in MOZE, or not yet pre-generated during the mirror month) is set `ended` with `review_reason = not_live`, so HomeHub never generates or auto-posts after MOZE stopped; the import deletes no extra pending row for it — MOZE-sourced pending rows whose record left the backup go by the ordinary rule below, owner-edited and HomeHub-generated pending rows stay (D37). The end is reversible: a later import whose mapped period has an enabled future record sets an `ended` + `not_live` definition back to `active` (`paused` when the mapping gives a review reason), clears the reason, and creates the pending periods. An `ended` definition with any other reason (the owner's 結束) is never revived; the owner's `end` clears a stale `not_live` (Task 11). `not_live` is stored on the definition only (it shows 需檢查, since the spec's `needs_check` is true whenever `review_reason` is set) and is not listed under `schedules.review`. Rows posted by `auto` / `owner` and rows skipped by anyone but `import` are kept. Imported pending rows absent from the backup are deleted unless owner-edited; rows without `moze_id` are never deleted; a row a mapped period matched or created in this import is never swept by that final loop. An imported definition absent from the backup ends (pending rows deleted) when it has history, else it is deleted. Local definitions are never touched here.
- `skipped_future` counts only enabled future records; disabled future records are counted under `disabled_skipped` (so `Σ skipped_future = records_mapped + rewards_ignored + Σ unsupported_types`).
- Owner-set template amounts (proposal decision 24, D37, spec "Owner-set price survives a re-import"): for a definition with `template_owner_edited = true` (Task 12's 這一期與之後 / 全部週期), `_upsert_definition` keeps the stored line amounts (a refreshed line at the same index with the same kind keeps its amount, `_keep_owner_amounts`), and `_apply_instances` writes no MOZE `amount_override` on its pending rows — new (override `None`), refreshed or adopted — while dates, statuses, adoption and every other rule above apply unchanged. Each such pending row whose MOZE amounts differ from the amounts it will post (its override, else the template's) is appended to the report list `amount_differs` as `{"definition_id", "seq", "date", "moze_amounts", "amounts"}` (owner-facing, like `loan_remainder_check`; nothing of it is logged).

- [ ] 17.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_import_apply.py`:

```python
"""Backup import of MOZE scheduled data (spec "Schedule definitions and instances from the backup")."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_generation as generation
from app.services import schedule_rules as rules
from tests.helpers import _by_moze_id, _import_backup

WEEKLY = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")


def _accounts(backup):
    return [backup.account("A-WALLET", "錢包"), backup.account("A-BANK", "銀行")]


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def _weekly(backup, days=WEEKLY, *, identifier="PER-W", weekday=2, exported_at=None, extra=()):
    kwargs = {"exported_at": exported_at} if exported_at else {}
    return backup.data(
        accounts=_accounts(backup), periods=[backup.period(identifier, unit=1, days=weekday, start=f"{days[0]}T00:00:00")],
        records=[_rec(backup, f"R-{day}", day, eventID=identifier) for day in days] + list(extra), **kwargs,
    )


def _definition(db, moze_id) -> ScheduleDefinition:
    db.expire_all()
    return db.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == moze_id))


def _instances(db, definition) -> list[ScheduleInstance]:
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


def _state(db) -> list[tuple]:
    db.expire_all()
    return sorted(
        (row.id, row.definition_id, row.seq, row.status, row.due_date, row.rule_date, row.moze_id, row.amount_override)
        for row in db.scalars(select(ScheduleInstance))
    )


def test_imported_definitions_start_active_and_automatic(db_session, backup, today):
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=300)],
        records=[_rec(backup, "R-1", "2026-09-21", eventID="PER-W"), _rec(backup, "R-3", "2026-10-05", eventID="PER-W"),
                 _rec(backup, "R-2", "2026-11-09", eventID="INS-1")],
    )
    summary = _import_backup(db_session, data)
    for moze_id in ("PER-W", "INS-1"):
        definition = _definition(db_session, moze_id)
        assert (definition.created_locally, definition.status, definition.posting_mode, definition.auto_post_from) == (
            False, "active", "auto", date(2026, 10, 3),
        )
    assert summary["schedules"]["definitions"]["recurring"]["created"] == 1
    assert summary["schedules"]["definitions"]["installment"]["created"] == 1
    assert _definition(db_session, "PER-W").moze_payload["unit"] == 1


def test_weekly_period_past_records_post_and_future_ones_wait(db_session, backup, today):
    # Spec "Weekly period keyed by weekday".
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    rows = _instances(db_session, definition)
    assert [(row.seq, row.status, row.acted_by) for row in rows] == [
        (1, "posted", "import"), (2, "posted", "import"), (3, "pending", None), (4, "pending", None),
    ]
    assert rows[0].posted_entry_ids == [_by_moze_id(db_session, "R-2026-09-21").id]
    assert rows[0].acted_at is not None
    assert (definition.interval_unit, definition.anchor_date, definition.times) == ("week", date(2026, 9, 21), None)
    counts = summary["schedules"]["instances"]
    assert (counts["created"], counts["posted_from_past"]) == (4, 2)


def test_installment_lines_resolve_the_loan(db_session, backup, today):
    # Spec "Repayment and interest are one period"; the loan line points at the imported payable.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000, remainder=16667)],
        records=[
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-9", relatedID="R-LOAN",
                 target="T-BANK"),
            _rec(backup, "R-INT", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-9"),
        ],
    )
    summary = _import_backup(db_session, data)
    definition = _definition(db_session, "INS-1")
    repayment, interest = definition.template["lines"]
    assert (repayment["kind"], repayment["loan_entry_id"], repayment["amount"]) == (
        "repayment", _by_moze_id(db_session, "R-LOAN").id, "8333",
    )
    assert interest["kind"] == "interest" and definition.review_reason is None
    [instance] = _instances(db_session, definition)
    assert (instance.seq, instance.amount_override, instance.moze_record_ids, instance.moze_id) == (
        2, ["8333", "612"], ["R-REP", "R-INT"], "R-REP",
    )
    assert summary["schedules"]["loan_remainder_check"] == [
        {"definition": definition.name, "moze_remainder": "16667", "open_amount": "25000", "difference": "8333"}
    ]


def test_disabled_future_period_is_skipped(db_session, backup, today):
    # Spec "Disabled future period".
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=_accounts(backup), periods=[backup.period("PER-D", unit=2, days=3, start="2026-11-03T00:00:00")],
        records=[_rec(backup, "R-NOV", "2026-11-03", eventID="PER-D"),
                 _rec(backup, "R-DEC", "2026-12-03", eventID="PER-D", isEnabled=False)],
    )
    summary = _import_backup(db_session, data)
    rows = _instances(db_session, _definition(db_session, "PER-D"))
    assert [(row.due_date, row.status, row.acted_by) for row in rows] == [
        (date(2026, 11, 3), "pending", None), (date(2026, 12, 3), "skipped", "import"),
    ]
    assert (summary["skipped_future"], summary["disabled_skipped"]) == ({"0": 1}, {"0": 1})
    assert summary["schedules"]["instances"]["skipped_disabled"] == 1


def test_single_record_becomes_its_own_definition(db_session, backup, today):
    # Spec "Single record keyed apart from definitions".
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, backup.data(accounts=_accounts(backup), records=[_rec(backup, "X1", "2026-11-20")]))
    definition = _definition(db_session, "record:X1")
    assert (definition.kind, definition.times) == ("recurring", 1)
    assert [(row.status, row.due_date) for row in _instances(db_session, definition)] == [("pending", date(2026, 11, 20))]
    assert summary["schedules"]["definitions"]["single"]["created"] == 1


def test_reimport_is_idempotent_for_schedules(db_session, backup, today):
    # Spec "Re-import is idempotent" (schedule half).
    today(date(2026, 10, 3))
    data = _weekly(backup)
    _import_backup(db_session, data)
    before = _state(db_session)
    definition_ids = sorted(db_session.scalars(select(ScheduleDefinition.id)))

    summary = _import_backup(db_session, data)

    assert _state(db_session) == before
    assert sorted(db_session.scalars(select(ScheduleDefinition.id))) == definition_ids
    block = summary["schedules"]
    assert all(bucket["created"] == 0 and bucket["deleted"] == 0 for bucket in block["definitions"].values())
    assert (block["instances"]["created"], block["instances"]["deleted"], block["instances"]["adopted"]) == (0, 0, 0)


def test_homehub_generated_period_adopted(db_session, backup, today):
    # Spec "HomeHub-generated period adopted".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    db_session.commit()
    generated = next(row for row in _instances(db_session, definition) if row.due_date == date(2027, 10, 4))
    assert generated.moze_id is None

    summary = _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-NEW", "2027-10-04", eventID="PER-W")]))

    rows = [row for row in _instances(db_session, _definition(db_session, "PER-W")) if row.due_date == date(2027, 10, 4)]
    assert [(row.id, row.moze_id) for row in rows] == [(generated.id, "R-NEW")]
    assert summary["schedules"]["instances"]["adopted"] == 1


def test_moved_period_adopted_by_its_rule_date(db_session, backup, today):
    # Spec "Moved period adopted by its rule date".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    moved = next(row for row in _instances(db_session, definition) if row.rule_date == date(2027, 10, 4))
    moved.due_date = date(2027, 10, 6)
    db_session.commit()

    _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-NEW", "2027-10-04", eventID="PER-W")]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, moved.id)
    assert (row.moze_id, row.due_date, row.rule_date) == ("R-NEW", date(2027, 10, 6), date(2027, 10, 4))


def test_seq_conflict_reported_not_fatal(db_session, seed, backup, today):
    # Spec "Seq conflict reported, not fatal".
    today(date(2026, 10, 3))
    wallet = seed.account("錢包", moze_id="A-WALLET")
    definition = seed.definition(
        [seed.line("expense", wallet, "100")], interval_unit="week", anchor=date(2026, 12, 14), created_locally=False,
        moze_id="PER-S",
    )
    existing = seed.instance(definition, 5, date(2027, 1, 4))
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=("2026-12-14", "2027-01-11"), identifier="PER-S"))

    db_session.expire_all()
    assert db_session.get(ScheduleInstance, existing.id).seq == 5
    assert {"moze_id": "R-2027-01-11", "reason": "seq_conflict"} in summary["schedules"]["review"]
    assert [row.seq for row in _instances(db_session, definition)] == [1, 5]


def test_new_review_reason_pauses_and_a_resolved_one_clears(db_session, seed, backup, today):
    # Spec "New review reason pauses, resolved one clears".
    today(date(2026, 10, 3))
    wallet = seed.account("錢包", moze_id="A-WALLET")
    left_active = seed.definition([seed.line("expense", wallet, "100")], name="A", interval_unit="week",
                                  anchor=date(2026, 9, 21), created_locally=False, moze_id="PER-A")
    resumed = seed.definition([seed.line("expense", wallet, "100")], name="B", interval_unit="week",
                              anchor=date(2026, 9, 21), created_locally=False, moze_id="PER-B",
                              review_reason="interval_mismatch")
    db_session.commit()
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-A", unit=1, days=3, start="2026-09-21T00:00:00"),
                 backup.period("PER-B", unit=1, days=2, start="2026-09-21T00:00:00")],
        records=[_rec(backup, "A-1", "2026-09-21", eventID="PER-A"), _rec(backup, "B-1", "2026-09-21", eventID="PER-B"),
                 # one enabled future record each: both periods are live (a period without one imports ended)
                 _rec(backup, "A-2", "2026-10-05", eventID="PER-A"), _rec(backup, "B-2", "2026-10-05", eventID="PER-B")],
    )

    _import_backup(db_session, data)

    first, second = _definition(db_session, "PER-A"), _definition(db_session, "PER-B")
    assert (first.status, first.review_reason) == ("paused", "interval_mismatch")
    assert (second.status, second.review_reason) == ("active", None)
    assert (left_active.id, resumed.id) == (first.id, second.id)


def test_owner_edited_pending_period_kept(db_session, backup, today):
    # Spec "Owner-edited pending period kept".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    pending = _instances(db_session, _definition(db_session, "PER-W"))[2]
    pending.amount_override, pending.edited_by_owner = ["9000"], True
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, pending.id)
    assert (row.amount_override, row.edited_by_owner, row.status) == (["9000"], True, "pending")
    assert summary["schedules"]["instances"]["kept_owner_edited"] == 1


def test_reimport_keeps_owner_decisions_and_local_definitions(db_session, seed, backup, today):
    # Spec "Re-import keeps owner decisions and local definitions".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    skipped = _instances(db_session, definition)[2]
    skipped.status, skipped.acted_at, skipped.acted_by = "skipped", datetime(2026, 10, 3, tzinfo=timezone.utc), "owner"
    definition.posting_mode = "confirm"
    card = seed.account("範例卡")
    local = seed.definition([seed.line("expense", card, "390")], name="本地")
    for seq in (1, 2, 3):
        entry = seed.entry(card, "-390", day=date(2026, 6 + seq, 22), source="schedule")
        seed.instance(local, seq, date(2026, 6 + seq, 22), status="posted", entries=[entry])
    db_session.commit()
    local_before = [(row.id, row.status, row.posted_entry_ids) for row in _instances(db_session, local)]

    _import_backup(db_session, _weekly(backup))

    db_session.expire_all()
    assert db_session.get(ScheduleInstance, skipped.id).status == "skipped"
    assert _definition(db_session, "PER-W").posting_mode == "confirm"
    assert [(row.id, row.status, row.posted_entry_ids) for row in _instances(db_session, local)] == local_before


def test_imported_definition_absent_from_the_backup_ends_or_is_deleted(db_session, backup, today):
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-H", unit=1, days=2, start="2026-09-21T00:00:00"),
                 backup.period("PER-F", unit=1, days=2, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "H-1", "2026-09-21", eventID="PER-H"), _rec(backup, "H-2", "2026-10-05", eventID="PER-H"),
                 _rec(backup, "F-1", "2026-10-05", eventID="PER-F")],
    )
    _import_backup(db_session, data)

    summary = _import_backup(db_session, backup.data(accounts=_accounts(backup)))

    with_history = _definition(db_session, "PER-H")
    assert with_history.status == "ended"
    # H-1 was posted by the import; its record left the backup with the period, so it is recomputed: skipped.
    assert [(row.status, row.acted_by, row.posted_entry_ids) for row in _instances(db_session, with_history)] == [
        ("skipped", "import", []),
    ]
    assert _definition(db_session, "PER-F") is None
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    assert summary["schedules"]["definitions"]["recurring"]["deleted"] == 1


def test_import_row_whose_record_left_the_backup_becomes_skipped(db_session, backup, today):
    # Spec: acted_by = import instances are recomputed from the newly imported entries; none left → skipped / import.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    first = _instances(db_session, _definition(db_session, "PER-W"))[0]
    assert (first.status, first.acted_by) == ("posted", "import")

    _import_backup(db_session, _weekly(backup, days=WEEKLY[1:]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, first.id)
    assert (row.status, row.acted_by, row.posted_entry_ids) == ("skipped", "import", [])
    assert _by_moze_id(db_session, "R-2026-09-21") is None


def test_template_change_realigns_kept_overrides(db_session, backup, today):
    # Global constraint: overrides stay aligned with the template's lines — an owner-edited row included.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]

    def data(with_interest):
        records = [
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1",
                 relatedID="R-LOAN", target="T-BANK"),
            _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
                 relatedID="R-LOAN", target="T-BANK"),
        ]
        if with_interest:
            records.append(_rec(backup, "R-INT1", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-1"))
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=records,
        )

    _import_backup(db_session, data(False))
    edited = _instances(db_session, _definition(db_session, "INS-1"))[1]
    edited.amount_override, edited.edited_by_owner = ["9000"], True
    db_session.commit()

    _import_backup(db_session, data(True))

    db_session.expire_all()
    assert [line["kind"] for line in _definition(db_session, "INS-1").template["lines"]] == ["repayment", "interest"]
    row = db_session.get(ScheduleInstance, edited.id)
    assert (row.amount_override, row.edited_by_owner, row.status) == (["9000", "612"], True, "pending")


def test_pending_imported_row_follows_moze_and_clears_failure_marks(db_session, backup, today):
    # Spec: pending imported instances are refreshed unless owner-edited — MOZE's new date included; a period MOZE
    # now marks disabled is skipped and no longer shows the failure it had while pending.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    rows = _instances(db_session, _definition(db_session, "PER-W"))
    waiting, failing = rows[2], rows[3]
    failing.last_error, failing.last_error_at = "lines[0].account_id: 帳戶已封存", datetime(2026, 10, 3, tzinfo=timezone.utc)
    db_session.commit()
    extra = [
        _rec(backup, "R-2026-10-05", "2026-10-06", eventID="PER-W"),  # MOZE moved this period by a day
        _rec(backup, "R-2026-10-12", "2026-10-12", eventID="PER-W", isEnabled=False),
    ]

    _import_backup(db_session, _weekly(backup, days=WEEKLY[:2], extra=extra))

    db_session.expire_all()
    moved = db_session.get(ScheduleInstance, waiting.id)
    assert (moved.rule_date, moved.due_date, moved.status) == (date(2026, 10, 6), date(2026, 10, 6), "pending")
    dropped = db_session.get(ScheduleInstance, failing.id)
    assert (dropped.status, dropped.acted_by, dropped.last_error, dropped.last_error_at) == ("skipped", "import", None, None)


def test_interval_mismatch_definition_never_generates(db_session, backup, today):
    # An off-rule import is paused with seqs by position; generation must not roll that invented rule forward.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup, weekday=3))  # MOZE says Tuesday, the records fall on Mondays
    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("paused", "interval_mismatch")
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0
    assert len(_instances(db_session, definition)) == 4


def test_period_without_future_records_imports_ended(db_session, backup, today):
    # Spec maps each live AHPeriod: one with no enabled future record (cancelled or finished in MOZE) ends.
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))
    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("ended", "not_live")
    assert [row.status for row in _instances(db_session, definition)] == ["posted", "posted"]
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    assert {"moze_id": "PER-W", "reason": "not_live"} not in summary["schedules"]["review"]


def test_not_live_end_keeps_owner_and_homehub_rows_and_reverses_when_moze_resumes(db_session, backup, today):
    # D37: an import never deletes owner-edited or HomeHub-generated pending rows. A backup without an enabled future
    # record (e.g. mid-mirror, before MOZE pre-generated the next period) ends the period with review_reason
    # not_live; the next backup that has one sets it back to active and creates the pending period.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    edited = _instances(db_session, definition)[2]  # R-2026-10-05, pending
    edited.amount_override, edited.edited_by_owner = ["9000"], True
    generated = next(row for row in _instances(db_session, definition) if row.due_date == date(2027, 10, 4))
    assert generated.moze_id is None
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))

    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("ended", "not_live")
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    rows = {row.id: row for row in _instances(db_session, definition)}
    assert (rows[edited.id].status, rows[edited.id].amount_override) == ("pending", ["9000"])  # owner-edited: kept
    assert (rows[generated.id].status, rows[generated.id].moze_id) == ("pending", None)  # HomeHub-generated: kept
    assert all(row.moze_id != "R-2026-10-12" for row in rows.values())  # MOZE-sourced, record gone: deleted
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0

    again = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))  # still not live: nothing changes
    assert (_definition(db_session, "PER-W").status, again["schedules"]["definitions"]["recurring"]["ended"]) == ("ended", 0)

    summary = _import_backup(db_session, _weekly(backup))

    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("active", None)
    assert summary["schedules"]["definitions"]["recurring"]["updated"] == 1
    rows = _instances(db_session, definition)
    revived = next(row for row in rows if row.moze_id == "R-2026-10-12")
    assert (revived.status, revived.due_date) == ("pending", date(2026, 10, 12))
    assert db_session.get(ScheduleInstance, edited.id).amount_override == ["9000"]
    assert db_session.get(ScheduleInstance, generated.id).status == "pending"


def test_account_missing_definition_realigns_moze_amounts_to_the_kept_template(db_session, backup, today):
    # Global constraint (overrides aligned with the template on every write): a re-import whose lines cannot resolve
    # keeps the stored template, so MOZE's per-period amounts are realigned to its lines, not written as mapped.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    loan = _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK")

    def data(records):
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=[loan, *records],
        )

    _import_backup(db_session, data([
        _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN",
             target="T-BANK"),
        _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-612, eventID="INS-1", packageID="PK-2"),
    ]))
    definition = _definition(db_session, "INS-1")
    assert [line["kind"] for line in definition.template["lines"]] == ["repayment", "interest"]

    # MOZE moved the repayment to an account the backup no longer lists and dropped the interest record.
    summary = _import_backup(db_session, data([
        _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8000, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN",
             target="T-BANK", account="A-GONE"),
    ]))

    definition = _definition(db_session, "INS-1")
    assert [line["kind"] for line in definition.template["lines"]] == ["repayment", "interest"]  # template kept
    assert {"moze_id": "INS-1", "reason": "account_missing"} in summary["schedules"]["review"]
    [row] = _instances(db_session, definition)
    assert (row.status, row.amount_override) == ("pending", ["8000", "612"])  # one amount per template line


def test_moze_date_refresh_never_lands_on_another_pending_period(db_session, backup, today):
    # The refresh of a pending row to MOZE's new date must not collide with another pending (or posted) period of
    # the definition: both would post on one day and the second would hit ux_schedule_instance_posted_day.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    rows = _instances(db_session, _definition(db_session, "PER-W"))
    waiting, moved_by_owner = rows[2], rows[3]
    moved_by_owner.due_date, moved_by_owner.edited_by_owner = date(2026, 10, 7), True
    db_session.commit()
    extra = [
        _rec(backup, "R-2026-10-05", "2026-10-07", eventID="PER-W"),  # MOZE moved this period onto the owner's day
        _rec(backup, "R-2026-10-12", "2026-10-12", eventID="PER-W"),
    ]

    _import_backup(db_session, _weekly(backup, days=WEEKLY[:2], extra=extra))

    db_session.expire_all()
    kept = db_session.get(ScheduleInstance, waiting.id)
    assert (kept.rule_date, kept.due_date, kept.status) == (date(2026, 10, 5), date(2026, 10, 5), "pending")
    owner = db_session.get(ScheduleInstance, moved_by_owner.id)
    assert (owner.due_date, owner.status) == (date(2026, 10, 7), "pending")


def test_row_matched_by_a_record_id_takes_the_new_primary_id(db_session, backup, today):
    # MOZE changed a package's primary record (the repayment joined an interest-only package): the row matched by the
    # shared record id takes the new moze_id and is refreshed — the final sweep must not delete it and lose the period.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]

    def data(with_repayment):
        records = [
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1",
                 relatedID="R-LOAN", target="T-BANK"),
            _rec(backup, "R-INT1", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-1"),
            _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-600, eventID="INS-1", packageID="PK-2"),
        ]
        if with_repayment:
            records.append(_rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
                                relatedID="R-LOAN", target="T-BANK"))
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=records,
        )

    _import_backup(db_session, data(False))
    second = next(row for row in _instances(db_session, _definition(db_session, "INS-1")) if row.due_date == date(2026, 12, 9))
    assert (second.moze_id, second.amount_override) == ("R-INT2", ["0", "600"])

    summary = _import_backup(db_session, data(True))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, second.id)
    assert (row.moze_id, row.moze_record_ids, row.amount_override, row.status) == (
        "R-REP2", ["R-REP2", "R-INT2"], ["8333", "600"], "pending",
    )
    assert summary["schedules"]["instances"]["deleted"] == 0


def test_owner_set_template_amounts_survive_a_reimport(db_session, backup, today):
    # Spec "Owner-set price survives a re-import" (proposal decision 24): after 全部週期 the owner's price stands on
    # refreshed and new pending periods; MOZE's differing amounts are only reported.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    line = definition.template["lines"][0]
    definition.template = {**definition.template, "lines": [{**line, "amount": "120"}]}
    definition.template_owner_edited = True
    for row in _instances(db_session, definition):
        if row.status == "pending":
            row.amount_override = None  # what Task 12's scope = all leaves behind
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-2026-10-19", "2026-10-19", eventID="PER-W")]))

    definition = _definition(db_session, "PER-W")
    assert (definition.template["lines"][0]["amount"], definition.template_owner_edited) == ("120", True)
    pending = [(row.seq, row.amount_override) for row in _instances(db_session, definition) if row.status == "pending"]
    assert pending == [(3, None), (4, None), (5, None)]
    assert summary["schedules"]["amount_differs"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": seq, "date": day, "line": 0, "kind": "expense",
         "amount": "120", "moze_amount": "100"}
        for seq, day in ((3, "2026-10-05"), (4, "2026-10-12"), (5, "2026-10-19"))
    ]


def test_owner_edited_pending_period_on_an_owner_priced_definition_is_compared(db_session, backup, today):
    # Multica R-A2: template_owner_edited, template 150, MOZE 100, a retained owner override 120 → one amount_differs
    # item, 120 against MOZE's 100 (the period's effective posting amount is the owner's override).
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    line = definition.template["lines"][0]
    definition.template = {**definition.template, "lines": [{**line, "amount": "150"}]}
    definition.template_owner_edited = True
    rows = [row for row in _instances(db_session, definition) if row.status == "pending"]
    for row in rows:
        row.amount_override = None
    rows[0].amount_override, rows[0].edited_by_owner = ["120"], True
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:3]))  # only the owner-edited period stays pending

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, rows[0].id)
    assert (row.status, row.amount_override, row.edited_by_owner) == ("pending", ["120"], True)
    assert summary["schedules"]["amount_differs"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": row.seq, "date": row.due_date.isoformat(),
         "line": 0, "kind": "expense", "amount": "120", "moze_amount": "100"},
    ]
```

- [ ] 17.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_import_apply.py
```

Expected: 24 failures (`KeyError: 'schedules'` / `AttributeError: 'NoneType' object has no attribute …`).

- [ ] 17.3 Create `services/accounting-service/app/services/schedule_import.py`:

```python
"""The backup importer's schedule step (design D32, D37).

Runs inside the importer's ledger transaction while the import advisory key is held exclusively. Owner and job
decisions (periods posted / skipped by auto or owner, owner-edited pending periods, posting_mode, auto_post_from) are
never overwritten; locally created definitions are never written except their loan links (restore_links).
"""

import copy
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, aliased

from ..models import Category, LedgerEntry, ScheduleDefinition, ScheduleInstance
from . import settlement_service
from .schedule_import_map import TRANSFER_TYPE, MapResult, MappedDefinition, MappedInstance
from .schedule_rules import plain
from .schedule_templates import (
    LEDGER_KINDS, LINE_KEYS, LOAN_LINE_KINDS, loan_line, realign_override, referenced_ids, template_amounts,
)

BUCKETS = {"period": "recurring", "installment": "installment", "single": "single"}
NOT_LIVE = "not_live"  # review_reason of a MOZE period the import ended for having no enabled future record
INSTANCE_COUNTERS = (
    "created", "updated", "kept", "kept_owner_edited", "adopted", "deleted", "posted_from_past", "skipped_disabled",
)


@dataclass
class CapturedLinks:
    template_links: list[tuple[int, int, str]] = field(default_factory=list)  # (definition id, line index, moze_id)
    settlement_links: list[tuple[int, str]] = field(default_factory=list)  # (schedule entry id, moze_id of its target)


@dataclass(frozen=True)
class Covered:
    """A past MOZE record HomeHub already decided through the instance `instance_id`: booked it (`posted`), dropped
    it (`skipped`), or holds it as an owner-edited pending period (`owner_pending`, Multica R-F1: the owner's pending
    choice wins, the record is suppressed). None of them is imported as an entry (D37).

    `moze_lines` is MOZE's amount per template line of the period — (|amount|, |in-leg amount| for a transfer line,
    else None) — for the per-line comparison (R-F5); `day` is the record date; `account_amounts` the record's signed
    `total` per MOZE account id (the balance comparison of a suppressed `owner_pending` record)."""

    instance_id: int
    status: str  # posted | skipped | owner_pending
    moze_lines: tuple[tuple[str, str | None], ...]
    day: date
    account_amounts: tuple[tuple[str, Decimal], ...] = ()


def _jsonable(value):
    def default(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError(type(item).__name__)

    return json.loads(json.dumps(value, default=default, ensure_ascii=False))


def new_report(mapped: MapResult) -> dict:
    zero = {"created": 0, "updated": 0, "ended": 0, "deleted": 0}
    return {
        "definitions": {bucket: dict(zero) for bucket in ("recurring", "installment", "single")},
        "instances": dict.fromkeys(INSTANCE_COUNTERS, 0),
        "records_mapped": mapped.records_mapped,
        "rewards_ignored": mapped.rewards_ignored,
        "unsupported_types": dict(sorted(mapped.unsupported_types.items())),
        "past_records_already_posted": 0,
        "past_records_already_skipped": 0,
        # per line (R-F5): count / instance_ids of posted periods with a differing line, `lines` the items (owner-facing)
        "past_records_amount_differs": {"count": 0, "instance_ids": [], "lines": []},
        "past_records_owner_pending": [],  # R-F1: {definition_id, seq, date} of suppressed records held by the owner
        "relinked": {"templates": 0, "settlements": 0},
        "review": list(mapped.review),
        # per line (R-F5, R-A2): pending periods whose amounts the import keeps (template_owner_edited definitions,
        # owner-edited periods) and MOZE's differ; owner-facing, never logged
        "amount_differs": [],
        "loan_remainder_check": [],
    }


def lock_schedule_rows(session: Session) -> None:
    """D32: before any entry is deleted, every definition, then every instance, FOR UPDATE by ascending id."""
    session.execute(select(ScheduleDefinition.id).order_by(ScheduleDefinition.id).with_for_update()).all()
    session.execute(select(ScheduleInstance.id).order_by(ScheduleInstance.id).with_for_update()).all()


def merge_known_past_singles(session: Session, mapped: MapResult) -> None:
    """A past record without a definition is an ordinary entry — unless HomeHub holds its `record:<id>` definition
    (a single future record mapped by an earlier import, posted or skipped by HomeHub since). Those join
    mapped.definitions, so covered_records keeps HomeHub's booking (the record is not imported twice) and
    apply_schedules neither ends the definition nor loses the period (spec "Record mapping")."""
    if mapped.past_singles:
        known = set(
            session.scalars(select(ScheduleDefinition.moze_id).where(ScheduleDefinition.moze_id.like("record:%")))
        )
        mapped.definitions.extend(item for item in mapped.past_singles if item.moze_id in known)
    mapped.past_singles = []


def capture_links(session: Session) -> CapturedLinks:
    """Before the full replace deletes MOZE entries: loan links of local templates and the targets of schedule
    settlements, by the target's moze_id (imported templates are rebuilt by apply_schedules)."""
    captured = CapturedLinks()
    for definition in session.scalars(
        select(ScheduleDefinition).where(ScheduleDefinition.created_locally.is_(True)).order_by(ScheduleDefinition.id)
    ):
        for index, line in enumerate(definition.template["lines"]):
            loan_id = line.get("loan_entry_id")
            entry = session.get(LedgerEntry, loan_id) if loan_id is not None else None
            if entry is not None and entry.moze_id is not None:
                captured.template_links.append((definition.id, index, entry.moze_id))
    target = aliased(LedgerEntry)
    captured.settlement_links = [
        (entry_id, moze_id)
        for entry_id, moze_id in session.execute(
            select(LedgerEntry.id, target.moze_id)
            .join(target, target.id == LedgerEntry.settles_entry_id)
            .where(LedgerEntry.source == "schedule", target.moze_id.is_not(None))
            .order_by(LedgerEntry.id)
        )
    ]
    return captured


def _adoptable(rows: list[ScheduleInstance], day: date) -> ScheduleInstance | None:
    free = [row for row in rows if row.moze_id is None]
    return next((row for row in free if row.rule_date == day), None) or next((row for row in free if row.due_date == day), None)


def _find(rows: list[ScheduleInstance], mapped: MappedInstance) -> tuple[ScheduleInstance | None, bool]:
    """(row, adopted): by moze_id over every row first, then by a merged record id (MOZE changed the package's
    primary record: the caller moves the row to the new moze_id), then an adoptable HomeHub row."""
    row = next((row for row in rows if row.moze_id == mapped.moze_id), None) or next(
        (row for row in rows if set(mapped.record_ids) & set(row.moze_record_ids or [])), None
    )
    if row is not None:
        return row, False
    row = _adoptable(rows, mapped.day)
    return row, row is not None


def _moze_lines(template: dict, amounts: list, records: list[dict]) -> tuple[tuple[str, str | None], ...]:
    """MOZE's amount per template line (R-F5): `amounts` is the mapped |total| per line (record → line identity kept by
    the mapping: a packaged repayment and its interest land on their own lines, "0" for a missing member); a transfer
    line also carries its in-leg record's |total|. `records` is empty when the amounts were realigned to a kept
    template (account_missing): the in-leg is then not compared."""
    in_leg = next((record for record in records if record["type"] == TRANSFER_TYPE and record["isTransferIn"]), None)
    lines = []
    for line, amount in zip(template["lines"], amounts):
        in_amount = None
        if line["kind"] == "transfer" and in_leg is not None:
            in_amount = plain(abs(Decimal(str(in_leg["total"]))))
        lines.append((plain(amount), in_amount))
    return tuple(lines)


def _entry_line_kind(entry: LedgerEntry) -> str:
    if entry.kind in ("transfer_out", "transfer_in"):
        return "transfer"
    if entry.is_settlement:
        return "repayment" if entry.kind == "payable" else "collection"
    return entry.kind  # expense, income, receivable, payable, interest


def _posted_lines(session: Session, template: dict, entry_ids: list[int]) -> list[tuple[str, str | None]]:
    """What HomeHub posted per template line, from the period's entries (posted_entry_ids keeps the line order and a
    "0" line wrote nothing): (|amount|, |in-leg amount| for a transfer line, else None); "0" for a missing entry."""
    found = {entry.id: entry for entry in session.scalars(select(LedgerEntry).where(LedgerEntry.id.in_(entry_ids)))}
    pool = [found[entry_id] for entry_id in entry_ids if entry_id in found]
    used: set[int] = set()
    lines: list[tuple[str, str | None]] = []
    for line in template["lines"]:
        out = next(
            (entry for entry in pool
             if entry.id not in used and entry.kind != "transfer_in" and _entry_line_kind(entry) == line["kind"]),
            None,
        )
        if out is not None:
            used.add(out.id)
        in_amount = None
        if line["kind"] == "transfer":
            leg = next((entry for entry in pool if entry.id not in used and entry.kind == "transfer_in"), None)
            if leg is not None:
                used.add(leg.id)
            in_amount = plain(abs(Decimal(leg.amount))) if leg is not None else "0"
        lines.append((plain(abs(Decimal(out.amount))) if out is not None else "0", in_amount))
    return lines


def _pending_lines(definition: ScheduleDefinition, row: ScheduleInstance) -> list[tuple[str, str | None]]:
    """What a pending period will post per line: its override, else the template; a transfer's in-leg is the line's
    to_amount (cross-currency), else the same amount."""
    amounts = list(row.amount_override) if row.amount_override is not None else template_amounts(definition.template)
    lines: list[tuple[str, str | None]] = []
    for line, amount in zip(definition.template["lines"], amounts):
        in_amount = None
        if line["kind"] == "transfer":
            in_amount = plain(line["to_amount"]) if line.get("to_amount") is not None else plain(amount)
        lines.append((plain(amount), in_amount))
    return lines


def _line_differences(definition: ScheduleDefinition, row: ScheduleInstance, ours, theirs) -> list[dict]:
    """One owner-facing item per differing line (R-F5): out-leg / entry amount under the line's kind, a transfer's
    in-leg under `transfer_in`. Never logged."""
    items = []

    def item(index: int, kind: str, amount: str, moze_amount: str) -> dict:
        return {
            "definition_id": definition.id, "name": definition.name, "seq": row.seq, "date": row.due_date.isoformat(),
            "line": index, "kind": kind, "amount": amount, "moze_amount": moze_amount,
        }

    for index, (line, (amount, in_amount), (moze_amount, moze_in)) in enumerate(
        zip(definition.template["lines"], ours, theirs)
    ):
        if Decimal(amount) != Decimal(moze_amount):
            items.append(item(index, line["kind"], amount, moze_amount))
        if line["kind"] == "transfer" and in_amount is not None and moze_in is not None:
            if Decimal(in_amount) != Decimal(moze_in):
                items.append(item(index, "transfer_in", in_amount, moze_in))
    return items


def covered_records(session: Session, mapped: MapResult) -> dict[str, Covered]:
    """Past MOZE records HomeHub already covered: their instance (matched or adoptable) is posted by auto / owner,
    skipped by anyone but import, or — Multica R-F1 — pending and owner-edited (the owner's pending choice wins: the
    record is suppressed, the instance stays pending with its override and a later post writes the period once).
    These records are not imported as entries (D37)."""
    covered: dict[str, Covered] = {}
    definitions = {
        row.moze_id: row for row in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.moze_id.is_not(None)))
    }
    for mapped_definition in mapped.definitions:
        definition = definitions.get(mapped_definition.moze_id)
        if definition is None:
            continue
        rows = list(session.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id)))
        for item in mapped_definition.instances:
            if not item.past:
                continue
            row, _ = _find(rows, item)
            if row is None:
                continue
            kept_posted = row.status == "posted" and row.acted_by in ("auto", "owner")
            kept_skipped = row.status == "skipped" and row.acted_by != "import"
            owner_pending = row.status == "pending" and row.edited_by_owner and item.enabled
            if kept_posted or kept_skipped or owner_pending:
                status = "posted" if kept_posted else "skipped" if kept_skipped else "owner_pending"
                moze_lines = _moze_lines(definition.template, list(item.amounts), item.records)
                account_amounts = tuple(
                    (record["account"], Decimal(str(record["total"]))) for record in item.records
                )
                for record_id in item.record_ids:
                    covered[record_id] = Covered(row.id, status, moze_lines, item.day, account_amounts)
    return covered


def suppressed_amounts(covered: dict[str, Covered]) -> list[tuple[str, date, Decimal]]:
    """(MOZE account id, date, signed total) of every record suppressed for an owner-edited pending period (R-F1):
    MOZE booked them, HomeHub has not yet, so the balance comparison adds them to `moze_part` (Task 18)."""
    seen: set[int] = set()
    amounts = []
    for item in covered.values():
        if item.status != "owner_pending" or item.instance_id in seen:
            continue
        seen.add(item.instance_id)
        amounts.extend((account, item.day, total) for account, total in item.account_amounts)
    return amounts


def template_usage(session: Session) -> dict[str, set[int]]:
    """Rows any definition's template references: they count as used (never deleted or archived by the import)."""
    usage: dict[str, set[int]] = {"account": set(), "category": set(), "counterparty": set(), "project": set()}
    for template in session.scalars(select(ScheduleDefinition.template)):
        for key, ids in referenced_ids(template).items():
            if key in usage:
                usage[key] |= ids
    return usage


def stand_in_entry_ids(session: Session, covered: dict[str, Covered]) -> set[int]:
    """The schedule entries standing in for records skipped as already posted (balance comparison, D37)."""
    instance_ids = {item.instance_id for item in covered.values() if item.status == "posted"}
    if not instance_ids:
        return set()
    rows = session.scalars(select(ScheduleInstance.posted_entry_ids).where(ScheduleInstance.id.in_(instance_ids)))
    return {entry_id for entry_ids in rows for entry_id in entry_ids}


def _resolve_lines(
    mapped: MappedDefinition, settings, entries, category_kinds: dict[int, str]
) -> tuple[list[dict] | None, str | None]:
    lines, reason = [], None
    for source in mapped.lines:
        account = settings.accounts.get(source.account)
        if account is None:
            return None, "account_missing"
        line = dict.fromkeys(LINE_KEYS)
        category_id = settings.categories.get(source.classification)
        line.update(
            kind=source.kind, account_id=account.id, amount=source.amount, currency=account.currency, name=source.name,
            merchant=source.store, project_id=settings.projects.get(source.project),
            category_id=category_id if category_kinds.get(category_id) == LEDGER_KINDS[source.kind] else None,
        )
        if source.kind == "transfer":
            target = settings.accounts.get(source.to_account)
            if target is None:
                return None, "account_missing"
            line["to_account_id"] = target.id
            line["to_amount"] = source.to_amount if target.currency != account.currency else None
        if source.kind in ("receivable", "payable"):
            line["counterparty_id"] = settings.counterparties.get(source.target)
        if source.kind in LOAN_LINE_KINDS:
            loan = entries.entries.get(source.related) if source.related else None
            line["loan_entry_id"] = loan.id if loan is not None else None
            if loan is None:
                reason = "loan_missing"
        lines.append(line)
    return lines, reason


def _clear_pending_marks(row: ScheduleInstance) -> None:
    """A period MOZE booked or dropped no longer shows a failure, a reopen or a delete note from its pending days."""
    row.last_error, row.last_error_at, row.reopened_at, row.note = None, None, None, None


def _recompute_import_row(row: ScheduleInstance, entries, started_at: datetime) -> bool:
    """Spec: an `acted_by = import` instance is recomputed from the newly imported entries; with none left it becomes
    `skipped` (acted_by import). Used for rows whose record left the backup (no mapped item). True when changed."""
    entry_ids = [entries.entries[record_id].id for record_id in row.moze_record_ids or [] if record_id in entries.entries]
    if entry_ids:
        if row.status == "posted" and row.posted_entry_ids == entry_ids:
            return False
        row.status, row.posted_entry_ids, row.is_partial = "posted", entry_ids, False
    else:
        if row.status == "skipped" and not row.posted_entry_ids:
            return False
        row.status, row.posted_entry_ids, row.is_partial = "skipped", [], False
    row.acted_at = started_at
    _clear_pending_marks(row)
    return True


def _moze_status(row: ScheduleInstance, mapped: MappedInstance, entries, started_at: datetime) -> str:
    """Set the status MOZE gives the period: skipped (disabled, or past without an imported entry), posted (past,
    imported), pending (future)."""
    entry_ids = [entries.entries[record_id].id for record_id in mapped.record_ids if record_id in entries.entries]
    if not mapped.enabled or (mapped.past and not entry_ids):
        row.status, row.posted_entry_ids, row.is_partial = "skipped", [], False
        row.acted_at, row.acted_by = started_at, "import"
        _clear_pending_marks(row)
        return "skipped"
    if mapped.past:
        row.status, row.posted_entry_ids, row.is_partial = "posted", entry_ids, False
        row.acted_at, row.acted_by = started_at, "import"
        _clear_pending_marks(row)
        return "posted"
    row.status, row.posted_entry_ids, row.is_partial, row.acted_at, row.acted_by = "pending", [], False, None, None
    return "pending"


def _keep_owner_amounts(old: dict, new: dict) -> dict:
    """template_owner_edited (proposal decision 24): MOZE refreshes the lines, the owner's per-line amounts stay for a
    line at the same index with the same kind."""
    kept = copy.deepcopy(new)
    for index, line in enumerate(kept["lines"]):
        if index < len(old["lines"]) and old["lines"][index]["kind"] == line["kind"]:
            line["amount"] = old["lines"][index]["amount"]
    return kept


def _note_amount_differs(
    report: dict, definition: ScheduleDefinition, row: ScheduleInstance, moze_lines: tuple[tuple[str, str | None], ...]
) -> None:
    """The owner's amounts stand (a template_owner_edited definition, or an owner-edited pending period — R-A2); each
    line whose MOZE amount differs from what the period will post (its override, else the template) is listed for the
    owner, per line (R-F5; report only — logs carry ids and counts)."""
    report["amount_differs"].extend(_line_differences(definition, row, _pending_lines(definition, row), moze_lines))


def _live(mapped: MappedDefinition) -> bool:
    """Spec maps each *live* AHPeriod: one with at least one enabled future record. Installments and singles are
    always live here (their series is fixed by MOZE's dateInfo / the record itself)."""
    return mapped.source != "period" or any(item.enabled and not item.past for item in mapped.instances)


def _upsert_definition(session: Session, mapped: MappedDefinition, template: dict, review_reason: str | None,
                       existing: ScheduleDefinition | None, today: date, report: dict, *,
                       live: bool = True) -> ScheduleDefinition:
    """Create or refresh one imported definition. A period that is not live (no enabled future record: cancelled or
    finished in MOZE, or not yet pre-generated during the mirror month) is set `ended` with review_reason
    `not_live`, so HomeHub never generates or auto-posts after MOZE stopped; the reason makes the end reversible —
    a later backup with an enabled future record sets it back to `active` (`paused` with a mapping reason) and
    clears the reason. Only an end the import made is undone: an owner's `ended` (any other reason) stays."""
    bucket = report["definitions"][BUCKETS[mapped.source]]
    if existing is not None and existing.template_owner_edited:
        template = _keep_owner_amounts(existing.template, template)  # the owner's price stands (decision 24)
    values = {
        "kind": mapped.kind, "name": mapped.name, "template": template, "interval_unit": mapped.interval_unit,
        "interval_n": mapped.interval_n, "anchor_date": mapped.anchor_date, "day_of_month": mapped.day_of_month,
        "times": mapped.times, "total_amount": mapped.total_amount,
        "moze_payload": _jsonable(mapped.payload) if mapped.payload is not None else None,
    }
    if existing is None:
        status = "ended" if not live else "paused" if review_reason else "active"
        definition = ScheduleDefinition(
            moze_id=mapped.moze_id, **values, posting_mode="auto", status=status, auto_post_from=today,
            created_locally=False, review_reason=NOT_LIVE if not live else review_reason,
        )
        session.add(definition)
        session.flush()
        bucket["created"] += 1
        if not live:
            bucket["ended"] += 1
        return definition
    changed = False
    old_template = existing.template
    for key, value in values.items():
        current = getattr(existing, key)
        if key == "total_amount" and current is not None and value is not None:
            same = Decimal(current) == Decimal(value)
        else:
            same = current == value
        if not same:
            setattr(existing, key, value)
            changed = True
    if existing.template != old_template:
        # Global constraint: overrides stay aligned with the template's lines on every write — owner-edited and
        # posted rows included (their override is kept per line where the kind still matches, D35).
        for row in session.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == existing.id)):
            row.amount_override = realign_override(old_template, existing.template, row.amount_override)
    if not live:
        if existing.status != "ended":
            # The import ends it and remembers why; pending rows are left to _apply_instances, which drops the
            # MOZE-sourced ones whose record left the backup and keeps owner-edited and HomeHub-generated ones (D37).
            existing.status, existing.review_reason, changed = "ended", NOT_LIVE, True
            bucket["ended"] += 1
        elif existing.review_reason != NOT_LIVE and existing.review_reason != review_reason:
            existing.review_reason, changed = review_reason, True  # the owner's end stays; the reason follows MOZE
    elif existing.review_reason == NOT_LIVE:
        # MOZE has an enabled future record again: undo the import's own end (a new mapping reason pauses instead).
        existing.status = "paused" if review_reason else "active"
        existing.review_reason, changed = review_reason, True
    else:
        if review_reason is not None and review_reason != existing.review_reason and existing.status != "ended":
            existing.status = "paused"  # a reason new in this import pauses; the owner's own status stays otherwise
        if existing.review_reason != review_reason:
            existing.review_reason = review_reason
            changed = True
    if changed:
        bucket["updated"] += 1
    session.flush()
    return existing


def _apply_instances(session: Session, definition: ScheduleDefinition, mapped: MappedDefinition, entries,
                     started_at: datetime, report: dict, *, realign_from: dict | None = None) -> None:
    """Upsert the definition's instances from the mapped periods. `realign_from` is the mapped template when the
    definition keeps its stored template (account_missing): MOZE's per-period amounts are then realigned to the
    stored lines with realign_override, so no override goes out of line with the template (Global Constraints).
    On a template_owner_edited definition pending rows get no MOZE override; differences go to amount_differs."""
    counters = report["instances"]
    rows = list(
        session.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.id))
    )
    mapped_ids: set[str] = set()
    matched: set[int] = set()  # rows a mapped period matched or created: never swept by the final loop
    for item in mapped.instances:
        mapped_ids.add(item.moze_id)
        if item.seq is None:
            continue
        amounts = (
            list(item.amounts) if realign_from is None
            else realign_override(realign_from, definition.template, list(item.amounts))
        )
        moze_lines = _moze_lines(definition.template, amounts, item.records if realign_from is None else [])
        row, adopted = _find(rows, item)
        if row is None:
            if any(other.seq == item.seq for other in rows):
                report["review"].append({"moze_id": item.moze_id, "reason": "seq_conflict"})
                continue
            if definition.status == "ended" and not item.past and item.enabled:
                continue  # no pending period for an ended definition
            row = ScheduleInstance(
                definition_id=definition.id, seq=item.seq, rule_date=item.day, due_date=item.day, moze_id=item.moze_id,
                moze_record_ids=list(item.record_ids), moze_payload=_jsonable(item.records), amount_override=amounts,
            )
            status = _moze_status(row, item, entries, started_at)
            if status == "pending" and definition.template_owner_edited:
                row.amount_override = None  # the period follows the owner's template price (decision 24)
                _note_amount_differs(report, definition, row, moze_lines)
            session.add(row)
            session.flush()
            rows.append(row)
            matched.add(row.id)
            counters["created"] += 1
            if status == "posted":
                counters["posted_from_past"] += 1
            elif status == "skipped" and not item.enabled:
                counters["skipped_disabled"] += 1
            continue
        matched.add(row.id)
        if adopted:
            row.moze_id = item.moze_id
            counters["adopted"] += 1
        elif row.moze_id != item.moze_id:
            row.moze_id = item.moze_id  # matched by a record id: MOZE changed the package's primary record
        row.moze_record_ids = list(item.record_ids)
        row.moze_payload = _jsonable(item.records)
        if row.status == "pending":
            if row.edited_by_owner:
                # Kept as the owner left it — also when its record is now past (R-F1: the record is suppressed by
                # covered_records, never imported beside it); its effective amounts are still compared (R-A2).
                counters["kept_owner_edited"] += 1
                if item.enabled:
                    _note_amount_differs(report, definition, row, moze_lines)
            elif item.past or not item.enabled:
                _moze_status(row, item, entries, started_at)
                counters["updated"] += 1
            else:
                # Spec: pending imported instances are refreshed unless owner-edited — amounts, and for a row matched
                # by its moze_id also the dates MOZE moved. A row adopted in this import keeps its dates (spec "Moved
                # period adopted": the HomeHub row's rule_date matched). A new date held by another posted or pending
                # period is refused (two pending periods on one day would collide on ux_schedule_instance_posted_day
                # when both post — Task 12 refuses the same move with 422 due_date).
                owner_price = definition.template_owner_edited  # decision 24: no MOZE override, only a report line
                changed = not owner_price and row.amount_override != amounts
                if not owner_price:
                    row.amount_override = amounts
                if not adopted and (row.rule_date, row.due_date) != (item.day, item.day):
                    taken = any(
                        other.id != row.id and other.status != "skipped" and other.due_date == item.day for other in rows
                    )
                    if not taken:
                        row.rule_date, row.due_date, changed = item.day, item.day, True
                if owner_price:
                    _note_amount_differs(report, definition, row, moze_lines)
                counters["updated" if changed else "kept"] += 1
        elif row.acted_by == "import":
            _moze_status(row, item, entries, started_at)
            counters["updated"] += 1
        else:
            counters["kept"] += 1  # posted by auto / owner, or skipped by HomeHub: the owner's decision stays
        session.flush()
    for row in rows:
        if row.id in matched:
            continue
        if row.moze_id is not None and row.moze_id not in mapped_ids and row.status == "pending" and not row.edited_by_owner:
            session.delete(row)
            counters["deleted"] += 1
        elif row.moze_id not in mapped_ids and row.acted_by == "import" and _recompute_import_row(row, entries, started_at):
            counters["updated"] += 1  # its record left the backup: recomputed from the entries now imported
    session.flush()


def _end_absent(session: Session, seen: set[int], entries, started_at: datetime, report: dict) -> None:
    absent = session.scalars(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.moze_id.is_not(None), ScheduleDefinition.created_locally.is_(False))
        .order_by(ScheduleDefinition.id)
    )
    for definition in absent:
        if definition.id in seen:
            continue
        bucket = report["definitions"][
            "single" if definition.moze_id.startswith("record:") else
            "installment" if definition.kind == "installment" else "recurring"
        ]
        for row in session.scalars(
            select(ScheduleInstance).where(
                ScheduleInstance.definition_id == definition.id, ScheduleInstance.acted_by == "import"
            )
        ):
            if _recompute_import_row(row, entries, started_at):
                report["instances"]["updated"] += 1  # its records left the backup with the definition
        history = session.scalar(
            select(
                exists().where(
                    ScheduleInstance.definition_id == definition.id, ScheduleInstance.status.in_(("posted", "skipped"))
                )
            )
        )
        if history:
            for row in session.scalars(
                select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.status == "pending")
            ):
                session.delete(row)
            if definition.status != "ended":
                definition.status = "ended"
                bucket["ended"] += 1
        else:
            session.delete(definition)
            bucket["deleted"] += 1
    session.flush()


def _amount_differs(session: Session, covered: dict[str, Covered], report: dict) -> None:
    """past_records_amount_differs, per line (R-F5): each line HomeHub posted (from the period's entries) against the
    matching MOZE record's amount — never period totals, so 8333 + 620 against 8400 + 553 is reported."""
    by_instance: dict[int, Covered] = {}
    for item in covered.values():
        if item.status == "posted":
            by_instance[item.instance_id] = item
    differs, lines = [], []
    for instance_id, item in sorted(by_instance.items()):
        instance = session.get(ScheduleInstance, instance_id)
        if instance is None or not instance.posted_entry_ids:
            continue
        definition = session.get(ScheduleDefinition, instance.definition_id)
        found = _line_differences(
            definition, instance, _posted_lines(session, definition.template, instance.posted_entry_ids), item.moze_lines
        )
        if found:
            differs.append(instance_id)
            lines.extend(found)
    report["past_records_amount_differs"] = {"count": len(differs), "instance_ids": differs, "lines": lines}


def loan_check(session: Session, mapped: MapResult, report: dict) -> None:
    """loan_remainder_check. Runs after restore_links: until then the schedule settlements of the loans point
    nowhere (ON DELETE SET NULL), and open_amount would ignore every repayment HomeHub posted."""
    for item in mapped.definitions:
        if item.source != "installment" or item.remainder is None:
            continue
        definition = session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == item.moze_id))
        reference = loan_line(definition.template) if definition is not None else None
        loan_id = reference[1].get("loan_entry_id") if reference is not None else None
        loan = session.get(LedgerEntry, loan_id) if loan_id is not None else None
        if loan is None:
            continue
        open_amount = Decimal(0) if loan.is_closed else settlement_service.open_amount(session, loan)
        report["loan_remainder_check"].append(
            {
                "definition": definition.name, "moze_remainder": plain(item.remainder), "open_amount": plain(open_amount),
                "difference": plain(open_amount - item.remainder),
            }
        )


def apply_schedules(
    session: Session,
    data,
    mapped: MapResult,
    settings,
    entries,
    covered: dict[str, Covered],
    *,
    started_at: datetime,
    today: date,
) -> dict:
    """Upsert imported definitions and instances (after the entries are inserted); returns the report block."""
    report = new_report(mapped)
    category_kinds = dict(session.execute(select(Category.id, Category.kind)).all())
    existing = {
        row.moze_id: row
        for row in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.moze_id.is_not(None)))
    }
    seen: set[int] = set()
    for item in mapped.definitions:
        lines, reason = _resolve_lines(item, settings, entries, category_kinds)
        current = existing.get(item.moze_id)
        if lines is None:
            report["review"].append({"moze_id": item.moze_id, "reason": reason})
            if current is not None and not current.created_locally:
                seen.add(current.id)  # keep its definition and template rather than ending it …
                # … but its instances still follow the new entries (import-posted rows, records now past); MOZE's
                # amounts are realigned from the mapped lines to the kept template's lines
                mapped_template = {"lines": [{"kind": line.kind, "amount": line.amount} for line in item.lines]}
                _apply_instances(session, current, item, entries, started_at, report, realign_from=mapped_template)
            continue
        if current is not None and current.created_locally:
            continue
        review_reason = item.review_reason or reason
        if reason == "loan_missing" and item.review_reason is None:
            report["review"].append({"moze_id": item.moze_id, "reason": "loan_missing"})
        template = {"lines": lines, "description": None, "tags": []}
        definition = _upsert_definition(session, item, template, review_reason, current, today, report, live=_live(item))
        seen.add(definition.id)
        _apply_instances(session, definition, item, entries, started_at, report)
    _end_absent(session, seen, entries, started_at, report)
    report["past_records_already_posted"] = sum(1 for item in covered.values() if item.status == "posted")
    report["past_records_already_skipped"] = sum(1 for item in covered.values() if item.status == "skipped")
    held = sorted({item.instance_id for item in covered.values() if item.status == "owner_pending"})
    for instance_id in held:  # R-F1: the owner's pending choice won over a MOZE record now past
        row = session.get(ScheduleInstance, instance_id)
        if row is not None:
            report["past_records_owner_pending"].append(
                {"definition_id": row.definition_id, "seq": row.seq, "date": row.due_date.isoformat()}
            )
    _amount_differs(session, covered, report)
    return report  # loan_check runs after restore_links (Task 18 wiring)


def _pause_loan_missing(definition: ScheduleDefinition, report: dict) -> None:
    if definition.status != "ended":
        definition.status = "paused"
    definition.review_reason = "loan_missing"
    report["review"].append({"moze_id": definition.moze_id or f"definition:{definition.id}", "reason": "loan_missing"})


def restore_links(session: Session, captured: CapturedLinks, report: dict) -> None:
    """After the entries are re-inserted: re-point local loan links and schedule settlements to the entry now
    carrying the captured moze_id; a vanished target clears the link and pauses the definition (loan_missing)."""
    needed = {moze_id for _, _, moze_id in captured.template_links} | {moze_id for _, moze_id in captured.settlement_links}
    new_ids = (
        dict(session.execute(select(LedgerEntry.moze_id, LedgerEntry.id).where(LedgerEntry.moze_id.in_(needed))).all())
        if needed else {}
    )
    for definition_id, index, moze_id in captured.template_links:
        definition = session.get(ScheduleDefinition, definition_id)
        template = copy.deepcopy(definition.template)
        new_id = new_ids.get(moze_id)
        template["lines"][index]["loan_entry_id"] = new_id
        definition.template = template
        if new_id is None:
            _pause_loan_missing(definition, report)
        else:
            report["relinked"]["templates"] += 1
    for entry_id, moze_id in captured.settlement_links:
        new_id = new_ids.get(moze_id)
        session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entry_id).values(settles_entry_id=new_id)
            .execution_options(synchronize_session=False)
        )
        if new_id is not None:
            report["relinked"]["settlements"] += 1
    for definition in session.scalars(select(ScheduleDefinition).where(ScheduleDefinition.created_locally.is_(True))):
        template, dangling = copy.deepcopy(definition.template), False
        for line in template["lines"]:
            if line.get("loan_entry_id") is not None and session.get(LedgerEntry, line["loan_entry_id"]) is None:
                line["loan_entry_id"], dangling = None, True
        if dangling:
            definition.template = template
            _pause_loan_missing(definition, report)
    session.flush()
```

- [ ] 17.4 Edit `services/accounting-service/app/services/moze_backup_import_service.py`:
  - imports: add `from . import schedule_import` and `from .schedule_import_map import map_schedules` after `from . import fx_rate_service, ledger_service`;
  - in `insert_entries` replace

```python
        if _is_future(record, cutoff):
            result.skipped_future[record["type"]] += 1
            result.skipped_records.append(record)
```

  with

```python
        if _is_future(record, cutoff):
            # Enabled future rows become schedule periods (schedule_import); disabled ones count with the other
            # disabled rows, so Σ skipped_future = records_mapped + rewards_ignored + Σ unsupported_types.
            if record["isEnabled"]:
                result.skipped_future[record["type"]] += 1
            else:
                result.disabled_skipped[record["type"]] += 1
            result.skipped_records.append(record)
```

  - in `replace_ledger_from_backup` replace

```python
    renamed = _apply_renames(session, renames or {})
    previous = _previous_moze_parts(session, data)
    delete_moze_entries(session)
```

  with

```python
    renamed = _apply_renames(session, renames or {})
    previous = _previous_moze_parts(session, data)
    mapped = map_schedules(data)
    schedule_import.lock_schedule_rows(session)  # D32: schedule rows before any entry is deleted
    schedule_import.merge_known_past_singles(session, mapped)
    started_at = _now()
    delete_moze_entries(session)
```

  and replace

```python
    delete_unused_rows(session, keep=settings.kept_moze_ids)
    session.flush()
```

  with

```python
    delete_unused_rows(session, keep=settings.kept_moze_ids)
    schedules = schedule_import.apply_schedules(
        session, data, mapped, settings, entries, {}, started_at=started_at, today=ledger_service._today()
    )
    schedule_import.loan_check(session, mapped, schedules)  # Task 18 moves it after restore_links
    session.flush()
```

  and add `"schedules": schedules,` to the returned dict right after the `"disabled_skipped": …` entry.

- [ ] 17.5 Run 17.2 again, then the importer suites.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_import_apply.py
.venv/bin/pytest -q -p no:warnings tests/integration/test_backup_entries_import.py tests/integration/test_backup_replace_and_report.py tests/integration/test_backup_settings_import.py tests/integration/test_backup_imports_api.py 2>&1 | tail -1
```

Expected: `24 passed`; then the same pass counts as after Task 16, 0 failed.

- [ ] 17.6 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/schedule_import.py services/accounting-service/app/services/moze_backup_import_service.py \
  services/accounting-service/tests/integration/test_schedule_import_apply.py
git commit -m "feat(accounting): backup import creates and re-imports schedule definitions and periods

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 18. Full-replace wiring: covered records, re-pointing, usage, balance, lock wait, job trigger

**Model:** opus

**Files:**
- Modify: `services/accounting-service/app/services/moze_import_service.py` (`import_lock` waits for shared holders; `delete_unused_rows` and `_archive_disappeared_accounts` count template references)
- Modify: `services/accounting-service/app/services/moze_backup_import_service.py` (`insert_entries(skip_ids=)`, `_dependants`, `_moze_part(extra_ids=, extra_amounts=)`, `_account_reports(stand_ins, suppressed)`, `replace_ledger_from_backup`, `run_backup_import(trigger_job=True)` triggers the job, the CLI `main` runs it inline)
- Test: `services/accounting-service/tests/integration/test_schedule_import_replace.py`

**Interfaces:**
- Consumes: `schedule_import.capture_links`, `covered_records`, `template_usage`, `restore_links`, `stand_in_entry_ids`, `suppressed_amounts`, `apply_schedules` (Task 17); `schedule_job.trigger_after_import`, `schedule_job.run_after_cli_import` (Task 15); `schedule_locks.import_key_free` (tests); `schedule_posting.post_instance`, `schedule_service.skip_instance`, `schedule_generation.generate` (tests).
- Produces: `moze_import_service.IMPORT_LOCK_WAIT_SEC = 30`, `IMPORT_LOCK_POLL_SEC = 0.2`, `import_lock(engine, *, wait_seconds=IMPORT_LOCK_WAIT_SEC)`; `_archive_disappeared_accounts(session, named_in_file, keep_ids=frozenset())`; `insert_entries(..., skip_ids=frozenset())`.

Rules (spec "Record mapping", "Transactional full replace and report", D32, D37):
- Order inside the ledger transaction: renames → previous parts → map → **lock schedule rows** → merge known past singles → capture links → covered records → delete MOZE entries → settings → template usage → archive (template accounts kept) → insert entries **skipping covered records** (and their fee / reward dependants) → delete unused rows (template references count as usage) → apply schedules → restore links → **loan check** (after re-pointing, so HomeHub's repayments count in `open_amount`) → stand-in ids → balance report (`moze_part` adds the schedule entries of covered posted periods on the account, posted by the cache date).
- `import_lock`: `pg_try_advisory_lock` retried every 0.2 s for up to 30 s while only shared holders (schedule writers) hold the key; an exclusive holder (another import) is refused at once (`ImportAlreadyRunningError`) as before; still refused after 30 s. Operator note: the daily job re-takes the shared key once per posting transaction with sub-millisecond gaps, so an import started during a long job posting loop can find the key shared on every poll and is refused after 30 s; that is accepted — run the import again once the job finishes (the README and runbook say so).
- After a real (non-dry-run) import that succeeded, once `import_lock` has been released, `schedule_job.trigger_after_import(engine)` (the API path: the in-process scheduler runs the job); a dry run never triggers it.
- ★CLI (Multica R-F4): the standalone `python -m app.services.moze_backup_import_service` process has no scheduler, so its `main` calls `run_backup_import(…, trigger_job=False)` and, after a real import that succeeded (the import lock is released when `run_backup_import` returns), runs `schedule_job.run_after_cli_import(engine)` inline: generation always; posting only when `ACCOUNTING_SCHEDULER_ENABLED` is truthy (same parsing as the scheduler), otherwise the run counts and logs the due-but-unposted instances. It prints one stderr line `schedule_job: status <s>, generated <n>, posted <n>, failed <n>, due_unposted <n>` (counts only); the CLI's exit code stays the import's.
- ★Owner-edited pending period whose record turned past (Multica R-F1, D37): `covered_records` returns it as `owner_pending`, so the record is not inserted (skipped like a covered record), the instance stays pending with its override, `apply_schedules` lists it under `past_records_owner_pending`, and the balance comparison adds the suppressed record's MOZE amount to `moze_part` (`schedule_import.suppressed_amounts` → `_account_reports(…, suppressed)` → `_moze_part(extra_amounts=)`, by the cache date), since MOZE booked it and HomeHub has not yet.
- ★Per-line amount comparison (Multica R-F5): `past_records_amount_differs` compares each line HomeHub posted with the matching MOZE record (Task 17 `_amount_differs`), so equal totals with a different principal / interest split are reported.

- [ ] 18.1 Write the failing test `services/accounting-service/tests/integration/test_schedule_import_replace.py`:

```python
"""Mirror-period reconciliation in the full replace (spec "Record mapping", "Transactional full replace and report",
"Schedule definitions and instances from the backup"; Review Focus 3)."""

import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import Account, Category, LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_generation as generation
from app.services import schedule_job, schedule_service, settlement_service
from app.services import schedule_posting as posting
from app.services import schedule_rules as rules
from app.schemas.schedules import InstanceUpdateIn
from app.services import moze_backup_import_service
from app.services.moze_backup_import_service import run_backup_import
from app.services.moze_import_service import IMPORT_LOCK_KEY, ImportAlreadyRunningError, import_lock
from app.services.schedule_locks import import_key_free
from tests.helpers import _by_moze_id, _import_backup


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def _loan_data(backup, *, exported_at="2026-10-01T17:00:37", first_total=-8333, first_interest=-620, with_loan=True):
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    records = [
        _rec(backup, "R-REP1", "2026-10-09", type_=6, price=first_total, eventID="INS-1", packageID="PK-1",
             relatedID="R-LOAN", target="T-BANK"),
        _rec(backup, "R-INT1", "2026-10-09", type_=15, price=first_interest, eventID="INS-1", packageID="PK-1"),
        _rec(backup, "R-REP2", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
             relatedID="R-LOAN", target="T-BANK"),
        _rec(backup, "R-INT2", "2026-11-09", type_=15, price=-620, eventID="INS-1", packageID="PK-2"),
    ]
    if with_loan:
        records.append(_rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"))
    return backup.data(
        exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包", cacheDate="2026-10-12T00:00:00")],
        targets=[backup.target("T-BANK", "範例銀行")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000, remainder=16667)],
        records=records,
    )


def _post_first_loan_period(db, backup, today) -> ScheduleInstance:
    today(date(2026, 10, 3))
    _import_backup(db, _loan_data(backup))
    instance = db.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-REP1"))
    today(date(2026, 10, 9))
    assert posting.post_instance(db, instance.id, actor="auto", job=True, today=date(2026, 10, 9)).outcome == "posted"
    db.commit()
    return instance


def _schedule_entries(db) -> list[LedgerEntry]:
    db.expire_all()
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.source == "schedule").order_by(LedgerEntry.id)))


def test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record(db_session, backup, today):
    # Review Focus 3, spec "Period already posted by HomeHub" for a loan period.
    instance = _post_first_loan_period(db_session, backup, today)
    posted_ids = _schedule_entries(db_session)
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R-REP1") is None and _by_moze_id(db_session, "R-INT1") is None
    assert [entry.id for entry in _schedule_entries(db_session)] == [entry.id for entry in posted_ids]
    loan = _by_moze_id(db_session, "R-LOAN")
    assert settlement_service.open_amount(db_session, loan) == Decimal("16667")
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert (row.status, row.acted_by) == ("posted", "auto")
    block = summary["schedules"]
    assert (block["past_records_already_posted"], block["past_records_amount_differs"]) == (
        2, {"count": 0, "instance_ids": [], "lines": []},
    )
    assert Decimal(summary["accounts"][0]["moze_part"]) == Decimal("25000") - Decimal("8953")
    # loan_check runs after restore_links: HomeHub's repayment counts, so MOZE's remainder matches (no false alarm).
    assert [(row["moze_remainder"], row["open_amount"], row["difference"]) for row in block["loan_remainder_check"]] == [
        ("16667", "16667", "0"),
    ]


def test_single_record_homehub_posted_is_not_imported_once_it_is_past(db_session, backup, today):
    # Spec "Record mapping": a record in the moze_record_ids of an instance HomeHub posted is not imported — also
    # for a single future record (record:<id>) that the next backup holds as a past record without an eventID.
    today(date(2026, 10, 3))

    def data(exported_at):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")],
            records=[_rec(backup, "X1", "2026-10-09", name="年費")],
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "X1"))
    assert posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9)).outcome == "posted"
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "X1") is None
    assert len(_schedule_entries(db_session)) == 1
    assert summary["schedules"]["past_records_already_posted"] == 1
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "record:X1"))
    db_session.expire_all()
    assert (definition is not None, db_session.get(ScheduleInstance, instance.id).status) == (True, "posted")
    assert summary["schedules"]["definitions"]["single"]["ended"] == 0


def test_different_amount_reported(db_session, backup, today):
    # Spec "Different amount reported".
    instance = _post_first_loan_period(db_session, backup, today)
    today(date(2026, 10, 12))
    summary = _import_backup(db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00", first_total=-8400))
    block = summary["schedules"]["past_records_amount_differs"]
    assert (block["count"], block["instance_ids"]) == (1, [instance.id])
    assert [(item["line"], item["kind"], item["amount"], item["moze_amount"]) for item in block["lines"]] == [
        (0, "repayment", "8333", "8400"),
    ]
    assert _by_moze_id(db_session, "R-REP1") is None


def test_equal_totals_with_a_different_allocation_are_reported_per_line(db_session, backup, today):
    # Multica R-F5: HomeHub posted 8333 + 620; MOZE's record says 8400 + 553 — the same 8953, a different split.
    instance = _post_first_loan_period(db_session, backup, today)
    today(date(2026, 10, 12))
    summary = _import_backup(
        db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00", first_total=-8400, first_interest=-553)
    )
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "INS-1"))
    block = summary["schedules"]["past_records_amount_differs"]
    assert (block["count"], block["instance_ids"]) == (1, [instance.id])
    assert block["lines"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": 1, "date": "2026-10-09", "line": 0,
         "kind": "repayment", "amount": "8333", "moze_amount": "8400"},
        {"definition_id": definition.id, "name": definition.name, "seq": 1, "date": "2026-10-09", "line": 1,
         "kind": "interest", "amount": "620", "moze_amount": "553"},
    ]
    # identical amounts → nothing reported (test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record)


def _owner_period_data(backup, exported_at):
    return backup.data(
        exported_at=exported_at,
        accounts=[backup.account("A-WALLET", "錢包", cacheDate=f"{exported_at[:10]}T00:00:00")],
        periods=[backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")],
        records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R", "2026-10-09", eventID="PER-M")],
    )


def test_owner_edited_period_whose_record_turned_past_is_booked_once(db_session, backup, today):
    # Multica R-F1: the owner edits an imported future period; the next backup holds its record as past. The owner's
    # pending choice wins: no entry is imported, the period stays pending with its override, the report lists it, and
    # a later post writes it once (a further re-import then counts it as already posted).
    today(date(2026, 10, 3))
    _import_backup(db_session, _owner_period_data(backup, "2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    schedule_service.update_instance(db_session, instance.id, InstanceUpdateIn(amounts=["120"]))
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, _owner_period_data(backup, "2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R") is None and _schedule_entries(db_session) == []
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert (row.status, row.amount_override, row.edited_by_owner, row.posted_entry_ids) == ("pending", ["120"], True, [])
    block = summary["schedules"]
    assert block["past_records_owner_pending"] == [{"definition_id": row.definition_id, "seq": row.seq, "date": "2026-10-09"}]
    assert block["past_records_already_posted"] == 0
    assert [(item["amount"], item["moze_amount"]) for item in block["amount_differs"]] == [("120", "100")]  # R-A2
    assert Decimal(summary["accounts"][0]["moze_part"]) == Decimal("-200")  # R-SEP and the suppressed R, as MOZE

    assert posting.post_instance(db_session, instance.id, actor="owner").outcome == "posted"
    db_session.commit()
    again = _import_backup(db_session, _owner_period_data(backup, "2026-10-13T17:00:00"))
    assert [entry.amount for entry in _schedule_entries(db_session)] == [Decimal("-120")]
    assert _by_moze_id(db_session, "R") is None
    assert (again["schedules"]["past_records_already_posted"], again["schedules"]["past_records_owner_pending"]) == (1, [])


def test_loan_link_survives_the_full_replace(db_session, backup, today):
    # Spec "Loan link survives the full replace".
    _post_first_loan_period(db_session, backup, today)
    old_loan_id = _by_moze_id(db_session, "R-LOAN").id
    summary = _import_backup(db_session, _loan_data(backup))
    new_loan = _by_moze_id(db_session, "R-LOAN")
    assert new_loan.id != old_loan_id
    repayment = next(entry for entry in _schedule_entries(db_session) if entry.is_settlement)
    assert repayment.settles_entry_id == new_loan.id
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "INS-1"))
    assert definition.template["lines"][0]["loan_entry_id"] == new_loan.id
    assert summary["schedules"]["relinked"]["settlements"] == 1


def test_period_already_posted_by_homehub(db_session, backup, today):
    # Spec "Period already posted by HomeHub".
    today(date(2026, 10, 3))
    period = backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")

    def data(exported_at):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")], periods=[period],
            records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R", "2026-10-09", eventID="PER-M")],
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9))
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R") is None
    assert len(_schedule_entries(db_session)) == 1
    assert summary["schedules"]["past_records_already_posted"] == 1


def test_posted_homehub_period_adopted(db_session, backup, today):
    # Spec "Posted HomeHub period adopted".
    today(date(2026, 10, 3))
    weekly = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")

    def data(exported_at, extra=()):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")],
            periods=[backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")],
            records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in weekly] + list(extra),
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "PER-W"))
    generation.generate(db_session, definition, date(2026, 10, 3))
    generated = db_session.scalar(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.due_date == date(2027, 10, 4))
    )
    today(date(2027, 10, 4))
    posting.post_instance(db_session, generated.id, actor="auto", job=True, today=date(2027, 10, 4))
    db_session.commit()
    today(date(2027, 10, 6))

    summary = _import_backup(db_session, data("2027-10-06T17:00:00", [_rec(backup, "R", "2027-10-04", eventID="PER-W")]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, generated.id)
    assert (row.moze_id, row.status, row.acted_by) == ("R", "posted", "auto")
    assert _by_moze_id(db_session, "R") is None
    assert summary["schedules"]["past_records_already_posted"] == 1
    assert summary["schedules"]["instances"]["adopted"] == 1


def test_a_period_homehub_skipped_is_not_imported(db_session, backup, today):
    today(date(2026, 10, 3))
    period = backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")
    days = ("2026-09-21", "2026-10-05")

    def data(exported_at):
        return backup.data(exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")], periods=[period],
                           records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in days])

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    pending = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-2026-10-05"))
    schedule_service.skip_instance(db_session, pending.id)
    db_session.commit()

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R-2026-10-05") is None
    assert summary["schedules"]["past_records_already_skipped"] == 1


def test_re_pointing_finds_no_loan(db_session, seed, backup, today):
    # Spec "Re-pointing finds no loan".
    today(date(2026, 10, 3))
    _import_backup(db_session, _loan_data(backup))
    wallet = db_session.scalar(select(Account).where(Account.moze_id == "A-WALLET"))
    loan = _by_moze_id(db_session, "R-LOAN")
    local = seed.definition([seed.line("repayment", wallet, "1000", loan_entry_id=loan.id)], kind="installment",
                            name="本地貸款", times=12)
    db_session.commit()

    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))

    db_session.expire_all()
    row = db_session.get(ScheduleDefinition, local.id)
    assert row.template["lines"][0]["loan_entry_id"] is None
    assert (row.status, row.review_reason) == ("paused", "loan_missing")
    assert {"moze_id": f"definition:{local.id}", "reason": "loan_missing"} in summary["schedules"]["review"]


def test_template_reference_keeps_a_category_and_an_account(db_session, seed, backup, today):
    # Spec "Template reference keeps a category" (and a referenced account is never archived as disappeared).
    today(date(2026, 10, 3))
    streaming = seed.category("串流", moze_id="K-STREAM")
    spare = seed.account("備用")
    seed.definition([seed.line("expense", spare, "390", category_id=streaming.id)], name="Netflix")
    db_session.commit()

    _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))

    db_session.expire_all()
    assert db_session.get(Category, streaming.id) is not None
    assert db_session.get(Account, spare.id).is_archived is False


def test_schedule_entry_survives_a_backup_import(db_session, seed, backup, today):
    # Ledger spec "Schedule entry survives a backup import".
    today(date(2026, 10, 30))
    card = seed.account("範例卡")
    entry = seed.entry(card, "-390", day=date(2026, 10, 22), source="schedule")
    db_session.commit()
    _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))
    db_session.expire_all()
    row = db_session.get(LedgerEntry, entry.id)
    assert (row.amount, row.source, row.entry_date) == (Decimal("-390"), "schedule", date(2026, 10, 22))


def test_skipped_future_adds_up(db_session, backup, today):
    # The report invariant behind spec "Real backup schedule counts".
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=[backup.account("A-WALLET", "錢包")],
        periods=[backup.period("PER-W", unit=1, days=2, start="2026-10-05T00:00:00")],
        records=[
            _rec(backup, "W-1", "2026-10-05", eventID="PER-W"), _rec(backup, "W-2", "2026-10-12", eventID="PER-W"),
            _rec(backup, "RW", "2026-10-20", type_=14, price=30), _rec(backup, "ADJ", "2026-10-20", type_=7, price=50),
            _rec(backup, "ONE", "2026-11-20"), _rec(backup, "OFF", "2026-11-21", isEnabled=False),
        ],
    )
    summary = _import_backup(db_session, data)
    block = summary["schedules"]
    assert sum(summary["skipped_future"].values()) == block["records_mapped"] + block["rewards_ignored"] + sum(
        block["unsupported_types"].values()
    ) == 5
    assert (block["records_mapped"], block["rewards_ignored"], block["unsupported_types"]) == (3, 1, {"7": 1})


def test_import_waits_for_schedule_writers_then_runs(pg_engine, db_session):
    writer = pg_engine.connect()
    writer.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY})
    release = threading.Timer(0.5, lambda: (writer.commit(), writer.close()))
    release.start()
    started = time.monotonic()
    with import_lock(pg_engine):
        waited = time.monotonic() - started
    release.join()
    assert 0.3 <= waited < 5


def test_import_is_refused_at_once_while_another_import_runs(pg_engine, db_session):
    holder = pg_engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
    try:
        started = time.monotonic()
        with pytest.raises(ImportAlreadyRunningError):
            with import_lock(pg_engine):
                pass
        assert time.monotonic() - started < 2
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        holder.close()


def test_real_import_triggers_a_job_run_and_a_dry_run_does_not(pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(schedule_job, "trigger_after_import", lambda engine: calls.append(engine))
    zip_path = tmp_path / "MOZE_4.0.zip"
    zip_path.write_bytes(b"PK")
    doc = backup.doc(accounts=[backup.account("A-WALLET", "錢包")])
    run_backup_import(pg_engine, zip_path, zip_path.name, dry_run=True, exporter=fake_exporter(doc))
    assert calls == []
    report = run_backup_import(pg_engine, zip_path, zip_path.name, exporter=fake_exporter(doc))
    assert report["status"] == "succeeded" and calls == [pg_engine]


@pytest.mark.parametrize(("switch", "posted"), [("true", 1), ("false", 0)], ids=["scheduler_on", "scheduler_off"])
def test_cli_import_runs_the_job_inline_after_releasing_the_lock(
    pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch, capsys, today, switch, posted
):
    # Multica R-F4: the standalone importer CLI (no scheduler in its process) runs the job itself, after the import
    # lock is released: generation always, posting only with ACCOUNTING_SCHEDULER_ENABLED on, else a due count.
    today(date(2026, 10, 9))
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", switch)
    triggered, seen_free = [], []
    monkeypatch.setattr(schedule_job, "trigger_after_import", lambda engine: triggered.append(engine))
    real_run = schedule_job.run

    def run_checking_the_lock(engine, trigger, **kwargs):
        seen_free.append(import_key_free(engine))  # the import's advisory lock is already released
        return real_run(engine, trigger, **kwargs)

    monkeypatch.setattr(schedule_job, "run", run_checking_the_lock)
    zip_path = tmp_path / "MOZE_4.0.zip"
    zip_path.write_bytes(b"PK")
    doc = backup.doc(
        exported_at="2026-10-08T03:00:00", accounts=[backup.account("A-WALLET", "錢包")],
        periods=[backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")],
        records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R-OCT", "2026-10-09", eventID="PER-M")],
    )

    assert moze_backup_import_service.main([str(zip_path)], engine=pg_engine, exporter=fake_exporter(doc)) == 0

    err = capsys.readouterr().err
    assert triggered == [] and seen_free == [True]
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-OCT"))
    db_session.expire_all()
    assert db_session.get(ScheduleInstance, instance.id).status == ("posted" if posted else "pending")
    generated = db_session.scalar(select(func.count()).select_from(ScheduleInstance).where(ScheduleInstance.moze_id.is_(None)))
    assert generated > 0  # the horizon was generated in both cases
    assert f"posted {posted}, failed 0, due_unposted {1 - posted}" in err


def test_the_old_schedule_listing_is_gone(client):
    # Spec REMOVED "Scheduled data preserved for phase 4".
    assert client.get("/imports/schedules").status_code == 404
```

- [ ] 18.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_import_replace.py
```

Expected: failures — the covered record is imported again (`_by_moze_id(db_session, "R-REP1")` / `"X1"` is not None), the local loan link is not re-pointed, the lock-wait test is refused at once, the trigger is never called, the owner-edited period's past record is imported beside it (R-F1), the CLI never runs the job (R-F4); `test_equal_totals_with_a_different_allocation_are_reported_per_line` already passes once Task 17's per-line comparison is in (its records are covered only after this task's wiring, so it fails here too); `test_the_old_schedule_listing_is_gone`, `test_schedule_entry_survives_a_backup_import`, `test_skipped_future_adds_up` (Task 17 already reports the counts) and `test_import_is_refused_at_once_while_another_import_runs` (an exclusive holder was already refused at once) already pass.

- [ ] 18.3 Edit `services/accounting-service/app/services/moze_import_service.py`:
  - add `import time` to the imports and, below `IMPORT_LOCK_KEY = 0x4D4F5A45 …`, add

```python
IMPORT_LOCK_WAIT_SEC = 30  # D32: schedule writers share the key for a few milliseconds each; an import waits for them
IMPORT_LOCK_POLL_SEC = 0.2
```

  - replace `import_lock` with

```python
def _held_exclusively(conn: Connection) -> bool:
    """Another import holds the key (exclusive); shared holders are schedule writers that finish in milliseconds.
    A one-bigint advisory key shows as classid = high 32 bits (0 here), objid = low 32 bits, objsubid = 1; a two-int
    key with the same low word has objsubid = 2 and must not be mistaken for an import."""
    return bool(
        conn.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 AND objid = :key "
                "AND objsubid = 1 AND mode = 'ExclusiveLock' AND granted"
            ),
            {"key": IMPORT_LOCK_KEY},
        ).scalar_one()
    )


@contextmanager
def import_lock(engine: Engine, *, wait_seconds: float = IMPORT_LOCK_WAIT_SEC) -> Iterator[Connection]:
    """Hold the shared advisory lock for one import (CSV or backup, real or dry run) on a dedicated connection.

    While schedule writers share the key (pg_try_advisory_xact_lock_shared), retry for up to `wait_seconds`; another
    import (an exclusive holder) is refused at once.
    """
    if import_locked():
        raise ImportLockedError("MOZE import is locked (ACCOUNTING_IMPORT_LOCKED=true)")
    with engine.connect() as conn:
        deadline = time.monotonic() + wait_seconds
        while True:
            acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY}).scalar_one()
            conn.commit()
            if acquired:
                break
            busy = _held_exclusively(conn)
            conn.commit()
            if busy or time.monotonic() >= deadline:
                raise ImportAlreadyRunningError("import already running")
            time.sleep(IMPORT_LOCK_POLL_SEC)
        try:
            yield conn
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            conn.commit()
```

  - in `delete_unused_rows` replace the `statements = [...]` list and the `clauses = {...}` dict with

```python
    statements = [
        "DELETE FROM category c WHERE c.parent_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id) AND {category_template} AND {category}",
        "DELETE FROM category c WHERE c.parent_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.category_id = c.id) "
        "AND NOT EXISTS (SELECT 1 FROM category child WHERE child.parent_id = c.id) AND {category_template} AND {category}",
        "DELETE FROM project p WHERE NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.project_id = p.id) "
        "AND NOT EXISTS (SELECT 1 FROM category c WHERE c.default_project_id = p.id) "
        "AND NOT EXISTS (SELECT 1 FROM reward_rule r WHERE r.reward_project_id = p.id) AND {project_template} AND {project}",
        "DELETE FROM counterparty cp WHERE NOT EXISTS (SELECT 1 FROM ledger_entry e WHERE e.counterparty_id = cp.id) "
        "AND {counterparty_template} AND {counterparty}",
    ]
```

```python
        clauses = {
            "category": _removable("c", "category", keep, params),
            "project": _removable("p", "project", keep, params),
            "counterparty": _removable("cp", "counterparty", keep, params),
            "category_template": _unused_by_templates("c", "category_id"),
            "project_template": _unused_by_templates("p", "project_id"),
            "counterparty_template": _unused_by_templates("cp", "counterparty_id"),
        }
```

  and add above `delete_unused_rows`

```python
def _unused_by_templates(alias: str, key: str) -> str:
    """SQL condition: no schedule template line names the row (a template reference counts as usage, D29/D37)."""
    return (
        "NOT EXISTS (SELECT 1 FROM schedule_definition d, jsonb_array_elements(d.template -> 'lines') l "
        f"WHERE l ->> '{key}' = {alias}.id::text)"
    )
```

  - replace `def _archive_disappeared_accounts(session: Session, named_in_file: set[str]) -> list[str]:` with `def _archive_disappeared_accounts(session: Session, named_in_file: set[str], keep_ids: Collection[int] = frozenset()) -> list[str]:` and its first loop line `if account.name in named_in_file or account.settings_locally_edited:` with `if account.name in named_in_file or account.settings_locally_edited or account.id in keep_ids:`.

- [ ] 18.4 Edit `services/accounting-service/app/services/moze_backup_import_service.py`:
  - add `Collection` to `from typing import Callable, Mapping, Sequence`, and `from . import schedule_job` next to `from . import schedule_import`;
  - add below `_disabled_ids`

```python
def _dependants(records: list[dict], roots: Collection[str]) -> set[str]:
    """Fee and reward records hanging off `roots` (records not imported because HomeHub already covered them)."""
    if not roots:
        return set()
    children: dict[str, list[str]] = {}
    for record in records:
        if record["feeID"] and record["feeID"] != record["identifier"]:
            children.setdefault(record["identifier"], []).append(record["feeID"])
        if record["type"] == 14 and record["rewardRecordID"]:
            children.setdefault(record["rewardRecordID"], []).append(record["identifier"])
    found: set[str] = set()
    pending = [child for root in roots for child in children.get(root, [])]
    while pending:
        identifier = pending.pop()
        if identifier not in found:
            found.add(identifier)
            pending.extend(children.get(identifier, []))
    return found
```

  - change the signature of `insert_entries` to end with `*, allow_fx_outliers: bool, skip_ids: frozenset[str] = frozenset()) -> EntryResult:`; in its record loop make the first statement `if record["identifier"] in skip_ids: continue  # HomeHub already posted or skipped this period (D37)`; and replace `live = [record for record in current if record["identifier"] not in disabled]` with

```python
    disabled |= _dependants(data.records, skip_ids)
    live = [record for record in current if record["identifier"] not in disabled]
```

  - replace `_moze_part` with

```python
def _moze_part(
    session: Session, account: Account, cutoff: date, *, sources: Sequence[str], any_moze_id: bool,
    extra_ids: Collection[int] = (), extra_amounts: Collection[tuple[date, Decimal]] = (),
) -> Decimal:
    """opening + Σ amount of the account's MOZE entries (and `extra_ids`: schedule entries standing in for records
    skipped as already posted, D37) posted up to `cutoff`, plus `extra_amounts` up to `cutoff`: MOZE records suppressed
    for an owner-edited pending period (R-F1), which MOZE booked and HomeHub has not yet."""
    condition = LedgerEntry.source.in_(sources)
    if any_moze_id:
        condition = or_(condition, LedgerEntry.moze_id.is_not(None))
    if extra_ids:
        condition = or_(condition, LedgerEntry.id.in_(list(extra_ids)))
    total = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account.id, LedgerEntry.posted_date <= cutoff, condition
        )
    )
    suppressed = sum((amount for day, amount in extra_amounts if day <= cutoff), Decimal(0))
    return _amount(account.opening_balance + total + suppressed)
```

  - change `def _account_reports(session, data, settings, previous) -> list[dict]:` to `def _account_reports(session, data, settings, previous, stand_ins: Collection[int] = (), suppressed: Collection[tuple[str, date, Decimal]] = ()) -> list[dict]:` and its `moze_part = _moze_part(session, account, _cache_date(record, data), sources=(SOURCE,), any_moze_id=False)` to

```python
        extra = [(day, amount) for moze_account, day, amount in suppressed if moze_account == record["identifier"]]
        moze_part = _moze_part(
            session, account, _cache_date(record, data), sources=(SOURCE,), any_moze_id=False, extra_ids=stand_ins,
            extra_amounts=extra,
        )
```

  - in `replace_ledger_from_backup` replace everything from `mapped = map_schedules(data)` through `accounts = _account_reports(session, data, settings, previous)` with

```python
    mapped = map_schedules(data)
    schedule_import.lock_schedule_rows(session)  # D32: schedule rows before any entry is deleted
    schedule_import.merge_known_past_singles(session, mapped)
    captured = schedule_import.capture_links(session)
    covered = schedule_import.covered_records(session, mapped)
    started_at = _now()
    delete_moze_entries(session)
    settings = upsert_settings(session, data)
    usage = schedule_import.template_usage(session)
    archived = _archive_disappeared_accounts(
        session, {account.name for account in settings.accounts.values()}, keep_ids=usage["account"]
    )
    entries = insert_entries(
        session, data, settings, import_run_id, rates or {}, allow_fx_outliers=allow_fx_outliers,
        skip_ids=frozenset(covered),
    )
    delete_unused_rows(session, keep=settings.kept_moze_ids)
    schedules = schedule_import.apply_schedules(
        session, data, mapped, settings, entries, covered, started_at=started_at, today=ledger_service._today()
    )
    schedule_import.restore_links(session, captured, schedules)
    schedule_import.loan_check(session, mapped, schedules)  # after re-pointing: HomeHub's repayments count
    stand_ins = schedule_import.stand_in_entry_ids(session, covered)
    suppressed = schedule_import.suppressed_amounts(covered)  # R-F1: owner-held periods MOZE already booked
    session.flush()

    accounts = _account_reports(session, data, settings, previous, stand_ins, suppressed)
```

  - in `run_backup_import` replace

```python
    with import_lock(engine) as conn:
        return _run_backup_locked(
            conn, data, file_name, sha256, dry_run=dry_run, renames=renames or {}, strict=strict,
            allow_fx_outliers=allow_fx_outliers, http_get=http_get,
        )
```

  with

```python
    with import_lock(engine) as conn:
        report = _run_backup_locked(
            conn, data, file_name, sha256, dry_run=dry_run, renames=renames or {}, strict=strict,
            allow_fx_outliers=allow_fx_outliers, http_get=http_get,
        )
    if trigger_job and not dry_run and report["status"] == "succeeded":
        schedule_job.trigger_after_import(engine)  # D34: once the advisory lock is released
    return report
```

  and add the keyword `trigger_job: bool = True,` after `keep_json: Path | None = None,` in the signature of `run_backup_import` (the API router keeps the default).
  - in `main`, add `trigger_job=False,` to the `run_backup_import(...)` call (after `keep_json=args.keep_json,`) and replace the final

```python
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0
```

  with

```python
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not args.dry_run and report["status"] == "succeeded":
        # R-F4: this process has no scheduler; the import lock is released, so the job runs here (posting only with
        # ACCOUNTING_SCHEDULER_ENABLED on, else a due-but-unposted count). Counts only on stderr.
        job = schedule_job.run_after_cli_import(engine)
        print(
            f"schedule_job: status {job['status']}, generated {job['generated']}, posted {len(job['posted'])}, "
            f"failed {len(job['failed'])}, due_unposted {job.get('due_unposted', 0)}",
            file=sys.stderr,
        )
    return 0
```

- [ ] 18.5 Run 18.2 again, then the whole backend suite.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
.venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_import_replace.py
.venv/bin/pytest -q -p no:warnings tests 2>&1 | tail -2
```

Expected: `19 passed`; the full suite `N passed`, 0 failed, with N = Task 3's count + 9 + 13 + 12 + 5 + 15 + 9 + 19 + 12 + 25 + 12 + 4 + 29 + 15 + 24 + 19 (Tasks 4–18 in order; = Task 3's count + 222).

- [ ] 18.6 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/app/services/moze_import_service.py services/accounting-service/app/services/moze_backup_import_service.py \
  services/accounting-service/tests/integration/test_schedule_import_replace.py
git commit -m "feat(accounting): import skips periods HomeHub booked, re-points loan links, waits for schedule writers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 19. Lock-order race tests and README

**Model:** opus

**Files:**
- Test: `services/accounting-service/tests/integration/test_schedule_lock_order.py`
- Modify: `services/accounting-service/README.md`

**Interfaces:**
- Consumes: `tests.helpers.race(engine, first, second)` (phase 2a); `entry_write_service.delete_entry`; `schedule_service.update_definition`, `repost_instance`; `schedule_posting.post_instance`; `moze_import_service.import_lock`.
- Produces: nine race tests pinning D32 (post vs delete of its entry, edit vs post, import key vs post, two deletes of one period, loan posts of neighbouring periods, two deletes reviving an ended definition, loan close-out vs a reviving delete, a delete after a loan close-out (409 `排程剛結束，請重試刪除`, then the retry revives), repost vs a loan delete on an ended definition) and the README section "Schedules".

Rules: each test runs `first` in an open transaction holding its locks, then `second` in a thread that must block (still running after 0.5 s) and finish after `first` commits — never a deadlock, never a lost update.

- [ ] 19.1 Write the test `services/accounting-service/tests/integration/test_schedule_lock_order.py`:

```python
"""D32 lock order under two overlapping transactions (tests.helpers.race)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.schemas.schedules import DefinitionUpdateIn
from app.services import entry_write_service as ews
from app.services import schedule_posting as posting
from app.services import schedule_service
from app.services.errors import ConflictError
from app.services.moze_import_service import import_lock
from tests.helpers import race


@pytest.fixture()
def period(seed, db_session, today):
    """A posted loan period (repayment + interest) on 2026-11-09; today is 2026-11-10."""
    today(date(2026, 11, 10))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id)
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36,
    )
    instance = seed.instance(definition, 1, date(2026, 11, 9))
    repayment_id, interest_id = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    return {
        "bank": bank, "definition": definition, "instance": instance, "payable": payable, "repayment": repayment_id,
        "interest": interest_id,
    }


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def test_repost_waits_for_a_delete_of_its_entry(pg_engine, db_session, period):
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: schedule_service.repost_instance(db, period["instance"].id, ["8333", "598"]),
    )
    assert outcome == "committed"
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.is_partial) == ("posted", False)
    amounts = sorted(db_session.get(LedgerEntry, entry_id).amount for entry_id in row.posted_entry_ids)
    assert amounts == [Decimal("-8333"), Decimal("-598")]


def test_definition_edit_waits_for_a_post(pg_engine, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()
    body = DefinitionUpdateIn(
        name="Netflix", template={"lines": [{"kind": "expense", "account_id": card.id, "amount": "420", "currency": "TWD"}]},
        interval_unit="month", anchor_date=date(2026, 10, 22),
    )
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, instance.id, actor="owner"),
        lambda db: schedule_service.update_definition(db, definition.id, body),
    )
    assert outcome == "committed"
    row = _row(db_session, instance.id)
    assert row.status == "posted"
    assert db_session.get(LedgerEntry, row.posted_entry_ids[0]).amount == Decimal("-390")


def test_import_waits_for_a_post(pg_engine, db_session, seed, today):
    today(date(2026, 10, 22))
    card = seed.account("範例卡")
    definition = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(definition, 1, date(2026, 10, 22))
    db_session.commit()

    def take_the_import_lock(_db):
        with import_lock(pg_engine):
            pass

    outcome = race(pg_engine, lambda db: posting.post_instance(db, instance.id, actor="owner"), take_the_import_lock)
    assert outcome == "committed"
    assert _row(db_session, instance.id).status == "posted"


def test_two_deletes_of_one_period_queue(pg_engine, db_session, period):
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: ews.delete_entry(db, period["repayment"]),
    )
    assert outcome == "committed"
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.posted_entry_ids, row.reopened_at is not None) == ("pending", [], True)


def _definition(db, definition_id) -> ScheduleDefinition:
    db.expire_all()
    return db.get(ScheduleDefinition, definition_id)


def test_loan_posts_of_neighbouring_periods_queue_without_deadlock(pg_engine, db_session, seed, period):
    # Loan post of k locks k+1… before the loan; a post of k+1 waits on the definition (FOR UPDATE) instead of
    # holding k+1 while it waits for the loan.
    second = seed.instance(period["definition"], 2, date(2026, 12, 9))
    third = seed.instance(period["definition"], 3, date(2027, 1, 9))
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, second.id, actor="owner"),
        lambda db: posting.post_instance(db, third.id, actor="owner"),
    )
    assert outcome == "committed"
    assert [_row(db_session, row.id).status for row in (second, third)] == ["posted", "posted"]


def test_two_deletes_reviving_an_ended_definition_queue(pg_engine, db_session, period):
    # Both deletes may revive the definition, so both take it FOR UPDATE; two FOR SHARE upgrades would deadlock.
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: ews.delete_entry(db, period["repayment"]),
    )
    assert outcome == "committed"
    assert _row(db_session, period["instance"].id).status == "pending"
    assert _definition(db_session, period["definition"].id).status == "active"


def test_loan_close_out_waits_for_a_delete_that_revives_the_definition(pg_engine, db_session, seed, period):
    # A loan post that closes out (open amount 0) and an entry delete that revives an ended definition both write
    # the definition; each holds it FOR UPDATE, so one waits for the other.
    seed.entry(
        period["bank"], "-291667", kind="payable", counterparty_id=period["payable"].counterparty_id,
        settles_entry_id=period["payable"].id, is_settlement=True,
    )
    pending = seed.instance(period["definition"], 2, date(2026, 12, 9))
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: ews.delete_entry(db, period["interest"]),
        lambda db: posting.post_instance(db, pending.id, actor="owner"),
    )
    assert outcome == "committed"
    assert (_row(db_session, pending.id).status, _row(db_session, pending.id).note) == ("skipped", "貸款已結清")
    assert _definition(db_session, period["definition"].id).status == "ended"


def test_delete_after_a_loan_close_out_is_refused_with_a_retry_message(pg_engine, db_session, seed, period):
    # The reverse order: the close-out holds the definition FOR UPDATE and ends it; the delete read the definition
    # active, waits on FOR SHARE, then finds it ended and answers 409 with the owner's retry text (no lost revive).
    seed.entry(
        period["bank"], "-291667", kind="payable", counterparty_id=period["payable"].counterparty_id,
        settles_entry_id=period["payable"].id, is_settlement=True,
    )
    pending = seed.instance(period["definition"], 2, date(2026, 12, 9))
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: posting.post_instance(db, pending.id, actor="owner"),
        lambda db: ews.delete_entry(db, period["interest"]),
    )
    assert isinstance(outcome, ConflictError) and str(outcome) == "排程剛結束，請重試刪除"
    assert _definition(db_session, period["definition"].id).status == "ended"
    assert sorted(_row(db_session, period["instance"].id).posted_entry_ids) == sorted(
        [period["repayment"], period["interest"]]
    )

    ews.delete_entry(db_session, period["interest"])  # the retry takes the definition FOR UPDATE and revives it
    db_session.commit()
    row = _row(db_session, period["instance"].id)
    assert (row.status, row.is_partial, row.posted_entry_ids) == ("posted", True, [period["repayment"]])
    assert _definition(db_session, period["definition"].id).status == "active"


def test_loan_delete_waits_for_a_repost_on_an_ended_definition(pg_engine, db_session, period):
    # The repost locks the loan in the same statement as the period's entries, so a DELETE of the loan (allowed once
    # the definition ended) waits for it instead of holding the loan while the repost waits for the repayment.
    definition = db_session.get(ScheduleDefinition, period["definition"].id)
    definition.status = "ended"
    db_session.commit()
    outcome = race(
        pg_engine,
        lambda db: schedule_service.repost_instance(db, period["instance"].id, ["8000", "620"]),
        lambda db: ews.delete_entry(db, period["payable"].id),
    )
    assert outcome == "committed"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, period["payable"].id) is None
    row = _row(db_session, period["instance"].id)
    repayment = next(
        db_session.get(LedgerEntry, entry_id) for entry_id in row.posted_entry_ids
        if db_session.get(LedgerEntry, entry_id).kind == "payable"
    )
    assert (repayment.amount, repayment.settles_entry_id) == (Decimal("-8000"), None)
```

- [ ] 19.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests/integration/test_schedule_lock_order.py
```

Expected: `9 passed` (the order is implemented by Tasks 8–18; a failure here is a lock-order bug to fix in the path it names, not in the test).

- [ ] 19.3 Edit `services/accounting-service/README.md`: in the "MOZE backup import" section replace `and stores future-dated rows, periods and installments in `moze_schedule`.` with `and maps future-dated rows, periods and installments to schedule definitions and periods (see Schedules).`; append

````markdown

Schedules (週期 / 分期)
-----------------------

Definitions (`schedule_definition`) hold the rule and a template of lines; instances (`schedule_instance`) are the
periods. Posting writes a period through the ledger services in one transaction (`source = 'schedule'`); a failure
stays `pending` with `last_error`.

    GET    /schedules/definitions?status=&kind=          POST /schedules/definitions (optional `loan`)
    GET    /schedules/definitions/{id}                   PUT  /schedules/definitions/{id}    DELETE (only before anything posted)
    POST   /schedules/definitions/{id}/pause | /resume {"backlog": "skip"|"post"} | /end | /catch-up
    PUT    /schedules/definitions/{id}/mode {"posting_mode": "auto"|"confirm"}
    GET    /schedules/instances?from=&until=&status=&definition_id=&queue=true
    PUT    /schedules/instances/{id} {"due_date", "amounts"}
    POST   /schedules/instances/{id}/post | /skip | /reopen | /repost {"amounts"} | /accept-partial
    POST   /schedules/run-now

The daily job runs in-process (APScheduler) at 00:05 Asia/Taipei, about 10 s after startup, after every real backup
import through the API, and every 10 minutes after a run that found the job lock or an import busy. The importer CLI
(`python -m app.services.moze_backup_import_service`) has no scheduler: after a real import it runs the job itself,
once the import lock is released — generation always, posting only when `ACCOUNTING_SCHEDULER_ENABLED` is on (else it
prints the number of due but unposted periods on its `schedule_job:` stderr line). The job generates instances 13 months
ahead and posts due periods of `active`, `auto` definitions dated on or after the definition's `auto_post_from`
(never a reopened period), per definition in `seq` order: a period waits while an earlier one is due or failed. `ACCOUNTING_SCHEDULER_ENABLED=false` turns the in-process job off (tests do); the job lock
(`pg_try_advisory_lock(0x53434844)`) makes a second runner return `busy`.

    .venv/bin/python -m app.services.schedule_job --dry-run     # print what would be generated and posted
    .venv/bin/python -m app.services.schedule_job               # run now (same as POST /schedules/run-now)

Every schedule write shares the import advisory key; during a backup import (a few seconds) schedule writes answer
HTTP 409 `import_running`. An import waits up to 30 s for schedule writers to finish; one started during a long job
posting loop may still be refused (`import already running`) — run it again once the job is done. Before cutover (`ACCOUNTING_IMPORT_LOCKED` not `true`) imported definitions cannot be
edited or deleted and MOZE-booked periods cannot be reposted (409 `locked_until_cutover`); pause, resume, end, mode,
catch-up and per-period actions work. A re-import never duplicates a period HomeHub posted or skipped
(`schedules.past_records_already_posted` / `…_skipped` in the report), nor one the owner edited while it was pending
(`past_records_owner_pending`: MOZE's record is not imported, the period stays pending), and never touches local
definitions. Amount differences are reported per line (`past_records_amount_differs.lines`, `amount_differs`).
````

- [ ] 19.4 Run the whole backend suite once more.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings tests 2>&1 | tail -2
```

Expected: Task 18's count + 9, 0 failed.

- [ ] 19.5 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add services/accounting-service/tests/integration/test_schedule_lock_order.py services/accounting-service/README.md
git commit -m "test(accounting): schedule lock order under overlapping transactions; README

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 20. Frontend models, service, `schedule-math`, toast

**Model:** opus

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (schedule DTOs; `EntrySource`; `LedgerEntry.schedule`; `EntryDetail.loan_schedule`)
- Modify: `frontend/src/app/services/accounting.service.ts` (schedule calls)
- Create: `frontend/src/app/services/accounting-schedules.service.spec.ts`
- Create: `frontend/src/app/components/accounting/schedule-math.ts`, `schedule-math.spec.ts`
- Create: `frontend/src/app/components/accounting/accounting-toast.ts`, `accounting-toast.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts`, `accounting-layout.html` (host the toast)
- Modify: `frontend/src/app/components/accounting/testing/fixtures.ts` (`makeDefinition`, `makeInstance`)

**Interfaces:**
- Consumes: the API shapes of Tasks 9–15 (`DefinitionOut`, `DefinitionDetailOut`, `InstanceOut`, `CatchUpOut`, `RunReportOut`, `EntryScheduleOut`, `LoanScheduleOut`); `pad`, `isHandledKey` (`accounting-ui.ts`); `addDays`, `slashDate` (`dates.ts`); `currencyDecimals`, `formatMoney` (`format.ts`); `writeErrorMessage` (`http-errors.ts`).
- Produces: the model types and service methods of the frontend contract; `schedule-math.ts` exports `IMPORT_RUNNING_TOAST`, `WEEKDAY_LABELS`, `ScheduleRule`, `isImportRunning`, `addMonthsIso`, `occurrenceDate`, `nextOccurrences`, `intervalLabel`, `ruleDayLabel`, `recurringFooter`, `splitInstallment`, `installmentFooter`, `ruleSummary`, `scheduleProgress`, `schedulePill`; `accounting-toast.ts` exports `AccountingToastService` (`message`, `show(text, ms = 3000)`, `clear()`), `AccountingToastComponent` (`app-accounting-toast`), `scheduleActionError(err, toast) -> string | null`; fixtures `makeDefinition(overrides)`, `makeInstance(overrides)`.

Rules: every schedule write bumps `entriesChanged` (posting writes entries; the reminder centre and the bell reload on it). `ScheduleItem`, `ScheduleKind` and `getSchedules` stay until Task 27 removes them with their last user. Pure helpers never read the clock: callers pass `today`.

- [ ] 20.1 Write the failing test `frontend/src/app/components/accounting/schedule-math.spec.ts`:

```typescript
import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import {
  ScheduleRule,
  installmentFooter,
  intervalLabel,
  isImportRunning,
  nextOccurrences,
  recurringFooter,
  ruleSummary,
  scheduleProgress,
  schedulePill,
  splitInstallment,
} from './schedule-math';

const MONTHLY: ScheduleRule = { interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', day_of_month: null };

describe('schedule-math', () => {
  it('labels intervals the MOZE way', () => {
    expect(intervalLabel('month', 1)).toBe('每月');
    expect(intervalLabel('week', 2)).toBe('每 2 週');
    expect(intervalLabel('day', 1)).toBe('每天');
    expect(intervalLabel('year', 1)).toBe('每年');
  });

  it('writes the 週期 footer', () => {
    expect(recurringFooter(MONTHLY, null)).toBe('週期：#1 / 無限期（每月 / 22號）');
    expect(recurringFooter(MONTHLY, 12)).toBe('週期：#1 / 12（每月 / 22號）');
    expect(recurringFooter({ ...MONTHLY, interval_unit: 'week', anchor_date: '2026-10-05' }, null)).toBe(
      '週期：#1 / 無限期（每週 / 星期一）',
    );
    expect(recurringFooter({ ...MONTHLY, interval_unit: 'day', interval_n: 3 }, 5)).toBe('週期：#1 / 5（每 3 天）');
  });

  it('splits an installment with the remainder on the last period', () => {
    expect(splitInstallment(10000, 3, 'TWD')).toEqual({ perPeriod: 3333, last: 3334 });
    expect(splitInstallment(300000, 36, 'TWD')).toEqual({ perPeriod: 8333, last: 8345 });
    expect(splitInstallment(100, 3, 'USD')).toEqual({ perPeriod: 33.33, last: 33.34 });
  });

  it('writes the 分期 footer', () => {
    expect(installmentFooter(10000, 3, '2026-11-03', 'TWD')).toBe('分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）');
  });

  it('summarises a rule and its progress', () => {
    const loan = { interval_unit: 'month' as const, interval_n: 1, anchor_date: '2026-11-09', day_of_month: 9, times: 36 };
    expect(ruleSummary(loan)).toBe('每月 9 號 · 36 期 · 自 2026/11/09');
    expect(ruleSummary({ ...loan, interval_unit: 'week', anchor_date: '2026-10-05', day_of_month: null, times: null })).toBe(
      '每週 星期一 · 無限期 · 自 2026/10/05',
    );
    expect(scheduleProgress({ ...loan, posted_count: 3 })).toBe('已入帳 3 / 36');
    expect(scheduleProgress({ ...loan, times: null, posted_count: 3 })).toBe('每月');
    expect(scheduleProgress({ ...loan, interval_unit: 'week', interval_n: 2, times: null, posted_count: 0 })).toBe('每 2 週');
  });

  it('names schedule pills', () => {
    expect(schedulePill({ kind: 'installment', seq: 5, times: 36 })).toBe('分期 #5/36');
    expect(schedulePill({ kind: 'recurring', seq: 25, times: null })).toBe('週期 #25');
    expect(schedulePill({ kind: 'recurring', seq: 3, times: 12 })).toBe('週期 #3/12');
  });

  it('lists occurrences from the anchor, clamping month ends', () => {
    expect(nextOccurrences({ ...MONTHLY, anchor_date: '2026-01-31' }, 3)).toEqual(['2026-01-31', '2026-02-28', '2026-03-31']);
    expect(nextOccurrences(MONTHLY, 2, '2026-10-22')).toEqual(['2026-11-22', '2026-12-22']);
    expect(nextOccurrences({ ...MONTHLY, interval_unit: 'week' }, 2, '2026-10-23')).toEqual(['2026-10-29', '2026-11-05']);
  });

  it('recognises the import_running refusal', () => {
    const conflict = (message: string) =>
      new HttpErrorResponse({ status: 409, error: { code: 409, message, trace_id: 't' } });
    expect(isImportRunning(conflict('import_running'))).toBe(true);
    expect(isImportRunning(conflict('locked_until_cutover'))).toBe(false);
    expect(isImportRunning(new HttpErrorResponse({ status: 500 }))).toBe(false);
    expect(isImportRunning(new Error('import_running'))).toBe(false);
  });
});
```

- [ ] 20.2 Write the failing test `frontend/src/app/components/accounting/accounting-toast.spec.ts`:

```typescript
import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingToastComponent, AccountingToastService, scheduleActionError } from './accounting-toast';

describe('AccountingToast', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows a message and clears it after the delay', () => {
    TestBed.configureTestingModule({ imports: [AccountingToastComponent] });
    const fixture = TestBed.createComponent(AccountingToastComponent);
    const toast = TestBed.inject(AccountingToastService);
    toast.show('匯入進行中，請稍後再試');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[role="status"]')?.textContent?.trim()).toBe('匯入進行中，請稍後再試');
    vi.advanceTimersByTime(3000);
    fixture.detectChanges();
    expect(el.querySelector('[role="status"]')).toBeNull();
  });

  it('turns import_running into the toast and anything else into an error line', () => {
    const toast = TestBed.inject(AccountingToastService);
    const running = new HttpErrorResponse({ status: 409, error: { message: 'import_running' } });
    expect(scheduleActionError(running, toast)).toBeNull();
    expect(toast.message()).toBe('匯入進行中，請稍後再試');
    const other = new HttpErrorResponse({ status: 409, error: { message: '只有進行中的排程可以暫停（目前 paused）' } });
    expect(scheduleActionError(other, toast)).toBe('只有進行中的排程可以暫停（目前 paused）');
  });
});
```

- [ ] 20.3 Write the failing test `frontend/src/app/services/accounting-schedules.service.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ScheduleDefinitionInput } from '../models/accounting.model';
import { makeDefinition, makeInstance } from '../components/accounting/testing/fixtures';
import { AccountingService } from './accounting.service';

const INPUT: ScheduleDefinitionInput = {
  kind: 'recurring',
  name: 'Netflix',
  template: {
    lines: [
      {
        kind: 'expense', account_id: 2, to_account_id: null, to_amount: null, counterparty_id: null, category_id: 41,
        project_id: null, amount: '390', currency: 'TWD', loan_entry_id: null, name: null, merchant: null,
      },
    ],
    description: null,
    tags: [],
  },
  interval_unit: 'month',
  interval_n: 1,
  anchor_date: '2026-10-22',
  day_of_month: null,
  times: null,
  end_date: null,
  total_amount: null,
  posting_mode: 'auto',
  loan: null,
};

describe('AccountingService schedules', () => {
  let service: AccountingService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(AccountingService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists definitions with filters and reads one', () => {
    service.getScheduleDefinitions({ status: 'active', kind: 'installment' }).subscribe();
    const list = http.expectOne(r => r.url === '/api/accounting/schedules/definitions');
    expect(list.request.params.get('status')).toBe('active');
    expect(list.request.params.get('kind')).toBe('installment');
    list.flush([makeDefinition()]);
    service.getScheduleDefinition(5).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/5').flush({ ...makeDefinition({ id: 5 }), instances: [] });
  });

  it('creates and updates a definition and bumps entriesChanged', () => {
    const before = service.entriesChanged();
    service.createScheduleDefinition(INPUT).subscribe();
    const create = http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    expect(create.request.body).toEqual(INPUT);
    create.flush(makeDefinition({ id: 9 }));
    const { kind: _kind, loan: _loan, ...update } = INPUT;
    service.updateScheduleDefinition(9, update).subscribe();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/definitions/9');
    expect(put.request.body).not.toHaveProperty('kind');
    put.flush(makeDefinition({ id: 9 }));
    expect(service.entriesChanged()).toBe(before + 2);
  });

  it('sends the state actions', () => {
    service.pauseSchedule(3).subscribe();
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/3/pause').flush(makeDefinition());
    service.resumeSchedule(3, 'post').subscribe();
    const resume = http.expectOne('/api/accounting/schedules/definitions/3/resume');
    expect(resume.request.body).toEqual({ backlog: 'post' });
    resume.flush(makeDefinition());
    service.setScheduleMode(3, 'confirm').subscribe();
    const mode = http.expectOne('/api/accounting/schedules/definitions/3/mode');
    expect([mode.request.method, mode.request.body]).toEqual(['PUT', { posting_mode: 'confirm' }]);
    mode.flush(makeDefinition());
    service.endSchedule(3).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/3/end').flush(makeDefinition());
    service.catchUpSchedule(3).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/3/catch-up').flush({ posted: [], failed: null, definition: makeDefinition() });
    service.deleteScheduleDefinition(3).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/schedules/definitions/3').flush(null);
  });

  it('reads the queue and sends the instance actions', () => {
    service.getScheduleInstances({ queue: true, until: '2026-11-02' }).subscribe();
    const queue = http.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(queue.request.params.get('until')).toBe('2026-11-02');
    expect(queue.request.params.get('queue')).toBe('true');
    queue.flush([makeInstance()]);
    for (const [call, path] of [
      [() => service.postScheduleInstance(7), 'post'],
      [() => service.skipScheduleInstance(7), 'skip'],
      [() => service.reopenScheduleInstance(7), 'reopen'],
      [() => service.acceptPartialScheduleInstance(7), 'accept-partial'],
    ] as const) {
      call().subscribe();
      http.expectOne(r => r.method === 'POST' && r.url === `/api/accounting/schedules/instances/7/${path}`).flush(makeInstance());
    }
    service.repostScheduleInstance(7, ['8333', '598']).subscribe();
    const repost = http.expectOne('/api/accounting/schedules/instances/7/repost');
    expect(repost.request.body).toEqual({ amounts: ['8333', '598'] });
    repost.flush(makeInstance());
    service.updateScheduleInstance(7, { due_date: '2026-11-12' }).subscribe();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/instances/7');
    expect(put.request.body).toEqual({ due_date: '2026-11-12' });
    put.flush(makeInstance());
  });

  it('runs the job now', () => {
    service.runSchedulesNow().subscribe();
    http
      .expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/run-now')
      .flush({ trigger: 'manual', today: '2026-10-03', status: 'completed', generated: 0, posted: [], failed: [], stopped_definitions: [] });
  });
});
```

- [ ] 20.4 Run the three.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/schedule-math.spec.ts' --include='src/app/components/accounting/accounting-toast.spec.ts' --include='src/app/services/accounting-schedules.service.spec.ts'
```

Expected: build fails with `TS2307: Cannot find module './schedule-math'` (and the toast / fixtures names).

- [ ] 20.5 Edit `frontend/src/app/models/accounting.model.ts`:
  - replace `export type EntrySource = 'moze_import' | 'moze_backup' | 'manual' | 'hermes' | 'rule';` with `export type EntrySource = 'moze_import' | 'moze_backup' | 'manual' | 'hermes' | 'rule' | 'schedule';`;
  - add as the last member of `LedgerEntry`:

```typescript
  /** `EntryOut.schedule`: the posted schedule period that wrote this entry (週期 #k/N, 分期 #k/N pill); optional for older literals. */
  schedule?: EntryScheduleLink | null;
```

  - add as the last member of `EntryDetail`:

```typescript
  /** `EntryDetailOut.loan_schedule`: a payable / receivable original that a schedule repays (剩餘 · 已還 · 下期). */
  loan_schedule?: LoanSchedule | null;
```

  - append at the end of the file:

```typescript
// ---- schedules (週期 / 分期 / 待完成交易) -----------------------------------------------------------------------

export type ScheduleDefinitionKind = 'recurring' | 'installment';
export type ScheduleIntervalUnit = 'day' | 'week' | 'month' | 'year';
export type SchedulePostingMode = 'auto' | 'confirm';
export type ScheduleStatus = 'active' | 'paused' | 'ended';
export type ScheduleInstanceStatus = 'pending' | 'posted' | 'skipped';
export type ScheduleActor = 'auto' | 'owner' | 'import';
export type ScheduleLineKind =
  | 'expense'
  | 'income'
  | 'receivable'
  | 'payable'
  | 'transfer'
  | 'repayment'
  | 'collection'
  | 'interest';

/** One template line; amounts are unsigned decimal strings, the server applies the sign. */
export interface ScheduleLineInput {
  kind: ScheduleLineKind;
  account_id: number;
  to_account_id: number | null;
  to_amount: string | null;
  counterparty_id: number | null;
  category_id: number | null;
  project_id: number | null;
  amount: string;
  currency: string;
  loan_entry_id: number | null;
  name: string | null;
  merchant: string | null;
}

export interface ScheduleLine extends ScheduleLineInput {
  account_name: string | null;
  to_account_name: string | null;
  category: string | null;
  counterparty: string | null;
}

export interface ScheduleTemplateInput {
  lines: ScheduleLineInput[];
  description: string | null;
  tags: string[];
}

export interface ScheduleTemplate {
  lines: ScheduleLine[];
  description: string | null;
  tags: string[];
}

export interface CurrencyAmount {
  currency: string;
  amount: string;
}

export interface ScheduleFailing {
  instance_id: number;
  due_date: string;
  last_error: string;
}

/** `GET /schedules/definitions` row (`DefinitionOut`). */
export interface ScheduleDefinition {
  id: number;
  kind: ScheduleDefinitionKind;
  name: string;
  status: ScheduleStatus;
  posting_mode: SchedulePostingMode;
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
  first_seq: number;
  times: number | null;
  end_date: string | null;
  total_amount: string | null;
  auto_post_from: string;
  template: ScheduleTemplate;
  created_locally: boolean;
  imported: boolean;
  /** Imported and before cutover: 編輯整個排程 and 刪除 answer 409 locked_until_cutover. */
  locked: boolean;
  review_reason: string | null;
  generated_until: string | null;
  posted_count: number;
  skipped_count: number;
  pending_count: number;
  next_due_date: string | null;
  next_amount: CurrencyAmount[];
  /** Loans: open amount signed like the loan (payable negative); card installments: what is left of the total. */
  remaining: string | null;
  repaid: string | null;
  loan_entry_id: number | null;
  needs_check: boolean;
  failing: ScheduleFailing | null;
  category_icon: string | null;
  category_color: string | null;
}

export interface ScheduleInstanceLine {
  kind: ScheduleLineKind;
  account_id: number;
  account_name: string | null;
  to_account_id: number | null;
  to_account_name: string | null;
  category: string | null;
  counterparty: string | null;
  /** Signed: money leaving the paying account is negative. */
  amount: string;
  currency: string;
}

/** `GET /schedules/instances` row (`InstanceOut`). */
export interface ScheduleInstance {
  id: number;
  definition_id: number;
  definition_name: string;
  kind: ScheduleDefinitionKind;
  posting_mode: SchedulePostingMode;
  seq: number;
  times: number | null;
  due_date: string;
  rule_date: string;
  status: ScheduleInstanceStatus;
  is_partial: boolean;
  overdue_days: number;
  lines: ScheduleInstanceLine[];
  totals: CurrencyAmount[];
  /** Unsigned amounts aligned with the template lines ("0" = not written), for repost bodies. */
  amounts: string[];
  last_error: string | null;
  reopened: boolean;
  edited_by_owner: boolean;
  note: string | null;
  posted_entry_ids: number[];
  acted_at: string | null;
  acted_by: ScheduleActor | null;
  category_icon: string | null;
  category_color: string | null;
}

export interface ScheduleDefinitionDetail extends ScheduleDefinition {
  instances: ScheduleInstance[];
}

export interface ScheduleLoanInput {
  account_id: number;
  counterparty_id: number;
  category_id: number | null;
  amount: string;
  entry_date: string;
  name: string | null;
}

export interface ScheduleDefinitionInput {
  kind: ScheduleDefinitionKind;
  name: string;
  template: ScheduleTemplateInput;
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
  times: number | null;
  end_date: string | null;
  total_amount: string | null;
  posting_mode: SchedulePostingMode;
  loan: ScheduleLoanInput | null;
}

/** `PUT /schedules/definitions/{id}`: the create fields except `kind` and `loan`. */
export type ScheduleDefinitionUpdate = Omit<ScheduleDefinitionInput, 'kind' | 'loan'>;

export interface ScheduleInstanceQuery {
  from?: string;
  until?: string;
  status?: ScheduleInstanceStatus | 'all';
  definition_id?: number;
  queue?: boolean;
}

export interface ScheduleCatchUpResult {
  posted: number[];
  failed: { instance_id: number; error: string } | null;
  definition: ScheduleDefinition;
}

export interface ScheduleRunReport {
  trigger: string;
  today: string;
  status: string;
  generated: number;
  posted: number[];
  failed: number[];
  stopped_definitions: number[];
}

/** `EntryOut.schedule`. */
export interface EntryScheduleLink {
  definition_id: number;
  instance_id: number;
  kind: ScheduleDefinitionKind;
  seq: number;
  times: number | null;
  name: string;
  is_partial: boolean;
  acted_by: ScheduleActor | null;
  posted_entry_ids: number[];
}

/** `EntryDetailOut.loan_schedule`. */
export interface LoanSchedule {
  definition_id: number;
  name: string;
  status: ScheduleStatus;
  posting_mode: SchedulePostingMode;
  posted_count: number;
  times: number | null;
  next_due_date: string | null;
  next_amount: CurrencyAmount[];
  remaining: string | null;
  repaid: string | null;
  needs_check: boolean;
}

/** One differing line of a period (Multica R-F5): HomeHub's amount against MOZE's record; owner-facing, never logged. */
export interface ScheduleAmountDiffer {
  definition_id: number;
  name: string;
  seq: number;
  date: string;
  line: number;
  kind: ScheduleLineKind | 'transfer_in';
  amount: string;
  moze_amount: string;
}

/** `summary.schedules` of a backup import. */
export interface ScheduleImportReport {
  definitions: Record<'recurring' | 'installment' | 'single', { created: number; updated: number; ended: number; deleted: number }>;
  instances: Record<string, number>;
  records_mapped: number;
  rewards_ignored: number;
  unsupported_types: Record<string, number>;
  past_records_already_posted: number;
  past_records_already_skipped: number;
  past_records_amount_differs: { count: number; instance_ids: number[]; lines: ScheduleAmountDiffer[] };
  /** R-F1: owner-edited pending periods whose MOZE record turned past (the record was not imported). */
  past_records_owner_pending: { definition_id: number; seq: number; date: string }[];
  relinked: { templates: number; settlements: number };
  review: { moze_id: string; reason: string }[];
  /** R-A3: pending periods whose kept amounts (owner price, owner edit) differ from MOZE's, per line. */
  amount_differs: ScheduleAmountDiffer[];
  loan_remainder_check: { definition: string; moze_remainder: string; open_amount: string; difference: string }[];
}
```

- [ ] 20.6 Edit `frontend/src/app/services/accounting.service.ts`: add to the model import list exactly `ScheduleCatchUpResult, ScheduleDefinition, ScheduleDefinitionDetail, ScheduleDefinitionInput, ScheduleDefinitionKind, ScheduleDefinitionUpdate, ScheduleInstance, ScheduleInstanceQuery, SchedulePostingMode, ScheduleRunReport, ScheduleStatus,` and insert before `// ---- imports ----`:

```typescript
  // ---- schedules (週期 / 分期 / 待完成交易) ------------------------------------------------------------------
  // Every write bumps entriesChanged: posting writes entries, and the reminder centre and the 🔔 reload on it.

  getScheduleDefinitions(query: { status?: ScheduleStatus; kind?: ScheduleDefinitionKind } = {}): Observable<ScheduleDefinition[]> {
    return this.http.get<ScheduleDefinition[]>(`${this.apiUrl}/schedules/definitions`, { params: toParams(query) });
  }

  getScheduleDefinition(id: number): Observable<ScheduleDefinitionDetail> {
    return this.http.get<ScheduleDefinitionDetail>(`${this.apiUrl}/schedules/definitions/${id}`);
  }

  createScheduleDefinition(input: ScheduleDefinitionInput): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions`, input));
  }

  updateScheduleDefinition(id: number, input: ScheduleDefinitionUpdate): Observable<ScheduleDefinition> {
    return this.bump(this.http.put<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}`, input));
  }

  deleteScheduleDefinition(id: number): Observable<void> {
    return this.bump(this.http.delete<void>(`${this.apiUrl}/schedules/definitions/${id}`));
  }

  pauseSchedule(id: number): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/pause`, {}));
  }

  resumeSchedule(id: number, backlog: 'skip' | 'post' = 'skip'): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/resume`, { backlog }));
  }

  endSchedule(id: number): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/end`, {}));
  }

  setScheduleMode(id: number, mode: SchedulePostingMode): Observable<ScheduleDefinition> {
    return this.bump(this.http.put<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/mode`, { posting_mode: mode }));
  }

  catchUpSchedule(id: number): Observable<ScheduleCatchUpResult> {
    return this.bump(this.http.post<ScheduleCatchUpResult>(`${this.apiUrl}/schedules/definitions/${id}/catch-up`, {}));
  }

  getScheduleInstances(query: ScheduleInstanceQuery = {}): Observable<ScheduleInstance[]> {
    return this.http.get<ScheduleInstance[]>(`${this.apiUrl}/schedules/instances`, { params: toParams(query) });
  }

  updateScheduleInstance(id: number, body: { due_date?: string; amounts?: string[] }): Observable<ScheduleInstance> {
    return this.bump(this.http.put<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}`, body));
  }

  postScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/post`, {}));
  }

  skipScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/skip`, {}));
  }

  reopenScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/reopen`, {}));
  }

  repostScheduleInstance(id: number, amounts: string[]): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/repost`, { amounts }));
  }

  acceptPartialScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/accept-partial`, {}));
  }

  runSchedulesNow(): Observable<ScheduleRunReport> {
    return this.bump(this.http.post<ScheduleRunReport>(`${this.apiUrl}/schedules/run-now`, {}));
  }

```

- [ ] 20.7 Create `frontend/src/app/components/accounting/schedule-math.ts`:

```typescript
import { HttpErrorResponse } from '@angular/common/http';

import { EntryScheduleLink, ScheduleDefinition, ScheduleIntervalUnit } from '../../models/accounting.model';
import { pad } from './accounting-ui';
import { addDays, slashDate } from './dates';
import { currencyDecimals, formatMoney } from './format';

/** Pure schedule helpers (design D43). The server stays the source of truth for instances; these only preview. */

export const IMPORT_RUNNING_TOAST = '匯入進行中，請稍後再試';
export const WEEKDAY_LABELS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
const UNIT_WORDS: Record<ScheduleIntervalUnit, string> = { day: '天', week: '週', month: '月', year: '年' };

export interface ScheduleRule {
  interval_unit: ScheduleIntervalUnit;
  interval_n: number;
  anchor_date: string;
  day_of_month: number | null;
}

/** A 409 whose body names `import_running` (a backup import holds the advisory lock for a few seconds). */
export function isImportRunning(err: unknown): boolean {
  if (!(err instanceof HttpErrorResponse) || err.status !== 409) {
    return false;
  }
  const body = err.error as { message?: unknown; detail?: unknown } | null;
  return body?.message === 'import_running' || body?.detail === 'import_running';
}

function parts(iso: string): [number, number, number] {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  return [year, month, day];
}

function lastDay(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** `iso` moved by whole months, on `dayOfMonth` (default: its own day), clamped to the month's length. */
export function addMonthsIso(iso: string, months: number, dayOfMonth: number | null = null): string {
  const [year, month, day] = parts(iso);
  const index = year * 12 + (month - 1) + months;
  const nextYear = Math.floor(index / 12);
  const nextMonth = (index % 12) + 1;
  return `${nextYear}-${pad(nextMonth)}-${pad(Math.min(dayOfMonth ?? day, lastDay(nextYear, nextMonth)))}`;
}

/** Occurrence k of a rule, always computed from the anchor (a 31st clamps in February and returns in March). */
export function occurrenceDate(rule: ScheduleRule, k: number): string {
  const step = k * rule.interval_n;
  const anchorDay = parts(rule.anchor_date)[2];
  switch (rule.interval_unit) {
    case 'day':
      return addDays(rule.anchor_date, step);
    case 'week':
      return addDays(rule.anchor_date, 7 * step);
    case 'month':
      return addMonthsIso(rule.anchor_date, step, rule.day_of_month ?? anchorDay);
    default:
      return addMonthsIso(rule.anchor_date, 12 * step, rule.day_of_month ?? anchorDay);
  }
}

/** The first `count` occurrences strictly after `after` (from occurrence 0 when omitted). */
export function nextOccurrences(rule: ScheduleRule, count: number, after?: string): string[] {
  const dates: string[] = [];
  for (let k = 0; dates.length < count && k < 10000; k++) {
    const date = occurrenceDate(rule, k);
    if (after === undefined || date > after) {
      dates.push(date);
    }
  }
  return dates;
}

/** `每月`, `每 2 週`, `每天`, `每年`. */
export function intervalLabel(unit: ScheduleIntervalUnit, n: number): string {
  return n === 1 ? `每${UNIT_WORDS[unit]}` : `每 ${n} ${UNIT_WORDS[unit]}`;
}

/** The day part of a rule: `22號` (monthly), `星期一` (weekly), `10月22號` (yearly), '' (daily). */
export function ruleDayLabel(rule: ScheduleRule): string {
  const [year, month, day] = parts(rule.anchor_date);
  switch (rule.interval_unit) {
    case 'month':
      return `${rule.day_of_month ?? day}號`;
    case 'week':
      return WEEKDAY_LABELS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
    case 'year':
      return `${month}月${rule.day_of_month ?? day}號`;
    default:
      return '';
  }
}

/** MOZE's 週期 footer: `週期：#1 / 無限期（每月 / 22號）`, `週期：#1 / 12（每月 / 22號）`. */
export function recurringFooter(rule: ScheduleRule, times: number | null): string {
  const label = intervalLabel(rule.interval_unit, rule.interval_n);
  const day = ruleDayLabel(rule);
  return `週期：#1 / ${times ?? '無限期'}（${day ? `${label} / ${day}` : label}）`;
}

/** floor(total ÷ times) in the currency's decimals; the remainder goes to the last period (分期餘額納入 末期). */
export function splitInstallment(total: number, times: number, currency: string): { perPeriod: number; last: number } {
  const factor = 10 ** currencyDecimals(currency);
  const perPeriod = Math.floor((total * factor) / times + 1e-9) / factor;
  const last = Math.round((total - perPeriod * (times - 1)) * factor) / factor;
  return { perPeriod, last };
}

/** MOZE's 分期 footer: `分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）`. */
export function installmentFooter(total: number, times: number, firstDate: string, currency: string): string {
  return `分期：#1 / ${times}（${formatMoney(total, currency)}） 首次還款日將從 ${slashDate(firstDate)} 開始進行（${times} 期）`;
}

type RuleWithTimes = ScheduleRule & Pick<ScheduleDefinition, 'times'>;

/** Manage-sheet summary: `每月 9 號 · 36 期 · 自 2026/11/09`. */
export function ruleSummary(rule: RuleWithTimes): string {
  const label = intervalLabel(rule.interval_unit, rule.interval_n);
  const [, month, day] = parts(rule.anchor_date);
  const when =
    rule.interval_unit === 'month'
      ? `${label} ${rule.day_of_month ?? day} 號`
      : rule.interval_unit === 'week'
        ? `${label} ${ruleDayLabel(rule)}`
        : rule.interval_unit === 'year'
          ? `${label} ${month} 月 ${rule.day_of_month ?? day} 號`
          : label;
  return `${when} · ${rule.times === null ? '無限期' : `${rule.times} 期`} · 自 ${slashDate(rule.anchor_date)}`;
}

/** `已入帳 3 / 36`, or the interval (`每月`, `每 2 週`) for an unlimited schedule. */
export function scheduleProgress(rule: RuleWithTimes & Pick<ScheduleDefinition, 'posted_count'>): string {
  return rule.times === null
    ? intervalLabel(rule.interval_unit, rule.interval_n)
    : `已入帳 ${rule.posted_count} / ${rule.times}`;
}

/** `分期 #5/36`, `週期 #25` (unlimited), in MOZE's wording. */
export function schedulePill(link: Pick<EntryScheduleLink, 'kind' | 'seq' | 'times'>): string {
  return `${link.kind === 'installment' ? '分期' : '週期'} #${link.seq}${link.times === null ? '' : `/${link.times}`}`;
}
```

- [ ] 20.8 Create `frontend/src/app/components/accounting/accounting-toast.ts`:

```typescript
import { ChangeDetectionStrategy, Component, DestroyRef, Injectable, inject, signal } from '@angular/core';

import { writeErrorMessage } from './http-errors';
import { IMPORT_RUNNING_TOAST, isImportRunning } from './schedule-math';

/** One short message at the bottom of the accounting pages (`匯入進行中，請稍後再試`, D43). */
@Injectable({ providedIn: 'root' })
export class AccountingToastService {
  private readonly text = signal<string | null>(null);
  private timer: ReturnType<typeof setTimeout> | null = null;

  readonly message = this.text.asReadonly();

  show(text: string, ms = 3000): void {
    this.clear();
    this.text.set(text);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.text.set(null);
    }, ms);
  }

  clear(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.text.set(null);
  }
}

/** A failed schedule action: `import_running` shows the toast (page state unchanged) → null; else the error line. */
export function scheduleActionError(err: unknown, toast: AccountingToastService): string | null {
  if (isImportRunning(err)) {
    toast.show(IMPORT_RUNNING_TOAST);
    return null;
  }
  return writeErrorMessage(err);
}

@Component({
  selector: 'app-accounting-toast',
  standalone: true,
  template: `
    @if (toast.message(); as text) {
      <p class="accounting-toast" role="status" aria-live="polite">{{ text }}</p>
    }
  `,
  styles: [
    `
      .accounting-toast {
        background: var(--app-text);
        border-radius: var(--radius-pill);
        bottom: calc(80px + env(safe-area-inset-bottom, 0px));
        color: var(--app-surface);
        font-size: var(--fs-sm, 0.85rem);
        font-weight: 700;
        left: 50%;
        margin: 0;
        max-width: calc(100vw - 32px);
        padding: 8px 16px;
        position: fixed;
        transform: translateX(-50%);
        z-index: 1200;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingToastComponent {
  readonly toast = inject(AccountingToastService);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.toast.clear());
  }
}
```

- [ ] 20.9 Host the toast: in `frontend/src/app/components/accounting/accounting-layout/accounting-layout.ts` add `import { AccountingToastComponent } from '../accounting-toast';` and add `AccountingToastComponent` to the component's `imports` array; append `<app-accounting-toast />` as the last line of `accounting-layout.html`.

- [ ] 20.10 Append to `frontend/src/app/components/accounting/testing/fixtures.ts` (and add `ScheduleDefinition, ScheduleInstance` to its model import):

```typescript
export function makeDefinition(overrides: Partial<ScheduleDefinition> = {}): ScheduleDefinition {
  return {
    id: 1,
    kind: 'recurring',
    name: 'Netflix',
    status: 'active',
    posting_mode: 'auto',
    interval_unit: 'month',
    interval_n: 1,
    anchor_date: '2026-10-22',
    day_of_month: null,
    first_seq: 1,
    times: null,
    end_date: null,
    total_amount: null,
    auto_post_from: '2026-10-03',
    template: {
      lines: [
        {
          kind: 'expense', account_id: 2, to_account_id: null, to_amount: null, counterparty_id: null, category_id: 41,
          project_id: null, amount: '390', currency: 'TWD', loan_entry_id: null, name: null, merchant: null,
          account_name: '範例卡', to_account_name: null, category: '娛樂/Netflix', counterparty: null,
        },
      ],
      description: null,
      tags: [],
    },
    created_locally: true,
    imported: false,
    locked: false,
    review_reason: null,
    generated_until: '2027-11-03',
    posted_count: 0,
    skipped_count: 0,
    pending_count: 13,
    next_due_date: '2026-10-22',
    next_amount: [{ currency: 'TWD', amount: '-390.0000' }],
    remaining: null,
    repaid: null,
    loan_entry_id: null,
    needs_check: false,
    failing: null,
    category_icon: '🎬',
    category_color: '#c9b8f0',
    ...overrides,
  };
}

export function makeInstance(overrides: Partial<ScheduleInstance> = {}): ScheduleInstance {
  return {
    id: 1,
    definition_id: 1,
    definition_name: 'Netflix',
    kind: 'recurring',
    posting_mode: 'confirm',
    seq: 1,
    times: null,
    due_date: '2026-10-22',
    rule_date: '2026-10-22',
    status: 'pending',
    is_partial: false,
    overdue_days: 0,
    lines: [
      {
        kind: 'expense', account_id: 2, account_name: '範例卡', to_account_id: null, to_account_name: null,
        category: '娛樂/Netflix', counterparty: null, amount: '-390.0000', currency: 'TWD',
      },
    ],
    totals: [{ currency: 'TWD', amount: '-390.0000' }],
    amounts: ['390'],
    last_error: null,
    reopened: false,
    edited_by_owner: false,
    note: null,
    posted_entry_ids: [],
    acted_at: null,
    acted_by: null,
    category_icon: '🎬',
    category_color: '#c9b8f0',
    ...overrides,
  };
}
```

- [ ] 20.11 Run 20.4 again.

Expected: `15 passed` (8 + 2 + 5).

- [ ] 20.12 Run the whole frontend suite (the layout now hosts the toast).

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false 2>&1 | tail -4
```

Expected: `B_front + 15` passed, no failed files.

- [ ] 20.13 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/models/accounting.model.ts frontend/src/app/services/accounting.service.ts \
  frontend/src/app/services/accounting-schedules.service.spec.ts frontend/src/app/components/accounting/schedule-math.ts \
  frontend/src/app/components/accounting/schedule-math.spec.ts frontend/src/app/components/accounting/accounting-toast.ts \
  frontend/src/app/components/accounting/accounting-toast.spec.ts frontend/src/app/components/accounting/accounting-layout \
  frontend/src/app/components/accounting/testing/fixtures.ts
git commit -m "feat(frontend): schedule models, service calls, schedule maths and the accounting toast

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 21. `app-schedule-tabs`

**Model:** opus

**Files:**
- Create: `frontend/src/app/components/accounting/schedule-tabs/schedule-draft.ts`
- Create: `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.ts`, `schedule-tabs.html`, `schedule-tabs.scss`, `schedule-tabs.spec.ts`

**Interfaces:**
- Consumes: `FormKind` (`entry-form/entry-draft.ts`); `addMonthsIso`, `splitInstallment`, `recurringFooter`, `installmentFooter`, `ScheduleRule` (Task 20); `formatMoney`, `formatNumber`; `LedgerAccount`, `ScheduleIntervalUnit`, `SchedulePostingMode`.
- Produces: `schedule-draft.ts` exports `ScheduleTab`, `ScheduleEndMode`, `ScheduleDraft`, `SCHEDULE_TABS`, `tabsFor(kind)`, `defaultDraft(entryDate)`; `ScheduleTabsComponent` (`app-schedule-tabs`) with inputs `kind` (required), `entryDate` (required), `amount`, `currency`, `accounts`, `accountId`, `disabled`, `definitionMode`, `errors`, the model `draft` (required) and `<ng-content>` as the 單次 panel.

```typescript
export interface ScheduleDraft {
  tab: ScheduleTab;                 // 'single' | 'recurring' | 'installment'
  unit: ScheduleIntervalUnit; every: number; start: string; startTouched: boolean; dayOfMonth: number | null;
  endMode: ScheduleEndMode; endTimes: number; endDate: string; mode: SchedulePostingMode;
  periods: number; firstDate: string; firstTouched: boolean; perPeriod: string; interest: string; repayAccountId: number | null;
}
```

Rules (spec "Entry form schedule tabs", D43): tabs 單次 / 週期 / 分期, 單次 by default; 週期 for 支出, 收入, 轉帳, 應收款項, 應付款項; 分期 for 支出 and 應付款項 only; 系統 offers 單次 only; in definition mode 單次 is not offered; a tab the record type stops offering falls back to the first offered one. 週期: `每 N {天|週|月|年}` (N ≥ 1, default 每 1 月), 起始日 (follows the entry date until edited), 結束 {無限期 | N 次 | 日期} (default 無限期), 入帳方式 {自動入帳 | 提醒入帳} (default 自動入帳). 分期: 總額 (the amount tile), 期數 (≥ 2), 首次還款日 (one month after the entry date until edited), 每期金額 (auto `splitInstallment(...).perPeriod`, editable), 利息 (optional), 入帳方式, 還款帳戶 (應付款項 only; default the entry's account). Footers in MOZE's wording. A server field error (`errors[field]`) shows beside its field: `interval_n` / `interval_unit` → 區間, `anchor_date` → 起始日 / 首次還款日, `times` → 結束 / 期數, `end_date` → 結束, `total_amount` → 總額, `lines[0].amount` → 每期金額, `lines[1].amount` → 利息. Every control is a native button / input / select (keyboard reachable; the entry form's ⏎ handling already skips selects and buttons and IME commits), tone tokens only, rows wrap at 390 px.

- [ ] 21.1 Write the failing test `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts`:

```typescript
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import { ScheduleDraft, defaultDraft } from './schedule-draft';
import { ScheduleTabsComponent } from './schedule-tabs';

const ACCOUNTS = [makeAccount({ id: 1, name: '薪轉' }), makeAccount({ id: 2, name: '範例卡' })];

interface Setup {
  kind?: string;
  entryDate?: string;
  amount?: number | null;
  accountId?: number | null;
  errors?: Record<string, string>;
  definitionMode?: boolean;
  draft?: ScheduleDraft;
}

function render(setup: Setup = {}): { fixture: ComponentFixture<ScheduleTabsComponent>; el: HTMLElement } {
  TestBed.configureTestingModule({ imports: [ScheduleTabsComponent] });
  const fixture = TestBed.createComponent(ScheduleTabsComponent);
  const ref = fixture.componentRef;
  ref.setInput('kind', setup.kind ?? 'expense');
  ref.setInput('entryDate', setup.entryDate ?? '2026-10-22');
  ref.setInput('amount', setup.amount ?? null);
  ref.setInput('currency', 'TWD');
  ref.setInput('accounts', ACCOUNTS);
  ref.setInput('accountId', setup.accountId ?? 1);
  ref.setInput('errors', setup.errors ?? {});
  ref.setInput('definitionMode', setup.definitionMode ?? false);
  ref.setInput('draft', setup.draft ?? defaultDraft(setup.entryDate ?? '2026-10-22'));
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function tab(fixture: ComponentFixture<ScheduleTabsComponent>, label: string): void {
  const el = fixture.nativeElement as HTMLElement;
  Array.from(el.querySelectorAll<HTMLButtonElement>('.schedule-tab')).find(button => text(button) === label)!.click();
  fixture.detectChanges();
}

function set(fixture: ComponentFixture<ScheduleTabsComponent>, selector: string, value: string, event = 'input'): void {
  const field = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
  field.value = value;
  field.dispatchEvent(new Event(event));
  fixture.detectChanges();
}

function labels(el: HTMLElement): string[] {
  return Array.from(el.querySelectorAll('.schedule-tab')).map(text);
}

describe('ScheduleTabsComponent', () => {
  it('offers the tabs each record type allows', () => {
    const { fixture, el } = render();
    expect(labels(el)).toEqual(['單次', '週期', '分期']);
    expect(el.querySelector('.schedule-tab.on')?.textContent?.trim()).toBe('單次');
    tab(fixture, '分期');
    fixture.componentRef.setInput('kind', 'transfer');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次', '週期']);
    expect(fixture.componentInstance.draft().tab).toBe('single');
    fixture.componentRef.setInput('kind', 'income');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次', '週期']);
    fixture.componentRef.setInput('kind', 'system');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次']);
  });

  it('drops 單次 in definition mode', () => {
    // A separate test: render() configures TestBed, which cannot be configured again once a component exists.
    const { el } = render({ definitionMode: true, draft: { ...defaultDraft('2026-10-22'), tab: 'recurring' } });
    expect(labels(el)).toEqual(['週期', '分期']);
  });

  it('starts 週期 at 每 1 月 from the entry date, 無限期, 自動入帳, with the MOZE footer', () => {
    const { fixture, el } = render();
    tab(fixture, '週期');
    expect((el.querySelector('.sched-every') as HTMLInputElement).value).toBe('1');
    expect((el.querySelector('.sched-unit') as HTMLSelectElement).value).toBe('month');
    expect((el.querySelector('.sched-start') as HTMLInputElement).value).toBe('2026-10-22');
    expect((el.querySelector('.sched-end') as HTMLSelectElement).value).toBe('never');
    expect(el.querySelector('.sched-mode[data-mode="auto"]')?.getAttribute('aria-checked')).toBe('true');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 無限期（每月 / 22號）');
  });

  it('counts a finite run in the footer and switches to 提醒入帳', () => {
    const { fixture, el } = render();
    tab(fixture, '週期');
    set(fixture, '.sched-end', 'times', 'change');
    expect((el.querySelector('.sched-end-times') as HTMLInputElement).value).toBe('12');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 12（每月 / 22號）');
    (el.querySelector('.sched-mode[data-mode="confirm"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().mode).toBe('confirm');
    set(fixture, '.sched-every', '2');
    set(fixture, '.sched-unit', 'week', 'change');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 12（每 2 週 / 星期四）');
  });

  it('splits a card installment and words the 分期 footer', () => {
    // Spec "Card installment split".
    const { fixture, el } = render({ entryDate: '2026-10-03', amount: 10000 });
    tab(fixture, '分期');
    set(fixture, '.sched-periods', '3');
    expect((el.querySelector('.sched-per') as HTMLInputElement).value).toBe('3,333');
    expect(text(el.querySelector('.sched-total'))).toBe('$10,000');
    expect(text(el.querySelector('.schedule-footer'))).toBe('分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）');
    expect(el.querySelector('.sched-repay')).toBeNull();
  });

  it('offers 還款帳戶 for 應付款項, defaulting to the entry account', () => {
    const { fixture, el } = render({ kind: 'payable', amount: 300000 });
    tab(fixture, '分期');
    const repay = el.querySelector('.sched-repay') as HTMLSelectElement;
    expect(repay.value).toBe('1');
    set(fixture, '.sched-repay', '2', 'change');
    expect(fixture.componentInstance.draft().repayAccountId).toBe(2);
  });

  it('moves the start and the first repayment with the entry date until they are edited', () => {
    const { fixture } = render({ entryDate: '2026-10-03' });
    tab(fixture, '分期');
    fixture.componentRef.setInput('entryDate', '2026-10-10');
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().firstDate).toBe('2026-11-10');
    expect(fixture.componentInstance.draft().start).toBe('2026-10-10');
    set(fixture, '.sched-first', '2026-11-09', 'change');
    fixture.componentRef.setInput('entryDate', '2026-10-12');
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().firstDate).toBe('2026-11-09');
  });

  it('shows a server field error beside its field', () => {
    const { fixture, el } = render({ errors: { times: '分期至少 2 期', 'lines[1].amount': '金額格式錯誤' } });
    tab(fixture, '分期');
    expect(text(el.querySelector('.field-error[data-field="times"]'))).toBe('分期至少 2 期');
    expect(text(el.querySelector('.field-error[data-field="interest"]'))).toBe('金額格式錯誤');
  });
});
```

- [ ] 21.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/schedule-tabs/schedule-tabs.spec.ts'
```

Expected: build fails with `TS2307: Cannot find module './schedule-draft'`.

- [ ] 21.3 Create `frontend/src/app/components/accounting/schedule-tabs/schedule-draft.ts`:

```typescript
import { ScheduleIntervalUnit, SchedulePostingMode } from '../../../models/accounting.model';
import { FormKind } from '../entry-form/entry-draft';
import { addMonthsIso } from '../schedule-math';

/** 進階 tabs of the entry form (MOZE 進階設定). */
export type ScheduleTab = 'single' | 'recurring' | 'installment';
export type ScheduleEndMode = 'never' | 'times' | 'date';

/** The form state of the 週期 / 分期 tabs; the entry form turns it into a definition (schedule-save.ts). */
export interface ScheduleDraft {
  tab: ScheduleTab;
  unit: ScheduleIntervalUnit;
  every: number;
  /** 起始日 (週期); follows the entry date until the owner edits it. */
  start: string;
  startTouched: boolean;
  /** Kept from an edited definition (an imported 31st); the form itself never sets it. */
  dayOfMonth: number | null;
  endMode: ScheduleEndMode;
  endTimes: number;
  endDate: string;
  mode: SchedulePostingMode;
  /** 期數 (分期). */
  periods: number;
  /** 首次還款日 (分期); one month after the entry date until edited. */
  firstDate: string;
  firstTouched: boolean;
  /** 每期金額 as typed; '' = the automatic floor(總額 ÷ 期數). */
  perPeriod: string;
  /** 利息 per period as typed; '' = none. */
  interest: string;
  /** 還款帳戶 (應付款項); null = the entry's account. */
  repayAccountId: number | null;
}

export const SCHEDULE_TABS: readonly { tab: ScheduleTab; label: string }[] = [
  { tab: 'single', label: '單次' },
  { tab: 'recurring', label: '週期' },
  { tab: 'installment', label: '分期' },
];

/** 分期 only for 支出 and 應付款項; 轉帳, 收入 and 應收款項 get 週期; 系統 (餘額調整) stays 單次. */
export function tabsFor(kind: FormKind): ScheduleTab[] {
  if (kind === 'system') {
    return ['single'];
  }
  return kind === 'expense' || kind === 'payable' ? ['single', 'recurring', 'installment'] : ['single', 'recurring'];
}

export function defaultDraft(entryDate: string): ScheduleDraft {
  return {
    tab: 'single',
    unit: 'month',
    every: 1,
    start: entryDate,
    startTouched: false,
    dayOfMonth: null,
    endMode: 'never',
    endTimes: 12,
    endDate: addMonthsIso(entryDate, 12),
    mode: 'auto',
    periods: 12,
    firstDate: addMonthsIso(entryDate, 1),
    firstTouched: false,
    perPeriod: '',
    interest: '',
    repayAccountId: null,
  };
}
```

- [ ] 21.4 Create `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.ts`:

```typescript
import { ChangeDetectionStrategy, Component, computed, effect, input, model, untracked } from '@angular/core';

import { LedgerAccount, ScheduleIntervalUnit, SchedulePostingMode } from '../../../models/accounting.model';
import { accountLabel } from '../accounting-ui';
import { FormKind } from '../entry-form/entry-draft';
import { formatMoney, formatNumber } from '../format';
import { ScheduleRule, addMonthsIso, installmentFooter, recurringFooter, splitInstallment } from '../schedule-math';
import { SCHEDULE_TABS, ScheduleDraft, ScheduleEndMode, ScheduleTab, tabsFor } from './schedule-draft';

const UNITS: readonly { unit: ScheduleIntervalUnit; label: string }[] = [
  { unit: 'day', label: '天' },
  { unit: 'week', label: '週' },
  { unit: 'month', label: '月' },
  { unit: 'year', label: '年' },
];

/** 進階 of the entry form: 單次 (projected content: 入帳日) / 週期 / 分期 (spec "Entry form schedule tabs"). */
@Component({
  selector: 'app-schedule-tabs',
  standalone: true,
  templateUrl: './schedule-tabs.html',
  styleUrl: './schedule-tabs.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ScheduleTabsComponent {
  readonly kind = input.required<FormKind>();
  readonly entryDate = input.required<string>();
  /** The amount tile (unsigned, account currency): 分期's 總額. */
  readonly amount = input<number | null>(null);
  readonly currency = input('TWD');
  readonly accounts = input<LedgerAccount[]>([]);
  /** The entry's account: 還款帳戶's default. */
  readonly accountId = input<number | null>(null);
  readonly disabled = input(false);
  /** 編輯整個排程: the definition's own tab only (no 單次). */
  readonly definitionMode = input(false);
  /** Server field errors (`fieldErrors()`), shown beside the field they name. */
  readonly errors = input<Record<string, string>>({});
  readonly draft = model.required<ScheduleDraft>();

  readonly units = UNITS;
  readonly accountLabel = accountLabel;
  readonly tabs = computed(() => {
    const allowed = tabsFor(this.kind());
    return SCHEDULE_TABS.filter(option => allowed.includes(option.tab) && !(this.definitionMode() && option.tab === 'single'));
  });
  readonly rule = computed<ScheduleRule>(() => {
    const draft = this.draft();
    return { interval_unit: draft.unit, interval_n: draft.every, anchor_date: draft.start, day_of_month: draft.dayOfMonth };
  });
  readonly split = computed(() => {
    const amount = this.amount();
    const periods = this.draft().periods;
    return amount !== null && amount > 0 && periods >= 2 ? splitInstallment(amount, periods, this.currency()) : null;
  });
  readonly perPeriodText = computed(() => {
    const typed = this.draft().perPeriod;
    const split = this.split();
    return typed || (split ? formatNumber(split.perPeriod, this.currency()) : '');
  });
  readonly totalText = computed(() => {
    const amount = this.amount();
    return amount !== null && amount > 0 ? formatMoney(amount, this.currency()) : '—';
  });
  readonly repayOptions = computed(() =>
    this.accounts().filter(account => !account.is_archived || account.id === this.draft().repayAccountId),
  );
  readonly repayValue = computed(() => this.draft().repayAccountId ?? this.accountId());
  readonly footer = computed(() => {
    const draft = this.draft();
    if (draft.tab === 'recurring') {
      return recurringFooter(this.rule(), draft.endMode === 'times' ? draft.endTimes : null);
    }
    if (draft.tab === 'installment') {
      const amount = this.amount();
      return amount !== null && amount > 0
        ? installmentFooter(amount, draft.periods, draft.firstDate, this.currency())
        : '輸入總額後顯示每期金額';
    }
    return '';
  });

  constructor() {
    // 起始日 and 首次還款日 follow the entry's 日期 until the owner edits them.
    effect(() => {
      const date = this.entryDate();
      untracked(() => {
        const draft = this.draft();
        const start = draft.startTouched ? draft.start : date;
        const firstDate = draft.firstTouched ? draft.firstDate : addMonthsIso(date, 1);
        if (start !== draft.start || firstDate !== draft.firstDate) {
          this.draft.set({ ...draft, start, firstDate });
        }
      });
    });
    // A tab the record type no longer offers (分期 after switching to 轉帳) falls back to the first offered one.
    effect(() => {
      const offered = this.tabs().map(option => option.tab);
      untracked(() => {
        const draft = this.draft();
        if (offered.length > 0 && !offered.includes(draft.tab)) {
          this.draft.set({ ...draft, tab: offered[0] });
        }
      });
    });
  }

  private patch(changes: Partial<ScheduleDraft>): void {
    this.draft.update(draft => ({ ...draft, ...changes }));
  }

  error(...fields: string[]): string | null {
    const errors = this.errors();
    for (const field of fields) {
      if (errors[field]) {
        return errors[field];
      }
    }
    return null;
  }

  selectTab(tab: ScheduleTab): void {
    if (!this.disabled()) {
      this.patch({ tab });
    }
  }

  setEvery(value: string): void {
    this.patch({ every: Math.max(1, Math.floor(Number(value)) || 1) });
  }

  setUnit(value: string): void {
    this.patch({ unit: value as ScheduleIntervalUnit });
  }

  setStart(value: string): void {
    if (value) {
      this.patch({ start: value, startTouched: true });
    }
  }

  setEndMode(value: string): void {
    this.patch({ endMode: value as ScheduleEndMode });
  }

  setEndTimes(value: string): void {
    this.patch({ endTimes: Math.max(1, Math.floor(Number(value)) || 1) });
  }

  setEndDate(value: string): void {
    if (value) {
      this.patch({ endDate: value });
    }
  }

  setMode(mode: SchedulePostingMode): void {
    this.patch({ mode });
  }

  setPeriods(value: string): void {
    this.patch({ periods: Math.max(2, Math.floor(Number(value)) || 2) });
  }

  setFirst(value: string): void {
    if (value) {
      this.patch({ firstDate: value, firstTouched: true });
    }
  }

  setPerPeriod(value: string): void {
    this.patch({ perPeriod: value.trim() });
  }

  setInterest(value: string): void {
    this.patch({ interest: value.trim() });
  }

  setRepay(value: string): void {
    this.patch({ repayAccountId: value ? Number(value) : null });
  }
}
```

- [ ] 21.5 Create `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.html`:

```html
<div class="schedule-tabs">
  <div class="sched-tablist" role="tablist" aria-label="進階設定">
    @for (option of tabs(); track option.tab) {
      <button
        type="button"
        role="tab"
        class="schedule-tab"
        [class.on]="draft().tab === option.tab"
        [attr.aria-selected]="draft().tab === option.tab"
        [disabled]="disabled()"
        (click)="selectTab(option.tab)"
      >{{ option.label }}</button>
    }
  </div>

  @switch (draft().tab) {
    @case ('single') {
      <ng-content />
    }
    @case ('recurring') {
      <div class="sched-grid">
        <div class="sched-row">
          <span class="sched-label">區間</span>
          <span class="sched-inline">
            每
            <input class="sched-every" type="number" min="1" inputmode="numeric" aria-label="間隔" [value]="draft().every" [disabled]="disabled()" (input)="setEvery($any($event.target).value)" />
            <select class="sched-unit" aria-label="單位" [disabled]="disabled()" (change)="setUnit($any($event.target).value)">
              @for (unit of units; track unit.unit) {
                <option [value]="unit.unit" [selected]="draft().unit === unit.unit">{{ unit.label }}</option>
              }
            </select>
          </span>
          @if (error('interval_n', 'interval_unit'); as message) {
            <small class="field-error" data-field="interval">{{ message }}</small>
          }
        </div>
        <label class="sched-row">
          <span class="sched-label">起始日</span>
          <input class="sched-start" type="date" [value]="draft().start" [disabled]="disabled()" (change)="setStart($any($event.target).value)" />
          @if (error('anchor_date'); as message) {
            <small class="field-error" data-field="anchor_date">{{ message }}</small>
          }
        </label>
        <div class="sched-row">
          <span class="sched-label">結束</span>
          <span class="sched-inline">
            <select class="sched-end" aria-label="結束" [disabled]="disabled()" (change)="setEndMode($any($event.target).value)">
              <option value="never" [selected]="draft().endMode === 'never'">無限期</option>
              <option value="times" [selected]="draft().endMode === 'times'">N 次</option>
              <option value="date" [selected]="draft().endMode === 'date'">日期</option>
            </select>
            @if (draft().endMode === 'times') {
              <input class="sched-end-times" type="number" min="1" inputmode="numeric" aria-label="次數" [value]="draft().endTimes" [disabled]="disabled()" (input)="setEndTimes($any($event.target).value)" />
              <span>次</span>
            }
            @if (draft().endMode === 'date') {
              <input class="sched-end-date" type="date" aria-label="結束日" [value]="draft().endDate" [disabled]="disabled()" (change)="setEndDate($any($event.target).value)" />
            }
          </span>
          @if (error('times', 'end_date'); as message) {
            <small class="field-error" data-field="end">{{ message }}</small>
          }
        </div>
        <div class="sched-row">
          <span class="sched-label">入帳方式</span>
          <span class="sched-modes" role="radiogroup" aria-label="入帳方式">
            <button type="button" role="radio" class="sched-mode" data-mode="auto" [attr.aria-checked]="draft().mode === 'auto'" [disabled]="disabled()" (click)="setMode('auto')">自動入帳</button>
            <button type="button" role="radio" class="sched-mode" data-mode="confirm" [attr.aria-checked]="draft().mode === 'confirm'" [disabled]="disabled()" (click)="setMode('confirm')">提醒入帳</button>
          </span>
        </div>
      </div>
    }
    @case ('installment') {
      <div class="sched-grid">
        <div class="sched-row">
          <span class="sched-label">總額</span>
          <b class="sched-total">{{ totalText() }}</b>
          @if (error('total_amount', 'amount'); as message) {
            <small class="field-error" data-field="total_amount">{{ message }}</small>
          }
        </div>
        <label class="sched-row">
          <span class="sched-label">期數</span>
          <input class="sched-periods" type="number" min="2" inputmode="numeric" [value]="draft().periods" [disabled]="disabled()" (input)="setPeriods($any($event.target).value)" />
          @if (error('times'); as message) {
            <small class="field-error" data-field="times">{{ message }}</small>
          }
        </label>
        <label class="sched-row">
          <span class="sched-label">首次還款日</span>
          <input class="sched-first" type="date" [value]="draft().firstDate" [disabled]="disabled()" (change)="setFirst($any($event.target).value)" />
          @if (error('anchor_date'); as message) {
            <small class="field-error" data-field="anchor_date">{{ message }}</small>
          }
        </label>
        <label class="sched-row">
          <span class="sched-label">每期金額</span>
          <input class="sched-per" inputmode="decimal" [value]="perPeriodText()" [disabled]="disabled()" (input)="setPerPeriod($any($event.target).value)" />
          @if (error('lines[0].amount'); as message) {
            <small class="field-error" data-field="per_period">{{ message }}</small>
          }
        </label>
        <label class="sched-row">
          <span class="sched-label">利息</span>
          <input class="sched-interest" inputmode="decimal" placeholder="0" [value]="draft().interest" [disabled]="disabled()" (input)="setInterest($any($event.target).value)" />
          @if (error('lines[1].amount'); as message) {
            <small class="field-error" data-field="interest">{{ message }}</small>
          }
        </label>
        @if (kind() === 'payable') {
          <label class="sched-row">
            <span class="sched-label">還款帳戶</span>
            <select class="sched-repay" [disabled]="disabled()" (change)="setRepay($any($event.target).value)">
              @for (account of repayOptions(); track account.id) {
                <option [value]="account.id" [selected]="account.id === repayValue()">{{ accountLabel(account) }}</option>
              }
            </select>
          </label>
        }
        <div class="sched-row">
          <span class="sched-label">入帳方式</span>
          <span class="sched-modes" role="radiogroup" aria-label="入帳方式">
            <button type="button" role="radio" class="sched-mode" data-mode="auto" [attr.aria-checked]="draft().mode === 'auto'" [disabled]="disabled()" (click)="setMode('auto')">自動入帳</button>
            <button type="button" role="radio" class="sched-mode" data-mode="confirm" [attr.aria-checked]="draft().mode === 'confirm'" [disabled]="disabled()" (click)="setMode('confirm')">提醒入帳</button>
          </span>
        </div>
      </div>
    }
  }

  @if (footer()) {
    <p class="schedule-footer">{{ footer() }}</p>
  }
</div>
```

- [ ] 21.6 Create `frontend/src/app/components/accounting/schedule-tabs/schedule-tabs.scss`:

```scss
:host {
  display: block;
}

.sched-tablist {
  background: var(--app-surface-soft);
  border-radius: var(--radius-pill);
  display: inline-flex;
  gap: 2px;
  margin: 6px 0;
  max-width: 100%;
  padding: 2px;
}

.schedule-tab {
  background: none;
  border: 0;
  border-radius: var(--radius-pill);
  color: var(--app-text-muted);
  cursor: pointer;
  font: inherit;
  font-size: var(--fs-label, 0.75rem);
  font-weight: 700;
  padding: 3px 12px;

  &.on {
    background: var(--app-surface);
    color: var(--app-text);
  }

  &:focus-visible {
    outline: 2px solid var(--app-primary);
    outline-offset: 1px;
  }
}

.sched-grid {
  display: flex;
  flex-direction: column;
}

.sched-row {
  align-items: center;
  border-bottom: 1px solid var(--app-border);
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  justify-content: space-between;
  min-width: 0;
  padding: 6px 0;
}

.sched-label {
  color: var(--app-text);
  font-weight: 600;
}

.sched-inline {
  align-items: center;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.sched-every,
.sched-end-times,
.sched-periods {
  width: 4.5em;
}

.sched-per,
.sched-interest {
  max-width: 9em;
  text-align: right;
}

.sched-modes {
  display: inline-flex;
  gap: 4px;
}

.sched-mode {
  background: var(--app-surface-soft);
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-text-muted);
  cursor: pointer;
  font: inherit;
  font-size: var(--fs-label, 0.75rem);
  padding: 2px 10px;

  &[aria-checked='true'] {
    background: var(--app-surface);
    border-color: var(--app-primary);
    color: var(--app-text);
    font-weight: 700;
  }

  &:focus-visible {
    outline: 2px solid var(--app-primary);
    outline-offset: 1px;
  }
}

.field-error {
  color: var(--app-danger);
  flex-basis: 100%;
  font-size: 0.72rem;
}

.schedule-footer {
  color: var(--app-text-muted);
  font-size: 0.75rem;
  margin: 6px 0 0;
  overflow-wrap: anywhere;
}
```

- [ ] 21.7 Run 21.2 again.

Expected: `8 passed`.

- [ ] 21.8 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/schedule-tabs
git commit -m "feat(frontend): 進階 單次 / 週期 / 分期 tabs with MOZE footers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 22. Entry form: save as a definition, hidden fields, definition mode

**Model:** opus

**Files:**
- Create: `frontend/src/app/components/accounting/entry-form/schedule-save.ts`, `schedule-save.spec.ts`
- Modify: `frontend/src/app/components/accounting/entry-form/entry-form.ts`, `entry-form.html`
- Modify: `frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts`, `transfer-panel.html` (`scheduled` input)
- Test: `frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`

**Interfaces:**
- Consumes: Task 20 (service methods, `isImportRunning`, `IMPORT_RUNNING_TOAST`, `splitInstallment`, `AccountingToastService`, `makeDefinition`); Task 21 (`ScheduleTabsComponent`, `ScheduleDraft`, `defaultDraft`); `fieldErrors`, `writeErrorMessage` (`http-errors.ts`); `parseAmountText`, `amountString`; `resolveCounterpartyId`, `transferCommonFrom`, `TransferEdit`; `TransferPanelComponent.buildInput`, `from()`, `to()`.
- Produces: `schedule-save.ts` exports `ScheduleFormError` (`field`, `message`), `ScheduleFormValues`, `ScheduleFormState`, `buildDefinitionInput(values) -> ScheduleDefinitionInput`, `definitionUpdateFrom(input) -> ScheduleDefinitionUpdate`, `shouldCatchUp(input, today) -> boolean`, `draftFromDefinition(definition) -> ScheduleFormState`, `transferEditFromLine(line) -> TransferEdit`; entry form signals `scheduleDraft`, `scheduleId`, `definitionLocked`, `fieldErrors`, `createdScheduleId` (R-F2), computed `scheduling`, the private `runCatchUp(definitionId, keepGoing)` and the exported copy `CATCH_UP_RETRY_TEXT`, `CATCH_UP_FAILED_TOAST` (`entry-form.ts`); definition mode at `/accounting/entry?schedule=<id>`; `TransferPanelComponent.scheduled` input.

Rules (spec "Entry form schedule tabs", "Scheduled entry edit scope" for 編輯整個排程, D43):
- With 週期 or 分期 selected the form calls `POST /schedules/definitions` instead of the entry endpoint: one template line from the form (kind, account, category, project, counterparty, amount, name, merchant; for 轉帳 the to-account and, across currencies, the in-amount); 分期 on 支出 → one `expense` line of 每期金額 (+ an `interest` line with 利息) and `total_amount` = 總額; 分期 on 應付款項 → a `repayment` line from 還款帳戶 (+ `interest`), and `loan` = the payable's account, counterparty, category, 總額, 日期 and name. The definition name is the entry's 名稱, else the category name, else the kind's label. After a create whose `posting_mode` is `auto` and whose `anchor_date` is today, `catch-up` is called; never otherwise.
- ★Create, then catch-up (Multica R-F2): creation is complete when the create response arrives. The form keeps `created.id` in `createdScheduleId` and from then on never calls create again: a catch-up that errors (409 `import_running` → the toast as well, a network error, any other failure) leaves the form open, disabled, with the error line `排程已建立，入帳未完成；按 ✓ 重試入帳`, and ✓ retries **only** `catch-up` for that id (leaving the page is fine too: the definition exists, its period waits in 待完成交易). A catch-up answered HTTP 200 with `failed` set (`CatchUpOut.failed`, the period's error recorded as `last_error`) is surfaced — the toast `排程已建立；這一期入帳失敗，請到待完成交易處理` — and the form finishes; the result is never discarded.
- On 週期 / 分期 the currency (FX) pill, the split "+" (split lines), the fee / discount "+" and chips, the reward chips and the invoice fields are hidden and `排程不支援` is shown (the entry form has no photo tile in phase 2a; nothing else to hide); a form that still holds an FX conversion is refused with `排程不支援外幣，請先移除匯率`, and while it holds one the currency pill stays a button on 週期 / 分期 too, so the owner can open the FX sheet and remove the rate without going back to 單次.
- Errors: a `ScheduleFormError` names its field; a 422 fills `fieldErrors` (shown beside the field by `app-schedule-tabs`) and the error line; a 409 `import_running` shows the toast and changes nothing else. `fieldErrors()` keys by the last string of `loc`, so a Pydantic request error on `template.lines.1.amount` arrives as `amount` (shown in the error line, not in a 每期金額 / 利息 slot); only the service-level `ValidationError("lines[i].amount")` reaches those slots. Accepted: the form validates amounts before sending, so a Pydantic amount error means a client bug.
- 連續記帳 resets the tab to 單次 for the next record.
- Definition mode (`?schedule=<id>`): `GET /schedules/definitions/{id}` (request-id guarded), the form pre-filled by `draftFromDefinition`, the record-type tabs locked to the definition's, 單次 not offered, title `編輯排程`, save with `PUT /schedules/definitions/{id}` (no `kind`, no `loan`), and for an imported definition before cutover (`locked`) the lock banner, a disabled form and ✓.

- [ ] 22.1 Write the failing unit test `frontend/src/app/components/accounting/entry-form/schedule-save.spec.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import { makeAccount, makeDefinition } from '../testing/fixtures';
import { defaultDraft } from '../schedule-tabs/schedule-draft';
import {
  ScheduleFormError,
  ScheduleFormValues,
  buildDefinitionInput,
  definitionUpdateFrom,
  draftFromDefinition,
  shouldCatchUp,
} from './schedule-save';

const PAY = makeAccount({ id: 1, name: '薪轉' });
const CARD = makeAccount({ id: 2, name: '範例卡' });
const BROKER = makeAccount({ id: 3, name: '交割' });
const YEN = makeAccount({ id: 4, name: '日幣', currency: 'JPY' });

function values(overrides: Partial<ScheduleFormValues> = {}): ScheduleFormValues {
  return {
    kind: 'expense', draft: { ...defaultDraft('2026-10-03'), tab: 'recurring', start: '2026-10-22' }, name: '', merchant: '',
    description: '', tags: [], projectId: null, categoryId: 41, categoryName: 'Netflix', account: CARD, amount: 390,
    counterpartyId: null, transfer: null, transferFrom: null, transferTo: null, repayAccount: null, entryDate: '2026-10-03',
    loanEntryId: null, ...overrides,
  };
}

describe('schedule-save', () => {
  it('builds a monthly expense definition named after the category', () => {
    const input = buildDefinitionInput(values());
    expect(input).toMatchObject({
      kind: 'recurring', name: 'Netflix', interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', times: null,
      end_date: null, total_amount: null, posting_mode: 'auto', loan: null,
    });
    expect(input.template.lines).toEqual([
      {
        kind: 'expense', account_id: 2, to_account_id: null, to_amount: null, counterparty_id: null, category_id: 41,
        project_id: null, amount: '390', currency: 'TWD', loan_entry_id: null, name: null, merchant: null,
      },
    ]);
    const finite = buildDefinitionInput(values({ draft: { ...values().draft, endMode: 'times', endTimes: 12 } }));
    expect(finite.times).toBe(12);
  });

  it('builds a transfer line, with the in-amount only across currencies', () => {
    const transfer = {
      from_account_id: 1, to_account_id: 3, out_amount: '15000', in_amount: '15000', entry_date: '2026-10-03', entry_time: null,
      posted_date: null, category_id: 60, name: null, merchant: null, description: null, project_id: null, tags: [],
      out_fee: null, out_discount: null, in_fee: null, in_discount: null, reward_rule_ids: [],
    };
    const same = buildDefinitionInput(values({ kind: 'transfer', transfer, transferFrom: PAY, transferTo: BROKER }));
    expect(same.template.lines[0]).toMatchObject({ kind: 'transfer', account_id: 1, to_account_id: 3, amount: '15000', to_amount: null, category_id: 60 });
    const across = buildDefinitionInput(
      values({ kind: 'transfer', transfer: { ...transfer, to_account_id: 4, in_amount: '70000' }, transferFrom: PAY, transferTo: YEN }),
    );
    expect(across.template.lines[0]).toMatchObject({ to_account_id: 4, to_amount: '70000' });
  });

  it('builds a card installment with the total', () => {
    const input = buildDefinitionInput(
      values({ amount: 10000, draft: { ...defaultDraft('2026-10-03'), tab: 'installment', periods: 3 } }),
    );
    expect(input).toMatchObject({ kind: 'installment', anchor_date: '2026-11-03', times: 3, total_amount: '10000', interval_unit: 'month' });
    expect(input.template.lines.map(line => [line.kind, line.amount])).toEqual([['expense', '3333']]);
  });

  it('builds a new loan with its interest in one input', () => {
    // Spec "New loan with interest".
    const input = buildDefinitionInput(
      values({
        kind: 'payable', name: '信貸', account: PAY, amount: 300000, categoryId: 50, counterpartyId: 7, repayAccount: PAY,
        draft: { ...defaultDraft('2026-10-03'), tab: 'installment', periods: 36, firstDate: '2026-11-09', interest: '620' },
      }),
    );
    expect(input).toMatchObject({ kind: 'installment', name: '信貸', times: 36, total_amount: '300000', anchor_date: '2026-11-09' });
    expect(input.template.lines.map(line => [line.kind, line.account_id, line.amount, line.loan_entry_id])).toEqual([
      ['repayment', 1, '8333', null], ['interest', 1, '620', null],
    ]);
    expect(input.loan).toEqual({ account_id: 1, counterparty_id: 7, category_id: 50, amount: '300000', entry_date: '2026-10-03', name: '信貸' });
  });

  it('names the field of a form problem', () => {
    const tryBuild = (overrides: Partial<ScheduleFormValues>) => {
      try {
        buildDefinitionInput(values(overrides));
        return null;
      } catch (error) {
        return error instanceof ScheduleFormError ? error.field : 'other';
      }
    };
    expect(tryBuild({ kind: 'income', draft: { ...defaultDraft('2026-10-03'), tab: 'installment' } })).toBe('kind');
    expect(tryBuild({ amount: null })).toBe('amount');
    expect(tryBuild({ draft: { ...defaultDraft('2026-10-03'), tab: 'installment', perPeriod: 'abc' }, amount: 900 })).toBe('lines[0].amount');
  });

  it('prefills the form from a loan definition', () => {
    const loan = makeDefinition({
      kind: 'installment', name: '信貸 每月還款', anchor_date: '2026-11-09', times: 36, total_amount: '300000.0000',
      template: {
        lines: [
          { ...makeDefinition().template.lines[0], kind: 'repayment', account_id: 1, amount: '8333', loan_entry_id: 4021, category_id: 50 },
          { ...makeDefinition().template.lines[0], kind: 'interest', account_id: 1, amount: '620', category_id: null },
        ],
        description: null,
        tags: [],
      },
    });
    const state = draftFromDefinition(loan);
    expect([state.kind, state.amount, state.accountId, state.loanEntryId, state.name]).toEqual(['payable', '300000', 1, 4021, '信貸 每月還款']);
    expect(state.draft).toMatchObject({ tab: 'installment', periods: 36, firstDate: '2026-11-09', perPeriod: '8333', interest: '620', repayAccountId: 1 });
  });

  it('decides the catch-up and strips kind and loan for an update', () => {
    const input = buildDefinitionInput(values({ draft: { ...values().draft, start: '2026-10-03' } }));
    expect(shouldCatchUp(input, '2026-10-03')).toBe(true);
    expect(shouldCatchUp({ ...input, posting_mode: 'confirm' }, '2026-10-03')).toBe(false);
    expect(shouldCatchUp(buildDefinitionInput(values()), '2026-10-03')).toBe(false);
    const update = definitionUpdateFrom(input);
    expect(update).not.toHaveProperty('kind');
    expect(update).not.toHaveProperty('loan');
  });
});
```

- [ ] 22.2 Write the failing component test `frontend/src/app/components/accounting/entry-form/entry-form-schedule.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, Routes, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { MockInstance, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Counterparty, Project } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingToastService } from '../accounting-toast';
import { FxValue } from '../fx-sheet/fx-sheet';
import { makeAccount, makeAccountDetail, makeCategory, makeDefinition, makePreference } from '../testing/fixtures';
import { EntryFormComponent } from './entry-form';

@Component({ template: '' })
class AccountingStubComponent {}

const ROUTES: Routes = [
  { path: 'accounting', component: AccountingStubComponent },
  { path: 'accounting/entry', component: EntryFormComponent },
];

const ACCOUNTS = [
  makeAccount({ id: 1, name: '薪轉' }),
  makeAccount({ id: 2, name: '範例卡', is_credit: true }),
  makeAccount({ id: 3, name: '交割' }),
];
const PROJECTS: Project[] = [];
const NETFLIX = makeCategory({ id: 41, parent_id: 40, name: 'Netflix' });
const STREAMING = makeCategory({ id: 40, name: '娛樂', icon: '🎬', children: [NETFLIX] });
const LOANS = makeCategory({ id: 50, kind: 'payable', name: '借款' });
const MOVE = makeCategory({ id: 60, kind: 'transfer_out', name: '轉帳' });
const LENDER: Counterparty = { id: 7, name: '範例銀行', open_amounts: [] };

describe('EntryFormComponent schedules', () => {
  let httpMock: HttpTestingController;
  let harness: RouterTestingHarness;
  let navigate: MockInstance<Router['navigateByUrl']> | undefined;

  beforeEach(() => {
    navigate = undefined;
    localStorage.clear();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 3, 9, 0));
    TestBed.configureTestingModule({
      providers: [provideRouter(ROUTES), provideHttpClient(), provideHttpClientTesting(), provideLocationMocks()],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(async () => {
    await Promise.all(navigate?.mock.results.map(result => result.value) ?? []);
    httpMock.verify();
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function settle(): void {
    for (let i = 0; i < 3; i++) {
      harness.detectChanges();
    }
  }

  function flushAll(url: string, body: object): number {
    const requests = httpMock.match(r => r.url === url && r.method === 'GET').filter(request => !request.cancelled);
    requests.forEach(request => request.flush(body));
    settle();
    return requests.length;
  }

  function respond(url: string, body: object): void {
    expect(flushAll(url, body), `GET ${url}`).toBeGreaterThan(0);
  }

  async function open(url: string, counterparties: Counterparty[] = []) {
    TestBed.inject(LayoutModeService).set('phone');
    harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    settle();
    respond('/api/accounting/accounts', ACCOUNTS);
    respond('/api/accounting/projects', PROJECTS);
    respond('/api/accounting/counterparties', counterparties);
    respond('/api/accounting/preference', makePreference());
    const spy = vi.spyOn(TestBed.inject(Router), 'navigateByUrl');
    navigate = spy;
    const back = vi.spyOn(TestBed.inject(Location), 'back').mockImplementation(() => undefined);
    const el = harness.routeNativeElement as HTMLElement;
    const left = () => spy.mock.calls.some(([target]) => target === '/accounting') || back.mock.calls.length > 0;
    return { el, left };
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function tap(el: HTMLElement, selector: string, label: string): void {
    const target = Array.from(el.querySelectorAll<HTMLElement>(selector)).find(node => text(node).includes(label));
    if (!target) {
      throw new Error(`no ${selector} with ${label}`);
    }
    target.click();
    settle();
  }

  function keys(el: HTMLElement, ...pressed: string[]): void {
    for (const key of pressed) {
      (el.querySelector(`app-amount-keypad button[data-key="${key}"]`) as HTMLButtonElement).click();
      settle();
    }
  }

  function set(el: HTMLElement, selector: string, value: string, event = 'input'): void {
    const field = el.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    field.value = value;
    field.dispatchEvent(new Event(event));
    settle();
  }

  async function netflix(start: string | null) {
    const opened = await open('/accounting/entry');
    const { el } = opened;
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    set(el, '.account-select', '2', 'change');
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2, name: '範例卡' }));
    keys(el, '3', '9', '0');
    tap(el, '.schedule-tab', '週期');
    if (start) {
      set(el, '.sched-start', start, 'change');
    }
    return opened;
  }

  function definitionRequest() {
    return httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
  }

  function catchUpRequest() {
    return httpMock.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/9/catch-up');
  }

  it('creates a monthly Netflix definition without an entry or a catch-up', async () => {
    // Spec "Monthly Netflix".
    const { el, left } = await netflix('2026-10-22');
    keys(el, '✓');
    const req = definitionRequest();
    expect(req.request.body).toMatchObject({
      kind: 'recurring', name: 'Netflix', interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', times: null,
      end_date: null, posting_mode: 'auto', loan: null,
      template: { lines: [{ kind: 'expense', account_id: 2, amount: '390', currency: 'TWD', category_id: 41 }] },
    });
    req.flush(makeDefinition({ id: 9 }));
    settle();
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/entries');
    expect(left()).toBe(true);
  });

  it('catches up when the start is today and posting is automatic', async () => {
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    httpMock
      .expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/9/catch-up')
      .flush({ posted: [31], failed: null, definition: makeDefinition({ id: 9 }) });
    settle();
    expect(left()).toBe(true);
  });

  it('retries only the catch-up after it failed: exactly one create request', async () => {
    // Multica R-F2: the create succeeded, the catch-up hit 409 import_running; ✓ again must not create a second
    // schedule (or a second payable for a loan) — it retries the catch-up for the kept id.
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    catchUpRequest().flush(
      { code: 'conflict', message: 'import_running' }, { status: 409, statusText: 'Conflict' },
    );
    settle();
    expect(left()).toBe(false);
    expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
    expect(text(el.querySelector('.form-error'))).toBe('排程已建立，入帳未完成；按 ✓ 重試入帳');

    keys(el, '✓');
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    catchUpRequest().flush({ posted: [31], failed: null, definition: makeDefinition({ id: 9 }) });
    settle();
    expect(left()).toBe(true);
  });

  it('surfaces a catch-up answered 200 with a failed period', async () => {
    // Multica R-F2: CatchUpOut.failed is shown, never discarded; the schedule exists, the period waits in 待完成交易.
    const { el, left } = await netflix(null);
    keys(el, '✓');
    definitionRequest().flush(makeDefinition({ id: 9, anchor_date: '2026-10-03' }));
    settle();
    catchUpRequest().flush({
      posted: [], failed: { instance_id: 31, error: 'lines[0].account_id: 帳戶已封存' }, definition: makeDefinition({ id: 9 }),
    });
    settle();
    expect(TestBed.inject(AccountingToastService).message()).toBe('排程已建立；這一期入帳失敗，請到待完成交易處理');
    expect(left()).toBe(true);
    httpMock.expectNone(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
  });

  it('leaves a back-dated start to 待完成交易', async () => {
    // Spec "Back-dated start waits for the owner".
    const { el } = await netflix('2026-09-15');
    keys(el, '✓');
    expect(definitionRequest().request.body.anchor_date).toBe('2026-09-15');
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
  });

  it('hides the fields a schedule cannot carry', async () => {
    // Spec "Unsupported fields hidden".
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    expect(el.querySelector('button.cur')).not.toBeNull();
    expect(el.querySelector('.fee-open')).not.toBeNull();
    expect(el.querySelector('.invoice-tile')).not.toBeNull();
    expect(el.querySelector('app-split-lines')).not.toBeNull();

    tap(el, '.schedule-tab', '週期');

    expect(el.querySelector('button.cur')).toBeNull();
    expect(el.querySelector('.fee-open')).toBeNull();
    expect(el.querySelector('.invoice-tile')).toBeNull();
    expect(el.querySelector('app-split-lines')).toBeNull();
    expect(text(el.querySelector('.schedule-unsupported'))).toBe('排程不支援');
  });

  it('keeps the currency pill while a rate is set, so the owner can remove it on 週期', async () => {
    // Otherwise 排程不支援外幣，請先移除匯率 would be a dead end: the pill that removes the rate would be hidden.
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    const form = harness.routeDebugElement!.componentInstance as EntryFormComponent;
    form.fx.set({ original_currency: 'JPY' } as unknown as FxValue);
    settle();
    tap(el, '.schedule-tab', '週期');
    expect(text(el.querySelector('button.cur'))).toBe('JPY');
    form.fx.set(null);
    settle();
    expect(el.querySelector('button.cur')).toBeNull();
  });

  it('saves a recurring transfer and offers no 分期 for 轉帳', async () => {
    // Spec "Recurring transfer from the form".
    const { el } = await open('/accounting/entry?kind=transfer');
    respond('/api/accounting/categories', [MOVE]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    set(el, '.to-select', '3', 'change');
    set(el, '.out-amount', '15000');
    expect(Array.from(el.querySelectorAll('.schedule-tab')).map(text)).toEqual(['單次', '週期']);
    tap(el, '.schedule-tab', '週期');
    expect(el.querySelector('app-transfer-panel .plus')).toBeNull();
    set(el, '.sched-start', '2026-11-05', 'change');
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = definitionRequest();
    expect(req.request.body.template.lines[0]).toMatchObject({
      kind: 'transfer', account_id: 1, to_account_id: 3, amount: '15000', to_amount: null, category_id: 60,
    });
    expect(req.request.body.anchor_date).toBe('2026-11-05');
  });

  it('creates a new loan with interest in one request', async () => {
    // Spec "New loan with interest".
    const { el } = await open('/accounting/entry?kind=payable', [LENDER]);
    respond('/api/accounting/categories', [LOANS]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '借款');
    set(el, '.name-input', '信貸');
    set(el, '.counterparty-input', '範例銀行');
    keys(el, '3', '0', '0', '0', '0', '0');
    tap(el, '.schedule-tab', '分期');
    set(el, '.sched-periods', '36');
    set(el, '.sched-first', '2026-11-09', 'change');
    expect((el.querySelector('.sched-per') as HTMLInputElement).value).toBe('8,333');
    set(el, '.sched-interest', '620');
    expect((el.querySelector('.sched-repay') as HTMLSelectElement).value).toBe('1');
    keys(el, '✓');
    const req = definitionRequest();
    expect(req.request.body).toMatchObject({
      kind: 'installment', name: '信貸', interval_unit: 'month', anchor_date: '2026-11-09', times: 36,
      total_amount: '300000', posting_mode: 'auto',
      loan: { account_id: 1, counterparty_id: 7, category_id: 50, amount: '300000', entry_date: '2026-10-03', name: '信貸' },
    });
    expect(req.request.body.template.lines.map((line: { kind: string; amount: string }) => [line.kind, line.amount])).toEqual([
      ['repayment', '8333'], ['interest', '620'],
    ]);
    req.flush(makeDefinition({ id: 9, kind: 'installment' }));
    settle();
    httpMock.expectNone(r => r.url.endsWith('/catch-up'));
  });

  it('shows the server field error beside its field', async () => {
    const { el } = await open('/accounting/entry');
    respond('/api/accounting/categories', [STREAMING]);
    respond('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    tap(el, '.cat', '娛樂');
    tap(el, '.cat', 'Netflix');
    keys(el, '9', '0', '0', '0');
    tap(el, '.schedule-tab', '分期');
    keys(el, '✓');
    definitionRequest().flush(
      { detail: [{ loc: ['body', 'times'], msg: '分期至少 2 期', type: 'value_error' }] },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    settle();
    expect(text(el.querySelector('.field-error[data-field="times"]'))).toBe('分期至少 2 期');
    expect(text(el.querySelector('.form-error'))).toBe('分期至少 2 期');
  });

  it('edits the whole schedule in definition mode with PUT', async () => {
    const { el, left } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5 }), instances: [] });
    respond('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    respond('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('.topbar h2'))).toBe('編輯排程');
    expect(text(el.querySelector('.schedule-tab.on'))).toBe('週期');
    expect((el.querySelector('.name-input') as HTMLInputElement).value).toBe('Netflix');
    set(el, '.name-input', 'Netflix 家庭');
    (el.querySelector('button.save') as HTMLButtonElement).click();
    settle();
    const req = httpMock.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/definitions/5');
    expect(req.request.body).toMatchObject({
      name: 'Netflix 家庭', interval_unit: 'month', anchor_date: '2026-10-22', posting_mode: 'auto',
      template: { lines: [{ kind: 'expense', account_id: 2, amount: '390', category_id: 41 }] },
    });
    expect(req.request.body).not.toHaveProperty('kind');
    expect(req.request.body).not.toHaveProperty('loan');
    req.flush(makeDefinition({ id: 5, name: 'Netflix 家庭' }));
    settle();
    expect(left()).toBe(true);
  });

  it('shows the lock banner for an imported definition before cutover', async () => {
    const { el } = await open('/accounting/entry?schedule=5');
    respond('/api/accounting/schedules/definitions/5', { ...makeDefinition({ id: 5, imported: true, locked: true }), instances: [] });
    flushAll('/api/accounting/categories', [STREAMING]);
    flushAll('/api/accounting/accounts/1', makeAccountDetail({ id: 1 }));
    flushAll('/api/accounting/accounts/2', makeAccountDetail({ id: 2 }));
    expect(text(el.querySelector('app-lock-banner'))).toContain('MOZE 匯入資料，切換後可編輯');
    expect((el.querySelector('button.save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('starts the next record on 單次 after 連續記帳', async () => {
    const { el, left } = await netflix('2026-10-22');
    el.querySelector('.entry-form')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
    settle();
    definitionRequest().flush(makeDefinition({ id: 9 }));
    settle();
    expect(left()).toBe(false);
    expect(text(el.querySelector('.schedule-tab.on'))).toBe('單次');
    expect(el.querySelector('.saved-flash')).not.toBeNull();
  });
});
```

- [ ] 22.3 Run both.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/entry-form/schedule-save.spec.ts' --include='src/app/components/accounting/entry-form/entry-form-schedule.spec.ts'
```

Expected: build fails with `TS2307: Cannot find module './schedule-save'`.

- [ ] 22.4 Create `frontend/src/app/components/accounting/entry-form/schedule-save.ts`:

```typescript
import {
  EntryDetail,
  LedgerAccount,
  ScheduleDefinition,
  ScheduleDefinitionInput,
  ScheduleDefinitionUpdate,
  ScheduleLine,
  ScheduleLineInput,
  ScheduleLineKind,
  TransferInput,
} from '../../../models/accounting.model';
import { amountString, parseAmountText } from '../amount-text';
import { splitInstallment } from '../schedule-math';
import { ScheduleDraft, defaultDraft } from '../schedule-tabs/schedule-draft';
import { FormKind } from './entry-draft';
import { TransferEdit } from './transfer-math';

/** A form problem found before sending; `field` is the server field the message belongs beside. */
export class ScheduleFormError extends Error {
  constructor(
    readonly field: string,
    message: string,
  ) {
    super(message);
    this.name = 'ScheduleFormError';
  }
}

/** Everything `buildDefinitionInput` reads from the entry form. */
export interface ScheduleFormValues {
  kind: FormKind;
  draft: ScheduleDraft;
  name: string;
  merchant: string;
  description: string;
  tags: string[];
  projectId: number | null;
  categoryId: number | null;
  categoryName: string | null;
  account: LedgerAccount | null;
  /** The amount tile, unsigned, in the account currency (FX is refused on schedules). */
  amount: number | null;
  counterpartyId: number | null;
  transfer: TransferInput | null;
  transferFrom: LedgerAccount | null;
  transferTo: LedgerAccount | null;
  /** 還款帳戶 (分期 on 應付款項); null = the entry's account. */
  repayAccount: LedgerAccount | null;
  entryDate: string;
  /** Definition mode of a loan: the payable the schedule already repays (no new loan is created). */
  loanEntryId: number | null;
}

/** The form fields a definition fills in definition mode (編輯整個排程). */
export interface ScheduleFormState {
  kind: FormKind;
  draft: ScheduleDraft;
  name: string;
  merchant: string;
  description: string;
  tags: string[];
  projectId: number | null;
  accountId: number;
  categoryId: number | null;
  amount: string;
  counterparty: string | null;
  transfer: TransferEdit | null;
  loanEntryId: number | null;
}

const KIND_NAMES: Partial<Record<FormKind, string>> = {
  expense: '支出',
  income: '收入',
  transfer: '轉帳',
  receivable: '應收款項',
  payable: '應付款項',
};

function line(kind: ScheduleLineKind, account: LedgerAccount, amount: string, extra: Partial<ScheduleLineInput> = {}): ScheduleLineInput {
  return {
    kind, account_id: account.id, to_account_id: null, to_amount: null, counterparty_id: null, category_id: null,
    project_id: null, amount, currency: account.currency, loan_entry_id: null, name: null, merchant: null, ...extra,
  };
}

function typed(text: string, currency: string, field: string, message: string): number {
  const value = parseAmountText(text, currency);
  if (value === null) {
    throw new ScheduleFormError(field, message);
  }
  return value;
}

function recurringLine(values: ScheduleFormValues, common: Partial<ScheduleLineInput>): ScheduleLineInput {
  if (values.kind === 'transfer') {
    const { transfer, transferFrom, transferTo } = values;
    if (!transfer || !transferFrom || !transferTo) {
      throw new ScheduleFormError('to_account_id', '請選擇轉出與轉入帳戶');
    }
    return line('transfer', transferFrom, transfer.out_amount, {
      ...common,
      to_account_id: transferTo.id,
      to_amount: transferFrom.currency === transferTo.currency ? null : transfer.in_amount,
      category_id: transfer.category_id,
    });
  }
  if (values.kind === 'system') {
    throw new ScheduleFormError('kind', '餘額調整不能排程');
  }
  const account = values.account;
  if (!account) {
    throw new ScheduleFormError('account_id', '請選擇帳戶');
  }
  if (values.amount === null || values.amount <= 0) {
    throw new ScheduleFormError('amount', '請輸入金額');
  }
  const party = values.kind === 'receivable' || values.kind === 'payable';
  return line(values.kind, account, amountString(values.amount, account.currency), {
    ...common,
    category_id: values.categoryId,
    counterparty_id: party ? values.counterpartyId : null,
  });
}

/** The `POST /schedules/definitions` body for the 週期 / 分期 tab (spec "Entry form schedule tabs"). */
export function buildDefinitionInput(values: ScheduleFormValues): ScheduleDefinitionInput {
  const { draft } = values;
  const typedName = values.name.trim();
  const name = typedName || values.categoryName || KIND_NAMES[values.kind] || '排程';
  const common: Partial<ScheduleLineInput> = {
    project_id: values.projectId,
    name: typedName || null,
    merchant: values.merchant.trim() || null,
  };
  const template = { description: values.description.trim() || null, tags: [...values.tags] };
  if (draft.tab === 'recurring') {
    return {
      kind: 'recurring',
      name,
      template: { ...template, lines: [recurringLine(values, common)] },
      interval_unit: draft.unit,
      interval_n: draft.every,
      anchor_date: draft.start,
      day_of_month: draft.dayOfMonth,
      times: draft.endMode === 'times' ? draft.endTimes : null,
      end_date: draft.endMode === 'date' ? draft.endDate : null,
      total_amount: null,
      posting_mode: draft.mode,
      loan: null,
    };
  }
  if (draft.tab !== 'installment') {
    throw new ScheduleFormError('kind', '請選擇週期或分期');
  }
  if (values.kind !== 'expense' && values.kind !== 'payable') {
    throw new ScheduleFormError('kind', '分期只適用支出與應付款項');
  }
  const account = values.account;
  if (!account) {
    throw new ScheduleFormError('account_id', '請選擇帳戶');
  }
  const total = values.amount;
  if (total === null || total <= 0) {
    throw new ScheduleFormError('total_amount', '請輸入總額');
  }
  if (draft.periods < 2) {
    throw new ScheduleFormError('times', '分期至少 2 期');
  }
  const currency = account.currency;
  const perPeriod = draft.perPeriod
    ? typed(draft.perPeriod, currency, 'lines[0].amount', '每期金額格式錯誤')
    : splitInstallment(total, draft.periods, currency).perPeriod;
  const interest = draft.interest ? typed(draft.interest, currency, 'lines[1].amount', '利息格式錯誤') : null;
  const totalText = amountString(total, currency);
  const base = {
    kind: 'installment' as const,
    name,
    interval_unit: 'month' as const,
    interval_n: 1,
    anchor_date: draft.firstDate,
    day_of_month: draft.dayOfMonth,
    times: draft.periods,
    end_date: null,
    total_amount: totalText,
    posting_mode: draft.mode,
  };
  if (values.kind === 'expense') {
    const lines = [line('expense', account, amountString(perPeriod, currency), { ...common, category_id: values.categoryId })];
    if (interest !== null) {
      lines.push(line('interest', account, amountString(interest, currency), { name: '利息' }));
    }
    return { ...base, template: { ...template, lines }, loan: null };
  }
  const repay = values.repayAccount ?? account;
  const lines = [
    line('repayment', repay, amountString(perPeriod, currency), {
      ...common,
      category_id: values.categoryId,
      loan_entry_id: values.loanEntryId,
    }),
  ];
  if (interest !== null) {
    lines.push(line('interest', repay, amountString(interest, currency), { name: '利息' }));
  }
  if (values.loanEntryId !== null) {
    return { ...base, template: { ...template, lines }, loan: null };
  }
  if (values.counterpartyId === null) {
    throw new ScheduleFormError('counterparty_id', '請輸入對象');
  }
  return {
    ...base,
    template: { ...template, lines },
    loan: {
      account_id: account.id,
      counterparty_id: values.counterpartyId,
      category_id: values.categoryId,
      amount: totalText,
      entry_date: values.entryDate,
      name: typedName || null,
    },
  };
}

/** `PUT /schedules/definitions/{id}`: everything but `kind` and `loan`. */
export function definitionUpdateFrom(input: ScheduleDefinitionInput): ScheduleDefinitionUpdate {
  const { kind: _kind, loan: _loan, ...update } = input;
  return update;
}

/** 起始日 / 首次還款日 today with 自動入帳: the form posts today's period at once (D43); a back-dated one waits. */
export function shouldCatchUp(input: ScheduleDefinitionInput, today: string): boolean {
  return input.posting_mode === 'auto' && input.anchor_date === today;
}

/** A transfer line as the two legs the transfer panel prefills from. */
export function transferEditFromLine(source: ScheduleLine): TransferEdit {
  const leg = (accountId: number, amount: string) =>
    ({ account_id: accountId, amount, category_id: source.category_id, children: [] }) as unknown as EntryDetail;
  return {
    groupId: '',
    out: leg(source.account_id, `-${source.amount}`),
    in: leg(source.to_account_id ?? source.account_id, source.to_amount ?? source.amount),
  };
}

function formKindOf(kind: ScheduleLineKind): FormKind {
  switch (kind) {
    case 'repayment':
      return 'payable';
    case 'collection':
      return 'receivable';
    case 'interest':
      return 'expense';
    default:
      return kind;
  }
}

/** 編輯整個排程: the entry form's fields from a definition (its first line, and the interest line of a loan). */
export function draftFromDefinition(definition: ScheduleDefinition): ScheduleFormState {
  const [first, ...rest] = definition.template.lines;
  const interest = rest.find(item => item.kind === 'interest') ?? null;
  const installment = definition.kind === 'installment';
  const base = defaultDraft(definition.anchor_date);
  const draft: ScheduleDraft = {
    ...base,
    tab: installment ? 'installment' : 'recurring',
    unit: definition.interval_unit,
    every: definition.interval_n,
    start: definition.anchor_date,
    startTouched: true,
    dayOfMonth: definition.day_of_month,
    endMode: !installment && definition.times !== null ? 'times' : definition.end_date ? 'date' : 'never',
    endTimes: definition.times ?? base.endTimes,
    endDate: definition.end_date ?? base.endDate,
    mode: definition.posting_mode,
    periods: definition.times ?? base.periods,
    firstDate: definition.anchor_date,
    firstTouched: true,
    perPeriod: first.amount,
    interest: interest?.amount ?? '',
    repayAccountId: first.kind === 'repayment' ? first.account_id : null,
  };
  const total = installment && definition.total_amount !== null ? definition.total_amount : first.amount;
  return {
    kind: formKindOf(first.kind),
    draft,
    name: definition.name,
    merchant: first.merchant ?? '',
    description: definition.template.description ?? '',
    tags: [...definition.template.tags],
    projectId: first.project_id,
    accountId: first.account_id,
    categoryId: first.category_id,
    amount: String(Number(total)),
    counterparty: first.counterparty,
    transfer: first.kind === 'transfer' ? transferEditFromLine(first) : null,
    loanEntryId: first.loan_entry_id,
  };
}
```

- [ ] 22.5 Edit `frontend/src/app/components/accounting/transfer-panel/transfer-panel.ts`: below `readonly keepArchived = input(false);` add

```typescript
  /** 週期 on the entry form: schedules carry no fee / discount, so both "+" buttons are hidden (排程不支援). */
  readonly scheduled = input(false);
```

  and in `transfer-panel.html` wrap each of the two lines `<button type="button" class="plus" aria-label="轉出手續費與折扣" (click)="openSheet('out', $event)">+</button>` and `<button type="button" class="plus" aria-label="轉入手續費與折扣" (click)="openSheet('in', $event)">+</button>` in `@if (!scheduled()) { … }`.

- [ ] 22.6 Edit `frontend/src/app/components/accounting/entry-form/entry-form.html`:
  - `[disabled]="locked() || saving() || loading() || loadFailed()"` → `[disabled]="locked() || definitionLocked() || saving() || loading() || loadFailed()"`;
  - `<app-lock-banner [locked]="locked()" />` → `<app-lock-banner [locked]="locked() || definitionLocked()" />`;
  - `<fieldset class="content" [disabled]="locked() || loading()">` → `<fieldset class="content" [disabled]="locked() || definitionLocked() || loading()">`;
  - in `<app-transfer-panel …>` add the binding `[scheduled]="scheduling()"` after `[keepArchived]="entryId() !== null"`;
  - `        @if (category() !== null) {` → `        @if (category() !== null && !scheduling()) {`;
  - replace `            <button type="button" class="cur" aria-label="幣種與匯率" (click)="openSheet('fx')">{{ entryCurrency() }}</button>` with

```html
            @if (scheduling() && !fx()) {
              <span class="cur">{{ accountCurrency() }}</span>
            } @else {
              <!-- also on 週期 / 分期 while a rate is set: the FX sheet is where the owner removes it -->
              <button type="button" class="cur" aria-label="幣種與匯率" (click)="openSheet('fx')">{{ entryCurrency() }}</button>
            }
```

  - replace `            <button type="button" class="plus fee-open" aria-label="手續費與折扣" (click)="openSheet('fee')">+</button>` with

```html
            @if (!scheduling()) {
              <button type="button" class="plus fee-open" aria-label="手續費與折扣" (click)="openSheet('fee')">+</button>
            }
```

  - replace

```html
        @if (kind() !== 'transfer') {
          <div class="tile invoice-tile">
```

  with

```html
        @if (scheduling()) {
          <p class="tile wide schedule-unsupported">排程不支援</p>
        }
        @if (kind() !== 'transfer' && !scheduling()) {
          <div class="tile invoice-tile">
```

  - replace

```html
      <div class="chips">
        @if (kind() !== 'transfer') {
          @for (rule of offeredRules(); track rule.id) {
```

  with

```html
      <div class="chips">
        @if (kind() !== 'transfer' && !scheduling()) {
          @for (rule of offeredRules(); track rule.id) {
```

  and

```html
        @if (kind() !== 'transfer') {
          @if (fee(); as child) {
```

  with

```html
        @if (kind() !== 'transfer' && !scheduling()) {
          @if (fee(); as child) {
```

  - replace the whole `<details class="advanced">…</details>` block with

```html
      <details class="advanced" [open]="scheduling() || scheduleId() !== null">
        <summary>進階</summary>
        <app-schedule-tabs
          [kind]="kind()"
          [entryDate]="entryDate()"
          [amount]="amount()"
          [currency]="accountCurrency()"
          [accounts]="accountOptions()"
          [accountId]="accountId()"
          [disabled]="locked() || definitionLocked() || createdScheduleId() !== null"
          [definitionMode]="scheduleId() !== null"
          [errors]="fieldErrors()"
          [(draft)]="scheduleDraft"
        >
          <label class="adv-row">
            <span>入帳日</span>
            <input type="date" class="posted-input" [value]="postedDate() || entryDate()" (change)="postedDate.set($any($event.target).value)" />
          </label>
        </app-schedule-tabs>
      </details>
```

- [ ] 22.7 Edit `frontend/src/app/components/accounting/entry-form/entry-form.ts`:
  - imports: add `ScheduleDefinition` to the `accounting.model` import list; replace `import { writeErrorMessage } from '../http-errors';` with `import { fieldErrors, writeErrorMessage } from '../http-errors';`; add

```typescript
import { AccountingToastService } from '../accounting-toast';
import { IMPORT_RUNNING_TOAST, isImportRunning } from '../schedule-math';
import { ScheduleTabsComponent } from '../schedule-tabs/schedule-tabs';
import { ScheduleDraft, defaultDraft } from '../schedule-tabs/schedule-draft';
import { ScheduleFormError, buildDefinitionInput, definitionUpdateFrom, draftFromDefinition, shouldCatchUp } from './schedule-save';
```

  and add `ScheduleTabsComponent` to the `imports` array of `@Component`;
  - below `readonly transferPanel = viewChild(TransferPanelComponent);` add

```typescript
  private readonly toast = inject(AccountingToastService);
  /** 進階 單次 / 週期 / 分期 (Task 21); 單次 is the plain entry. */
  readonly scheduleDraft = signal<ScheduleDraft>(defaultDraft(todayIso()));
  /** 編輯整個排程: the definition being edited (`?schedule=<id>`), else null. */
  readonly scheduleId = signal<number | null>(null);
  /** An imported definition before cutover: shown read-only with the lock banner. */
  readonly definitionLocked = signal(false);
  /** Server 422 field errors of the last schedule save, shown beside their fields. */
  readonly fieldErrors = signal<Record<string, string>>({});
  readonly scheduling = computed(() => this.scheduleDraft().tab !== 'single');
  /** The payable a loan definition repays (definition mode). */
  private loanEntryId: number | null = null;
  private definitionRequest = 0;
  /** R-F2: a definition this form created whose catch-up has not completed; ✓ then retries the catch-up only. */
  readonly createdScheduleId = signal<number | null>(null);
```

  - replace `readonly title = computed(() => (this.editing() ? '編輯記錄' : '新增記錄'));` with `readonly title = computed(() => (this.scheduleId() !== null ? '編輯排程' : this.editing() ? '編輯記錄' : '新增記錄'));`;
  - replace

```typescript
      .subscribe(([params, query]) => this.load(this.start(params.get('id'), query.get('kind'), query.get('copy'))));
```

  with

```typescript
      .subscribe(([params, query]) => {
        const schedule = query.get('schedule');
        this.load(this.start(params.get('id'), query.get('kind'), query.get('copy'), schedule));
        if (schedule !== null) {
          this.loadDefinition(Number(schedule));
        }
      });
```

  - change the signature `private start(id: string | null, kindParam: string | null, copyParam: string | null): EntryTarget | null {` to `private start(id: string | null, kindParam: string | null, copyParam: string | null, scheduleParam: string | null = null): EntryTarget | null {`, and right after its line `this.editing.set(id !== null);` insert

```typescript
    ++this.definitionRequest;
    this.loanEntryId = null;
    this.definitionLocked.set(false);
    this.createdScheduleId.set(null);
    this.scheduleId.set(scheduleParam !== null ? Number(scheduleParam) : null);
```

  - in `resetFields()` add at the end

```typescript
    this.scheduleDraft.set(defaultDraft(todayIso()));
    this.fieldErrors.set({});
```

  - replace `tabDisabled` with

```typescript
  /** While editing, the record type cannot change into 系統 or between a transfer and a single entry; in definition
   * mode it cannot change at all. */
  tabDisabled(kind: FormKind): boolean {
    if (this.scheduleId() !== null) {
      return kind !== this.kind();
    }
    return this.editing() && (kind === 'system' || (kind === 'transfer') !== (this.kind() === 'transfer'));
  }
```

  - in `validate()`, right after the `if (this.unsupported()) { return this.unsupported(); }` statement, insert

```typescript
    if (this.definitionLocked()) {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (this.scheduling() && this.fx()) {
      return '排程不支援外幣，請先移除匯率';
    }
```

  - in `validate()` replace `if (this.isParty() && !this.counterpartyName().trim()) {` with `if (this.isParty() && !this.counterpartyName().trim() && this.loanEntryId === null) {` (a loan definition repays an existing payable; its form carries no counterparty);
  - in `save()` replace

```typescript
    this.error.set(null);
    this.saving.set(true);
    // Captured now: the id set when the matching response was applied, not whatever the route says later.
```

  with

```typescript
    this.error.set(null);
    if (this.scheduling() || this.scheduleId() !== null) {
      this.saveSchedule(continuous && this.scheduleId() === null);
      return;
    }
    this.saving.set(true);
    // Captured now: the id set when the matching response was applied, not whatever the route says later.
```

  - add these methods below `save()`:

```typescript
  /** 編輯整個排程: the definition's fields into the form; an answer for an older navigation is dropped. */
  private loadDefinition(definitionId: number): void {
    const request = ++this.definitionRequest;
    this.loading.set(true);
    this.accounting
      .getScheduleDefinition(definitionId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: definition => {
          if (request === this.definitionRequest) {
            this.applyDefinition(definition);
            this.loading.set(false);
          }
        },
        error: () => {
          if (request === this.definitionRequest) {
            this.loading.set(false);
            this.error.set('排程讀取失敗，請稍後再試。');
          }
        },
      });
  }

  private applyDefinition(definition: ScheduleDefinition): void {
    const form = draftFromDefinition(definition);
    this.definitionLocked.set(definition.locked);
    this.loanEntryId = form.loanEntryId;
    this.kind.set(form.kind);
    this.scheduleDraft.set(form.draft);
    this.name.set(form.name);
    this.merchant.set(form.merchant);
    this.description.set(form.description);
    this.tags.set(form.tags);
    this.projectId.set(form.projectId);
    this.accountId.set(form.accountId);
    this.amountExpr.set(form.amount);
    this.counterpartyName.set(form.counterparty ?? '');
    this.pendingCategoryId = form.categoryId;
    this.resolvePendingCategory();
    this.transferEdit.set(form.transfer);
  }

  /** 週期 / 分期: create (then catch-up when it starts today, 自動入帳) or, in definition mode, PUT the definition. */
  private saveSchedule(keepGoing: boolean): void {
    this.fieldErrors.set({});
    const created = this.createdScheduleId();
    if (created !== null) {
      this.runCatchUp(created, keepGoing); // R-F2: the create already completed — never create it again
      return;
    }
    const panel = this.transferPanel();
    const transfer = this.kind() === 'transfer' ? (panel?.buildInput(transferCommonFrom(this.sharedFields())) ?? null) : null;
    if (this.kind() === 'transfer' && !transfer) {
      return; // the panel shows its own message
    }
    const repayId = this.scheduleDraft().repayAccountId;
    const counterparty: Observable<number | null> = this.isParty() && this.loanEntryId === null
      ? resolveCounterpartyId(this.accounting, this.counterpartyName(), this.counterparties(), party =>
          this.counterparties.update(list => [...list, party]),
        )
      : of(null);
    const definitionId = this.scheduleId();
    const request = counterparty.pipe(
      map(counterpartyId =>
        buildDefinitionInput({
          kind: this.kind(),
          draft: this.scheduleDraft(),
          name: this.name(),
          merchant: this.merchant(),
          description: this.description(),
          tags: this.tags(),
          projectId: this.projectId(),
          categoryId: this.category()?.id ?? null,
          categoryName: this.category()?.name ?? null,
          account: this.account(),
          amount: this.amount(),
          counterpartyId,
          transfer,
          transferFrom: panel?.from() ?? null,
          transferTo: panel?.to() ?? null,
          repayAccount: repayId === null ? null : (this.accounts().find(account => account.id === repayId) ?? null),
          entryDate: this.entryDate(),
          loanEntryId: this.loanEntryId,
        }),
      ),
      switchMap(input =>
        definitionId !== null
          ? this.accounting.updateScheduleDefinition(definitionId, definitionUpdateFrom(input)).pipe(map(() => null))
          : this.accounting
              .createScheduleDefinition(input)
              .pipe(map(created => (shouldCatchUp(input, todayIso()) ? created.id : null))),
      ),
    );
    this.saving.set(true);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: catchUpId => {
        this.saving.set(false);
        if (catchUpId !== null) {
          this.createdScheduleId.set(catchUpId); // creation is complete from here on (R-F2)
          this.runCatchUp(catchUpId, keepGoing);
          return;
        }
        this.finish(keepGoing);
      },
      error: (error: unknown) => {
        this.saving.set(false);
        if (error instanceof ScheduleFormError) {
          this.fieldErrors.set({ [error.field]: error.message });
          this.error.set(error.message);
          return;
        }
        if (isImportRunning(error)) {
          this.toast.show(IMPORT_RUNNING_TOAST);
          return;
        }
        this.fieldErrors.set(fieldErrors(error));
        this.error.set(writeErrorMessage(error));
      },
    });
  }

  /** 補入帳 right after a create that starts today (自動入帳), and its retry (R-F2). A 200 with `failed` is surfaced in
   *  the toast, never discarded; an error keeps `createdScheduleId`, so ✓ retries this call only. */
  private runCatchUp(definitionId: number, keepGoing: boolean): void {
    this.saving.set(true);
    this.accounting
      .catchUpSchedule(definitionId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: result => {
          this.saving.set(false);
          this.createdScheduleId.set(null);
          if (result.failed !== null) {
            this.toast.show(CATCH_UP_FAILED_TOAST);
          }
          this.finish(keepGoing);
        },
        error: (error: unknown) => {
          this.saving.set(false);
          if (isImportRunning(error)) {
            this.toast.show(IMPORT_RUNNING_TOAST);
          }
          this.error.set(CATCH_UP_RETRY_TEXT);
        },
      });
  }
```

  and add above `@Component` (module level):

```typescript
/** R-F2 copy: the schedule exists; only its first catch-up is outstanding. */
export const CATCH_UP_RETRY_TEXT = '排程已建立，入帳未完成；按 ✓ 重試入帳';
export const CATCH_UP_FAILED_TOAST = '排程已建立；這一期入帳失敗，請到待完成交易處理';
```

- [ ] 22.8 Run 22.3 again, then the existing entry form and transfer panel specs.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend
npx ng test --watch=false --include='src/app/components/accounting/entry-form/schedule-save.spec.ts' --include='src/app/components/accounting/entry-form/entry-form-schedule.spec.ts'
npx ng test --watch=false --include='src/app/components/accounting/entry-form/**/*.spec.ts' --include='src/app/components/accounting/transfer-panel/*.spec.ts' 2>&1 | tail -4
```

Expected: `20 passed` (7 + 13); then every entry-form and transfer-panel spec passes (the existing ones see the 進階 block as a tab block whose 單次 panel still holds 入帳日).

- [ ] 22.9 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/entry-form frontend/src/app/components/accounting/transfer-panel
git commit -m "feat(frontend): entry form saves 週期 / 分期 as schedules and edits a whole schedule

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 23. 提醒中心 tabs by query and the 待完成交易 tab

**Model:** opus

**Files:**
- Create: `frontend/src/app/components/accounting/reminders/schedule-queue.ts`, `schedule-queue.spec.ts`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts`, `reminders.html`, `reminders.scss`, `reminders.spec.ts`

**Interfaces:**
- Consumes: `getScheduleInstances`, `postScheduleInstance`, `skipScheduleInstance`, `catchUpSchedule`, `repostScheduleInstance`, `acceptPartialScheduleInstance` (Task 20); `AccountingToastService`, `scheduleActionError`, `makeInstance` (Task 20); `shortDate` (`dates.ts`); `formatMoney`, `formatSigned`; `isHandledKey`.
- Produces: `schedule-queue.ts` exports `QueueLine`, `QueueRow`, `QueueGroups`, `queueTitle(item)`, `queueRows(items, today) -> QueueGroups`; `ReminderTab` gains `'pending'`, `REMINDER_TABS` gains `{ key: 'pending', label: '待完成交易' }`, `isReminderTab(value)`; the reminder centre reads `?tab=` and loads the queue with its other data.

Rules (spec "待完成交易 tab", "Import-running feedback"): tabs exactly 全部 / 信用卡帳單 / 借還款追蹤 / 待完成交易, selectable by `?tab=all|cards|debts|pending` (default 全部). 待完成交易 lists `GET /schedules/instances?queue=true` (the server's `until` default is today + 30) in 已到期 (due today or earlier, plus every partial period whatever its date) and 即將到來; each item shows the definition's icon, `name #k/N`, its lines (account, signed amount, `→ to-account` for transfers), the total, the date, `已逾期 N 天` in the warning tone when overdue, `今天` when due today, `last_error` with 重試, and [入帳] [略過]; a partial period instead shows 部分入帳 with [重新入帳] (`repost` with its current amounts) and [保留部分]. 略過 asks inline once (`略過這一期？剩餘不變`); Esc closes the prompt. The first overdue item of a definition with more than one overdue item offers [補入帳至今天]. 全部 shows the card, counterparty and 已到期 sections, in that order. After an action the service bumps `entriesChanged`, which reloads the list (and the 🔔). A 409 `import_running` shows the toast and leaves the list as it is. A queue that fails to load shows `待完成交易讀取失敗，請稍後再試。` without hiding the cards and counterparties.

- [ ] 23.1 Write the failing test `frontend/src/app/components/accounting/reminders/schedule-queue.spec.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import { makeInstance } from '../testing/fixtures';
import { queueRows, queueTitle } from './schedule-queue';

const TODAY = '2026-10-03';

describe('queueRows', () => {
  it('splits 已到期 from 即將到來 and words the date', () => {
    const groups = queueRows(
      [
        makeInstance({ id: 1, due_date: '2026-09-30', overdue_days: 3 }),
        makeInstance({ id: 2, definition_id: 2, due_date: '2026-10-03' }),
        makeInstance({ id: 3, definition_id: 3, due_date: '2026-10-20' }),
      ],
      TODAY,
    );
    expect(groups.due.map(row => [row.id, row.overdueText, row.overdue, row.today])).toEqual([
      [1, '已逾期 3 天', true, false], [2, '今天', false, true],
    ]);
    expect(groups.upcoming.map(row => [row.id, row.overdueText, row.dueText])).toEqual([[3, null, '10/20']]);
  });

  it('keeps a partial period under 已到期 whatever its date', () => {
    const groups = queueRows([makeInstance({ id: 9, status: 'posted', is_partial: true, due_date: '2026-11-09' })], TODAY);
    expect(groups.due.map(row => [row.id, row.partial])).toEqual([[9, true]]);
    expect(groups.upcoming).toEqual([]);
  });

  it('offers 補入帳至今天 on the first of several overdue periods of one definition', () => {
    const groups = queueRows(
      [
        makeInstance({ id: 1, definition_id: 7, due_date: '2026-09-22', overdue_days: 11 }),
        makeInstance({ id: 2, definition_id: 7, due_date: '2026-10-01', overdue_days: 2 }),
        makeInstance({ id: 3, definition_id: 8, due_date: '2026-10-02', overdue_days: 1 }),
      ],
      TODAY,
    );
    expect(groups.due.map(row => [row.id, row.offerCatchUp])).toEqual([[1, true], [2, false], [3, false]]);
  });

  it('titles, lines and totals', () => {
    const item = makeInstance({
      definition_name: '房租', seq: 4, times: 12,
      lines: [
        { kind: 'transfer', account_id: 1, account_name: '薪轉', to_account_id: 3, to_account_name: '交割', category: null,
          counterparty: null, amount: '-15000.0000', currency: 'TWD' },
        { kind: 'expense', account_id: 1, account_name: '薪轉', to_account_id: null, to_account_name: null, category: null,
          counterparty: null, amount: '-18000.0000', currency: 'TWD' },
      ],
      totals: [{ currency: 'TWD', amount: '-33000.0000' }],
    });
    expect(queueTitle(item)).toBe('房租 #4/12');
    expect(queueTitle({ ...item, times: null })).toBe('房租 #4');
    const [row] = queueRows([item], TODAY).upcoming.concat(queueRows([item], TODAY).due);
    expect(row.lines).toEqual([
      { text: '薪轉 → 交割', amount: '$15,000', tone: 'neutral' },
      { text: '薪轉', amount: '−$18,000', tone: 'out' },
    ]);
    expect(row.totalText).toBe('−$33,000');
  });
});
```

- [ ] 23.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/reminders/schedule-queue.spec.ts'
```

Expected: `TS2307: Cannot find module './schedule-queue'`.

- [ ] 23.3 Create `frontend/src/app/components/accounting/reminders/schedule-queue.ts`:

```typescript
import { ScheduleInstance } from '../../../models/accounting.model';
import { shortDate } from '../dates';
import { formatMoney, formatSigned } from '../format';

/** Pure 待完成交易 rows (spec "待完成交易 tab"); the server decides what is in the queue. */

export interface QueueLine {
  text: string;
  amount: string;
  tone: 'out' | 'in' | 'neutral';
}

export interface QueueRow {
  id: number;
  definitionId: number;
  icon: string;
  color: string;
  title: string;
  lines: QueueLine[];
  totalText: string;
  dueText: string;
  /** `已逾期 N 天`, `今天`, or null for a later date. */
  overdueText: string | null;
  overdue: boolean;
  today: boolean;
  error: string | null;
  partial: boolean;
  offerCatchUp: boolean;
  /** Unsigned, aligned with the template: the body of 重新入帳 (`repost`). */
  amounts: string[];
}

export interface QueueGroups {
  due: QueueRow[];
  upcoming: QueueRow[];
}

/** `房租 #4/12`, `Netflix #25` (unlimited). */
export function queueTitle(item: Pick<ScheduleInstance, 'definition_name' | 'seq' | 'times'>): string {
  return `${item.definition_name} #${item.seq}${item.times === null ? '' : `/${item.times}`}`;
}

function lineOf(line: ScheduleInstance['lines'][number]): QueueLine {
  const text = line.to_account_name ? `${line.account_name ?? ''} → ${line.to_account_name}` : (line.account_name ?? '');
  if (line.kind === 'transfer') {
    return { text, amount: formatMoney(Math.abs(Number(line.amount)), line.currency), tone: 'neutral' };
  }
  const amount = Number(line.amount);
  return { text, amount: formatSigned(amount, line.currency), tone: amount < 0 ? 'out' : amount > 0 ? 'in' : 'neutral' };
}

export function queueRows(items: ScheduleInstance[], today: string): QueueGroups {
  const overdueCount = new Map<number, number>();
  for (const item of items) {
    if (item.status === 'pending' && item.due_date < today) {
      overdueCount.set(item.definition_id, (overdueCount.get(item.definition_id) ?? 0) + 1);
    }
  }
  const offered = new Set<number>();
  const groups: QueueGroups = { due: [], upcoming: [] };
  for (const item of items) {
    const pending = item.status === 'pending';
    const overdue = pending && item.due_date < today;
    const offerCatchUp = overdue && (overdueCount.get(item.definition_id) ?? 0) > 1 && !offered.has(item.definition_id);
    if (offerCatchUp) {
      offered.add(item.definition_id);
    }
    const row: QueueRow = {
      id: item.id,
      definitionId: item.definition_id,
      icon: item.category_icon ?? (item.kind === 'installment' ? '💳' : '🔁'),
      color: item.category_color ?? 'var(--app-surface-soft)',
      title: queueTitle(item),
      lines: item.lines.map(lineOf),
      totalText: item.totals.map(total => formatSigned(total.amount, total.currency)).join(' · '),
      dueText: shortDate(item.due_date),
      overdueText: overdue ? `已逾期 ${item.overdue_days} 天` : pending && item.due_date === today ? '今天' : null,
      overdue,
      today: pending && item.due_date === today,
      error: item.last_error,
      partial: item.is_partial,
      offerCatchUp,
      amounts: [...item.amounts],
    };
    (item.is_partial || item.due_date <= today ? groups.due : groups.upcoming).push(row);
  }
  return groups;
}
```

- [ ] 23.4 Run 23.2 again.

Expected: `4 passed`.

- [ ] 23.5 Update the existing reminder-centre spec for the new request, then add the 待完成交易 tests. In `frontend/src/app/components/accounting/reminders/reminders.spec.ts`:
  - imports: add `ScheduleInstance` to the model import, `makeInstance` to the fixtures import, `AccountingToastService` from `'../accounting-toast'`;
  - replace `flushLoad` and `render` with

```typescript
  function flushLoad(
    accounts: LedgerAccount[],
    counterparties: Counterparty[],
    open: LedgerEntry[],
    queue: ScheduleInstance[] = [],
  ): void {
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(accounts);
    http.expectOne('/api/accounting/counterparties').flush(counterparties);
    const req = http.expectOne(r => r.url === '/api/accounting/entries' && !r.params.has('counterparty_id'));
    expect(req.request.params.get('open')).toBe('true');
    req.flush({ items: open, total: open.length, limit: 500, offset: 0 });
    const queueReq = http.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(queueReq.request.params.get('queue')).toBe('true');
    queueReq.flush(queue);
  }
```

```typescript
  function render(
    accounts: LedgerAccount[] = [],
    counterparties: Counterparty[] = [],
    open: LedgerEntry[] = [],
    queue: ScheduleInstance[] = [],
  ) {
    const fixture = TestBed.createComponent(AccountingRemindersComponent);
    fixture.detectChanges();
    flushLoad(accounts, counterparties, open, queue);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }
```

  - every other call of `flushLoad(...)` in the file keeps working (the queue defaults to `[]`);
  - append inside the `describe`:

```typescript
  describe('待完成交易', () => {
    const RENT = makeInstance({
      id: 31, definition_id: 7, definition_name: '房租', seq: 4, times: 12, posting_mode: 'confirm',
      due_date: '2026-09-30', overdue_days: 3, amounts: ['18000'],
      lines: [{ kind: 'expense', account_id: 1, account_name: '薪轉', to_account_id: null, to_account_name: null,
        category: '居家/房租', counterparty: null, amount: '-18000.0000', currency: 'TWD' }],
      totals: [{ currency: 'TWD', amount: '-18000.0000' }],
    });

    function item(el: HTMLElement, id: number): HTMLElement {
      return el.querySelector(`.queue-item[data-instance-id="${id}"]`) as HTMLElement;
    }

    function button(scope: HTMLElement, label: string): HTMLButtonElement {
      return Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(node => text(node) === label)!;
    }

    function sections(el: HTMLElement): string[] {
      return Array.from(el.querySelectorAll('h3.section')).map(text);
    }

    it('has the four tabs and opens 待完成交易 from ?tab=pending', async () => {
      await TestBed.inject(Router).navigateByUrl('/?tab=pending');
      const { el } = render([], [], [], [RENT]);
      expect(Array.from(el.querySelectorAll('.tabs [role="radio"]')).map(text)).toEqual(['全部', '信用卡帳單', '借還款追蹤', '待完成交易']);
      expect(text(el.querySelector('.tabs [aria-checked="true"]'))).toBe('待完成交易');
    });

    it('shows an overdue 提醒入帳 period under 已到期 in the warning tone', () => {
      // Spec "Overdue confirm item".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      expect(sections(el)).toEqual(['已到期']);
      const row = item(el, 31);
      expect(text(row.querySelector('.name'))).toBe('房租 #4/12');
      expect(text(row.querySelector('.due'))).toBe('已逾期 3 天');
      expect(row.querySelector('.due')!.classList).toContain('overdue');
      expect(text(row.querySelector('.total'))).toBe('−$18,000');
      expect(button(row, '入帳')).toBeTruthy();
      expect(button(row, '略過')).toBeTruthy();
    });

    it('asks once before skipping and keeps the remaining', () => {
      // Spec "Skip prompt keeps the remaining".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '略過').click();
      fixture.detectChanges();
      http.expectNone(r => r.url.endsWith('/skip'));
      expect(text(item(el, 31).querySelector('.skip-confirm'))).toBe('略過這一期？剩餘不變');
      (item(el, 31).querySelector('.skip-yes') as HTMLButtonElement).click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/31/skip').flush(makeInstance({ id: 31, status: 'skipped' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('shows the error of a failing period with 重試 instead of 入帳, and 重試 posts it again', () => {
      // Spec "待完成交易 tab": the last_error text with 重試 when set.
      const failing = makeInstance({
        id: 32, definition_id: 10, due_date: '2026-10-01', overdue_days: 2, last_error: 'lines[0].account_id: 帳戶已封存',
      });
      const { fixture, el } = render([], [], [], [failing]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      const row = item(el, 32);
      expect(text(row.querySelector('.queue-error'))).toBe('lines[0].account_id: 帳戶已封存');
      expect(button(row, '入帳')).toBeUndefined();
      button(row, '重試').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/32/post')
        .flush(makeInstance({ id: 32, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('closes the skip prompt on Esc', () => {
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '略過').click();
      fixture.detectChanges();
      item(el, 31).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();
      expect(item(el, 31).querySelector('.skip-confirm')).toBeNull();
    });

    it('posts from the queue and the item leaves the list', () => {
      // Spec "Post from the queue".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '入帳').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/31/post').flush(makeInstance({ id: 31, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
      fixture.detectChanges();
      expect(item(el, 31)).toBeNull();
    });

    it('offers 補入帳至今天 for a backlog', () => {
      // Spec "Catch-up offered for a backlog".
      const first = makeInstance({ id: 41, definition_id: 8, due_date: '2026-09-22', overdue_days: 11 });
      const second = makeInstance({ id: 42, definition_id: 8, due_date: '2026-10-01', overdue_days: 2 });
      const { fixture, el } = render([], [], [], [first, second]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      expect(button(item(el, 41), '補入帳至今天')).toBeTruthy();
      expect(button(item(el, 42), '補入帳至今天')).toBeUndefined();
      button(item(el, 41), '補入帳至今天').click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/8/catch-up')
        .flush({ posted: [41, 42], failed: null, definition: {} });
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('lists a partial period under 已到期 with 重新入帳 and 保留部分', () => {
      const partial = makeInstance({ id: 51, status: 'posted', is_partial: true, due_date: '2026-11-09', amounts: ['8333', '620'] });
      const { fixture, el } = render([], [], [], [partial]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      const row = item(el, 51);
      expect(sections(el)).toEqual(['已到期']);
      expect(text(row.querySelector('.badge.partial'))).toBe('部分入帳');
      expect(button(row, '入帳')).toBeUndefined();
      button(row, '重新入帳').click();
      const repost = http.expectOne(r => r.url === '/api/accounting/schedules/instances/51/repost');
      expect(repost.request.body).toEqual({ amounts: ['8333', '620'] });
      repost.flush(makeInstance({ id: 51, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], [partial]);
      fixture.detectChanges();
      button(item(el, 51), '保留部分').click();
      http.expectOne(r => r.url === '/api/accounting/schedules/instances/51/accept-partial').flush(makeInstance({ id: 51, status: 'posted' }));
      fixture.detectChanges();
      flushLoad([], [], [], []);
    });

    it('shows the toast and keeps the item while an import runs', () => {
      // Spec "Posting during an import".
      const { fixture, el } = render([], [], [], [RENT]);
      tab(el, '待完成交易').click();
      fixture.detectChanges();
      button(item(el, 31), '入帳').click();
      http.expectOne(r => r.url.endsWith('/instances/31/post')).flush(
        { code: 409, message: 'import_running', trace_id: 't' }, { status: 409, statusText: 'Conflict' },
      );
      fixture.detectChanges();
      expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
      expect(item(el, 31)).not.toBeNull();
      expect(el.querySelector('.action-error')).toBeNull();
    });

    it('全部 shows the due periods as a 待完成交易 section after the counterparties', () => {
      const later = makeInstance({ id: 61, definition_id: 9, due_date: '2026-10-20' });
      const { el } = render([], [ALAN], ALAN_OPEN, [RENT, later]);
      expect(sections(el)).toEqual(['借還款追蹤', '待完成交易']);
      expect(item(el, 31)).not.toBeNull();
      expect(item(el, 61)).toBeNull();
    });
  });
```

- [ ] 23.6 Run the reminder spec.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/reminders/reminders.spec.ts'
```

Expected: the old tests (the `借還款追蹤 filters` specs among them) fail only on `Expected one matching request for criteria "Match by function: …/schedules/instances"`, and the new ones fail; nothing else.

- [ ] 23.7 Edit `frontend/src/app/components/accounting/reminders/reminders.ts` **in place** (targeted edits, the way Task 22 edits the entry form). Main's file already carries the 借還款追蹤 filters and the signed-remaining wording of #43 / #44 (`DebtFilter`, `DebtKind`, `DEBT_KIND_OPTIONS`, `filterDebtEntries`, `debtCounterparty` / `debtKind`, `debtCounterparties`, `showDebtFilters`, `debtFilter`, `debtFilteredOut`, `setDebtCounterparty` / `setDebtKind`, `'../filters.scss'` in `styleUrls`, `debtRemaining` with `detail: '2026/09/01 · 原始 … · 已收 …'`, `debtReminders` sorted by name, `filterDebtEntries` inside `expandedRows`); every one of them stays exactly as it is, and `reminders.html` keeps binding them. Make only these edits:

  - imports: replace

```typescript
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { Counterparty, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
```

  with

```typescript
import { NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, catchError, forkJoin, of } from 'rxjs';

import { Counterparty, LedgerAccount, LedgerEntry, ScheduleInstance } from '../../../models/accounting.model';
```

  and add, after `import { LayoutModeService } from '../../../services/layout-mode.service';`,

```typescript
import { AccountingToastService, scheduleActionError } from '../accounting-toast';
import { isHandledKey } from '../accounting-ui';
```

  and, after `import { TimelineRow, buildDays } from '../timeline/timeline';`,

```typescript
import { QueueRow, queueRows } from './schedule-queue';
```

  - replace

```typescript
export type ReminderTab = 'all' | 'cards' | 'debts';

export const REMINDER_TABS: { key: ReminderTab; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'cards', label: '信用卡帳單' },
  { key: 'debts', label: '借還款追蹤' },
];
```

  with

```typescript
export type ReminderTab = 'all' | 'cards' | 'debts' | 'pending';

export const REMINDER_TABS: { key: ReminderTab; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'cards', label: '信用卡帳單' },
  { key: 'debts', label: '借還款追蹤' },
  { key: 'pending', label: '待完成交易' },
];

export function isReminderTab(value: string | null): value is ReminderTab {
  return REMINDER_TABS.some(option => option.key === value);
}
```

  - in the `@Component({...})` decorator: replace `imports: [RouterLink],` with `imports: [RouterLink, NgTemplateOutlet],`, keep `styleUrls: ['../filters.scss', '../entry-row.scss', './reminders.scss'],` unchanged, and add `host: { '(keydown)': 'onKeydown($event)' },` after `changeDetection: ChangeDetectionStrategy.OnPush,`; replace the class comment `/** \`/accounting/reminders\` (提醒中心): unpaid card statements and open receivables / payables. */` with `/** \`/accounting/reminders\` (提醒中心): card statements, open debts, 待完成交易 and (Task 24) 週期／分期. */`;
  - replace

```typescript
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
```

  with

```typescript
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly toast = inject(AccountingToastService);
```

  - replace

```typescript
  readonly openEntries = signal<LedgerEntry[]>([]);
  readonly loaded = signal(false);
```

  with

```typescript
  readonly openEntries = signal<LedgerEntry[]>([]);
  readonly queue = signal<ScheduleInstance[]>([]);
  readonly queueFailed = signal(false);
  readonly loaded = signal(false);
```

  - replace

```typescript
  readonly expandError = signal(false);
  /** 借還款追蹤 filters: kept while the page is open, applied on that tab only. */
```

  with

```typescript
  readonly expandError = signal(false);
  /** The 待完成交易 item whose inline 略過這一期？剩餘不變 prompt is open. */
  readonly confirmingSkip = signal<number | null>(null);
  readonly busyInstance = signal<number | null>(null);
  readonly actionError = signal<string | null>(null);
  /** 借還款追蹤 filters: kept while the page is open, applied on that tab only. */
```

  - replace

```typescript
  /** Cards whose statement read failed: shown as 帳單讀取失敗 with 重試, never as "nothing to pay". */
```

  with

```typescript
  readonly queueGroups = computed(() => queueRows(this.queue(), this.today()));
  /** Cards whose statement read failed: shown as 帳單讀取失敗 with 重試, never as "nothing to pay". */
```

  - replace

```typescript
  readonly showCards = computed(() => this.tab() !== 'debts');
  readonly showDebts = computed(() => this.tab() !== 'cards');
  readonly empty = computed(() => {
    if (!this.loaded() || this.loadError()) {
      return false;
    }
    const cards = this.showCards() ? this.cardRows().length : 0;
    const debts = this.showDebts() ? this.debtCounterparties().length : 0;
    const failed = this.showCards() && this.billsFailed();
    return cards === 0 && debts === 0 && !failed && (!this.showCards() || this.billsSettled());
  });
```

  with (the debt count keeps main's `debtCounterparties()`, so a filter that hides every row still shows `沒有符合篩選的項目`, not the empty state)

```typescript
  readonly showCards = computed(() => this.tab() === 'all' || this.tab() === 'cards');
  readonly showDebts = computed(() => this.tab() === 'all' || this.tab() === 'debts');
  readonly showQueue = computed(() => this.tab() === 'all' || this.tab() === 'pending');
  readonly empty = computed(() => {
    if (!this.loaded() || this.loadError()) {
      return false;
    }
    const groups = this.queueGroups();
    const cards = this.showCards() ? this.cardRows().length : 0;
    const debts = this.showDebts() ? this.debtCounterparties().length : 0;
    const queue = this.tab() === 'pending' ? groups.due.length + groups.upcoming.length : this.tab() === 'all' ? groups.due.length : 0;
    const failed = (this.showCards() && this.billsFailed()) || (this.showQueue() && this.queueFailed());
    return cards === 0 && debts === 0 && queue === 0 && !failed && (!this.showCards() || this.billsSettled());
  });
```

  - at the top of the constructor, before the first `effect(() => {`, add

```typescript
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe(params => {
      const tab = params.get('tab');
      if (isReminderTab(tab)) {
        this.tab.set(tab);
      }
    });

```

  - in `load()`, replace

```typescript
      open: this.accounting.getAllEntries({ open: true, limit: REMINDER_ENTRY_LIMIT, offset: 0 }),
    }).subscribe({
      next: ({ accounts, counterparties, open }) => {
```

  with

```typescript
      open: this.accounting.getAllEntries({ open: true, limit: REMINDER_ENTRY_LIMIT, offset: 0 }),
      queue: this.accounting.getScheduleInstances({ queue: true }).pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ accounts, counterparties, open, queue }) => {
```

  and replace `        this.openEntries.set(open.items);` with

```typescript
        this.openEntries.set(open.items);
        this.queue.set(queue ?? []);
        this.queueFailed.set(queue === null);
```

  - append at the end of the class, after `open(row: Pick<TimelineRow, 'entryId'>): void { … }`:

```typescript

  // ---- 待完成交易 -------------------------------------------------------------------------------------------

  /** One schedule action; on success the service bumps entriesChanged, which reloads the list and the 🔔. */
  private act(row: QueueRow, request: Observable<unknown>): void {
    this.busyInstance.set(row.id);
    this.actionError.set(null);
    request.subscribe({
      next: () => {
        this.busyInstance.set(null);
        this.confirmingSkip.set(null);
      },
      error: (error: unknown) => {
        this.busyInstance.set(null);
        this.actionError.set(scheduleActionError(error, this.toast));
      },
    });
  }

  post(row: QueueRow): void {
    this.act(row, this.accounting.postScheduleInstance(row.id));
  }

  askSkip(row: QueueRow): void {
    this.confirmingSkip.set(row.id);
  }

  skip(row: QueueRow): void {
    this.act(row, this.accounting.skipScheduleInstance(row.id));
  }

  catchUp(row: QueueRow): void {
    this.act(row, this.accounting.catchUpSchedule(row.definitionId));
  }

  repost(row: QueueRow): void {
    this.act(row, this.accounting.repostScheduleInstance(row.id, row.amounts));
  }

  acceptPartial(row: QueueRow): void {
    this.act(row, this.accounting.acceptPartialScheduleInstance(row.id));
  }

  /** Esc closes an open 略過 prompt first (marked handled so the layout's Esc does not also close the pane). */
  onKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape' && this.confirmingSkip() !== null) {
      event.preventDefault();
      this.confirmingSkip.set(null);
    }
  }
```

  Check after the edits: `grep -c 'filterDebtEntries\|debtCounterparties\|showDebtFilters\|debtFilteredOut\|setDebtCounterparty\|setDebtKind\|filters.scss' frontend/src/app/components/accounting/reminders/reminders.ts` prints the same number as before them, and `grep -c '待收\|待還' frontend/src/app/components/accounting/reminders/reminders.ts` prints `0`.

- [ ] 23.8 In `frontend/src/app/components/accounting/reminders/reminders.html`, insert before the line `    @if (empty()) {`:

```html
    @if (showQueue()) {
      @if (queueFailed()) {
        <p class="load-error queue-load-error">待完成交易讀取失敗，請稍後再試。</p>
      }
      @if (tab() === 'pending') {
        @if (queueGroups().due.length > 0) {
          <h3 class="section">已到期</h3>
          @for (row of queueGroups().due; track row.id) {
            <ng-container *ngTemplateOutlet="queueItem; context: { $implicit: row }" />
          }
        }
        @if (queueGroups().upcoming.length > 0) {
          <h3 class="section">即將到來</h3>
          @for (row of queueGroups().upcoming; track row.id) {
            <ng-container *ngTemplateOutlet="queueItem; context: { $implicit: row }" />
          }
        }
      } @else if (queueGroups().due.length > 0) {
        <h3 class="section">待完成交易</h3>
        @for (row of queueGroups().due; track row.id) {
          <ng-container *ngTemplateOutlet="queueItem; context: { $implicit: row }" />
        }
      }
      @if (actionError(); as message) {
        <p class="load-error action-error" role="alert">{{ message }}</p>
      }
    }
```

  and append at the end of the file (after `</section>`):

```html
<ng-template #queueItem let-row>
  <div class="queue-item" [attr.data-instance-id]="row.id" [attr.aria-busy]="busyInstance() === row.id">
    <span class="ico" [style.background]="row.color">{{ row.icon }}</span>
    <span class="name">{{ row.title }}</span>
    <span class="due" [class.overdue]="row.overdue" [class.today]="row.today">{{ row.overdueText ?? row.dueText }}</span>
    <span class="lines">
      @for (line of row.lines; track $index) {
        <span class="line">
          <small>{{ line.text }}</small>
          <span [class.neg]="line.tone === 'out'" [class.pos]="line.tone === 'in'">{{ line.amount }}</span>
        </span>
      }
    </span>
    <span class="total">{{ row.totalText }}</span>
    @if (row.error) {
      <span class="queue-error" role="alert">{{ row.error }}</span>
    }
    <span class="queue-actions">
      @if (row.partial) {
        <span class="badge partial">部分入帳</span>
        <button type="button" class="repost" [disabled]="busyInstance() === row.id" (click)="repost(row)">重新入帳</button>
        <button type="button" class="accept" [disabled]="busyInstance() === row.id" (click)="acceptPartial(row)">保留部分</button>
      } @else {
        @if (row.error) {
          <button type="button" class="retry" [disabled]="busyInstance() === row.id" (click)="post(row)">重試</button>
        } @else {
          <button type="button" class="post" [disabled]="busyInstance() === row.id" (click)="post(row)">入帳</button>
        }
        @if (confirmingSkip() === row.id) {
          <span class="skip-confirm">略過這一期？剩餘不變</span>
          <button type="button" class="skip-yes" [disabled]="busyInstance() === row.id" (click)="skip(row)">略過</button>
          <button type="button" class="skip-no" (click)="confirmingSkip.set(null)">取消</button>
        } @else {
          <button type="button" class="skip" [disabled]="busyInstance() === row.id" (click)="askSkip(row)">略過</button>
        }
        @if (row.offerCatchUp) {
          <button type="button" class="catch-up" [disabled]="busyInstance() === row.id" (click)="catchUp(row)">補入帳至今天</button>
        }
      }
    </span>
  </div>
</ng-template>
```

  (The 重試 button replaces 入帳 on a failing item, so the test that looks for 入帳 on a healthy item and the one that retries a failing item never see two posting buttons.)

- [ ] 23.9 Append to `frontend/src/app/components/accounting/reminders/reminders.scss`:

```scss
/* 待完成交易: one period per row; wraps at 390 px. */
.queue-item {
  align-items: center;
  border-top: 1px solid var(--app-border);
  display: grid;
  gap: 2px 10px;
  grid-template-areas: 'ico name due' 'ico lines total' 'ico error error' 'ico actions actions';
  grid-template-columns: 40px minmax(0, 1fr) auto;
  min-width: 0;
  padding: 8px 0;

  > .ico {
    align-items: center;
    border-radius: 50%;
    display: flex;
    font-size: 1.1rem;
    grid-area: ico;
    height: 38px;
    justify-content: center;
    width: 38px;
  }

  > .name {
    font-weight: 800;
    grid-area: name;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.queue-item .due {
  border-radius: var(--radius-pill);
  color: var(--app-text-muted);
  font-size: 0.68rem;
  font-weight: 800;
  grid-area: due;
  justify-self: end;
  padding: 1px 8px;
  white-space: nowrap;

  &.today {
    background: var(--app-state-warning-bg);
    color: var(--app-text);
  }

  &.overdue {
    background: var(--app-state-warning-bg);
    color: var(--tone-neg, var(--c-red));
  }
}

.queue-item .lines {
  display: flex;
  flex-direction: column;
  font-size: 0.75rem;
  grid-area: lines;
  min-width: 0;

  .line {
    display: flex;
    flex-wrap: wrap;
    gap: 0 6px;
  }

  small {
    color: var(--app-text-muted);
  }
}

.queue-item .total {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  grid-area: total;
  text-align: right;
}

.queue-error {
  color: var(--app-danger);
  font-size: 0.72rem;
  grid-area: error;
}

.queue-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  grid-area: actions;

  button {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: var(--radius-pill);
    color: var(--app-primary);
    cursor: pointer;
    font: inherit;
    font-size: var(--fs-label, 0.75rem);
    font-weight: 700;
    padding: 2px 12px;

    &:focus-visible {
      outline: 2px solid var(--app-primary);
      outline-offset: 1px;
    }

    &:disabled {
      opacity: 0.5;
    }
  }

  .skip-confirm {
    font-size: 0.75rem;
    font-weight: 700;
  }
}

.badge.partial {
  background: var(--app-state-warning-bg);
  border-radius: var(--radius-pill);
  font-size: 0.68rem;
  font-weight: 800;
  padding: 1px 8px;
}
```

- [ ] 23.10 Run 23.6 again.

Expected: every reminder test passes (the previous count + 10), the existing `借還款追蹤 filters` specs included.

- [ ] 23.11 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/reminders
git commit -m "feat(frontend): 待完成交易 tab in the reminder centre

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 24. 週期／分期 section and manage sheet

**Model:** opus

**Files:**
- Modify: `frontend/src/app/components/accounting/reminders/schedule-queue.ts` (`DefinitionRow`, `definitionRows`), `schedule-queue.spec.ts`
- Create: `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.ts`, `schedule-sheet.html`, `schedule-sheet.scss`, `schedule-sheet.spec.ts`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts`, `reminders.html`, `reminders.scss`, `reminders.spec.ts`
- Modify: `frontend/src/app/models/accounting.model.ts` (`ScheduleAmountScope`), `frontend/src/app/services/accounting.service.ts` (`updateScheduleInstance` body gains `scope`)

**Interfaces:**
- Consumes: `updateScheduleInstance` (Task 20; `scope` added here, Task 12's `InstanceUpdateIn.scope`), `getScheduleDefinitions`, `getScheduleDefinition`, `pauseSchedule`, `resumeSchedule`, `endSchedule`, `setScheduleMode`, `catchUpSchedule`, `deleteScheduleDefinition` (Task 20); `ruleSummary`, `scheduleProgress` (Task 20); `AccountingToastService`, `scheduleActionError`; `sheetKeyAction`, `focusSheetField` (`accounting-ui.ts`); `../../sheet.scss`; `makeDefinition`, `makeInstance`.
- Produces: `DefinitionRow` (`id, icon, color, name, nextText, progress, remainingText, modeBadge, paused, needsCheck, failing`), `definitionRows(definitions) -> { active: DefinitionRow[]; ended: DefinitionRow[] }`; `ScheduleSheetComponent` (`app-schedule-sheet`, input `definitionId`, output `closed`; 調整 / 儲存 on each listed period and the `套用範圖` scope sheet); `ScheduleAmountScope`; the reminder centre opens the sheet from a row or from `?schedule=<id>` and scrolls to `#schedules` for the fragment `schedules`.

Rules (spec "週期／分期 section"): below the counterparties of 借還款追蹤 (that tab only), every definition that is not ended, ordered by next due date (the server's order): icon, name, `下期 11/09`, `已入帳 k / N` (or the interval when unlimited), `剩餘 −$275,001` / `剩餘 $6,667`, badge 自動 or 提醒, 已暫停 when paused, 需檢查 when `needs_check`, and the failing period's error with 重試 (`catch-up`). A collapsed 已結束 row lists ended definitions (with 需檢查). The sheet shows the rule summary (`每月 9 號 · 36 期 · 自 2026/11/09`), the next three periods, 暫停 / 繼續 (繼續 asks `略過期間的 N 期` / `補入帳` when paused periods are overdue and sends `backlog`), 入帳方式 自動入帳 / 提醒入帳, 補入帳至今天 (when something is overdue and the schedule is not paused), 編輯 (`/accounting/entry?schedule=<id>`), 結束 (inline confirm `結束後未入帳的 N 期將刪除`) and 刪除 only while nothing was posted. Esc closes an open confirmation first, then the sheet; the overlay closes it; a request id drops a stale load; `import_running` shows the toast.

Period amounts (spec "週期／分期 section", "Instance amount edit scope", proposal decision 24): each listed pending period has 調整, which opens its amounts (one field per template line, labelled 每期金額, or 利息 for an `interest` line, pre-filled with the instance's `amounts`) with 取消 / 儲存. 儲存 with every amount unchanged closes the editor and sends nothing; an amount that is not an unsigned decimal with at most 4 decimals shows `金額格式不正確`; otherwise a sheet titled `套用範圖` asks `僅這一期` / `這一期與之後` / `全部週期` (focus on `僅這一期`), each sending `PUT …/instances/{id}` with `{ amounts, scope: 'this' | 'following' | 'all' }`, after which the sheet reloads (the service bump reloads the reminder centre). Keys follow `sheetKeyAction`: Esc closes the scope question first (cancel: nothing saved, focus back on 儲存), then the editor, then an open confirmation, then the sheet; ⏎ in an amount field saves. Imported definitions before cutover take these edits too (amounts are not rule fields; 編輯 still opens the lock banner).

- [ ] 24.1 Write the failing tests. Append to `frontend/src/app/components/accounting/reminders/schedule-queue.spec.ts` (add `makeDefinition` to its fixtures import and `definitionRows` to its `./schedule-queue` import):

```typescript
describe('definitionRows', () => {
  it('words a loan row and splits off ended definitions', () => {
    const loan = makeDefinition({
      id: 12, kind: 'installment', name: '信貸 每月還款', times: 36, posted_count: 3, next_due_date: '2027-02-09',
      remaining: '-275001.0000', repaid: '24999.0000', category_icon: '🏦',
    });
    const ended = makeDefinition({ id: 13, name: '舊貸款', status: 'ended', needs_check: true, next_due_date: null });
    const paused = makeDefinition({
      id: 14, name: 'Netflix', status: 'paused', posting_mode: 'confirm',
      failing: { instance_id: 5, due_date: '2026-10-01', last_error: 'lines[0].account_id: 帳戶已封存' },
    });
    const rows = definitionRows([loan, ended, paused]);
    expect(rows.active.map(row => row.id)).toEqual([12, 14]);
    expect(rows.ended.map(row => [row.id, row.needsCheck])).toEqual([[13, true]]);
    const [first, second] = rows.active;
    expect([first.name, first.nextText, first.progress, first.remainingText, first.modeBadge, first.paused]).toEqual([
      '信貸 每月還款', '下期 02/09', '已入帳 3 / 36', '剩餘 −$275,001', '自動', false,
    ]);
    expect([second.progress, second.modeBadge, second.paused, second.remainingText]).toEqual(['每月', '提醒', true, null]);
    expect(second.failing).toEqual({ instanceId: 5, text: 'lines[0].account_id: 帳戶已封存' });
  });
});
```

  Create `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScheduleDefinitionDetail } from '../../../../models/accounting.model';
import { makeDefinition, makeInstance } from '../../testing/fixtures';
import { ScheduleSheetComponent } from './schedule-sheet';

function detail(overrides: Partial<ScheduleDefinitionDetail> = {}): ScheduleDefinitionDetail {
  const base = makeDefinition({ id: 5, kind: 'installment', name: '信貸 每月還款', anchor_date: '2026-11-09', day_of_month: 9, times: 36 });
  const days = ['2026-11-09', '2026-12-09', '2027-01-09', '2027-02-09'];
  return {
    ...base,
    instances: days.map((day, index) =>
      makeInstance({ id: 100 + index, definition_id: 5, seq: index + 1, due_date: day, totals: [{ currency: 'TWD', amount: '-8953.0000' }] }),
    ),
    ...overrides,
  };
}

describe('ScheduleSheetComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ScheduleSheetComponent>;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 3, 12, 0, 0));
    TestBed.configureTestingModule({
      imports: [ScheduleSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });

  function render(body: ScheduleDefinitionDetail): HTMLElement {
    fixture = TestBed.createComponent(ScheduleSheetComponent);
    fixture.componentRef.setInput('definitionId', 5);
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(body);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function click(el: HTMLElement, selector: string): void {
    (el.querySelector(selector) as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('shows the rule summary and the next three periods', () => {
    const el = render(detail());
    expect(text(el.querySelector('h3'))).toBe('信貸 每月還款');
    expect(text(el.querySelector('.rule-summary'))).toBe('每月 9 號 · 36 期 · 自 2026/11/09');
    expect(Array.from(el.querySelectorAll('.next-period .date')).map(text)).toEqual(['11/09', '12/09', '01/09']);
    expect(text(el.querySelector('.next-period .amount'))).toBe('−$8,953');
  });

  it('pauses from the sheet', () => {
    // Spec "Pause from the sheet" (the row badge is checked in the reminder centre spec).
    const el = render(detail());
    click(el, '.sheet-pause');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/5/pause').flush(makeDefinition({ id: 5, status: 'paused' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ status: 'paused' }));
    fixture.detectChanges();
    expect(text(el.querySelector('.badge.paused'))).toBe('已暫停');
    expect(el.querySelector('.sheet-resume')).not.toBeNull();
  });

  it('asks how to resume when paused periods are overdue', () => {
    const overdue = detail({
      status: 'paused',
      instances: [
        makeInstance({ id: 1, definition_id: 5, due_date: '2026-09-09' }),
        makeInstance({ id: 2, definition_id: 5, due_date: '2026-10-01' }),
        makeInstance({ id: 3, definition_id: 5, due_date: '2026-11-09' }),
      ],
    });
    const el = render(overdue);
    click(el, '.sheet-resume');
    http.expectNone(r => r.url.endsWith('/resume'));
    expect(text(el.querySelector('.resume-skip'))).toBe('略過期間的 2 期');
    click(el, '.resume-post');
    const req = http.expectOne('/api/accounting/schedules/definitions/5/resume');
    expect(req.request.body).toEqual({ backlog: 'post' });
    req.flush(makeDefinition({ id: 5 }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail());
  });

  it('switches 入帳方式', () => {
    const el = render(detail());
    click(el, '.sheet-mode[data-mode="confirm"]');
    const req = http.expectOne('/api/accounting/schedules/definitions/5/mode');
    expect([req.request.method, req.request.body]).toEqual(['PUT', { posting_mode: 'confirm' }]);
    req.flush(makeDefinition({ id: 5, posting_mode: 'confirm' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ posting_mode: 'confirm' }));
  });

  it('confirms before ending and says how many periods go', () => {
    const el = render(detail());
    click(el, '.sheet-end');
    expect(text(el.querySelector('.end-confirm'))).toBe('結束後未入帳的 4 期將刪除');
    http.expectNone(r => r.url.endsWith('/end'));
    click(el, '.sheet-end');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/5/end').flush(makeDefinition({ id: 5, status: 'ended' }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/schedules/definitions/5').flush(detail({ status: 'ended', instances: [] }));
  });

  it('offers 刪除 only while nothing was posted, and closes after it', () => {
    const posted = render(detail({ posted_count: 2 }));
    expect(posted.querySelector('.sheet-delete')).toBeNull();
    fixture.destroy();
    const el = render(detail({ posted_count: 0 }));
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    click(el, '.sheet-delete');
    click(el, '.sheet-delete');
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/schedules/definitions/5').flush(null);
    fixture.detectChanges();
    expect(closed).toHaveBeenCalled();
  });

  it('closes on Esc and opens the entry form for 編輯', () => {
    const el = render(detail());
    const closed = vi.fn();
    fixture.componentInstance.closed.subscribe(closed);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    click(el, '.sheet-edit');
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { schedule: 5 } });
    el.querySelector('.sheet')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(closed).toHaveBeenCalledTimes(2);
  });

  describe('period amounts and 套用範圖 (proposal decision 24)', () => {
    function openEditor(el: HTMLElement): HTMLInputElement {
      click(el, '.next-period .period-edit');
      return el.querySelector('.period-editor input') as HTMLInputElement;
    }

    function type(input: HTMLInputElement, value: string): void {
      input.value = value;
      input.dispatchEvent(new Event('input'));
      fixture.detectChanges();
    }

    it('asks 套用範圖 only when an amount changed', () => {
      const el = render(detail());
      openEditor(el);
      click(el, '.period-save');
      expect(el.querySelector('.period-editor')).toBeNull();
      expect(el.querySelector('.scope-sheet')).toBeNull();
      http.expectNone(r => r.method === 'PUT');
      type(openEditor(el), '420');
      click(el, '.period-save');
      expect(text(el.querySelector('.scope-sheet h4'))).toBe('套用範圖');
      expect(Array.from(el.querySelectorAll('.scope-sheet button')).map(text)).toEqual(['僅這一期', '這一期與之後', '全部週期']);
      http.expectNone(r => r.method === 'PUT');
    });

    for (const [selector, scope] of [
      ['.scope-this', 'this'],
      ['.scope-following', 'following'],
      ['.scope-all', 'all'],
    ] as const) {
      it(`sends scope ${scope} from ${selector}`, () => {
        // Spec "Price change from this period on" (following) and its siblings.
        const el = render(detail());
        type(openEditor(el), '420');
        click(el, '.period-save');
        click(el, selector);
        const req = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/instances/100');
        expect(req.request.body).toEqual({ amounts: ['420'], scope });
        req.flush(makeInstance({ id: 100, definition_id: 5, amounts: ['420'] }));
        fixture.detectChanges();
        http.expectOne('/api/accounting/schedules/definitions/5').flush(detail());
        fixture.detectChanges();
        expect(el.querySelector('.scope-sheet')).toBeNull();
        expect(el.querySelector('.period-editor')).toBeNull();
      });
    }

    it('focuses 僅這一期, and Esc cancels without saving or closing the sheet', async () => {
      // Spec "Scope question cancelled".
      const el = render(detail());
      const closed = vi.fn();
      fixture.componentInstance.closed.subscribe(closed);
      type(openEditor(el), '420');
      click(el, '.period-save');
      await vi.waitFor(() => {
        fixture.detectChanges();
        expect(document.activeElement).toBe(el.querySelector('.scope-sheet .scope-this'));
      });
      el.querySelector('.scope-sheet')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      fixture.detectChanges();
      expect(el.querySelector('.scope-sheet')).toBeNull();
      expect(el.querySelector('.period-editor')).not.toBeNull();
      expect(closed).not.toHaveBeenCalled();
      http.expectNone(r => r.method === 'PUT');
    });
  });
});
```

  In `frontend/src/app/components/accounting/reminders/reminders.spec.ts` add `ScheduleDefinition` to the model import and `makeDefinition` to the fixtures import; extend `flushLoad` with a fifth parameter `definitions: ScheduleDefinition[] = []` and, at its end,

```typescript
    http.expectOne(r => r.url === '/api/accounting/schedules/definitions').flush(definitions);
```

  extend `render` the same way (fifth parameter `definitions: ScheduleDefinition[] = []` passed to `flushLoad`), and append inside the top-level `describe`:

```typescript
  describe('週期／分期', () => {
    const LOAN = makeDefinition({
      id: 12, kind: 'installment', name: '信貸 每月還款', times: 36, posted_count: 3, next_due_date: '2027-02-09',
      remaining: '-275001.0000', repaid: '24999.0000',
    });

    function row(el: HTMLElement, id: number): HTMLElement {
      return el.querySelector(`.schedule-row[data-definition-id="${id}"]`) as HTMLElement;
    }

    it('words the loan row on 借還款追蹤 only', () => {
      // Spec "Loan row".
      const { fixture, el } = render([], [], [], [], [LOAN]);
      expect(row(el, 12)).toBeNull();
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      const loan = row(el, 12);
      expect(text(loan.querySelector('.name'))).toBe('信貸 每月還款');
      expect(text(loan.querySelector('.next'))).toBe('下期 02/09');
      expect(text(loan.querySelector('.progress'))).toBe('已入帳 3 / 36');
      expect(text(loan.querySelector('.remaining'))).toBe('剩餘 −$275,001');
      expect(text(loan.querySelector('.badge.mode'))).toBe('自動');
    });

    it('marks an ended loan that is still open under 已結束', () => {
      // Spec "Ended loan still open needs a check".
      const ended = makeDefinition({ id: 13, name: '舊貸款', status: 'ended', needs_check: true, next_due_date: null });
      const { fixture, el } = render([], [], [], [], [LOAN, ended]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(row(el, 13)?.closest('details.ended-schedules')).not.toBeNull();
      expect(text(row(el, 13).querySelector('.badge.check'))).toBe('需檢查');
    });

    it('retries a failing schedule with catch-up', () => {
      const failing = makeDefinition({ id: 14, failing: { instance_id: 5, due_date: '2026-10-01', last_error: 'lines[0].account_id: 帳戶已封存' } });
      const { fixture, el } = render([], [], [], [], [failing]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(text(el.querySelector('.schedule-failing .msg'))).toBe('lines[0].account_id: 帳戶已封存');
      (el.querySelector('.schedule-failing .retry') as HTMLButtonElement).click();
      http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/14/catch-up')
        .flush({ posted: [5], failed: null, definition: failing });
      fixture.detectChanges();
      flushLoad([], [], [], [], []);
    });

    it('opens the manage sheet from a row and shows 已暫停 after a pause', () => {
      // Spec "Pause from the sheet".
      const { fixture, el } = render([], [], [], [], [LOAN]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      row(el, 12).click();
      fixture.detectChanges();
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, instances: [] });
      fixture.detectChanges();
      (el.querySelector('app-schedule-sheet .sheet-pause') as HTMLButtonElement).click();
      http.expectOne('/api/accounting/schedules/definitions/12/pause').flush({ ...LOAN, status: 'paused' });
      fixture.detectChanges();
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, status: 'paused', instances: [] });
      flushLoad([], [], [], [], [{ ...LOAN, status: 'paused' }]);
      fixture.detectChanges();
      expect(text(row(el, 12).querySelector('.badge.paused'))).toBe('已暫停');
    });

    it('does not say 目前沒有待處理項目 on 借還款追蹤 while schedules are listed', () => {
      // No open counterparty, one live schedule: the 週期／分期 section is the page's content.
      const { fixture, el } = render([], [], [], [], [LOAN]);
      tab(el, '借還款追蹤').click();
      fixture.detectChanges();
      expect(row(el, 12)).not.toBeNull();
      expect(el.querySelector('.empty')).toBeNull();
    });

    it('opens the sheet from ?schedule= on 借還款追蹤', async () => {
      await TestBed.inject(Router).navigateByUrl('/?tab=debts&schedule=12');
      const { fixture, el } = render([], [], [], [], [LOAN]);
      http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...LOAN, instances: [] });
      fixture.detectChanges();
      expect(text(el.querySelector('.tabs [aria-checked="true"]'))).toBe('借還款追蹤');
      expect(text(el.querySelector('app-schedule-sheet h3'))).toBe('信貸 每月還款');
    });
  });
```

- [ ] 24.2 Run them.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/reminders/**/*.spec.ts'
```

Expected: build fails (`definitionRows` and `./schedule-sheet` do not exist).

- [ ] 24.3 Append to `frontend/src/app/components/accounting/reminders/schedule-queue.ts` (and extend its imports to `import { ScheduleDefinition, ScheduleInstance } from '../../../models/accounting.model';` and add `import { scheduleProgress } from '../schedule-math';`):

```typescript
export interface DefinitionRow {
  id: number;
  icon: string;
  color: string;
  name: string;
  /** `下期 11/09`; null when nothing is pending. */
  nextText: string | null;
  /** `已入帳 3 / 36`, or `每月` / `每 2 週` for an unlimited schedule. */
  progress: string;
  /** `剩餘 −$275,001` (loan) / `剩餘 $6,667` (card installment); null otherwise. */
  remainingText: string | null;
  modeBadge: '自動' | '提醒';
  paused: boolean;
  needsCheck: boolean;
  failing: { instanceId: number; text: string } | null;
}

function definitionRow(definition: ScheduleDefinition): DefinitionRow {
  const currency = definition.template.lines[0]?.currency ?? 'TWD';
  return {
    id: definition.id,
    icon: definition.category_icon ?? (definition.kind === 'installment' ? '💳' : '🔁'),
    color: definition.category_color ?? 'var(--app-surface-soft)',
    name: definition.name,
    nextText: definition.next_due_date ? `下期 ${shortDate(definition.next_due_date)}` : null,
    progress: scheduleProgress(definition),
    remainingText: definition.remaining === null ? null : `剩餘 ${formatMoney(definition.remaining, currency)}`,
    modeBadge: definition.posting_mode === 'auto' ? '自動' : '提醒',
    paused: definition.status === 'paused',
    needsCheck: definition.needs_check,
    failing: definition.failing ? { instanceId: definition.failing.instance_id, text: definition.failing.last_error } : null,
  };
}

/** The 週期／分期 section: live definitions in the server's order (next due date first), ended ones apart. */
export function definitionRows(definitions: ScheduleDefinition[]): { active: DefinitionRow[]; ended: DefinitionRow[] } {
  return {
    active: definitions.filter(definition => definition.status !== 'ended').map(definitionRow),
    ended: definitions.filter(definition => definition.status === 'ended').map(definitionRow),
  };
}
```

- [ ] 24.4 The scope in the service. In `frontend/src/app/models/accounting.model.ts` add, below `ScheduleActor`,

```typescript
/** `PUT /schedules/instances/{id}` `scope` (套用範圖): 僅這一期 / 這一期與之後 / 全部週期. */
export type ScheduleAmountScope = 'this' | 'following' | 'all';
```

  and in `frontend/src/app/services/accounting.service.ts` add `ScheduleAmountScope` to the model import and replace the signature `updateScheduleInstance(id: number, body: { due_date?: string; amounts?: string[] }): Observable<ScheduleInstance> {` with `updateScheduleInstance(id: number, body: { due_date?: string; amounts?: string[]; scope?: ScheduleAmountScope }): Observable<ScheduleInstance> {` (the body is sent as given; Task 20's service spec stays green).

  Then create `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.ts`:

```typescript
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { Router } from '@angular/router';
import { Observable } from 'rxjs';

import { ScheduleAmountScope, ScheduleDefinitionDetail, SchedulePostingMode } from '../../../../models/accounting.model';
import { AccountingService } from '../../../../services/accounting.service';
import { AccountingToastService, scheduleActionError } from '../../accounting-toast';
import { focusSheetField, sheetKeyAction } from '../../accounting-ui';
import { shortDate, todayIso } from '../../dates';
import { formatSigned } from '../../format';
import { ruleSummary } from '../../schedule-math';

type SheetConfirm = 'resume' | 'end' | 'delete' | null;

/** The manage sheet of one definition (spec "週期／分期 section"). */
@Component({
  selector: 'app-schedule-sheet',
  standalone: true,
  templateUrl: './schedule-sheet.html',
  styleUrls: ['../../sheet.scss', './schedule-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class ScheduleSheetComponent {
  private readonly accounting = inject(AccountingService);
  private readonly router = inject(Router);
  private readonly toast = inject(AccountingToastService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly destroyed = signal(false);
  private requestId = 0;

  readonly definitionId = input.required<number>();
  readonly closed = output<void>();

  readonly definition = signal<ScheduleDefinitionDetail | null>(null);
  readonly loadFailed = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly confirm = signal<SheetConfirm>(null);
  readonly today = signal(todayIso());
  /** 調整 (proposal decision 24): the pending period whose amounts are open, the draft, and the 套用範圖 question. */
  readonly editingId = signal<number | null>(null);
  readonly editAmounts = signal<string[]>([]);
  readonly askScope = signal(false);
  readonly editLabels = computed(() =>
    (this.definition()?.template.lines ?? []).map(line => (line.kind === 'interest' ? '利息' : '每期金額')),
  );

  readonly summary = computed(() => {
    const definition = this.definition();
    return definition ? ruleSummary(definition) : '';
  });
  private readonly pending = computed(() => (this.definition()?.instances ?? []).filter(item => item.status === 'pending'));
  readonly nextPeriods = computed(() =>
    this.pending()
      .slice(0, 3)
      .map(item => ({
        id: item.id,
        date: shortDate(item.due_date),
        amount: item.totals.map(total => formatSigned(total.amount, total.currency)).join(' · '),
      })),
  );
  /** Pending periods due today or earlier: 補入帳至今天, and the N of 略過期間的 N 期. */
  readonly overdue = computed(() => this.pending().filter(item => item.due_date <= this.today()).length);
  readonly pendingCount = computed(() => this.pending().length);
  readonly canDelete = computed(() => this.definition()?.posted_count === 0);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.destroyed.set(true));
    effect(() => {
      const id = this.definitionId();
      untracked(() => this.load(id));
    });
    focusSheetField(this.host.nativeElement, '.sheet button', this.injector);
  }

  private load(id: number): void {
    const request = ++this.requestId;
    this.today.set(todayIso());
    this.accounting.getScheduleDefinition(id).subscribe({
      next: definition => {
        if (request === this.requestId && !this.destroyed()) {
          this.definition.set(definition);
          this.loadFailed.set(false);
        }
      },
      error: () => {
        if (request === this.requestId && !this.destroyed()) {
          this.loadFailed.set(true);
        }
      },
    });
  }

  /** One action; on success the sheet reloads (or `after` runs), and the service bump reloads the reminder centre. */
  private act(request: Observable<unknown>, after?: () => void): void {
    this.busy.set(true);
    this.error.set(null);
    request.subscribe({
      next: () => {
        this.busy.set(false);
        this.confirm.set(null);
        if (after) {
          after();
        } else {
          this.load(this.definitionId());
        }
      },
      error: (error: unknown) => {
        this.busy.set(false);
        this.error.set(scheduleActionError(error, this.toast));
      },
    });
  }

  pause(): void {
    this.act(this.accounting.pauseSchedule(this.definitionId()));
  }

  /** 繼續: with overdue paused periods the owner chooses 略過期間的 N 期 (default) or 補入帳. */
  resume(backlog?: 'skip' | 'post'): void {
    if (backlog === undefined && this.overdue() > 0) {
      this.confirm.set('resume');
      return;
    }
    this.act(this.accounting.resumeSchedule(this.definitionId(), backlog ?? 'skip'));
  }

  setMode(mode: SchedulePostingMode): void {
    if (this.definition()?.posting_mode !== mode) {
      this.act(this.accounting.setScheduleMode(this.definitionId(), mode));
    }
  }

  catchUp(): void {
    this.act(this.accounting.catchUpSchedule(this.definitionId()));
  }

  edit(): void {
    const id = this.definitionId();
    this.closed.emit();
    void this.router.navigate(['/accounting/entry'], { queryParams: { schedule: id } });
  }

  end(): void {
    if (this.confirm() !== 'end') {
      this.confirm.set('end');
      return;
    }
    this.act(this.accounting.endSchedule(this.definitionId()));
  }

  remove(): void {
    if (this.confirm() !== 'delete') {
      this.confirm.set('delete');
      return;
    }
    this.act(this.accounting.deleteScheduleDefinition(this.definitionId()), () => this.closed.emit());
  }

  /** 調整 on a listed pending period: its amounts, one field per template line. */
  editPeriod(id: number): void {
    const period = this.pending().find(item => item.id === id);
    if (!period) {
      return;
    }
    this.confirm.set(null);
    this.error.set(null);
    this.askScope.set(false);
    this.editingId.set(id);
    this.editAmounts.set([...period.amounts]);
    focusSheetField(this.host.nativeElement, '.period-editor input', this.injector);
  }

  setEditAmount(index: number, value: string): void {
    this.editAmounts.update(amounts => amounts.map((amount, i) => (i === index ? value.trim() : amount)));
  }

  cancelEdit(): void {
    this.askScope.set(false);
    this.editingId.set(null);
  }

  /** 儲存: nothing changed → close without a request; a changed amount → ask 套用範圖 before anything is sent. */
  saveEdit(): void {
    const period = this.pending().find(item => item.id === this.editingId());
    if (!period) {
      return;
    }
    const amounts = this.editAmounts();
    if (amounts.some(amount => !/^\d+(\.\d{1,4})?$/.test(amount))) {
      this.error.set('金額格式不正確');
      return;
    }
    this.error.set(null);
    if (amounts.every((amount, index) => Number(amount) === Number(period.amounts[index]))) {
      this.cancelEdit();
      return;
    }
    this.askScope.set(true);
    focusSheetField(this.host.nativeElement, '.scope-sheet .scope-this', this.injector);
  }

  /** 僅這一期 / 這一期與之後 / 全部週期 → `PUT …/instances/{id}` with `scope`; the sheet then reloads. */
  applyScope(scope: ScheduleAmountScope): void {
    const id = this.editingId();
    if (id === null) {
      return;
    }
    this.act(this.accounting.updateScheduleInstance(id, { amounts: this.editAmounts(), scope }), () => {
      this.cancelEdit();
      this.load(this.definitionId());
    });
  }

  close(): void {
    this.closed.emit();
  }

  /**
   * Handled at the host so the page never sees the key. Esc closes, in order: the 套用範圖 question (nothing is saved),
   * the period editor, an open confirmation, the sheet. ⏎ in an amount field saves the period.
   */
  onKeydown(event: KeyboardEvent): void {
    const action = sheetKeyAction(event, this.host.nativeElement.querySelector('.sheet'));
    if (action === 'confirm') {
      if (this.editingId() !== null && !this.askScope()) {
        this.saveEdit();
      }
      return;
    }
    if (action !== 'close') {
      return;
    }
    if (this.askScope()) {
      this.askScope.set(false);
      focusSheetField(this.host.nativeElement, '.period-save', this.injector);
    } else if (this.editingId() !== null) {
      this.cancelEdit();
    } else if (this.confirm() !== null) {
      this.confirm.set(null);
    } else {
      this.closed.emit();
    }
  }
}
```

- [ ] 24.5 Create `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.html`:

```html
<div class="overlay" (click)="close()">
  <div class="sheet schedule-sheet" role="dialog" aria-modal="true" aria-labelledby="schedule-sheet-title" (click)="$event.stopPropagation()">
    <div class="grab"></div>
    @if (definition(); as d) {
      <h3 id="schedule-sheet-title">{{ d.name }}</h3>
      <p class="rule-summary">{{ summary() }}</p>
      @if (d.status === 'paused') {
        <p class="badge paused">已暫停</p>
      }
      @if (d.needs_check) {
        <p class="badge check">需檢查</p>
      }
      <div class="next-periods">
        @for (period of nextPeriods(); track period.id) {
          <div class="next-period">
            <span class="date">{{ period.date }}</span><span class="amount">{{ period.amount }}</span>
            <button type="button" class="period-edit" [disabled]="busy()" [attr.aria-label]="'調整 ' + period.date" (click)="editPeriod(period.id)">調整</button>
          </div>
          @if (editingId() === period.id) {
            <div class="period-editor">
              @for (label of editLabels(); track $index) {
                <label class="period-amount">
                  <span>{{ label }}</span>
                  <input type="text" inputmode="decimal" [value]="editAmounts()[$index]" (input)="setEditAmount($index, $any($event.target).value)" />
                </label>
              }
              <div class="btnrow">
                <button type="button" class="period-cancel" (click)="cancelEdit()">取消</button>
                <button type="button" class="period-save" [disabled]="busy()" (click)="saveEdit()">儲存</button>
              </div>
              @if (askScope()) {
                <div class="scope-sheet" role="dialog" aria-modal="true" aria-labelledby="scope-sheet-title">
                  <h4 id="scope-sheet-title">套用範圖</h4>
                  <div class="btnrow scope-actions">
                    <button type="button" class="scope-this" [disabled]="busy()" (click)="applyScope('this')">僅這一期</button>
                    <button type="button" class="scope-following" [disabled]="busy()" (click)="applyScope('following')">這一期與之後</button>
                    <button type="button" class="scope-all" [disabled]="busy()" (click)="applyScope('all')">全部週期</button>
                  </div>
                </div>
              }
            </div>
          }
        } @empty {
          <p class="muted">沒有待入帳的期別</p>
        }
      </div>
      <div class="kv">
        <div>
          <span class="k">狀態</span>
          <span class="v">
            @if (d.status === 'active') {
              <button type="button" class="sheet-pause" [disabled]="busy()" (click)="pause()">暫停</button>
            }
            @if (d.status === 'paused') {
              <button type="button" class="sheet-resume" [disabled]="busy()" (click)="resume()">繼續</button>
            }
          </span>
        </div>
        @if (confirm() === 'resume') {
          <div class="resume-choice">
            <button type="button" class="resume-skip" [disabled]="busy()" (click)="resume('skip')">略過期間的 {{ overdue() }} 期</button>
            <button type="button" class="resume-post" [disabled]="busy()" (click)="resume('post')">補入帳</button>
          </div>
        }
        <div>
          <span class="k">入帳方式</span>
          <span class="v sheet-modes" role="radiogroup" aria-label="入帳方式">
            <button type="button" role="radio" class="sheet-mode" data-mode="auto" [attr.aria-checked]="d.posting_mode === 'auto'" [disabled]="busy()" (click)="setMode('auto')">自動入帳</button>
            <button type="button" role="radio" class="sheet-mode" data-mode="confirm" [attr.aria-checked]="d.posting_mode === 'confirm'" [disabled]="busy()" (click)="setMode('confirm')">提醒入帳</button>
          </span>
        </div>
      </div>
      @if (error(); as message) {
        <p class="form-error" role="alert">{{ message }}</p>
      }
      @if (confirm() === 'end') {
        <p class="end-confirm" role="alert">結束後未入帳的 {{ pendingCount() }} 期將刪除</p>
      }
      @if (confirm() === 'delete') {
        <p class="delete-confirm" role="alert">刪除這個排程？</p>
      }
      <div class="btnrow sheet-actions">
        @if (d.status !== 'paused' && overdue() > 0) {
          <button type="button" class="sheet-catch-up" [disabled]="busy()" (click)="catchUp()">補入帳至今天</button>
        }
        <button type="button" class="sheet-edit" (click)="edit()">編輯</button>
        @if (d.status !== 'ended') {
          <button type="button" class="danger sheet-end" [disabled]="busy()" (click)="end()">{{ confirm() === 'end' ? '確定結束' : '結束' }}</button>
        }
        @if (canDelete()) {
          <button type="button" class="danger sheet-delete" [disabled]="busy()" (click)="remove()">{{ confirm() === 'delete' ? '確定刪除' : '刪除' }}</button>
        }
      </div>
    } @else if (loadFailed()) {
      <p class="load-error">排程讀取失敗，請稍後再試。</p>
    } @else {
      <p class="muted">讀取中…</p>
    }
  </div>
</div>
```

- [ ] 24.6 Create `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.scss`:

```scss
.next-period .period-edit {
  background: none;
  border: 1px solid var(--app-border);
  border-radius: var(--radius-pill);
  color: var(--app-primary);
  font-size: 0.72rem;
  margin-left: 8px;
  padding: 0 10px;
}

.period-editor {
  border: 1px solid var(--app-border);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 2px 0 6px;
  padding: 8px;
}

.period-amount {
  align-items: center;
  display: flex;
  gap: 8px;
  justify-content: space-between;

  input {
    font-variant-numeric: tabular-nums;
    min-width: 0;
    text-align: right;
    width: 8rem;
  }
}

/* 套用範圖: three buttons that wrap at 390 px. */
.scope-sheet {
  border-top: 1px solid var(--app-border);
  padding-top: 6px;

  h4 {
    font-size: 0.85rem;
    margin: 0 0 6px;
    text-align: center;
  }
}

.scope-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
}

.rule-summary {
  color: var(--app-text-muted);
  font-size: 0.8rem;
  margin: 0 0 6px;
  text-align: center;
}

.badge {
  border-radius: var(--radius-pill);
  display: inline-block;
  font-size: 0.68rem;
  font-weight: 800;
  margin: 0 4px 6px 0;
  padding: 1px 8px;

  &.paused {
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
  }

  &.check {
    background: var(--app-state-warning-bg);
    color: var(--tone-neg, var(--c-red));
  }
}

.next-periods {
  border-top: 1px solid var(--app-border);
  display: flex;
  flex-direction: column;
  padding: 4px 0;
}

.next-period {
  display: flex;
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
  justify-content: space-between;
  padding: 2px 0;
}

.sheet-modes,
.resume-choice,
.sheet-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.sheet-mode[aria-checked='true'] {
  border-color: var(--app-primary);
  font-weight: 700;
}

.end-confirm,
.delete-confirm,
.form-error {
  color: var(--app-danger);
  font-size: 0.8rem;
  margin: 6px 0;
}

.muted,
.load-error {
  color: var(--app-text-muted);
  font-size: var(--fs-sm, 0.85rem);
  text-align: center;
}
```

- [ ] 24.7 Edit `frontend/src/app/components/accounting/reminders/reminders.ts`:
  - imports: add `afterNextRender, ElementRef, Injector` to the `@angular/core` import; add `ScheduleDefinition` to the model import; replace `import { QueueRow, queueRows } from './schedule-queue';` with `import { DefinitionRow, QueueRow, definitionRows, queueRows } from './schedule-queue';` and add `import { ScheduleSheetComponent } from './schedule-sheet/schedule-sheet';`; add `ScheduleSheetComponent` to the component's `imports`;
  - add members below `readonly actionError = signal<string | null>(null);`:

```typescript
  readonly definitions = signal<ScheduleDefinition[]>([]);
  readonly definitionsFailed = signal(false);
  /** The definition whose manage sheet is open (a row tap or `?schedule=<id>`). */
  readonly sheetId = signal<number | null>(null);
  readonly scheduleRows = computed(() => definitionRows(this.definitions()));
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private scrollToSchedules = false;
```

  - in the constructor's `queryParamMap` subscription, after the tab handling, add

```typescript
      const schedule = params.get('schedule');
      if (schedule !== null && Number(schedule) > 0) {
        this.sheetId.set(Number(schedule));
      }
```

  and below that subscription add

```typescript
    this.route.fragment.pipe(takeUntilDestroyed()).subscribe(fragment => {
      this.scrollToSchedules = fragment === 'schedules';
    });
```

  - in `load()`, add `definitions: this.accounting.getScheduleDefinitions().pipe(catchError(() => of(null))),` to the `forkJoin` object, `definitions` to the destructured `next` argument, and after `this.queueFailed.set(queue === null);`:

```typescript
        this.definitions.set(definitions ?? []);
        this.definitionsFailed.set(definitions === null);
        if (this.scrollToSchedules) {
          this.scrollToSchedules = false;
          afterNextRender(() => this.host.nativeElement.querySelector('#schedules')?.scrollIntoView?.({ block: 'start' }), {
            injector: this.injector,
          });
        }
```

  - in `empty()`, replace `    return cards === 0 && debts === 0 && queue === 0 && !failed && (!this.showCards() || this.billsSettled());` with

```typescript
    // 借還款追蹤 also lists 週期／分期: live or ended definitions (or their failed load) are not "nothing pending".
    const schedules = this.tab() === 'debts' ? this.scheduleRows().active.length + this.scheduleRows().ended.length : 0;
    const schedulesFailed = this.tab() === 'debts' && this.definitionsFailed();
    return (
      cards === 0 && debts === 0 && queue === 0 && schedules === 0 && !failed && !schedulesFailed &&
      (!this.showCards() || this.billsSettled())
    );
```

  - add methods below `acceptPartial`:

```typescript
  openSchedule(id: number): void {
    this.sheetId.set(id);
  }

  closeSchedule(): void {
    this.sheetId.set(null);
  }

  /** 重試 on a failing definition row: 補入帳至今天 posts the failing period (and the ones after it) again. */
  retryDefinition(row: DefinitionRow): void {
    this.actionError.set(null);
    this.accounting.catchUpSchedule(row.id).subscribe({
      error: (error: unknown) => this.actionError.set(scheduleActionError(error, this.toast)),
    });
  }
```

- [ ] 24.8 In `frontend/src/app/components/accounting/reminders/reminders.html`, insert directly after the closing `}` of the `@if (showDebts() && debtRows().length > 0) { … }` block (before the `@if (showQueue())` block of Task 23):

```html
    @if (tab() === 'debts') {
      <h3 class="section" id="schedules">週期／分期</h3>
      @if (definitionsFailed()) {
        <p class="load-error">週期／分期讀取失敗，請稍後再試。</p>
      }
      @for (row of scheduleRows().active; track row.id) {
        <ng-container *ngTemplateOutlet="scheduleRow; context: { $implicit: row }" />
        @if (row.failing; as failing) {
          <div class="schedule-failing" role="alert">
            <span class="msg">{{ failing.text }}</span>
            <button type="button" class="retry" (click)="retryDefinition(row)">重試</button>
          </div>
        }
      } @empty {
        <p class="muted schedules-empty">沒有週期或分期</p>
      }
      @if (scheduleRows().ended.length > 0) {
        <details class="ended-schedules">
          <summary>已結束（{{ scheduleRows().ended.length }}）</summary>
          @for (row of scheduleRows().ended; track row.id) {
            <ng-container *ngTemplateOutlet="scheduleRow; context: { $implicit: row }" />
          }
        </details>
      }
    }
```

  append after the `queueItem` template at the end of the file:

```html
<ng-template #scheduleRow let-row>
  <button type="button" class="schedule-row" [attr.data-definition-id]="row.id" (click)="openSchedule(row.id)">
    <span class="ico" [style.background]="row.color">{{ row.icon }}</span>
    <span class="name">{{ row.name }}</span>
    @if (row.nextText) {
      <span class="next">{{ row.nextText }}</span>
    }
    <span class="facts">
      <span class="progress">{{ row.progress }}</span>
      @if (row.remainingText) {
        <span class="remaining">{{ row.remainingText }}</span>
      }
      <span class="badge mode">{{ row.modeBadge }}</span>
      @if (row.paused) {
        <span class="badge paused">已暫停</span>
      }
      @if (row.needsCheck) {
        <span class="badge check">需檢查</span>
      }
    </span>
  </button>
</ng-template>

@if (sheetId(); as id) {
  <app-schedule-sheet [definitionId]="id" (closed)="closeSchedule()" />
}
```

- [ ] 24.9 Append to `frontend/src/app/components/accounting/reminders/reminders.scss`:

```scss
/* 週期／分期: same grid as the debt rows; facts wrap under the name at 390 px. */
.schedule-row {
  align-items: center;
  background: none;
  border: 0;
  border-top: 1px solid var(--app-border);
  color: inherit;
  cursor: pointer;
  display: grid;
  font: inherit;
  gap: 2px 10px;
  grid-template-areas: 'ico name next' 'ico facts facts';
  grid-template-columns: 40px minmax(0, 1fr) auto;
  min-width: 0;
  padding: 8px 0;
  text-align: left;
  width: 100%;

  &:focus-visible {
    outline: 2px solid var(--app-primary);
    outline-offset: 2px;
  }

  > .ico {
    align-items: center;
    border-radius: 50%;
    display: flex;
    font-size: 1.1rem;
    grid-area: ico;
    height: 38px;
    justify-content: center;
    width: 38px;
  }

  > .name {
    font-weight: 800;
    grid-area: name;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  > .next {
    color: var(--app-text-muted);
    font-size: 0.72rem;
    grid-area: next;
    white-space: nowrap;
  }

  > .facts {
    align-items: baseline;
    display: flex;
    flex-wrap: wrap;
    font-size: 0.75rem;
    gap: 0 8px;
    grid-area: facts;
    min-width: 0;
  }

  .remaining {
    font-variant-numeric: tabular-nums;
    font-weight: 800;
  }

  .badge {
    border-radius: var(--radius-pill);
    font-size: 0.68rem;
    font-weight: 800;
    padding: 0 8px;
  }

  .badge.mode,
  .badge.paused {
    background: var(--app-surface-soft);
    color: var(--app-text-muted);
  }

  .badge.check {
    background: var(--app-state-warning-bg);
    color: var(--tone-neg, var(--c-red));
  }
}

.schedule-failing {
  align-items: center;
  background: var(--app-state-warning-bg);
  border-radius: var(--radius-sm);
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  justify-content: space-between;
  margin: 2px 0 6px 50px;
  padding: 4px 10px;

  .msg {
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }

  .retry {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: var(--radius-pill);
    color: var(--app-primary);
    cursor: pointer;
    font: inherit;
    font-size: var(--fs-label, 0.75rem);
    font-weight: 700;
    padding: 2px 12px;
  }
}

.ended-schedules summary {
  color: var(--app-text-muted);
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 700;
  padding: 6px 0;
}
```

- [ ] 24.10 Run 24.2 again.

Expected: every spec under `reminders/` passes (schedule-queue 5, schedule-sheet 12, reminders the Task 23 count + 6).

- [ ] 24.11 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/reminders frontend/src/app/models/accounting.model.ts \
  frontend/src/app/services/accounting.service.ts
git commit -m "feat(frontend): 週期／分期 section and manage sheet (period amounts with 套用範圖) under 借還款追蹤

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 25. Bell count and schedule pills (timeline, passbook, reminder rows)

**Model:** sonnet

**Files:**
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts`, `timeline.spec.ts`
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.ts`, `account-entries.html`, `account-entries.spec.ts`

**Interfaces:**
- Consumes: `getScheduleInstances` (Task 20), `schedulePill` (Task 20), `LedgerEntry.schedule`, `makeInstance`.
- Produces: `schedulePills(entry) -> TimelinePill[]` (exported from `timeline.ts`; `buildDays` puts it first on every row, so the reminder centre's expanded rows get it too); the timeline's `queue` signal and `loadQueue()`; `reminderCount` adds the queue items due today or earlier plus the partial ones.

Rules (spec "Reminder bell count", "Schedule pills"): 🔔 = cards whose current statement has a remaining balance + counterparties with a non-zero open count + 待完成交易 queue items (`queue=true`, which already excludes paused and ended definitions) that are due today or earlier or are partial. The aria-label keeps `提醒中心，N 項`. The queue is re-read with the counterparties (entry and account writes, every schedule write); a failed read keeps the last known queue (the bell is a hint). Pills: `週期 #k/N` / `週期 #k` / `分期 #k/N`, plus `部分` (review tone) for a partial period, on timeline rows (single, group and transfer rows), passbook rows and the reminder centre's rows.

- [ ] 25.1 Update and extend `frontend/src/app/components/accounting/timeline/timeline.spec.ts`:
  - add `ScheduleInstance` to the model import and `makeInstance` to the fixtures import; import `schedulePills` next to `buildDays` from `./timeline`;
  - give `render` a sixth parameter `queue: ScheduleInstance[] = []` and pass it on: `flushCounterparties(counterparties, queue);`
  - replace `flushCounterparties` with

```typescript
  /** The 🔔's counterparties and 待完成交易 queue: read on init and again after every entry or account write. */
  function flushCounterparties(body: Counterparty[] = [], queue: ScheduleInstance[] = []): void {
    httpMock!.expectOne('/api/accounting/counterparties').flush(body);
    const req = httpMock!.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(req.request.params.get('queue')).toBe('true');
    req.flush(queue);
  }
```

  - inside `describe('billing hints', …)`, after the test `counts unpaid cards and counterparties with an open amount, and opens the reminder centre`, add

```typescript
    it('counts 待完成交易 items due today or earlier', () => {
      // Spec "Bell includes due confirm items" (today here is 2026-10-02: 10-01 is due, 10-20 is not).
      const queue = [makeInstance({ id: 1, due_date: '2026-10-01' }), makeInstance({ id: 2, due_date: '2026-10-20' })];
      const rendered = render('phone', makePreference(), [], [...ACCOUNTS, CARD], [], queue);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      flushBill('-12345.0000');
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('2');
      expect(bell(rendered.el).getAttribute('aria-label')).toBe('提醒中心，2 項');
    });

    it('counts a partial period until it is accepted', () => {
      // Spec "Partial period counted until accepted".
      const partial = makeInstance({ id: 5, status: 'posted', is_partial: true, due_date: '2026-11-09' });
      const rendered = render('phone', makePreference(), [], ACCOUNTS, [], [partial]);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('1');
      TestBed.inject(AccountingService).acceptPartialScheduleInstance(5).subscribe();
      httpMock!.expectOne(r => r.url.endsWith('/instances/5/accept-partial')).flush(partial);
      rendered.fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([], []);
      rendered.fixture.detectChanges();
      expect(rendered.el.querySelector('.bell-count')).toBeNull();
    });

    it('stops counting the item of a definition the owner paused', () => {
      // Spec "Paused definition not counted".
      const due = makeInstance({ id: 6, definition_id: 3, due_date: '2026-10-01' });
      const rendered = render('phone', makePreference(), [], ACCOUNTS, [], [due]);
      flushSummary('2026-10');
      flushEntries([]);
      rendered.fixture.detectChanges();
      expect(text(rendered.el.querySelector('.bell-count'))).toBe('1');
      TestBed.inject(AccountingService).pauseSchedule(3).subscribe();
      httpMock!.expectOne(r => r.url.endsWith('/definitions/3/pause')).flush({});
      rendered.fixture.detectChanges();
      flushSummary('2026-10');
      flushEntries([]);
      flushCounterparties([], []);
      rendered.fixture.detectChanges();
      expect(rendered.el.querySelector('.bell-count')).toBeNull();
    });
```

  - inside `describe('buildDays', …)` add

```typescript
  const link = (overrides: Record<string, unknown>) =>
    ({ definition_id: 3, instance_id: 9, kind: 'installment', seq: 5, times: 36, name: '分期', is_partial: false, acted_by: 'auto',
       posted_entry_ids: [1], ...overrides }) as LedgerEntry['schedule'];

  it('puts the schedule pill first, in MOZE wording', () => {
    // Spec "Installment pill", "Unlimited recurring pill".
    const [installment] = buildDays([makeEntry({ id: 1, schedule: link({}) })], 'TWD', false);
    expect(installment.rows[0].pills[0]).toEqual({ label: '分期 #5/36', tone: '' });
    expect(schedulePills(makeEntry({ schedule: link({ kind: 'recurring', seq: 25, times: null }) }))).toEqual([
      { label: '週期 #25', tone: '' },
    ]);
    const transfer = buildDays(
      [makeEntry({ id: 3, kind: 'transfer_out', amount: '-15000.0000', transfer_group_id: 't-9', schedule: link({ kind: 'recurring', seq: 2, times: 12 }) })],
      'TWD',
      false,
    );
    expect(transfer[0].rows[0].pills.map(pill => pill.label)).toEqual(['週期 #2/12', '轉帳']);
  });

  it('adds 部分 to a partial period', () => {
    expect(schedulePills(makeEntry({ schedule: link({ seq: 1, is_partial: true }) }))).toEqual([
      { label: '分期 #1/36', tone: '' },
      { label: '部分', tone: 'review' },
    ]);
  });
```

- [ ] 25.2 Add the passbook test to `frontend/src/app/components/accounting/account-entries/account-entries.spec.ts`, inside its `describe`:

```typescript
  it('shows the schedule pill on a scheduled row', () => {
    const scheduled = entry(9, {
      schedule: { definition_id: 3, instance_id: 4, kind: 'recurring', seq: 25, times: null, name: 'Netflix', is_partial: false,
        acted_by: 'auto', posted_entry_ids: [9] },
    });
    const fixture = render({ items: [scheduled], total: 1, limit: PAGE_SIZE, offset: 0 });
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.schedule-pill')?.textContent?.trim()).toBe('週期 #25');
  });
```

- [ ] 25.3 Run both specs.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/timeline/timeline.spec.ts' --include='src/app/components/accounting/account-entries/account-entries.spec.ts'
```

Expected: build fails on `schedulePills` (not exported); after that, every timeline test would fail on the missing queue request.

- [ ] 25.4 Edit `frontend/src/app/components/accounting/timeline/timeline.ts`:
  - add `ScheduleInstance` to the model import and `import { schedulePill } from '../schedule-math';`;
  - add above `pillsOf`:

```typescript
/** `分期 #5/36` / `週期 #25` (and `部分` while the period is partial) for an entry a schedule wrote. */
export function schedulePills(entry: Pick<LedgerEntry, 'schedule'>): TimelinePill[] {
  const link = entry.schedule;
  if (!link) {
    return [];
  }
  const pills: TimelinePill[] = [{ label: schedulePill(link), tone: '' }];
  if (link.is_partial) {
    pills.push({ label: '部分', tone: 'review' });
  }
  return pills;
}
```

  - in `pillsOf`, replace `const pills: TimelinePill[] = [];` with `const pills: TimelinePill[] = schedulePills(entry);`;
  - in `buildDays`, replace `pills: [{ label: '轉帳', tone: '' }],` with `pills: [...schedulePills(out), { label: '轉帳', tone: '' }],`;
  - add below `private counterpartyRequestId = 0;`:

```typescript
  /** For the 🔔 count only: the 待完成交易 queue (`queue=true`). */
  private readonly queue = signal<ScheduleInstance[]>([]);
  private queueRequestId = 0;
```

  - replace the body of `reminderCount` with

```typescript
  readonly reminderCount = computed(
    () =>
      this.reminderEvents().filter(event => this.openBill(event) !== null).length +
      this.counterparties().filter(counterparty => (counterparty.open_count ?? 0) > 0).length +
      this.queue().filter(item => item.is_partial || item.due_date <= this.today()).length,
  );
```

  - in the effect that calls `untracked(() => this.loadCounterparties());`, replace that line with

```typescript
      untracked(() => {
        this.loadCounterparties();
        this.loadQueue();
      });
```

  - add below `loadCounterparties()`:

```typescript
  private loadQueue(): void {
    const id = ++this.queueRequestId;
    this.accounting.getScheduleInstances({ queue: true }).subscribe({
      next: items => {
        if (id === this.queueRequestId) {
          this.queue.set(items);
        }
      },
      // Swallowed like the counterparties: the 🔔 is a hint; the reminder centre reports its own load errors.
      error: () => undefined,
    });
  }
```

  Also update the doc comment above `reminderCount` to read: `🔔 count: cards with something left to pay, counterparties with open debt rows, and 待完成交易 items due today or earlier or partial (paused and ended definitions are not in the queue).`

- [ ] 25.5 Edit `frontend/src/app/components/accounting/account-entries/account-entries.ts`: add `import { schedulePill } from '../schedule-math';` and the member `readonly schedulePill = schedulePill;`. In `account-entries.html`, insert as the first child of `<span class="pills">`:

```html
          @if (entry.schedule; as link) {
            <span class="pill schedule-pill">{{ schedulePill(link) }}</span>
            @if (link.is_partial) {
              <span class="pill review-marker">部分</span>
            }
          }
```

- [ ] 25.6 Run 25.3 again, then the reminder centre spec (its expanded rows use `buildDays`).

```bash
cd /home/opc/workspace/home-hub-schedules/frontend
npx ng test --watch=false --include='src/app/components/accounting/timeline/timeline.spec.ts' --include='src/app/components/accounting/account-entries/account-entries.spec.ts'
npx ng test --watch=false --include='src/app/components/accounting/reminders/**/*.spec.ts' 2>&1 | tail -3
```

Expected: all pass (timeline + 5, passbook + 1); the reminders specs unchanged.

- [ ] 25.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/timeline frontend/src/app/components/accounting/account-entries
git commit -m "feat(frontend): bell counts due 待完成交易 items; schedule pills on rows

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 26. Entry detail: loan line, pill, edit scope, repost, partial actions, delete wording

**Model:** opus

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts`, `entry-detail.html`, `entry-detail.scss`
- Test: `frontend/src/app/components/accounting/entry-detail/entry-detail-schedule.spec.ts`

**Interfaces:**
- Consumes: `getScheduleDefinition`, `repostScheduleInstance`, `acceptPartialScheduleInstance` (Task 20); `schedulePill` (Task 20); `AccountingToastService`, `scheduleActionError`; `EntryDetail.schedule`, `EntryDetail.loan_schedule`; `shortDate`; the reminder centre's `?tab=debts&schedule=<id>` (Task 24) and the entry form's `?schedule=<id>` (Task 22).
- Produces: `DetailPanel` gains `'scope' | 'repost'`; signals `scheduleDefinition`, `repostAmounts`; computed `scheduleLink`, `schedulePillText`, `loanLine`, `loanLinkText`, `formEditable`, `repostLines`, `scheduleDeleteNote`; methods `openSchedule(definitionId)`, `editOne()`, `editSchedule()`, `setRepostAmount(index, value)`, `submitRepost()`, `repostPartial()`, `acceptPartial()`.

Rules (spec "Scheduled entry edit scope", "Loan breakdown in entry detail", "Schedule pills", "Import-running feedback"):
- A payable / receivable original with `loan_schedule` shows `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09` (下期 left out when nothing is pending) and a link `信貸 每月還款 · 已入帳 3 / 36` to `/accounting/reminders?tab=debts&schedule=<id>` (the manage sheet), instead of the 原始 · 已還 breakdown and instead of main's plain `剩餘 …` span (剩餘 appears once, signed); a receivable original (a `collection` loan, money lent) reads `已收` instead of `已還`.
- An entry with `schedule` shows its pill (`分期 #5/36`, `週期 #25`) linking to the same sheet; a partial period adds the badge `部分` and the actions 重新入帳 (the repost panel) and 保留部分 (`accept-partial`).
- 編輯 on a scheduled entry (enabled also for its settlement rows) opens the scope choice 編輯這一筆 / 編輯整個排程 after reading the definition (request-id guarded). 編輯這一筆: the normal edit for kinds the entry form edits (expense, income, receivable, payable that is not a settlement, transfer legs) unless the entry is a member of the period's `entry_group` (the form would save it through `PUT /splits/{id}`, which the scheduled-group guard refuses with 409), else the repost panel with the period's line amounts → `repost`, then the page follows the entry that replaced this one (same position in `posted_entry_ids`). 編輯整個排程: `/accounting/entry?schedule=<id>`; for a locked (imported, before cutover) definition the panel shows `MOZE 匯入資料，切換後可編輯` instead.
- 刪除 on a scheduled entry: the confirmation names the period's other entries that stay (`同期的 利息 −$620 會保留，此期標示為部分入帳`), or, for the period's last entry, `此期將回到待完成交易` (HomeHub posted it) / `此期將標示為略過` (`acted_by = import`); the 刪除整組 choice is not offered for a period's group.
- Every write's error goes through `scheduleActionError`: `import_running` → the toast, the page unchanged. Esc closes any panel; ⏎ in the repost panel's fields submits it.

- [ ] 26.1 Write the failing test `frontend/src/app/components/accounting/entry-detail/entry-detail-schedule.spec.ts`:

```typescript
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EntryDetail, EntryScheduleLink } from '../../../models/accounting.model';
import { AccountingToastService } from '../accounting-toast';
import { makeAccount, makeDefinition, makeEntry, makeEntryDetail, makeInstance } from '../testing/fixtures';
import { EntryDetailComponent } from './entry-detail';

const ACCOUNTS = [makeAccount({ id: 1, name: '薪轉' })];

function link(overrides: Partial<EntryScheduleLink> = {}): EntryScheduleLink {
  return {
    definition_id: 12, instance_id: 77, kind: 'installment', seq: 1, times: 36, name: '信貸 每月還款', is_partial: false,
    acted_by: 'auto', posted_entry_ids: [42, 43], ...overrides,
  };
}

const REPAYMENT = makeEntryDetail({
  id: 42, kind: 'payable', amount: '-8333.0000', is_settlement: true, name: '信貸 每月還款', account_id: 1, account_name: '薪轉',
  schedule: link(), group: { id: 5, kind: 'installment', name: '信貸 每月還款 #1/36', merchant: null, description: null, count: 2, total: '-8953.0000', currency: 'TWD' },
  group_members: [
    makeEntry({ id: 42, kind: 'payable', amount: '-8333.0000' }),
    makeEntry({ id: 43, kind: 'interest', amount: '-620.0000' }),
  ],
});

const DEFINITION = {
  ...makeDefinition({
    id: 12, kind: 'installment', name: '信貸 每月還款', times: 36,
    template: {
      lines: [
        { ...makeDefinition().template.lines[0], kind: 'repayment', account_id: 1, amount: '8333', loan_entry_id: 4021 },
        { ...makeDefinition().template.lines[0], kind: 'interest', account_id: 1, amount: '620' },
      ],
      description: null,
      tags: [],
    },
  }),
  instances: [makeInstance({ id: 77, definition_id: 12, status: 'posted', amounts: ['8333', '620'], posted_entry_ids: [42, 43] })],
};

describe('EntryDetailComponent schedules', () => {
  let http: HttpTestingController;
  let navigate: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [EntryDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: of(convertToParamMap({ id: '42' })) } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
    navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true) as unknown as ReturnType<typeof vi.fn>;
  });

  afterEach(() => http.verify());

  function render(body: EntryDetail): { fixture: ComponentFixture<EntryDetailComponent>; el: HTMLElement } {
    const fixture = TestBed.createComponent(EntryDetailComponent);
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(body);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush(ACCOUNTS);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  function text(node: Element | null | undefined): string {
    return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }

  function click(fixture: ComponentFixture<EntryDetailComponent>, selector: string): void {
    ((fixture.nativeElement as HTMLElement).querySelector(selector) as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('shows the loan line and links to its schedule', () => {
    // Spec "Loan detail line".
    const loan = makeEntryDetail({
      id: 42, kind: 'payable', amount: '300000.0000', open_amount: '275001.0000', is_settled: false, name: '信貸',
      settled_by: [makeEntry({ id: 50, kind: 'payable', amount: '-24999.0000', is_settlement: true })],
      loan_schedule: {
        definition_id: 12, name: '信貸 每月還款', status: 'active', posting_mode: 'auto', posted_count: 3, times: 36,
        next_due_date: '2027-02-09', next_amount: [{ currency: 'TWD', amount: '-8953.0000' }], remaining: '-275001.0000',
        repaid: '24999.0000', needs_check: false,
      },
    });
    const { fixture, el } = render(loan);
    expect(text(el.querySelector('.loan-line'))).toBe('剩餘 −$275,001 · 已還 $24,999 · 下期 02/09');
    expect(text(el.querySelector('.loan-link'))).toBe('信貸 每月還款 · 已入帳 3 / 36');
    expect(el.querySelector('.breakdown')).toBeNull();
    expect(el.querySelector('.open-amount')).toBeNull(); // 剩餘 once, signed, in the loan line
    click(fixture, '.loan-link');
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: 12 } });
  });

  it('words a lent-money loan with 已收', () => {
    const lent = makeEntryDetail({
      id: 42, kind: 'receivable', amount: '-50000.0000', open_amount: '30000.0000', is_settled: false, name: '借款',
      loan_schedule: {
        definition_id: 13, name: '借款 每月收回', status: 'active', posting_mode: 'auto', posted_count: 4, times: 10,
        next_due_date: null, next_amount: [], remaining: '30000.0000', repaid: '20000.0000', needs_check: false,
      },
    });
    const { el } = render(lent);
    expect(text(el.querySelector('.loan-line'))).toBe('剩餘 $30,000 · 已收 $20,000');
  });

  it('sends a member of a scheduled split group to the repost panel, never to the entry form', () => {
    // Two plain lines post an entry_group of kind split; the entry form would save a member with PUT /splits/{id},
    // which assert_group_not_scheduled refuses (409, D29), so 編輯這一筆 goes to the repost panel.
    const member = makeEntryDetail({
      id: 42, kind: 'expense', amount: '-390.0000', name: '串流組合', account_id: 1, account_name: '薪轉',
      schedule: link({ kind: 'recurring', times: null, name: '串流組合', posted_entry_ids: [42, 44] }),
      group: { id: 6, kind: 'split', name: '串流組合 #1', merchant: null, description: null, count: 2, total: '-539.0000', currency: 'TWD' },
      group_members: [makeEntry({ id: 42, amount: '-390.0000' }), makeEntry({ id: 44, amount: '-149.0000' })],
    });
    const { fixture, el } = render(member);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...DEFINITION, kind: 'recurring' });
    fixture.detectChanges();
    click(fixture, '.scope-one');
    expect(navigate).not.toHaveBeenCalledWith(['/accounting/entries', 42, 'edit']);
    expect(el.querySelectorAll('.repost-amount').length).toBe(2);
  });

  it('shows the 分期 pill and opens its schedule', () => {
    const { fixture, el } = render(REPAYMENT);
    expect(text(el.querySelector('.schedule-pill'))).toBe('分期 #1/36');
    click(fixture, '.schedule-pill');
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: 12 } });
  });

  it('offers 重新入帳 and 保留部分 on a partial period', () => {
    // Spec "Partial period pill".
    const { fixture, el } = render({ ...REPAYMENT, schedule: link({ is_partial: true, posted_entry_ids: [42] }) });
    expect(text(el.querySelector('.schedule-pill .badge.partial'))).toBe('部分');
    expect(el.querySelector('.partial-repost')).not.toBeNull();
    click(fixture, '.partial-accept');
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/instances/77/accept-partial').flush(makeInstance({ id: 77 }));
    fixture.detectChanges();
    http.expectOne('/api/accounting/entries/42').flush(REPAYMENT);
  });

  it("corrects one period's interest with 編輯這一筆", () => {
    // Spec "Correct one period's interest".
    const { fixture, el } = render(REPAYMENT);
    expect((el.querySelector('.action-edit') as HTMLButtonElement).disabled).toBe(false);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    click(fixture, '.scope-one');
    const inputs = Array.from(el.querySelectorAll<HTMLInputElement>('.repost-amount'));
    expect(inputs.map(input => input.value)).toEqual(['8333', '620']);
    inputs[1].value = '598';
    inputs[1].dispatchEvent(new Event('input'));
    fixture.detectChanges();
    click(fixture, '.repost-submit');
    const req = http.expectOne('/api/accounting/schedules/instances/77/repost');
    expect(req.request.body).toEqual({ amounts: ['8333', '598'] });
    req.flush(makeInstance({ id: 77, status: 'posted', posted_entry_ids: [201, 202] }));
    fixture.detectChanges();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 201], { replaceUrl: true });
  });

  it('opens the whole schedule in the entry form, or shows the lock for an imported one', () => {
    const { fixture } = render(REPAYMENT);
    click(fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush(DEFINITION);
    fixture.detectChanges();
    click(fixture, '.scope-all');
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], { queryParams: { schedule: 12 } });

    const locked = render(REPAYMENT);
    click(locked.fixture, '.action-edit');
    http.expectOne('/api/accounting/schedules/definitions/12').flush({ ...DEFINITION, imported: true, locked: true });
    locked.fixture.detectChanges();
    expect(locked.el.querySelector('.scope-all')).toBeNull();
    expect(text(locked.el.querySelector('.scope-locked'))).toBe('MOZE 匯入資料，切換後可編輯');
  });

  it("words what deleting does to the entry's period", () => {
    // Spec "Deleting a MOZE-booked period's last entry" and the 部分入帳 wording.
    const partial = render(REPAYMENT);
    click(partial.fixture, '.action-delete');
    expect(text(partial.el.querySelector('.schedule-delete-note'))).toBe('同期的 利息 −$620 會保留，此期標示為部分入帳');
    expect(partial.el.querySelector('.delete-group')).toBeNull();

    const booked = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', acted_by: 'import', posted_entry_ids: [42] }) }));
    click(booked.fixture, '.action-delete');
    expect(text(booked.el.querySelector('.schedule-delete-note'))).toBe('此期將標示為略過');

    const homehub = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', posted_entry_ids: [42] }) }));
    click(homehub.fixture, '.action-delete');
    expect(text(homehub.el.querySelector('.schedule-delete-note'))).toBe('此期將回到待完成交易');
  });

  it('shows the toast when an import holds the period', () => {
    const { fixture, el } = render(makeEntryDetail({ id: 42, amount: '-390.0000', schedule: link({ kind: 'recurring', posted_entry_ids: [42] }) }));
    click(fixture, '.action-delete');
    click(fixture, '.delete-entry');
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/entries/42').flush(
      { code: 409, message: 'import_running', trace_id: 't' }, { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();
    expect(TestBed.inject(AccountingToastService).message()).toBe('匯入進行中，請稍後再試');
    expect(el.querySelector('.form-error')).toBeNull();
    expect(el.querySelector('.detail-name')).not.toBeNull();
  });
});
```

- [ ] 26.2 Run it.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/entry-detail/entry-detail-schedule.spec.ts'
```

Expected: 9 failures (no `.loan-line`, `.schedule-pill`, `.scope-one`, no definitions request …).

- [ ] 26.3 Edit `frontend/src/app/components/accounting/entry-detail/entry-detail.ts`:
  - imports: add `ScheduleDefinitionDetail, ScheduleLineKind` to the model import; add `import { AccountingToastService, scheduleActionError } from '../accounting-toast';` and `import { schedulePill } from '../schedule-math';`;
  - replace `export type DetailPanel = 'settle' | 'refund' | 'delete' | null;` with `export type DetailPanel = 'settle' | 'refund' | 'delete' | 'scope' | 'repost' | null;`;
  - add above the component: 

```typescript
const LINE_LABELS: Record<ScheduleLineKind, string> = {
  expense: '支出', income: '收入', receivable: '應收款項', payable: '應付款項', transfer: '轉帳', repayment: '還款',
  collection: '收款', interest: '利息',
};
const FORM_KINDS: ReadonlySet<string> = new Set(['expense', 'income', 'receivable', 'payable']);
```

  - add members below `readonly busy = signal(false);`:

```typescript
  private readonly toast = inject(AccountingToastService);
  /** The definition of the shown entry's period (scope choice, repost amounts); request-id guarded. */
  readonly scheduleDefinition = signal<ScheduleDefinitionDetail | null>(null);
  readonly repostAmounts = signal<string[]>([]);
  private scheduleRequest = 0;

  readonly scheduleLink = computed(() => this.detail()?.schedule ?? null);
  readonly schedulePillText = computed(() => {
    const link = this.scheduleLink();
    return link ? schedulePill(link) : null;
  });
  /** `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09` for a loan a schedule repays. */
  readonly loanLine = computed(() => {
    const detail = this.detail();
    const loan = detail?.loan_schedule;
    if (!detail || !loan || loan.remaining === null) {
      return null;
    }
    const back = detail.kind === 'receivable' ? '已收' : '已還'; // a collection loan (money lent) comes back
    const parts = [`剩餘 ${formatMoney(loan.remaining, detail.currency)}`, `${back} ${formatMoney(loan.repaid ?? 0, detail.currency)}`];
    if (loan.next_due_date) {
      parts.push(`下期 ${shortDate(loan.next_due_date)}`);
    }
    return parts.join(' · ');
  });
  readonly loanLinkText = computed(() => {
    const loan = this.detail()?.loan_schedule;
    if (!loan) {
      return null;
    }
    return `${loan.name} · 已入帳 ${loan.posted_count}${loan.times === null ? '' : ` / ${loan.times}`}`;
  });
  /** Kinds the entry form edits (a settlement is edited by repost instead). */
  readonly formEditable = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return false;
    }
    if (detail.schedule && detail.group) {
      // A period's group member: the form saves it with PUT /splits/{id}, which a scheduled group refuses (409, D29).
      return false;
    }
    return (FORM_KINDS.has(detail.kind) && !detail.is_settlement) || detail.kind === 'transfer_out' || detail.kind === 'transfer_in';
  });
  readonly repostLines = computed(() =>
    (this.scheduleDefinition()?.template.lines ?? []).map((line, index) => ({ index, label: LINE_LABELS[line.kind], currency: line.currency })),
  );
  /** What deleting this entry does to its period (spec "Scheduled entry edit scope"). */
  readonly scheduleDeleteNote = computed(() => {
    const detail = this.detail();
    const link = detail?.schedule;
    if (!detail || !link) {
      return null;
    }
    const gone = new Set<number>([detail.id]);
    if (detail.transfer_counterpart) {
      gone.add(detail.transfer_counterpart.id);
    }
    const others = link.posted_entry_ids.filter(id => !gone.has(id));
    if (others.length === 0) {
      return link.acted_by === 'import' ? '此期將標示為略過' : '此期將回到待完成交易';
    }
    const named = (detail.group_members ?? [])
      .filter(member => others.includes(member.id))
      .map(member => `${this.kindLabels[member.kind]} ${formatMoney(member.amount, member.currency)}`);
    return `同期的 ${named.length > 0 ? named.join('、') : `其他 ${others.length} 筆`} 會保留，此期標示為部分入帳`;
  });
```

  - replace `canEdit` with

```typescript
  /** 收款 / 還款 rows cannot be edited by the form (`PUT` answers 422 `kind`); a scheduled one is (編輯這一筆 reposts it). */
  readonly canEdit = computed(() => {
    const detail = this.detail();
    return !!detail && !this.locked() && (!detail.is_settlement || !!detail.schedule);
  });
```

  - in `runWrite`, replace `this.formError.set(writeErrorMessage(err));` with `this.formError.set(scheduleActionError(err, this.toast));`;
  - replace `edit()` with

```typescript
  edit(): void {
    const detail = this.detail();
    if (!detail || !this.canEdit()) {
      return;
    }
    if (detail.schedule) {
      this.formError.set(null);
      this.panel.set('scope');
      this.loadSchedule();
      return;
    }
    this.router.navigate(['/accounting/entries', detail.id, 'edit']);
  }

  /** The definition of the period (its lines and this period's amounts). */
  private loadSchedule(): void {
    const link = this.scheduleLink();
    if (!link) {
      return;
    }
    const request = ++this.scheduleRequest;
    this.scheduleDefinition.set(null);
    this.service
      .getScheduleDefinition(link.definition_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: definition => {
          if (request !== this.scheduleRequest) {
            return;
          }
          this.scheduleDefinition.set(definition);
          const instance = definition.instances.find(item => item.id === link.instance_id);
          this.repostAmounts.set(instance ? [...instance.amounts] : definition.template.lines.map(line => line.amount));
        },
        error: () => {
          if (request === this.scheduleRequest) {
            this.formError.set('排程讀取失敗，請稍後再試。');
          }
        },
      });
  }

  /** The manage sheet of the schedule in 提醒中心 › 借還款追蹤. */
  openSchedule(definitionId: number): void {
    this.router.navigate(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: definitionId } });
  }

  /** 編輯這一筆: the entry form for kinds it edits, else the repost panel with the period's amounts. */
  editOne(): void {
    const detail = this.routedDetail();
    if (!detail) {
      return;
    }
    if (this.formEditable()) {
      this.router.navigate(['/accounting/entries', detail.id, 'edit']);
      return;
    }
    this.panel.set('repost');
  }

  /** 編輯整個排程: the entry form in definition mode (not for an imported definition before cutover). */
  editSchedule(): void {
    const link = this.scheduleLink();
    const definition = this.scheduleDefinition();
    if (!link || !definition || definition.locked) {
      return;
    }
    this.router.navigate(['/accounting/entry'], { queryParams: { schedule: link.definition_id } });
  }

  setRepostAmount(index: number, value: string): void {
    this.repostAmounts.update(list => list.map((amount, at) => (at === index ? value.trim() : amount)));
  }

  /** 重新入帳: the period's entries are replaced; the page follows the entry that took this one's place. */
  submitRepost(): void {
    const detail = this.routedDetail();
    const link = this.scheduleLink();
    if (!detail || !link) {
      return;
    }
    const position = Math.max(link.posted_entry_ids.indexOf(detail.id), 0);
    this.busy.set(true);
    this.formError.set(null);
    this.service
      .repostScheduleInstance(link.instance_id, this.repostAmounts())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: instance => {
          this.busy.set(false);
          this.panel.set(null);
          const next = instance.posted_entry_ids[position] ?? instance.posted_entry_ids[0];
          if (next !== undefined) {
            this.router.navigate(['/accounting/entries', next], { replaceUrl: true });
          }
        },
        error: (error: unknown) => {
          this.busy.set(false);
          this.formError.set(scheduleActionError(error, this.toast));
        },
      });
  }

  /** 重新入帳 on a partial period: the repost panel with the period's current amounts. */
  repostPartial(): void {
    this.formError.set(null);
    this.panel.set('repost');
    this.loadSchedule();
  }

  /** 保留部分: keep what is left of the period. */
  acceptPartial(): void {
    const detail = this.routedDetail();
    const link = this.scheduleLink();
    if (!detail || !link) {
      return;
    }
    const id = detail.id;
    this.runWrite(this.service.acceptPartialScheduleInstance(link.instance_id), id, () => this.load(id, true));
  }
```

  - in `onKeydown`, replace

```typescript
      (this.panel() === 'settle' || this.panel() === 'refund') &&
```

  with

```typescript
      (this.panel() === 'settle' || this.panel() === 'refund' || this.panel() === 'repost') &&
```

  and inside that branch replace

```typescript
        this.submitForm();
```

  with

```typescript
        if (this.panel() === 'repost') {
          this.submitRepost();
        } else {
          this.submitForm();
        }
```

- [ ] 26.4 Edit `frontend/src/app/components/accounting/entry-detail/entry-detail.html`:
  - after the closing `</div>` of `<div class="dtitle">…</div>` insert

```html
    @if (scheduleLink(); as link) {
      <div class="schedule-line">
        <button type="button" class="schedule-pill" (click)="openSchedule(link.definition_id)">
          {{ schedulePillText() }}
          @if (link.is_partial) {
            <span class="badge partial">部分</span>
          }
        </button>
        @if (link.is_partial && !locked()) {
          <button type="button" class="partial-repost" [disabled]="busy()" (click)="repostPartial()">重新入帳</button>
          <button type="button" class="partial-accept" [disabled]="busy()" (click)="acceptPartial()">保留部分</button>
        }
      </div>
    }
```

  - replace

```html
        } @else {
          <span class="open-amount">剩餘 {{ formatMoney(d.open_amount, d.currency) }}</span>
        }
```

  with (the loan line below carries 剩餘, signed; showing both would print 剩餘 twice)

```html
        } @else if (!loanLine()) {
          <span class="open-amount">剩餘 {{ formatMoney(d.open_amount, d.currency) }}</span>
        }
```

  - replace

```html
        @if (breakdown(); as line) {
          <p class="breakdown">{{ line }}</p>
        }
```

  with

```html
        @if (loanLine(); as line) {
          <p class="loan-line">{{ line }}</p>
          @if (d.loan_schedule; as loan) {
            <button type="button" class="loan-link" (click)="openSchedule(loan.definition_id)">{{ loanLinkText() }}</button>
          }
        } @else if (breakdown(); as line) {
          <p class="breakdown">{{ line }}</p>
        }
```

  - in the delete panel, replace the first `@if (d.group) {` (the one before `<p>整組「…`) with `@if (d.group && !d.schedule) {`, the second `@if (d.group) {` (before the `delete-group` button) with `@if (d.group && !d.schedule) {`, and insert right after `<p><b>確定刪除這筆記錄？</b></p>`:

```html
        @if (scheduleDeleteNote(); as note) {
          <p class="schedule-delete-note">{{ note }}</p>
        }
```

  - insert before `<nav class="actions" aria-label="記錄動作">`:

```html
    @if (panel() === 'scope') {
      <div class="inline-form scope-choice" role="group" aria-label="編輯範圍">
        <h4>編輯</h4>
        <div class="btnrow">
          <button type="button" class="scope-one" (click)="editOne()">編輯這一筆</button>
          @if (scheduleDefinition()?.locked) {
            <p class="scope-locked">MOZE 匯入資料，切換後可編輯</p>
          } @else {
            <button type="button" class="scope-all" [disabled]="!scheduleDefinition()" (click)="editSchedule()">編輯整個排程</button>
          }
        </div>
        @if (formError()) {
          <p class="form-error" role="alert">{{ formError() }}</p>
        }
        <div class="btnrow">
          <button type="button" (click)="panel.set(null)">取消</button>
        </div>
      </div>
    }

    @if (panel() === 'repost') {
      <div class="inline-form repost-form" role="group" aria-label="重新入帳">
        <h4>{{ schedulePillText() }} · 重新入帳</h4>
        @for (line of repostLines(); track line.index) {
          <label>
            <span>{{ line.label }}</span>
            <input class="repost-amount" inputmode="decimal" [attr.data-index]="line.index" [value]="repostAmounts()[line.index] ?? ''" (input)="setRepostAmount(line.index, $any($event.target).value)" />
            <small>{{ line.currency }}</small>
          </label>
        }
        @if (formError()) {
          <p class="form-error" role="alert">{{ formError() }}</p>
        }
        <div class="btnrow">
          <button type="button" (click)="panel.set(null)">取消</button>
          <button type="button" class="pri repost-submit" [disabled]="busy() || repostLines().length === 0" (click)="submitRepost()">儲存</button>
        </div>
      </div>
    }
```

- [ ] 26.5 Append to `frontend/src/app/components/accounting/entry-detail/entry-detail.scss`:

```scss
.schedule-line {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 4px 0 8px;

  button {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: var(--radius-pill);
    color: var(--app-primary);
    cursor: pointer;
    font: inherit;
    font-size: var(--fs-label, 0.75rem);
    font-weight: 700;
    padding: 2px 10px;

    &:focus-visible {
      outline: 2px solid var(--app-primary);
      outline-offset: 1px;
    }
  }

  .schedule-pill {
    background: var(--app-surface-soft);
    color: var(--app-text);
  }

  .badge.partial {
    background: var(--app-state-warning-bg);
    border-radius: var(--radius-pill);
    margin-left: 4px;
    padding: 0 6px;
  }
}

.loan-line {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  margin: 4px 0;
  overflow-wrap: anywhere;
}

.loan-link {
  background: none;
  border: 0;
  color: var(--app-primary);
  cursor: pointer;
  font: inherit;
  font-size: 0.8rem;
  padding: 0;
  text-align: left;
}

.schedule-delete-note,
.scope-locked {
  color: var(--app-text-muted);
  font-size: 0.8rem;
}
```

- [ ] 26.6 Run 26.2 again, then the existing entry-detail spec.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend
npx ng test --watch=false --include='src/app/components/accounting/entry-detail/entry-detail-schedule.spec.ts'
npx ng test --watch=false --include='src/app/components/accounting/entry-detail/entry-detail.spec.ts' 2>&1 | tail -3
```

Expected: `9 passed`; the existing entry-detail tests all pass.

- [ ] 26.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/entry-detail
git commit -m "feat(frontend): entry detail shows loan schedule, pills, edit scope and period-aware delete

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 27. Settings link and import report `schedules` block

**Model:** sonnet

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.ts`, `accounting-settings.html`, `accounting-settings.scss`, `accounting-settings.spec.ts`
- Modify: `frontend/src/app/components/accounting/accounting-settings/import-report.ts`, `import-report.spec.ts`
- Modify: `frontend/src/app/models/accounting.model.ts` (`ScheduleKind`, `ScheduleItem` removed; `BackupImportReport.schedules`)
- Modify: `frontend/src/app/services/accounting.service.ts` (`getSchedules` removed)

**Interfaces:**
- Consumes: `ScheduleImportReport` (Task 20); the reminder centre's `?tab=debts` and fragment `schedules` (Task 24).
- Produces: `AccountingSettingsComponent.openSchedules()`; `ReportView.schedules: ScheduleReportView | null` with `ScheduleReportView { recurring, installment, single, recordsMapped, rewardsIgnored, alreadyPosted, alreadySkipped, amountDiffers, review, ownerPending, pendingAmountDiffers }` (numbers) and the item lists `pastAmountLines`, `pendingAmountLines: ScheduleAmountLineView[]` with `ScheduleAmountLineView { name, seq, date, kind, amount, mozeAmount }` (Multica R-A3).

Rules (spec "Accounting settings page"): the 匯入 section's 週期／分期 row opens `/accounting/reminders?tab=debts` scrolled to the 週期／分期 section; the page never calls `/api/accounting/imports/schedules`; after an import the report shows its `schedules` block (definitions created per kind, records mapped, rewards ignored, periods HomeHub already posted or skipped, amount differences, items to review). ★Multica R-A3: both amount-difference lists are shown — `past_records_amount_differs` (periods HomeHub posted) and `amount_differs` (pending periods whose owner-kept amounts differ) — each as a count plus one line per differing item, `<名稱> 第 k 期 <日期>：HomeHub $<amount> / MOZE $<moze_amount>`; `past_records_owner_pending` shows as a count (`保留待入帳 N 期`). An import whose only finding is `amount_differs` still shows the warning.

- [ ] 27.1 Update the tests. In `frontend/src/app/components/accounting/accounting-settings/accounting-settings.spec.ts`: delete the `SCHEDULES` constant; delete both lines `http.expectOne(r => r.url.startsWith('/api/accounting/imports/schedules')).flush(…);` (in `beforeEach` and in `imports for real after the dry run and refreshes the latest import and schedules`, whose title becomes `imports for real after the dry run and refreshes the latest import`); add `Router` to its `import { provideRouter } from '@angular/router';` line and `vi` to its vitest import; replace the test `shows the static lock state and the schedules list` with

```typescript
  it('shows the static lock state and links 週期／分期 to the reminder centre', () => {
    // Spec "Schedule list moved to 提醒中心".
    expect(el.querySelector('.lock-row .r')?.textContent?.trim()).toBe('未鎖定（切換後鎖定）');
    expect(el.querySelector('.lock-note')?.textContent).toContain('ACCOUNTING_IMPORT_LOCKED');
    expect(el.querySelector('.schedule-row')).toBeNull();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    (el.querySelector('.schedules-link') as HTMLButtonElement).click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/reminders'], { queryParams: { tab: 'debts' }, fragment: 'schedules' });
    http.expectNone(r => r.url.includes('/imports/schedules'));
  });
```

  In `import-report.spec.ts` append

```typescript
  it('summarises the schedules block', () => {
    const view = summarizeReport({
      summary: {
        schedules: {
          definitions: {
            recurring: { created: 11, updated: 0, ended: 0, deleted: 0 },
            installment: { created: 14, updated: 0, ended: 0, deleted: 0 },
            single: { created: 0, updated: 0, ended: 0, deleted: 0 },
          },
          records_mapped: 534, rewards_ignored: 63, past_records_already_posted: 2, past_records_already_skipped: 1,
          past_records_amount_differs: { count: 1, instance_ids: [77] }, review: [{ moze_id: 'P-1', reason: 'interval_mismatch' }],
        },
      },
    });
    expect(view.schedules).toEqual({
      recurring: 11, installment: 14, single: 0, recordsMapped: 534, rewardsIgnored: 63, alreadyPosted: 2, alreadySkipped: 1,
      amountDiffers: 1, review: 1, ownerPending: 0, pendingAmountDiffers: 0, pastAmountLines: [], pendingAmountLines: [],
    });
    expect(summarizeReport({ summary: {} }).schedules).toBeNull();
  });

  it('shows pending amount differences when they are the only finding', () => {
    // Multica R-A3: only amount_differs is non-empty; the view still carries its count and per-period lines.
    const view = summarizeReport({
      summary: {
        schedules: {
          definitions: {
            recurring: { created: 0, updated: 1, ended: 0, deleted: 0 },
            installment: { created: 0, updated: 0, ended: 0, deleted: 0 },
            single: { created: 0, updated: 0, ended: 0, deleted: 0 },
          },
          records_mapped: 4, rewards_ignored: 0, past_records_already_posted: 0, past_records_already_skipped: 0,
          past_records_amount_differs: { count: 0, instance_ids: [], lines: [] }, past_records_owner_pending: [], review: [],
          amount_differs: [
            { definition_id: 7, name: 'Netflix', seq: 3, date: '2026-10-05', line: 0, kind: 'expense', amount: '120', moze_amount: '100' },
          ],
        },
      },
    });
    expect(view.schedules?.pendingAmountDiffers).toBe(1);
    expect(view.schedules?.amountDiffers).toBe(0);
    expect(view.schedules?.pendingAmountLines).toEqual([
      { name: 'Netflix', seq: 3, date: '2026-10-05', kind: 'expense', amount: '120', mozeAmount: '100' },
    ]);
  });
```

- [ ] 27.2 Run them.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false --include='src/app/components/accounting/accounting-settings/*.spec.ts'
```

Expected: failures — `.schedules-link` missing, `view.schedules` undefined, and an unmatched `/imports/schedules` request.

- [ ] 27.3 Edit `frontend/src/app/components/accounting/accounting-settings/import-report.ts`: add `schedules: ScheduleReportView | null;` as the last member of `ReportView`, add

```typescript
export interface ScheduleReportView {
  recurring: number;
  installment: number;
  single: number;
  recordsMapped: number;
  rewardsIgnored: number;
  alreadyPosted: number;
  alreadySkipped: number;
  amountDiffers: number;
  review: number;
  /** R-F1: owner-edited pending periods whose MOZE record turned past. */
  ownerPending: number;
  /** R-A3: pending periods (owner-kept amounts) with a line MOZE disagrees with. */
  pendingAmountDiffers: number;
  pastAmountLines: ScheduleAmountLineView[];
  pendingAmountLines: ScheduleAmountLineView[];
}

export interface ScheduleAmountLineView {
  name: string;
  seq: number;
  date: string;
  kind: string;
  amount: string;
  mozeAmount: string;
}

function amountLines(value: unknown): ScheduleAmountLineView[] {
  return (Array.isArray(value) ? value : []).map(item => {
    const row = record(item);
    return {
      name: String(row['name'] ?? ''), seq: Number(row['seq']) || 0, date: String(row['date'] ?? ''),
      kind: String(row['kind'] ?? ''), amount: String(row['amount'] ?? ''), mozeAmount: String(row['moze_amount'] ?? ''),
    };
  });
}

function scheduleView(value: unknown): ScheduleReportView | null {
  const block = record(value);
  if (!('definitions' in block)) {
    return null;
  }
  const created = (kind: string) => Number(record(record(block['definitions'])[kind])['created']) || 0;
  return {
    recurring: created('recurring'),
    installment: created('installment'),
    single: created('single'),
    recordsMapped: Number(block['records_mapped']) || 0,
    rewardsIgnored: Number(block['rewards_ignored']) || 0,
    alreadyPosted: Number(block['past_records_already_posted']) || 0,
    alreadySkipped: Number(block['past_records_already_skipped']) || 0,
    amountDiffers: Number(record(block['past_records_amount_differs'])['count']) || 0,
    review: Array.isArray(block['review']) ? (block['review'] as unknown[]).length : 0,
    ownerPending: Array.isArray(block['past_records_owner_pending']) ? (block['past_records_owner_pending'] as unknown[]).length : 0,
    pendingAmountDiffers: new Set(
      amountLines(block['amount_differs']).map(line => `${line.name}#${line.seq}`),
    ).size,
    pastAmountLines: amountLines(record(block['past_records_amount_differs'])['lines']),
    pendingAmountLines: amountLines(block['amount_differs']),
  };
}
```

  and add `schedules: scheduleView(summary['schedules']),` as the last property of the object `summarizeReport` returns.

- [ ] 27.4 Edit `frontend/src/app/components/accounting/accounting-settings/accounting-settings.ts`: delete `ScheduleItem,` from the model import, the `SCHEDULE_LABELS` constant, the `schedules` signal, `readonly scheduleLabels = SCHEDULE_LABELS;` and the line `this.service.getSchedules().subscribe(…);` in `loadImportState()`; inject the router if the component does not already (`private readonly router = inject(Router);` with `import { Router } from '@angular/router';`) and add

```typescript
  /** 週期／分期 moved to 提醒中心 › 借還款追蹤 (spec "Schedule list moved to 提醒中心"). */
  openSchedules(): void {
    void this.router.navigate(['/accounting/reminders'], { queryParams: { tab: 'debts' }, fragment: 'schedules' });
  }
```

  In `accounting-settings.html` replace the block from `<div class="lbl">分期 / 週期</div>` through the `}` closing its `} @empty { <p class="muted">沒有預定項目</p>` (the explanatory `<p class="muted">MOZE 的分期與週期記錄…</p>` and the `@for` go too) with

```html
  <div class="lbl">週期／分期</div>
  <button type="button" class="data-row schedules-link" (click)="openSchedules()">
    <span class="row-name">週期／分期</span>
    <span class="muted">在提醒中心管理 ›</span>
  </button>
```

  and, inside `<div class="import-report">`, after the `@if (r.fxOutliers > 0) { … }` block, add

```html
        @if (r.schedules; as s) {
          <p class="schedules-report">
            <b>週期／分期：</b>週期 {{ s.recurring }}、分期 {{ s.installment }}、單筆 {{ s.single }}；對應 {{ s.recordsMapped }} 筆、略過回饋 {{ s.rewardsIgnored }} 筆
            @if (s.alreadyPosted || s.alreadySkipped) {
              ；HomeHub 已入帳 {{ s.alreadyPosted }} 筆、已略過 {{ s.alreadySkipped }} 筆
            }
            @if (s.amountDiffers) {
              <span class="import-error">；金額不同 {{ s.amountDiffers }} 期</span>
            }
            @if (s.pendingAmountDiffers) {
              <span class="import-error">；待入帳金額與 MOZE 不同 {{ s.pendingAmountDiffers }} 期</span>
            }
            @if (s.ownerPending) {
              <span>；保留待入帳 {{ s.ownerPending }} 期</span>
            }
            @if (s.review) {
              <span class="import-error">；待檢查 {{ s.review }} 筆</span>
            }
          </p>
          @if (s.pastAmountLines.length || s.pendingAmountLines.length) {
            <ul class="schedule-amount-lines">
              @for (line of s.pastAmountLines.concat(s.pendingAmountLines); track $index) {
                <li>{{ line.name }} 第 {{ line.seq }} 期 {{ line.date }}：HomeHub ${{ line.amount }} / MOZE ${{ line.mozeAmount }}</li>
              }
            </ul>
          }
        }
```

  Append to `accounting-settings.scss` (the row is a button; it keeps the list row's look and the focus ring):

```scss
.schedules-link {
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
  width: 100%;

  &:focus-visible {
    outline: 2px solid var(--app-primary);
    outline-offset: 1px;
  }
}
```

  Delete `formatMoney` / `slashDate` members of the component only if nothing else in the template uses them (`grep -n "slashDate\|formatMoney" accounting-settings.html`).

- [ ] 27.5 Remove the last users of the old listing: in `frontend/src/app/services/accounting.service.ts` delete `getSchedules(...)` and `ScheduleItem, ScheduleKind,` from its import; in `frontend/src/app/models/accounting.model.ts` delete `export type ScheduleKind = 'period' | 'installment' | 'skipped_record';`, the `ScheduleItem` interface, and replace in `BackupImportReport` the two lines

```typescript
  /** Future MOZE rows stored by kind; a kind with no rows is absent (`dict(Counter(...))` on the server). */
  schedules: Partial<Record<ScheduleKind, number>>;
```

  with

```typescript
  /** The schedule block of the import (definitions, periods, mirror-period reconciliation). */
  schedules?: ScheduleImportReport;
```

  Confirm nothing else uses them:

```bash
cd /home/opc/workspace/home-hub-schedules/frontend && grep -rn "ScheduleItem\|getSchedules\|ScheduleKind\b\|imports/schedules" src/app || echo "none left"
```

Expected: `none left`.

- [ ] 27.6 Run 27.2 again, then the whole frontend suite and a production build.

```bash
cd /home/opc/workspace/home-hub-schedules/frontend
npx ng test --watch=false --include='src/app/components/accounting/accounting-settings/*.spec.ts'
npx ng test --watch=false 2>&1 | tail -4
npx ng build 2>&1 | tail -3
git checkout -- angular.json 2>/dev/null; git status --short | grep -v '^ M frontend/src\|^?? ' || true
```

Expected: the settings specs pass; the full suite `B_front + 88` passed (Tasks 20–27: 15 + 8 + 20 + 14 + 14 + 6 + 9 + 2), no failed files; the build ends with `Application bundle generation complete.`

- [ ] 27.7 Commit.

```bash
cd /home/opc/workspace/home-hub-schedules
git add frontend/src/app/components/accounting/accounting-settings frontend/src/app/models/accounting.model.ts \
  frontend/src/app/services/accounting.service.ts
git commit -m "feat(frontend): settings link to 週期／分期 and the import report's schedules block

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## 28. Verify on preview data (disposable database) and owner checklist

**Model:** opus

**Files:** none committed. Scratch directory `/home/opc/workspace/moze-verify-schedules/` (outside the repo, mode 700) holds the aggregate script, the verify server, the converter JSON kept for the key check (deleted in 28.6) and `notes.txt` (aggregates only, mode 600; Task 29 copies it into `design.md` and deletes the directory). The worktree `.env` symlink becomes a filtered private copy pointing at `accounting_schedules_verify` and is restored in 28.12.

**Interfaces:**
- Consumes: the migration (Task 3), the backup CLI with schedules (Tasks 16–18), the job CLI (Task 15), the API (Tasks 9–15), the SPA (Tasks 20–27), the converter `tools/moze-realm-export`.
- Produces: the aggregates for the PR description and `design.md` "Implementation notes" (definitions per kind, `records_mapped`, `rewards_ignored`, `skipped_future` total, review reasons, loan remainder differences as a count, instance statuses, import timing); the confirmed MOZE weekday numbering and posting-mode field; the owner's pass/fail list (one loan, one recurring transfer, one 提醒入帳 item, 390 px). Drops `accounting_schedules_verify` at the end. Never touches `accounting_db`.

Rules: nothing from the backup is printed row by row: the script prints counts, key names and reason codes only (the loan remainder check is reduced to the number of loans whose difference is not 0); of the importer CLI's stderr only its `schedule_job:` line (counts) is shown — the rest can name accounts. The verify API and the importer CLI run with `ACCOUNTING_SCHEDULER_ENABLED=false`, so the CLI's inline job (Task 18, Multica R-F4) generates the horizon but posts nothing, and only the explicit job run of 28.8b and the owner's actions post anything; the preview binds to the Tailscale address only, and opening its port (and any firewall rule) is an owner-approved action (28.10a), closed again in 28.12.

- [ ] 28.1 Run every suite and the production build.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/pytest -q -p no:warnings 2>&1 | tail -2
cd /home/opc/workspace/home-hub-schedules/frontend && npx ng test --watch=false 2>&1 | tail -4
cd /home/opc/workspace/home-hub-schedules/frontend && npm run build 2>&1 | tail -3 && ls dist/inventory-ui/browser/index.html
```

Expected: pytest `… passed` with no failures (the Task 19 count); Vitest no failed files (`B_front + 88`); `Application bundle generation complete` and the `index.html` path.

- [ ] 28.2 Create the disposable database and point the worktree `.env` at it (a filtered private copy; nothing is printed).

```bash
docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d postgres -At -c "SELECT count(*) FROM pg_database WHERE datname = '"'"'accounting_schedules_verify'"'"'"'
docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_schedules_verify'
cd /home/opc/workspace/home-hub-schedules
rm -f .env
sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_schedules_verify/' \
    -e 's/^ACCOUNTING_IMPORT_LOCKED=.*/ACCOUNTING_IMPORT_LOCKED=false/' \
    -e 's/^ACCOUNTING_SCHEDULER_ENABLED=.*//' \
    -e 's/^ACCOUNTING_API_TOKENS=.*/ACCOUNTING_API_TOKENS=/' \
    /home/opc/workspace/home-hub/.env > .env
echo 'ACCOUNTING_SCHEDULER_ENABLED=false' >> .env
chmod 600 .env
grep -c '^ACCOUNTING_DB=accounting_schedules_verify$' .env
grep -qx 'ACCOUNTING_DB=accounting_schedules_verify' .env || { echo "verify .env not pointed at the verify DB"; exit 1; }
mkdir -p /home/opc/workspace/moze-verify-schedules && chmod 700 /home/opc/workspace/moze-verify-schedules
cd services/accounting-service && .venv/bin/alembic current 2>&1 | tail -1
.venv/bin/alembic upgrade head 2>&1 | tail -1 && .venv/bin/alembic current 2>&1 | tail -1
```

Expected: `0` (no leftover; a `1` means an earlier run did not clean up — drop it with the 28.12 `dropdb` line first); `1`; then nothing from the guard (if it prints `verify .env not pointed at the verify DB`, the block stopped before Alembic: fix the `sed`, never run `alembic upgrade` against the production `accounting_db`); an `alembic current` line with no revision id (only Alembic's `INFO … Will assume transactional DDL.` line: the new verify database has no `alembic_version` yet — a revision id such as `7b1e4a2c9d05` printed here means the `.env` does not point at the empty verify database: stop); `Running upgrade 7b1e4a2c9d05 -> c4e8b2f1a7d3, …`; `c4e8b2f1a7d3 (head)`. The verify `.env` blanks `ACCOUNTING_API_TOKENS`, so the verify API on 127.0.0.1 runs without the #42 token auth and the `curl`s below need no header; the production build of 28.1 may carry `ACCOUNTING_SPA_TOKEN`, whose header the verify API then ignores, and the verify server of 28.10 forwards `Authorization` anyway, so the preview also works against a verify API that keeps a token.

- [ ] 28.3 Install the converter and create the aggregate script `/home/opc/workspace/moze-verify-schedules/aggregates.py`.

```bash
cd /home/opc/workspace/home-hub-schedules/tools/moze-realm-export && npm ci 2>&1 | tail -1
```

```python
"""Print aggregates of a backup import report (stdin: the CLI's JSON). Counts, key names and codes only."""
import json
import sys
from collections import Counter
from decimal import Decimal

report = json.load(sys.stdin)
summary = report["summary"]
block = summary["schedules"]
future = sum(int(n) for n in summary["skipped_future"].values())
out = {
    "status": report["status"],
    "definitions_created": {kind: block["definitions"][kind]["created"] for kind in ("recurring", "installment", "single")},
    "definitions_ended_deleted": {kind: [block["definitions"][kind]["ended"], block["definitions"][kind]["deleted"]] for kind in ("recurring", "installment", "single")},
    "instances": block["instances"],
    "records_mapped": block["records_mapped"],
    "rewards_ignored": block["rewards_ignored"],
    "unsupported_types": block["unsupported_types"],
    "skipped_future_total": future,
    "skipped_future_balanced": future == block["records_mapped"] + block["rewards_ignored"] + sum(block["unsupported_types"].values()),
    "past_records_already_posted": block["past_records_already_posted"],
    "past_records_already_skipped": block["past_records_already_skipped"],
    "past_records_amount_differs": block["past_records_amount_differs"]["count"],
    "relinked": block["relinked"],
    "review_reasons": dict(Counter(item["reason"] for item in block["review"])),
    "loans_checked": len(block["loan_remainder_check"]),
    "loans_with_remainder_difference": sum(1 for item in block["loan_remainder_check"] if Decimal(str(item["difference"])) != 0),
    "compared_accounts": summary["compared_accounts"],
}
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
```

Expected: `added … packages` (or `up to date`).

- [ ] 28.4 Dry run against the empty upgraded database, keeping the converter JSON once for the key check.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
time .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip --dry-run \
  --keep-json /home/opc/workspace/moze-verify-schedules/backup.json 2>/dev/null \
  | python3 /home/opc/workspace/moze-verify-schedules/aggregates.py
```

Expected: one JSON line with `status` `dry_run`, `definitions_created` `{"installment": 14, "recurring": 11, "single": 0}`, `records_mapped` 534, `rewards_ignored` 63, `unsupported_types` `{}`, `skipped_future_total` 597, `skipped_future_balanced` true, `past_records_already_posted` 0, `past_records_already_skipped` 0, `review_reasons` without `interval_mismatch` (the weekday and day-of-month conventions hold on every period), `loans_with_remainder_difference` 0. Any other value: stop and report the line (it holds no owner data) — the spec numbers come from the 2026-10-02 exploration of the same zip.

- [ ] 28.5 Confirm the two implementation-time assumptions from the kept JSON (key names and value histograms of rule fields only).

```bash
python3 - <<'PY'
import json
from collections import Counter
doc = json.load(open("/home/opc/workspace/moze-verify-schedules/backup.json", encoding="utf-8"))
for name in ("AHPeriod", "AHInstallment"):
    rows = doc["classes"].get(name, [])
    print(name, len(rows), sorted({key for row in rows for key in row}))
periods = doc["classes"].get("AHPeriod", [])
print("unit", dict(Counter(row.get("unit") for row in periods)))
print("weekly days", dict(Counter(row.get("days") for row in periods if row.get("unit") == 1)))
print("type", dict(Counter(row.get("type") for row in periods)))
# Live periods and finite series (Task 16 caps a finite period's times at its last record; Task 17 ends a period
# with no enabled future record). Counts only.
records = doc["classes"].get("AHRecord", [])
cutoff = str(doc["exported_at"])[:10]
future = Counter(row.get("eventID") for row in records if str(row.get("date"))[:10] > cutoff and row.get("isEnabled", True))
dates = {}
for row in records:
    if row.get("eventID") and row.get("type") != 15 and not row.get("isTransferIn"):
        dates.setdefault(row["eventID"], set()).add(str(row.get("date"))[:10])
print("periods without an enabled future record", sum(1 for row in periods if future[row["identifier"]] == 0))
finite = [row for row in periods if row.get("times")]
print("finite periods", len(finite),
      "with dates == times", sum(1 for row in finite if len(dates.get(row["identifier"], ())) == row["times"]),
      "with dates < times", sum(1 for row in finite if len(dates.get(row["identifier"], ())) < row["times"]))
# What AHInstallment.remainder means (D37 assumes the remaining principal) before loan_remainder_check is trusted.
installments = doc["classes"].get("AHInstallment", [])
print("installments total == installment*times + remainder",
      sum(1 for row in installments
          if abs(float(row.get("total") or 0)) == round(abs(float(row.get("installment") or 0)) * int(row.get("times") or 0)
                                                       + abs(float(row.get("remainder") or 0)), 4)))
# Interest on money lent (D29 writes interest negative): type-15 records packaged with a type-5 collection.
collection_packages = {row.get("packageID") for row in records if row.get("type") == 5 and row.get("packageID")}
print("interest records on collection packages",
      sum(1 for row in records if row.get("type") == 15 and row.get("packageID") in collection_packages))
PY
```

Expected: `AHPeriod 11 [...]` and `AHInstallment 14 [...]` key lists. Weekday numbering: confirmed when 28.4 showed no `interval_mismatch` and every weekly period's `days` is in 1–7 (with no weekly period, write "no weekly period in the backup; numbering unconfirmed, a mismatch would surface as `interval_mismatch`"). Posting mode: look for a key beyond `identifier, unit, days, times, type, startDate` (periods) and `identifier, dayOfMonth, dateInfo, times, total, remainder` (installments) whose name suggests reminding or confirming (`remind`, `confirm`, `notify`, `auto`); none → "no MOZE posting-mode field; every imported definition is `auto`". If one exists, stop and report its name and value histogram — the spec then needs a decision (Known Spec Conflicts).

The four added lines must read: `periods without an enabled future record 0` (every one of the 11 periods is live; a non-zero count means that many periods import `ended` — report the count to the owner before 28.7); `finite periods F with dates == times F with dates < times 0` (MOZE pre-generates every finite series, so Task 16's cap on `times` never shortens a real series; a non-zero `dates < times` count: stop and report — the cap would end those series early); `installments total == installment*times + remainder` either 0 (then `remainder` is the remaining principal, as D37 assumes, and `loan_remainder_check` is meaningful) or a count — any non-zero count means `remainder` may be MOZE's 分期餘額 (rounding), so stop and report before trusting `loan_remainder_check`; `interest records on collection packages 0` (a non-zero count means a lent-money package carries interest, which D29's negative interest line would map with the wrong sign — report it to the owner). Write the four lines to `notes.txt`.

- [ ] 28.6 Delete the kept JSON and write the first notes.

```bash
rm -f /home/opc/workspace/moze-verify-schedules/backup.json
ls /home/opc/workspace/moze-verify-schedules
```

Write `/home/opc/workspace/moze-verify-schedules/notes.txt` (then `chmod 600`) with: the 28.4 aggregate line, its `real` time, and the two conclusions of 28.5 (counts and key names only).

Expected: `aggregates.py  notes.txt`.

- [ ] 28.7 Real import, then a second one (re-import idempotence on real data).

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
time .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip \
  2> >(grep '^schedule_job:' >&2) \
  | python3 /home/opc/workspace/moze-verify-schedules/aggregates.py
docker exec -i stonk-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d accounting_schedules_verify -At' <<'SQL'
SELECT kind || ':' || status || ':' || posting_mode || ':' || count(*) FROM schedule_definition GROUP BY kind, status, posting_mode ORDER BY 1;
SELECT status || ':' || coalesce(acted_by::text, '-') || ':' || count(*) FROM schedule_instance GROUP BY status, acted_by ORDER BY 1;
SELECT 'schedule_entries:' || count(*) FROM ledger_entry WHERE source = 'schedule';
SELECT md5(string_agg(id || ':' || status || ':' || seq || ':' || due_date || ':' || coalesce(amount_override::text, ''), ',' ORDER BY id)) FROM schedule_instance;
SQL
echo "psql exit $?"
.venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip \
  2> >(grep '^schedule_job:' >&2) \
  | python3 /home/opc/workspace/moze-verify-schedules/aggregates.py
docker exec -i stonk-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d accounting_schedules_verify -At' <<'SQL'
SELECT md5(string_agg(id || ':' || status || ':' || seq || ':' || due_date || ':' || coalesce(amount_override::text, ''), ',' ORDER BY id)) FROM schedule_instance;
SQL
```

Expected (Multica R-F7: every query groups by its plain columns, and `ON_ERROR_STOP=1` makes a failing statement end psql with exit 3 instead of letting later statements print — `psql exit 0` is part of the evidence):
- first import: the stderr line `schedule_job: status completed, generated G, posted 0, failed 0, due_unposted D` — the CLI's inline job (R-F4) generated the horizon (G ≥ 0 HomeHub-generated periods beyond MOZE's records) and, with the scheduler switched off, posted nothing; D is the number of periods due on the import date (0 or a small count); then one JSON line equal to 28.4's except `status` `succeeded`;
- definition rows, one per (kind, status, mode), e.g. `installment:active:auto:14`, `recurring:active:auto:11` (plus `…:ended:…` rows for series MOZE already finished and `…:paused:…` only for a review reason of 28.4), whose counts sum to 25;
- instance rows, one per (status, actor): `pending:-:P`, `posted:import:I` (periods MOZE booked up to the export date) and `skipped:import:S` (disabled ones) — no `posted:auto` row yet;
- `schedule_entries:0` (nothing posted: D periods wait for 28.8b);
- a hash; `psql exit 0`;
- second import: `schedule_job: status completed, generated 0, posted 0, failed 0, due_unposted D` (same D), `definitions_created` all 0, `instances.created` 0, `instances.deleted` 0, `instances.adopted` 0, and the same hash.

Append both `schedule_job:` lines, both aggregate lines, the status counts and the `real` time to `notes.txt`.

- [ ] 28.8 Generate the horizon and post what is due — two explicit steps (Multica R-F4; the scheduler is off here, so nothing else does either).

  28.8a **Generate the horizon.** The importer CLI's inline job already generated it (28.7, `generated G`); confirm every live definition reaches `horizon(today)` and that a further generation adds nothing:

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service
HORIZON=$(.venv/bin/python -c "from app.services import ledger_service, schedule_rules as r; print(r.horizon(ledger_service._today()))")
docker exec -i stonk-postgres-1 sh -c "psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d accounting_schedules_verify -At" <<SQL
SELECT 'short_of_horizon:' || count(*) FROM schedule_definition
 WHERE status <> 'ended' AND review_reason IS DISTINCT FROM 'interval_mismatch' AND generated_until IS DISTINCT FROM DATE '$HORIZON';
SELECT 'homehub_generated:' || count(*) FROM schedule_instance WHERE moze_id IS NULL;
SQL
.venv/bin/python -m app.services.schedule_job --dry-run | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['status'], r['generated'], len(r['posted']), len(r['failed']), len(r['stopped_definitions']))"
```

Expected: `short_of_horizon:0`; `homehub_generated:G` (the `generated` of 28.7's first `schedule_job:` line); then `dry_run 0 D 0 0` — nothing left to generate, and the D periods due on the import date are what a run would post.

  28.8b **Post what is due.** Run the job once for real (trigger `manual`, the same code path as the 00:05 run), then dry-run again:

```bash
.venv/bin/python -m app.services.schedule_job | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['status'], r['generated'], len(r['posted']), len(r['failed']), len(r['stopped_definitions']))"
.venv/bin/python -m app.services.schedule_job --dry-run | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['status'], r['generated'], len(r['posted']), len(r['failed']), len(r['stopped_definitions']))"
docker exec -i stonk-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d accounting_schedules_verify -At' <<'SQL'
SELECT status || ':' || coalesce(acted_by::text, '-') || ':' || count(*) FROM schedule_instance GROUP BY status, acted_by ORDER BY 1;
SQL
```

Expected: `completed 0 D 0 0`; `dry_run 0 0 0 0`; the instance rows of 28.7 with D moved from `pending:-` to a new `posted:auto:D` row (absent when D = 0). Periods due before the import date wait for the owner (no silent backlog). A non-zero `failed` count: stop and read the failing instances' `last_error` codes (`SELECT id, last_error FROM schedule_instance WHERE last_error IS NOT NULL` — field and message only, by construction). Append the three lines to `notes.txt`.

- [ ] 28.9 Start the verify API on :8011 (background, e.g. `run_in_background`) and check it.

```bash
cd /home/opc/workspace/home-hub-schedules/services/accounting-service && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
```

When `curl -s http://127.0.0.1:8011/health` returns `{"status":"ok"}`:

```bash
curl -s http://127.0.0.1:8011/schedules/definitions | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), sorted({x['kind'] for x in d}), sum(1 for x in d if x['locked']))"
curl -s 'http://127.0.0.1:8011/schedules/instances?queue=true' | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
curl -s http://127.0.0.1:8011/imports/schedules -o /dev/null -w '%{http_code}\n'
```

Expected: `25 ['installment', 'recurring'] 25` (every imported definition is locked before cutover); the number of periods waiting in 待完成交易; `404`.

- [ ] 28.10a **Owner-approved action: ask before opening the port.** Precedent: the phase 2a preview served the build on `100.81.25.128:4300` (archived 2a tasks 31.8–31.9). Ask the owner, in one message, to approve serving this preview on `100.81.25.128:4301`; the preview binds the Tailscale IP only (never `0.0.0.0` or a public interface), and the verify API stays on `127.0.0.1:8011`. firewalld's public zone blocks 4301, so the default access is an SSH tunnel (`ssh -N -L 4301:100.81.25.128:4301 opc@<this host>`, then `http://localhost:4301/hub/accounting`). For the phone, which cannot use the tunnel, a firewall change is a second owner-approved action: only with the owner's explicit yes run the runtime-only rule (no `--permanent`) `sudo firewall-cmd --zone=public --add-port=4301/tcp`, note in `notes.txt` that it was added, and remove it in 28.12. No tailnet ACL is changed. Without the owner's yes, do not start 28.10; stop and report.

- [ ] 28.10 Serve the production build over Tailscale (after the owner approved 28.10a): create `/home/opc/workspace/moze-verify-schedules/verify_server.py` from the phase 2a verify server (the code block of step 31.8 in `openspec/changes/archive/2026-10-03-add-accounting-entry/tasks.md`) with the build path, API port and listen port of this worktree, and start it in the background.

```bash
python3 - <<'PY'
import re
from pathlib import Path
plan = Path("/home/opc/workspace/home-hub-schedules/openspec/changes/archive/2026-10-03-add-accounting-entry/tasks.md").read_text(encoding="utf-8")
start = plan.index("31.8 Create `/home/opc/workspace/moze-verify-2a/verify_server.py`:")
code = re.search(r"```python\n(.*?)```", plan[start:], re.S).group(1)
code = (code.replace("home-hub-entry/frontend", "home-hub-schedules/frontend")
            .replace("http://127.0.0.1:8010", "http://127.0.0.1:8011")
            .replace("('100.81.25.128', 4300)", "('100.81.25.128', 4301)")
            .replace("FORWARDED_HEADERS = ('Content-Type', 'Accept')",
                     "FORWARDED_HEADERS = ('Content-Type', 'Accept', 'Authorization')")  # #42 bearer token
            .replace("phase 2a verify server", "schedules verify server"))
assert "8011" in code and "4301" in code and "home-hub-schedules" in code and "'Authorization'" in code
Path("/home/opc/workspace/moze-verify-schedules/verify_server.py").write_text(code, encoding="utf-8")
print("written")
PY
python3 /home/opc/workspace/moze-verify-schedules/verify_server.py
```

Expected: `written`; the server stays up (run the second command in the background). `curl -s -o /dev/null -w '%{http_code}\n' http://100.81.25.128:4301/hub/accounting/reminders` prints `200`.

- [ ] 28.11 Owner checklist (the owner, on a phone over Tailscale at `http://100.81.25.128:4301/hub/accounting`, beside MOZE). Record pass / fail per line in `notes.txt` (no amounts or names; a loan is named by its initials):
  1. **One loan.** 提醒中心 › 借還款追蹤 › 週期／分期: the loan row reads `下期 MM/DD · 已入帳 k / N · 剩餘 …`, and its 剩餘 equals MOZE's remaining after the same posted periods; the loan's entry detail shows the `剩餘 · 已還 · 下期` line and the link opens the manage sheet. If a period is due today or overdue, 入帳 it from 待完成交易: the repayment and interest appear in the passbook with the `分期 #k/N` pill and 剩餘 drops by the repayment.
  2. **One recurring transfer.** Its row shows `週期 #k/N` (or `週期 #k`); 入帳 (or 補入帳 for a backlog) books both legs with the same date, and each passbook shows its leg.
  3. **One 提醒入帳 item.** Before cutover imported definitions are locked, so: create a local 週期 expense from the entry form with 入帳方式 提醒入帳 and 起始日 today — after 儲存 it waits in 待完成交易 as `今天`, the bell count includes it, 入帳 posts it and the count drops; then 結束 it from its manage sheet (`結束後未入帳的 N 期將刪除`) and delete the posted entry (`此期將回到待完成交易`).
  4. **390 px and keyboard.** At 390 px wide: 待完成交易, 週期／分期, the manage sheet, the entry form's 週期 / 分期 tabs and the entry detail have no horizontal page scroll; with a keyboard, Esc closes the sheet and ⏎ in its fields confirms.

  Any failure: stop and report the line; do not deploy.

- [ ] 28.12 Clean up: stop the verify server and the :8011 API (end their background processes), then:

```bash
docker exec stonk-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" --force accounting_schedules_verify'
cd /home/opc/workspace/home-hub-schedules
rm -f .env && ln -s /home/opc/workspace/home-hub/.env .env && readlink .env
git checkout -- frontend/angular.json 2>/dev/null; git status --short
docker exec stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d postgres -At -c "SELECT count(*) FROM pg_database WHERE datname = '"'"'accounting_schedules_verify'"'"'"'
```

Then confirm the preview is torn down (the port of 28.10a is closed again):

```bash
ss -ltn | grep -c ':4301 ' || true
sudo firewall-cmd --zone=public --list-ports | grep -c '4301/tcp' || true
```

Run `sudo firewall-cmd --zone=public --remove-port=4301/tcp` first if 28.10a added the rule (notes.txt says so).

Expected: `/home/opc/workspace/home-hub/.env`; `git status --short` prints nothing (the build output is ignored); `0`; then `0` and `0` (nothing listens on 4301 any more and no firewall rule for it remains). Tell the owner the preview is down. `notes.txt` stays for Task 29. Nothing is committed in this task.

## 29. Deploy runbook, design notes, superseded text

**Model:** opus

**Files:**
- Create: `docs/deploy/accounting-schedules.md`
- Modify: `openspec/changes/add-accounting-schedules/design.md` ("Migration Plan" step 5, "Implementation notes")
- Modify: `docs/superpowers/specs/2026-10-03-scheduled-transactions-design.md` (the superseded `moze_schedule` sentences)
- Modify: `.env.example` (`ACCOUNTING_SCHEDULER_ENABLED`)

**Interfaces:**
- Consumes: `notes.txt` of Task 28; the phase 2a runbook `docs/deploy/accounting-phase-2a.md` (its dump, drill and restore-and-swap rollback are reused by reference, with this release's names).
- Produces: the runbook; the filled "Implementation notes"; no stale statement that the migration moves `moze_schedule` rows or that downgrade refills them.

Rules: the runbook states, in order, that (1) the migration drops `moze_schedule` and its rows are not migrated (they are reproducible from the backup), (2) backend and SPA deploy together (the old SPA calls the removed `/imports/schedules`; the new backend needs `apscheduler`), (3) the post-upgrade step is a backup import, dry run first, and that the import ends with a job run, (4) `ACCOUNTING_SCHEDULER_ENABLED` and the job's 00:05 / startup / retry / after-import runs, (5) operator `curl`s pass `Authorization: Bearer $TOKEN` when `ACCOUNTING_API_TOKENS` is set (#42), (6) no Caddy change, but the live `@hub_spa` matcher must serve `/accounting/reminders` and `/accounting/entry`, and (7) rollback is the phase 2a restore-and-swap with this release's own script (phase 2a head `7b1e4a2c9d05` in all its head checks, written out in full in the runbook), since `alembic downgrade` refuses as soon as HomeHub has posted or acted on a period.

- [ ] 29.1 Create `docs/deploy/accounting-schedules.md`:

````markdown
# Accounting schedules deploy (add-accounting-schedules)

Spec: `openspec/changes/add-accounting-schedules/`. Endpoints, the job and its CLI: [`services/accounting-service/README.md`](../../services/accounting-service/README.md) ("Schedules"). The procedures for the pre-deploy dump, the restore drill and the rollback are those of [`accounting-phase-2a.md`](accounting-phase-2a.md), with the names below.

## Configuration

Add to the root `.env` (`.env.example` documents it):

```text
ACCOUNTING_SCHEDULER_ENABLED=true
```

`true` (the default when absent) runs the schedule job inside `accounting-service`: daily at 00:05 Asia/Taipei, once 10 s after start-up, every 10 minutes after a run that ended `busy` or `import_running` until a run completes or the day changes, and once after every real backup import (through the API; the importer CLI runs the job inline after its import — generation always, posting only when this switch is on). `false`, `0` or `no` turn it off; periods then wait until someone runs `python -m app.services.schedule_job`, taps 補入帳至今天, or calls `POST /api/accounting/schedules/run-now`. Run exactly one `accounting-service` process with the job enabled: the job lock (`pg_try_advisory_lock` on key `0x53434844`) makes a second runner a no-op, but logs `busy` every day.

## What the migration does

Revision `c4e8b2f1a7d3`:

- adds `schedule` to `entry_source` (in an autocommit block, before the transaction of the rest);
- creates `schedule_definition` and `schedule_instance` with their enums and indexes;
- **drops `moze_schedule`.** Its rows are not migrated: they were a raw copy of the backup's future records, periods and installments, and the backup import after the upgrade (step 3) recreates them as definitions and periods. Between the upgrade and that import, 提醒中心 shows no 週期／分期 and nothing is posted.

## Deploy order

0. **Pre-deploy record, dump and restore drill**: steps 0 and 0b of [`accounting-phase-2a.md`](accounting-phase-2a.md) with `~/backups/home-hub-schedules/`, file names `*-pre-schedules-$TS.*`, drill databases `accounting_drill_schedules` / `accounting_drill_restore` / `accounting_drill_broken`, and the expected head after the drill's upgrade `c4e8b2f1a7d3`. The rollback script is **not** the 2a file with names swapped: the 2a script checks the phase 1 head `5d2e7c9a1b3f`, which would stop a real rollback of this release at its step c. Save the script of the "Rollback" section below (phase 2a head `7b1e4a2c9d05` in its step c check, its `restore verified` line and its post-swap check) as `~/backups/home-hub-schedules/rollback-schedules.sh` and `chmod 700` it, then confirm it before the drill:

   ```bash
   grep -c '7b1e4a2c9d05' ~/backups/home-hub-schedules/rollback-schedules.sh
   grep -c '5d2e7c9a1b3f\|home-hub-2a\|pre-2a' ~/backups/home-hub-schedules/rollback-schedules.sh
   ```

   Expected: `4` and `0`. In the drill (2a step 0b with this release's names), step c's `alembic current` on the swapped-in database must print exactly `7b1e4a2c9d05 (head)` — the very string the script's step c compares against, so the drill proves the script's head check on a good restore; the failure drill (step d) runs `rollback-schedules.sh` on the truncated dump. Any difference: stop, do not deploy.

1. **Dependencies and migration**

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service
   .venv/bin/pip install -r requirements.txt 2>&1 | tail -1
   .venv/bin/python -c "import apscheduler; print(apscheduler.__version__)"
   .venv/bin/alembic upgrade head
   ```

   Expected: `3.11.0`; `Running upgrade 7b1e4a2c9d05 -> c4e8b2f1a7d3, …`.

2. **Backend and frontend together** (the previous SPA's settings page calls `GET /imports/schedules`, which now answers 404; the new SPA needs the schedule endpoints)

   ```bash
   cd /home/opc/workspace/home-hub && npx pm2 restart accounting-service
   curl -s http://localhost:8000/health
   curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/schedules/definitions
   sleep 15; npx pm2 logs accounting-service --lines 50 --nostream | grep -c 'schedule_job.run'
   ```

   Expected: `{"status":"ok"}`; `[]`; at least `1` (the start-up run, which finds no definitions). As in phase 2a step 2: with `ACCOUNTING_API_TOKENS` unset the header is ignored; when it is set (#42), export `TOKEN` to one of its tokens first — every `/schedules/*` route, `run-now` included, answers 401 without it (`/health` needs none). Then build and publish the SPA as in step 3 of the phase 2a runbook (`cd frontend && npm run build`, publish `frontend/dist/inventory-ui/browser` with the production publisher).

3. **Post-upgrade backup import**: dry run first, read the `schedules` block, then the real run.

   ```bash
   cd /home/opc/workspace/home-hub/services/accounting-service
   umask 077   # the reports hold loan_remainder_check names and amounts: never world-readable, not even briefly
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip --dry-run > ~/backups/home-hub-schedules/moze-schedules-dry-run.json
   .venv/bin/python -m app.services.moze_backup_import_service ~/workspace/moze-backup/MOZE_4.0.zip > ~/backups/home-hub-schedules/moze-schedules-import.json
   ls -l ~/backups/home-hub-schedules/moze-schedules-*.json
   ```

   Or from the SPA: 記帳設定 → 匯入 → 試算 → 匯入 (the report shows 週期／分期 counts). Check: definitions `recurring` 11 and `installment` 14 created, `records_mapped` 534, `rewards_ignored` 63, `unsupported_types` empty, `review` without `interval_mismatch`, every `loan_remainder_check` difference 0, and the balance comparison as in phase 2a. The real import ends with a job run (the CLI runs it inline and prints a `schedule_job:` counts line on stderr; with `ACCOUNTING_SCHEDULER_ENABLED` on it posts): periods due today post, periods due earlier wait in 提醒中心 › 待完成交易 for 補入帳至今天 (nothing is posted silently for the past). Delete both JSON files after reading (`rm -f ~/backups/home-hub-schedules/moze-schedules-*.json`).

4. **Caddy**: no Caddy change in this release (no new SPA path), but its deep links `/accounting/reminders?tab=debts&schedule=<id>` (entry detail, settings) and `/accounting/entry?schedule=<id>` need `/accounting/reminders` and `/accounting/entry` in the live `@hub_spa` matcher (the 2a matcher lists `entry`; `reminders` came with the reminder centre). Confirm:

   ```bash
   for p in accounting/reminders accounting/entry; do
     printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "https://oracle.saola-mamba.ts.net/hub/$p")" "$p"
   done
   ```

   Expected: `200` for both. A `404`: add the missing path to the matcher exactly as in the 2a "Caddy `@hub_spa` matcher" procedure (edit in place to keep the bind-mounted inode, backup `command cp -p Caddyfile Caddyfile.bak-accounting-schedules` first, `caddy validate`, `caddy reload`, re-run the loop); its rollback is `cat Caddyfile.bak-accounting-schedules > Caddyfile` followed by the same validate and reload.

5. **Owner check**: one loan (剩餘 equals MOZE after the posted periods), one recurring transfer, one 提醒入帳 item, as in the verify checklist; the bell counts due 提醒入帳 periods.

## During the mirror period (before `ACCOUNTING_IMPORT_LOCKED=true`)

- Imported definitions are locked (409 `locked_until_cutover` on edit, delete, and on 重新入帳 of a period MOZE booked); pause, resume, end, mode, skip and post work.
- Re-import the backup whenever MOZE has new records. A period HomeHub posted or skipped is not imported again from MOZE (`past_records_already_posted` / `past_records_already_skipped`); an amount difference is reported under `past_records_amount_differs` for a manual check.
- Schedule writes answer 409 `import_running` (toast `匯入進行中，請稍後再試`) for the seconds an import holds its lock; the importer waits up to 30 s for running schedule writes.

## Rollback

Window: **3 days**, as in phase 2a. `alembic downgrade 7b1e4a2c9d05` is **not** the rollback: it refuses (`refusing to downgrade c4e8b2f1a7d3: …`) as soon as any `schedule` entry exists or any period was posted or skipped by HomeHub (`acted_by` `auto` / `owner`), and when it runs it recreates `moze_schedule` empty (only then does a backup import with the old code refill it). Use it only for a migration that failed in step 1.

The rollback is the restore-and-swap of the phase 2a runbook with this script and the `*-pre-schedules-$TS.*` files. The script itself verifies the restored copy (phase 2a head `7b1e4a2c9d05` and the manifest counts), stops the backend, swaps the database names, checks out the pre-deploy backend commit and starts the backend; the restored dump already holds the pre-deploy `moze_schedule` rows, so no re-import is needed. After `ROLLBACK-DB-OK`, republish the previous SPA release (`spa-release-pre-schedules-$TS.txt`) as in the 2a rollback's step 1; Caddy needs nothing unless step 4 changed it (then restore `Caddyfile.bak-accounting-schedules` as written there). Keep `accounting_db_broken_<ts>` until the window ends, as in phase 2a.

Save as `~/backups/home-hub-schedules/rollback-schedules.sh` (mode `700`) in step 0:

```bash
#!/usr/bin/env bash
# Usage: rollback-schedules.sh <TS from step 0> [dump path: failure drill only]
set -euo pipefail
TS=${1:?usage: rollback-schedules.sh <TS> [dump]}
REPO=/home/opc/workspace/home-hub
B=$HOME/backups/home-hub-schedules
DUMP=${2:-$B/accounting_db-pre-schedules-$TS.dump}
MANIFEST=$B/accounting_db-pre-schedules-$TS.counts
PREV_BACKEND=$(cat "$B/backend-commit-pre-schedules-$TS.txt")
VENV=$REPO/services/accounting-service/.venv/bin
RTS=$(date -u +%Y%m%d%H%M%S)   # digits only: an unquoted database name is folded to lower case
COUNTS_SQL='SELECT (SELECT count(*) FROM account), (SELECT count(*) FROM ledger_entry), (SELECT count(*) FROM category), (SELECT count(*) FROM project), (SELECT count(*) FROM import_run)'
pg() { docker exec -i stonk-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -At -d "$0"' "$1"; }   # SQL on stdin
fail() { echo "ROLLBACK STOPPED: $*" >&2; exit 1; }

# a. preconditions: no leftover restore database, inputs present, clean checkout
[ "$(pg postgres <<<"SELECT count(*) FROM pg_database WHERE datname = 'accounting_db_restore'")" = 0 ] \
  || fail "accounting_db_restore exists (interrupted attempt?): inspect it, drop it explicitly with dropdb --force accounting_db_restore, then re-run"
{ [ -s "$DUMP" ] && [ -s "$MANIFEST" ]; } || fail "dump or manifest missing"
{ git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet; } || fail "production checkout has local changes"

# b. restore into a new database; any pg_restore error stops here (the schedules release keeps running, accounting_db untouched)
docker exec stonk-postgres-1 sh -c 'createdb -U "$POSTGRES_USER" accounting_db_restore'
docker exec -i stonk-postgres-1 sh -c 'pg_restore -U "$POSTGRES_USER" -Fc --exit-on-error --no-owner -d accounting_db_restore' < "$DUMP" \
  || fail "pg_restore failed; accounting_db untouched, accounting_db_restore left for inspection"

# c. verify the restored copy BEFORE any service stop or rename: phase 2a head and the manifest's row counts
WORK=$(mktemp -d) && chmod 700 "$WORK"
trap 'git -C "$REPO" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
git -C "$REPO" worktree add --quiet --detach "$WORK/old" "$PREV_BACKEND"
sed -e 's/^ACCOUNTING_DB=.*/ACCOUNTING_DB=accounting_db_restore/' "$REPO/.env" > "$WORK/old/.env" && chmod 600 "$WORK/old/.env"
HEAD_LINE=$(cd "$WORK/old/services/accounting-service" && { "$VENV/alembic" current 2>/dev/null || true; } | tail -1)
[ "$HEAD_LINE" = "7b1e4a2c9d05 (head)" ] || fail "alembic current on accounting_db_restore printed '$HEAD_LINE'"
GOT=$(pg accounting_db_restore <<<"$COUNTS_SQL")
WANT=$(sed -n 2p "$MANIFEST")
[ "$GOT" = "$WANT" ] || fail "restored row counts $GOT differ from the dump manifest $WANT"
echo "restore verified: 7b1e4a2c9d05 (head), counts $GOT"

# d. only now: stop the backend, require no sessions, swap names in one psql session, previous backend, start
cd "$REPO" && npx pm2 stop accounting-service
SESSIONS=$(pg postgres <<<"SELECT datname, pid, application_name, state FROM pg_stat_activity WHERE datname IN ('accounting_db', 'accounting_db_restore')")
if [ -n "$SESSIONS" ]; then
  echo "$SESSIONS" >&2
  npx pm2 start accounting-service
  fail "sessions connected (listed above); backend restarted on the schedules release; close them, dropdb --force accounting_db_restore, re-run"
fi
docker exec stonk-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1 -c 'ALTER DATABASE accounting_db RENAME TO accounting_db_broken_$RTS; ALTER DATABASE accounting_db_restore RENAME TO accounting_db;'"
[ "$(pg accounting_db <<<'SELECT version_num FROM alembic_version')" = 7b1e4a2c9d05 ] \
  || fail "accounting_db after the swap is not 7b1e4a2c9d05; backend left stopped"
git -C "$REPO" switch --detach "$PREV_BACKEND"
npx pm2 start accounting-service
for _ in $(seq 30); do [ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] && break; sleep 1; done
[ "$(curl -s http://localhost:8000/health)" = '{"status":"ok"}' ] || fail "health check failed after start on $PREV_BACKEND"
echo "ROLLBACK-DB-OK accounting_db_broken_$RTS"
```

Run it (`$TS` from step 0): `bash ~/backups/home-hub-schedules/rollback-schedules.sh "$TS"; echo "exit=$?"`. Expected: `restore verified: 7b1e4a2c9d05 (head), counts <the manifest's line>`, the pm2 stop output, `ALTER DATABASE` twice, the pm2 start output, `ROLLBACK-DB-OK accounting_db_broken_<ts>` and `exit=0`; a `ROLLBACK STOPPED:` line means what the 2a runbook says it means.
````

- [ ] 29.2 Add `ACCOUNTING_SCHEDULER_ENABLED` to `.env.example` right after the `ACCOUNTING_IMPORT_LOCKED` line:

```text
# Runs the schedule job inside accounting-service (00:05 Asia/Taipei, start-up, retries, after imports); false/0/no disable it
ACCOUNTING_SCHEDULER_ENABLED=true
```

```bash
cd /home/opc/workspace/home-hub-schedules && grep -n -A2 '^ACCOUNTING_IMPORT_LOCKED' .env.example
```

Expected: the comment and `ACCOUNTING_SCHEDULER_ENABLED=true` follow the `ACCOUNTING_IMPORT_LOCKED` line.

- [ ] 29.3 Edit `openspec/changes/add-accounting-schedules/design.md`:
  - replace the "Migration Plan" step 5 `5. Rollback: \`alembic downgrade\` to \`7b1e4a2c9d05\` (refused while HomeHub-posted data exists; delete it first or keep the revision), republish the previous SPA build, re-import the backup to refill \`moze_schedule\`.` with `5. Rollback: the phase 2a restore-and-swap (\`docs/deploy/accounting-schedules.md\`). \`alembic downgrade\` to \`7b1e4a2c9d05\` is only for a failed migration: it refuses while HomeHub-posted data exists and recreates \`moze_schedule\` empty; a backup import with the old code refills it.`;
  - replace the "Implementation notes" placeholder paragraph `(Filled in by the implementation tasks, …)` with bullet points from `/home/opc/workspace/moze-verify-schedules/notes.txt`: the dry-run aggregates (definitions per kind, `records_mapped`, `rewards_ignored`, `skipped_future` total and balance, review reasons, loans with a remainder difference), instance status counts after the first real import, the re-import result (0 created / deleted / adopted, identical instance hash), the job dry run line, import timings, the weekday-numbering and posting-mode conclusions of 28.5, the owner checklist result (pass / fail per item), and these plan decisions: `DefinitionOut.locked`, `category_icon`, `category_color`, `InstanceOut.amounts` and `rule_date`, `EntryScheduleOut.acted_by` and `posted_entry_ids` added to the API shapes; the entry form's definition mode is `/accounting/entry?schedule=<id>`; `PUT …/mode` moves `auto_post_from` only from `confirm` to `auto`; a `times` below the highest remaining seq is refused with 422 `times`; `reopen` of an import-posted period obeys the cutover lock.

- [ ] 29.4 Edit `docs/superpowers/specs/2026-10-03-scheduled-transactions-design.md`:
  - in the line beginning `- 597 enabled future-dated MOZE records`, replace `The importer keeps each as \`moze_schedule(kind='skipped_record')\`;` with `Phase 2a kept each as \`moze_schedule(kind='skipped_record')\`; the schedules release maps them to periods;`;
  - replace `The old \`moze_schedule\` table is retired (migration moves its rows).` with `The old \`moze_schedule\` table is dropped by the migration; the post-upgrade backup import recreates its content as definitions and periods.`

```bash
cd /home/opc/workspace/home-hub-schedules
grep -rn "migration moves its rows\|refill .moze_schedule.\|(Filled in by the implementation tasks" docs openspec/changes/add-accounting-schedules || echo "no stale text"
```

Expected: `no stale text`.

- [ ] 29.5 Remove the scratch directory and commit.

```bash
rm -rf /home/opc/workspace/moze-verify-schedules
cd /home/opc/workspace/home-hub-schedules
git add docs/deploy/accounting-schedules.md docs/superpowers/specs/2026-10-03-scheduled-transactions-design.md \
  openspec/changes/add-accounting-schedules/design.md .env.example
git commit -m "docs(accounting): schedules deploy runbook, implementation notes, superseded moze_schedule text

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git status --short
```

Expected: one commit; `git status --short` prints nothing.

## Spec coverage

Every requirement and scenario of the four spec files, with the task (and test) that implements and pins it. "2a suite" means a phase 2a test that stays green through the full-suite runs of Tasks 3, 18, 19 and 28.1 (baselines of Task 1).

### `specs/accounting-schedules/spec.md`

| Requirement / Scenario | Task — test |
|---|---|
| **Schedule definition model** | 2 (`test_definition_defaults`, `test_definition_checks`; `template_owner_edited` default), 3 (migration tests; `template_owner_edited` column), 5 (schemas), 12 (`DefinitionOut.template_owner_edited`) |
| Local recurring definition | 10 — `test_local_recurring_definition`, `test_day_of_month_before_the_start_day_starts_next_month` (anchor = occurrence 0); 4 — `test_occurrence_zero_before_the_anchor_day_is_indexed_and_normalized`; 2 — `test_definition_defaults` |
| Installment must be monthly | 10 — `test_installment_must_be_monthly_and_have_two_periods` |
| Installment needs two periods | 10 — `test_installment_must_be_monthly_and_have_two_periods` |
| **Schedule instance model** | 2 (`test_instance_defaults`, `test_instance_status_checks`, `test_a_day_is_posted_once`, `test_instances_cascade_with_their_definition`), 3 |
| One row per period | 2 — `test_one_row_per_period` |
| Override length checked | 5 (amount alignment), 12 (instance `PUT` 422 `amounts`) |
| **Template lines** | 5 — `schedule_templates` tests (`test_transfer_lines`, line kinds, signs) |
| Loan repayment template | 5 |
| Currency mismatch refused | 5, 10 |
| **Rows referenced by definitions** | 13 |
| Archiving a paying account | 13 — `test_archiving_a_paying_account_is_refused` |
| Deleting a loan with an active schedule | 13 — `test_deleting_a_loan_with_an_active_schedule_is_refused`; categories / projects / counterparties: `test_categories_projects_and_counterparties_are_protected_until_the_schedule_ends` |
| **Instance generation** | 4 (`schedule_rules`), 6 (`schedule_generation`) |
| 13-month horizon | 6 |
| Month-end anchor clamps and returns | 6 — `test_month_end_anchor_generates_feb_28_then_returns_to_31`; 4 — `test_month_end_and_leap_day_clamp_from_the_anchor` (Review Focus 1) |
| Finite run | 6 |
| End date stops the run | 6 |
| Rolling forward | 6; 15 (job generation) |
| A period moved later does not shift the series | 6 |
| A period moved earlier does not repeat the series | 6 |
| **Posting an instance** | 7 (ledger `source=`, bypass), 8 (`schedule_posting`) |
| Repayment with interest | 8 — `test_repayment_with_interest`; lock order of a loan post: 8 — `test_posting_a_loan_period_holds_the_definition_for_update_and_locks_later_periods`, 19 — `test_loan_posts_of_neighbouring_periods_queue_without_deadlock` |
| Last repayment clamped to the open amount | 8 |
| Posting twice is refused | 8 — `test_posting_twice_is_refused`; 12 (409 on `post`) |
| Closed loan ends the schedule | 8 — `test_closed_loan_ends_the_schedule`, `test_loan_settled_by_hand_while_pending_skips_the_rest_and_ends` (Review Focus 4); 19 — `test_loan_close_out_waits_for_a_delete_that_revives_the_definition`, `test_delete_after_a_loan_close_out_is_refused_with_a_retry_message` |
| Archived account found at posting time | 8 — `test_archived_account_found_at_posting_time_names_the_line`; 12 — `test_archived_account_found_at_posting_time` |
| Zero override leaves a line out | 8 |
| Recurring transfer | 8; 7 — `test_create_transfer_with_schedule_source` |
| **Daily schedule job** | 15 |
| No silent backlog after an import | 15 — `test_no_silent_backlog_after_an_import` |
| Back-dated local definition | 15 — `test_back_dated_local_definition` |
| Only one runner | 15 — `test_only_one_runner`, `test_job_run_twice_in_one_day_posts_each_instance_once` (Review Focus 2) |
| Retry after an import | 15 — `test_retry_after_an_import`, `test_after_run_adds_and_clears_the_retry`, `test_retry_stops_on_a_new_taipei_day` |
| Failure stops the loan, not the others | 15 — `test_failure_stops_the_loan_not_the_others`, `test_a_period_deleted_mid_run_is_passed_over` |
| Paused definition | 15 — `test_paused_definition` |
| Dry run | 15 — `test_dry_run`; 28.8 on real data |
| A moved earlier period holds the next one | 15 — `test_moved_earlier_seq_is_attempted_before_the_next_one`, `test_failed_moved_earlier_seq_holds_the_next_one_until_the_owner_posts_it`, `test_a_failed_earlier_period_the_job_does_not_own_still_holds_the_series` (Multica R-F6) |
| (importer CLI runs the job inline, requirement text) | 15 — `test_cli_import_job_posts_only_when_the_scheduler_switch_is_on`, `test_due_but_unposted_are_counted_when_posting_is_off`; 18 — `test_cli_import_runs_the_job_inline_after_releasing_the_lock`; 28.7–28.8 on real data (Multica R-F4) |
| **Definition endpoints** | 5 (schemas), 9 (read shapes), 10 (CRUD, 409 / 422 cases) |
| Card installment remainder on the last period | 6 (split), 10 |
| Loan and schedule in one call | 10 (today 2026-10-09, so the 13-month horizon holds all 13 instances the scenario counts) |
| Edit regenerates only future pending periods, then rolls forward | 10 — `test_edit_regenerates_only_future_pending_periods_then_rolls_forward`, `test_edit_keeps_the_instance_due_today_and_regenerates_from_tomorrow` (Review Focus 5), `test_put_without_posting_mode_keeps_the_stored_mode` |
| Edit keeps a month-end anchor | 10 — `test_edit_keeps_a_month_end_anchor_then_rolls_forward_on_the_31st` (implicit and explicit `day_of_month`), `test_edit_keeps_a_leap_day_yearly_anchor_then_returns_to_feb_29` (Multica R-F3) |
| Edit with a new anchor takes its day | 10 — `test_edit_with_a_new_anchor_takes_its_day` (implicit and explicit) |
| Delete refused after posting | 10 |
| **Definition state endpoints** | 11 (pause, resume, end, mode, catch-up; 409 / 422 cases) |
| Resume skips the paused months by default | 11 |
| End removes pending periods | 11 |
| Ending an ended definition | 11 |
| Catch-up posts the backlog in order | 11 (and `test_catch_up_stops_at_the_first_failure_and_still_answers_200`, `test_catch_up_records_any_error_by_class_and_stops`: any error is stored in `last_error`) |
| Catch-up refused while paused | 11 |
| Switching to automatic posting | 11; 15 — `test_mode_switch_then_the_job_posts_only_today` |
| Write during an import | 11 (409 `import_running` on every state action); 5 (`ImportRunningError`); 19 (race tests) |
| **Instance endpoints** | 9 (queue read), 12 (edit, post, skip, reopen, repost, accept-partial; 409 / 422 cases) |
| 待完成交易 queue | 9, 12 |
| Skip leaves the loan open | 12 |
| Reopen revives an ended definition | 12 (`test_reopen_of_a_posted_period_deletes_its_entries` and the scenario test) |
| Repost a repayment with a corrected amount | 12; 8 — `test_delete_period_entries_locks_the_loan_in_the_same_statement_and_keeps_it`; 19 — `test_repost_waits_for_a_delete_of_its_entry`, `test_loan_delete_waits_for_a_repost_on_an_ended_definition` |
| Accept a partial period | 12 |
| Date edit onto a posted day refused | 12 — `test_date_edit_onto_a_posted_day_refused`, `test_date_edit_onto_another_pending_period_refused`; posting failures of any class: `test_post_records_any_error_and_answers_409` |
| **Instance amount edit scope** | 12 (`_apply_amount_scope`; `test_following_replaces_the_edited_periods_own_override_and_keeps_later_owner_edits`, `test_installment_scope_keeps_the_last_period_remainder`), 17 (re-import keeps the owner's template amounts), 24 (SPA `套用範圖`) |
| Only this period by default | 12 — `test_only_this_period_by_default` |
| This period and the following ones keep the old price before | 12 — `test_this_period_and_the_following_ones_keep_the_old_price_before` |
| All periods keep other owner edits | 12 — `test_all_periods_keep_other_owner_edits` |
| Installment total kept by a scoped edit | 12 — `test_installment_following_keeps_the_total_against_the_actual_allocations`, `test_installment_scope_counts_posted_and_owner_edited_periods`, `test_installment_scope_keeps_the_last_period_remainder` (Multica R-A1) |
| Scoped edit that would overbook refused | 12 — `test_installment_scope_that_would_overbook_is_refused` |
| A scope with another field is refused | 12 — `test_a_scope_with_another_field_is_refused` |
| Imported definition before cutover | 12 — `test_scope_edit_allowed_on_an_imported_definition_before_cutover` |
| **Run-now endpoint** | 15 (and `test_run_now_needs_the_bearer_token_when_api_tokens_are_set`: #42 token auth, design D42); 10 — `test_schedule_writes_need_the_bearer_token_when_api_tokens_are_set` |
| Manual run while the job runs | 15 — `test_run_now_refused_while_the_job_runs` |
| Import starts mid-run | 15 — `test_import_starts_mid_run`, `test_run_now_refused_while_an_import_runs` |
| **Deleting entries of a posted period** | 8 (`delete_period_entries`), 13 (entry hooks), 19 (race: two deletes of one period) |
| Deleting the interest leaves a partial period | 13 — `test_deleting_the_interest_leaves_a_partial_period` |
| Deleting the last entry reopens the period | 13 — `test_deleting_the_last_entry_reopens_the_period` |
| Deleting a MOZE-booked period | 13 — `test_deleting_a_moze_booked_period` |
| Deleting from an ended definition revives it | 13 — `test_deleting_from_an_ended_definition_revives_it`; 19 — `test_two_deletes_reviving_an_ended_definition_queue` |
| **Imported definitions before cutover** | 10, 11, 12 (409 `locked_until_cutover`), 8 (bypass for the loan target) |
| Template edit refused during the mirror period | 10 |
| Repost of a MOZE-booked period refused before cutover | 12 |
| Imported period posts against an imported loan | 8; 7 — `test_settle_bypasses_the_cutover_lock_only_when_asked` |
| **Loan summary** | 9 |
| Loan detail after three periods | 9; 14 — `test_loan_detail_after_three_periods` |
| Card installment remaining from posted entries | 9 |
| **Schedule links on entries** | 14 |
| Pill data on a posted entry | 9; 14 — `test_entry_rows_carry_the_schedule_link`, `test_passbook_and_detail_carry_the_link` |

### `specs/accounting-ledger/spec.md`

| Requirement / Scenario | Task — test |
|---|---|
| **Ledger entry model** (MODIFIED: `source` gains `schedule`; `interest` may carry the loan's counterparty) | 2 (`ENTRY_SOURCES`), 3 (`test_upgrade_adds_schedule_source_and_tables_and_drops_moze_schedule`), 8 (interest counterparty) |
| Entry currency follows its account | unchanged — 2a suite; schedule postings go through `entry_write_service` (7, 8) |
| Converted entry keeps its original amount | unchanged — 2a suite |
| A paired transfer shares a group id | unchanged — 2a suite; 7 — `test_create_transfer_with_schedule_source` |
| Posting date defaults to the entry date | unchanged — 2a suite; 8 (`entry_date = posted_date = due_date`) |
| Migration links existing counterparties | unchanged — 2a migration tests (run in 3.6) |
| Loan interest names the lender | 8 — `test_repayment_with_interest` (`interest.counterparty_id == loan.lender.id`) |
| **Schedule-sourced entries** | 7, 13 |
| Schedule entry survives a backup import | 18 — `test_schedule_entry_survives_a_backup_import` |
| Schedule entry editable before cutover | 7 — `test_schedule_entry_editable_before_cutover_and_keeps_its_source`, `test_scheduled_transfer_update_keeps_its_source` |
| Deleting one leg of a scheduled transfer | 13 — `test_deleting_one_leg_of_a_scheduled_transfer` |
| Split edit on a scheduled group refused | 13 — `test_split_edit_on_a_scheduled_installment_group_refused` (the scenario's installment group), `test_split_edit_on_a_scheduled_group_refused` (split kind) |

### `specs/accounting-moze-backup-import/spec.md`

| Requirement / Scenario | Task — test |
|---|---|
| **Record mapping** (MODIFIED: future rows → instances; HomeHub-posted / skipped past records not imported) | 16, 17, 18 |
| Expense with fee | unchanged — 2a suite |
| Future installment skipped | 16 — `test_installment_anchored_on_its_first_period`; 17 — `test_weekly_period_past_records_post_and_future_ones_wait`, `test_installment_lines_resolve_the_loan`; 18 — `test_skipped_future_adds_up` |
| Period already posted by HomeHub | 18 — `test_period_already_posted_by_homehub`, `test_a_period_homehub_skipped_is_not_imported`, `test_single_record_homehub_posted_is_not_imported_once_it_is_past` (a `record:<id>` single turned past) |
| **Transactional full replace and report** (MODIFIED: schedule row locks, lock wait, template usage, re-pointing, `schedules` block, job trigger) | 17, 18, 19 |
| Balances equal MOZE | 2a suite; 18 — stand-in schedule entries in `moze_part` (`test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record`, Review Focus 3); 28.4 on the real backup |
| Manual entries do not break strict mode | unchanged — 2a suite |
| Re-import is idempotent | 17 — `test_reimport_is_idempotent_for_schedules`; 28.7 on the real backup |
| Manual rows survive | 2a suite; 17 — `test_reimport_keeps_owner_decisions_and_local_definitions`; 18 — `test_schedule_entry_survives_a_backup_import` |
| (lock wait and job trigger, requirement text) | 18 — `test_import_waits_for_schedule_writers_then_runs`, `test_import_is_refused_at_once_while_another_import_runs`, `test_real_import_triggers_a_job_run_and_a_dry_run_does_not`; 19 — `test_import_waits_for_a_post` (proves only that `import_lock` waits for a shared holder; the importer transaction's `lock_schedule_rows` against a post is excluded by the key itself, not raced) |
| **Schedule definitions and instances from the backup** | 16 (`schedule_import_map`), 17 (`schedule_import`), 18 (wiring) |
| Real backup schedule counts | 28.4 (11 + 14 definitions, 534 mapped, 63 rewards); 16 — `test_counts_add_up_to_the_enabled_future_records` |
| Weekly period keyed by weekday | 16 — `test_weekly_period_keyed_by_weekday`, `test_moze_weekday_numbering`; 17; 28.5 (numbering on real data) |
| Monthly period keyed by day of month | 16 — `test_monthly_transfer_period_keyed_by_day_of_month`, `test_finite_period_never_runs_past_its_last_record`; 17 — `test_period_without_future_records_imports_ended` (only live periods run), `test_not_live_end_keeps_owner_and_homehub_rows_and_reverses_when_moze_resumes` (the end is reversible and deletes no owner-edited or HomeHub-generated row, D37) |
| Day of month clamped | 16 — `test_day_of_month_clamped` |
| Weekday mismatch flagged | 16 — `test_weekday_mismatch_flagged`, `test_period_type_must_match_its_records`; 17 — `test_interval_mismatch_definition_never_generates` |
| Installment anchored on its first period | 16 — `test_installment_anchored_on_its_first_period` |
| Repayment and interest are one period | 16, 17 — `test_repayment_and_interest_are_one_period`, `test_installment_lines_resolve_the_loan`; 16 — `test_a_later_package_kind_joins_the_template`; 17 — `test_template_change_realigns_kept_overrides`, `test_account_missing_definition_realigns_moze_amounts_to_the_kept_template`, `test_row_matched_by_a_record_id_takes_the_new_primary_id` |
| Disabled future period | 16 — `test_disabled_future_period`; 17 — `test_disabled_future_period_is_skipped` |
| Single record keyed apart from definitions | 16 — `test_single_record_keyed_apart_from_definitions`; 17 — `test_single_record_becomes_its_own_definition` |
| HomeHub-generated period adopted | 17 — `test_homehub_generated_period_adopted` |
| Posted HomeHub period adopted | 18 — `test_posted_homehub_period_adopted` |
| Moved period adopted by its rule date | 17 — `test_moved_period_adopted_by_its_rule_date` |
| Seq conflict reported, not fatal | 17 — `test_seq_conflict_reported_not_fatal` |
| New review reason pauses, resolved one clears | 17 — `test_new_review_reason_pauses_and_a_resolved_one_clears` |
| Re-pointing finds no loan | 18 — `test_re_pointing_finds_no_loan` |
| Owner-edited pending period kept | 17 — `test_owner_edited_pending_period_kept` |
| Owner-set price survives a re-import | 17 — `test_owner_set_template_amounts_survive_a_reimport` |
| Owner-edited period compared on re-import | 17 — `test_owner_edited_pending_period_on_an_owner_priced_definition_is_compared` (Multica R-A2) |
| Owner-edited period whose record turned past | 18 — `test_owner_edited_period_whose_record_turned_past_is_booked_once` (Multica R-F1) |
| Re-import keeps owner decisions and local definitions | 17 — `test_reimport_keeps_owner_decisions_and_local_definitions`, `test_import_row_whose_record_left_the_backup_becomes_skipped`, `test_pending_imported_row_follows_moze_and_clears_failure_marks`, `test_moze_date_refresh_never_lands_on_another_pending_period` |
| Different amount reported | 18 — `test_different_amount_reported` |
| Different allocation reported per line | 18 — `test_equal_totals_with_a_different_allocation_are_reported_per_line`; identical amounts: `test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record` (Multica R-F5) |
| CLI import runs the job | 18 — `test_cli_import_runs_the_job_inline_after_releasing_the_lock` (scheduler on / off); 15 — `test_cli_import_job_posts_only_when_the_scheduler_switch_is_on` (Multica R-F4) |
| Loan link survives the full replace | 18 — `test_loan_link_survives_the_full_replace`; `loan_remainder_check` after re-pointing: `test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record` |
| Template reference keeps a category | 18 — `test_template_reference_keeps_a_category_and_an_account` |
| **REMOVED: Scheduled data preserved for phase 4** | 2 (`MozeSchedule` and `GET /imports/schedules` removed), 3 (drops `moze_schedule`; `test_downgrade_recreates_an_empty_moze_schedule`), 18 — `test_the_old_schedule_listing_is_gone`, 27 (SPA no longer calls it), 29 (runbook) |

### `specs/frontend-accounting-ledger/spec.md`

| Requirement / Scenario | Task — test |
|---|---|
| **Accounting settings page** (MODIFIED: 週期／分期 link, `schedules` report block) | 27 |
| Rename a counterparty | unchanged — 2a `accounting-settings.spec.ts` (kept in 27.1) |
| Backup dry run from the page | unchanged — 2a spec (kept, its real-run test only loses the schedules request); 27 — `summarises the schedules block` |
| Pending amount differences shown | 27 — `shows pending amount differences when they are the only finding` (Multica R-A3) |
| Schedule list moved to 提醒中心 | 27 — `shows the static lock state and links 週期／分期 to the reminder centre` |
| **Entry form schedule tabs** | 20 (`schedule-math`), 21 (`app-schedule-tabs`), 22 (save as definition, definition mode) |
| Monthly Netflix | 22 |
| Catch-up retried without a second create | 22 — `retries only the catch-up after it failed: exactly one create request`, `surfaces a catch-up answered 200 with a failed period` (Multica R-F2) |
| Recurring transfer from the form | 22 |
| Card installment split | 20 — `splits an installment with the remainder on the last period`; 21 |
| New loan with interest | 22 |
| **Scheduled entry edit scope** | 26 |
| Correct one period's interest | 26 — `corrects one period's interest with 編輯這一筆`, `sends a member of a scheduled split group to the repost panel, never to the entry form` |
| Deleting a MOZE-booked period's last entry | 26 — `words what deleting does to the entry's period` |
| **待完成交易 tab** | 23 (the `last_error` text with 重試: `shows the error of a failing period with 重試 instead of 入帳, and 重試 posts it again`) |
| Overdue confirm item | 23 |
| Skip prompt keeps the remaining | 23 |
| Back-dated start waits for the owner | 22 (catch-up only for `auto` anchored today); 23 |
| Unsupported fields hidden | 22 — `hides the fields a schedule cannot carry`, `keeps the currency pill while a rate is set, so the owner can remove it on 週期` |
| Post from the queue | 23 |
| Catch-up offered for a backlog | 23 |
| **週期／分期 section** | 24 |
| Price change from this period on | 24 — `asks 套用範圖 only when an amount changed`, `sends scope following from .scope-following` (and `this` / `all`) |
| Scope question cancelled | 24 — `focuses 僅這一期, and Esc cancels without saving or closing the sheet` |
| Loan row | 24 (and `does not say 目前沒有待處理項目 on 借還款追蹤 while schedules are listed`) |
| Ended loan still open needs a check | 24 |
| Pause from the sheet | 24 |
| **Reminder bell count** | 25 |
| Bell includes due confirm items | 25 — `counts 待完成交易 items due today or earlier` |
| Partial period counted until accepted | 25 — `counts a partial period until it is accepted` |
| Paused definition not counted | 25 — `stops counting the item of a definition the owner paused` |
| **Loan breakdown in entry detail** | 26 |
| Loan detail line | 26 — `shows the loan line and links to its schedule` (剩餘 once), `words a lent-money loan with 已收` |
| **Import-running feedback** | 20 (`AccountingToastService`, `scheduleActionError`), 23, 24, 26 |
| Posting during an import | 23; 26 — `shows the toast when an import holds the period`; 20 — `turns import_running into the toast and anything else into an error line` |
| **Schedule pills** | 20 (`schedulePill`), 25, 26 |
| Installment pill | 20 — `names schedule pills`; 25 — `puts the schedule pill first, in MOZE wording`; 26 |
| Unlimited recurring pill | 20 — `names schedule pills`; 25; 14 (link data) |
| Partial period pill | 25 — `adds 部分 to a partial period`; 26 — `offers 重新入帳 and 保留部分 on a partial period` |

Gaps: none.
