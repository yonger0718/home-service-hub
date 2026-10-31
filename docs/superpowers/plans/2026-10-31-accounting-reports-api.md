# Accounting Reports API Implementation Plan (add-accounting-reports PR A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the accounting-service side of `add-accounting-reports` — shared inclusion rules, `GET /reports/breakdown`, `GET /reports/trend`, budgets, saved reports and `project.exclude_from_reports` — so the already-built SPA stack (PRs #236–#239) stops answering with 示範資料.

**Architecture:** One new router `app/routers/reports.py` over one service `app/services/report_service.py`, both built on `app/services/report_rules.py`, which is extracted from today's `_converted_totals` / `_category_expense` / `_filters` / `_attribute_filters` so the month summary, the daily summary and the reports count the same rows. Aggregation is SQL grouped by (dimension[, split], currency); conversion to the main currency happens on the grouped sums with `latest_rates`. Budgets and saved reports are two small tables with their own CRUD; one Alembic revision adds both and `project.exclude_from_reports`.

**Tech Stack:** FastAPI, SQLAlchemy 2 (select API), Alembic, Pydantic v2, pytest against the disposable Postgres databases of `tests/conftest.py` (`db_session`, `client`, `today`, `database_factory`, `alembic_config`).

**Spec:** `openspec/changes/add-accounting-reports/` — `design.md` (D1–D8) and `specs/accounting-reports/spec.md` are the authority; `proposal.md` and `tasks.md` (items 1.1–1.5, 4.1–4.2, 5.1) frame it. The change lands on main with PR #235; until then read it from `origin/pr/235` (`git show origin/pr/235:openspec/changes/add-accounting-reports/design.md`). The SPA contract this API must satisfy verbatim is `frontend/src/app/models/reports.model.ts` on `origin/pr/239` (`git show origin/pr/239:frontend/src/app/models/reports.model.ts`) — where the spec is silent, that file decides (see "Contract decisions" below).

## Global Constraints

- Inclusion rules (spec "Shared inclusion rules"): expense = negative `expense`/`fee` amounts + every `refund` row; income = positive `income`/`reward`/`interest`/`discount` amounts; transfers, receivables/payables (settlements included) and balance adjustments excluded; main currency via `latest_rates`; currencies without a rate left out and listed in `missing_rates`. `month_summary` and `daily_summary` output must not change (their existing tests are the gate).
- Amounts in responses are **positive magnitudes as decimal strings** (`"41890"`, 4 dp max, no trailing-zero requirement): `totals.expense`, `rows[].amount`, `refund`, `avg`, `median`, `min`, `max`, trend values, budget `limit`/`used`/`remaining`. `net = income − expense` (may be negative). `count` is an integer. (`month_summary` keeps its negative expense; only `/reports/*` is positive.)
- Row `amount` for `metric=expense` is net of the refunds in the row; `refund` is their positive sum; gross = `amount + refund`. For other metrics `refund` is `"0"`.
- `date_from`/`date_to` inclusive by `entry_date`; `date_from > date_to` → 422. `split_by == group_by` → 422. Unknown `group_by`/`split_by`/`metric`/`compare`/`granularity` → 422 (Pydantic/Literal). Trend span caps: `day` ≤ 400 days, `week` ≤ 1100 days, `month` ≤ 10 years → 422 `"span exceeds …"`.
- Excluded projects (D2): rows whose `project_id` is in `project.exclude_from_reports = true` are left out of every `/reports/*` figure unless `include_excluded_projects=true`; `project` budgets always include their own project. `month_summary`/`daily_summary` unchanged (they keep counting them).
- Row limit: rows sorted by `amount` desc (time dimensions by key asc), at most 200 kept; the remainder folds into one `{"key": "__other__", "label": "__other__"}` row with summed amount/refund/count and null statistics. Statistics (`median`, `min`, `max`, `first_date`, `last_date`, `avg_interval_days`) only when the number of groups ≤ 50 and `metric != count`; else null.
- Scopes: the router carries `dependencies=[Depends(method_scope())]` (GET → `read`, writes → `write`), exactly like `app/routers/calendar.py`.
- Alembic: one new revision with `down_revision = "d5e1f7a3b9c2"` (the current head). `tests/integration/test_migration.py` pins the head by constant: add `REPORTS_HEAD` and move the current-head assertions to it, plus a `test_reports_round_trip` (upgrade → tables + column present; downgrade to `d5e1f7a3b9c2` → gone). Run that file in the migration task.
- Decimal handling: SQL sums come back as `Decimal`; convert with `ledger_service._convert` (quantize 4 dp); serialize with Pydantic `Decimal` fields (the existing `MonthSummaryOut` pattern) — the SPA parses strings.
- Tests: every new endpoint has integration tests under `tests/integration/reports/` using the `client` fixture and ledger rows written through the existing write path (`POST /entries` or `ledger_service.create_entry` as `tests/integration/test_entries_api.py` does); pure helpers get unit tests under `tests/unit/reports/`. Backend suite command (DB env exported in-shell only, never into a file): `set -a; eval "$(grep -E '^(POSTGRES_[A-Z_]+|DB_HOST|ACCOUNTING_DB)=' /home/opc/workspace/home-hub/.env)"; set +a; export OTEL_SDK_DISABLED=true OTEL_SERVICE_NAME_ACCOUNTING=test OTEL_COLLECTOR_ENDPOINT_GRPC=http://127.0.0.1:1; .venv/bin/pytest -q <paths>` from `services/accounting-service`.
- Commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; `git add <paths>` explicitly; never `git stash`.

