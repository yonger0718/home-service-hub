# accounting-moze-backup-import Specification

## Purpose
TBD - created by archiving change add-accounting-entry. Update Purpose after archive.
## Requirements
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
- **Accounts.** Each `AHAccount` SHALL be matched by `moze_id`, then by `name`, else created. On a **newly created** account every field below is taken from the backup. On an **existing** account the import SHALL always update `name` (when matched by `moze_id`), `opening_balance` and `is_archived`; it SHALL update the remaining settings columns only when `settings_locally_edited` is false; and it SHALL never change `currency` while the account still has any entry after the old imported rows are deleted, whatever its `source` (a differing backup currency then fails the import naming the account). Accounts whose settings were skipped because of the flag SHALL be listed in the report with the differing fields. Fields: `name`, `mainCurrency` → `currency`, `originalAmount` → `opening_balance`, `isArchived`, `group` → `group_id`, `desc` → `note`, `sequence` → `sort_order`, `isBalanceIncluded` → `include_in_total`, `isCreditAccount` → `is_credit`, `startDay` (the first day of MOZE's statement period) → `closing_day = startDay − 1` (1 → NULL, the calendar month closing on its last day, for every account), `paymentDeadlineType` 0 → `due_rule = fixed_day`, 1 → `days_after_closing` (the implementation SHALL confirm the mapping against the docs' examples and record it in the report), `paymentDeadline` → `due_value`, `creditLimit` → `credit_limit` (NULL when 0), `combinedAccount` → `combined_account_id`, `creditSharingID` → `credit_sharing_id` (a UUID derived deterministically from the MOZE id), `autoPaidAccount` → `auto_pay_account_id`, `isCurrencyFeeEnabled` + `feePercentage` → `fx_fee_pct`, `feeCalculation` → `fx_fee_rounding`, `isRefundWithCurrencyFee` → `fx_fee_refundable`, icon by group per the design's table when the account has none. Accounts with `type` 3 or 4 (MOZE's system accounts 應收應付款項 and 分期帳款) SHALL be imported but marked `is_archived = true` and `include_in_total = false`, since HomeHub tracks receivables by counterparty and installments by phase 4.
- **Categories.** Each `AHCategory` SHALL become a main `category` of the kind given by its `type` (1 expense, 2 income, 3 transfer_out, 4 receivable, 5 payable, 6 balance_adjustment, 9 fee, 10 discount, 11 reward, 12 interest; 7, 8, 13 and 102 are not imported), with `name` mapped from MOZE's localisation keys (`CATEGORY_FOOD → 飲食`, `CATEGORY_TRANSPORT → 交通`, `CATEGORY_ENTERTAINMENT → 娛樂`, `CATEGORY_SHOPPING → 購物`, `CATEGORY_PERSONAL → 個人`, `CATEGORY_MEDICAL → 醫療`, `CATEGORY_HOUSE → 家居`, `CATEGORY_FAMILY → 家庭`, `CATEGORY_LIVING → 生活`, `CATEGORY_LEARNING → 學習`, `CATEGORY_OTHERS → 其他`, `CATEGORY_INCOME → 收入`, `CATEGORY_TRANSFER → 轉帳`, `CATEGORY_RECEIVABLE → 應收款項`, `CATEGORY_PAYABLE → 應付款項`, `CATEGORY_ADJUSTMENT → 餘額調整`, `CATEGORY_SYSTEM_FEE → 手續費`, `CATEGORY_SYSTEM_BONUS → 折扣`, `CATEGORY_SYSTEM_BONUS_REWARD → 紅利回饋`, `CATEGORY_SYSTEM_INTEREST → 利息`), `colorHex` → `color`, `imageName` → `icon` per the design's table, `isHidden` → `is_hidden`, `sequence` → `sort_order`. Each `AHClassification` SHALL become a sub-category of its `category` (`name` as is, except `CATEGORY_*` keys mapped as above) with `defaultAccount` → `default_account_id`, `defaultProject` → `default_project_id`. The categories the CSV importer created SHALL be matched by `(kind, parent name, name)` so existing entries keep their rows.
- **Projects.** Each `AHProject` SHALL become a `project` (`NO_PROJECT` is skipped; `PROJECT_TRAVEL → 旅遊`, `PROJECT_LIVING → 生活`, `PROJECT_INVEST → 投資`), `isArchived`.
- **Rules.** Each `AHBonusReward` SHALL be upserted into `reward_rule` by `moze_id` (existing rows updated in place, so their `id` and any attachments survive). A rule present in the ledger with a `moze_id` but absent from the backup SHALL be deleted when no `entry_reward_rule` or `reward` entry references it, and otherwise set `is_enabled = false` and listed in the report under `orphaned_rules`. Mapping: `type` 0 → `percent` with `rewardPercentage` → `rate`, 1 → `fixed` with `rewardAmount` → `fixed_amount`; `rewardPeriodType` 0 → `statement_cycle` (other values fail the import); `rewardTimeType` 0 → `after_transaction`, 2 → `after_window`, 3 → `manual` (other values fail); `rewardDelayDays` → `delay_days`; `rewardMonth` → `post_month_offset`; `rewardDay` → `post_day`; `rewardCalculation` and `totalRewardCalculation` → `txn_rounding` / `total_rounding` by the map 0 → `keep`, 1 → `floor`, 2 → `ceil`, 4 → `round` (the implementation SHALL confirm this map against the reward records in the backup and record it in the report); `totalRewardLimit` → `total_cap` (NULL when 0); `rewardSharingID` → `shared_cap_id` (deterministic UUID); `isBasic`; `rewardAccountID` → `reward_account_id`; `rewardProjectID` → `reward_project_id`; `startDate` / `dueDate` → `starts_on` / `ends_on`; `isEnabled`; `desc`; `sequence`; `name`. A non-zero `rewardLimit`, `spendThreshold`, `totalSpendThreshold` or `minCountThreshold` SHALL be recorded in the report as unsupported and the rule imported with `is_enabled = false`.
- **Preferences.** `AHPreference` SHALL seed the `preference` row: `expenseIncomeColor` 0 → `red_green`, 1 → `green_red`; `numberPadType` 1 → `calculator`, 0 → `phone`; `firstWeekday` → `week_start` (1 → 0 Sunday, 2 → 1 Monday, …); `mainCurrency.code`; `hideRewardsOnHome`; `isTotalBalanceAbbreviate`.

