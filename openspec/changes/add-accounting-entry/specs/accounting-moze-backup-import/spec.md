## ADDED Requirements

### Requirement: Backup archive acceptance

The importer SHALL accept a MOZE backup zip (`MOZE_4.0_*.zip` or any name) that contains a file `moze.realm` and a file `info`. It SHALL reject an archive without `moze.realm`, an archive over 200 MB, or a `moze.realm` that Realm cannot open, naming the reason. Receipt images under `re/` SHALL be ignored. The archive's export date SHALL be the `moze.realm` entry's modification time inside the zip, and SHALL be recorded in the `import_run`.

#### Scenario: Zip without a database
- **WHEN** a zip containing only images is imported
- **THEN** the import SHALL fail with a message naming `moze.realm` and SHALL write no `import_run` row

### Requirement: Realm to JSON conversion

A Node tool at `tools/moze-realm-export/` (`realm` pinned to an exact 12.x version, Node ≥ 20) SHALL convert the archive to one JSON document: `node tools/moze-realm-export/index.js <zip> --out <json> [--work <dir>]`. It SHALL copy `moze.realm` into the work directory before opening it (Realm upgrades the file format in place), SHALL open the copy without a schema, SHALL export only rows with `isDeleted == false` of the classes `AHAccount`, `AHAccountGroup`, `AHCategory`, `AHClassification`, `AHProject`, `AHTarget`, `AHRecord`, `AHTransfer`, `AHPackage`, `AHBonusReward`, `AHBonusRewardSharing`, `AHCreditSharing`, `AHCurrencyConversion`, `AHCurrency` (favourites and custom only), `AHPeriod`, `AHInstallment`, `AHPreference`, `AHAppConfig`, with object links flattened to the linked row's primary key, lists of links to arrays of keys, dates to ISO-8601 strings in `Asia/Taipei`, and SHALL delete the work copy afterwards. It SHALL exit 0 on success and non-zero with a message on failure, and SHALL terminate the process explicitly after closing the Realm.

The Python importer SHALL run the tool as a subprocess, located by the `MOZE_REALM_EXPORTER` setting (default `node <repo>/tools/moze-realm-export/index.js`), with a 10-minute timeout, and SHALL treat a non-zero exit as an import failure before taking the advisory lock.

#### Scenario: Conversion of the real backup
- **WHEN** the tool runs on `MOZE_4.0.zip`
- **THEN** it SHALL exit 0 within 3 minutes and the JSON SHALL contain 7,553 `AHRecord`, 72 `AHAccount`, 544 `AHTransfer` and 100 `AHBonusReward` rows

#### Scenario: Converter missing
- **GIVEN** `MOZE_REALM_EXPORTER` points to a non-existent file
- **WHEN** an import is requested
- **THEN** it SHALL fail naming the path, and no lock, `import_run` row or ledger change SHALL occur

### Requirement: Record mapping

Each `AHRecord` SHALL map to ledger entries by its `type`:

| `type` | kind | `amount` |
|---|---|---|
| 0 | `expense` | `price` |
| 1 | `income` | `price` |
| 2 | `transfer_out` when `isTransferIn` is false, else `transfer_in` | `price` |
| 3 | `receivable` | `price` |
| 5 | `receivable` (a collection; `settles_entry_id` per "Links") | `price` |
| 4 | `payable` | `price` |
| 6 | `payable` (a repayment) | `price` |
| 7 | `balance_adjustment` | `price` |
| 12, 16 | `fee` | `price` |
| 13 | `discount` | `price` |
| 14 | `reward` | `price` |
| 15 | `interest` | `price` |

Any other `type` SHALL fail the import naming the type and the row's identifier.

Fields: `name` → `name` (NULL when empty); `store` → `merchant`; `desc` → `description`; `tags` → `tags` (split on the delimiter determined in implementation, trimmed, empty removed); `date` → `entry_date` and `entry_time`; `chargeDate` → `posted_date`; `invoiceNumber` → `invoice_number`; `classification` → `category_id`; `project` → `project_id`; `target` → `counterparty_id`; `account` → `account_id`; `identifier` → `moze_id`; `source = 'moze_backup'`.

