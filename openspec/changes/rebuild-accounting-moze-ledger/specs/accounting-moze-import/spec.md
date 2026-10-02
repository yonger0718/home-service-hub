## ADDED Requirements

### Requirement: CSV format acceptance

The importer SHALL accept a UTF-8 file, with or without a BOM, whose header row is exactly these 16 columns:

`帳戶,幣種,記錄類型,主類別,子類別,金額,手續費,折扣,名稱,商家,日期,時間,專案,描述,標籤,對象`

It SHALL reject any other header, naming the missing or unexpected columns, and SHALL write nothing.

Field rules:

- `日期` SHALL be parsed as `YYYY/MM/DD`.
- `時間` SHALL be parsed as `HH:MM`, or treated as empty.
- `金額`, `手續費` and `折扣` SHALL be parsed as decimals; an empty value counts as 0.
- `標籤` SHALL be split on `;` with any leading `#` stripped.

#### Scenario: Changed export format is rejected
- **GIVEN** a CSV whose header lacks `對象`
- **WHEN** an import is attempted
- **THEN** the import SHALL fail with an error listing `對象` as missing
- **AND** no ledger data (accounts, entries, categories, projects) SHALL be deleted or inserted

### Requirement: Record type mapping

The importer SHALL map `記錄類型` to a ledger `kind` as follows:

| 記錄類型 | kind |
|---|---|
| 支出 | `expense` |
| 收入 | `income` |
| 轉出 | `transfer_out` |
| 轉入 | `transfer_in` |
| 應收款項 | `receivable` |
| 應付款項 | `payable` |
| 餘額調整 | `balance_adjustment` |
| 手續費 | `fee` |
| 折扣 | `discount` |
| 紅利回饋 | `reward` |
| 利息 | `interest` |
| 退款 | `refund` |

`初始金額` rows SHALL set the account's `opening_balance` and SHALL NOT create an entry.

Every account named in the file SHALL have **exactly one** `初始金額` row. If any account has none, or more than one, the import SHALL fail, naming the account and the offending row numbers, and SHALL leave the existing ledger unchanged.

Any other `記錄類型` value SHALL fail the import, naming the value and its row number.

Each entry's `amount` SHALL be the `金額` value with its sign preserved.

#### Scenario: Initial amount sets opening balance
- **GIVEN** the row `去日本的錢,JPY,初始金額,,,180000,…`
- **WHEN** imported
- **THEN** account `去日本的錢` SHALL have `currency = JPY` and `opening_balance = 180000`
- **AND** no entry SHALL be created for that row

#### Scenario: Missing opening balance row fails the import
- **GIVEN** account `Line Bank` has entries but no `初始金額` row
- **WHEN** an import is attempted
- **THEN** the import SHALL fail naming `Line Bank`, and the existing ledger SHALL be unchanged

#### Scenario: Duplicate opening balance rows fail the import
- **GIVEN** account `錢包` has two `初始金額` rows, `2000` and `1500`
- **WHEN** an import is attempted
- **THEN** the import SHALL fail naming `錢包` and both row numbers, and the existing ledger SHALL be unchanged

#### Scenario: Unknown record type fails the whole import
- **GIVEN** a row whose `記錄類型` is `測試`
- **WHEN** an import is attempted
- **THEN** the import SHALL fail naming `測試` and its row number, and no ledger data SHALL be written

#### Scenario: Refund is imported as its own kind
- **GIVEN** a row whose `記錄類型` is `退款` with `金額 = 350`
- **WHEN** imported
- **THEN** a `refund` entry of `+350` SHALL exist on that row's account

### Requirement: Fee and discount columns become child entries

For each row with a non-zero `手續費`, the importer SHALL create a `fee` entry with these properties:

- `amount` equal to the `手續費` value
- the same account, date and time as the parent row
- `parent_entry_id` set to the parent entry
- the fixed `fee` category

A non-zero `折扣` SHALL likewise create a `discount` child entry.

The parent entry's `amount` SHALL remain the `金額` value.

