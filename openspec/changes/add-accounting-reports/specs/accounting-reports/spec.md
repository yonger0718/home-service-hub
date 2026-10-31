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

#### Scenario: Daily summary adds up to the day report
- **GIVEN** no project is excluded from reports
- **WHEN** `GET /entries/summary/daily?month=2026-10` and `GET /reports/breakdown?date_from=2026-10-01&date_to=2026-10-31&group_by=day` are read
- **THEN** the negated sum of the daily `expense` figures SHALL equal the breakdown `totals.expense` within 0.0005 per day of the month, and each day row's `amount` SHALL equal that day's negated daily `expense`

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

#### Scenario: Validation errors
- **WHEN** `date_from` follows `date_to`, or an unknown `group_by`, `split_by`, `metric`, `compare` or `granularity` is sent
- **THEN** the endpoint SHALL answer 422

#### Scenario: Positive magnitudes on the wire
- **WHEN** any `/reports/*` response is read
- **THEN** `totals.expense`, `rows[].amount`, `refund`, `avg`, `median`, `min`, `max`, trend values and budget `limit`/`used`/`remaining` SHALL be positive magnitudes as decimal strings with at most 4 decimals, `net` SHALL be `income − expense` (possibly negative) and `count` an integer, while `GET /entries/summary` keeps its negative expense

#### Scenario: Expense rows come from spend rows only
- **GIVEN** 薪資 only has income in October and a group only has a refund
- **WHEN** October is grouped by category with `metric=expense`
- **THEN** 薪資 SHALL not appear as a 0 row, the refund-only group SHALL stay with a negative `amount`, `metric=income` rows SHALL come from the income rows only, `net` and `count` rows from every counted row, and `totals` SHALL always cover every counted row

#### Scenario: Count metric
- **WHEN** `metric=count` is sent
- **THEN** each row's `amount` SHALL be its count as a string, its `avg` SHALL be `1`, its `refund` `"0"`, its statistics null, and `totals` SHALL still carry the expense, income, net, refund and count of the counted rows

#### Scenario: Average is amount over count
- **WHEN** a breakdown has more than 50 groups or folds rows into `__other__`
- **THEN** every row's `avg` SHALL still be `amount / count` rounded to 4 decimals and SHALL never be null; for metrics other than `expense` the row `refund` SHALL be `"0"`

