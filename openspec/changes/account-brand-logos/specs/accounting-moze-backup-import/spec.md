## MODIFIED Requirements

### Requirement: Accounts, groups, categories, projects, rules and preferences

- **Groups.** Each `AHAccountGroup` except `ARCHIVE` SHALL become an `account_group`, named by the map `APP_GROUP_CASH → 現金`, `APP_GROUP_BANK → 銀行`, `APP_GROUP_CREDIT_CARD → 信用卡`, `APP_GROUP_STORED_VALUE_CARD → 電子票證`, `APP_GROUP_GIFT_VOUCHER → 禮券`, `APP_GROUP_REWARD → 紅利點數`, `APP_GROUP_INSURANCE_POLICY → 保單`, `APP_GROUP_SECURITIES → 證券`, `APP_GROUP_CRYPTO_CURRENCY → 加密貨幣`, `ACCOUNT_OTHERS → 其他`, other keys unchanged; `sequence` → `sort_order`.
- **Accounts.** Each `AHAccount` SHALL be matched by `moze_id`, then by `name`, else created. On a **newly created** account every field below is taken from the backup. On an **existing** account the import SHALL always update `name` (when matched by `moze_id`), `opening_balance` and `is_archived`; it SHALL update the remaining settings columns only when `settings_locally_edited` is false; and it SHALL never change `currency` while the account still has any entry after the old imported rows are deleted, whatever its `source` (a differing backup currency then fails the import naming the account). Accounts whose settings were skipped because of the flag SHALL be listed in the report with the differing fields. Fields: `name`, `mainCurrency` → `currency`, `originalAmount` → `opening_balance`, `isArchived`, `group` → `group_id`, `desc` → `note`, `sequence` → `sort_order`, `isBalanceIncluded` → `include_in_total`, `isCreditAccount` → `is_credit`, `startDay` (the first day of MOZE's statement period) → `closing_day = startDay − 1` (1 → NULL, the calendar month closing on its last day, for every account), `paymentDeadlineType` 0 → `due_rule = fixed_day`, 1 → `days_after_closing` (the implementation SHALL confirm the mapping against the docs' examples and record it in the report), `paymentDeadline` → `due_value`, `creditLimit` → `credit_limit` (NULL when 0), `combinedAccount` → `combined_account_id`, `creditSharingID` → `credit_sharing_id` (a UUID derived deterministically from the MOZE id), `autoPaidAccount` → `auto_pay_account_id`, `isCurrencyFeeEnabled` + `feePercentage` → `fx_fee_pct`, `feeCalculation` → `fx_fee_rounding`, `isRefundWithCurrencyFee` → `fx_fee_refundable`, icon by group and `imageName` → `logo` per the design's tables, each only when the account has none and its settings are not locally edited (generic MOZE pictures give no logo). Accounts with `type` 3 or 4 (MOZE's system accounts 應收應付款項 and 分期帳款) SHALL be imported but marked `is_archived = true` and `include_in_total = false`, since HomeHub tracks receivables by counterparty and installments by phase 4.
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

#### Scenario: Logo from the MOZE picture
- **WHEN** a backup account with `imageName` `AccountCollection10_05` is created by the import
- **THEN** its `logo` SHALL be `cathay-united`; an account with `Account_24` SHALL have no logo
- **AND** a re-import SHALL NOT change a logo that is already set or belongs to a locally edited account