The importer SHALL assign `seq` in file row order. A parent entry's children SHALL receive the `seq` values immediately after their parent: the `fee` child first, then the `discount` child.

#### Scenario: Expense with a card fee
- **GIVEN** a 支出 row with `金額 = -266` and `手續費 = -3`
- **WHEN** imported
- **THEN** there SHALL be an `expense` entry of `-266` and a `fee` entry of `-3` whose `parent_entry_id` is the expense
- **AND** the account balance SHALL decrease by `269`

### Requirement: Automatic creation of accounts, categories and projects

The importer SHALL create any account, category or project referenced in the file that does not already exist.

An account's `currency` SHALL be the `幣種` of its single `初始金額` row. Rows on that account MAY carry a different `幣種`; they are converted as described in "Foreign-currency rows".

Rows of every non-system kind SHALL use a category of that kind named after `主類別`, with a child category `子類別` when `子類別` is non-empty and differs from `主類別`. System kinds (`fee`, `discount`, `reward`, `interest`, `balance_adjustment`) SHALL use their fixed category.

#### Scenario: Receivable sub-categories are preserved
- **GIVEN** 應收款項 rows with `主類別 = 應收款項` and `子類別` values `代付` and `報帳`
- **WHEN** imported
- **THEN** receivable categories `應收款項/代付` and `應收款項/報帳` SHALL exist and the entries SHALL reference them

#### Scenario: Account currency comes from its opening row
- **GIVEN** account `華航卡` has a `初始金額` row in `TWD` and some expense rows in `JPY`
- **WHEN** imported
- **THEN** account `華航卡` SHALL have `currency = TWD`

### Requirement: Foreign-currency rows

When a row's `幣種` differs from its account's currency, the importer SHALL convert it to the account currency:

- `original_amount` = `金額`, and `original_currency` = the row's `幣種`.
- `amount` = `金額 × rate`, rounded half-up to 4 decimal places, where `rate` is the daily rate from the row currency to the account currency on the row's `日期`.
- `fx_rate` = that rate, and `fx_source = 'fx_api'`.
- Fee and discount children of the row SHALL be converted with the same rate, and SHALL keep their own `original_amount`.
- Rows in the account currency SHALL leave `original_amount`, `original_currency`, `fx_rate` and `fx_source` NULL.

**Rates.** Rates SHALL be read from the accounting service's own `fx_rate` cache table, with columns `date`, `base`, `quote`, `rate` and `source`, and primary key `(date, base, quote)`.

- A missing `(date, base, quote)` SHALL be fetched from the daily historical FX source used by stock-portfolio-service: jsDelivr `@fawazahmed0/currency-api@{YYYY-MM-DD}`, with `{YYYY-MM-DD}.currency-api.pages.dev` as fallback. The fetched rate SHALL be stored in the cache.
- Rate fetching SHALL happen before the ledger transaction starts.
- If a rate cannot be obtained from either source, the import SHALL fail naming the currency pair and date, and SHALL leave the existing ledger unchanged.
- The cache SHALL NOT be cleared by imports.

**Pairing.** Transfer pairing SHALL compare the rows' own `幣種` and `金額`, not the converted amounts.

**Report.** The import report SHALL list, per account, the number of converted entries and the sum of their converted amounts, so the owner can compare those accounts with MOZE.

#### Scenario: JPY expense on a TWD card is converted
- **GIVEN** a TWD account and a `支出` row on it with `幣種 = JPY`, `金額 = -1800` dated `2026/07/10`
- **AND** the JPY→TWD rate for 2026-07-10 is `0.2` (cached or fetched)
- **WHEN** imported
- **THEN** the entry SHALL have `amount = -360.0000`, `currency = TWD`, `original_amount = -1800`, `original_currency = JPY`, `fx_rate = 0.2` and `fx_source = 'fx_api'`
- **AND** the account balance SHALL decrease by `360`