#### Scenario: Credit card settings seeded
- **GIVEN** an `AHAccount` with `isCreditAccount = true`, `startDay = 15`, `paymentDeadlineType = 1`, `paymentDeadline = 20`, `creditLimit = 300000`, `combinedAccount = M`, `isCurrencyFeeEnabled = true`, `feePercentage = 1.5`
- **WHEN** imported
- **THEN** the account SHALL have `is_credit = true`, `closing_day = 14`, `due_rule = days_after_closing`, `due_value = 20`, `credit_limit = 300000`, `combined_account_id` = the account imported from `M`, `fx_fee_pct = 1.5`

#### Scenario: Percent rule with cap
- **GIVEN** an `AHBonusReward` with `type = 0`, `rewardPercentage = 2`, `rewardTimeType = 2`, `rewardMonth = 1`, `rewardDay = 15`, `totalRewardLimit = 150`, `rewardSharingID = nil`
- **WHEN** imported
- **THEN** a `reward_rule` with `method = percent`, `rate = 2`, `posting = after_window`, `post_month_offset = 1`, `post_day = 15`, `total_cap = 150`, `shared_cap_id = NULL` SHALL exist

#### Scenario: Unsupported threshold disables the rule
- **GIVEN** an `AHBonusReward` with `totalSpendThreshold = 5000`
- **WHEN** imported
- **THEN** the rule SHALL be imported with `is_enabled = false` and the report SHALL list it under `unsupported_rules`

