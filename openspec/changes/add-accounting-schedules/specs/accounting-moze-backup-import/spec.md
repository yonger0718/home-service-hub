## MODIFIED Requirements

### Requirement: Record mapping

Each `AHRecord` SHALL map to ledger entries by its `type`:

| `type` | kind | `amount` |
|---|---|---|
| 0 | `expense` | `price` |
| 1 | `income` | `price` |
| 2 | `transfer_out` when `isTransferIn` is false, else `transfer_in` | `price` |
| 3 | `receivable` | `price` |
| 5 | `receivable` (a collection; `is_settlement = true`; `settles_entry_id` per "Links") | `price` |
| 4 | `payable` | `price` |
| 6 | `payable` (a repayment; `is_settlement = true`) | `price` |
| 7 | `balance_adjustment` | `price` |
| 12, 16 | `fee` | `price` |
| 13 | `discount` | `price` |
| 14 | `reward` | `price` |
| 15 | `interest` | `price` |

Any other `type` SHALL fail the import naming the type and the row's identifier.

Fields: `name` → `name` (NULL when empty); `store` → `merchant`; `desc` → `description`; `tags` → `tags` (split on the delimiter determined in implementation, trimmed, empty removed); `date` → `entry_date` and `entry_time`; `chargeDate` → `posted_date`; `invoiceNumber` → `invoice_number`; `classification` → `category_id`; `project` → `project_id`; `target` → `counterparty_id`; `account` → `account_id`; `identifier` → `moze_id`; `source = 'moze_backup'`.

`fee` ≠ 0 SHALL become a `fee` child (`amount = fee`, `name = feeName` or `手續費`) and `bonus` ≠ 0 a `discount` child (`amount = bonus`, `name = bonusName` or `折扣`), with `parent_entry_id` set and the parent's date, time and posting date. The parent's `amount` is `price` alone, so the row's balance effect equals `total`.

Rows whose `entry_date` is after the archive's export date SHALL NOT become entries; they SHALL be counted per type in the report under `skipped_future` and mapped to schedule instances (see "Schedule definitions and instances from the backup"). A row dated up to the export date whose identifier is in the `moze_record_ids` of an instance that HomeHub posted (`acted_by` `auto` or `owner`) or skipped SHALL NOT be imported either, since HomeHub already booked or deliberately dropped that period; it SHALL be counted under `schedules.past_records_already_posted` or `schedules.past_records_already_skipped`. A row dated up to the export date whose identifier is in the `moze_record_ids` of (or which adopts) a `pending` instance with `edited_by_owner = true` SHALL NOT be imported either: the owner's pending choice wins, the instance SHALL stay `pending` with its override, and the row SHALL be listed under `schedules.past_records_owner_pending`; a later post of that instance writes the period once. Rows with `isEnabled == false` SHALL be skipped and counted per type in the report under `disabled_skipped`, since MOZE excludes them from balances.

#### Scenario: Expense with fee
- **GIVEN** an `AHRecord` with `type = 0`, `price = -266`, `fee = -3`, `total = -269`
- **WHEN** imported
- **THEN** an `expense` of `-266` and a `fee` child of `-3` SHALL be created and the account balance SHALL change by `-269`

#### Scenario: Future installment skipped
- **GIVEN** the archive exported on 2026-10-01 and a `type = 6` row dated 2026-11-09
- **WHEN** imported
- **THEN** no entry SHALL be created for it, the report's `skipped_future` SHALL count it under type 6, and it SHALL be a `pending` instance of its installment definition

#### Scenario: Period already posted by HomeHub
- **GIVEN** HomeHub's job posted the instance whose `moze_id` is record R (due 2026-10-09), and a later backup exported on 2026-10-12 holds R as a past record
- **WHEN** that backup is imported
- **THEN** no `moze_backup` entry SHALL be created for R, the `schedule` entries SHALL remain, and `schedules.past_records_already_posted` SHALL be 1

### Requirement: Transactional full replace and report

