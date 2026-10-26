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
- `logo` (VARCHAR(32), nullable; a slug of the SPA's built-in brand-logo set, `^[a-z0-9-]{1,32}$`; the API validates the shape only)
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

#### Scenario: Logo slug shape
- **WHEN** an account is created or updated with `logo` `"Cathay"`, `"cathay_united"` or 33 characters
- **THEN** the API SHALL answer 422; `"cathay-united"` SHALL be stored and returned on every account read

### Requirement: Read-only ledger endpoints

The service SHALL expose these read endpoints:

- `GET /api/accounting/accounts?as_of=YYYY-MM-DD` (default today; `balance`, `balance_main` and `available_credit` count entries with `posted_date ≤ as_of`): every account with `id`, `name`, `currency`, `opening_balance`, `balance`, `balance_main` (the balance converted to the preference's main currency with the latest cached rate; equal to `balance` for main-currency accounts), `entry_count`, `group_id`, `group_name`, `icon`, `logo`, `color`, `is_archived`, `include_in_total`, `is_credit`, `closing_day`, `due_rule`, `due_value`, `credit_limit`, `combined_account_id`, `credit_sharing_id`, `auto_pay_account_id`, `fx_fee_pct`, `sort_order` and `moze_id`, sorted by group order, then `sort_order`, then name. `?include_archived=false` (default) SHALL omit archived accounts.
- `GET /api/accounting/accounts/{id}?as_of=`: one account with the same fields plus `note` and `reward_rules` (the account's rules, read-only in 2a).
- `GET /api/accounting/accounts/{id}/entries`: that account's entries, newest first (the reverse of the canonical order). It SHALL be paginated with `limit` (default 50, max 500) and `offset`, filterable by `kind`, `date_from`, `date_to` and `q` (case-insensitive match on name, merchant, description), and SHALL include each entry's category path (`main/sub`), category icon and colour, project name, counterparty name, group summary, attached rule names and a running balance.
- `GET /api/accounting/entries`: entries across accounts, newest first by canonical order, with the same pagination and filters plus `account_id` (repeatable) and `hide_rewards`. Each item SHALL carry the account name and currency. A month summary endpoint `GET /api/accounting/entries/summary?month=YYYY-MM` SHALL return expense, income and net totals in the main currency, converting foreign-currency accounts with the latest cached rate.
- `GET /api/accounting/entries/{id}`: the entry with its children (fee, discount), its group and sibling children, its transfer counterpart, the entry it settles or refunds and the entries that settle or refund it, attached rules, and reward entries generated from it.

#### Scenario: Accounts list shows computed balances
- **GIVEN** the MOZE export has been imported
- **WHEN** a client calls `GET /api/accounting/accounts`
- **THEN** each account's `balance` SHALL equal its opening balance plus the sum of its posted entries

#### Scenario: Running balance in entry history
- **WHEN** a client lists an account's entries
- **THEN** every entry SHALL carry `running_balance`, equal to the account balance immediately after that entry in canonical order

#### Scenario: Unknown account
- **WHEN** a client requests entries for an account id that does not exist
- **THEN** the response SHALL be HTTP 404

#### Scenario: Cross-account listing filters by text
- **GIVEN** entries named `午餐` on two accounts and one named `電費`
- **WHEN** a client calls `GET /api/accounting/entries?q=午餐`
- **THEN** exactly the two `午餐` entries SHALL be returned, newest first

#### Scenario: Entry detail includes links
- **GIVEN** an expense with a fee child, two attached rules and one reward entry generated from it
- **WHEN** `GET /api/accounting/entries/{id}` is called
- **THEN** the response SHALL include the fee child, both rule names and the reward entry