#### Scenario: Missing rate fails the import without ledger changes
- **GIVEN** a foreign-currency row dated `2025/06/01`, no cached rate, and both FX sources unreachable
- **WHEN** an import is attempted
- **THEN** the import SHALL fail naming `JPY→TWD` and `2025-06-01`
- **AND** the existing ledger SHALL be unchanged

#### Scenario: Cached rates are reused
- **GIVEN** the cache already holds JPY→TWD for 2026-07-10
- **WHEN** a second import needs that rate
- **THEN** no network request SHALL be made for it

### Requirement: Transfer pairing

The importer SHALL pair each `轉出` row with one `轉入` row from the same file. Row adjacency SHALL NOT be sufficient on its own; it is only a tie-breaker.

It SHALL apply these passes in order, and a row paired in one pass SHALL NOT be reconsidered in a later pass:

1. **Adjacent and opposite.** The row immediately after the `轉出` is an unpaired `轉入` with the same `日期`, `時間` and `幣種`, and its `金額` is the exact negation: pair them.
2. **Same currency, opposite amount, unique both ways.** Candidates are unpaired `轉入` rows with the same `日期`, `時間` and `幣種` whose `金額` is the exact negation.
   - Pair only when this `轉出` has exactly one candidate **and** that candidate has exactly one unpaired `轉出` that it could match.
   - Otherwise, leave every leg involved unpaired.
3. **Cross currency.** Candidates are unpaired `轉入` rows with the same `日期` and `時間` and a different `幣種`. Pair only when this `轉出` has exactly one candidate and that candidate has exactly one unpaired `轉出` counterpart of a different currency at the same date and time.

Same-currency legs whose amounts are not exact negations SHALL NOT be paired.

The two legs of a paired transfer SHALL share a new `transfer_group_id`.

A leg still unpaired after all three passes SHALL be imported with `transfer_group_id = NULL` and `needs_review = true`.

#### Scenario: Interleaved transfers in the same minute are paired by amount
- **GIVEN** these rows, in this order, all at the same date and time, all TWD:
  - `轉入 +30000` (account A)
  - `轉出 -30000` (account B)
  - `轉入 +7500` (account C)
  - `轉出 -7500` (account A)
- **WHEN** imported
- **THEN** `-30000` SHALL be paired with `+30000`, and `-7500` with `+7500`
- **AND** `-30000` SHALL NOT be paired with the adjacent `+7500`

#### Scenario: Non-adjacent transfer is paired by amount
- **GIVEN** a `轉出 -22000` on account A and, three rows later, a `轉入 +22000` on account B with the same date, time and currency
- **WHEN** imported
- **THEN** both legs SHALL share one `transfer_group_id`

#### Scenario: Cross-currency transfer is paired when unique
- **GIVEN** a `轉出 -10000 TWD` and a `轉入 +46000 JPY` at the same date and time, with no other unpaired legs at that time
- **WHEN** imported
- **THEN** both legs SHALL share one `transfer_group_id`

#### Scenario: Ambiguous candidates leave legs unpaired
- **GIVEN** two `轉出 -500` rows and two `轉入 +500` rows, all TWD at the same date and time, with no `轉入` immediately after either `轉出`
- **WHEN** imported
- **THEN** the importer SHALL NOT guess between them
- **AND** all four legs SHALL be imported with `needs_review = true`
- **AND** every account balance SHALL still reflect all four amounts

#### Scenario: One-sided uniqueness is not enough
- **GIVEN** two `轉出 -500` rows and one `轉入 +500`, all TWD at the same date and time, with neither `轉出` immediately followed by the `轉入`
- **WHEN** imported
- **THEN** none of the three legs SHALL be paired
- **AND** all three SHALL be imported with `needs_review = true`

#### Scenario: Real export pairs completely
- **WHEN** `MOZE_20261001_170037.csv` is imported
- **THEN** all 509 transfers SHALL be paired: 316 in pass 1, 176 in pass 2 and 17 in pass 3
- **AND** no transfer leg SHALL be flagged `needs_review`

### Requirement: Transactional full replace

Every import entry point (CLI and REST) SHALL first acquire one shared PostgreSQL advisory lock (a fixed key), held until the import finishes.