`fee` ≠ 0 SHALL become a `fee` child (`amount = fee`, `name = feeName` or `手續費`) and `bonus` ≠ 0 a `discount` child (`amount = bonus`, `name = bonusName` or `折扣`), with `parent_entry_id` set and the parent's date, time and posting date. The parent's `amount` is `price` alone, so the row's balance effect equals `total`.

Rows whose `entry_date` is after the archive's export date SHALL be skipped and counted per type in the report. Rows with `isEnabled == false` SHALL be imported with `needs_review = true`.

#### Scenario: Expense with fee
- **GIVEN** an `AHRecord` with `type = 0`, `price = -266`, `fee = -3`, `total = -269`
- **WHEN** imported
- **THEN** an `expense` of `-266` and a `fee` child of `-3` SHALL be created and the account balance SHALL change by `-269`

#### Scenario: Future installment skipped
- **GIVEN** the archive exported on 2026-10-01 and a `type = 6` row dated 2026-11-09
- **WHEN** imported
- **THEN** no entry SHALL be created for it and the report's `skipped_future` SHALL count it under type 6

### Requirement: Foreign-currency records

When a record's `currency` differs from its account's currency, the importer SHALL convert it to the account currency as follows:

- if the account currency is the main currency (`AHPreference.mainCurrency`, TWD) and the record has a `currencyConversion`, then `amount = price × currencyConversion.exchangeRate` rounded half-up to 4 decimals, `original_amount = price`, `original_currency = record currency`, `fx_rate = exchangeRate`, `fx_source = 'moze_backup'`;
- otherwise the rate SHALL come from the `fx_rate` cache for `entry_date` (fetched as in phase 1) with `fx_source = 'fx_api'`.

Fee and discount children of such a record SHALL be converted with the same rate. Each `moze_backup` conversion SHALL be sanity-checked against the cached daily rate: if the two differ by more than 25 % the import SHALL fail naming the record's `moze_id`, unless `--allow-fx-outliers` is given.

#### Scenario: JPY charge on a TWD card uses MOZE's amount
- **GIVEN** a TWD card, a `type = 0` record with `currency = JPY`, `price = -5390` and `currencyConversion.exchangeRate = 0.2163`
- **WHEN** imported
- **THEN** the entry SHALL have `amount = -1165.8570`, `original_amount = -5390`, `original_currency = JPY`, `fx_rate = 0.2163` and `fx_source = 'moze_backup'`

#### Scenario: JPY record on a JPY account is not converted
- **GIVEN** a JPY account and a record with `currency = JPY` that carries a `currencyConversion` to TWD
- **WHEN** imported
- **THEN** `amount = price`, and `original_amount`, `fx_rate` and `fx_source` SHALL be NULL

### Requirement: Links

