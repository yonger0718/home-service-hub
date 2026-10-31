## Context

- `ledger_service._converted_totals` defines what the month summary counts: expense = negative `expense`/`fee` amounts plus every refund; income = positive `income`/`reward`/`interest`/`discount` amounts; transfers, receivables/payables (settlements included) and balance adjustments excluded; amounts converted to the main currency at the latest cached rate, currencies without a rate left out and listed in `missing_rates`. `_category_expense` rolls expense up to top-level categories.
- `GET /entries` already filters by `account_id[]`, `category_id[]` (with descendants), `project_id`, `tag[]` (any), `merchant` (substring), `counterparty_id`, `amount_min/max` (account currency), `q`, dates.
- Projects (`project`: name, archived, order, moze_id) have no budget or report flag. There is no budget concept anywhere.

## Decisions

### D1. Two read endpoints, one rule set

Extract the inclusion rules into `report_rules.py` (filters + kind predicates + conversion) and make `month_summary`, `daily_summary` and the new endpoints use it, so a figure on the timeline equals the same figure in 報表 (as long as no project is excluded, D2).

`GET /reports/breakdown`

| Param | Meaning |
|---|---|
| `date_from`, `date_to` | inclusive, by `entry_date` |
| `metric` | `expense` (default), `income`, `net`, `count` |
| `group_by` | `category` (top level), `subcategory`, `account`, `project`, `merchant`, `tag`, `counterparty`, `month`, `week`, `weekday`, `day` |
| `split_by` | optional, any `group_by` value except the same one |
| filters | the `/entries` filters plus `weekday[]` (1 = Monday) and `project_id[]` (several) |
| `compare` | optional `previous` (same length, immediately before) or `last_year` |
| `include_excluded_projects` | default false; true when the report filters on projects |

Response:

```json
{"currency": "TWD", "date_from": "2026-10-01", "date_to": "2026-10-31",
 "totals": {"expense": "41890", "income": "68126", "net": "26236", "count": 87, "refund": "490"},
 "compare": {"date_from": "2026-09-01", "date_to": "2026-09-30", "totals": {"expense": "39608"}},
 "rows": [{"key": "12", "label": "餐飲", "color": "#d38a5c", "amount": "9860", "refund": "0", "count": 46,
           "avg": "214", "median": "120", "min": "45", "max": "1280",
           "first_date": "2026-10-01", "last_date": "2026-10-30", "avg_interval_days": 0.6,
           "compare_amount": "8640",
           "splits": [{"key": "31", "label": "晚餐", "amount": "4280", "count": 11}]}],
 "overlapping": false, "missing_rates": []}
```

- Rows are sorted by `amount` descending (`month`/`week`/`day`/`weekday` by key ascending), at most 200; the rest fold into one row `{"key": "__other__"}`.
- Entries without the dimension (no merchant, no tag, no project, no counterparty) form a row with `key: null`, labelled by the client (（無商家）…).
- `tag`: an entry with several tags counts in each; the response sets `overlapping: true` so the client can say totals exceed the period.
- Refunds: in `expense` a row's `amount` is net of the refunds in it (as the month summary does), and `refund` carries their sum; `totals.refund` is the period's. The overview draws the donut on gross (`amount + refund`, labelled 未扣退款) and lists 退款 −N as its own line, so the KPI (net) and the donut add up.
- Statistics (`median`, `avg_interval_days`) are computed only when the group count ≤ 50, otherwise null.

`GET /reports/trend`: same filters and `metric`, plus `granularity` (`month`, `week`, `day`) and optional `series` (a list of category ids; one series each, else one series for everything). Response `{buckets: [{start, values: {<series>: amount}}], average: {<series>: amount}}`; empty buckets are present with 0.

### D2. Excluded projects

`project.exclude_from_reports` (default false). Entries of such projects are left out of every report endpoint unless `include_excluded_projects=true`, which the client sets whenever the report filters on projects. The month summary and the timeline are unchanged and keep counting them: the timeline lists entries, while 報表 answers "what did ordinary months cost". The 報表 header names the excluded projects so the difference is visible.

