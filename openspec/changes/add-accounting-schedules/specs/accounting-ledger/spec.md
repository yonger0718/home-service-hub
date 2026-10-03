## MODIFIED Requirements

### Requirement: Ledger entry model

The system SHALL persist every money movement in a `ledger_entry` table with these columns:

- `id` (PK)
- `account_id` (FK → `account.id`, ON DELETE RESTRICT, NOT NULL)
- `kind` (enum: `expense`, `income`, `transfer_out`, `transfer_in`, `receivable`, `payable`, `balance_adjustment`, `fee`, `discount`, `reward`, `interest`, `refund`)
- `amount` (NUMERIC(20,4), NOT NULL; positive increases the account balance, negative decreases it)
- `currency` (copied from the account at insert; `amount` is always in this currency)
- `original_amount` (NUMERIC(20,4), nullable) and `original_currency` (VARCHAR(8), nullable): the amount as recorded, when it was in a different currency
- `fx_rate` (NUMERIC(20,10), nullable) and `fx_source` (enum `fx_api`, `moze_backup`, `manual`, nullable): the rate used to convert `original_amount` into `amount`, and where it came from
- `entry_date` (DATE, NOT NULL): the consumption date
- `entry_time` (TIME, nullable)
- `posted_date` (DATE, NOT NULL, default = `entry_date`): the date the movement counts on the account; statements and balances use it
- `category_id` (FK, nullable)
- `project_id` (FK, nullable)
- `name`, `merchant` (VARCHAR(128), nullable)
- `counterparty_id` (FK → `counterparty.id`, nullable; required for `receivable` and `payable`; allowed on an `interest` entry, which carries the loan's counterparty when a schedule posts it for a loan; not set on other kinds)
- `description` (TEXT, nullable)
- `tags` (TEXT[], default empty)
- `parent_entry_id` (FK → `ledger_entry.id`, nullable; used by fee and discount children)
- `group_id` (FK → `entry_group.id`, nullable)
- `transfer_group_id` (UUID, nullable)
- `is_settlement` (BOOLEAN, NOT NULL, default FALSE; TRUE on collections and repayments, whether or not they are linked to an original; a check constraint requires it on a positive `receivable` or a negative `payable` and forbids it on other kinds)
- `settles_entry_id` (FK → `ledger_entry.id`, nullable; a collection or repayment pointing at the receivable or payable it settles)
- `refunds_entry_id` (FK → `ledger_entry.id`, nullable; a `refund` pointing at the refunded entry)
- `reward_rule_id` (FK → `reward_rule.id`, nullable) and `reward_source_entry_id` (FK → `ledger_entry.id`, nullable): set on `reward` entries
- `invoice_number` (VARCHAR(16), nullable) and `invoice_random` (VARCHAR(8), nullable)
- `needs_review` (BOOLEAN, default FALSE)
- `source` (enum: `moze_import`, `moze_backup`, `manual`, `hermes`, `rule`, `schedule`)
- `import_run_id` (FK → `import_run.id`, nullable)
- `moze_id` (VARCHAR(64), UNIQUE, nullable)
- `seq` (BIGINT, NOT NULL, UNIQUE; insertion order, assigned from a sequence)
- `created_at` and `updated_at` (TIMESTAMPTZ)

Indexes SHALL exist on `(account_id, entry_date, entry_time, seq)`, `(account_id, posted_date)`, `(entry_date)`, `transfer_group_id`, `counterparty_id`, `group_id`, `settles_entry_id`, `reward_source_entry_id` and `source`.

The free-text `counterparty` column of phase 1 SHALL be removed; the migration SHALL create one `counterparty` row per distinct value and link the entries.

#### Scenario: Entry currency follows its account
- **GIVEN** an account with currency `JPY`
- **WHEN** an entry is inserted for that account
- **THEN** the entry's `currency` SHALL be `JPY`

#### Scenario: Converted entry keeps its original amount
- **GIVEN** a TWD account
- **WHEN** an entry recorded as `-1800 JPY` is converted at rate `0.2`
- **THEN** the entry SHALL have `amount = -360`, `currency = TWD`, `original_amount = -1800` and `original_currency = JPY`
- **AND** only `amount` SHALL count toward the account balance

#### Scenario: A paired transfer shares a group id
- **WHEN** a transfer moves 1000 from account A to account B
- **THEN** there SHALL be a `transfer_out` entry of `-1000` on A and a `transfer_in` entry of `+1000` on B
- **AND** both SHALL carry the same non-null `transfer_group_id`

#### Scenario: Posting date defaults to the entry date
- **WHEN** an entry is created without `posted_date`
- **THEN** `posted_date` SHALL equal `entry_date`

#### Scenario: Migration links existing counterparties
- **GIVEN** phase 1 entries with `counterparty = 'Alan'` on 3 rows
- **WHEN** the migration runs
- **THEN** one `counterparty` row named `Alan` SHALL exist and the 3 entries SHALL reference it

#### Scenario: Loan interest names the lender
- **WHEN** a schedule posts the interest of a loan from 玉山銀行
- **THEN** the `interest` entry SHALL have `counterparty_id` = 玉山銀行

## ADDED Requirements

### Requirement: Schedule-sourced entries

The `entry_source` enum SHALL gain the value `schedule`, added by a migration after `7b1e4a2c9d05` with `ALTER TYPE entry_source ADD VALUE IF NOT EXISTS 'schedule'` executed inside an Alembic `autocommit_block`. Entries written by posting a schedule instance SHALL carry `source = 'schedule'`, `moze_id = NULL` and `import_run_id = NULL`. They SHALL be treated like manual rows: editable at all times (not subject to `locked_until_cutover`), and never deleted or changed by either importer, except that the backup importer re-points their `settles_entry_id` to the re-created MOZE entry (see the backup import spec, "Schedule definitions and instances from the backup"). `PUT /api/accounting/entries/{id}` and `PUT /api/accounting/transfers/{transfer_group_id}` SHALL accept them under the same rules as manual entries.

Deleting any entry that a `posted` schedule instance lists SHALL follow "Deleting entries of a posted period" in the schedules spec (only that entry, with its children and transfer pair, is deleted; the instance becomes partial, or `pending` / `skipped` when nothing remains). Deleting a `payable` or `receivable` original that a definition which is not `ended` references as `loan_entry_id` SHALL be refused with HTTP 409 naming the definition. `PUT /api/accounting/splits/{group_id}` on a group whose members a `posted` instance lists SHALL be refused with HTTP 409 naming the instance. The downgrade of the migration SHALL refuse while any `schedule` entry exists.

#### Scenario: Schedule entry survives a backup import
- **GIVEN** a `schedule` expense of `−390` posted on 2026-10-22
- **WHEN** a backup import runs
- **THEN** the entry SHALL still exist unchanged

#### Scenario: Schedule entry editable before cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false` and a `schedule` expense
- **WHEN** its name is changed with `PUT /api/accounting/entries/{id}`
- **THEN** the update SHALL succeed

#### Scenario: Deleting one leg of a scheduled transfer
- **GIVEN** a scheduled transfer posted on 2026-10-05 as the only line of its period
- **WHEN** its `transfer_in` leg is deleted
- **THEN** both legs SHALL be deleted and the instance SHALL be `pending` with `reopened_at` set

#### Scenario: Split edit on a scheduled group refused
- **GIVEN** the `installment` group `信貸 每月還款 #1/36` listed by a posted instance
- **WHEN** `PUT /api/accounting/splits/{group_id}` is called
- **THEN** the response SHALL be HTTP 409 naming the instance