The backup importer SHALL share the CSV importer's advisory lock (retrying `pg_try_advisory_lock` for up to 30 seconds while schedule writers hold the key shared), `import_run` lifecycle, dry-run, `--rename OLD=NEW` and `ACCOUNTING_IMPORT_LOCKED` behaviour. Its ledger transaction SHALL: lock, before deleting anything, every schedule definition it will touch (imported ones, and local ones whose templates or posted entries point at MOZE entries) by ascending id and then their instances by ascending id (`SELECT … FOR UPDATE`); delete every `ledger_entry` and `entry_group` whose `source` is a MOZE source or whose `moze_id` is set, together with their `entry_reward_rule` rows; upsert groups, accounts, categories, projects, counterparties and rules (rules by `moze_id`, never recreated); insert entries and groups; archive accounts not in the backup that have no remaining entries; delete unused categories, projects and counterparties **except** those whose `moze_id` is present in the backup (seeded settings stay, so the pickers match MOZE) — a reference from a schedule template counts as usage, for accounts too (such an account is never archived as disappeared); upsert schedule definitions and instances and re-point schedule links (see "Schedule definitions and instances from the backup"); and record success; after the commit, once the advisory lock is released, a real (non-dry-run) import SHALL trigger a schedule job run (through the in-process scheduler for an import made through the API; the standalone CLI SHALL run the job inline in its own process — generation always, posting only when `ACCOUNTING_SCHEDULER_ENABLED` is on, otherwise logging the count of due but unposted instances — and print a counts-only `schedule_job:` line); a dry run SHALL NOT. Manual rows, `schedule` rows, locally created schedule definitions and their instances, their attachments, and their categories, projects and counterparties SHALL be untouched. Before the delete step the importer SHALL record, for every template `loan_entry_id` and every `settles_entry_id` of a `schedule` entry that points at a MOZE-sourced entry, that entry's `moze_id`, and after inserting SHALL re-point each to the entry now carrying that `moze_id`; a link whose target is gone SHALL be cleared and its definition set `paused` with `review_reason = loan_missing`.

The report SHALL contain: per-type counts of imported and skipped-future rows; counts of groups, transfers, rules, attachments, counterparties; `needs_review` count with reasons; `unsupported_rules`, `orphaned_rules`, `settings_skipped`; the balance comparison below; the FX sanity-check outcome; the mappings confirmed at import time (due rule, rounding map, refund direction, tag delimiter, `balanceInfo` key); and a `schedules` block with `definitions` (`recurring`, `installment`, `single`: created, updated, ended, deleted), `instances` (created, updated, kept, `kept_owner_edited`, adopted, deleted, `posted_from_past`, `skipped_disabled`), `records_mapped`, `rewards_ignored`, `unsupported_types` (per type), `past_records_already_posted`, `past_records_already_skipped`, `past_records_amount_differs` (`count` and `instance_ids` of the posted instances with a differing line, and `lines`: one item per differing line), `past_records_owner_pending` (`[{definition_id, seq, date}]`), `relinked` (template and settlement links re-pointed), `review` (`[{moze_id, reason}]`, including same-date duplicates), `amount_differs` (one item per differing line of a pending period whose amounts the import keeps — every pending period of a definition with `template_owner_edited = true`, and every retained `edited_by_owner` pending period, compared at the amounts it will post) and `loan_remainder_check` (`[{definition, moze_remainder, open_amount, difference}]` per loan definition). A line item is `{definition_id, name, seq, date, line, kind, amount, moze_amount}`: the template line's index and kind (`transfer_in` for a transfer's in-leg), HomeHub's amount and the matching MOZE record's amount — lines are compared one by one (repayment, interest, expense, both transfer legs), never as period totals. `past_records_amount_differs.lines`, `amount_differs` and `loan_remainder_check` are the only report lists with amounts; logs SHALL carry none of them. The `skipped_future` counts and `records_mapped + rewards_ignored + Σ unsupported_types` SHALL both equal the number of enabled future records.

