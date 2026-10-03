# accounting-ledger Specification

## Purpose
TBD - created by archiving change rebuild-accounting-moze-ledger. Update Purpose after archive.
## Requirements
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

### Requirement: Category and project model

The system SHALL persist categories in a `category` table:

- `id` (PK)
- `kind` (the record kind it applies to)
- `parent_id` (FK → `category.id`, nullable)
- `name` (VARCHAR(64), NOT NULL)
- `icon` (VARCHAR(16), nullable; emoji) and `color` (VARCHAR(9), nullable)
- `sort_order` (INTEGER, default 0)
- `is_hidden` (BOOLEAN, default FALSE; hidden categories are not offered in the entry form but keep their entries)
- `default_account_id` (FK → `account.id`, nullable) and `default_project_id` (FK → `project.id`, nullable): the last-used defaults for the entry form
- `moze_id` (VARCHAR(64), UNIQUE, nullable)

`(kind, parent_id, name)` SHALL be unique.

Categories of any non-system kind MAY have two levels (a main category with sub-categories). System kinds (`fee`, `discount`, `reward`, `interest`, `balance_adjustment`) SHALL use a single fixed category each.

Projects SHALL be persisted in a `project` table with a unique `name`, plus `is_archived` (BOOLEAN, default FALSE), `sort_order` and `moze_id` (UNIQUE, nullable).

A sub-category without its own `icon` or `color` SHALL resolve to its parent's values when read.

#### Scenario: Same sub-category name under different main categories
- **WHEN** expense categories `飲食/午餐` and `社交/午餐` are created
- **THEN** both SHALL exist as distinct rows sharing the name `午餐` with different `parent_id`

#### Scenario: Sub-category inherits icon and colour
- **GIVEN** `飲食` has icon `🍜` and colour `#f0cd92`, and `飲食/午餐` has neither
- **WHEN** `飲食/午餐` is returned by the API
- **THEN** its `icon` SHALL be `🍜` and its `color` `#f0cd92`

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
- `counterparty_id` (FK → `counterparty.id`, nullable; required for `receivable` and `payable`)
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
- `source` (enum: `moze_import`, `moze_backup`, `manual`, `hermes`, `rule`)
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

The service SHALL expose these read endpoints:

