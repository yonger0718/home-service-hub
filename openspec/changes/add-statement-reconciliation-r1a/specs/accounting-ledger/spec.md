## MODIFIED Requirements

### Requirement: Account model

The system SHALL persist accounts in an `account` table with these columns:

- `id` (PK)
- `name` (VARCHAR(64), UNIQUE, NOT NULL)
- `currency` (VARCHAR(8), NOT NULL; ISO-4217 or a MOZE code such as `USDT`)
- `opening_balance` (NUMERIC(20,4), NOT NULL, default 0)
- `is_archived` (BOOLEAN, default FALSE)
- `group_id` (FK → `account_group.id`, nullable)
- `icon` (VARCHAR(16), nullable; an emoji) and `color` (VARCHAR(9), nullable; `#RRGGBB`)
- `note` (TEXT, nullable)
- `sort_order` (INTEGER, default 0)
- `include_in_total` (BOOLEAN, default TRUE)
- `is_credit` (BOOLEAN, default FALSE)
- `closing_day` (SMALLINT 1–31, nullable; the statement closing day; NULL means the calendar month)
- `due_rule` (enum `fixed_day`, `days_after_closing`, nullable) and `due_value` (SMALLINT, nullable)
- `credit_limit` (NUMERIC(20,4), nullable)
- `combined_account_id` (FK → `account.id`, nullable; the 主帳戶 whose statement includes this account)
- `credit_sharing_id` (UUID, nullable; accounts with the same value share one credit limit)
- `auto_pay_account_id` (FK → `account.id`, nullable)
- `fx_fee_pct` (NUMERIC(6,3), nullable), `fx_fee_rounding` (enum `floor`, `round`, `ceil`, `keep`, nullable), `fx_fee_refundable` (BOOLEAN, default FALSE)
- `statement_password_rule` (VARCHAR(64), nullable; the NAME of the rule the statement worker uses to open this account's password-protected PDFs, never a password), `statement_live_from` (DATE, nullable; NULL means every statement of the account is historical, otherwise statements whose `period_end` is on or after it are live) and `statement_source_root` (enum `mail`, `manual`, nullable; the Drive root its statement folders live under)
- `settings_locally_edited` (BOOLEAN, default FALSE; set by any settings write, cleared by the owner to let the next import overwrite settings)
- `moze_id` (VARCHAR(64), UNIQUE, nullable)
- `created_at` and `updated_at` (TIMESTAMPTZ)

An account's balance SHALL be `opening_balance + Σ ledger_entry.amount` over its entries whose `posted_date` is not after today. `currency` SHALL NOT change while the account has any entry, by edit or by import. The credit-only fields `due_rule`, `due_value`, `credit_limit`, `combined_account_id`, `credit_sharing_id` and `auto_pay_account_id` SHALL be NULL unless `is_credit` is TRUE; `closing_day` and the `fx_fee_*` fields are allowed on any account. `combined_account_id` SHALL NOT point at the account itself and SHALL NOT form a cycle.

#### Scenario: Balance includes opening balance and all entries
- **GIVEN** an account with `opening_balance = 2000`
- **AND** entries with amounts `-120`, `-80.5` and `+500`
- **WHEN** the balance is computed
- **THEN** it SHALL equal `2299.5000`

#### Scenario: Future-posted entry is excluded from the balance
- **GIVEN** an account with an entry of `-100` whose `posted_date` is tomorrow
- **WHEN** the balance is computed today
- **THEN** that entry SHALL NOT be included

#### Scenario: Account names are unique
- **GIVEN** an account named `Line Bank` exists
- **WHEN** a second account named `Line Bank` is inserted
- **THEN** the insert SHALL fail with a uniqueness violation

#### Scenario: Credit fields require the credit flag
- **WHEN** an account with `is_credit = false` is saved with `credit_limit = 300000`
- **THEN** the save SHALL be rejected with a validation error naming `credit_limit`

#### Scenario: Currency frozen once entries exist
- **GIVEN** a TWD account with 3 entries
- **WHEN** `PUT /api/accounting/accounts/{id}` sets `currency = JPY`
- **THEN** the response SHALL be HTTP 422 naming `currency`

#### Scenario: Statement columns default to unconfigured
- **WHEN** an account is created without statement settings
- **THEN** `statement_password_rule`, `statement_live_from` and `statement_source_root` SHALL be NULL
- **AND** its statements SHALL be treated as historical


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
- `source` (enum: `moze_import`, `moze_backup`, `manual`, `hermes`, `rule`, `schedule`, `statement`)
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
- **WHEN** a schedule posts the interest of a loan from 範例銀行
- **THEN** the `interest` entry SHALL have `counterparty_id` = 範例銀行

#### Scenario: Statement is a valid entry source
- **WHEN** an entry is inserted with `source = 'statement'`
- **THEN** the insert SHALL succeed
- **AND** the enum value SHALL have been added by a migration step in an Alembic `autocommit_block` before any use