#### Scenario: Row keys and labels
- **WHEN** a breakdown is grouped by each dimension
- **THEN** `category`, `subcategory`, `account`, `project` and `counterparty` keys SHALL be the id as text, `merchant` and `tag` keys the text itself, `month` `YYYY-MM`, `week` and `day` `YYYY-MM-DD`, `weekday` `"1"` to `"7"` with 1 = Monday; a `null` key SHALL mean no value for the dimension and carry `label: ""`; time dimensions SHALL never have a null key
- **AND** category and subcategory rows SHALL carry the category name and colour (a subcategory row is the entry's own category, which may itself be top-level), account rows the account name and colour, project and counterparty rows the name with `color: null`

#### Scenario: Weeks start on Monday
- **WHEN** `group_by=week` is read for 2026-10-05 to 2026-10-18
- **THEN** the row keys SHALL be the Mondays `2026-10-05` and `2026-10-12` (`date_trunc('week', entry_date)`), whatever the stored `week_start` preference

### Requirement: Row statistics

For groups numbering 50 or fewer, each breakdown row SHALL include `median`, `min`, `max`, `first_date`, `last_date` and `avg_interval_days` over its entries; above 50 groups these SHALL be null. More than 200 groups SHALL fold the remainder into one `__other__` row.

#### Scenario: Coffee shop statistics
- **GIVEN** 12 路易莎 entries in October between 65 and 120
- **WHEN** October expense is grouped by merchant
- **THEN** the 路易莎 row SHALL report count 12, min 65, max 120 and its median and average interval

#### Scenario: Remainder folded into __other__
- **GIVEN** a breakdown with more than 200 groups
- **WHEN** it is read
- **THEN** the first 200 rows by amount (time dimensions by key) SHALL be kept and the rest SHALL fold into one last row `{"key": "__other__", "label": "__other__"}` with summed `amount`, `refund` and `count`, `avg = amount / count`, null statistics, the folded rows' splits merged per split key and their `compare_amount` summed

#### Scenario: Day report over a long range
- **WHEN** a year is grouped by `day`
- **THEN** the days after the first 200 SHALL fold into `__other__`, while `GET /reports/trend` with `granularity=day` SHALL return the uncapped series

#### Scenario: Statistics leave refunds out of expense
- **GIVEN** 購物 holds a 1,590 purchase on 2026-10-13 and a 490 refund on 2026-10-20 in October
- **WHEN** October is grouped by category with `metric=expense`
- **THEN** the 購物 row SHALL keep `amount` 1,100, `refund` 490, `count` 2 and `avg` 550, while `median`, `min` and `max` SHALL be 1,590 and `first_date`, `last_date` and `avg_interval_days` SHALL come from the purchase alone (expense statistics cover the negative `expense` and `fee` rows only)
- **AND** with `metric=income` the statistics SHALL cover the income rows, and with `metric=net` every counted row signed (income positive, spending negative: 購物 `min` −1,590, `max` 490)

#### Scenario: Statistics only on few groups
- **WHEN** a breakdown has more than 50 groups or `metric=count`
- **THEN** `median`, `min`, `max`, `first_date`, `last_date` and `avg_interval_days` SHALL be null on every row

### Requirement: Overlapping tag totals

When `group_by` or `split_by` is `tag`, an entry with several tags SHALL count once in each of its tags and the response SHALL set `overlapping: true`.

#### Scenario: Two tags on one entry
- **GIVEN** a 600 dinner tagged 朋友 and 生日
- **WHEN** October is grouped by tag
- **THEN** both rows SHALL include 600 and `overlapping` SHALL be true

#### Scenario: Tags do not multiply other figures
- **GIVEN** a 600 dinner tagged 朋友 and 生日
- **WHEN** October is grouped by category, and then by category split by tag
- **THEN** the 600 SHALL count once in its category row and once in `totals.expense`, and SHALL produce two 600 tag splits; tags SHALL be unnested only to build the `tag` dimension

#### Scenario: Untagged entries
- **WHEN** October is grouped by tag
- **THEN** the entries with an empty tag list SHALL form one row with `key: null`, and `overlapping` SHALL be false for any request where neither `group_by` nor `split_by` is `tag`

### Requirement: Trend endpoint

`GET /reports/trend` SHALL take the breakdown filters and `metric`, a `granularity` (`month`, `week` starting Monday, `day`) and optional `series` category ids, and SHALL return every bucket in the range (empty ones as 0) with one value per series and each series' average.

#### Scenario: Twelve months of food
- **WHEN** 2025-11 to 2026-10 is requested by month with `series=餐飲`
- **THEN** twelve buckets SHALL be returned with the 餐飲 value and its 12-month average

#### Scenario: Trend response shape
- **WHEN** `GET /reports/trend` is read
- **THEN** it SHALL return `{currency, granularity, buckets: [{start, values}], average}`; without `series` each bucket's single value SHALL be keyed `"all"`, with `series` each value SHALL be keyed by the category id as text, and `average` SHALL be the sum of a series' values divided by the number of buckets, empty buckets included

#### Scenario: First bucket holds date_from
- **WHEN** `granularity=week` is requested from Wednesday 2026-10-07, or `granularity=month` from 2026-10-15
- **THEN** the first bucket SHALL start on Monday 2026-10-05, or on 2026-10-01, before `date_from`; only entries within `date_from`..`date_to` SHALL be counted

#### Scenario: Series must be top-level categories
- **WHEN** `series` names a subcategory or an unknown id
- **THEN** the endpoint SHALL answer 422

#### Scenario: Span caps
- **WHEN** a `day` trend spans more than 400 days, a `week` trend more than 1,100 days or a `month` trend more than 10 years
- **THEN** the endpoint SHALL answer 422 with a detail starting `span exceeds`

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

#### Scenario: Status fields
- **WHEN** `GET /reports/budgets/status?month=2026-10` is read
- **THEN** each budget SHALL carry `{id, scope, category_id, project_id, label, color, limit, used, remaining, ratio, over, effective_from}` where `id` is the row in force for the month (the non-archived row of its identity with the greatest `effective_from` not after the month), `label` is 全部支出, the category name or the project name, `color` the category colour or null, `ratio = used / limit` (4 decimals), `over = used > limit` and `remaining = limit − used`
- **AND** the month SHALL be the half-open window from its first day to the first day of the next month, and the budgets SHALL be ordered total first, then categories by name, then projects by name

#### Scenario: Time progress
- **GIVEN** today is 2026-10-15 in Asia/Taipei
- **WHEN** the statuses of 2026-10, 2026-09 and 2026-11 are read
- **THEN** `time_progress` SHALL be 15/31 (elapsed days, today included, over the days of the month), `1.0` for the past month and `0.0` for the future one

### Requirement: Saved reports

The service SHALL store saved reports (unique name up to 40 characters, a versioned JSON config, chart `bar`/`line`/`donut`/`table`, pinned, order) with list, create, update, reorder and delete endpoints. The config SHALL be validated for known keys, a split different from the group, and existing ids; ids that no longer exist SHALL be dropped on read and listed in `stale_refs`.

#### Scenario: Save a relative period
- **WHEN** a report is saved with `period: {preset: "last_6_months"}`
- **THEN** reading it back SHALL return the preset, not resolved dates

#### Scenario: Deleted category in a saved report
- **GIVEN** a saved report filters on category 41, which the owner then deletes
- **WHEN** the report is read
- **THEN** `filters.category_id` SHALL omit 41 and `stale_refs` SHALL list it

### Requirement: Report totals and currencies

Report `totals` and `compare.totals` SHALL be one sum per currency converted once per currency, so `totals.expense` equals the negated month summary expense exactly. Row amounts SHALL be converted per key and currency and MAY differ from the totals by rounding. `missing_rates` SHALL list the currencies of the counted rows that have no rate.

#### Scenario: Totals equal the month summary
- **GIVEN** October holds TWD and USD rows and the USD rate is 31.1234
- **WHEN** the October month summary and the October breakdown by category are read
- **THEN** `totals.expense` SHALL equal the negated summary expense exactly, while the sum of the rows MAY differ by the per-row rounding

#### Scenario: Missing rates on reports
- **GIVEN** a JPY row without a rate in October and a currency without a rate that appears only in September
- **WHEN** October is read with `compare=previous`
- **THEN** `missing_rates` SHALL be `["JPY"]` (the currencies of the counted rows of the requested range only, where the month summary lists any row's currency) and the September-only currency SHALL be left out of `compare.totals` without being listed

### Requirement: Comparison windows

With `compare`, the breakdown SHALL return `compare` with the comparison window's dates and its five totals, and each row's `compare_amount` SHALL be the same key's amount there or null when the key is absent. `previous` and `last_year` SHALL resolve the window as the scenarios below state.

#### Scenario: Previous for whole calendar months
- **WHEN** `compare=previous` is sent with a range starting on the 1st and ending on the last day of a month
- **THEN** the window SHALL move back by the range's number of calendar months and end the day before `date_from`: `2026-10-01..2026-10-31` → `2026-09-01..2026-09-30`, `2026-05-01..2026-10-31` → `2025-11-01..2026-04-30`

#### Scenario: Previous for any other range
- **WHEN** `compare=previous` is sent with `2026-10-05..2026-10-14`
- **THEN** the window SHALL have the same inclusive day count and end the day before `date_from`: `2026-09-25..2026-10-04`

#### Scenario: Last year
- **WHEN** `compare=last_year` is sent
- **THEN** both dates SHALL move back one year with Feb 29 becoming Feb 28: `2024-01-01..2024-12-31` → `2023-01-01..2023-12-31`, `2024-02-29` → `2023-02-28`

### Requirement: Budget writes

Budget identity SHALL be `(total)`, `(category, category_id)` or `(project, project_id)`. `POST`, `PUT /{id}` and `DELETE /{id}` under `/reports/budgets` SHALL create a budget, change its amount from a month on, and end it, as the scenarios below state. Archived rows SHALL never appear in status.

#### Scenario: Create
- **WHEN** `POST /reports/budgets` sends `{scope, category_id?, project_id?, amount, effective_from}`
- **THEN** the service SHALL answer 201 with the status of the budget for `effective_from`'s month; 409 only when a non-archived row of the same identity already starts in that month (several category budgets may start in the same month, and a deleted budget can be recreated in its old start month); 422 when the id its scope needs is absent, the category is not an existing top-level category, the project does not exist, `amount` is not above 0, or a `total`/`category` budget has no well-formed `effective_from` month
- **AND** a `project` budget SHALL store `effective_from` as null, and a second live budget for the same project SHALL answer 409

#### Scenario: Change the amount
- **WHEN** `PUT /reports/budgets/{id}` sends `{amount, effective_from}` for a `total` or `category` budget
- **THEN** a new row of the same identity SHALL start at `effective_from` (or the row already starting that month SHALL be updated in place), earlier months SHALL keep the old row, and the new row's status SHALL be returned
- **AND** for a `project` budget the one row's `amount` SHALL be updated in place and its `effective_from` SHALL stay null whatever month is sent

#### Scenario: Delete ends the budget
- **WHEN** `DELETE /reports/budgets/{id}` is sent
- **THEN** every row of that identity SHALL be marked archived (history kept) and the service SHALL answer 204; the budget SHALL no longer appear in any month's status

### Requirement: Saved report config

The saved report `config` SHALL be validated (422) as `v: 1`, a `period`, `metric`, `group_by`, an optional `split_by` different from `group_by`, `compare` and `filters`, and the order SHALL be a `sort_order` set on create or through `PUT`; there SHALL be no separate reorder endpoint. Names SHALL be unique (409).

#### Scenario: Period presets
- **WHEN** a config is saved
- **THEN** `period` SHALL be `{preset}` with one of `this_month`, `last_month`, `last_6_months`, `last_12_months`, `this_year`, `last_year`, `all`, or `{from, to}` with `from` not after `to`; anything else SHALL answer 422

#### Scenario: Wire filter keys
- **WHEN** `config.filters` is saved
- **THEN** it SHALL accept only the optional keys `account_id: int[]`, `category_id: int[]`, `project_id: int[]`, `tag: str[]`, `merchant: str | null`, `counterparty_id: int | null`, `amount_min: str | null`, `amount_max: str | null`, `q: str | null` and `weekday: int[]` (1–7); any other key, `include_excluded_projects` included, SHALL answer 422, and every referenced account, category, project and counterparty id SHALL exist

#### Scenario: Order and reorder
- **WHEN** a report is created without `sort_order`, and then another report is sent through `PUT` with a new `sort_order`
- **THEN** the first SHALL get the current maximum `sort_order` plus one (0 for the first report), `GET /reports/saved` SHALL list by `sort_order` then id, and `pinned` SHALL be required on every write

#### Scenario: Stale references format
- **GIVEN** a saved report filters on category 41 and account 7, both since deleted
- **WHEN** the report is read
- **THEN** `stale_refs` SHALL be `["account_id:7", "category_id:41"]` (`"<filter>:<id>"`, sorted), the returned `config.filters` SHALL omit both ids, and the stored config SHALL stay unchanged
