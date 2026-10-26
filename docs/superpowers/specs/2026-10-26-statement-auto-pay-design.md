# Card statement auto-pay — design

**Status:** rev 4.1 (plan errata in §8; rev 3 was builder check + Multica AGENT-303 P1/P2/minors folded), for re-check + owner review. **Owner decision:** 2026-10-26 「自動扣繳做選項 1」 — the fully automatic form, not the one-tap confirmation.
**Code paths** below are relative to `services/accounting-service/` unless they start with `frontend/`. Line numbers are from main `f2d8498` (#185 merged).

## 1. Intent

Every live card in HomeHub that has an `auto_pay_account_id` is paid by its bank automatically: on the statement's due date (or the next business day when that falls on a weekend or holiday) the bank debits the full 本期應繳總額 from the linked bank account and credits the card. Today HomeHub knows all of this — the card statement carries `due_date` and `statement_total`, the account carries the linked bank account — and does nothing with it. `auto_pay_account_id` is stored by the settings page and the MOZE import and read by no service (`app/services/settings_service.py:487-490` validates it; nothing else uses it).

The consequence, seen on 2026-10-26: the owner books every card payment by hand (through MOZE), the accounts list shows 已逾期 until that happens (and, per #188, even after it when the payment was booked on a combined child card), and the later bank and card statements carry debit / payment lines that only match because the owner typed the transfer.

This feature makes HomeHub book that transfer itself, as a system entry pair, on the day the bank debits it. The owner stops typing card payments. The bank statement's debit line and the next card statement's payment line claim the pair when they arrive; if the owner (or an old MOZE habit) books the same payment anyway, the owner's entry wins and the system pair is retired.

## 2. Goals, non-goals, anti-goals

**Goals**
1. A card statement with `due_date`, `statement_total > 0` and a linked, eligible bank account is paid in the ledger on its pay date without the owner doing anything.
2. The pair is recognised as a stand-in on **both** sides (the card family's statements and the bank account's statements): the matcher prefers owner entries over it, an owner twin retires it, and an unclaimed leg surfaces as an orphan once the covering statement exists.
3. One booking per bill, ever. Re-runs, restarts, re-parses, duplicate statements and double timers never book twice.
4. The owner can switch it off, cap it, and see every decision (booked / skipped / retired and why) in the audit trail.
5. Best effort: the 已逾期 pill clears on the pay date for a card whose statement was auto-paid (§4.9 explains the two cases where it does not).

**Non-goals**
- Minimum-payment or partial payments. The owner pays in full; the bank debits `statement_total`.
- Booking payments for statements already due before the feature is switched on (rollout cutoff, §5).
- A UI for the auto-pay rows. The legs are ordinary entries (source `statement`, name 自動扣繳) and appear wherever entries do; the audit rows appear in the existing audit list.
- Business days of any country but Taiwan; the holiday table is Taiwan's public holidays.
- Cards without statements in HomeHub (e.g. 元大黑啤, which also has no `auto_pay_account_id`). No statement → nothing to pay.
- Fixing #188 (billing counts payments on the master only) or moving the pill's due date onto `statement.due_date`. Frontend follow-ups; this feature books on the master, so #188 does not affect it.

**Anti-goals** (a technically working build that does these has failed)
- Booking a payment the bank will not make: a card whose bank link is wrong (`auto_pay_account_id` pointing at a credit card, an archived account or an account in another currency), a statement whose header is disputed (open `parse_review` or `statement_conflict`), a statement total ≤ 0, or a second statement of the same bill.
- A duplicate beside any other representation of the same payment, in either order and whoever wrote it: an owner entry (MOZE import or typed), a policy stand-in (`backfill_entry` on the bank line, `pair_transfer`), or another auto-pay row. Owner first → skip; system first, owner later → the pair is retired without the owner lifting a finger.
- Silently changing a payment the owner has already edited.

## 3. Current state (facts the design rests on)

- `AccountStatement` has nullable `closing_date` / `due_date`, `statement_total` (NOT NULL), `minimum_payment`, `mode` ∈ historical|live, `status` ∈ open|reconciled, `kind` ∈ card|bank (`app/models/statements.py:137-169`). `due_date` is parsed by the worker (`worker/parse_schema.py:41`) and copied onto the statement when a revision becomes current (`app/services/statement_revision_service.py:511-513`). No guardrail checks it (`app/services/statements/derive.py:160-168`). A re-parse that changes `due_date` or `statement_total` is a header change: the new revision does not become current (`:659`); a live `open` statement gets a `parse_review` case, a `reconciled` one a `statement_conflict` case (`:642, 680-689`). `parse_review` can only be dismissed (`reconciliation_actions/__init__.py:35`); after a dismiss the old header stays current.
- A statement can be retired (`mode='historical'`) by `statement_retire_service` (`:3-19`); one bill has in the past produced two live statements when a PDF was re-parsed with another period.
- `Account.auto_pay_account_id` (`app/models/ledger.py:130`) must be another existing, non-archived account at the time it is set; there is no `is_credit` or currency check (`settings_service.py:487-490`). Deleting the target clears the link (`:602-605`); **archiving does not** (`update_account`, `:564-580`), so links to archived accounts can exist.
- Transfers are created by `transfer_service.create_transfer(db, payload: TransferIn, *, source="manual", remember=True) -> UUID` (`app/services/transfer_service.py:139`): two legs `transfer_out` (−amount) / `transfer_in` (+amount) sharing `transfer_group_id`, `source`, `entry_date` and one `posted_date` (`:103-115`); cross-currency needs `in_amount` (`:91-92`). The schedule job books transfers through it with `source="schedule"` (`app/services/schedule_posting.py:166`). `DELETE /entries/{leg}` deletes both legs (`entry_write_service.delete_entry`, `locked_with_legs`). `PUT /transfers/{group}` flips `source` to `manual`; `adjust_transfer` rewrites both legs and keeps `source='statement'` (`reconciliation_actions/transfer_actions.py:191-203`); `move_account` changes an entry's account (`reconciliation_actions/__init__.py:34`).
- System entries from reconciliation carry `source='statement'` and are anchored to a `ReconciliationAction` row with `case_id` NOT NULL and nullable `event_id` (`app/models/statements.py:349-370`; `reconciliation_actions/engine.py:277-279`). A stand-in is recognised by `stand_ins.stand_in_entry_ids` as an entry created by an applied `BACKFILL_ACTIONS` action whose case statement is in the account family **and** whose own account is in that family (`app/services/stand_ins.py:21, 27-39`); `held_by_stand_ins` and `orphans` drop any stand-in without a creating action (`:140-142, 332-335`). `pair_transfer` treats the peer leg as an owner entry (`transfer_actions.py:316-318`). Owner-wins today: `shadows()` (`stand_ins.py:296-313`) fires only when the stand-in **already holds the line** and an owner twin appears later; it opens `entry_unmatched` with hint `shadowed_by_stand_in`, resolved by `replace_stand_in`, which deletes that one entry and refuses any stand-in that is a transfer leg (`reconciliation_actions/ledger_actions.py:648-656, 690, 700-701`). When both entries exist at first match, "an owner representation drops every stand-in one" (`statements/matching.py:682-683`): the owner's entry takes the line and the stand-in is simply unmatched — no shadow, no retirement. `coverage_service._retire_creations` / `_retire_stranded_creations` (`coverage_service.py:556-610`) mark an action reverted when the owner deletes its entries, scoped to the swept statement's cases.
- Matcher (`app/services/statements/matching.py`): a line is **claimed** only by a representation scoring at or above `accept`, and the score is zero beyond `exact_window_days` = 3 (`:268-277, 634, 1080`); `_date_distance` takes the smaller of the `entry_date` and `posted_date` distances (`:245-249`). Near (`_near_case`), drift (`_drift_case`) and transfer (`_transfer_case`) matches **never claim**: they open `amount_delta` cases (`:885-887, 913-917, 942-945`), and for a transfer-kind line `_transfer_case` runs first (`:1097`). Gates: card `payment` → {transfer_in} with `_peer_ok` requiring the peer leg's account to be non-credit (`:190-198, 325-327`); bank `withdrawal` → {expense, transfer_out, fee, settlement_out, receivable}, bank `payment` → {settlement_out, transfer_out}, bank `transfer_out` → {transfer_out} (`:199-211`). Windows: `exact_window_days` 3, `near_window_days` 5, `drift_window_days` 15, candidate population ±`window_days` of the period (`:125-140`; `reconciliation_service.py:254-255`). Participating accounts of a statement = the account plus its combined children (`coverage_service.participating_accounts`, `:188-191`). `adjust_transfer` refuses `peer_leg_claimed` when the other leg already holds a line (`transfer_actions.py:180-181`); there is no policy `adjust_transfer`.
- The policy can book the bank debit line itself: `backfill_entry` writes an `expense` stand-in for a `withdrawal` (`ledger_actions.py:510-512`), `pair_transfer` a transfer pair from a bank line plus a card `payment` line (`transfer_actions.py:315-320`); both `source='statement'`.
- No business-day or holiday logic exists anywhere (`derive.py:17` is the only mention). Schedules post on the nominal date.
- Settings: one JSONB row with `version`; `_check_policy` refuses unknown keys (`settings_service.py:266-312`); `PUT /settings/reconciliation` replaces `data` wholesale (`:394-418`) and needs `admin`.
- `reconciliation_audit` has a free-text `event` (`String(48)`) and nullable `case_id`, `statement_id`, `action_id` (`app/models/statements.py:376-388`); case-less rows already exist (`statement_retired`, `statements/retired.py:9-14`).
- The worker token is `ingest`-only and lease-bound (`README.md:162-165`); `POST /transfers` needs `write` (`app/routers/transfers.py:12`). The API already runs an in-process APScheduler cron job for schedules with an advisory lock and a `--dry-run` CLI (`app/services/schedule_job.py:1-9, 326`; started from `app/main.py:38-39`).
- Billing pill (`frontend/src/app/components/accounting/billing/billing-math.ts:120-128, 169-185`; `accounts/accounts.ts:191-197`): due date from the card's `due_rule`/`due_value` (not `statement.due_date`), `remaining` = ledger cycle spend of the master + combined cards − the master's `transfer_in` in the payment window.
- Observed data (live card statements with auto-pay, 2026-07 → 2026-10): the owner booked every payment on the due date itself; the card statement's `payment` line is dated (`txn_date`) 0–5 days after the due date, on the bank's posting day (HSBC: due Fri 07-24 → line Mon 07-27; due Thu 09-24 → line Tue 09-29 across 中秋/教師節); the bank side posts on the debit day. Pending at the time of writing: 匯豐旅人 155 (66,227) and HSBC Live+ 156 (1,148), due Sat 2026-10-24; Mon 10-26 is the 光復節 make-up holiday, so the bank debits Tue 2026-10-27.
- Alembic head: `b8d4f2a1c6e3` (`alembic/versions/b8d4f2a1c6e3_account_logo.py`, revises `a7c3e1d9b2f4`).

## 4. Design

### 4.1 Vocabulary

- **Auto-pay candidate**: a live card statement (`kind='card'`, `mode='live'`) whose account (the statement account, i.e. the family master) has `auto_pay_account_id`, with `due_date IS NOT NULL` and `statement_total > 0`.
- **Pay date**: `due_date` moved forward to the first business day on or after it (§4.3).
- **Auto-pay pair**: the `transfer_out` leg on the bank account and the `transfer_in` leg on the card master, both `source='statement'`, `name='自動扣繳'`, `entry_date = pay date`, amount `statement_total`, `needs_review = False`. `posted_date` of the bank leg = pay date; `posted_date` of the card leg = the first business day **after** the pay date (the card issuer's posting day; `_date_distance` uses whichever of the two dates is closer).
- **Auto-pay row**: the provenance row in the new table `statement_auto_pay` (§4.2), one per statement.
- **Twin**: any other ledger representation of the same payment (§4.4 gate `twin`): a `transfer_in` on any account of the card family, or a `transfer_out` on the pay account, with `amount == statement_total`, `entry_date` within `[pay_date − twin_window_days, pay_date + twin_window_days]`, **whatever its `source`** (owner entries, MOZE imports, policy stand-ins from `backfill_entry` / `pair_transfer`, other auto-pay legs), excluding the row's own legs. An `expense` stand-in from `backfill_entry` on the pay account with the same amount in the window counts too.

### 4.2 Provenance: table `statement_auto_pay`

A new table instead of a `ReconciliationAction`: an action needs a case (`case_id` NOT NULL) and a case needs a statement line, and at booking time no line exists yet on either side. The row is the anchor that `stand_ins`, retirement and orphan detection use for this kind of stand-in.

| column | type | notes |
|---|---|---|
| `id` | serial PK | |
| `statement_id` | FK `account_statement` RESTRICT, **UNIQUE** | one row per statement |
| `card_account_id` | FK `account` | the statement account (family master) |
| `pay_account_id` | FK `account` | `auto_pay_account_id` at booking time |
| `amount` | MONEY | `statement_total` at booking time |
| `due_date`, `pay_date` | DATE | |
| `transfer_group_id` | UUID, nullable | NULL unless booked |
| `out_entry_id`, `in_entry_id` | FK `ledger_entry` ON DELETE SET NULL, nullable | |
| `status` | varchar(16): `booked` / `retired` / `owner_deleted` / `owner_edited` / `skipped` | §4.5 |
| `reason` | varchar(64), nullable | skip / retire reason code |
| `settings_version` | int | the `reconciliation_settings.version` the decision read |
| `created_at`, `updated_at` | timestamptz | |

Plus an **exclusion constraint**: no two `booked` rows for one card whose pay dates lie fewer than 20 days apart — `EXCLUDE USING gist (card_account_id WITH =, daterange(pay_date, pay_date + 20) WITH &&) WHERE (status = 'booked')` (extension `btree_gist`; `daterange` is half-open, so `[D, D+20)` overlaps a second copy exactly when the two pay dates differ by fewer than 20 days, and a normal ~30-day cycle never collides). Card bills are monthly; two live statements of the same card due within 20 days of each other describe one bill, however their `period_end`, `due_date` or `statement_total` were read (statement identity is `(account_id, currency, period_end)`, `app/models/statements.py:140`, and the known incident was a re-parse with another period). The job's `duplicate_statement` gate (§4.4) applies the same rule before writing; the constraint is the backstop.

Row states: `booked`, `retired`, `owner_deleted`, `owner_edited` are final for the job (a statement with such a row is never a candidate again). `skipped` rows with a *terminal* reason (`twin`, `cutoff`, `duplicate_statement`, `expired`) are final too; a `skipped` row with a *transient* reason (`header_disputed`, `no_link`, `link_ineligible`, `over_cap`, `monthly_budget`) is re-evaluated on every run and rewritten when the condition clears — until `today > pay_date + retry_days` (default 30), when it becomes terminal `expired` (by then the covering statements have arrived and the policy or the owner has dealt with the line; booking a month-old payment helps nobody). A second run over a `booked` statement is a clean no-op, not a unique-violation. Migration: new revision after `b8d4f2a1c6e3`.

### 4.3 Pay date and the business-day calendar

`pay_date = next_business_day(due_date)`, where a business day is Monday–Friday and not in the holiday set. The holiday set is `TW_HOLIDAYS` (a static frozenset of ISO dates in a new module `app/services/business_days.py`, seeded with the Taiwan public holidays for 2026 and 2027 as published by the 行政院人事行政總處 calendar, make-up holidays included, make-up workdays excluded) **union** `settings.auto_pay.extra_holidays`, **minus** `settings.auto_pay.extra_workdays`. The two settings lists let the owner correct a year the module does not cover without a deploy. A statement whose `due_date` lies beyond the last date the module knows is still shifted for weekends only; the job audits `auto_pay_calendar_stale` once per day so the owner knows to add the year.

**My call:** the ledger date is the debit day (the shifted date), not the printed due date, because that is what the bank statement prints. The card leg's `posted_date` on the next business day covers the issuer's posting lag (§4.6).

### 4.4 The job

`app/services/auto_pay_job.py`, modelled on `schedule_job.py`: an APScheduler `CronTrigger` inside the API process, daily at **06:00 Asia/Taipei**, plus one run at API start-up (so a restart or a missed day catches up), guarded by a Postgres advisory lock so two API processes never both book; CLI `python -m app.services.auto_pay_job [--dry-run] [--as-of YYYY-MM-DD]` that prints every candidate with the decision the job would take and writes nothing when `--dry-run`. It runs inside the API because booking needs `write` and the worker token is `ingest`-only; no new token, scope or endpoint.

One run = housekeeping (step 0) then candidates, each statement in its own transaction (a failure on one never blocks the others):

0. **Housekeeping**, for every `booked` row (the whole pass holds the same shared ledger-import advisory key as step 4, so a retire never interleaves with a MOZE backup import):
   - both legs gone → `owner_deleted` (the owner removed the payment; never re-book), audit `auto_pay_owner_deleted`;
   - a leg's `source` is no longer `statement` (a `PUT /transfers` or entry edit) → `owner_edited`, audit once; the legs are the owner's now, the row keeps its original figures;
   - a leg's amount, dates or accounts differ from the row while `source` is still `statement` (an `adjust_transfer` / `move_account` action did it) → the row stays `booked` and its figures are updated to the legs; no audit;
   - **twin appeared** (a twin per §4.1 exists, excluding the row's legs) → retire the pair **whether or not its legs hold coverage**: release every active coverage row of the two legs (the same release the sweep performs when a claimed entry disappears, `coverage_service.py:401-410`), delete both legs with the transfer-delete cascade, `status='retired'`, `reason='twin'`, audit `auto_pay_retired`; the delete trigger marks both statements dirty and their next sweep re-matches the lines with the twin. This is the normal owner-wins path (MOZE import after the booking); the twin test runs on every pass while `today ≤ pay_date + twin_window_days + 7`, and again at every sweep that has the leg in its population (§4.5);
   - the statement's current `statement_total` or `due_date` differs from the row (a disputed header resolved to new figures after booking) → `needs_review = True` on both legs and audit `auto_pay_header_changed` once; the owner edits or deletes the transfer. No automatic re-booking or amount change (anti-goal 3).
1. **Enabled?** `settings.auto_pay.enabled` false → stop after housekeeping (the CLI prints `disabled`).
2. **Candidates**: auto-pay candidates (§4.1) with `pay_date ≤ today`, ordered by `pay_date, statement_id`, that have **no** `statement_auto_pay` row or only a `skipped` row with a transient reason (§4.2). A `booked` statement is never re-evaluated here (clean no-op, no unique violation). Statements without `due_date` are not candidates; the dry-run lists them as `no_due_date` so the owner sees a parser gap; nothing is booked from `due_rule`/`due_value` (**my call**: a guessed date is how you book a payment the bank did not make).
3. **Gates**, in order, each ending in a `skipped` row with the reason named:
   - `cutoff` (terminal): `pay_date < settings.auto_pay.enabled_from`.
   - `duplicate_statement` (terminal): the same card account already has a `booked` row whose `pay_date` lies fewer than 20 days from this statement's pay date (one bill per card per month, whatever the two headers say; §4.2's exclusion constraint backs this).
   - `header_disputed` (transient): an open `parse_review` or `statement_conflict` case on the statement → wait. A dismissed `parse_review` means the owner kept the current header → book it.
   - `no_link` (transient): `auto_pay_account_id` is NULL on the master (a child card's link does not count; the statement is the master's).
   - `link_ineligible` (transient): the linked account `is_credit`, `is_archived`, or has another currency than the card.
   - `twin` (terminal): a twin per §4.1 exists → the payment is already in the ledger.
   - `over_cap` (transient): `statement_total > settings.auto_pay.max_amount`.
   - `monthly_budget` (transient): Σ `amount` of `booked` rows with `pay_date` in this pay date's calendar month + `statement_total` > `settings.auto_pay.monthly_total`.
   - transient reasons expire to `expired` once `today > pay_date + retry_days`.
4. **Book**: take the shared ledger-import advisory key that `schedule_posting` takes before its writes (`schedule_posting.py:94`) so a 06:00 run never interleaves with a MOZE backup import; then `transfer_service.create_transfer(db, TransferIn(from_account_id=pay, to_account_id=card_master, out_amount=statement_total, entry_date=pay_date, posted_date=pay_date, name="自動扣繳", description=f"{card.name} {period_end:%Y-%m-%d} 帳單 自動扣繳"), source=STATEMENT_SOURCE, remember=False)`, set the card leg's `posted_date` to `next_business_day(pay_date + 1 day)`, insert the `statement_auto_pay` row (`booked`), audit `auto_pay_booked`. Row and legs commit together; the UNIQUE on `statement_id` and the exclusion constraint make a concurrent duplicate fail the second transaction.

### 4.5 Stand-in semantics on both sides

`stand_ins.stand_in_entry_ids(db, account_ids)` returns, in addition to today's set, every `out_entry_id` / `in_entry_id` of a `statement_auto_pay` row with `status='booked'` whose entry is on an account in `account_ids`. Both legs are therefore stand-ins — the `transfer_in` leg on the card family's statements, the `transfer_out` leg on the bank account's statements. Everything that keys off `stand_in` then applies: the matcher drops the stand-in when an owner representation exists (`matching.py:682`), `_exact_pair` prefers owner rows, `owner_population` excludes them. The places that today drop a stand-in **without a creating action** (`held_by_stand_ins`, `orphans`, `stand_ins.py:140-142, 332-335`) learn the auto-pay row as a second kind of provenance (`statement_auto_pay.id`), so an auto-pay leg holding a line counts as "held by a stand-in" for `owner_may_record` and the policy twin guards.

Owner-wins for auto-pay legs is **not** `shadows` → `replace_stand_in` (that path requires the stand-in to hold the line first, auto-applies only inside the exact window, and refuses transfer legs; `ledger_actions.py:648-657`, `policy_service.py:526-543`). It is one rule, applied in two places: **a twin (§4.1) anywhere retires the pair; the auto-pay leg never competes with a twin for a line.**

- **In the job** (§4.4 step 0), on every pass: twin → release the legs' coverage, delete both legs, row `retired`. Covers the common order (booking first, MOZE import days later) and the order where a leg already holds a line (the bank statement swept before the import): the release marks both statements dirty and their next sweep re-matches with the twin. No case, no owner action.
- **In the sweep** (`reconciliation_service`, before matching): for every auto-pay leg in the population, run the same twin test over the population's entries (the population already spans the period ± `window_days`, which covers the ±7-day twin window); a twin → retire the pair inside the sweep's transaction, before any representation is built, so the line meets only the twin. This closes the gap dev-grok found: with the leg still present, an owner entry 4–15 days from the line never scores (`matching.py:268-277, 633-636`), while the leg's `posted_date` is close enough for the exact rule to claim at `:1080`; `matching.py:682` is an exact-window filter and would not save the owner. With the leg retired first, the owner entry gets exactly today's treatment: an exact claim within 3 days, otherwise `amount_delta` / `transfer_delta` with the owner entry as candidate.
- `shadows()` therefore never sees an auto-pay leg with a twin; `replace_stand_in` is unchanged and keeps refusing transfer legs.
- Other-statement effects: when a retired leg held a line on a statement that is already `reconciled`, that statement's sweep releases the claim and re-matches the line with the twin's leg; a case reopens only if the twin does not fit, which is correct (Q7 of the builder check: acceptable).
- **Orphan**: `orphans()` additionally reports an auto-pay leg on this statement's account family with **no active coverage anywhere** when the swept statement's period contains the leg's `entry_date` and no twin exists. Same hint `orphaned_stand_in`, same `entry_unmatched` case; the owner resolves it by deleting the pair (→ `owner_deleted`) or by `match`. A leg waiting for next month's statement is not an orphan.
- **Retirement on delete**: `_retire_creations` / `_retire_stranded_creations` stay as they are (they key off actions). The auto-pay equivalent is housekeeping (§4.4 step 0).
- **Statement retired** (`statement_retire_service`): a `booked` row on a retired statement stays as it is (the money moved regardless); the retire audits `auto_pay_statement_retired` with the row id so the owner can see it. The `duplicate_statement` gate and the 20-day exclusion constraint (§4.2) keep the re-parsed twin statement from booking again.

### 4.6 Matching the pair (one matcher addition)

The observed posting lag of card `payment` lines (0–5 days, longer over 春節-class holidays) exceeds the 3-day exact window, and near / drift / transfer matches never claim (§3). So, one new rule in `matching.py`:

- **`auto_pay` rule**: for a card `payment` line or a bank `withdrawal` / `payment` / `transfer_out` line, when the only admissible stand-in auto-pay leg (per §4.5 provenance) has **exactly** the line's flow and `_date_distance ≤ drift_window_days` (15), it **claims** with `match_rule='auto_pay'`, `match_kind` as the exact rule uses, before `_transfer_case` runs. Owner entries win by construction, not by `matching.py:682`: the sweep-time twin retire (§4.5) has already removed any auto-pay leg that has a twin in the population, and the rule additionally refuses to claim when any **non-stand-in** entry conserves the line (`_conserves`, `stand_ins.py:169-175`) within `drift_window_days` — the same test `owner_twins` uses — leaving the line to today's owner paths. Two auto-pay legs within the window for one line → no claim, `ambiguous` as today. The `r6_dryrun.py` gate attributes the new rule by `match_rule`.
- Card side gate `payment → {transfer_in}` and `_peer_ok` (non-credit peer) hold because §4.4 refuses credit-card links. Bank side: all three bank gates admit `transfer_out`; the bank leg is dated on the debit day, so it is exact anyway.
- **Balance gaps**: the pair is dated after the card statement's `period_end`, so it does not touch that statement's gap; it lands in the next card period and the bank period that covers the pay date, where it belongs.

### 4.7 Settings and audit

New top-level key `auto_pay` in the reconciliation settings `data` (validated by a `_check_auto_pay` next to `_check_policy`; unknown keys refused; PUT replaces wholesale as today):

```json
"auto_pay": {
  "enabled": false,
  "enabled_from": null,
  "max_amount": "200000",
  "monthly_total": "500000",
  "twin_window_days": 7,
  "retry_days": 30,
  "extra_holidays": [],
  "extra_workdays": []
}
```

- `enabled_from` (ISO date) is required when `enabled` is true; the rollout sets it to the day the owner switches the feature on, so statements already due are never back-booked. Changing `auto_pay` bumps `version` but not `policy_version` / `rules_version` (case outcomes are unchanged except through the `auto_pay` match rule, which is attributed in the dry-run gate).
- Audit events (free-text `reconciliation_audit.event`, case-less rows, `statement_id` set): `auto_pay_booked`, `auto_pay_skipped` (`reason` in the payload), `auto_pay_retired`, `auto_pay_owner_deleted`, `auto_pay_owner_edited`, `auto_pay_header_changed`, `auto_pay_statement_retired`, `auto_pay_calendar_stale`. Payload: `card_account_id`, `pay_account_id`, `amount`, `pay_date`, entry ids when there are any.

### 4.8 Link validation (settings page)

`settings_service` refuses an `auto_pay_account_id` whose target `is_credit`, `is_archived`, or whose currency differs from the card's (message in the existing style: "must be a non-credit, non-archived account in the same currency"), and archiving an account clears the links that point at it (as deleting already does). The MOZE import keeps copying whatever MOZE holds (historical data); the job's `link_ineligible` gate covers links that pre-date the rule. Current data: all 22 linked cards point at bank accounts; the plan includes a one-off host query of their currencies.

### 4.9 Billing UI

No change in this feature. The pill's `paid` sums the master's `transfer_in` from the closing date to `min(today, day before next closing)`; the auto-pay leg is on the master and dated `pay_date ≤ today` on the day it is booked, so the pill clears that morning on the next change round **when** ledger cycle spend ≤ `statement_total`. Two known gaps, both frontend follow-ups: the pill's due date comes from `due_rule`, so on a weekend/holiday shift it reads 已逾期 for the shift days; and #188 (payments on a combined child card).

## 5. Rollout

1. Merge with `auto_pay.enabled = false` (default). Migration creates the table and index.
2. On the host: `python -m app.services.auto_pay_job --dry-run --as-of 2026-10-27` prints every candidate with its decision; expect 匯豐旅人 155 and HSBC Live+ 156 as `would_book` (pay date Tue 2026-10-27), everything earlier as `cutoff` once `enabled_from` is set, and 星展 123 as `twin` (owner booked on the child card).
3. Owner sets `auto_pay.enabled = true, enabled_from = <today>` through `PUT /settings/reconciliation`.
4. First live booking: the 06:00 run on the first business day on or after a pending pay date (if the feature ships after 10-27, the two October bills are `cutoff` and the owner books them by hand as before). Verify: two legs, row `booked`, audit row, 已逾期 pill cleared, `gaps.py` unchanged for the card's current statement.
5. First claims: the bank statement covering the pay date (國泰薪轉戶 76 for 匯豐旅人, HSBC TW 80 for Live+) and the next card statements claim the legs (`match_rule` exact or `auto_pay`) and open no new case for them. The owner stops booking card payments in MOZE; while the habit persists, housekeeping retires the system pair within a day of the import.
6. Rollback: `enabled = false` stops bookings; existing pairs stay and are retired/claimed as above. A bad booking is removed with `DELETE /entries/{leg}` (both legs) → row `owner_deleted`.

## 6. Success criteria

1. For a statement whose pay date falls after `enabled_from`, the pair is booked on the pay date at 06:00 with no owner action; no duplicate when the job runs again, the API restarts, the CLI is run, or a second live statement of the same bill appears.
2. When the following bank and card statements arrive, their debit / payment lines claim the legs (`exact` or `auto_pay` rule) and no `line_unmatched` / `needs_peer` / `entry_unmatched` case opens for them, including a payment line posted up to 15 days after the pay date.
3. An owner-booked payment for an auto-paid statement ends with exactly one pair in the ledger (the owner's) and the row `retired` / `reason='twin'`, in all three orders: owner first (`skipped`/`twin`), owner within days after (job housekeeping), owner after a leg already holds a line (housekeeping or sweep-time retire, claim released and re-matched). This holds for an owner entry dated anywhere within the twin window, including 4–15 days from the statement line.
4. A card linked to a card, an archived account or another currency is never booked, and the settings page refuses the link.
5. `python -m app.services.auto_pay_job --dry-run` on the host before enabling lists every live card statement with the decision the job would take.

## 7. Tests (what the plan must cover)

- Unit: `next_business_day` over weekends, a holiday run (2026-10-24 → 10-27 across the 光復節 make-up day; 2027 春節), a make-up workday, `extra_holidays` / `extra_workdays`; candidate selection and every gate in §4.4 including `duplicate_statement`, `header_disputed` for both case kinds, expiry; the twin test against a child-card `transfer_in` (the 星展 case), a `transfer_out` on the pay account, a policy `backfill_entry` expense, and a `pair_transfer` pair; housekeeping transitions (`owner_deleted`, `owner_edited`, action-edited stays `booked`, header changed → `needs_review`).
- Integration (API + DB): booking creates two `statement` legs with the two `posted_date`s and the row; a second run is a clean no-op (no insert attempted); two concurrent runs book once (unique + exclusion); a second live statement of the same card with another `period_end` and a misread `due_date` 3 days off → `duplicate_statement`; the same card's next monthly statement (pay date ~30 days later) **books** while the previous row is still `booked`; `stand_in_entry_ids` returns the card leg for the card family and the bank leg for the bank account; a card `payment` line 1, 4 and 12 days after the pay date claims the leg (`exact`, `auto_pay`, `auto_pay`); bank `withdrawal`, `payment` and `transfer_out` lines each claim the bank leg; **owner entry and auto-pay leg on one line at distance 4, 5 and 12 days** → the pair is retired in the sweep and the line goes to the owner's path (exact or `amount_delta`), never to the leg; housekeeping retires a pair whose bank leg already holds a claim (claim released, bank statement dirty, re-matched with the twin's leg); the three owner-wins orders of success criterion 3; `orphans` opens `orphaned_stand_in` only once the covering statement exists with no line and no twin; owner delete of a leg → `owner_deleted`; a header change after booking → `needs_review` on both legs and the audit; settings validation refuses a credit / archived / other-currency link and unknown `auto_pay` keys; `owner_may_record` refuses a policy backfill on a line an auto-pay leg holds; the job holds the shared import advisory key while booking.
- Dry-run gate: `r6_dryrun.py` on the live dump before and after deploy with the flag off shows no change.

## 8. Left to the builder

**rev 4.1 errata (plan).**

- Task 1's Alembic revision id is `c4a7e2d9f1b3`.
- A `no_link` skip row has no linked bank account; it stores the card's own `card_account_id` in the NOT NULL `pay_account_id`.
- The job shares the `ACCOUNTING_SCHEDULER_ENABLED` switch with the schedule job (`false` turns both off).
- The `header_changed` marker lives in `reason` of the booked row.
- The job lock key is `0x41505041`.
- A transient skip past `retry_days` becomes `expired` even when the gate now passes; a new candidate with no row still books.
- The sweep retires a twinned pair only while both legs keep `source='statement'`.
- An auto-pay orphan is reported only when the matcher leaves the leg unmatched and not deferred.
- `replace_stand_in` is never offered or attempted for an auto-pay leg; the owner deletes the pair.
- The matcher's `auto_pay` rule leaves the leg to a later line of the statement that fits within the exact window.
- The job retries nothing after `busy` / `import_running` (next day or next restart).
- A dry run performs no housekeeping.


- Module layout (`auto_pay_job.py` vs a package), the advisory-lock key, APScheduler wiring in `app/main.py` next to the schedule job.
- How the matcher learns auto-pay provenance (a `stand_in_kind` on the entry rows it loads, or a second id set), and the exact insertion point of the `auto_pay` rule relative to `_transfer_case`.
- How the sweep-time twin retire and the `replace_stand_in` branch share the delete cascade with `delete_entry`.
- The TW holiday list contents for 2026–2027 (official calendar), with a unit test pinning a handful of dates.
- Dry-run output format.

## 9. Calls I made (for the owner to check while reading)

1. Ledger date = the **shifted** debit day, not the printed due date; the card leg's `posted_date` = the next business day after it (§4.3, §4.1).
2. Booking does **not** wait for the statement to be reconciled or gap-free; it waits only for an undisputed header (no open `parse_review` / `statement_conflict`). The bank debits the printed total regardless of our matching state.
3. No booking from `due_rule`/`due_value` when the parser produced no `due_date`; the dry-run lists these.
4. Default caps: `max_amount` 200,000, `monthly_total` 500,000 (the largest statement seen is 匯豐旅人 October, 66,227).
5. Twin window ±7 days around the pay date; twins of **any** source count (owner, MOZE, policy stand-ins); transient skips give up 30 days after the pay date.
6. Both legs are stand-ins (unlike `pair_transfer`'s option A); a twin retires the **pair**, never one leg, without a case, whether or not a leg already holds a line (claims are released and re-matched). `replace_stand_in` is not extended.
7. One matcher addition (`auto_pay` rule, exact amount within 15 days claims, never beside a non-stand-in entry that conserves the line within 15 days) so holiday posting lag opens no case; without it every long holiday would hand the owner a `transfer_delta` case.
11. One booked pair per card per 20 days (exclusion constraint), which is how a re-parsed twin statement with a misread due date is kept from paying twice.
8. The job lives in the API process (APScheduler, like schedules), not in the worker, because of token scopes.
9. The link rule (non-credit, non-archived, same currency) is enforced on the settings page from now on; existing links are gated at booking time only.
10. Retiring a statement leaves its booked pair alone (the money moved regardless).