- **Transfers.** Each `AHTransfer` SHALL give its `outRecord` and `inRecord` entries one new `transfer_group_id`. A `type = 2` record not referenced by any `AHTransfer` SHALL be imported with `needs_review = true`. Legs whose currencies differ SHALL each store the other leg's amount, carrying this leg's sign, as `original_amount` / `original_currency`, with `fx_rate = |amount| / |original_amount|` rounded half-up to 10 decimals and `fx_source = 'moze_backup'`; the amounts are the source of truth and the rate is derived, so consistency checks use the tolerance defined in the ledger spec's transfer requirement, never exact equality. `AHTransfer.exchangeRate` SHALL be ignored for amounts and only reported when it disagrees with the derived rate by more than 1 %.
- **Fee records.** A record referenced by another record's `feeID` SHALL become a `fee` child (`parent_entry_id`) of that record instead of a standalone entry.
- **Refunds.** A record with `isRefund == true` SHALL be imported with `kind = refund`. When either the refund record or the original carries `refundID`, the refund entry SHALL get `refunds_entry_id` pointing at the original entry. The real backup holds one such record; the implementation SHALL confirm which side carries `refundID` and record the direction in the report.
- **Settlements.** A `type = 5` or `6` record with `relatedID` pointing at a `type = 3` or `4` record SHALL get `settles_entry_id` to that entry. Without `relatedID` the entry is still imported with its counterparty; open amounts are then computed per counterparty.
- **Groups.** Each `AHPackage` with one or more records SHALL become an `entry_group` (`kind = reward_claim` when `AHPackage.type = 4`, `installment` when `eventType = 2`, else `split`; `name`, `store` → `merchant`, `desc`) and its records SHALL carry `group_id`. A package with exactly one record SHALL still be a group (so re-import is stable).
- **Rewards.** A `type = 14` record SHALL get `reward_rule_id` from `rewardID` and `reward_source_entry_id` from `rewardRecordID`. A missing rule or source SHALL set `needs_review = true`, not fail.
- **Attachments.** Each record's `bonusRewards` list SHALL become `entry_reward_rule` rows.
- **Counterparties.** Each `AHTarget` SHALL become a `counterparty` (`name`, `moze_id`).

#### Scenario: Transfer pair from the backup
- **GIVEN** an `AHTransfer` whose out and in records are `-10000 TWD` on A and `+46200 JPY` on B
- **WHEN** imported
- **THEN** both entries SHALL share a `transfer_group_id`, neither SHALL be `needs_review`
- **AND** the in-leg SHALL carry `original_amount = 10000`, `original_currency = TWD`, `fx_rate = 4.62`
- **AND** the out-leg SHALL carry `original_amount = -46200`, `original_currency = JPY`, `fx_rate = 0.2164502165`

#### Scenario: Reward links rule and source
- **GIVEN** a `type = 14` record with `rewardID = R` and `rewardRecordID = S`
- **WHEN** imported
- **THEN** the `reward` entry SHALL reference the rule imported from `R` and the entry imported from `S`

#### Scenario: Split package
- **GIVEN** an `AHPackage` of `type = 0` with 2 records
- **WHEN** imported
- **THEN** one `entry_group` of kind `split` SHALL exist and both entries SHALL reference it

### Requirement: Accounts, groups, categories, projects, rules and preferences