## Contract decisions (spec silent → SPA model decides; recorded here for the spec delta in Task 8)

| Topic | Decision |
|---|---|
| Trend response | `{currency, granularity, buckets: [{start, values}], average}`; the single series without `series` is keyed `"all"` (`TREND_ALL`); category series are keyed by the id as text (`"12"`). `average` = Σ values / number of buckets (empty buckets count). |
| Row keys | `category`/`subcategory`/`account`/`project`/`counterparty`: id as text; `merchant`/`tag`: the text itself; `month`: `YYYY-MM`; `week`: Monday's `YYYY-MM-DD`; `day`: `YYYY-MM-DD`; `weekday`: `"1"`…`"7"` (1 = Monday). `null` key = no value for the dimension (no category, merchant, tag, project, counterparty). Time dimensions never have a null key. |
| Labels / colours | category/subcategory: `Category.name`/`color` (subcategory = the entry's own category, which may itself be top-level); account: `Account.name`/`color`; project/counterparty: name, `color: null`; merchant/tag: the text; month `2026-10`, week `2026-10-06`, day `2026-10-06`, weekday `"1"`–`"7"` (client labels 週一…). Null-key rows: `label: ""` (client labels （無商家） etc.). |
| `metric=count` | `amount` = the row's count as a string; `totals` still carry expense/income/net/refund of the counted rows. |
| `compare` | `compare.totals` carries all five totals of the comparison window; `rows[].compare_amount` is the same key's amount there (`null` when absent). `previous` = same length ending the day before `date_from`; `last_year` = both dates minus one year (Feb 29 → Feb 28). |
| `overlapping` | `true` whenever `group_by` or `split_by` is `tag`, else `false`. |
| Budget rows | `BudgetStatus` = `{id, scope, category_id, project_id, label, color, limit, used, remaining, ratio, over, effective_from}`; `id` is the row in force for the month; `label` = 全部支出 / category name / project name; `color` = category colour or null. `ratio = used / limit` (float, 4 dp); `over = used > limit`; `remaining = limit − used` (negative when over). `time_progress` = elapsed days (today included) / days in month, `1.0` for past months, `0.0` for future ones; "today" from `ledger_service.today()` (the `today` fixture in tests). |
| Budget writes | `POST /reports/budgets` (`BudgetInput` {scope, category_id?, project_id?, amount, effective_from}) → 201 + the `BudgetStatus` for `effective_from`'s month; 409 when a row for the same scope already starts in that month; 422 when the scope/ids are inconsistent (category not top-level, project missing, `amount ≤ 0`, bad month) — `project` scope ignores `effective_from` (stored null). `PUT /reports/budgets/{id}` `{amount, effective_from}` inserts a **new row of the same scope** starting at `effective_from` (updates in place when a row of that scope already starts that month) and returns its status; earlier months keep the old row. `DELETE /reports/budgets/{id}` sets `archived = true` on every row of that scope (the budget ends; history kept) → 204. Archived rows never appear in status. |
| Saved reports | `GET /reports/saved` (ordered by `sort_order, id`), `GET /{id}`, `POST` (201), `PUT /{id}`, `DELETE /{id}` (204, hard delete). Input `{name, config, chart, pinned, sort_order?}`; `sort_order` defaults to max + 1 on create. Reorder = `PUT` with `sort_order` (no separate endpoint; spec delta says so). `name` unique → 409. `config` validation (422): `v == 1`; `period` is `{preset}` with a known preset or `{from, to}` with `from ≤ to`; `metric`, `group_by`, `split_by` from the enums with `split_by != group_by`; `compare` enum; `filters` only the `ReportFilters` keys; every `category_id`/`account_id`/`project_id`/`counterparty_id` exists. On read, ids that no longer exist are dropped from `config.filters` and listed in `stale_refs` as `"<filter>:<id>"` (e.g. `"category_id:41"`); the stored row is not rewritten. |
| Project flag | `ProjectIn.exclude_from_reports: bool = False`, `ProjectOut.exclude_from_reports: bool`; `update_project` sets it like the other fields. The MOZE importer never touches it (locally set). |

## Review Focus

1. A refund dated in the window whose purchase is outside it: the row still nets it (amount can be negative-looking → clamp? No: report it as is — the spec's "net of refunds" has no floor); Task 3 asserts the signed arithmetic with a refund larger than the row's spending.
2. Foreign-currency rows with no cached rate: left out of totals and rows, listed once in `missing_rates`; Task 3 test.
3. A category deleted after a saved report referenced it: `GET /reports/saved/{id}` still answers (200) with the id dropped and `stale_refs` set; Task 7 test.
4. `split_by=tag` on rows that have no tags: the split list carries one `key: null` split; `tag` grouping with zero-tag entries → one null row; Task 4 tests.
5. `weekday[]` filter and `project_id[]` filter combined with `include_excluded_projects` — the flag only widens the project exclusion, it never bypasses `project_id[]`; Task 3 test.

---

### Task 1: Alembic revision, models and the project flag

**Files:**
- Create: `services/accounting-service/alembic/versions/<rev>_reports_budget_saved_report.py` (`revision = "c7a2e9f4b1d8"`, `down_revision = "d5e1f7a3b9c2"`)
- Modify: `services/accounting-service/app/models/ledger.py` (`Project` + two new models), `app/schemas/writes.py` (`ProjectIn`), `app/schemas/ledger.py` (`ProjectOut`), `app/services/settings_service.py` (`_project_dict`)
- Test: `tests/integration/test_migration.py`, `tests/integration/test_models_schema.py` (if it enumerates tables), `tests/integration/test_settings_crud_api.py`

**Interfaces:**
- Produces: `Project.exclude_from_reports: Column(Boolean, nullable=False, server_default=text("false"))`; `class Budget(Base)` table `budget`: `id`, `scope` (`String(16)`, check in (`total`,`category`,`project`)), `category_id` FK category nullable, `project_id` FK project nullable, `amount Numeric(20,4)` (check > 0), `effective_from String(7)` nullable (YYYY-MM), `archived Boolean default false`, `created_at`, `updated_at`; index `(scope, category_id, project_id, effective_from)`. `class SavedReport(Base)` table `saved_report`: `id`, `name String(40)` unique, `config JSONB`, `chart String(8)`, `pinned Boolean default false`, `sort_order Integer default 0`, `created_at`, `updated_at`.

- [ ] **Step 1: Write the failing tests** — in `test_migration.py`: add `REPORTS_HEAD = "c7a2e9f4b1d8"`, switch every current-head assertion (`_version(url) == ACCOUNT_LOGO_HEAD` after `upgrade head` — grep them) to `REPORTS_HEAD`, and add

```python
def test_reports_round_trip(database_factory, alembic_config):
    url = database_factory(); cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    assert _version(url) == REPORTS_HEAD
    assert _table_present(url, "budget") and _table_present(url, "saved_report")
    assert _column(url, "project", "exclude_from_reports")["nullable"] is False
    command.downgrade(cfg, PREVIOUS_HEAD)  # the constant that names d5e1f7a3b9c2 in this file today (grep it)
    assert not _table_present(url, "budget") and _column(url, "project", "exclude_from_reports") is None
    command.upgrade(cfg, "head")
```
(write `_table_present(url, name)` / `_column(url, table, name)` helpers beside `_account_logo_column`). In `test_settings_crud_api.py`: `test_project_exclude_from_reports_round_trip` — POST a project without the field → `exclude_from_reports` false; PUT with `true` → true; GET list shows it.
- [ ] **Step 2: Run them** — `.venv/bin/pytest tests/integration/test_migration.py tests/integration/test_settings_crud_api.py -q` → the new tests fail (unknown revision / unknown field).
- [ ] **Step 3: Implement** the revision (`op.add_column("project", sa.Column("exclude_from_reports", sa.Boolean(), nullable=False, server_default=sa.text("false")))`, `op.create_table("budget", …)`, `op.create_table("saved_report", …)`, downgrade reverses), the two models, `ProjectIn.exclude_from_reports: bool = False`, `ProjectOut.exclude_from_reports: bool`, and `_project_dict` carrying it.
- [ ] **Step 4: Run** the two files plus `tests/integration/test_models_schema.py tests/integration/test_backup_settings_import.py -q` → all pass (the backup importer must keep working with the new column defaulting to false).
- [ ] **Step 5: Commit** `feat(accounting): budget and saved_report tables, project.exclude_from_reports (reports PR A)`

### Task 2: `report_rules.py` — one rule set for summaries and reports

**Files:**
- Create: `services/accounting-service/app/services/report_rules.py`
- Modify: `app/services/ledger_service.py` (`_spend_filter`, `_converted_totals`, `_category_expense` import from the new module; behaviour unchanged)
- Test: `tests/unit/reports/test_report_rules.py` (new; create `tests/unit/reports/__init__.py` only if the other unit dirs have one), existing `tests/integration/test_entries_api.py` (summary tests) stay green

**Interfaces:**
- Produces, in `report_rules.py`:
  - `EXPENSE_KINDS`, `INCOME_KINDS` re-exported; `spend_filter()` (moved `_spend_filter`), `income_filter(hide_rewards: bool = False)`, `counted_filter(hide_rewards=False)` = `or_(spend_filter(), income_filter(...))`.
  - `Metric = Literal["expense", "income", "net", "count"]`, `Dimension = Literal["category","subcategory","account","project","merchant","tag","counterparty","month","week","weekday","day"]`, `Granularity = Literal["month","week","day"]`, `Compare = Literal["previous","last_year"]`.
  - `@dataclass(frozen=True) class ReportFilters: date_from: date; date_to: date; account_ids: list[int] | None = None; category_ids: list[int] | None = None; project_ids: list[int] | None = None; tags: list[str] | None = None; merchant: str | None = None; counterparty_id: int | None = None; amount_min: Decimal | None = None; amount_max: Decimal | None = None; q: str | None = None; weekdays: list[int] | None = None; include_excluded_projects: bool = False`.
  - `entry_filters(db, f: ReportFilters) -> list` = date bounds + `ledger_service._filters`-equivalent pieces (q, account_ids, counterparty_id) + `_attribute_filters`-equivalent (tags overlap, category closure, merchant, amount bounds) + `LedgerEntry.project_id.in_(project_ids)` + `extract("isodow", LedgerEntry.entry_date).in_(weekdays)` + `excluded_projects_filter(db)` unless `include_excluded_projects`.
  - `excluded_projects_filter(db) -> ColumnElement`: `or_(LedgerEntry.project_id.is_(None), LedgerEntry.project_id.not_in(select(Project.id).where(Project.exclude_from_reports.is_(True))))`.
  - `compare_window(date_from, date_to, compare: Compare) -> tuple[date, date]`; `period_bounds(month: str) -> tuple[date, date]` (moved `_month_bounds`); `convert(amount, rate)` (re-export of `_convert`); `week_start(d: date) -> date` (Monday).
- Consumes: `ledger_service.main_currency`, `latest_rates`, `_category_closure`, `_like_pattern`, `_text_filter` (import them; do not duplicate).

- [ ] **Step 1: Unit tests** in `test_report_rules.py`: `compare_window(2026-10-01, 2026-10-31, "previous") == (2026-09-01, 2026-09-30)`; `compare_window(2026-10-05, 2026-10-14, "previous") == (2026-09-25, 2026-10-04)`; `compare_window(2024-02-29, 2024-02-29, "last_year") == (2023-02-28, 2023-02-28)`; `week_start(date(2026,10,8)) == date(2026,10,5)`; `period_bounds("2026-12") == (date(2026,12,1), date(2027,1,1))`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/unit/reports -q` → ImportError.
- [ ] **Step 3: Implement** the module; rewire `ledger_service._spend_filter = report_rules.spend_filter` (keep the old name as an alias so `period_summary` and tests keep working), `_month_bounds` → `report_rules.period_bounds`.
- [ ] **Step 4: Run** `tests/unit/reports tests/integration/test_entries_api.py tests/integration/test_entry_writes.py -q` → green; `git diff` of `ledger_service.py` is imports + aliases only.
- [ ] **Step 5: Commit** `refactor(accounting): report_rules.py holds the summary inclusion rules (reports PR A)`

### Task 3: `GET /reports/breakdown` — grouping, totals, compare, fold

**Files:**
- Create: `app/services/report_service.py`, `app/schemas/reports.py`, `app/routers/reports.py`, `tests/integration/reports/__init__.py` (empty, mirrors existing dirs if they use one), `tests/integration/reports/conftest.py` (a `ledger` fixture that seeds accounts/categories/entries through the API — see Step 1), `tests/integration/reports/test_breakdown_api.py`
- Modify: `app/main.py` (add `reports.router` to the `routers=[…]` list)

**Interfaces:**
- Produces: `report_service.breakdown(db, f: ReportFilters, *, metric: Metric, group_by: Dimension, split_by: Dimension | None, compare: Compare | None) -> dict` shaped as `BreakdownOut`; schemas `BreakdownRowOut`, `BreakdownSplitOut`, `BreakdownTotalsOut`, `BreakdownCompareOut`, `BreakdownOut` matching `reports.model.ts` field for field (Decimal fields; `key: str | None`; `splits: list[BreakdownSplitOut] | None`; `compare_amount: Decimal | None = None`; statistics fields present and `None` in this task).
- Router: `router = APIRouter(prefix="/reports", tags=["Reports"], dependencies=[Depends(method_scope())])`; `GET /breakdown` with `date_from: date`, `date_to: date`, `metric: Metric = "expense"`, `group_by: Dimension`, `split_by: Dimension | None = None`, `compare: Compare | None = None`, `include_excluded_projects: bool = False`, and the filters as on `/entries` (`account_id: list[int] | None = Query(None)`, `category_id`, `project_id: list[int] | None`, `tag`, `merchant` (max_length 100), `counterparty_id`, `amount_min`, `amount_max`, `q` (max_length 100), `weekday: list[int] | None = Query(None)` each `ge=1, le=7`).
- Grouping SQL: one `select` over `LedgerEntry` with `entry_filters` + `counted_filter()`, grouped by `(group_key, split_key?, currency)` with `sum(amount).filter(spend_filter())`, `sum(amount).filter(LedgerEntry.kind == "refund")`, `sum(amount).filter(income_filter())`, `count(id)`; key expressions per dimension: category → root id via the `_category_expense` outer-join trick; subcategory → `LedgerEntry.category_id`; account/project/counterparty → the FK; merchant → `LedgerEntry.merchant`; tag → `func.unnest(LedgerEntry.tags)` via a lateral/`select_from(LedgerEntry, func.unnest(...).alias())` (rows with no tags via a `coalesce(nullif(array_length(tags,1),0)…)` left join, or a second UNION query for the null row — implementer's call, test-pinned); month → `func.to_char(entry_date, 'YYYY-MM')`; week → `func.date_trunc('week', entry_date)` (Postgres weeks start Monday); day → `entry_date`; weekday → `extract('isodow', entry_date)`. Labels/colours resolved after grouping with one query per id-dimension.
- Totals: `expense = −Σspend` (positive), `income`, `net = income − expense`, `count`, `refund = Σrefund rows`; per row `amount` by metric (expense: `−(spend)` i.e. net of refunds; income; net; count); `missing_rates` from currencies without a rate.

- [ ] **Step 1: Fixture + tests.** `conftest.py` fixture `ledger(client, today)` creates: accounts 錢包 (TWD) and 日幣 (JPY, no rate cached → missing rate case), categories 餐飲 (children 晚餐, 午餐), 購物, 交通; project 東京旅行; entries in 2026-10 and 2026-09 and 2025-10 written via `POST /entries` (amounts negative for expense, kinds `expense`, a `refund` of 490 in 購物 dated 2026-10-20 referencing a 1590 purchase, one `income` 68126, one `transfer` pair, one JPY expense). Tests in `test_breakdown_api.py`:
  - `test_totals_match_month_summary`: `GET /entries/summary?month=2026-10` expense == −`totals.expense` of `GET /reports/breakdown?date_from=2026-10-01&date_to=2026-10-31&group_by=category` (as Decimal); `missing_rates == ["JPY"]` on both.
  - `test_refund_nets_its_category`: 購物 row `amount == "1100.0000"` (or Decimal-equal), `refund == "490.0000"`, `totals.refund == "490.0000"`.
  - `test_group_by_merchant_null_row`: entries without merchant form one row `key is None` with their sum and count.
  - `test_split_by_subcategory_sums_to_row`: `group_by=month&split_by=subcategory&category_id=<餐飲>` → each row's splits (晚餐, 午餐) sum to the row amount.
  - `test_compare_last_year`: `compare=last_year` → `compare.date_from == "2025-10-01"`, `compare.totals.expense` equals the 2025-10 spending, each row has `compare_amount` (null where absent).
  - `test_invalid_split_422`: `group_by=tag&split_by=tag` → 422; `date_from > date_to` → 422.
  - `test_excluded_project_left_out_unless_flagged`: PUT 東京旅行 `exclude_from_reports=true` → breakdown total drops its 24,860; with `include_excluded_projects=true` it is back; `project_id=<東京旅行>&include_excluded_projects=true` returns only it.
  - `test_weekday_filter`: `weekday=6&weekday=7` → only weekend rows.
  - `test_fold_over_200_merchants`: 205 one-off merchants → 200 rows + one `__other__` row whose count is 5.
  - `test_metric_count`: `metric=count` → `rows[].amount` equals the row's count as text.
  - `test_refund_larger_than_spend_stays_signed`: a 300 refund in 交通 with 100 spend → `amount == "-200.0000"`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/integration/reports -q` → 404 on `/reports/breakdown`.
- [ ] **Step 3: Implement** schemas, service (`breakdown`, `_group_rows`, `_resolve_labels`, `_fold`, `_compare`), router, `main.py` registration. Time keys sorted ascending; others by `amount` desc then key.
- [ ] **Step 4: Run** the reports tests + `tests/integration/test_entries_api.py -q` → green.
- [ ] **Step 5: Commit** `feat(accounting): GET /reports/breakdown — dimensions, splits, compare, excluded projects (reports PR A)`

### Task 4: Row statistics and tag overlap

**Files:**
- Modify: `app/services/report_service.py`, `app/schemas/reports.py` (no shape change; fields stop being always-null)
- Test: `tests/integration/reports/test_breakdown_stats_api.py`, `tests/unit/reports/test_statistics.py`

**Interfaces:**
- Produces: `report_service.row_statistics(amounts_dates: list[tuple[Decimal, date]]) -> dict` with `median`, `min`, `max`, `first_date`, `last_date`, `avg_interval_days` (float, 1 dp; `0.0` for a single entry; computed over the sorted distinct dates: `(last − first).days / (n − 1)` with n = number of entries, so same-day entries pull it down — matches the design example 0.6 for 46 entries in a month). Statistics are over **converted** magnitudes of the counted rows of that group (refund rows included as negative magnitudes for expense).
- `overlapping` = `group_by == "tag" or split_by == "tag"`.

- [ ] **Step 1: Tests.** Unit: `row_statistics([(100, d1), (300, d1), (200, d3)])` → median 200, min 100, max 300, first d1, last d3, `avg_interval_days == 1.0`; one entry → 0.0; empty → all None. Integration: `test_coffee_statistics` (12 路易莎 entries 65–120 on 12 distinct days → count 12, min 65, max 120, median = the two middle values' mean, `avg_interval_days` = (last−first)/11); `test_stats_null_above_50_groups` (51 merchants → every row's `median is None`); `test_tag_overlap` (a 600 dinner tagged 朋友 and 生日 → both rows include 600, `overlapping is True`; `group_by=category` → `False`); `test_tag_rows_without_tags_form_null_row`; `test_split_by_tag_null_split`.
- [ ] **Step 2: Run** → statistics None / overlap false.
- [ ] **Step 3: Implement**: after grouping, when `len(groups) ≤ 50` and metric != count, one query fetching `(group_key, currency, amount, entry_date)` for the counted rows, converted per row, bucketed by key → `row_statistics`. Tag dimension via `unnest`.
- [ ] **Step 4: Run** `tests/unit/reports tests/integration/reports -q` → green.
- [ ] **Step 5: Commit** `feat(accounting): breakdown row statistics and tag overlap (reports PR A)`

### Task 5: `GET /reports/trend`

**Files:**
- Modify: `app/services/report_service.py`, `app/schemas/reports.py` (`TrendBucketOut {start: date, values: dict[str, Decimal]}`, `TrendOut {currency, granularity, buckets, average: dict[str, Decimal]}`), `app/routers/reports.py`
- Test: `tests/integration/reports/test_trend_api.py`

**Interfaces:**
- Produces: `report_service.trend(db, f: ReportFilters, *, metric: Metric, granularity: Granularity, series: list[int] | None) -> dict`. Buckets enumerated from `date_from` (month → first of month; week → `week_start(date_from)`; day) to `date_to`, every bucket present; `values` keyed `"all"` or the top-level category id as text (a series category matches entries whose root category is that id — reuse the root expression from Task 3); `average[key] = Σ values / len(buckets)` quantized 4 dp.
- Router: `GET /trend` with the Task 3 filters plus `granularity: Granularity`, `series: list[int] | None = Query(None)`; span caps per Global Constraints → 422.

- [ ] **Step 1: Tests.** `test_twelve_months_of_food` (2025-11-01..2026-10-31, month, `series=<餐飲>` → 12 buckets, `values["<id>"]`, `average["<id>"]` = Σ/12, empty months present with `"0"` or `0`); `test_week_buckets_start_monday` (granularity week over 2026-10-05..2026-10-18 → starts 2026-10-05, 2026-10-12); `test_all_series_key` (no `series` → `values == {"all": …}`); `test_day_span_cap_422` (401 days by day → 422).
- [ ] **Step 2: Run** → 404.
- [ ] **Step 3: Implement** (one grouped query by bucket expression + root category + currency; fill empty buckets).
- [ ] **Step 4: Run** `tests/integration/reports -q` → green.
- [ ] **Step 5: Commit** `feat(accounting): GET /reports/trend with empty buckets and averages (reports PR A)`

### Task 6: Budgets — CRUD and monthly status

**Files:**
- Create: `app/services/budget_service.py`, `tests/integration/reports/test_budgets_api.py`
- Modify: `app/schemas/reports.py` (`BudgetScope = Literal["total","category","project"]`, `BudgetIn {scope, category_id: int | None = None, project_id: int | None = None, amount: Decimal (gt 0), effective_from: str | None (pattern `^\d{4}-(0[1-9]|1[0-2])$`)}`, `BudgetAmendIn {amount, effective_from}`, `BudgetStatusOut`, `BudgetStatusListOut {month, currency, time_progress: float, budgets}`), `app/routers/reports.py`

**Interfaces:**
- Produces: `budget_service.status(db, month: str) -> dict`; `create(db, payload) -> int`; `amend(db, budget_id, payload) -> int` (returns the row id now in force for `effective_from`); `archive(db, budget_id) -> None`. The row in force for a scope in month M = the non-archived row of that scope with the greatest `effective_from ≤ M` (project scope: its single row). `used`: `total` → period expense of M under the report rules (exclusion applied); `category` → expense of entries whose root category is the scope category in M; `project` → expense of that project's entries over all time **including** the project even when excluded. `remaining = limit − used`, `ratio = float(used / limit)` 4 dp, `over = used > limit`, `time_progress` per Contract decisions.
- Router: `GET /budgets/status?month=` (MONTH_PATTERN), `POST /budgets` (201), `PUT /budgets/{id}`, `DELETE /budgets/{id}` (204).

- [ ] **Step 1: Tests.** `test_over_budget` (購物 3000 from 2026-09; October 購物 3490 → used 3490, remaining −490, `over` true, `ratio == 1.1633`); `test_changing_amount_keeps_history` (餐飲 10000 from 2026-01, PUT 12000 from 2026-11 → October limit 10000, November 12000, two rows in DB); `test_project_budget_whole_life` (東京旅行 budget 30000; spending in 2026-09 and 2026-10 → `used` is the sum in both months when reading 2026-10; project excluded from reports still counted); `test_total_budget_uses_report_rules` (refund reduces `used`; excluded project's spend not in `used`); `test_time_progress` (`today` fixture = 2026-10-15 → `0.4839`; month 2026-09 → `1.0`; 2026-11 → `0.0`); `test_delete_archives_scope` (DELETE → 204, status no longer lists it, rows still in DB with `archived`); `test_create_conflicts_and_422s` (same scope same month → 409; category not top-level → 422; `amount=0` → 422; `scope=project` without `project_id` → 422).
- [ ] **Step 2: Run** → 404.
- [ ] **Step 3: Implement** service + router; status reuses `report_service` grouping helpers (`breakdown` with `group_by=category` for category budgets is acceptable if it keeps one query per status call; otherwise a dedicated grouped query).
- [ ] **Step 4: Run** `tests/integration/reports -q` → green.
- [ ] **Step 5: Commit** `feat(accounting): budgets — CRUD and /reports/budgets/status (reports PR A)`

### Task 7: Saved reports — CRUD, config validation, stale refs

**Files:**
- Create: `app/services/saved_report_service.py`, `tests/integration/reports/test_saved_reports_api.py`, `tests/unit/reports/test_report_config.py`
- Modify: `app/schemas/reports.py` (`ReportPeriod = PresetPeriod {preset: Literal[...]} | DatePeriod {from_: date (alias "from"), to: date}`, `ReportFiltersIn` (the `ReportFilters` keys, all optional), `ReportConfigIn {v: Literal[1], period, metric, group_by, split_by: Dimension | None = None, filters: ReportFiltersIn = {}, compare: Compare | None = None}` with a model validator for `split_by != group_by` and `from ≤ to`; `SavedReportIn {name (1–40), config, chart: Literal["bar","line","donut","table"], pinned: bool = False, sort_order: int | None = None}`, `SavedReportOut {id, name, config, chart, pinned, sort_order, stale_refs: list[str], created_at, updated_at}`), `app/routers/reports.py`

**Interfaces:**
- Produces: `saved_report_service.list(db)`, `get(db, id)`, `create(db, payload) -> int`, `update(db, id, payload)`, `delete(db, id)`; `resolve_stale(db, config: dict) -> tuple[dict, list[str]]` (drops ids not in `category`/`account`/`project`/`counterparty` tables; returns `"<filter>:<id>"` list). Existence of ids is validated on write (422 `"unknown category_id 999"`), not on read.
- Router: `GET /saved`, `GET /saved/{id}`, `POST /saved` (201), `PUT /saved/{id}`, `DELETE /saved/{id}` (204); 404 for unknown ids, 409 on duplicate name.

- [ ] **Step 1: Tests.** Unit: `ReportConfigIn` rejects `split_by == group_by`, unknown preset, `from > to`, unknown filter key (`extra="forbid"`), `v: 2`. Integration: `test_save_relative_period_round_trips_preset` (`period: {preset: "last_6_months"}` → read back unchanged, `stale_refs == []`); `test_deleted_category_dropped_and_listed` (save with `filters.category_id=[<41>]`, delete the category via `DELETE /settings/categories/{id}` (or archive-then-delete as the API requires) → GET returns `filters.category_id == []` and `stale_refs == ["category_id:41"]`); `test_unknown_id_on_write_422`; `test_duplicate_name_409`; `test_sort_order_default_and_reorder` (create two → 1, 2; PUT second with `sort_order=0` → list order flips); `test_pinned_flag_persists`.
- [ ] **Step 2: Run** → 404.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/unit/reports tests/integration/reports -q` → green.
- [ ] **Step 5: Commit** `feat(accounting): saved reports CRUD with config validation and stale_refs (reports PR A)`

### Task 8: Parity, performance, spec delta and verification

**Files:**
- Create: `tests/integration/reports/test_parity_and_performance.py`
- Modify: `openspec/changes/add-accounting-reports/specs/accounting-reports/spec.md` (append the "Contract decisions" as requirement text/scenarios: trend response fields and `all` key, budget status fields and write semantics, saved-report reorder via `sort_order`, `stale_refs` format, `metric=count`), `openspec/changes/add-accounting-reports/tasks.md` (tick 1.1–1.5, 4.1, 4.2, 5.1), `services/accounting-service/README.md` (one paragraph: `/reports/*` endpoints and the shared rules)

- [ ] **Step 1: Tests.** `test_daily_summary_sum_equals_breakdown_by_day` (Σ daily expense over the month == breakdown `group_by=day` total within 0.0005 × days rounding); `test_breakdown_12_months_under_300ms_on_20k_entries` (bulk-insert 20,000 entries with `db_session.execute(insert(LedgerEntry), rows)` across 12 months, 30 merchants, 8 categories; time `GET /reports/breakdown?group_by=category` over the year; assert `< 0.3` s, marked `@pytest.mark.slow` if the suite has such a marker, else plain).
- [ ] **Step 2: Run** → the performance test may fail; if it does, add indexes/`EXPLAIN`-driven fixes in `report_service` (never relax the assertion past 0.5 s without a ledger ruling).
- [ ] **Step 3: Spec delta + tasks.md + README.** `npx -y @fission-ai/openspec@latest validate add-accounting-reports --strict` → valid (requires PR #235's change on the branch; if it is not merged yet, cherry-pick its `openspec/changes/add-accounting-reports/` directory first and say so in the report).
- [ ] **Step 4: Full backend suite** `.venv/bin/pytest -q` → green (count reported); `tests/integration/test_migration.py` included.
- [ ] **Step 5: Commit** `test(accounting): report parity and performance; openspec deltas for the API contract (reports PR A)`