**Balance comparison.** For each imported account the report SHALL show `moze_part` = `opening_balance + Σ amount` of that account's `moze_backup` entries, plus its `schedule` entries that stand in for records skipped as already posted, plus MOZE's own amount of each record not imported because an owner-edited pending instance holds it (MOZE booked it, HomeHub has not yet), with `posted_date` (for such a record, its date) `≤ the backup's balance cache date` (`AHAccount.cacheDate`, falling back to the export date), MOZE's cached balance from `balanceInfo` when the implementation has confirmed which key holds the current balance, and the difference. Accounts without a comparable cached balance, and MOZE's system accounts (`type` 3 and 4), SHALL be listed under `not_compared` for the owner's manual check. Manual entries and entries posted after the cache date SHALL NOT enter the comparison. The report SHALL also show, per account, `previous_moze_part`: the same sum over the MOZE-sourced entries that existed before this import (for the first backup import, the phase 1 CSV ledger the owner already checked against MOZE), so the owner can see every account whose balance moved and why (new records since the CSV export, MOZE's own FX amounts, skipped future rows).

Exploration on the real backup (2026-10-02) found that `balanceInfo` is keyed by Unix timestamps of period ends and that no simple record filter reproduces its values for more than 38 of 60 accounts, so `balanceInfo` is treated as **not comparable** unless the first implementation task finds the exact rule; the `previous_moze_part` comparison and the manual check are the acceptance path.

The report header SHALL state `compared_accounts: N of M` and `not_compared: M − N`, and the CLI SHALL print a warning when N is 0, so that an import with nothing compared is never read as fully verified. `previous_moze_part` is a difference check against the earlier ledger, not a verification against MOZE; the manual check stays in acceptance regardless of N. In strict mode (default for the CLI, `strict=true` for REST) a non-zero difference on any compared account SHALL fail the import; `not_compared` accounts never fail it. Until `balanceInfo` is confirmed comparable on the real backup, the phase 1 manual check (10 accounts including 3 cards and 1 JPY account) remains part of acceptance.

#### Scenario: Balances equal MOZE
- **WHEN** `MOZE_4.0.zip` is imported into a database holding the phase 1 CSV import
- **THEN** the import SHALL succeed, every `moze_import` entry SHALL be gone, and every compared account's `moze_part` SHALL equal MOZE's cached balance

#### Scenario: Manual entries do not break strict mode
- **GIVEN** an account whose MOZE balance is `1000` and a manual expense of `-100` on it
- **WHEN** the backup is imported in strict mode
- **THEN** the comparison SHALL use `moze_part = 1000`, the import SHALL succeed, and the account's balance SHALL be `900`

#### Scenario: Re-import is idempotent
- **WHEN** the same backup is imported twice
- **THEN** the second run SHALL leave identical accounts, entries (by `moze_id`), groups, rules and balances, and no duplicate categories, projects or counterparties
- **AND** every schedule definition and instance SHALL be unchanged (same ids, statuses, seqs, dates and overrides), and the report's `schedules` block SHALL show 0 created, 0 deleted and 0 adopted

#### Scenario: Manual rows survive
- **GIVEN** 3 manual entries exist
- **WHEN** a backup import runs
- **THEN** the 3 entries SHALL remain and their categories SHALL not be deleted

## ADDED Requirements

### Requirement: Schedule definitions and instances from the backup

The backup importer SHALL map MOZE's scheduled data to `schedule_definition` and `schedule_instance` rows (accounting-schedules spec) in its ledger transaction, after inserting entries. New imported definitions SHALL have `created_locally = false`, `status = active`, `posting_mode = auto` and `auto_post_from` = the import's Asia/Taipei date.