- **Groups.** Each `AHAccountGroup` except `ARCHIVE` SHALL become an `account_group`, named by the map `APP_GROUP_CASH → 現金`, `APP_GROUP_BANK → 銀行`, `APP_GROUP_CREDIT_CARD → 信用卡`, `APP_GROUP_STORED_VALUE_CARD → 電子票證`, `APP_GROUP_GIFT_VOUCHER → 禮券`, `APP_GROUP_REWARD → 紅利點數`, `APP_GROUP_INSURANCE_POLICY → 保單`, `APP_GROUP_SECURITIES → 證券`, `APP_GROUP_CRYPTO_CURRENCY → 加密貨幣`, `ACCOUNT_OTHERS → 其他`, other keys unchanged; `sequence` → `sort_order`.
- **Accounts.** Each `AHAccount` SHALL be matched by `moze_id`, then by `name`, else created. On a **newly created** account every field below is taken from the backup. On an **existing** account the import SHALL always update `name` (when matched by `moze_id`), `opening_balance` and `is_archived`; it SHALL update the remaining settings columns only when `settings_locally_edited` is false; and it SHALL never change `currency` while the account still has any entry after the old imported rows are deleted, whatever its `source` (a differing backup currency then fails the import naming the account). Accounts whose settings were skipped because of the flag SHALL be listed in the report with the differing fields. Fields: `name`, `mainCurrency` → `currency`, `originalAmount` → `opening_balance`, `isArchived`, `group` → `group_id`, `desc` → `note`, `sequence` → `sort_order`, `isBalanceIncluded` → `include_in_total`, `isCreditAccount` → `is_credit`, `startDay` → `closing_day` (1 → NULL for non-credit accounts), `paymentDeadlineType` 0 → `due_rule = fixed_day`, 1 → `days_after_closing` (the implementation SHALL confirm the mapping against the docs' examples and record it in the report), `paymentDeadline` → `due_value`, `creditLimit` → `credit_limit` (NULL when 0), `combinedAccount` → `combined_account_id`, `creditSharingID` → `credit_sharing_id` (a UUID derived deterministically from the MOZE id), `autoPaidAccount` → `auto_pay_account_id`, `isCurrencyFeeEnabled` + `feePercentage` → `fx_fee_pct`, `feeCalculation` → `fx_fee_rounding`, `isRefundWithCurrencyFee` → `fx_fee_refundable`, icon by group per the design's table when the account has none. Accounts with `type` 3 or 4 (MOZE's system accounts 應收應付款項 and 分期帳款) SHALL be imported but marked `is_archived = true` and `include_in_total = false`, since HomeHub tracks receivables by counterparty and installments by phase 4.
- **Categories.** Each `AHCategory` SHALL become a main `category` of the kind given by its `type` (1 expense, 2 income, 3 transfer_out, 4 receivable, 5 payable, 6 balance_adjustment, 9 fee, 10 discount, 11 reward, 12 interest; 7, 8, 13 and 102 are not imported), with `name` mapped from MOZE's localisation keys (`CATEGORY_FOOD → 飲食`, `CATEGORY_TRANSPORT → 交通`, `CATEGORY_ENTERTAINMENT → 娛樂`, `CATEGORY_SHOPPING → 購物`, `CATEGORY_PERSONAL → 個人`, `CATEGORY_MEDICAL → 醫療`, `CATEGORY_HOUSE → 家居`, `CATEGORY_FAMILY → 家庭`, `CATEGORY_LIVING → 生活`, `CATEGORY_LEARNING → 學習`, `CATEGORY_OTHERS → 其他`, `CATEGORY_INCOME → 收入`, `CATEGORY_TRANSFER → 轉帳`, `CATEGORY_RECEIVABLE → 應收款項`, `CATEGORY_PAYABLE → 應付款項`, `CATEGORY_ADJUSTMENT → 餘額調整`, `CATEGORY_SYSTEM_FEE → 手續費`, `CATEGORY_SYSTEM_BONUS → 折扣`, `CATEGORY_SYSTEM_BONUS_REWARD → 紅利回饋`, `CATEGORY_SYSTEM_INTEREST → 利息`), `colorHex` → `color`, `imageName` → `icon` per the design's table, `isHidden` → `is_hidden`, `sequence` → `sort_order`. Each `AHClassification` SHALL become a sub-category of its `category` (`name` as is, except `CATEGORY_*` keys mapped as above) with `defaultAccount` → `default_account_id`, `defaultProject` → `default_project_id`. The categories the CSV importer created SHALL be matched by `(kind, parent name, name)` so existing entries keep their rows.
- **Projects.** Each `AHProject` SHALL become a `project` (`NO_PROJECT` is skipped; `PROJECT_TRAVEL → 旅遊`, `PROJECT_LIVING → 生活`, `PROJECT_INVEST → 投資`), `isArchived`.
- **Rules.** Each `AHBonusReward` SHALL be upserted into `reward_rule` by `moze_id` (existing rows updated in place, so their `id` and any attachments survive). A rule present in the ledger with a `moze_id` but absent from the backup SHALL be deleted when no `entry_reward_rule` or `reward` entry references it, and otherwise set `is_enabled = false` and listed in the report under `orphaned_rules`. Mapping: `type` 0 → `percent` with `rewardPercentage` → `rate`, 1 → `fixed` with `rewardAmount` → `fixed_amount`; `rewardPeriodType` 0 → `statement_cycle` (other values fail the import); `rewardTimeType` 0 → `after_transaction`, 2 → `after_window`, 3 → `manual` (other values fail); `rewardDelayDays` → `delay_days`; `rewardMonth` → `post_month_offset`; `rewardDay` → `post_day`; `rewardCalculation` and `totalRewardCalculation` → `txn_rounding` / `total_rounding` by the map 0 → `keep`, 1 → `floor`, 2 → `ceil`, 4 → `round` (the implementation SHALL confirm this map against the reward records in the backup and record it in the report); `totalRewardLimit` → `total_cap` (NULL when 0); `rewardSharingID` → `shared_cap_id` (deterministic UUID); `isBasic`; `rewardAccountID` → `reward_account_id`; `rewardProjectID` → `reward_project_id`; `startDate` / `dueDate` → `starts_on` / `ends_on`; `isEnabled`; `desc`; `sequence`; `name`. A non-zero `rewardLimit`, `spendThreshold`, `totalSpendThreshold` or `minCountThreshold` SHALL be recorded in the report as unsupported and the rule imported with `is_enabled = false`.
- **Preferences.** `AHPreference` SHALL seed the `preference` row: `expenseIncomeColor` 0 → `red_green`, 1 → `green_red`; `numberPadType` 1 → `calculator`, 0 → `phone`; `firstWeekday` → `week_start` (1 → 0 Sunday, 2 → 1 Monday, …); `mainCurrency.code`; `hideRewardsOnHome`; `isTotalBalanceAbbreviate`.

#### Scenario: Credit card settings seeded
- **GIVEN** an `AHAccount` with `isCreditAccount = true`, `startDay = 15`, `paymentDeadlineType = 1`, `paymentDeadline = 20`, `creditLimit = 300000`, `combinedAccount = M`, `isCurrencyFeeEnabled = true`, `feePercentage = 1.5`
- **WHEN** imported
- **THEN** the account SHALL have `is_credit = true`, `closing_day = 15`, `due_rule = days_after_closing`, `due_value = 20`, `credit_limit = 300000`, `combined_account_id` = the account imported from `M`, `fx_fee_pct = 1.5`

#### Scenario: Percent rule with cap
- **GIVEN** an `AHBonusReward` with `type = 0`, `rewardPercentage = 2`, `rewardTimeType = 2`, `rewardMonth = 1`, `rewardDay = 15`, `totalRewardLimit = 150`, `rewardSharingID = nil`
- **WHEN** imported
- **THEN** a `reward_rule` with `method = percent`, `rate = 2`, `posting = after_window`, `post_month_offset = 1`, `post_day = 15`, `total_cap = 150`, `shared_cap_id = NULL` SHALL exist

#### Scenario: Unsupported threshold disables the rule
- **GIVEN** an `AHBonusReward` with `totalSpendThreshold = 5000`
- **WHEN** imported
- **THEN** the rule SHALL be imported with `is_enabled = false` and the report SHALL list it under `unsupported_rules`

#### Scenario: Locally edited settings survive a re-import
- **GIVEN** an imported credit card whose `closing_day` the owner changed from 15 to 20 (`settings_locally_edited = true`)
- **WHEN** the backup is imported again with `startDay = 15`
- **THEN** `closing_day` SHALL remain 20, the opening balance SHALL be refreshed, and the report SHALL list the account under `settings_skipped` with `closing_day: 15 → kept 20`

#### Scenario: Currency change refused while a hermes entry remains
- **GIVEN** an imported TWD account that also holds one entry with `source = 'hermes'`
- **WHEN** a backup is imported in which that account's `mainCurrency` is `JPY`
- **THEN** the import SHALL fail naming the account, and nothing SHALL change

#### Scenario: Manual attachment survives a re-import
- **GIVEN** a manual expense attached to an imported rule
- **WHEN** the backup is imported again
- **THEN** the rule SHALL keep its `id` and the attachment SHALL still exist

### Requirement: Scheduled data preserved for phase 4

The importer SHALL store, in a `moze_schedule` table (`id`, `kind` enum `period | installment | skipped_record`, `moze_id`, `payload` JSONB, `import_run_id`), every live `AHPeriod` and `AHInstallment` row and every record skipped as future-dated, replacing the table's contents on each backup import. `GET /api/accounting/imports/schedules` SHALL list them with the next due date taken from the payload, so the settings page can show upcoming installments until phase 4 generates them.

#### Scenario: Skipped rows are retrievable
- **GIVEN** 240 future repayment rows were skipped
- **WHEN** `GET /api/accounting/imports/schedules?kind=skipped_record` is called
- **THEN** 240 items SHALL be returned, each with its `moze_id`, date and amount from the payload

### Requirement: Transactional full replace and report

The backup importer SHALL share the CSV importer's advisory lock, `import_run` lifecycle, dry-run, `--rename OLD=NEW` and `ACCOUNTING_IMPORT_LOCKED` behaviour. Its ledger transaction SHALL: delete every `ledger_entry` and `entry_group` whose `source` is a MOZE source or whose `moze_id` is set, together with their `entry_reward_rule` rows; upsert groups, accounts, categories, projects, counterparties and rules (rules by `moze_id`, never recreated); insert entries and groups; archive accounts not in the backup that have no remaining entries; delete unused categories, projects and counterparties; replace `moze_schedule`; and record success. Manual rows, their attachments, and their categories, projects and counterparties SHALL be untouched.

The report SHALL contain: per-type counts of imported and skipped-future rows; counts of groups, transfers, rules, attachments, counterparties; `needs_review` count with reasons; `unsupported_rules`, `orphaned_rules`, `settings_skipped`; the balance comparison below; the FX sanity-check outcome; and the mappings confirmed at import time (due rule, rounding map, refund direction, tag delimiter, `balanceInfo` key).

**Balance comparison.** For each imported account the report SHALL show `moze_part` = `opening_balance + Σ amount` of that account's `moze_backup` entries with `posted_date ≤ the backup's balance cache date` (`AHAccount.cacheDate`, falling back to the export date), MOZE's cached balance from `balanceInfo` when the implementation has confirmed which key holds the current balance, and the difference. Accounts without a comparable cached balance, and MOZE's system accounts (`type` 3 and 4), SHALL be listed under `not_compared` for the owner's manual check. Manual entries and entries posted after the cache date SHALL NOT enter the comparison. The report SHALL also show, per account, `previous_moze_part`: the same sum over the MOZE-sourced entries that existed before this import (for the first backup import, the phase 1 CSV ledger the owner already checked against MOZE), so the owner can see every account whose balance moved and why (new records since the CSV export, MOZE's own FX amounts, skipped future rows).

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

#### Scenario: Manual rows survive
- **GIVEN** 3 manual entries exist
- **WHEN** a backup import runs
- **THEN** the 3 entries SHALL remain and their categories SHALL not be deleted

### Requirement: Import entry points

The backup importer SHALL be invocable in two ways:

- **CLI**: `python -m app.services.moze_backup_import_service <zip> [--dry-run] [--rename OLD=NEW ...] [--allow-fx-outliers] [--no-strict] [--keep-json <path>]`.
- **REST**: `POST /api/accounting/imports/moze-backup` with a multipart upload (max 200 MB), optional `renames`, `dry_run`, `strict`, `allow_fx_outliers`. `GET /api/accounting/imports/latest` SHALL report backup imports too, with `kind = moze_backup`.

The frontend settings page SHALL call the REST endpoint; the cutover runbook (2b) uses the CLI.

#### Scenario: Dry run writes nothing
- **WHEN** the CLI runs with `--dry-run`
- **THEN** it SHALL print the full report and the database SHALL be unchanged, including no `import_run` row

#### Scenario: Real backup imports within limits
- **WHEN** `MOZE_4.0.zip` is imported against a fresh database
- **THEN** the import SHALL succeed in under 5 minutes including conversion
- **AND** the report SHALL show 544 transfers, 100 rules and at least 1,900 attachments