If the lock is already held, the second import SHALL be refused immediately:

- REST: HTTP 409;
- CLI: non-zero exit with an "import already running" message.

A refused import SHALL write nothing, not even an `import_run` row. Dry runs SHALL take the same lock.

An import SHALL write the ledger in a single database transaction (the *ledger transaction*) that performs these steps in order:

1. Apply any account renames supplied with the import (see "Account renames and disappearing accounts").
2. Delete every `ledger_entry` with `source = 'moze_import'`.
3. For every account named in the file:
   - if it exists, update its `opening_balance` and `currency` and set `is_archived = false`;
   - otherwise, create it.
4. For every account **not** named in the file that has no remaining entries: set `opening_balance = 0` and `is_archived = true`.
5. Insert all entries parsed from the file.
6. Delete categories and projects that are no longer used. A category counts as used if any remaining entry references it **or any of its descendants**. A main category is therefore kept while any of its sub-categories is used.

Accounts SHALL NOT be deleted by an import. Entries whose `source` is not `moze_import` SHALL NOT be touched. Any error SHALL roll back the entire ledger transaction.

When the configuration flag `ACCOUNTING_IMPORT_LOCKED = true` is set, the importer SHALL refuse to run.

#### Scenario: Parent category kept while only its sub-category is used
- **GIVEN** every expense in category `飲食` references the sub-category `飲食/午餐`, and none references `飲食` directly
- **WHEN** the cleanup step runs
- **THEN** both `飲食` and `飲食/午餐` SHALL remain

#### Scenario: Unused branch is removed
- **GIVEN** a previous import created `娛樂/電影`, and the new file has no entry in `娛樂` or any of its sub-categories
- **WHEN** imported
- **THEN** both `娛樂/電影` and `娛樂` SHALL be deleted

#### Scenario: Re-import reflects edits made in MOZE
- **GIVEN** a previous import contained an expense of `-120` that was later changed to `-150` in MOZE
- **WHEN** the new export is imported
- **THEN** the ledger SHALL contain the `-150` expense and not the `-120` one

#### Scenario: Failure leaves the previous import intact
- **GIVEN** a successful earlier import
- **WHEN** a new import fails while parsing row 4,000
- **THEN** every entry from the earlier import SHALL still be present and unchanged

#### Scenario: Concurrent imports are serialised by refusal
- **GIVEN** a CLI import is running
- **WHEN** a REST import is requested before it finishes
- **THEN** the REST request SHALL receive HTTP 409
- **AND** the ledger SHALL afterwards contain exactly the data of the CLI import's file

#### Scenario: Import is locked after cutover
- **GIVEN** `ACCOUNTING_IMPORT_LOCKED = true`
- **WHEN** an import is requested by any path (CLI or REST)
- **THEN** it SHALL be refused with a message that import is locked, and nothing SHALL change

### Requirement: Account renames and disappearing accounts

The importer SHALL handle account renames and accounts that disappear from the export. The MOZE export carries no account identifier, so a rename in MOZE looks like one account disappearing and another appearing.

An import MAY carry rename mappings `old_name → new_name`: CLI `--rename OLD=NEW` (repeatable), or REST field `renames`. A rename SHALL update the existing account's `name` before entries are written, keeping its `id` and any settings attached to it.

A rename SHALL fail the import if:

- `old_name` does not exist; or
- `new_name` already exists as a different account.

An account that is not named in the file and has no remaining entries SHALL be archived with `opening_balance = 0` (step 4 of the full replace). It SHALL be listed in the report under `accounts_archived`.

The dry-run report SHALL list accounts that would be archived and accounts that would be created, so the owner can detect a rename and re-run with a mapping.

#### Scenario: Rename with mapping keeps one account
- **GIVEN** account `A` exists with `opening_balance = 1000` and one imported entry of `-200`
- **AND** the new export names the account `B` (opening balance `1000`, entry `-200`)
- **WHEN** imported with `--rename A=B`
- **THEN** exactly one account SHALL exist for it, named `B`, with the same `id` as before and balance `800`

