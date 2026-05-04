## ADDED Requirements

### Requirement: 報表查詢不得寫入資料

Accounting report queries SHALL be read-only. `get_monthly_report`, `get_monthly_compare_report`, and future annual report queries MUST NOT call `recurring_service.generate_recurring_items()` or otherwise create/update/delete rows. Recurring item generation SHALL be triggered only by an explicit command endpoint or scheduled job.

#### Scenario: 查詢當月月報

- **WHEN** active subscriptions/installments exist and no transaction has been generated for the current month
- **AND** the caller requests the monthly report for the current month
- **THEN** the report query returns data from existing transactions only
- **AND** no new transaction rows are inserted

#### Scenario: 明確觸發 recurring generation

- **WHEN** the caller invokes `POST /api/accounting/recurring/generate`
- **THEN** recurring transactions may be created according to existing recurring generation rules

### Requirement: 更新交易卡片時同步預設付款方式

When updating a transaction with `card_id` and without an explicit `payment_method`, the service SHALL set `payment_method` to `card.default_payment_method` when present, otherwise `"Apple Pay"`. It MUST NOT set `payment_method` to `card.name` unless that exact value was explicitly provided by the caller and is a valid payment method.

#### Scenario: 更新卡片且未帶 payment_method

- **WHEN** card A has `default_payment_method = "Apple Pay"`
- **AND** a transaction update payload contains `card_id = cardA.id` and no `payment_method`
- **THEN** the updated transaction has `payment_method = "Apple Pay"`

#### Scenario: 更新卡片且明確帶 payment_method

- **WHEN** the caller updates a transaction with `card_id = cardA.id` and `payment_method = "Cash"`
- **THEN** the service validates `"Cash"` as a payment method
- **AND** preserves `payment_method = "Cash"`

### Requirement: 刪除語意統一為硬刪除

All accounting service DELETE endpoints for transactions, subscriptions, installments, cards, categories, and payment methods SHALL perform hard delete semantics. API summaries, docstrings, and generated OpenAPI output MUST NOT contain "軟刪除", "soft delete", or "soft-delete".

#### Scenario: 刪除 transaction

- **WHEN** `DELETE /api/accounting/transactions/{id}` succeeds
- **THEN** the transaction row is removed from the database
- **AND** fetching the same id returns 404

#### Scenario: OpenAPI 文件

- **WHEN** `/openapi.json` is generated
- **THEN** DELETE endpoint descriptions do not mention soft delete semantics

### Requirement: Alembic 可從空 DB 建立完整 schema

The accounting service Alembic history SHALL support bootstrapping an empty database with `alembic upgrade head`. The resulting database MUST contain all tables required by current models, including `transactions`, `credit_cards`, `categories`, `payment_methods`, `subscriptions`, and `installments`, with expected foreign keys and unique/index constraints.

#### Scenario: 空 DB bootstrap

- **WHEN** an empty PostgreSQL database is configured for accounting service
- **AND** `alembic upgrade head` is executed
- **THEN** all current accounting tables exist
- **AND** the application can start without calling `Base.metadata.create_all()`

#### Scenario: 既有 DB 安全套用

- **WHEN** an existing database already contains accounting tables
- **THEN** the baseline repair process MUST NOT drop or recreate existing production tables
- **AND** the migration/stamp strategy is documented or automated

### Requirement: Refund 端點防呆

`POST /api/accounting/transactions/{id}/refund` SHALL reject invalid refunds with HTTP 400 when: `refund_amount <= 0`, the source transaction is `INCOME`, the cumulative refund amount would exceed the source `transaction_amount`, or the source has already been fully refunded. Cumulative refunded amount SHALL be calculated from transactions where `related_transaction_id = source.id` and `transaction_type = "INCOME"`.

#### Scenario: 超額退款

- **WHEN** the source transaction amount is 1000
- **AND** existing related refunds total 700
- **AND** the caller submits `refund_amount = 400`
- **THEN** the endpoint returns HTTP 400

#### Scenario: 對收入退款

- **WHEN** the source transaction has `transaction_type = "INCOME"`
- **THEN** the endpoint returns HTTP 400

#### Scenario: 合法部分退款

- **WHEN** the source transaction amount is 1000
- **AND** existing related refunds total 300
- **AND** the caller submits `refund_amount = 200`
- **THEN** an `INCOME` refund transaction is created
- **AND** `related_transaction_id` points to the source transaction

### Requirement: Transaction response 提供 refundable_amount

Transaction list and detail responses SHALL include `refundable_amount: int`. For `EXPENSE` transactions it SHALL equal `transaction_amount - cumulative_refunded_amount`, never below 0. For `INCOME` transactions it SHALL be 0.

#### Scenario: 部分退款後查詢

- **WHEN** an expense transaction amount is 1000
- **AND** related refunds total 300
- **THEN** the transaction response contains `refundable_amount = 700`

#### Scenario: 收入交易

- **WHEN** a transaction has `transaction_type = "INCOME"`
- **THEN** the transaction response contains `refundable_amount = 0`

### Requirement: 信用卡週期淨額允許負數

Card cycle usage SHALL return true net usage, calculated as expense total minus income/refund total. The result MAY be negative. When net usage is negative, `is_near_limit` and `is_over_limit` MUST both be false.

#### Scenario: 退款大於消費

- **WHEN** cycle expenses total 1000
- **AND** cycle refunds total 1500
- **THEN** `current_usage = -500`
- **AND** `is_near_limit = false`
- **AND** `is_over_limit = false`

### Requirement: 查詢消除 N+1

Transaction, recurring, and report queries that return or aggregate card/category data SHALL eager-load required many-to-one relationships with `joinedload` or an equivalent constant-query strategy.

#### Scenario: 大量交易列表

- **WHEN** `GET /api/accounting/transactions?limit=100` is called for 100 transactions with cards
- **THEN** SQL query count remains constant and does not grow linearly with the transaction count

### Requirement: 金額型別一律為整數

All amount fields in accounting API responses SHALL be JSON integers. Percentage/rate fields SHALL remain numbers that may contain decimals.

#### Scenario: 月報表 JSON

- **WHEN** the monthly report response is serialized
- **THEN** `total_income`, `total_expense`, `surplus`, category amounts, and payment method amounts are integers
- **AND** `savings_rate` may be decimal

### Requirement: 分類改名同步 legacy category 字串

When a category name is updated, transactions referencing that category through `category_id` SHALL have their legacy `category` string synchronized to the new name. Reports SHALL prefer `category_info.name` and fall back to the legacy `category` string only for historical rows without a valid relationship.

#### Scenario: 分類改名後查詢報表

- **WHEN** category "餐飲" is renamed to "外食"
- **AND** existing transactions reference that category id
- **THEN** those transaction rows have `category = "外食"`
- **AND** monthly reports show "外食"

### Requirement: 分類合併

The system SHOULD provide a category merge workflow that previews affected records before applying. Applying a merge SHALL move source category references to the target category, synchronize legacy category strings, and remove the source category only after references are migrated.

#### Scenario: 合併 preview

- **WHEN** the caller requests a preview for merging source category A into target category B
- **THEN** the response includes affected transaction and subscription counts
- **AND** no database rows are modified

#### Scenario: 合併 apply

- **WHEN** the caller applies merging source category A into target category B
- **THEN** transactions and subscriptions previously referencing A reference B
- **AND** legacy transaction category strings match B's name
- **AND** source category A is deleted after references are migrated
