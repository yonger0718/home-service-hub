## ADDED Requirements

### Requirement: Account model

The system SHALL persist accounts in an `account` table with these columns:

- `id` (PK)
- `name` (VARCHAR(64), UNIQUE, NOT NULL)
- `currency` (VARCHAR(8), NOT NULL; ISO-4217 or a MOZE code such as `USDT`)
- `opening_balance` (NUMERIC(20,4), NOT NULL, default 0)
- `is_archived` (BOOLEAN, default FALSE)
- `created_at` and `updated_at` (TIMESTAMPTZ)

An account's balance SHALL be `opening_balance + Σ ledger_entry.amount` over its entries.

#### Scenario: Balance includes opening balance and all entries
- **GIVEN** an account with `opening_balance = 2000`
- **AND** entries with amounts `-120`, `-80.5` and `+500`
- **WHEN** the balance is computed
- **THEN** it SHALL equal `2299.5000`

#### Scenario: Account names are unique
- **GIVEN** an account named `Line Bank` exists
- **WHEN** a second account named `Line Bank` is inserted
- **THEN** the insert SHALL fail with a uniqueness violation

### Requirement: Category and project model

The system SHALL persist categories in a `category` table:

- `id` (PK)
- `kind` (the record kind it applies to)
- `parent_id` (FK → `category.id`, nullable)
- `name` (VARCHAR(64), NOT NULL)

`(kind, parent_id, name)` SHALL be unique.

Categories of any non-system kind MAY have two levels (a main category with sub-categories). System kinds (`fee`, `discount`, `reward`, `interest`, `balance_adjustment`) SHALL use a single fixed category each.

Projects SHALL be persisted in a `project` table with a unique `name`.

#### Scenario: Same sub-category name under different main categories
- **WHEN** expense categories `飲食/午餐` and `社交/午餐` are created
- **THEN** both SHALL exist as distinct rows sharing the name `午餐` with different `parent_id`

### Requirement: Ledger entry model

The system SHALL persist every money movement in a `ledger_entry` table with these columns:

- `id` (PK)
- `account_id` (FK → `account.id`, ON DELETE RESTRICT, NOT NULL)
- `kind` (enum: `expense`, `income`, `transfer_out`, `transfer_in`, `receivable`, `payable`, `balance_adjustment`, `fee`, `discount`, `reward`, `interest`, `refund`)
- `amount` (NUMERIC(20,4), NOT NULL; positive increases the account balance, negative decreases it)
- `currency` (copied from the account at insert)
- `entry_date` (DATE, NOT NULL)
- `entry_time` (TIME, nullable)
- `category_id` (FK, nullable)
- `project_id` (FK, nullable)
- `name`, `merchant`, `counterparty` (VARCHAR(128), nullable)
- `description` (TEXT, nullable)
- `tags` (TEXT[], default empty)
- `parent_entry_id` (FK → `ledger_entry.id`, nullable; used by fee and discount children)
- `transfer_group_id` (UUID, nullable)
- `needs_review` (BOOLEAN, default FALSE)
- `source` (enum: `moze_import`, `manual`, `hermes`)
- `import_run_id` (FK → `import_run.id`, nullable)
- `seq` (BIGINT, NOT NULL, UNIQUE; insertion order, assigned from a sequence)
- `created_at` (TIMESTAMPTZ)

Indexes SHALL exist on `(account_id, entry_date, entry_time, seq)`, `transfer_group_id`, `counterparty` and `source`.

#### Scenario: Entry currency follows its account
- **GIVEN** an account with currency `JPY`
- **WHEN** an entry is inserted for that account
- **THEN** the entry's `currency` SHALL be `JPY`

#### Scenario: A paired transfer shares a group id
- **WHEN** a transfer moves 1000 from account A to account B
- **THEN** there SHALL be a `transfer_out` entry of `-1000` on A and a `transfer_in` entry of `+1000` on B
- **AND** both SHALL carry the same non-null `transfer_group_id`

### Requirement: Canonical entry order

The *canonical order* of entries SHALL be `entry_date` ascending, then `entry_time` ascending with NULL times sorting **before** any timed entry on the same date, then `seq` ascending. `seq` is unique, so the order is total.

Running balances, pagination and every listing SHALL use this one order. Newest-first listings SHALL be its exact reverse.

#### Scenario: Same-minute entries have a stable order
- **GIVEN** three entries on one account with the same date and time, inserted with `seq` 10, 11 and 12
- **WHEN** the entries are listed twice, and paginated with `limit = 1`
- **THEN** every listing SHALL return them in the same order
- **AND** pagination SHALL return each entry exactly once

#### Scenario: Fee child follows its parent
- **GIVEN** an expense with a fee child imported from one CSV row
- **WHEN** running balances are computed
- **THEN** the expense SHALL be applied first and the fee immediately after it

#### Scenario: Untimed entries sort first in their day
- **GIVEN** an entry with `entry_time = NULL` and another at `08:00` on the same date
- **WHEN** listed in canonical order
- **THEN** the untimed entry SHALL come first

### Requirement: Legacy accounting schema removal

The migration SHALL drop the tables `transactions`, `categories`, `credit_cards`, `subscriptions`, `installments` and `payment_methods`. It SHALL abort without changes if any of those tables holds one or more rows. The downgrade SHALL restore the schema of revision `8a4c4f9b2d1b`.

#### Scenario: Migration refuses to drop non-empty legacy data
- **GIVEN** the legacy `transactions` table contains 1 row
- **WHEN** `alembic upgrade head` runs
- **THEN** the migration SHALL fail with an error naming the non-empty table
- **AND** no table SHALL be dropped or created

### Requirement: Read-only ledger endpoints

The service SHALL expose these endpoints:

- `GET /api/accounting/accounts`: every non-archived account with `id`, `name`, `currency`, `opening_balance`, `balance` and `entry_count`, sorted by `entry_count` descending.
- `GET /api/accounting/accounts/{id}/entries`: that account's entries, newest first (the reverse of the canonical order). It SHALL be paginated with `limit` (default 50, max 500) and `offset`, filterable by `kind`, `date_from` and `date_to`, and SHALL include each entry's category path (`main/sub`), project name and a running balance.

In phase 1, the service SHALL expose no endpoint that creates, updates or deletes accounts or entries, other than the import endpoint.

#### Scenario: Accounts list shows computed balances
- **GIVEN** the MOZE export has been imported
- **WHEN** a client calls `GET /api/accounting/accounts`
- **THEN** each account's `balance` SHALL equal its opening balance plus the sum of its entries

#### Scenario: Running balance in entry history
- **WHEN** a client lists an account's entries
- **THEN** every entry SHALL carry `running_balance`, equal to the account balance immediately after that entry in canonical order

#### Scenario: Unknown account
- **WHEN** a client requests entries for an account id that does not exist
- **THEN** the response SHALL be HTTP 404