#### Scenario: Locally edited settings survive a re-import
- **GIVEN** an imported credit card whose `closing_day` the owner changed from 14 to 20 (`settings_locally_edited = true`)
- **WHEN** the backup is imported again with `startDay = 15`
- **THEN** `closing_day` SHALL remain 20, the opening balance SHALL be refreshed, and the report SHALL list the account under `settings_skipped` with `closing_day: 14 → kept 20`

#### Scenario: Currency change refused while a hermes entry remains
- **GIVEN** an imported TWD account that also holds one entry with `source = 'hermes'`
- **WHEN** a backup is imported in which that account's `mainCurrency` is `JPY`
- **THEN** the import SHALL fail naming the account, and nothing SHALL change

#### Scenario: Manual attachment survives a re-import
- **GIVEN** a manual expense attached to an imported rule
- **WHEN** the backup is imported again
- **THEN** the rule SHALL keep its `id` and the attachment SHALL still exist

### Requirement: Transactional full replace and report

The backup importer SHALL share the CSV importer's advisory lock (retrying `pg_try_advisory_lock` for up to 30 seconds while schedule writers hold the key shared), `import_run` lifecycle, dry-run, `--rename OLD=NEW` and `ACCOUNTING_IMPORT_LOCKED` behaviour. Its ledger transaction SHALL: lock, before deleting anything, every schedule definition it will touch (imported ones, and local ones whose templates or posted entries point at MOZE entries) by ascending id and then their instances by ascending id (`SELECT … FOR UPDATE`); delete every `ledger_entry` and `entry_group` whose `source` is a MOZE source or whose `moze_id` is set, together with their `entry_reward_rule` rows; upsert groups, accounts, categories, projects, counterparties and rules (rules by `moze_id`, never recreated); insert entries and groups; archive accounts not in the backup that have no remaining entries; delete unused categories, projects and counterparties **except** those whose `moze_id` is present in the backup (seeded settings stay, so the pickers match MOZE) — a reference from a schedule template counts as usage, for accounts too (such an account is never archived as disappeared); upsert schedule definitions and instances and re-point schedule links (see "Schedule definitions and instances from the backup"); and record success; after the commit, once the advisory lock is released, a real (non-dry-run) import SHALL trigger a schedule job run (through the in-process scheduler for an import made through the API; the standalone CLI SHALL run the job inline in its own process — generation always, posting only when `ACCOUNTING_SCHEDULER_ENABLED` is on, otherwise logging the count of due but unposted instances — and print a counts-only `schedule_job:` line); a dry run SHALL NOT. Manual rows, `schedule` rows, locally created schedule definitions and their instances, their attachments, and their categories, projects and counterparties SHALL be untouched. Before the delete step the importer SHALL record, for every template `loan_entry_id` and every `settles_entry_id` of a `schedule` entry that points at a MOZE-sourced entry, that entry's `moze_id`, and after inserting SHALL re-point each to the entry now carrying that `moze_id`; a link whose target is gone SHALL be cleared and its definition set `paused` with `review_reason = loan_missing`.