#### Scenario: Rename without mapping leaves no stale balance
- **GIVEN** the same situation but no rename mapping is supplied
- **WHEN** imported
- **THEN** account `B` SHALL be created with balance `800`
- **AND** account `A` SHALL be archived with `opening_balance = 0` and balance `0`
- **AND** the report SHALL list `A` under `accounts_archived` and `B` under `accounts_created`

#### Scenario: Dry run exposes a likely rename
- **WHEN** a dry run finds one account that would be archived and one that would be created, with the same currency
- **THEN** the dry-run report SHALL list both, so the owner can supply `--rename`

### Requirement: Import report

Every non-dry-run import attempt SHALL record an `import_run` row. Dry runs SHALL NOT record one.

The `import_run` row SHALL be written **outside** the ledger transaction:

- It is inserted with `status = 'running'` in its own committed transaction, after the advisory lock is acquired and before the ledger transaction starts.
- On success, it is set to `succeeded` with its summary **inside the ledger transaction**, so the new ledger and its success report commit together.
- On failure, after the ledger transaction rolls back, it is set to `failed` with the error in a separate transaction.
- Before creating its own row, a non-dry-run import holding the lock SHALL mark any `import_run` rows still `running` as `failed` with the error `interrupted`. These rows come from processes that died mid-import; their ledger transactions never committed.

A failed import therefore leaves no ledger changes but keeps its `failed` report.

The `import_run` row SHALL have these fields:

- `id`
- `started_at` and `finished_at`
- `file_name` and `file_sha256`
- `status` (`running`, `succeeded` or `failed`)
- `row_count`
- a `summary` JSON with:
  - counts per kind
  - `accounts_created`, `accounts_archived` and `accounts_renamed`
  - per-account opening balance, entry count and final balance
  - unpaired transfer legs with their row numbers
  - the error message on failure

#### Scenario: Interrupted import is reported, not left running
- **GIVEN** an import process was killed during its ledger transaction, leaving its `import_run` as `running`
- **WHEN** the next import starts
- **THEN** the stale run SHALL be marked `failed` with error `interrupted`
- **AND** the ledger SHALL show no trace of the killed import

#### Scenario: Failed import keeps its report
- **GIVEN** a successful earlier import
- **WHEN** a new import fails while parsing row 4,000
- **THEN** the ledger SHALL be unchanged
- **AND** an `import_run` row SHALL exist with `status = 'failed'` and the error naming row 4,000
- **AND** `GET /api/accounting/imports/latest` SHALL return that failed run

The report SHALL be returned by the import call and by `GET /api/accounting/imports/latest`.

#### Scenario: Owner verifies balances from the report
- **WHEN** an import succeeds
- **THEN** the report SHALL list every account with its final balance in its own currency
- **AND** SHALL list every unpaired transfer leg, giving its account, date, time, amount and source row number

### Requirement: Import entry points

The importer SHALL be invocable in two ways:

- **CLI**: `python -m app.services.moze_import_service <path> [--dry-run] [--rename OLD=NEW ...]`. With `--dry-run`, the CLI parses, pairs and prints the report without writing.
- **REST**: `POST /api/accounting/imports/moze` with a multipart upload and an optional `renames` field (JSON object `{old: new}`). Optional query `dry_run=true`. Maximum file size 20 MB.

#### Scenario: Dry run writes nothing
- **WHEN** the CLI runs with `--dry-run`
- **THEN** it SHALL print the full report
- **AND** the database SHALL be unchanged, including no `import_run` row

#### Scenario: Real export imports within limits
- **WHEN** `MOZE_20261001_170037.csv` (6,976 rows) is imported against a fresh database
- **THEN** the import SHALL succeed in under 30 seconds
- **AND** the report SHALL show 61 accounts and entry counts per kind that match the file, with fee and discount children counted separately
- **AND** exactly 130 entries across 9 accounts SHALL carry `fx_source = 'fx_api'`
