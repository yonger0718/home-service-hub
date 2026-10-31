## ADDED Requirements

### Requirement: Shared inclusion rules

Report endpoints and the month and daily summaries SHALL use one rule set: expense is the negative `expense` and `fee` amounts plus every refund; income is the positive `income`, `reward`, `interest` and `discount` amounts; transfers, receivables, payables, settlements and balance adjustments are excluded; amounts are converted to the main currency at the latest cached rate, and currencies without a rate are left out and listed in `missing_rates`.

#### Scenario: Same figure on the timeline and in reports
- **GIVEN** no project is excluded from reports
- **WHEN** `GET /entries/summary?month=2026-10` and `GET /reports/breakdown?date_from=2026-10-01&date_to=2026-10-31&group_by=category` are read
- **THEN** their expense totals SHALL be equal

#### Scenario: Refund reduces its category
- **GIVEN** a 1,590 UNIQLO purchase and a 490 refund in 購物 in October
- **WHEN** the October breakdown by category is read
- **THEN** the 購物 row SHALL have `amount` 1,100 and `refund` 490, and `totals.refund` SHALL be 490

### Requirement: Breakdown endpoint

`GET /reports/breakdown` SHALL take `date_from`, `date_to`, `metric` (`expense`, `income`, `net`, `count`), `group_by`, an optional `split_by` different from `group_by`, the `/entries` filters plus `weekday[]` and `project_id[]`, and an optional `compare` (`previous` or `last_year`). It SHALL return totals and rows sorted by amount (time dimensions by key), each with label, colour where the dimension has one, amount (net of refunds), refund, count, average, and splits when `split_by` is set.

#### Scenario: Group by merchant with no merchant
- **WHEN** October expense is grouped by merchant and nine entries have no merchant
- **THEN** one row SHALL have `key: null` with those nine entries' sum and count

#### Scenario: Split by subcategory
- **WHEN** the request groups by month and splits by subcategory with `category_id=餐飲`
- **THEN** each month row SHALL carry splits for 晚餐, 午餐 and 宵夜 that sum to the row

#### Scenario: Compare with last year
- **WHEN** `compare=last_year` is sent for 2026-10
- **THEN** the response SHALL include October 2025 totals and each row's `compare_amount`

#### Scenario: Invalid split
- **WHEN** `group_by=tag&split_by=tag` is sent
- **THEN** the endpoint SHALL answer 422

### Requirement: Row statistics

For groups numbering 50 or fewer, each breakdown row SHALL include `median`, `min`, `max`, `first_date`, `last_date` and `avg_interval_days` over its entries; above 50 groups these SHALL be null. More than 200 groups SHALL fold the remainder into one `__other__` row.

#### Scenario: Coffee shop statistics
- **GIVEN** 12 路易莎 entries in October between 65 and 120
- **WHEN** October expense is grouped by merchant
- **THEN** the 路易莎 row SHALL report count 12, min 65, max 120 and its median and average interval

### Requirement: Overlapping tag totals

When `group_by` or `split_by` is `tag`, an entry with several tags SHALL count once in each of its tags and the response SHALL set `overlapping: true`.

#### Scenario: Two tags on one entry
- **GIVEN** a 600 dinner tagged 朋友 and 生日
- **WHEN** October is grouped by tag
- **THEN** both rows SHALL include 600 and `overlapping` SHALL be true

### Requirement: Trend endpoint

`GET /reports/trend` SHALL take the breakdown filters and `metric`, a `granularity` (`month`, `week` starting Monday, `day`) and optional `series` category ids, and SHALL return every bucket in the range (empty ones as 0) with one value per series and each series' average.

#### Scenario: Twelve months of food
- **WHEN** 2025-11 to 2026-10 is requested by month with `series=餐飲`
- **THEN** twelve buckets SHALL be returned with the 餐飲 value and its 12-month average

### Requirement: Project report exclusion

Projects SHALL carry `exclude_from_reports` (default false), editable through the project endpoints. Report endpoints SHALL leave out entries of excluded projects unless `include_excluded_projects=true`. The month and daily summaries SHALL keep including them.

#### Scenario: Trip left out of October
- **GIVEN** 東京旅行 is excluded and holds 24,860 of October expense
- **WHEN** October is read from `/reports/breakdown` without the flag
- **THEN** the total SHALL not include the 24,860, and SHALL include it when the flag is true

### Requirement: Budgets

The service SHALL store budgets with a scope (`total`, a top-level `category`, or a `project`), an amount in the main currency and, for monthly scopes, the first month they apply from; a new row for the same scope SHALL take over from its month without changing earlier months. `GET /reports/budgets/status?month=` SHALL return for each budget its limit, used (expense rule), remaining, ratio, `over` and the month's `time_progress`; a project budget's used SHALL cover the project's whole life.

#### Scenario: Over budget
- **GIVEN** a 購物 budget of 3,000 from 2026-09 and October 購物 expense of 3,490
- **WHEN** the October status is read
- **THEN** 購物 SHALL report used 3,490, remaining −490 and `over: true`

#### Scenario: Changing the amount keeps history
- **GIVEN** 餐飲 is 10,000 from 2026-01 and the owner sets 12,000 from 2026-11
- **WHEN** October and November statuses are read
- **THEN** October's limit SHALL be 10,000 and November's 12,000

### Requirement: Saved reports

The service SHALL store saved reports (unique name up to 40 characters, a versioned JSON config, chart `bar`/`line`/`donut`/`table`, pinned, order) with list, create, update, reorder and delete endpoints. The config SHALL be validated for known keys, a split different from the group, and existing ids; ids that no longer exist SHALL be dropped on read and listed in `stale_refs`.

#### Scenario: Save a relative period
- **WHEN** a report is saved with `period: {preset: "last_6_months"}`
- **THEN** reading it back SHALL return the preset, not resolved dates

#### Scenario: Deleted category in a saved report
- **GIVEN** a saved report filters on category 41, which the owner then deletes
- **WHEN** the report is read
- **THEN** `filters.category_id` SHALL omit 41 and `stale_refs` SHALL list it