The report SHALL contain: per-type counts of imported and skipped-future rows; counts of groups, transfers, rules, attachments, counterparties; `needs_review` count with reasons; `unsupported_rules`, `orphaned_rules`, `settings_skipped`; the balance comparison below; the FX sanity-check outcome; the mappings confirmed at import time (due rule, rounding map, refund direction, tag delimiter, `balanceInfo` key); and a `schedules` block with `definitions` (`recurring`, `installment`, `single`: created, updated, ended, deleted), `instances` (created, updated, kept, `kept_owner_edited`, adopted, deleted, `posted_from_past`, `skipped_disabled`), `records_mapped`, `rewards_ignored`, `artifact_records` (MOZE's single disabled extra record group of a finite series, past its last period or on its last period's date: dropped, never an instance, and counted here), `unsupported_types` (per type), `past_records_already_posted`, `past_records_already_skipped`, `past_records_amount_differs` (`count` and `instance_ids` of the posted instances with a differing line, and `lines`: one item per differing line), `past_records_owner_pending` (`[{definition_id, seq, date}]`), `past_records_matched_by_seq` (`count` and `items`: `[{definition_id, seq, date, due_date}]`, the record's date and the instance's `due_date` of each past record covered by a HomeHub-acted instance of the same seq), `relinked` (template and settlement links re-pointed), `review` (`[{moze_id, reason}]`, including same-date duplicates), `amount_differs` (one item per differing line of a pending period whose amounts the import keeps — every pending period of a definition with `template_owner_edited = true`, and every retained `edited_by_owner` pending period, compared at the amounts it will post) and `loan_remainder_check` (`[{definition, moze_remainder, open_amount, difference}]` per loan definition). A line item is `{definition_id, name, seq, date, line, kind, amount, moze_amount}`: the template line's index and kind (`transfer_in` for a transfer's in-leg), HomeHub's amount and the matching MOZE record's amount — lines are compared one by one (repayment, interest, expense, both transfer legs), never as period totals. `past_records_amount_differs.lines`, `amount_differs` and `loan_remainder_check` are the only report lists with amounts; logs SHALL carry none of them. The `skipped_future` counts and `records_mapped + rewards_ignored + Σ unsupported_types` SHALL both equal the number of enabled future records.

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

### Requirement: Schedule definitions and instances from the backup

The backup importer SHALL map MOZE's scheduled data to `schedule_definition` and `schedule_instance` rows (accounting-schedules spec) in its ledger transaction, after inserting entries. New imported definitions SHALL have `created_locally = false`, `posting_mode = auto`, `auto_post_from` = the import's Asia/Taipei date and `status = active` — `paused` with a `review_reason` only for a live definition with a mapping problem, `ended` with `review_reason = not_live` for a period that is not live (below). A definition whose series is complete (its last period exists) with nothing pending or partial SHALL be ended by the job's generation step, which the job run after a real import performs; its `review_reason` is kept.