### D3. Budgets

Table `budget`: `id`, `scope` (`total` | `category` | `project`), `category_id` (scope category, top-level only), `project_id` (scope project), `amount` (main currency, > 0), `effective_from` (first month, `YYYY-MM`, scopes total/category), `archived`. A monthly budget applies from `effective_from` until a newer row for the same scope starts; changing an amount inserts a new row so past months keep their limit. No rollover.

`GET /reports/budgets/status?month=YYYY-MM` → per budget `{limit, used, remaining, ratio, over}` plus `time_progress` (elapsed days / days in month, 1.0 for past months). `used` uses the expense rule (refunds reduce it). Project budgets report `used` over the project's whole life.

### D4. Saved reports

Table `saved_report`: `id`, `name` (unique, ≤ 40), `config` (JSONB), `chart` (`bar` | `line` | `donut` | `table`), `pinned`, `sort_order`, timestamps. `config` is versioned:

```json
{"v": 1, "period": {"preset": "last_6_months"}, "metric": "expense", "group_by": "month",
 "split_by": "subcategory", "filters": {"category_id": [12], "weekday": [6, 7]}, "compare": "last_year"}
```

Presets: `this_month`, `last_month`, `last_6_months`, `last_12_months`, `this_year`, `last_year`, `all`, or `{"from", "to"}`. The client resolves presets to dates on every open (relative periods roll forward). The server validates the shape (known keys, `split_by != group_by`, ids that exist) and stores it; chart suitability is a client rule (D6).

### D5. Navigation and routes

`/accounting/reports` (tabs via `?tab=overview|trend|rank|plan`), `/accounting/reports/custom/new`, `/accounting/reports/custom/:id`. Sidebar: 財務 › 報表 between 帳戶 and 記帳設定. Phone sub-nav: 記錄 / 帳戶 / 報表 / 記帳設定. Pinned saved reports render as extra tabs after the built-in four on desktop and as a chip row under the tabs on phones.

### D6. Chart rules (client)

- 圓環 only with one dimension and no split, and not for `month`/`week`/`day`.
- 折線 only for time dimensions.
- Every chart renders its table underneath (reports page) or beside it (editor). Charts have `role="img"` and an `aria-label` with the values.
- Spend comparisons use ▲ / ▼ and `--app-spend-more` / `--app-spend-less`; never the gain/loss tokens.

### D7. Drill-through

Every row offers 在記帳記錄看這 N 筆, opening `/accounting` with the same date range and filters as query parameters (the shared filter bar reads them).

### D8. Performance

Aggregation happens in SQL grouped by the dimension (and split), currency and, for trends, the bucket; conversion happens on the grouped sums. Statistics that need individual rows (median, interval) fetch amounts and dates only for groups within the 50-group limit. Target: a 12-month breakdown on 20k entries under 300 ms on the Oracle host.

## Risks / Trade-offs

- Latest-rate conversion means a past month's TWD figure moves with today's rates for foreign-currency accounts; same as the month summary today, called out in the header.
- Tag overlap makes tag totals exceed the period; the response flag and the UI note keep that honest.
- Saved configs reference ids; deleting a category leaves a dangling id. The server drops unknown ids on read and returns `stale_refs` so the editor can show 「部分條件已失效」.

## Migration Plan

1. PR A — `report_rules.py` extraction (month summary unchanged; existing tests green), `/reports/breakdown` and `/reports/trend` with tests.
2. PR B — 報表 page: 總覽 and 趨勢與比較; navigation; spend tokens.
3. PR C — 排行 with the statistics panel; drill-through.
4. PR D — budgets (table, CRUD, status) and `exclude_from_reports`; 專案與預算 tab; 記帳設定 › 專案 gets the flag.
5. PR E — saved reports (table, CRUD) and the editor; pinned tabs; phone viewer and sheet.

Alembic: one revision adds `budget`, `saved_report` and `project.exclude_from_reports` (PR D and PR E can share it if they land together; otherwise two revisions).

## Open Questions

- Should budgets also apply to income (savings goals)? Out of scope here.
- Week buckets start on Monday; confirm with the owner.
