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

An uploaded logo image SHALL live in a side table `account_logo_image` (`account_id` PK and FK with `ON DELETE CASCADE`, `png`, `sha256`, `source_media_type`, `source_bytes`, `created_at`, `updated_at`), never as a column of `account`.

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

#### Scenario: Uploaded logo cascades with the account
- **GIVEN** an account with an uploaded logo image
- **WHEN** the account is deleted
- **THEN** its `account_logo_image` row SHALL be removed with it

### Requirement: Read-only ledger endpoints

The service SHALL expose these read endpoints:

- `GET /api/accounting/accounts?as_of=YYYY-MM-DD` (default today; `balance`, `balance_main` and `available_credit` count entries with `posted_date ≤ as_of`): every account with `id`, `name`, `currency`, `opening_balance`, `balance`, `balance_main` (the balance converted to the preference's main currency with the latest cached rate; equal to `balance` for main-currency accounts), `entry_count`, `group_id`, `group_name`, `icon`, `logo`, `logo_hash`, `color`, `is_archived`, `include_in_total`, `is_credit`, `closing_day`, `due_rule`, `due_value`, `credit_limit`, `combined_account_id`, `credit_sharing_id`, `auto_pay_account_id`, `fx_fee_pct`, `sort_order` and `moze_id`, sorted by group order, then `sort_order`, then name. `?include_archived=false` (default) SHALL omit archived accounts.
- `GET /api/accounting/accounts/{id}?as_of=`: one account with the same fields (including `logo_hash`) plus `note` and `reward_rules` (the account's rules, read-only in 2a).
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

#### Scenario: logo_hash is the sha256 prefix or null
- **GIVEN** one account with an uploaded logo image and one without
- **WHEN** a client reads both accounts
- **THEN** the first SHALL carry `logo_hash` equal to the first 16 hex characters of the stored image's sha256 and the second SHALL carry `logo_hash: null`

## ADDED Requirements

### Requirement: Account logo upload

`PUT /api/accounting/accounts/{id}/logo` (scope `write`) SHALL accept the raw image as the body; `Content-Type` is recorded, not trusted. A `Content-Length` above 5 MiB SHALL be refused before reading, and the body SHALL be streamed against a 5 MiB budget (413 `logo_too_large`). The stored image SHALL be a 256×256 RGBA PNG; the response SHALL be the account detail with `logo_hash`.

#### Scenario: Valid PNG is normalised
- **GIVEN** an account and a valid 600×300 PNG
- **WHEN** the client PUTs it to `/api/accounting/accounts/{id}/logo`
- **THEN** the response SHALL be 200 with `logo_hash` set, and the stored image SHALL be a 256×256 RGBA PNG with the picture centred on transparent padding

#### Scenario: Oversized body without Content-Length
- **WHEN** a client streams a body larger than 5 MiB with no `Content-Length`
- **THEN** the response SHALL be 413 `logo_too_large` in the shared `{code, message, trace_id}` envelope and reading SHALL stop at the budget

#### Scenario: Declared length too large
- **WHEN** a request declares a `Content-Length` above 5 MiB
- **THEN** the response SHALL be 413 `logo_too_large` before the body is read

### Requirement: Account logo upload validation

The upload SHALL be validated in this order, each failure answering 422: empty body `logo_empty`; sniffed format not PNG, JPEG or WEBP `logo_format`; a side outside 16–8192 px or over 25 million pixels `logo_dimensions`; undecodable `logo_unreadable`. A rejected upload SHALL leave the stored image unchanged.

#### Scenario: Pixel budget exceeded
- **WHEN** a client uploads a valid 6000×6000 PNG
- **THEN** the response SHALL be 422 `logo_dimensions`

#### Scenario: SVG text is not an image
- **WHEN** a client uploads SVG markup, even with `Content-Type: image/png`
- **THEN** the response SHALL be 422 `logo_unreadable`

#### Scenario: Empty body
- **WHEN** a client PUTs an empty body
- **THEN** the response SHALL be 422 `logo_empty`

#### Scenario: Format not allowed
- **WHEN** a client uploads a valid GIF
- **THEN** the response SHALL be 422 `logo_format`

#### Scenario: Rejected upload keeps the old image
- **GIVEN** an account with an uploaded logo
- **WHEN** a later upload fails validation
- **THEN** the account's `logo_hash` SHALL be unchanged

### Requirement: Account logo image endpoint

`GET /api/accounting/accounts/{id}/logo.png` (scope `read`) SHALL return the stored image as `image/png` with `ETag: "<sha256>"`, `X-Content-Type-Options: nosniff` and `Content-Disposition: inline; filename="logo.png"`. It SHALL send `Cache-Control: private, max-age=31536000, immutable` only when the query `v` equals the account's current `logo_hash`, otherwise `no-store`. A matching `If-None-Match` SHALL answer 304. An unknown account, or an account without an image, SHALL be 404.

#### Scenario: Matching v is cached immutably
- **GIVEN** an account with an uploaded logo
- **WHEN** a client requests `logo.png?v=<current logo_hash>`
- **THEN** the response SHALL be 200 with `Cache-Control: private, max-age=31536000, immutable`

#### Scenario: Stale v is not cached
- **GIVEN** an account whose logo was replaced
- **WHEN** a client requests `logo.png?v=<old hash>`
- **THEN** the response SHALL carry the current bytes with `Cache-Control: no-store`

#### Scenario: Conditional request
- **WHEN** a client repeats the request with `If-None-Match` equal to the current ETag
- **THEN** the response SHALL be 304

#### Scenario: No image
- **WHEN** a client requests `logo.png` for an account without an uploaded image
- **THEN** the response SHALL be 404

### Requirement: Account logo removal

`DELETE /api/accounting/accounts/{id}/logo` (scope `write`) SHALL remove the account's uploaded image and answer 204, including when none exists. An unknown account SHALL be 404. The `logo` slug SHALL be unaffected.

#### Scenario: Delete twice
- **GIVEN** an account with an uploaded logo
- **WHEN** a client sends `DELETE` twice
- **THEN** both responses SHALL be 204 and `logo_hash` SHALL be null afterwards

#### Scenario: Unknown account
- **WHEN** a client sends `DELETE` for an account id that does not exist
- **THEN** the response SHALL be 404