- **Installments.** Each `AHInstallment` SHALL become a definition with `kind = installment` (`recurring` when `times < 2`, because an installment needs at least 2 periods), `moze_id = identifier`, `interval_unit = month`, `interval_n = 1`, `day_of_month = dayOfMonth`, `times`, `total_amount = total`, `first_seq = 1` and `moze_payload` = the raw row. Its origin SHALL first be set aside and stay an ordinary entry: a loan's type-4 (payable) records, else the record group that shares a date with another group and whose |total| equals `total`; an enabled future origin becomes a single definition. The period dates SHALL be `dateInfo` when present (it keeps priority), else the dates of the remaining record groups; `anchor_date` = the first period date. Template lines SHALL come from its first package: a type-6 record → a `repayment` line (`account_id` = the record's account, `category_id`, `amount = |total|`, `loan_entry_id` = the entry imported from the record's `relatedID` — or, when `relatedID` names MOZE's disabled copy of the loan, from the enabled type-4 record); a type-15 record in the same package → an `interest` line; a type-0 record → an `expense` line; plus one line for each record kind a later package carries (that period's amount for a missing member is `"0"`). `name` = the first record's `name`, else its category's name, else `分期`. When the period dates are not the monthly series on `dayOfMonth` (clamped to each month's length) the definition SHALL be imported `paused` with `review_reason = interval_mismatch`. An installment without period records SHALL not be imported and SHALL be listed under `review` as `no_records`.
- **Recurring.** Each `AHPeriod` with records SHALL become a definition with `kind = recurring`, `interval_n = 1`, and: `unit = 1` → `interval_unit = week` on the weekday given by `days` (1 = Sunday … 7 = Saturday), `day_of_month = NULL`; `unit = 2` → `interval_unit = month` with `day_of_month = days` (clamped to the month's length); `times = 0` → `times = NULL` (unlimited), else `times`, the period count; `anchor_date` = the date of the earliest record group for that `eventID` (past or future), with `first_seq = 1`. `startDate` SHALL only be checked for the rule's phase (its weekday, or its clamped day of month); it may lie before or after the anchor. `type = 1` periods generate transfers (record type 2); `type = 0` periods generate ordinary records. Lines SHALL come from the first record group (record type → line kind: 0 `expense`, 1 `income`, 2 `transfer` with the in-leg's account and amount as `to_account_id` / `to_amount`, 3 `receivable`, 4 `payable`, 5 `collection`, 6 `repayment`, 15 `interest`), plus one line for each record kind a later group carries. A finite period's `times` SHALL be capped at its highest mapped seq, so HomeHub never generates past MOZE's last record. Another `unit`, a weekday or day of month that disagrees with the records, or a `startDate` off the rule's phase SHALL flag `review_reason = interval_mismatch`. A period without records SHALL be listed under `review` as `no_records`.
- **Finite series artifact.** MOZE keeps one disabled extra record group in a finite series, on another group's date or after the last occurrence. That single disabled extra SHALL be dropped (no instance) and its records counted under `artifact_records`; a second extra, or an enabled record past the series' end, SHALL flag `interval_mismatch` instead.
- **Liveness.** A period with at least one enabled future record is live. A period that is not live SHALL be imported `ended` with `review_reason = not_live` (an existing one set so); a later backup with an enabled future record SHALL set it back to `active` (`paused` with a mapping reason) and clear the reason. An `ended` the owner set SHALL stay. The import's end SHALL delete only the pending periods whose MOZE record left the backup; owner-edited and HomeHub-generated pending periods stay. Installments and single records are always live.
- **Single future records.** An enabled future record without `eventID`, other than a reward, SHALL become its own `recurring` definition with `times = 1` and `moze_id = "record:" + identifier`; so SHALL one whose `eventID` names no period or installment, listed under `review` as `event_missing`.
- **Loan record missing.** A definition whose loan record (`relatedID`) is not imported SHALL be imported `paused` with `review_reason = loan_missing`.
- **Instances and seq.** Each record of type 0–6 or 15 generated by a definition (past or future) SHALL map to exactly one instance: `moze_id` = the record's identifier, `rule_date` = `due_date` = its `date` (Asia/Taipei), `seq` = for an `AHInstallment` the 1-based position of that date among its period dates, for an `AHPeriod` 1 + the occurrence index of that date counted from the anchor over past and future records together; `amount_override` = `|total|` per line, `"0"` for a line whose package member is missing; `moze_payload` = the raw records. A type-15 record that shares `packageID` with a type-6 record, and the in-leg of a transfer, SHALL merge into the same instance (`moze_record_ids` lists every merged record). When two record groups of one definition share a date, the enabled one SHALL be mapped and the other listed under `schedules.review` as `same_date`. Type-14 records SHALL be ignored and counted under `rewards_ignored`; other types SHALL be counted under `unsupported_types` and ignored.
- **Status.** A past record imported as an entry SHALL give a `posted` instance with `acted_by = import`, `acted_at` = the import's start time and `posted_entry_ids` = those entries. A disabled record (past or future) SHALL give a `skipped` instance with `acted_by = import`. An enabled future record SHALL give a `pending` instance.
- **Re-import.** Definitions SHALL be upserted by `moze_id`: rule, template, `name`, `times`, `total_amount` and `moze_payload` refreshed; `posting_mode` and `auto_post_from` kept. `status` SHALL stay as the owner left it, except that a mismatch or `loan_missing` that is new in this import SHALL set `review_reason` and `status = paused`, and a `review_reason` that no longer applies SHALL be cleared without changing `status`. Instances SHALL be upserted by `moze_id`. An instance without `moze_id` of the same definition whose `rule_date` equals the record's date (else, when none, whose `due_date` does) SHALL be adopted: it gets the record's `moze_id` and `moze_record_ids`, and, when `pending` and not `edited_by_owner`, its `amount_override`; an adopted instance that is `posted` keeps its entries and the record is counted under `past_records_already_posted` instead of being imported. A past record that matches no instance by `moze_id`, by a merged record id, by `rule_date` or by `due_date` SHALL fall back to the instance of the same definition with the same `seq` when that instance has no `moze_id` and is `posted` or `skipped` with `acted_by` `auto` or `owner` (MOZE's record for the occurrence sits on another date): the record SHALL count as covered and SHALL NOT be imported, the instance SHALL take its `moze_id` and `moze_record_ids` and keep its status, and it SHALL be listed under `schedules.past_records_matched_by_seq`. When the record's computed `seq` belongs to another instance of the definition, the existing instance SHALL keep its seq and the record SHALL be listed under `schedules.review` and skipped; the import SHALL never fail on it. `pending` imported instances SHALL be refreshed unless `edited_by_owner` is true, in which case they SHALL be kept unchanged — also when their record is now past (it is then not imported, "Record mapping") — and their amounts compared with MOZE's under `amount_differs`. Instances with `acted_by = import` SHALL be recomputed from the newly imported entries; one with no entries left SHALL become `skipped` with `acted_by = import`. Instances posted by `auto` or `owner`, and every `skipped` instance whose `acted_by` is not `import`, SHALL be kept unchanged. Imported `pending` instances absent from the backup SHALL be deleted unless `edited_by_owner`; `pending` instances without `moze_id` SHALL never be deleted by an import. An imported definition absent from the backup SHALL be set `ended` with its `pending` instances deleted when it has any posted or skipped instance, and deleted otherwise. No `pending` instance SHALL be created for an `ended` definition. Definitions with `created_locally = true` and their instances SHALL never be written, except for the link re-pointing of "Transactional full replace and report".
- **Owner-set template amounts.** For a definition with `template_owner_edited = true` (the owner changed its amounts with scope `following` or `all`, accounting-schedules "Instance amount edit scope"), the import SHALL keep the template's line amounts (a refreshed line at the same index with the same kind keeps the stored amount), SHALL NOT write MOZE's amounts as `amount_override` on its `pending` instances (new, refreshed or adopted), and SHALL list each line of a pending period whose MOZE amount differs under `amount_differs`; adoption, dates, statuses and the other re-import rules SHALL apply unchanged.
- **Amounts of skipped past records.** When a past record is not imported because a kept posted instance lists it, each template line of that instance SHALL be compared with the matching MOZE record (`moze_record_ids` keeps which record is which line: a packaged repayment and its interest map to their own lines, a transfer's legs to its out- and in-amount); every line whose absolute posted amount differs from the record's `|total|` SHALL be listed under `past_records_amount_differs.lines` and the instance counted in `count` / `instance_ids`.
- **Loan check.** For each imported installment with a `repayment` line the report SHALL compare `AHInstallment.remainder` with the loan entry's open amount after the import, listing both and the difference under `loan_remainder_check`.

#### Scenario: Real backup schedule counts
- **WHEN** `MOZE_4.0.zip` (exported 2026-10-01) is imported into an empty schedule table
- **THEN** the report SHALL show 11 `recurring` and 12 `installment` definitions created (the 2 installments without any record are listed under `review` as `no_records`), `records_mapped = 534`, `rewards_ignored = 63`, `artifact_records = 11`, and `skipped_future` summing to 597

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
- **GIVEN** an `AHPeriod` with `unit = 1` whose `days` names Tuesday while its records, one of them enabled and future, fall on Mondays
- **WHEN** imported
- **THEN** the definition SHALL be `paused` with `review_reason = interval_mismatch` and listed under `schedules.review`

#### Scenario: Installment anchored on its first period
- **GIVEN** an `AHInstallment` with `dayOfMonth = 9`, `times = 36`, no MOZE date list, its loan's type-4 records dated 2026-01-20 and record groups dated 2026-02-09, 2026-03-09, … 2029-01-09
- **WHEN** imported
- **THEN** the type-4 records SHALL stay ordinary entries, the definition SHALL have `anchor_date = 2026-02-09`, and the record dated 2026-11-09 SHALL be the instance with `seq = 10`

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

