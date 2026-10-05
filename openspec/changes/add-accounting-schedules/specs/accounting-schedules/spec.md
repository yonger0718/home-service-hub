## ADDED Requirements

### Requirement: Schedule definition model

The system SHALL persist each recurring or installment rule in a `schedule_definition` table with:

- `id` (PK), `moze_id` (VARCHAR(64), UNIQUE, nullable; the `AHPeriod` / `AHInstallment` identifier, or `record:<AHRecord identifier>` for a single imported record)
- `kind` (enum `schedule_kind`: `recurring`, `installment`), `name` (VARCHAR(128), NOT NULL)
- `template` (JSONB, NOT NULL; see "Template lines")
- `interval_unit` (enum `schedule_interval`: `day`, `week`, `month`, `year`) and `interval_n` (SMALLINT, ≥ 1)
- `anchor_date` (DATE, NOT NULL; occurrence 0 of the rule) and `day_of_month` (SMALLINT 1–31, nullable; monthly and yearly only; NULL means the anchor's day)
- `first_seq` (INTEGER, NOT NULL, default 1, ≥ 1), `times` (INTEGER, nullable, ≥ `first_seq`; NULL = until stopped), `end_date` (DATE, nullable, ≥ `anchor_date`)
- `total_amount` (NUMERIC(20,4), nullable, > 0; installments only; for a loan, the loan amount)
- `posting_mode` (enum `schedule_posting_mode`: `auto`, `confirm`; default `auto`)
- `status` (enum `schedule_status`: `active`, `paused`, `ended`; default `active`)
- `auto_post_from` (DATE, NOT NULL; the Asia/Taipei date the definition was created locally or first imported)
- `generated_until` (DATE, nullable), `created_locally` (BOOLEAN, NOT NULL, default true), `template_owner_edited` (BOOLEAN, NOT NULL, default false; set when the owner changes the template's amounts through a period edit with scope `following` or `all`, see "Instance amount edit scope")
- `moze_payload` (JSONB, nullable; the raw MOZE row when imported), `review_reason` (VARCHAR(64), nullable)
- `created_at`, `updated_at` (TIMESTAMPTZ)

An `installment` definition SHALL have `interval_unit = month` and `times ≥ 2`. A definition with `day_of_month` set SHALL have `interval_unit` `month` or `year`.

#### Scenario: Local recurring definition
- **WHEN** on 2026-10-03 the owner creates a monthly Netflix definition starting 2026-10-22
- **THEN** a row SHALL exist with `kind = recurring`, `interval_unit = month`, `interval_n = 1`, `anchor_date = 2026-10-22`, `times = NULL`, `posting_mode = auto`, `status = active`, `auto_post_from = 2026-10-03`, `created_locally = true`, `moze_id = NULL`

#### Scenario: Installment must be monthly
- **WHEN** a client creates an `installment` definition with `interval_unit = week`
- **THEN** the response SHALL be HTTP 422 naming `interval_unit`

#### Scenario: Installment needs two periods
- **WHEN** a client creates an `installment` definition with `times = 1`
- **THEN** the response SHALL be HTTP 422 naming `times`

### Requirement: Schedule instance model

The system SHALL persist one row per period in `schedule_instance` with:

- `id` (PK), `definition_id` (FK → `schedule_definition.id`, ON DELETE CASCADE), `seq` (INTEGER), `rule_date` (DATE, NOT NULL; the rule's occurrence date, set at generation or import and never changed by an instance edit), `due_date` (DATE; an Asia/Taipei date; equals `rule_date` unless the owner moved the period)
- `status` (enum `schedule_instance_status`: `pending`, `posted`, `skipped`)
- `posted_entry_ids` (JSONB array of `ledger_entry.id`, NOT NULL, default `[]`), `is_partial` (BOOLEAN, NOT NULL, default false)
- `acted_at` (TIMESTAMPTZ, nullable), `acted_by` (enum `schedule_actor`: `auto`, `owner`, `import`; nullable)
- `amount_override` (JSONB, nullable; a list of unsigned decimal strings, one per template line; `"0"` = the line is not written this period), `edited_by_owner` (BOOLEAN, NOT NULL, default false)
- `last_error` (TEXT, nullable), `last_error_at` (TIMESTAMPTZ, nullable), `reopened_at` (TIMESTAMPTZ, nullable), `note` (VARCHAR(128), nullable)
- `moze_id` (VARCHAR(64), UNIQUE, nullable), `moze_record_ids` (JSONB array, NOT NULL, default `[]`), `moze_payload` (JSONB, nullable)
- `created_at`, `updated_at`

Constraints: `UNIQUE (definition_id, seq)`; a partial unique index on `(definition_id, due_date) WHERE status = 'posted'`; `status = 'posted'` if and only if `posted_entry_ids` is non-empty; `status = 'pending'` if and only if `acted_at` IS NULL; `is_partial` only on `posted` rows. Indexes SHALL exist on `(status, due_date)` and, with `jsonb_path_ops`, a GIN index on `posted_entry_ids`. The schedule service SHALL refuse (HTTP 422 naming `amounts`) any write that leaves an `amount_override` whose length differs from the template's line count.

#### Scenario: One row per period
- **GIVEN** a definition with an instance of `seq = 3`
- **WHEN** generation tries to insert `seq = 3` again
- **THEN** no second row SHALL be created

#### Scenario: Override length checked
- **GIVEN** a definition with two template lines
- **WHEN** `PUT /api/accounting/schedules/instances/{id}` sends `amounts = ["8333"]`
- **THEN** the response SHALL be HTTP 422 naming `amounts`

### Requirement: Template lines

`template` SHALL be an object `{"lines": [...], "description": string | null, "tags": [string]}` with 1 to 10 lines. Each line SHALL have exactly the keys `kind`, `account_id`, `to_account_id`, `to_amount`, `counterparty_id`, `category_id`, `project_id`, `amount`, `currency`, `loan_entry_id`, `name`, `merchant` (unused keys `null`), validated on every write:

- `kind` ∈ `expense`, `income`, `receivable`, `payable`, `transfer`, `repayment`, `collection`, `interest`;
- `account_id`: an existing, non-archived account; `currency` SHALL equal its currency;
- `amount`: an unsigned decimal string > 0 with at most 4 decimals, in the account currency;
- `transfer`: `to_account_id` required and different from `account_id`; `to_amount` required when the two currencies differ, else NULL (defaults to `amount`);
- `receivable` / `payable`: `counterparty_id` required;
- `repayment`: `loan_entry_id` required, naming a `payable` entry with `is_settlement = false` in the line's currency; `collection`: the same for a `receivable`;
- `category_id`, when set, SHALL belong to the line's ledger kind (`transfer_out` for `transfer`, `payable` for `repayment`, `receivable` for `collection`); `interest` lines without a category use the system 利息 category.

Lines SHALL carry no FX, fee or discount children, split members, reward rules, photos or invoice fields. Violations SHALL be refused with HTTP 422 naming `lines[i].<key>`.

#### Scenario: Loan repayment template
- **GIVEN** a payable entry 4021 of `+300000` TWD (信貸, counterparty 範例銀行)
- **WHEN** a template `{"lines": [{"kind": "repayment", "account_id": 3, "amount": "8333", "currency": "TWD", "loan_entry_id": 4021, …}, {"kind": "interest", "account_id": 3, "amount": "620", "currency": "TWD", …}]}` is saved
- **THEN** it SHALL be accepted

#### Scenario: Currency mismatch refused
- **WHEN** a line names a JPY account with `currency = "TWD"`
- **THEN** the response SHALL be HTTP 422 naming `lines[0].currency`

### Requirement: Rows referenced by definitions

While a definition that is not `ended` references an account, category, counterparty, project or loan entry in its template, archiving it (`is_archived = true` on an account or project, `is_hidden = true` on a category) or deleting it (`DELETE` on the account, category, project or counterparty, or `DELETE /api/accounting/entries/{id}` on the loan entry) SHALL be refused with HTTP 409 naming the definition. `PUT /api/accounting/splits/{group_id}` on a group whose members a `posted` instance lists SHALL be refused with HTTP 409 naming the instance.

#### Scenario: Archiving a paying account
- **GIVEN** the active definition `信貸 每月還款` pays from account 薪轉
- **WHEN** 薪轉 is archived
- **THEN** the response SHALL be HTTP 409 naming `信貸 每月還款`

#### Scenario: Deleting a loan with an active schedule
- **WHEN** `DELETE /api/accounting/entries/4021` is called while `信貸 每月還款` is active
- **THEN** the response SHALL be HTTP 409 naming `信貸 每月還款`

### Requirement: Instance generation

Occurrence k (k = 0, 1, …) of a definition's rule SHALL have the date:

- `day`: `anchor_date + k × interval_n` days; `week`: `anchor_date + 7 × k × interval_n` days;
- `month`: the month `interval_n × k` months after the anchor's, on day `min(day_of_month or the anchor's day, the month's length)`, always computed from the anchor;
- `year`: the anchor's month `interval_n × k` years later, the day clamped the same way.

For every `active` or `paused` definition, generation SHALL repeatedly insert the rule's next occurrence strictly after the latest existing instance's `rule_date` (occurrence 0 when the definition has none), as an instance with `rule_date = due_date` = that occurrence, with `seq` = the previous maximum `seq` + 1 (`first_seq` for the first instance), while `seq ≤ times` (when set), `due_date ≤ end_date` (when set) and `due_date ≤ today + 13 months` (the same day of month, clamped). An occurrence whose date equals a `posted` instance's `due_date` of the same definition SHALL be passed over without consuming a seq. Generation SHALL never delete, move or re-date an existing instance, and SHALL set `generated_until` to the horizon. `today` SHALL be the Asia/Taipei date. A definition SHALL become `ended` when its last occurrence (by `times` or `end_date`) exists and none of its instances is `pending`. Generation SHALL run on definition create and update and in every job run, each definition in its own transaction that first takes the shared import lock (see "Daily schedule job").

#### Scenario: 13-month horizon
- **GIVEN** today is 2026-10-03
- **WHEN** an unlimited monthly definition anchored 2026-10-22 is created
- **THEN** 13 instances SHALL exist, `seq` 1 to 13, due 2026-10-22 through 2027-10-22, and `generated_until = 2027-11-03`

#### Scenario: Month-end anchor clamps and returns
- **WHEN** a monthly definition anchored 2026-01-31 is generated
- **THEN** its due dates SHALL be 2026-01-31, 2026-02-28, 2026-03-31, 2026-04-30

#### Scenario: Finite run
- **WHEN** a definition anchored 2026-11-09 with `times = 3` is generated
- **THEN** exactly 3 instances SHALL exist (2026-11-09, 2026-12-09, 2027-01-09)

#### Scenario: End date stops the run
- **WHEN** a weekly definition anchored 2026-10-05 with `end_date = 2026-10-26` is generated
- **THEN** its instances SHALL be due 2026-10-05, 10-12, 10-19 and 10-26

#### Scenario: Rolling forward
- **GIVEN** the definition of "13-month horizon"
- **WHEN** the job runs on 2026-10-21 and again on 2026-10-22
- **THEN** after the first run no instance `seq = 14` SHALL exist, and after the second an instance `seq = 14` due 2027-11-22 SHALL exist

#### Scenario: A period moved later does not shift the series
- **GIVEN** a monthly definition on the 22nd whose latest instance `seq = 13` (`rule_date` 2027-10-22) the owner moved to 2027-10-25
- **WHEN** generation runs on 2026-10-22
- **THEN** the new instance SHALL be `seq = 14` with `rule_date` and `due_date` 2027-11-22, and `seq = 13` SHALL keep `rule_date` 2027-10-22

#### Scenario: A period moved earlier does not repeat the series
- **GIVEN** the same definition whose latest instance `seq = 13` (`rule_date` 2027-10-22) the owner moved to 2027-10-15
- **WHEN** generation runs on 2026-10-22
- **THEN** the new instance SHALL be `seq = 14` due 2027-11-22, and no second instance due 2027-10-22 SHALL be created

### Requirement: Posting an instance

Posting an instance SHALL, in one database transaction: take the shared import lock (HTTP 409 `import_running` when an import holds it); lock its definition (`FOR SHARE`; `FOR UPDATE` when the template has a loan line, since the post may end the definition) and the instance (`FOR UPDATE`); refuse with HTTP 409 (`already_posted` or `skipped`) unless the instance is `pending`; re-validate every line as in "Template lines" (a failure names `lines[i].<key>`); resolve each line's amount — the owner's instance edit, else the imported `amount_override`, else the template's `amount` — and leave out lines whose amount is `"0"`; and write each line through the ledger services with `entry_date = posted_date = due_date`, `entry_time = NULL`, `source = 'schedule'`, name `line.name` or the definition's `name`, the template's `description` and `tags`, without updating category defaults:

- `expense`, `income`, `receivable`, `payable` → one entry of that kind, signed as in "Entry write endpoints";
- `transfer` → a transfer pair (`out_amount = amount`, `in_amount = to_amount or amount`);
- `repayment` → a `payable` entry of `−amount` with `is_settlement = true`, `settles_entry_id = loan_entry_id` and the loan's counterparty; `collection` → a `receivable` entry of `+amount` likewise;
- `interest` → an `interest` entry of `−amount` carrying the loan's counterparty when the template has a repayment or collection line.

Loan rules: when the loan entry is `is_closed` or its open amount is 0, nothing SHALL be written; the instance and every later `pending` instance of the definition SHALL become `skipped` with `note = 貸款已結清`, and the definition `ended`. When the open amount is below the repayment amount, the repayment SHALL be clamped to the open amount; interest SHALL NOT be clamped. A repayment or collection whose loan entry is missing or of the wrong kind SHALL be refused (HTTP 422 naming `loan_entry_id`). The cutover lock (D19) SHALL NOT apply to the loan entry when a schedule posts against it.

When two or more non-transfer entries are written they SHALL share a new `entry_group` (`kind = installment` for an installment definition, else `split`) named `<definition name> #k/N` (`#k` when `times` is NULL). The instance SHALL then be `posted` with `posted_entry_ids` = every top-level entry written, `acted_at = now()`, `acted_by` (`auto` when the job posts it; `owner` when `post`, `catch-up`, `resume` with `backlog = post` or `repost` posts it), `is_partial = false`, and `last_error`, `last_error_at`, `reopened_at` cleared. On any error nothing SHALL be written by the posting transaction; a separate transaction SHALL store the error message in `last_error` with `last_error_at`, and the instance SHALL stay `pending`.

Lock order: the shared import lock → `schedule_definition` → `schedule_instance` → the ledger's order (`entry_group` rows ascending → target entries with their transfer legs in one statement → nothing else). No path that holds an entry or group lock SHALL lock a schedule row.

#### Scenario: Repayment with interest
- **GIVEN** a definition `信貸 每月還款` (`installment`, `times = 36`) with the template of "Loan repayment template", the loan's open amount `300000`, and its `seq = 1` instance due 2026-11-09
- **WHEN** the instance is posted
- **THEN** account 3 SHALL change by `−8953`, a repayment of `−8333` dated 2026-11-09 SHALL settle entry 4021, an `interest` entry of `−620` with counterparty 範例銀行 SHALL exist, both SHALL share an `installment` group named `信貸 每月還款 #1/36`, both SHALL have `source = 'schedule'`, and the loan's open amount SHALL be `291667`

#### Scenario: Last repayment clamped to the open amount
- **GIVEN** the same loan, whose open amount is `8245` because the owner once settled `100` by hand, and its `seq = 36` instance with `amount_override = ["8345", "620"]`
- **WHEN** the instance posts
- **THEN** the repayment SHALL be `−8245`, the interest `−620`, and the loan's open amount `0`

#### Scenario: Posting twice is refused
- **GIVEN** an instance already `posted`
- **WHEN** `POST /api/accounting/schedules/instances/{id}/post` is called
- **THEN** the response SHALL be HTTP 409 and no entry SHALL be written

#### Scenario: Closed loan ends the schedule
- **GIVEN** the loan entry has `is_closed = true` and the definition has pending instances due 2026-12-09 to 2027-10-09
- **WHEN** the job reaches the 2026-12-09 instance
- **THEN** no entry SHALL be written, every pending instance SHALL be `skipped` with note `貸款已結清`, and the definition SHALL be `ended`

#### Scenario: Archived account found at posting time
- **GIVEN** a definition that was `ended`, whose second line's account was then archived
- **WHEN** the owner reopens one of its skipped instances (allowed despite the archived reference) and posts it
- **THEN** the reopen SHALL succeed, the post SHALL write nothing, and `last_error` SHALL name `lines[1].account_id`

#### Scenario: Zero override leaves a line out
- **GIVEN** an instance with `amount_override = ["8333", "0"]`
- **WHEN** it posts
- **THEN** only the repayment SHALL be written and no group SHALL be created

#### Scenario: Recurring transfer
- **GIVEN** a monthly definition with one line `transfer 15000` from 薪轉 to 交割 (both TWD)
- **WHEN** its 2026-10-05 instance posts
- **THEN** a `transfer_out` of `−15000` and a `transfer_in` of `+15000` dated 2026-10-05 SHALL share a transfer group and both ids SHALL be in `posted_entry_ids`

### Requirement: Daily schedule job

The accounting service SHALL run a schedule job in-process (APScheduler, `coalesce`, one instance at a time) at 00:05 Asia/Taipei every day, about 10 seconds after startup, after every successful backup import that is not a dry run (once the import has released its advisory lock), and every 10 minutes after a run that ended `busy` or `import_running` until a run completes that Taipei day, unless `ACCOUNTING_SCHEDULER_ENABLED` is `false`. A run SHALL:

1. take the PostgreSQL advisory lock `0x53434844` with `pg_try_advisory_lock` on a dedicated connection, and return `busy` without doing anything when it is held;
2. generate instances for every definition, one transaction per definition;
3. post, each in its own transaction with `acted_by = auto`, every `pending` instance with `due_date ≤ today`, `due_date ≥ auto_post_from` of its definition and `reopened_at` NULL whose definition is `active` with `posting_mode = auto`, ordered by `definition_id`, `seq` — within one definition the periods SHALL be attempted in `seq` order, never in `due_date` order — selecting and locking each instance with `FOR UPDATE SKIP LOCKED`; each posting transaction SHALL first take `pg_try_advisory_xact_lock_shared` on the import lock key and the run SHALL stop with `import_running` when an import holds it; after a failure the same definition's later instances SHALL NOT be posted in that run; and, checked under the definition's row lock, an instance SHALL NOT be posted while an earlier `seq` of the same definition is `pending` and either due for the job (the conditions above) or failed (`last_error` set), even when the owner moved that earlier instance's `due_date` past this one's (instances due before `auto_post_from` and reopened instances are the owner's and do not hold the series);
4. log a report with counts and ids only (`status`, `generated`, `posted`, `failed`, `stopped_definitions`), never names or amounts.

`confirm`, `paused` and `ended` definitions, instances due before their definition's `auto_post_from`, and reopened instances SHALL never be auto-posted. An earlier pending period that is due before `auto_post_from` or reopened SHALL hold the definition's later periods only when it carries `last_error`; otherwise it is exempt from the sequence barrier. The job SHALL never convert or import anything. It SHALL also be runnable as `python -m app.services.schedule_job [--dry-run]`; `--dry-run` SHALL print the report of what it would generate and post and write nothing. The standalone backup importer CLI has no in-process scheduler: after a successful real import, once it has released the import lock, it SHALL run the job inline in its own process — generation always, posting only when `ACCOUNTING_SCHEDULER_ENABLED` is not `false` / `0` / `no`; otherwise the run SHALL log the count of due instances it did not post.

#### Scenario: A moved earlier period holds the next one
- **GIVEN** an `auto` monthly definition whose seq 1 the owner moved to 2026-11-10 and whose seq 2 is due 2026-11-09
- **WHEN** the job runs on 2026-11-10
- **THEN** seq 1 SHALL be attempted before seq 2
- **AND** when seq 1 fails, seq 2 SHALL stay `pending` in that run and in later runs while seq 1 is still `pending` and failing, and SHALL be posted by the first run after the owner posts seq 1

#### Scenario: No silent backlog after an import
- **GIVEN** a backup imported on 2026-10-03 with an `auto` definition whose pending instances are due 2026-10-01 and 2026-10-03
- **WHEN** the job runs at the end of the import
- **THEN** the 2026-10-03 instance SHALL be `posted` with `acted_by = auto` and the 2026-10-01 instance SHALL stay `pending` and appear in the 待完成交易 queue

#### Scenario: Back-dated local definition
- **GIVEN** on 2026-10-03 the owner creates an `auto` monthly definition anchored 2026-08-15
- **WHEN** the job runs
- **THEN** the 2026-08-15 and 2026-09-15 instances SHALL stay `pending` in the queue and nothing SHALL be posted

#### Scenario: Only one runner
- **GIVEN** a run is in progress
- **WHEN** a second run starts
- **THEN** it SHALL return `busy` and post nothing

#### Scenario: Retry after an import
- **GIVEN** the 00:05 run on 2026-10-09 stopped with `import_running`
- **WHEN** the import finishes at 00:07
- **THEN** a run SHALL execute at the end of the import or by 00:15 and post the 2026-10-09 instances

#### Scenario: Failure stops the loan, not the others
- **GIVEN** loan definition L has pending instances due 2026-10-09 and 2026-11-09 (both after `auto_post_from`) whose first fails, and definition N has one due 2026-11-01
- **WHEN** the job runs on 2026-11-10
- **THEN** L's 2026-10-09 instance SHALL carry `last_error`, L's 2026-11-09 instance SHALL stay `pending` without an error, and N's instance SHALL be posted

#### Scenario: Paused definition
- **GIVEN** a paused definition with an instance due today
- **WHEN** the job runs
- **THEN** the instance SHALL stay `pending`

#### Scenario: Dry run
- **WHEN** `python -m app.services.schedule_job --dry-run` runs with two due `auto` instances
- **THEN** it SHALL print `posted` listing both ids and the database SHALL be unchanged

### Requirement: Definition endpoints

The service SHALL expose:

- `GET /api/accounting/schedules/definitions?status=&kind=` returning every definition (default: all statuses) ordered by next due date, each with `id`, `kind`, `name`, `status`, `posting_mode`, `interval_unit`, `interval_n`, `anchor_date`, `day_of_month`, `first_seq`, `times`, `end_date`, `total_amount`, `auto_post_from`, `template` (lines with `account_name`, `to_account_name`, `category`, `counterparty` added), `created_locally`, `template_owner_edited`, `imported` (`moze_id` is set), `review_reason`, `generated_until`, `posted_count`, `skipped_count`, `pending_count`, `next_due_date`, `next_amount` (signed sum of the next pending instance's lines, per currency), `remaining` and `repaid` (see "Loan summary"), `loan_entry_id`, `needs_check` (an `ended` loan definition whose loan's open amount is not 0, or `review_reason` set), and `failing` (`{instance_id, due_date, last_error}` of the earliest pending instance with an error, or null).
- `GET /api/accounting/schedules/definitions/{id}` returning the same object plus `instances` (all, by `seq`).
- `POST /api/accounting/schedules/definitions` with `kind`, `name`, `template`, `interval_unit`, `interval_n`, `anchor_date`, optional `day_of_month`, `times`, `end_date`, `total_amount`, `posting_mode`, and optional `loan`. It SHALL create the definition with `auto_post_from` = today, generate its instances, post nothing, and return HTTP 201 with the definition. For an `installment` with `total_amount`, the first line's `amount` is the per-period amount; the last instance SHALL get an `amount_override` covering every line — the first line's remainder so that the periods sum to `total_amount`, the template amounts for the other lines (for a loan of `300000` over 36 with interest `620`: `["8345", "620"]`) — and a request where `amount × (times − 1) ≥ total_amount` SHALL be refused (HTTP 422 naming `total_amount`). `loan` (`{account_id, counterparty_id, category_id, amount, entry_date, name}`, installments only) SHALL create a `payable` entry of `+amount` (`source = 'manual'`) in the same transaction, set `total_amount = loan.amount`, and set `loan_entry_id` on every `repayment` line whose `loan_entry_id` is null.
- `PUT /api/accounting/schedules/definitions/{id}` with the create fields except `kind` and `loan`. It SHALL lock the definition (`FOR UPDATE`) then its instances, delete its `pending` instances due on or after tomorrow, save the new rule, and regenerate (the first new instance is the new rule's first occurrence on or after tomorrow and strictly after the latest remaining instance's `rule_date` and `due_date`, with `seq` = the remaining maximum + 1). Occurrences SHALL always be computed from the rule anchor: the edit SHALL NOT rebase `anchor_date` or `first_seq` implicitly. `anchor_date` SHALL change only when the request sends a different `anchor_date` (stored as occurrence 0 of the new rule); a `month` or `year` rule sent with a new `anchor_date` and no `day_of_month` SHALL get `day_of_month` = that anchor's day. With the anchor unchanged, `day_of_month` SHALL be stored as sent (null keeps the anchor's own day), so a 31st or a 02-29 anchor keeps returning to its day. Overrides of remaining pending instances SHALL be kept for lines still at the same index with the same kind and cleared otherwise. Posted and skipped instances SHALL keep their status, dates and entries; the one change is metadata: when the request changes the template's line amounts, every `posted` instance without `amount_override` SHALL first get the template amounts before the edit as its `amount_override` (the amounts it was posted with; its entries are not touched), so an installment's periods keep summing to `total_amount`.
- `DELETE /api/accounting/schedules/definitions/{id}` deleting the definition and its instances; refused with HTTP 409 when any instance is `posted`, with a message pointing to `end`.

Every write SHALL first take the shared import lock (HTTP 409 `import_running` when an import holds it). Unknown ids SHALL return HTTP 404. Every write SHALL return the definition in the list shape.

#### Scenario: Card installment remainder on the last period
- **WHEN** an `installment` with one `expense` line of `3333`, `times = 3`, `total_amount = 10000`, anchored 2026-10-15 is created
- **THEN** instances SHALL be due 2026-10-15, 2026-11-15, 2026-12-15 and the last SHALL have `amount_override = ["3334"]`

#### Scenario: Loan and schedule in one call
- **WHEN** a definition is posted with `loan = {account_id: 3, counterparty_id: 7, amount: 300000, entry_date: 2026-10-03, name: 信貸}`, `times = 36`, a repayment line of `8333` with `loan_entry_id = null` and anchor 2026-11-09
- **THEN** account 3 SHALL gain `+300000` from a `payable` entry, `total_amount` SHALL be `300000`, the repayment line SHALL reference that entry, 13 instances SHALL exist, and none SHALL be posted

#### Scenario: Edit regenerates only future pending periods, then rolls forward
- **GIVEN** today 2026-10-03, an unlimited monthly `1000` definition with seq 1 (2026-09-01) posted, seq 2 (2026-10-01) pending and overdue, and seq 3 to 14 pending from 2026-11-01
- **WHEN** its amount is changed to `1200` and `day_of_month` to 15
- **THEN** seq 1 SHALL keep its entry (its `amount_override` records `["1000"]`, the amount it was posted with), seq 2 SHALL be unchanged, the first regenerated instance SHALL be seq 3 due 2026-10-15 with amount `1200`, `anchor_date` SHALL be 2026-09-15 (occurrence 0 of the new rule, not rebased to seq 3) and `first_seq` 1
- **AND** when the job runs on 2026-11-15 the next new instance SHALL be due 2027-11-15 with the next free seq

#### Scenario: Edit keeps a month-end anchor
- **GIVEN** today 2026-02-01 and a monthly definition anchored 2026-01-31 without `day_of_month`, seq 1 posted on 2026-01-31
- **WHEN** its amount is edited with `anchor_date = 2026-01-31` and no `day_of_month`
- **THEN** the regenerated instances SHALL be due 2026-02-28, 2026-03-31 and 2026-04-30, `anchor_date` SHALL stay 2026-01-31, and later roll-forwards SHALL keep landing on the month's last day up to the 31st
- **AND** a yearly definition anchored 2028-02-29 and edited on 2028-03-01 SHALL generate 2029-02-28, 2030-02-28, 2031-02-28 and 2032-02-29

#### Scenario: Edit with a new anchor takes its day
- **GIVEN** the month-end definition above
- **WHEN** it is edited with `anchor_date = 2026-02-10` and no `day_of_month`
- **THEN** `anchor_date` SHALL be 2026-02-10, `day_of_month` 10, and the regenerated instances SHALL be due 2026-02-10 and 2026-03-10

#### Scenario: Delete refused after posting
- **GIVEN** a definition with one posted instance
- **WHEN** `DELETE /api/accounting/schedules/definitions/{id}` is called
- **THEN** the response SHALL be HTTP 409

### Requirement: Definition state endpoints

The service SHALL expose, each taking the shared import lock and then locking the definition `FOR UPDATE` before its instances, and returning the definition:

- `POST /api/accounting/schedules/definitions/{id}/pause` → `status = paused` (409 unless `active`).
- `POST /api/accounting/schedules/definitions/{id}/resume` with `{"backlog": "skip" | "post"}` (default `skip`) → `status = active`; pending instances due ≤ today SHALL be skipped (`acted_by = owner`), or, after the status change commits, posted one transaction each in `seq` order, stopping at the first failure (409 unless `paused`).
- `POST /api/accounting/schedules/definitions/{id}/end` → `status = ended` and every `pending` instance deleted (409 when already `ended`).
- `PUT /api/accounting/schedules/definitions/{id}/mode` with `{"posting_mode": "auto" | "confirm"}`; switching to `auto` SHALL set `auto_post_from` to today (Asia/Taipei), so the switch never auto-posts earlier periods.
- `POST /api/accounting/schedules/definitions/{id}/catch-up` (補入帳至今天) → post every `pending` instance due ≤ today in `seq` order, whatever the mode, `auto_post_from` or `reopened_at`, with `acted_by = owner`, one transaction each, stopping at the first failure; refused with HTTP 409 while the definition is `paused`. The response SHALL be `{"posted": [instance ids], "failed": {"instance_id", "error"} | null, "definition": …}` with HTTP 200 even when one failed.

#### Scenario: Resume skips the paused months by default
- **GIVEN** a definition paused on 2026-07-01 with pending instances due 2026-07-22, 08-22 and 09-22, and today 2026-10-03
- **WHEN** `resume` is called without a body
- **THEN** the three instances SHALL be `skipped` and the definition `active`

#### Scenario: End removes pending periods
- **GIVEN** a definition with 2 posted and 11 pending instances
- **WHEN** `end` is called
- **THEN** 2 instances SHALL remain and the definition SHALL be `ended`

#### Scenario: Ending an ended definition
- **GIVEN** an `ended` definition
- **WHEN** `end` is called
- **THEN** the response SHALL be HTTP 409

#### Scenario: Catch-up posts the backlog in order
- **GIVEN** a `confirm` definition with pending instances due 2026-09-22 and 2026-10-01, and today 2026-10-03
- **WHEN** `catch-up` is called
- **THEN** both SHALL be posted, 09-22 first, with `acted_by = owner`

#### Scenario: Catch-up refused while paused
- **GIVEN** a paused definition with an overdue pending instance
- **WHEN** `catch-up` is called
- **THEN** the response SHALL be HTTP 409

#### Scenario: Switching to automatic posting
- **GIVEN** today 2026-10-03 and a `confirm` definition with pending instances due 2026-09-22 and 2026-10-03
- **WHEN** `PUT …/mode` sets `posting_mode = auto`
- **THEN** `auto_post_from` SHALL be 2026-10-03, the next job run SHALL post the 2026-10-03 instance, and the 2026-09-22 instance SHALL stay in the queue

#### Scenario: Write during an import
- **GIVEN** a backup import is running
- **WHEN** `pause` is called
- **THEN** the response SHALL be HTTP 409 `import_running`

### Requirement: Instance endpoints

The service SHALL expose:

- `GET /api/accounting/schedules/instances?from=&until=&status=&definition_id=&queue=` returning instances ordered by `due_date`, `definition_id`, `seq`. Defaults: `status = pending`, `until = today + 30`, no `from`. `queue=true` SHALL return the 待完成交易 set for definitions with `status = active`: `pending` instances due ≤ `until` that belong to a `confirm` definition, or are reopened, or carry `last_error`, or are due before their definition's `auto_post_from`, plus every `posted` instance with `is_partial = true` whatever its date (the `until` window does not apply to them). Each item SHALL have `id`, `definition_id`, `definition_name`, `kind`, `posting_mode`, `seq`, `times`, `due_date`, `status`, `is_partial`, `overdue_days` (`today − due_date` for a pending past instance, else 0), `lines` (kind, account and to-account ids and names, category, counterparty, signed amount after the precedence of "Posting an instance", currency), `totals` (signed sum per currency), `last_error`, `reopened` (bool), `edited_by_owner`, `note`, `posted_entry_ids`, `acted_at`, `acted_by`.
- `PUT /api/accounting/schedules/instances/{id}` with optional `due_date` and `amounts` (unsigned, one per line) on a `pending` instance (409 otherwise), storing `amount_override` and setting `edited_by_owner = true`; a `due_date` equal to the `due_date` of a `posted` instance of the same definition SHALL be refused (HTTP 422 naming `due_date`). An optional `scope` (`this`, the default, `following` or `all`) applies an amounts-only edit to later or all periods as described in "Instance amount edit scope".
- `POST /api/accounting/schedules/instances/{id}/post` (`acted_by = owner`; see "Posting an instance").
- `POST /api/accounting/schedules/instances/{id}/skip` → `skipped`, `acted_by = owner` (409 unless `pending`). Skipping writes nothing, so a loan's open amount SHALL not change.
- `POST /api/accounting/schedules/instances/{id}/reopen` → the instance SHALL always end `pending`: a `skipped` instance becomes `pending`; a `posted` instance (partial or not) has every entry in `posted_entry_ids` deleted (with their children and transfer pairs, without the per-entry rules of "Deleting entries of a posted period") and becomes `pending`; both set `reopened_at`, and an `ended` definition SHALL become `active` (409 when already `pending`). Reopen SHALL be allowed even when the template references an archived row; posting then fails as in "Posting an instance".
- `POST /api/accounting/schedules/instances/{id}/repost` with `amounts` on a `posted` instance (partial or not): delete its entries and post again with the new `amount_override`, in one transaction (409 unless `posted`).
- `POST /api/accounting/schedules/instances/{id}/accept-partial` on a partial instance (保留部分): `is_partial = false`, `note` kept, entries unchanged; the instance then leaves the queue (409 unless `posted` with `is_partial = true`).

Every write SHALL first take the shared import lock (HTTP 409 `import_running`), and SHALL return the instance in the list shape; 422 from posting SHALL carry the field name.

#### Scenario: 待完成交易 queue
- **GIVEN** today 2026-10-03, a `confirm` instance due 2026-09-30, a `confirm` instance due 2026-10-20, an `auto` instance due 2026-10-05, an `auto` instance due 2026-10-01 with `last_error`, and a `confirm` instance due 2026-10-02 of a paused definition
- **WHEN** `GET /api/accounting/schedules/instances?queue=true` is called
- **THEN** it SHALL return the 09-30 item with `overdue_days = 3`, the 10-01 item, and the 10-20 item, in that order, and neither the 10-05 item nor the paused definition's item

#### Scenario: Skip leaves the loan open
- **GIVEN** the loan's open amount is `291667`
- **WHEN** its pending 2026-12-09 instance is skipped
- **THEN** the instance SHALL be `skipped`, `acted_by = owner`, no entry SHALL be written, and the open amount SHALL stay `291667`

#### Scenario: Reopen revives an ended definition
- **GIVEN** an `ended` definition whose last instance the owner skipped
- **WHEN** that instance is reopened
- **THEN** it SHALL be `pending` and the definition `active`

#### Scenario: Repost a repayment with a corrected amount
- **GIVEN** a posted instance with repayment `8333` and interest `620`
- **WHEN** `repost` is called with `amounts = ["8333", "598"]`
- **THEN** the old two entries SHALL be gone, a new interest entry of `−598` SHALL exist, and the instance SHALL be `posted`

#### Scenario: Accept a partial period
- **GIVEN** a partial instance whose interest entry was deleted
- **WHEN** `accept-partial` is called
- **THEN** `is_partial` SHALL be false, the note SHALL stay, the repayment SHALL remain, and the instance SHALL leave the queue

#### Scenario: Date edit onto a posted day refused
- **GIVEN** a weekly definition whose 2026-10-05 instance is posted
- **WHEN** the pending 2026-10-12 instance is moved to 2026-10-05
- **THEN** the response SHALL be HTTP 422 naming `due_date`

### Requirement: Instance amount edit scope

`PUT /api/accounting/schedules/instances/{id}` SHALL accept an optional `scope`: `this` (default; 僅這一期), `following` (這一期與之後) or `all` (全部週期). With `this` the edit SHALL behave as in "Instance endpoints": the instance's `amount_override` is stored and `edited_by_owner = true`. `following` and `all` SHALL be accepted only for a body that sends `amounts` and no other field; a body with `due_date`, or without `amounts`, and a scope other than `this` SHALL be refused with HTTP 422 naming `scope`. Their `amounts` SHALL be one per template line (HTTP 422 naming `amounts` otherwise) and each greater than 0, because they become template amounts. The instance SHALL be `pending` (HTTP 409 otherwise). Both SHALL take the shared import lock, then the definition (`FOR UPDATE`), then every instance of the definition (`FOR UPDATE`, ascending id), and in one transaction:

- `following`: every `pending` instance with a `seq` below the edited one that has no `amount_override` SHALL get an `amount_override` equal to the template amounts before the edit (it keeps the old price); the template's line amounts SHALL become the sent amounts and `template_owner_edited` SHALL become true; every `pending` instance with a `seq` at or above the edited one whose `edited_by_owner` is false SHALL have its `amount_override` cleared; the edited instance SHALL follow the template, unless it was already `edited_by_owner`, in which case its `amount_override` SHALL become the sent amounts.
- `all`: the same without the first step, the clearing applying to every `pending` instance of the definition; the overrides of other owner-edited instances SHALL be kept.

`posted` and `skipped` instances SHALL keep their status, dates and entries under any scope; the one change is metadata: before the template amounts change, every `posted` instance without `amount_override` SHALL get the template amounts before the edit as its `amount_override` (the amounts it was posted with; its entries are not touched). Instances generated later SHALL take the new template amounts. For an `installment` with `total_amount` and `times`, the pending last instance (`seq = times`) SHALL get an `amount_override` whose first line is the residual `total_amount − Σ` of the first line's effective amount of every other period — posted periods as posted (their override, else the template before the edit), skipped periods excluded, pending periods after the edit (preserved owner overrides, the old price `following` keeps, imported amounts, else the new template amounts), and periods not generated yet at the new template amount — and whose other lines are the new amounts (or a preserved owner override's); this SHALL hold for local and imported definitions alike, so the periods keep summing to `total_amount`. A request for which that residual is not above 0 SHALL be refused with HTTP 422 naming `amounts` (the message carries no amount) and SHALL change nothing. A last period not generated yet SHALL get its amount when it is generated. `following` and `all` SHALL be allowed on imported definitions before cutover (amounts are not rule fields); `PUT` and `DELETE` on the definition stay refused with `locked_until_cutover`.

#### Scenario: Only this period by default
- **GIVEN** a monthly Netflix definition with template amount `390` and pending instances seq 2 (2026-10-22), seq 3 (2026-11-22) and seq 4 (2026-12-22) without overrides
- **WHEN** `PUT /api/accounting/schedules/instances/{id}` sends `amounts = ["420"]` for seq 3 without `scope`
- **THEN** seq 3 SHALL have `amount_override = ["420"]` and `edited_by_owner = true`, the template amount SHALL stay `390`, and seq 2 and seq 4 SHALL be unchanged

#### Scenario: This period and the following ones keep the old price before
- **GIVEN** the same definition, whose seq 1 (2026-09-22) is posted with `−390`
- **WHEN** seq 3 is edited with `amounts = ["420"]` and `scope = following`
- **THEN** seq 1 SHALL keep its entry of `−390` and only record `amount_override = ["390"]` (the amount it was posted with), seq 2 SHALL have `amount_override = ["390"]`, the template amount SHALL be `420` with `template_owner_edited = true`, and seq 3 and seq 4 SHALL have no override (they post `−420`)

#### Scenario: All periods keep other owner edits
- **GIVEN** the same definition whose seq 4 the owner edited to `400`
- **WHEN** seq 2 is edited with `amounts = ["420"]` and `scope = all`
- **THEN** the template amount SHALL be `420`, seq 2 and seq 3 SHALL have no override, seq 4 SHALL keep `["400"]`, and the posted seq 1 SHALL keep its entry, its `amount_override` recording `["390"]`

#### Scenario: Installment total kept by a scoped edit
- **GIVEN** an `installment` of `10000` over 3 with pending periods of `3333`, `3333` and `3334`
- **WHEN** the second period is edited with `amounts = ["3000"]` and `scope = following`
- **THEN** the periods SHALL post `3333`, `3000` and `3667`, which sum to `10000`

#### Scenario: Scoped edit that would overbook refused
- **GIVEN** an `installment` of `10000` over 3 whose first period was posted at `6000`
- **WHEN** the second period is edited with `amounts = ["4000"]` and `scope = following`
- **THEN** the response SHALL be HTTP 422 naming `amounts` and nothing SHALL change

#### Scenario: A scope with another field is refused
- **WHEN** `PUT /api/accounting/schedules/instances/{id}` sends `due_date` with `scope = following`
- **THEN** the response SHALL be HTTP 422 naming `scope` and nothing SHALL change

#### Scenario: Imported definition before cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false` and a pending instance of an imported definition
- **WHEN** it is edited with `scope = all`
- **THEN** the response SHALL be HTTP 200, the template amounts SHALL change with `template_owner_edited = true`, and `DELETE` on the definition SHALL still answer HTTP 409 `locked_until_cutover`

### Requirement: Run-now endpoint

`POST /api/accounting/schedules/run-now` SHALL check the job lock and the import lock before starting and return HTTP 409 when either is held. Otherwise it SHALL run the daily job synchronously with trigger `manual` and return HTTP 200 with its report (`status`, `generated`, `posted`, `failed`, `stopped_definitions`); when an import starts during the run, the run SHALL stop and the response SHALL be HTTP 200 with `status = import_running` and the partial counts. It SHALL be served behind the same perimeter as every accounting endpoint; no extra credential is defined.

#### Scenario: Manual run while the job runs
- **GIVEN** the 00:05 run holds the job lock
- **WHEN** `run-now` is called
- **THEN** the response SHALL be HTTP 409 and nothing SHALL be posted twice

#### Scenario: Import starts mid-run
- **GIVEN** `run-now` has posted 2 instances
- **WHEN** an import takes the import lock before the third
- **THEN** the response SHALL be HTTP 200 with `status = import_running` and `posted` listing 2 ids

### Requirement: Deleting entries of a posted period

When an entry listed in a `posted` instance's `posted_entry_ids` is deleted (through `DELETE /api/accounting/entries/{id}`, the transfer or split delete), the server SHALL first read the instance without a lock, then take the shared import lock, lock its definition (`FOR SHARE`; `FOR UPDATE` when the definition is `ended`, since the delete may revive it) and the instance (`FOR UPDATE`), re-check that it still lists the entry, delete only that entry (with its children and its transfer pair, in the ledger's lock order), and remove it from `posted_entry_ids`. When entries remain, the instance SHALL stay `posted` with `is_partial = true` and `note = "部分入帳記錄已於 YYYY-MM-DD 刪除"`. When a pending or partial instance results and its definition is `ended`, the definition SHALL become `active` again. When none remain, an instance with `acted_by = import` SHALL become `skipped` with `note = 入帳記錄已刪除`, and any other instance SHALL become `pending` with `acted_at` and `acted_by` cleared, `is_partial = false`, `reopened_at = now()` and `note = "入帳記錄已於 YYYY-MM-DD 刪除"`. A reopened instance SHALL never be posted by the job; it stays in the 待完成交易 queue until posted or skipped. The backup importer's full-replace delete SHALL NOT apply this requirement.

#### Scenario: Deleting the interest leaves a partial period
- **GIVEN** the posted instance of "Repayment with interest" on 2026-11-09
- **WHEN** the owner deletes its interest entry on 2026-11-10
- **THEN** the repayment SHALL remain, the instance SHALL stay `posted` with `is_partial = true` and note `部分入帳記錄已於 2026-11-10 刪除`, and it SHALL appear in the queue

#### Scenario: Deleting the last entry reopens the period
- **GIVEN** that partial instance
- **WHEN** the owner then deletes the repayment
- **THEN** the loan's open amount SHALL return to `300000`, the instance SHALL be `pending` with `reopened_at` set, and the next job run SHALL NOT post it

#### Scenario: Deleting a MOZE-booked period
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = true` and an instance with `acted_by = import` listing one transfer pair
- **WHEN** the owner deletes one leg
- **THEN** both legs SHALL be deleted and the instance SHALL be `skipped` with note `入帳記錄已刪除`

#### Scenario: Deleting from an ended definition revives it
- **GIVEN** an `ended` definition whose last posted instance lists one expense
- **WHEN** the owner deletes that expense
- **THEN** the instance SHALL be `pending` and the definition `active`

### Requirement: Imported definitions before cutover

While `ACCOUNTING_IMPORT_LOCKED` is false, `PUT` and `DELETE` on a definition with a `moze_id`, and `repost` on an instance with `acted_by = import`, SHALL be refused with HTTP 409 `locked_until_cutover`. `pause`, `resume`, `end`, `mode`, `catch-up` and the instance endpoints (including amount edits with `scope = following` or `all`) SHALL be allowed on imported definitions; the job SHALL post them like local ones. After cutover every definition SHALL be editable.

#### Scenario: Template edit refused during the mirror period
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false` and an imported loan definition
- **WHEN** `PUT /api/accounting/schedules/definitions/{id}` is called
- **THEN** the response SHALL be HTTP 409 with `locked_until_cutover`

#### Scenario: Repost of a MOZE-booked period refused before cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false` and an instance with `acted_by = import`
- **WHEN** `repost` is called
- **THEN** the response SHALL be HTTP 409 with `locked_until_cutover`

#### Scenario: Imported period posts against an imported loan
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false`, a backup imported on 2026-10-03, a `moze_backup` payable, and an imported instance due 2026-10-09 repaying it
- **WHEN** the job runs on 2026-10-09
- **THEN** a `schedule` settlement of that payable SHALL be written despite the payable being locked for owner edits

### Requirement: Loan summary

For a definition with a `repayment` or `collection` line, `remaining` SHALL be the loan entry's open amount signed like the loan (negative for a payable; 0 when `is_closed`) and `repaid` SHALL be the sum of the absolute amounts of all entries settling it. For an `installment` without such a line, `remaining` SHALL be `total_amount` minus the absolute amounts of the entries actually posted for its first line (found through `posted_entry_ids`; null without `total_amount`) and `repaid` null. For other definitions both SHALL be null.

The entry detail of a `payable` or `receivable` original that a definition's line references SHALL include `loan_schedule`: `{definition_id, name, status, posting_mode, posted_count, times, next_due_date, next_amount, remaining, repaid, needs_check}`; otherwise `loan_schedule` SHALL be null.

#### Scenario: Loan detail after three periods
- **GIVEN** the `300000` loan with 3 posted repayments of `8333` and the next instance due 2027-02-09
- **WHEN** `GET /api/accounting/entries/4021` is called
- **THEN** `loan_schedule` SHALL report `remaining = -275001`, `repaid = 24999`, `posted_count = 3`, `times = 36`, `next_due_date = 2027-02-09`

#### Scenario: Card installment remaining from posted entries
- **GIVEN** the `10000` installment of "Card installment remainder on the last period" whose first period posted `−3333` and whose entry the owner then edited to `−3300`
- **WHEN** the definition is listed
- **THEN** `remaining` SHALL be `6700`

### Requirement: Schedule links on entries

`GET /api/accounting/entries`, `GET /api/accounting/accounts/{id}/entries` and `GET /api/accounting/entries/{id}` SHALL include `schedule` on every top-level entry: `{definition_id, instance_id, kind, seq, times, name, is_partial}` of the instance whose `posted_entry_ids` contains the entry, or null.

#### Scenario: Pill data on a posted entry
- **GIVEN** the 5th posted instance of a 36-period installment
- **WHEN** its repayment is listed
- **THEN** `schedule` SHALL be `{kind: installment, seq: 5, times: 36, is_partial: false, …}`