- **Installments.** Each live `AHInstallment` SHALL become a definition with `kind = installment`, `moze_id = identifier`, `interval_unit = month`, `interval_n = 1`, `day_of_month = dayOfMonth`, `anchor_date = dateInfo[0]`, `times`, `total_amount = total`, `first_seq = 1`, `moze_payload` = the raw row, and template lines from its first package: a type-6 record → a `repayment` line (`account_id` = the record's account, `category_id`, `amount = |total|`, `loan_entry_id` = the entry imported from the record's `relatedID`); a type-15 record in the same package → an `interest` line; a type-0 record → an `expense` line. `name` = the first record's `name`, else its category's name, else `分期`. When `dateInfo` is not the monthly series on `dayOfMonth` (clamped to each month's length) the definition SHALL be imported `paused` with `review_reason = interval_mismatch`.
- **Recurring.** Each live `AHPeriod` SHALL become a definition with `kind = recurring`, `interval_n = 1`, and: `unit = 1` → `interval_unit = week` on the weekday given by `days`, `day_of_month = NULL`; `unit = 2` → `interval_unit = month` with `day_of_month = days` (clamped to the month's length); `times = 0` → `times = NULL` (unlimited), else `times`; `anchor_date` = the date of the earliest record generated for that `eventID` (past or future), with `first_seq = 1`; `startDate` is the next occurrence MOZE tracks and SHALL only be checked to be an occurrence of the rule. `type = 1` periods generate transfers (record type 2); `type = 0` periods generate ordinary records. Lines SHALL come from the first record generated for that `eventID` (record type → line kind: 0 `expense`, 1 `income`, 2 `transfer` with the in-leg's account and amount as `to_account_id` / `to_amount`, 3 `receivable`, 4 `payable`, 5 `collection`, 6 `repayment`, 15 `interest`). Another `unit`, a weekday or day of month that disagrees with the records, or a `startDate` that is not an occurrence of the rule SHALL import the definition `paused` with `review_reason = interval_mismatch`.
- **Single future records.** An enabled future record without `eventID`, other than a reward, SHALL become its own `recurring` definition with `times = 1` and `moze_id = "record:" + identifier`.
- **Loan record missing.** A definition whose loan record (`relatedID`) is not imported SHALL be imported `paused` with `review_reason = loan_missing`.
- **Instances and seq.** Each record of type 0–6 or 15 generated by a definition (past or future) SHALL map to exactly one instance: `moze_id` = the record's identifier, `rule_date` = `due_date` = its `date` (Asia/Taipei), `seq` = for an `AHInstallment` the 1-based position of that date in `dateInfo`, for an `AHPeriod` 1 + the occurrence index of that date counted from the anchor over past and future records together; `amount_override` = `|total|` per line, `"0"` for a line whose package member is missing; `moze_payload` = the raw records. A type-15 record that shares `packageID` with a type-6 record, and the in-leg of a transfer, SHALL merge into the same instance (`moze_record_ids` lists every merged record). Two records of one definition on the same date SHALL be listed under `schedules.review` and only the first mapped. Type-14 records SHALL be ignored and counted under `rewards_ignored`; other types SHALL be counted under `unsupported_types` and ignored.
- **Status.** A past record imported as an entry SHALL give a `posted` instance with `acted_by = import`, `acted_at` = the import's start time and `posted_entry_ids` = those entries. A disabled record (past or future) SHALL give a `skipped` instance with `acted_by = import`. An enabled future record SHALL give a `pending` instance.
- **Re-import.** Definitions SHALL be upserted by `moze_id`: rule, template, `name`, `times`, `total_amount` and `moze_payload` refreshed; `posting_mode` and `auto_post_from` kept. `status` SHALL stay as the owner left it, except that a mismatch or `loan_missing` that is new in this import SHALL set `review_reason` and `status = paused`, and a `review_reason` that no longer applies SHALL be cleared without changing `status`. Instances SHALL be upserted by `moze_id`. An instance without `moze_id` of the same definition whose `rule_date` equals the record's date (else, when none, whose `due_date` does) SHALL be adopted: it gets the record's `moze_id` and `moze_record_ids`, and, when `pending` and not `edited_by_owner`, its `amount_override`; an adopted instance that is `posted` keeps its entries and the record is counted under `past_records_already_posted` instead of being imported. When the record's computed `seq` belongs to another instance of the definition, the existing instance SHALL keep its seq and the record SHALL be listed under `schedules.review` and skipped; the import SHALL never fail on it. `pending` imported instances SHALL be refreshed unless `edited_by_owner` is true, in which case they SHALL be kept unchanged — also when their record is now past (it is then not imported, "Record mapping") — and their amounts compared with MOZE's under `amount_differs`. Instances with `acted_by = import` SHALL be recomputed from the newly imported entries; one with no entries left SHALL become `skipped` with `acted_by = import`. Instances posted by `auto` or `owner`, and every `skipped` instance whose `acted_by` is not `import`, SHALL be kept unchanged. Imported `pending` instances absent from the backup SHALL be deleted unless `edited_by_owner`; `pending` instances without `moze_id` SHALL never be deleted by an import. An imported definition absent from the backup SHALL be set `ended` with its `pending` instances deleted when it has any posted or skipped instance, and deleted otherwise. No `pending` instance SHALL be created for an `ended` definition. Definitions with `created_locally = true` and their instances SHALL never be written, except for the link re-pointing of "Transactional full replace and report".
- **Owner-set template amounts.** For a definition with `template_owner_edited = true` (the owner changed its amounts with scope `following` or `all`, accounting-schedules "Instance amount edit scope"), the import SHALL keep the template's line amounts (a refreshed line at the same index with the same kind keeps the stored amount), SHALL NOT write MOZE's amounts as `amount_override` on its `pending` instances (new, refreshed or adopted), and SHALL list each line of a pending period whose MOZE amount differs under `amount_differs`; adoption, dates, statuses and the other re-import rules SHALL apply unchanged.
- **Amounts of skipped past records.** When a past record is not imported because a kept posted instance lists it, each template line of that instance SHALL be compared with the matching MOZE record (`moze_record_ids` keeps which record is which line: a packaged repayment and its interest map to their own lines, a transfer's legs to its out- and in-amount); every line whose absolute posted amount differs from the record's `|total|` SHALL be listed under `past_records_amount_differs.lines` and the instance counted in `count` / `instance_ids`.
- **Loan check.** For each imported installment with a `repayment` line the report SHALL compare `AHInstallment.remainder` with the loan entry's open amount after the import, listing both and the difference under `loan_remainder_check`.

#### Scenario: Real backup schedule counts
- **WHEN** `MOZE_4.0.zip` (exported 2026-10-01) is imported into an empty schedule table
- **THEN** the report SHALL show 11 `recurring` and 14 `installment` definitions created, `records_mapped = 534`, `rewards_ignored = 63`, and `skipped_future` summing to 597

#### Scenario: Weekly period keyed by weekday
- **GIVEN** an `AHPeriod` with `unit = 1`, `days` naming Monday, `times = 0`, `startDate = 2026-10-05`, and generated records dated 2026-09-21, 2026-09-28, 2026-10-05 and 2026-10-12
- **WHEN** imported (exported 2026-10-01)
- **THEN** a definition with `interval_unit = week`, `interval_n = 1`, `anchor_date = 2026-09-21`, `times = NULL` SHALL exist, the 09-21 and 09-28 records SHALL be `posted` instances (`acted_by = import`, seq 1 and 2), and the 10-05 and 10-12 records SHALL be `pending` instances seq 3 and 4

#### Scenario: Monthly period keyed by day of month
- **GIVEN** an `AHPeriod` with `unit = 2`, `days = 21`, `times = 12`, `type = 1`, generated transfer pairs dated 2026-08-21 through 2027-07-21, and `startDate = 2026-10-21`
- **WHEN** imported
- **THEN** a definition with `interval_unit = month`, `day_of_month = 21`, `anchor_date = 2026-08-21`, `times = 12` and one `transfer` line SHALL exist, with one instance per pair

#### Scenario: Day of month clamped
- **GIVEN** an `AHPeriod` with `unit = 2`, `days = 31` and records on 2027-01-31 and 2027-02-28
- **WHEN** imported
- **THEN** the definition SHALL have `day_of_month = 31`, both records SHALL map to consecutive seqs, and it SHALL NOT be flagged `interval_mismatch`

#### Scenario: Weekday mismatch flagged
- **GIVEN** an `AHPeriod` with `unit = 1` whose `days` names Tuesday while its records fall on Mondays
- **WHEN** imported
- **THEN** the definition SHALL be `paused` with `review_reason = interval_mismatch` and listed under `schedules.review`


#### Scenario: Installment anchored on its first period
- **GIVEN** an `AHInstallment` with `dayOfMonth = 9`, `times = 36` and `dateInfo` 2026-02-09, 2026-03-09, … 2029-01-09
- **WHEN** imported
- **THEN** the definition SHALL have `anchor_date = 2026-02-09`, and the record dated 2026-11-09 SHALL be the instance with `seq = 10`

#### Scenario: Repayment and interest are one period
- **GIVEN** a type-6 record of `total = -8333` and a type-15 record of `total = -612` sharing a `packageID`, both dated 2026-11-09, generated by installment I
- **WHEN** imported
- **THEN** one instance of I due 2026-11-09 SHALL exist with `amount_override = ["8333", "612"]` and both record ids in `moze_record_ids`

#### Scenario: Disabled future period
- **GIVEN** a disabled type-0 record dated 2026-12-03 generated by `AHPeriod` P
- **WHEN** imported
- **THEN** P's 2026-12-03 instance SHALL be `skipped` with `acted_by = import`

#### Scenario: Single record keyed apart from definitions
- **GIVEN** an enabled future expense record `X1` without `eventID`, dated 2026-11-20
- **WHEN** imported
- **THEN** a `recurring` definition with `moze_id = "record:X1"` and `times = 1` SHALL exist with one pending instance due 2026-11-20

#### Scenario: HomeHub-generated period adopted
- **GIVEN** an unlimited imported weekly definition whose pending instance due 2027-10-04 HomeHub generated (no `moze_id`), and a later backup that holds a generated record R dated 2027-10-04
- **WHEN** that backup is imported
- **THEN** the same instance SHALL carry `moze_id = R`, and no second instance SHALL exist for 2027-10-04

#### Scenario: Posted HomeHub period adopted
- **GIVEN** a weekly imported definition whose instance due 2027-10-04 HomeHub generated (no `moze_id`) and posted on 2027-10-04, and a backup exported 2027-10-06 holding record R dated 2027-10-04
- **WHEN** that backup is imported
- **THEN** the instance SHALL carry `moze_id = R` and stay `posted`, R SHALL NOT become an entry, and `past_records_already_posted` SHALL count it

#### Scenario: Moved period adopted by its rule date
- **GIVEN** an instance without `moze_id` with `rule_date` 2027-10-04 that the owner moved to 2027-10-06, and a backup record R dated 2027-10-04
- **WHEN** imported
- **THEN** that instance SHALL be adopted and keep `due_date` 2027-10-06

#### Scenario: Seq conflict reported, not fatal
- **GIVEN** a definition whose instance `seq = 5` is due 2027-01-04 without `moze_id`, and a backup record dated 2027-01-11 whose computed seq is 5
- **WHEN** imported
- **THEN** the import SHALL succeed, the existing instance SHALL keep `seq = 5`, and the record SHALL be listed under `schedules.review` and not mapped

#### Scenario: New review reason pauses, resolved one clears
- **GIVEN** an imported definition the owner left `active`, and a second imported definition that is `paused` with `review_reason = interval_mismatch` that the owner then resumed
- **WHEN** a backup is imported in which the first definition's records no longer fit its rule and the second's do
- **THEN** the first SHALL be `paused` with `review_reason = interval_mismatch`, and the second SHALL have `review_reason` cleared and stay `active`

#### Scenario: Re-pointing finds no loan
- **GIVEN** a local definition whose repayment line references the `moze_backup` payable with `moze_id = P`
- **WHEN** a backup without P is imported
- **THEN** the line's `loan_entry_id` SHALL be cleared, the definition SHALL be `paused` with `review_reason = loan_missing`, and `schedules.review` SHALL list it

#### Scenario: Owner-edited pending period kept
- **GIVEN** an imported pending instance whose amount the owner changed to `9000` with `PUT /api/accounting/schedules/instances/{id}`
- **WHEN** the backup is imported again with the record's `total = -8333`
- **THEN** the instance SHALL keep `amount_override = ["9000", …]` and `edited_by_owner = true`, and `instances.kept_owner_edited` SHALL count it

#### Scenario: Owner-set price survives a re-import
- **GIVEN** an imported weekly definition whose template amount MOZE gave as `100`, which the owner changed to `120` with `scope = all`, leaving its pending periods seq 3 and seq 4 without overrides
- **WHEN** the same backup (records of `−100`) is imported again
- **THEN** the template amount SHALL stay `120`, seq 3 and seq 4 SHALL have no `amount_override`, and `schedules.amount_differs` SHALL list line 0 of seq 3 and of seq 4 with `amount = "120"` and `moze_amount = "100"`

#### Scenario: Owner-edited period compared on re-import
- **GIVEN** a definition with `template_owner_edited = true` and template amount `150`, whose pending period the owner edited to `120`
- **WHEN** a backup whose record for that period has `total = -100` is imported
- **THEN** the period SHALL keep `amount_override = ["120"]` and `schedules.amount_differs` SHALL list exactly one item for it, with `amount = "120"` and `moze_amount = "100"`

#### Scenario: Owner-edited period whose record turned past
- **GIVEN** an imported pending period (record R, due 2026-10-09) that the owner edited to `120`
- **WHEN** a backup exported on 2026-10-12, holding R as a past record of `−100`, is imported
- **THEN** no entry SHALL be created for R, the period SHALL stay `pending` with `amount_override = ["120"]`, and `schedules.past_records_owner_pending` SHALL list its definition, seq and date
- **AND** when the owner then posts it, exactly one `−120` entry SHALL exist for the period, and a further import SHALL count R under `past_records_already_posted`

#### Scenario: Re-import keeps owner decisions and local definitions
- **GIVEN** an imported instance the owner skipped, an imported definition the owner set to `confirm`, and a local definition with 3 posted instances
- **WHEN** the backup is imported again
- **THEN** the instance SHALL still be `skipped`, the definition SHALL still be `confirm`, and the local definition and its instances SHALL be unchanged

#### Scenario: Different amount reported
- **GIVEN** HomeHub posted instance 77 with repayment `8333`, and the next backup holds that record as a past record with `total = -8400`
- **WHEN** imported
- **THEN** the record SHALL NOT become an entry and `past_records_amount_differs` SHALL list instance 77, with one line item for the repayment line (`amount = "8333"`, `moze_amount = "8400"`)

#### Scenario: Different allocation reported per line
- **GIVEN** HomeHub posted a period as repayment `8333` and interest `620`
- **WHEN** the next backup holds its records as a past repayment of `−8400` and interest of `−553` (the same total)
- **THEN** `past_records_amount_differs.lines` SHALL list both lines (`8333` / `8400` and `620` / `553`); with identical amounts nothing SHALL be listed

#### Scenario: CLI import runs the job
- **GIVEN** an import through `python -m app.services.moze_backup_import_service` whose backup holds a pending period due on the import date
- **WHEN** the import succeeds and releases its lock
- **THEN** the CLI SHALL run the schedule job inline: the horizon SHALL be generated, and the period SHALL be posted when `ACCOUNTING_SCHEDULER_ENABLED` is on, or stay `pending` and be counted as due but unposted when it is off

#### Scenario: Loan link survives the full replace
- **GIVEN** a `schedule` repayment settling the `moze_backup` payable with `moze_id = P`
- **WHEN** the backup is imported again and the payable is re-created with a new id
- **THEN** the repayment's `settles_entry_id` and the definition's `loan_entry_id` SHALL point at the new entry

#### Scenario: Template reference keeps a category
- **GIVEN** a local definition whose line uses category `娛樂/串流`, which no entry uses
- **WHEN** the backup is imported
- **THEN** `娛樂/串流` SHALL NOT be deleted as unused

## REMOVED Requirements

### Requirement: Scheduled data preserved for phase 4

**Reason**: Superseded by "Schedule definitions and instances from the backup": future records and `AHPeriod` / `AHInstallment` rows now become real definitions and instances instead of raw JSON in `moze_schedule`.

**Migration**: The migration drops `moze_schedule` (its rows are reproducible from the backup); the post-upgrade runbook step is a backup import, which creates the definitions and instances. `GET /api/accounting/imports/schedules` is removed; clients use `GET /api/accounting/schedules/definitions` and `GET /api/accounting/schedules/instances`.
