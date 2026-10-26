# Card statement auto-pay — design

**Status:** rev 1, draft for review. **Owner decision:** 2026-10-26 「自動扣繳做選項 1」 — the fully automatic form, not the one-tap confirmation.
**Code paths** below are relative to `services/accounting-service/` unless they start with `frontend/`. Line numbers are from `5b519a2` (R6c, #177) and `f2d8498` (main after #185).

## 1. Intent

Every live card in HomeHub that has an `auto_pay_account_id` is paid by its bank automatically: on the statement's due date (or the next business day when that falls on a weekend or holiday) the bank debits the full 本期應繳總額 from the linked bank account and credits the card. Today HomeHub knows all of this — the card statement carries `due_date` and `statement_total`, the account carries the linked bank account — and does nothing with it. `auto_pay_account_id` is stored by the settings page and the MOZE import and read by no service (`app/services/settings_service.py:487-490` validates it; nothing else uses it).

The consequence, seen on 2026-10-26: the owner books every card payment by hand (through MOZE), the accounts list shows 已逾期 until that happens (and, per #188, even after it when the payment was booked on a combined child card), and the later bank and card statements carry debit / payment lines that only match because the owner typed the transfer.

This feature makes HomeHub book that transfer itself, as a system entry pair, on the day the bank debits it. The owner stops typing card payments. The bank statement's debit line and the next card statement's payment line claim the pair when they arrive; if the owner (or an old MOZE habit) books the same payment anyway, the owner's entry wins and the system pair is retired, exactly as stand-ins already work for backfilled lines.

## 2. Goals, non-goals, anti-goals

**Goals**
1. A card statement with `due_date`, `statement_total > 0` and a linked, eligible bank account is paid in the ledger on its pay date without the owner doing anything.
2. The pair is recognised as a stand-in on **both** sides (the card family's statements and the bank account's statements), so the matcher prefers owner entries over it, owner-wins retires it, and an unclaimed leg surfaces as an orphan.
3. One booking per statement, ever. Re-runs, restarts, re-parses and double timers never book twice.
4. The owner can switch it off, cap it, and see every decision (booked / skipped and why) in the audit trail.
5. The 已逾期 pill clears on the pay date for a card whose statement was auto-paid.

**Non-goals**
- Minimum-payment or partial payments. The owner pays in full; the bank debits `statement_total`.
- Booking payments for statements already due before the feature is switched on (rollout cutoff, §5.6).
- A UI for the auto-pay rows. The legs are ordinary entries (source `statement`, name 自動扣繳) and appear wherever entries do; the audit rows appear in the existing audit list.
- Business days of any country but Taiwan; the holiday table is Taiwan's public holidays.
- Cards without statements in HomeHub (e.g. 元大黑啤, which also has no `auto_pay_account_id`). No statement → nothing to pay.
- Fixing #188 (billing counts payments on the master only). Independent frontend issue; this feature books on the master, so it is not affected.

**Anti-goals** (a technically working build that does these has failed)
- Booking a payment the bank will not make: a card whose bank link is wrong (`auto_pay_account_id` pointing at a credit card or an account in another currency), a statement whose header is under `parse_review`, or a statement total ≤ 0.
- A duplicate beside the owner's own booking, in either order (owner first → skip; system first, owner later → retire the system pair).
- Silently changing a payment the owner has already edited.

## 3. Current state (facts the design rests on)

- `AccountStatement` has nullable `closing_date` / `due_date`, `statement_total` (NOT NULL), `minimum_payment`, `mode` ∈ historical|live, `status` ∈ open|reconciled, `kind` ∈ card|bank (`app/models/statements.py:137-169`). `due_date` is parsed by the worker (`worker/parse_schema.py:41`) and copied onto the statement when a revision becomes current (`app/services/statement_revision_service.py:511-513`). No guardrail checks it (`app/services/statements/derive.py:160-168`). A re-parse that changes `due_date` or `statement_total` is a header change: the new revision does not become current and a live statement opens a `parse_review` case (`statement_revision_service.py:56, 658, 684-687`).
- `Account.auto_pay_account_id` (`app/models/ledger.py:130`) must be another existing, non-archived account; there is no `is_credit` or currency check (`settings_service.py:487-490`). Archiving the target clears the link (`:602-605`).
- Transfers are created by `transfer_service.create_transfer(db, payload: TransferIn, *, source="manual", remember=True) -> UUID` (`app/services/transfer_service.py:139`): two legs `transfer_out` (−amount) / `transfer_in` (+amount) sharing `transfer_group_id`, `source`, dates; cross-currency needs `in_amount` (`:91-92`). The schedule job books transfers through it with `source="schedule"` (`app/services/schedule_posting.py:166`). `DELETE /entries/{leg}` deletes both legs (`entry_write_service.delete_entry`, `locked_with_legs`).
- System entries from reconciliation carry `source='statement'` and are anchored to a `ReconciliationAction` row with `case_id` NOT NULL and nullable `event_id` (`app/models/statements.py:349-370`; `reconciliation_actions/engine.py:277-279`). A stand-in is recognised by `stand_ins.stand_in_entry_ids` as an entry created by an applied `BACKFILL_ACTIONS` action whose case statement is in the account family **and** whose own account is in that family (`app/services/stand_ins.py:21, 27-39`). `pair_transfer` therefore treats the peer leg as an owner entry (`reconciliation_actions/transfer_actions.py:316-318`). Owner-wins: `shadows()` (`stand_ins.py:296-313`) opens `entry_unmatched` with hint `shadowed_by_stand_in`, resolved by `replace_stand_in` (policy default on, `settings_service.py:127`); `owner_may_record` / `owner_twins` (`:260-293`) stop new bookings beside an owner twin. `orphans()` (`:316-339`) needs the creating action's `event_id`. `coverage_service._retire_creations` / `_retire_stranded_creations` (`coverage_service.py:556-610`) mark an action reverted when the owner deletes its entries, scoped to the swept statement's cases.
- Matcher gates (`app/services/statements/matching.py`): card `payment` → {transfer_in} with `_peer_ok` requiring the peer leg's account to be non-credit (`:190-198, 325-327`); bank `withdrawal` → {expense, transfer_out, fee, settlement_out, receivable}, bank `payment` → {settlement_out, transfer_out}, bank `transfer_out` → {transfer_out} (`:199-211`). Windows: exact 3 days, near 5 days (amount tolerance 10 abs / 5 %), drift 15 days, candidate population ±`window_days` of the period (`:125-140`; `reconciliation_service.py:254-255`). Participating accounts of a statement = the account plus its combined children (`coverage_service.participating_accounts`, `:188-191`).
- No business-day or holiday logic exists anywhere (`derive.py:17` is the only mention). Schedules post on the nominal date.
- Settings: one JSONB row with `version`; `_check_policy` refuses unknown keys (`settings_service.py:266-312`); `PUT /settings/reconciliation` replaces `data` wholesale (`:394-418`) and needs `admin`.
- The worker token is `ingest`-only and lease-bound (`worker/README.md:429`); `POST /transfers` needs `write` (`app/routers/transfers.py:12`). The API already runs an in-process APScheduler cron job for schedules with an advisory lock and a `--dry-run` CLI (`app/services/schedule_job.py:1-9, 326`; started from `app/main.py:38-39`).
- Observed data (live card statements with auto-pay, 2026-07 → 2026-10): the owner booked every payment on the due date itself; the card statement's `payment` line is dated 0–5 days after the due date (HSBC: due Fri 07-24 → line Mon 07-27; due Thu 09-24 → line Tue 09-29); the bank side posts on the debit day. Pending at the time of writing: 匯豐旅人 155 (66,227) and HSBC Live+ 156 (1,148), due Sat 2026-10-24, which the bank debits Mon 2026-10-27.
- Alembic head: `a7c3e1d9b2f4` (`alembic/versions/a7c3e1d9b2f4_owner_rules.py`).

## 4. Design

### 4.1 Vocabulary

- **Auto-pay candidate**: a live card statement (`kind='card'`, `mode='live'`) whose account (the statement account, i.e. the family master) has `auto_pay_account_id`, with `due_date IS NOT NULL` and `statement_total > 0`.
- **Pay date**: `due_date` moved forward to the first business day on or after it (§4.3).
- **Auto-pay pair**: the `transfer_out` leg on the bank account and the `transfer_in` leg on the card master, both `source='statement'`, `name='自動扣繳'`, `entry_date = posted_date = pay date`, amount `statement_total`, `needs_review = False`.
- **Auto-pay row**: the provenance row in the new table `statement_auto_pay` (§4.2), one per statement.

### 4.2 Provenance: table `statement_auto_pay`

A new table instead of a `ReconciliationAction`: an action needs a case (`case_id` NOT NULL) and a case needs a statement line, and at booking time no line exists yet on either side. The row is the anchor that `stand_ins`, retirement and orphan detection use for this kind of stand-in.

| column | type | notes |
|---|---|---|
| `id` | serial PK | |
| `statement_id` | FK `account_statement` RESTRICT, **UNIQUE** | the idempotency key: one row per statement, ever |
| `card_account_id` | FK `account` | the statement account (family master) |
| `pay_account_id` | FK `account` | `auto_pay_account_id` at booking time |
| `amount` | MONEY | `statement_total` at booking time |
| `due_date`, `pay_date` | DATE | |
| `transfer_group_id` | UUID, nullable | NULL when `status='skipped'` |
| `out_entry_id`, `in_entry_id` | FK `ledger_entry` ON DELETE SET NULL, nullable | |
| `status` | enum `booked` / `retired` / `owner_deleted` / `owner_edited` / `skipped` | §4.5 |
| `reason` | varchar(64), nullable | skip / retire reason code |
| `settings_version` | int | the `reconciliation_settings.version` the booking read |
| `created_at`, `updated_at` | timestamptz | |

`skipped` rows are terminal **only** for reasons that cannot change (`owner_booked`, `cutoff`); the job rewrites a `skipped` row with a transient reason (`parse_review_open`, `no_link`, `link_ineligible`, `over_cap`, `monthly_budget`) when the condition clears — see §4.5. Migration: new revision after `a7c3e1d9b2f4`; also widens `reconciliation_audit.kind` values (§4.7) if that column is an enum (builder checks; if it is free text, no change).

### 4.3 Pay date and the business-day calendar

`pay_date = next_business_day(due_date)`, where a business day is Monday–Friday and not in the holiday set. The holiday set is `TW_HOLIDAYS` (a static frozenset of ISO dates in a new module `app/services/business_days.py`, seeded with the Taiwan public holidays for 2026 and 2027 as published by the 行政院人事行政總處 calendar, including the make-up workdays as non-holidays) **union** `settings.auto_pay.extra_holidays`, **minus** `settings.auto_pay.extra_workdays`. The two settings lists let the owner correct a year the module does not cover without a deploy. A statement whose `due_date` is more than 400 days past the last date the module knows is still shifted for weekends only; the job audits `auto_pay_calendar_stale` once per day so the owner knows to add the year.

**My call:** the ledger date is the debit day (the shifted date), not the printed due date, because that is what the bank statement will print and what the card statement's payment line is closest to. The owner has so far dated these on the due date; the matcher windows (exact 3, near 5, drift 15) absorb either choice.

### 4.4 The job

`app/services/auto_pay_job.py`, modelled on `schedule_job.py`: an APScheduler `CronTrigger` inside the API process, daily at **06:00 Asia/Taipei**, plus one run at API start-up (so a restart or a missed day catches up), guarded by a Postgres advisory lock so two API processes never both book; CLI `python -m app.services.auto_pay_job [--dry-run] [--as-of YYYY-MM-DD]` that prints the decisions without writing when `--dry-run`. It runs inside the API because booking needs `write` and the worker token is `ingest`-only; no new token, scope or endpoint.

One run, in one transaction per statement (a failure on one statement never blocks the others):

1. **Enabled?** `settings.auto_pay.enabled` false → return, audit nothing (the CLI prints `disabled`).
2. **Candidates**: auto-pay candidates (§4.1) with `pay_date ≤ today` and `pay_date ≥ settings.auto_pay.enabled_from` and no `statement_auto_pay` row in a terminal state, ordered by `pay_date, statement_id`. Statements without `due_date` are not candidates: they are listed by the dry-run as `no_due_date` so the owner can see a parser gap, but nothing is booked from `due_rule`/`due_value` (**my call**: a guessed date is how you book a payment the bank did not make).
3. **Gates**, in order, each ending in a `skipped` row with the reason named (transient reasons are retried on later runs):
   - `parse_review_open`: the statement has an open `parse_review` case → wait.
   - `no_link`: `auto_pay_account_id` is NULL on the master (a child card's link does not count; the statement is the master's).
   - `link_ineligible`: the linked account `is_credit`, is archived, or has another currency than the card. (Also fixed at the source: `settings_service` refuses such a link from now on, §4.8.)
   - `owner_booked`: an owner (non-`statement` source) `transfer_in` on any account of the card family **or** a `transfer_out` on the pay account whose peer leg is in the card family, with `amount == statement_total` and `entry_date` within `[pay_date − twin_window_days, pay_date + twin_window_days]` (default 7). Terminal: the owner has paid this statement by hand.
   - `cutoff`: `pay_date < enabled_from`. Terminal.
   - `over_cap`: `statement_total > settings.auto_pay.max_amount`.
   - `monthly_budget`: Σ `amount` of `booked` rows with `pay_date` in the calendar month of this pay date + `statement_total` > `settings.auto_pay.monthly_total`.
4. **Book**: `transfer_service.create_transfer(db, TransferIn(from_account_id=pay, to_account_id=card_master, out_amount=statement_total, entry_date=pay_date, posted_date=pay_date, name="自動扣繳", description=f"{card.name} {period_end:%Y-%m-%d} 帳單 自動扣繳"), source=STATEMENT_SOURCE, remember=False)`; insert the `statement_auto_pay` row (`booked`); audit `auto_pay_booked`. The row insert and the two legs commit together; the UNIQUE on `statement_id` makes a concurrent duplicate fail the second transaction.
5. **Housekeeping** (every run, before step 2): for each `booked` row,
   - both legs gone → `owner_deleted` (the owner removed the payment; never re-book), audit `auto_pay_owner_deleted`;
   - a leg's `source` is no longer `statement`, or its amount / date / accounts differ from the row → `owner_edited`, audit once; the row keeps its original figures; the legs are the owner's now;
   - the statement's current `statement_total` or `due_date` differs from the row (a `parse_review` resolved to new figures after booking) → set `needs_review = True` on both legs and audit `auto_pay_header_changed` once; the owner edits or deletes the transfer. No automatic re-booking or amount change (**anti-goal 3**).

### 4.5 Stand-in semantics on both sides

`stand_ins.stand_in_entry_ids(db, account_ids)` returns, in addition to today's set, every `out_entry_id` / `in_entry_id` of a `statement_auto_pay` row with `status='booked'` whose entry is on an account in `account_ids`. Both legs are therefore stand-ins — the `transfer_in` leg on the card family's statements, the `transfer_out` leg on the bank account's statements. Everything that keys off `stand_in` then applies unchanged: the matcher drops the stand-in when an owner representation exists (`matching.py:682`), `_exact_pair` prefers owner rows, `owner_population` excludes them.

What has to be taught about auto-pay rows explicitly:

- **Owner-wins (`shadows` → `replace_stand_in`)**: when an owner entry and an auto-pay leg both represent the same line, the `shadowed_by_stand_in` case is resolved by `replace_stand_in` as today, and for an auto-pay leg the action **retires the whole pair**: deletes both legs (through the same cascade a transfer delete uses), sets the row `retired` with `reason='owner_twin'`, audits `auto_pay_retired`. The owner's pair takes both lines on its own (the peer line matches the owner's other leg by the ordinary gates). The policy may apply this automatically under the existing `replace_stand_in` toggle and its exact-window rule.
- **Orphan**: `orphans()` additionally reports an auto-pay leg on this statement's account family with **no active coverage anywhere** when the swept statement's period contains `pay_date` and the statement is current (its sweep has run). Same hint `orphaned_stand_in`, same `entry_unmatched` case; the owner resolves it by deleting the pair (→ `owner_deleted`) or by matching it. Not reported before that statement exists: a leg waiting for next month's statement is not an orphan.
- **Retirement on delete**: `_retire_creations` / `_retire_stranded_creations` stay as they are (they key off actions). The auto-pay equivalent is the job's housekeeping (§4.4 step 5), which does not depend on a statement sweep.
- **Twin guard for other bookers**: `owner_may_record` and the policy's backfill / `pair_transfer` / `create_system_entry` paths already refuse when a *stand-in* holds the line; an auto-pay leg claiming the line counts as held. The `BANK_ONLY` policy (`create_system_entry`) must not book a second entry for a bank debit line that an auto-pay leg can claim: the leg is in the candidate population (it is dated within the period), so exact matching claims it before the policy runs. Builder verifies with a test.

### 4.6 What the matcher does with the pair (no matcher change)

- **Card side**: the next card statement prints a `payment` line (+total, dated 0–5 days after the pay date). Gate `payment → {transfer_in}`, `_peer_ok` true because the pay account is non-credit (§4.8 guarantees it). Exact match when the dates are within 3 days, near match (same amount) within 5; beyond that, `_transfer_case` opens `amount_delta`/`transfer_delta` with the leg as the candidate and the owner (or policy `adjust_transfer`) re-dates it. The observed lag has been ≤ 5 days.
- **Bank side**: the bank statement prints the debit as `withdrawal`, `payment` or `transfer_out`; all three gates admit `transfer_out`. The leg is dated on the debit day, so exact.
- **Balance gaps**: the pair is dated after the card statement's `period_end`, so it does not touch that statement's gap; it lands in the next card period and in the bank period that covers the pay date, where it belongs.

### 4.7 Settings and audit

New top-level key `auto_pay` in the reconciliation settings `data` (validated by a `_check_auto_pay` next to `_check_policy`; unknown keys refused; PUT replaces wholesale as today):

```json
"auto_pay": {
  "enabled": false,
  "enabled_from": null,
  "max_amount": "200000",
  "monthly_total": "500000",
  "twin_window_days": 7,
  "extra_holidays": [],
  "extra_workdays": []
}
```

- `enabled_from` (ISO date) is required when `enabled` is true; the rollout sets it to the day the owner switches the feature on, so statements already due are never back-booked (§5.6). Changing `auto_pay` bumps `version` but not `policy_version` / `rules_version` (it does not change case outcomes).
- Audit kinds (new): `auto_pay_booked`, `auto_pay_skipped` (with `reason`), `auto_pay_retired`, `auto_pay_owner_deleted`, `auto_pay_owner_edited`, `auto_pay_header_changed`, `auto_pay_calendar_stale`. Each carries `statement_id`, `card_account_id`, `pay_account_id`, `amount`, `pay_date`, and the entry ids when there are any, in the existing `reconciliation_audit` shape (the builder checks whether that table is per-case; if `case_id` is required there, the audit goes to the service log plus a new nullable column — the builder decides and documents).

### 4.8 Link validation (settings page)

`settings_service` refuses an `auto_pay_account_id` whose target `is_credit` or whose currency differs from the card's (message in the existing style: "must be a non-credit account in the same currency"). The MOZE import keeps copying whatever MOZE holds (it is historical data); the job's `link_ineligible` gate covers links that pre-date the rule. Current data: all 22 linked cards point at bank accounts; the builder verifies currencies in the migration's dry-run.

### 4.9 Billing UI

No change. `paid` sums the master's `transfer_in` from the closing date to `min(today, day before next closing)`; the auto-pay leg is on the master and dated `pay_date ≤ today` on the day it is booked, so the pill flips that morning on the next change round. #188 (payments on a combined child) stays a separate issue.

## 5. Rollout

1. Merge with `auto_pay.enabled = false` (default). Migration creates the table.
2. On the host: `python -m app.services.auto_pay_job --dry-run --as-of 2026-10-27` prints every candidate with its decision; expect 匯豐旅人 155 and HSBC Live+ 156 as `would_book` (pay date 2026-10-27), everything earlier as `cutoff` once `enabled_from` is set, 星展 123 as `owner_booked`.
3. Owner sets `auto_pay.enabled = true, enabled_from = <today>` through `PUT /settings/reconciliation`.
4. First live booking: the 06:00 run on the first business day on or after a pending pay date. Verify: two legs, row `booked`, audit row, 已逾期 pill cleared on the accounts list, `gaps.py` unchanged for the card's current statement.
5. First claims: the bank statement covering the pay date (國泰薪轉戶 76 for 匯豐旅人, HSBC TW 80 for Live+) and the next card statements must claim the legs with `match_rule` exact/near and open no new case. Then the owner stops booking card payments in MOZE; while the habit persists, `replace_stand_in` retires the system pair.
6. Rollback: `enabled = false` stops bookings; existing pairs stay and are retired/claimed as above. A bad booking is removed with `DELETE /entries/{leg}` (both legs) → row `owner_deleted`.

## 6. Success criteria

1. For the two statements due 2026-10-24 the pair is booked on 2026-10-27 at 06:00 with no owner action; no duplicate when the job runs again, the API restarts, or the CLI is run.
2. When the following bank and card statements arrive, their debit / payment lines claim the legs and no `line_unmatched` / `needs_peer` / `entry_unmatched` case opens for them.
3. An owner-booked payment for an auto-paid statement ends with exactly one pair in the ledger (the owner's) and the row `retired`.
4. A card with `auto_pay_account_id` pointing at a card, or in another currency, is never booked and the settings page refuses the link.
5. `python -m app.services.auto_pay_job --dry-run` on the host before enabling lists every live card statement with the decision the job would take.

## 7. Tests (what the plan must cover)

- Unit: `next_business_day` over weekends, a holiday run (e.g. 2027 春節), a make-up workday, `extra_holidays` / `extra_workdays`; candidate selection and every gate in §4.4; the twin guard against a child-card `transfer_in` (the 星展 case) and against a `transfer_out` on the pay account.
- Integration (API + DB): booking creates two `statement` legs and the row; a second run books nothing; `stand_in_entry_ids` returns the card leg for the card family and the bank leg for the bank account; a card `payment` line claims the leg (exact and near windows); a bank `withdrawal` line claims the bank leg; `shadows` + `replace_stand_in` retire the pair when the owner books the same payment; `orphans` opens `orphaned_stand_in` only once the covering statement exists and has no line; owner delete of a leg → `owner_deleted`; a header change after booking → `needs_review` on both legs and the audit; `parse_review` open → `parse_review_open` skip; settings validation refuses a credit / other-currency link and unknown `auto_pay` keys.
- Dry-run gate: `r6_dryrun.py` on the live dump before and after deploy with the flag off shows no change.

## 8. Left to the builder

- Exact module layout (`auto_pay_job.py` vs a package), the advisory-lock key, APScheduler wiring in `app/main.py` next to the schedule job.
- Whether `reconciliation_audit` can carry case-less rows (§4.7) and the resulting audit storage.
- The migration's enum vs free-text handling for `status` / audit kinds.
- How `replace_stand_in` discovers that a stand-in is an auto-pay leg (lookup by entry id in `statement_auto_pay`) and shares the delete cascade with `delete_entry`.
- The TW holiday list contents for 2026–2027 (official calendar), with a unit test pinning a handful of dates.
- Dry-run output format.

## 9. Calls I made (for the owner to check while reading)

1. Ledger date = the **shifted** debit day, not the printed due date (§4.3).
2. Booking does **not** wait for the statement to be reconciled or gap-free; it waits only for the header to be settled (no open `parse_review`). The bank debits the printed total regardless of our matching state.
3. No booking from `due_rule`/`due_value` when the parser produced no `due_date`; the dry-run lists these.
4. Default caps: `max_amount` 200,000, `monthly_total` 500,000 (匯豐旅人's October bill is 66,227; the largest statement seen is that one).
5. Twin window ±7 days around the pay date for the owner-booked check.
6. Both legs are stand-ins (unlike `pair_transfer`'s option A); an owner twin retires the **pair**, never one leg.
7. The job lives in the API process (APScheduler, like schedules), not in the worker, because of token scopes.
8. The link rule (non-credit, same currency) is enforced on the settings page from now on; existing links are gated at booking time only.