- `GET /api/accounting/accounts?as_of=YYYY-MM-DD` (default today; `balance`, `balance_main` and `available_credit` count entries with `posted_date ≤ as_of`): every account with `id`, `name`, `currency`, `opening_balance`, `balance`, `balance_main` (the balance converted to the preference's main currency with the latest cached rate; equal to `balance` for main-currency accounts), `entry_count`, `group_id`, `group_name`, `icon`, `color`, `is_archived`, `include_in_total`, `is_credit`, `closing_day`, `due_rule`, `due_value`, `credit_limit`, `combined_account_id`, `credit_sharing_id`, `auto_pay_account_id`, `fx_fee_pct`, `sort_order` and `moze_id`, sorted by group order, then `sort_order`, then name. `?include_archived=false` (default) SHALL omit archived accounts.
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

### Requirement: Account group model

The system SHALL persist account groups in `account_group (id, name UNIQUE NOT NULL, sort_order INTEGER, moze_id UNIQUE nullable)`. Deleting a group SHALL be refused while any account references it.

#### Scenario: Group with accounts cannot be deleted
- **GIVEN** group `信用卡` has 3 accounts
- **WHEN** `DELETE /api/accounting/account-groups/{id}` is called
- **THEN** the response SHALL be HTTP 409

### Requirement: Counterparty model

The system SHALL persist counterparties in `counterparty (id, name UNIQUE NOT NULL, moze_id UNIQUE nullable)`. `GET /api/accounting/counterparties` SHALL return each counterparty with `open_amounts`: one item per currency, each `−Σ amount` over that counterparty's `receivable` and `payable` entries in that currency (settling entries are `receivable` / `payable` entries too, so they are included). A positive value means others owe the owner; a negative value means the owner owes.

#### Scenario: Open amount per counterparty
- **GIVEN** a receivable of `-420 TWD` (代付 for Alan) and a collection of `+200 TWD` from Alan
- **WHEN** counterparties are listed
- **THEN** Alan's `open_amounts` SHALL be `[{currency: TWD, amount: 220}]`

#### Scenario: Currencies kept apart
- **GIVEN** Alan also has a payable of `+1000 JPY`
- **WHEN** counterparties are listed
- **THEN** Alan's `open_amounts` SHALL contain `TWD 220` and `JPY -1000` as separate items

### Requirement: Entry group model

The system SHALL persist `entry_group (id, kind enum split | reward_claim | installment, name, merchant, description, moze_id UNIQUE nullable, created_at)`. A group has no amount of its own; its total is the sum of its members' `amount` converted to the first member's currency only for display. Members may belong to different accounts and have different kinds. Deleting a group SHALL delete its members and their fee and discount children.

#### Scenario: Split with two accounts
- **WHEN** a split is created with members `午餐 -230` on account A and `代付 -180` (receivable, Alan) on account B
- **THEN** both members SHALL share one `group_id` of kind `split`
- **AND** A's balance SHALL change by `-230` and B's by `-180`

### Requirement: Reward rule model

The system SHALL persist reward rules in `reward_rule` with: `id`, `account_id` (FK, NOT NULL), `name`, `method` (enum `percent`, `fixed`), `rate` (NUMERIC(8,4), percent, nullable), `fixed_amount` (NUMERIC(20,4), nullable), `window` (enum, `statement_cycle` only in 2a), `posting` (enum `after_window`, `after_transaction`, `manual`), `delay_days` (SMALLINT), `post_month_offset` (SMALLINT 0–2) and `post_day` (SMALLINT 1–31), `txn_rounding` and `total_rounding` (enum `keep`, `round`, `floor`, `ceil`), `total_cap` (NUMERIC(20,4), nullable), `shared_cap_id` (UUID, nullable), `is_basic` (BOOLEAN), `reward_account_id` (FK), `reward_project_id` (FK, nullable), `starts_on` and `ends_on` (DATE), `is_enabled` (BOOLEAN), `description`, `sort_order`, `moze_id` (UNIQUE, nullable).

The system SHALL persist attachments in `entry_reward_rule (entry_id, rule_id)` with a composite primary key and `ON DELETE RESTRICT` on `rule_id`. In 2a rules are created only by import; `GET /api/accounting/accounts/{id}/reward-rules` SHALL list them. No reward is computed in 2a. Imports SHALL upsert rules by `moze_id` and SHALL never delete a rule that any entry references (see the backup import spec), so attachments on manual entries survive re-imports.

#### Scenario: Rules listed per account
- **GIVEN** an account with 3 imported rules, 2 enabled
- **WHEN** its rules are listed
- **THEN** all 3 SHALL be returned with `is_enabled` reflecting the backup

### Requirement: Preference model and endpoint

The system SHALL persist one `preference` row with `expense_income_colors` (enum `red_green`, `green_red`), `keypad_layout` (enum `calculator`, `phone`), `week_start` (SMALLINT 0–6, 0 = Sunday), `main_currency` (VARCHAR(8)), `hide_rewards_on_timeline` (BOOLEAN) and `abbreviate_totals` (BOOLEAN). `GET /api/accounting/preference` SHALL return it (creating defaults `red_green`, `calculator`, `0`, `TWD`, `false`, `true` if absent) and `PUT /api/accounting/preference` SHALL update it.

#### Scenario: Defaults created on first read
- **GIVEN** no preference row
- **WHEN** `GET /api/accounting/preference` is called
- **THEN** the defaults SHALL be returned and persisted

### Requirement: Entry write endpoints

The service SHALL expose:

- `POST /api/accounting/entries` creating one entry of kind `expense`, `income`, `receivable` or `payable` with: `account_id`, `kind`, `amount` (unsigned; the server applies the sign of the kind: negative for `expense` and `receivable`, positive for `income` and `payable`), optional `original_amount` + `original_currency` + `fx_rate` (see "Foreign-currency manual entries"), `entry_date`, `entry_time`, optional `posted_date`, `category_id`, `project_id`, `name`, `merchant`, `counterparty_id` (required for `receivable` / `payable`, forbidden otherwise), `description`, `tags`, `invoice_number`, `invoice_random`, optional `fee` and `discount` children (`{amount, name}`; fee stored negative, discount positive, same account, date, time and posting date, `parent_entry_id` set), optional `reward_rule_ids` (must belong to the account and be enabled on `entry_date`).
- `PUT /api/accounting/entries/{id}` replacing the editable fields and children of a manual entry. Children not present in the request SHALL be deleted; `kind` and `account_id` MAY change only for entries without a transfer group. Entries with `is_settlement = true` and entries of kind `refund` SHALL be refused (HTTP 422 naming `kind`); they change only by delete and re-settle or re-refund. An entry that other entries settle or refund SHALL refuse changes to `amount`, `account_id`, `kind` and `counterparty_id` (HTTP 422 naming the field). The server SHALL lock the target row (`SELECT … FOR UPDATE`) before these checks so a concurrent settlement or refund is either seen or waited for.
- `DELETE /api/accounting/entries/{id}` deleting the entry, its fee and discount children, and, for a transfer leg, the other leg. When the entry belongs to an entry group, the server SHALL lock the `entry_group` row before the entry (the same group → members order as the split endpoints), so a single delete and a group replacement never deadlock; deleting the last member deletes the group. Deleting a `reward` entry SHALL be refused in 2a (HTTP 409). Deleting an entry that is settled or refunded by other entries SHALL clear those links rather than delete them.
- `POST /api/accounting/balance-adjustments` with `account_id`, `target_balance`, `entry_date`, `entry_time`, `description`: creates a `balance_adjustment` entry whose `amount` equals `target_balance − current balance` at the time of the call. A zero delta SHALL be refused.

Every write SHALL set `source = 'manual'`, SHALL update `category.default_account_id` and `default_project_id` to the values used, and SHALL return the created or updated entry in the same shape as the detail endpoint. Amounts with more than 4 decimals SHALL be rejected.

#### Scenario: Expense with fee child
- **WHEN** a client posts an expense of `790` with a fee of `15` named `運費` on account A
- **THEN** an `expense` entry of `-790` and a `fee` child of `-15` SHALL be created with the same date, time and posting date
- **AND** A's balance SHALL change by `-805`

#### Scenario: Receivable requires a counterparty
- **WHEN** a client posts a `receivable` without `counterparty_id`
- **THEN** the response SHALL be HTTP 422 naming `counterparty_id`

#### Scenario: Balance adjustment stores the delta
- **GIVEN** account A's balance is `24806`
- **WHEN** a client posts a balance adjustment with `target_balance = 24798`
- **THEN** a `balance_adjustment` entry of `-8` SHALL be created and A's balance SHALL be `24798`

#### Scenario: Deleting a transfer leg deletes the pair
- **GIVEN** a transfer with legs on A and B
- **WHEN** either leg is deleted
- **THEN** both legs SHALL be deleted and both balances restored

#### Scenario: Category remembers the last account and project
- **WHEN** an expense in `飲食/午餐` is saved on account `玉山 UNI` with project `生活`
- **THEN** `飲食/午餐` SHALL have `default_account_id = 玉山 UNI` and `default_project_id = 生活`

### Requirement: Transfer endpoint

`POST /api/accounting/transfers` SHALL take `from_account_id`, `to_account_id` (distinct), `out_amount` (unsigned, in the from-account's currency), `in_amount` (unsigned, in the to-account's currency; defaults to `out_amount` when both currencies match and is required otherwise), `entry_date`, `entry_time`, optional `posted_date`, `category_id` (a `transfer_out` category), `name`, `merchant`, `project_id`, `description`, `tags`, optional `out_fee`, `out_discount`, `in_fee`, `in_discount` children, optional `reward_rule_ids` for the from-account. It SHALL create a `transfer_out` entry of `-out_amount` and a `transfer_in` entry of `+in_amount` sharing a new `transfer_group_id`, with fee and discount children on their respective legs. For cross-currency transfers each leg SHALL store the other leg's amount, carrying this leg's sign, as `original_amount` / `original_currency`, with `fx_rate = |amount| / |original_amount|` rounded half-up to 10 decimals and `fx_source = 'manual'`. The two amounts are the source of truth and the rate is derived; any check of `original_amount × fx_rate ≈ amount` SHALL use a tolerance of half a unit in the leg currency's smallest displayed unit (0.5 for TWD and JPY, 0.005 otherwise), never exact equality. `PUT /api/accounting/transfers/{transfer_group_id}` SHALL update both legs together.

#### Scenario: Same-currency transfer
- **WHEN** a client posts a transfer of `3000` from A to B (both TWD) with an out-fee of `15`
- **THEN** A SHALL change by `-3015` and B by `+3000`

#### Scenario: Cross-currency transfer
- **WHEN** a client posts `out_amount = 10000` (TWD) and `in_amount = 46200` (JPY)
- **THEN** the legs SHALL be `-10000 TWD` and `+46200 JPY` with the same group id
- **AND** the in-leg SHALL carry `original_amount = 10000`, `original_currency = TWD` and `fx_rate = 4.62`
- **AND** the out-leg SHALL carry `original_amount = -46200`, `original_currency = JPY` and `fx_rate = 0.2164502165` (`10000 / 46200`, 10 decimals)

### Requirement: Split endpoint

`POST /api/accounting/splits` SHALL take group fields (`name`, `merchant`, `description`) and `members`: a non-empty list of entry payloads as in "Entry write endpoints" (each with its own kind, account, category, amount, counterparty, fee and discount children, rule ids), plus shared `entry_date`, `entry_time`, `posted_date`, `project_id` and `tags` applied to every member unless a member overrides them. It SHALL create one `entry_group` of kind `split` and all members in one transaction. `PUT /api/accounting/splits/{group_id}` SHALL replace the members, after locking the `entry_group` row and then its existing members (`SELECT … FOR UPDATE`, always in that order, which `DELETE` SHALL follow too), so that two concurrent replacements serialise and the last writer's members are the only ones left; it SHALL be refused (HTTP 422 naming `members`) when any existing member has `is_settlement = true`, is a `refund`, or is settled or refunded by other entries, since replacing such members would lose their identity and direction. `DELETE` SHALL delete the group and members.

#### Scenario: Friend paid for lunch
- **WHEN** a split is posted with members `payable +100 (借入, Alan)` on account A and `expense -100 (早餐)` on account A
- **THEN** A's balance SHALL be unchanged and both entries SHALL share one group of kind `split`

### Requirement: Settlement endpoint

`POST /api/accounting/entries/{id}/settle` SHALL take `account_id` (the account receiving or paying; its currency MUST equal the target entry's currency, else HTTP 422), `amount` (unsigned, at most the open amount of the target entry), `entry_date`, `entry_time`, `description` and create an entry on that account with `settles_entry_id = {id}` and `is_settlement = true`: a `receivable` entry of `+amount` when the target is a `receivable` (收款), a `payable` entry of `-amount` when the target is a `payable` (還款), copying the target's `counterparty_id`. The target's open amount is `|amount + Σ amounts of entries settling it|`, in the target's currency. A request exceeding the open amount SHALL be refused. The target entry SHALL be locked (`SELECT … FOR UPDATE`) while the open amount is computed and the settlement inserted, so concurrent settlements cannot exceed it; refunds SHALL lock the original the same way. The detail of a `receivable` or `payable` SHALL report `open_amount` and `is_settled`.

#### Scenario: Partial collection
- **GIVEN** a receivable of `-420` for Alan on card C
- **WHEN** a settlement of `200` is posted to account W
- **THEN** W SHALL gain `+200`, the receivable's `open_amount` SHALL be `220` and `is_settled` false

#### Scenario: Over-settlement refused
- **GIVEN** the same receivable with `220` open
- **WHEN** a settlement of `300` is posted
- **THEN** the response SHALL be HTTP 422

#### Scenario: Settlement in another currency refused
- **GIVEN** a TWD receivable
- **WHEN** a settlement is posted to a JPY account
- **THEN** the response SHALL be HTTP 422 naming `currency`

### Requirement: Refund endpoint

`POST /api/accounting/entries/{id}/refund` SHALL take `account_id` (defaults to the original's account; its currency MUST equal the original's currency, else HTTP 422), `amount` (unsigned, at most the original's absolute amount minus prior refunds), `entry_date`, `entry_time`, `description` and create a `refund` entry of `+amount` with `refunds_entry_id = {id}`, the original's category, name and merchant. Refunding a group member SHALL be allowed; refunding a group itself SHALL be refused. The original's detail SHALL report `refunded_amount`.

#### Scenario: Partial refund to another account
- **GIVEN** an expense of `-1200` on card C
- **WHEN** a refund of `570` to wallet W is posted
- **THEN** W SHALL gain `+570`, the expense's `refunded_amount` SHALL be `570`, and the refund SHALL reference the expense

### Requirement: Settings endpoints

The service SHALL expose CRUD endpoints:

- `POST /api/accounting/accounts`, `PUT /api/accounting/accounts/{id}`, `DELETE /api/accounting/accounts/{id}` (delete allowed only when the account has no entries; otherwise HTTP 409 suggesting archive). `PUT` accepts every account column except `moze_id` and `settings_locally_edited`, refuses a `currency` change while entries exist, validates credit fields, `combined_account_id` (must be a different, non-archived account; no cycles) and `credit_sharing_id`, and sets `settings_locally_edited = true`. `POST /api/accounting/accounts/{id}/reset-settings-flag` clears the flag.
- `GET/POST/PUT/DELETE /api/accounting/account-groups`, with `PUT /api/accounting/account-groups/order` taking an ordered id list.
- `GET /api/accounting/categories?kind=` returning the two-level tree with icons and colours; `POST`, `PUT` (name, icon, colour, parent, hidden, order), `DELETE` (allowed only when unused; otherwise HTTP 409), `PUT /api/accounting/categories/order`.
- `GET/POST/PUT/DELETE /api/accounting/projects` (delete only when unused).
- `GET/POST/PUT/DELETE /api/accounting/counterparties` (delete only when unused); `PUT` renames.

#### Scenario: Archive instead of delete
- **GIVEN** an account with 12 entries
- **WHEN** `DELETE /api/accounting/accounts/{id}` is called
- **THEN** the response SHALL be HTTP 409 with a message pointing to `is_archived`

#### Scenario: Master account cycle refused
- **GIVEN** account X has `combined_account_id = Y`
- **WHEN** Y is updated with `combined_account_id = X`
- **THEN** the response SHALL be HTTP 422

### Requirement: Imported rows are read-only until cutover

While the configuration flag `ACCOUNTING_IMPORT_LOCKED` is false, any `PUT`, `DELETE`, settle or refund request targeting an entry or entry group whose `source` is `moze_import` or `moze_backup`, or whose `moze_id` is set, SHALL be refused with HTTP 409 and the message `locked_until_cutover`. Account **settings** writes (`PUT /api/accounting/accounts/{id}`) SHALL be allowed on imported accounts at any time; they set `settings_locally_edited`, and a re-import then leaves that account's settings alone (see the backup import spec, "Accounts, groups, categories…"). When the flag is true, every row SHALL be editable and imports SHALL be refused.

Manual rows (`source = 'manual'`) SHALL be editable at all times and SHALL never be deleted by an import.

#### Scenario: Editing an imported entry before cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = false` and an entry with `source = 'moze_backup'`
- **WHEN** `PUT /api/accounting/entries/{id}` is called
- **THEN** the response SHALL be HTTP 409 with `locked_until_cutover`

#### Scenario: Manual entry survives a re-import
- **GIVEN** a manual expense and a later backup import
- **WHEN** the import finishes
- **THEN** the manual expense SHALL still exist unchanged

#### Scenario: Everything editable after cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = true`
- **WHEN** an imported entry is updated
- **THEN** the update SHALL succeed

### Requirement: Foreign-currency manual entries

When a write request carries `original_currency` different from the account currency, the server SHALL store `original_amount` (signed like `amount`) and `original_currency`, and SHALL compute `amount = original_amount × fx_rate` rounded half-up to 4 decimals, where `fx_rate` is: the request's `fx_rate` (then `fx_source = 'manual'`); else, when the request gives `amount` explicitly, `amount / original_amount` (`fx_source = 'manual'`); else the cached or fetched daily rate for `entry_date` from the `fx_rate` cache (`fx_source = 'fx_api'`). `GET /api/accounting/fx-rate?date=&base=&quote=` SHALL return the cached or fetched rate so the form can propose it. When the account has `fx_fee_pct` set and the request does not include a fee child named `國外交易手續費`, the response SHALL include `proposed_fee` = `|amount| × fx_fee_pct / 100` rounded per `fx_fee_rounding`, for the client to add; the server SHALL NOT add it silently.

#### Scenario: Converted amount overridden from the card statement
- **GIVEN** a TWD card and a request with `original_amount = 5390`, `original_currency = JPY`, `amount = 1166`
- **WHEN** the expense is created
- **THEN** it SHALL store `amount = -1166`, `original_amount = -5390`, `fx_rate = 0.2163` (±0.0001) and `fx_source = 'manual'`

#### Scenario: Rate proposed from the cache
- **GIVEN** the JPY→TWD rate for 2026-10-02 is cached as `0.2163`
- **WHEN** `GET /api/accounting/fx-rate?date=2026-10-02&base=JPY&quote=TWD` is called
- **THEN** it SHALL return `0.2163` without a network request

