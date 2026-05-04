## ADDED Requirements

### Requirement: 年度趨勢報表 endpoint

The system SHALL provide `GET /api/accounting/transactions/report/annual/{year}`. The endpoint SHALL return monthly income/expense/surplus trend, category expense trend, and annual summary for the requested calendar year. The endpoint MUST be read-only and MUST NOT generate recurring transactions.

#### Scenario: 查詢有資料年度

- **WHEN** `GET /api/accounting/transactions/report/annual/2026` is requested
- **THEN** the response is 200
- **AND** the body contains `year`, `monthly_trend`, `category_trend`, and `summary`

#### Scenario: 查詢無資料年度

- **WHEN** the requested year has no transactions
- **THEN** `monthly_trend` contains 12 entries with zero amounts
- **AND** `category_trend` is an empty array
- **AND** `summary.total_income = 0`
- **AND** `summary.total_expense = 0`
- **AND** `summary.highest_expense_month = null`
- **AND** `summary.lowest_expense_month = null`

#### Scenario: 報表查詢不產生 recurring

- **WHEN** active recurring items exist but have not generated transactions for the requested year/month
- **AND** the annual report endpoint is requested
- **THEN** no new transaction rows are inserted by the report query

### Requirement: monthly_trend 結構

`monthly_trend` SHALL include one entry per visible month in the requested year. For completed years it SHALL contain 12 entries from January through December. For the current calendar year it SHALL contain year-to-date entries from January through the current month only. Each entry SHALL include `month: "YYYY-MM"`, `total_income: int`, `total_expense: int`, and `surplus: int`. Missing months inside the visible range SHALL be represented with zero amounts.

#### Scenario: 部分月份無交易

- **WHEN** only January and August have transactions
- **THEN** `monthly_trend` still contains the full visible range for that year
- **AND** months without transactions inside that range contain zero amounts

#### Scenario: 當年度 year-to-date

- **WHEN** the requested year is the current calendar year and today is May
- **THEN** `monthly_trend` contains 5 entries from January through May
- **AND** months after May are not included in the response

### Requirement: category_trend 結構

`category_trend` SHALL contain one entry per expense category. Each entry SHALL include `category: str`, `monthly_amounts: int[12]`, `total: int`, and `average: int`. The array SHALL be sorted by `total` descending. The backend SHALL return all categories; frontend MAY display only top N categories.

#### Scenario: 分類年度趨勢

- **WHEN** category "餐飲" has expense amounts in January, May, and December only
- **THEN** only indexes 0, 4, and 11 in `monthly_amounts` contain non-zero values
- **AND** `total` equals the sum of all 12 months
- **AND** `average = total // 12`

#### Scenario: 分類排序

- **WHEN** multiple categories exist
- **THEN** `category_trend` is sorted by `total` descending

### Requirement: AnnualSummary 結構

`summary` SHALL include `total_income: int`, `total_expense: int`, `surplus: int`, `savings_rate: float`, `highest_expense_month: str | null`, and `lowest_expense_month: str | null`. `savings_rate` SHALL equal `surplus / total_income * 100` when `total_income > 0`, otherwise `0.0`.

#### Scenario: 最高與最低支出月

- **WHEN** December has the highest positive expense
- **AND** June has the lowest positive expense
- **THEN** `highest_expense_month = "YYYY-12"`
- **AND** `lowest_expense_month = "YYYY-06"`

#### Scenario: 全年無支出

- **WHEN** every month has expense 0
- **THEN** `highest_expense_month = null`
- **AND** `lowest_expense_month = null`

### Requirement: 分類名稱來源

Annual report category names SHALL use `Transaction.category_info.name` when available and fall back to legacy `Transaction.category` only when no valid category relationship exists.

#### Scenario: 分類已改名

- **WHEN** a transaction references a category id whose current name is "外食"
- **AND** the legacy transaction category string is stale
- **THEN** annual `category_trend` uses "外食"

### Requirement: 一次性年度查詢

`get_annual_report` SHALL fetch the requested year's transactions using a single year-scoped transaction query and aggregate by month/category in application code. It MUST NOT issue one query per month.

#### Scenario: 查詢數量

- **WHEN** `get_annual_report(db, 2026)` runs
- **THEN** transaction SELECT query count is constant and not 12 month-by-month queries

### Requirement: 金額型別

All amount fields in `AnnualReport` SHALL be JSON integers. `savings_rate` SHALL be a JSON number and MAY contain decimals.

#### Scenario: JSON 序列化

- **WHEN** the annual report response is serialized
- **THEN** amount fields do not contain `.0`
- **AND** `savings_rate` may contain decimal precision
