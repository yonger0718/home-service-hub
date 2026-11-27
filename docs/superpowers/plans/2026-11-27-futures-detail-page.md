# 期貨明細 page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only page `/portfolio/futures` that shows one month of 永豐期貨 statements: month summary, per-day fills, and open positions.

**Architecture:** Two new GET endpoints on the existing `futures` router of stock-portfolio-service, built only from `futures_statement_day` / `futures_statement_month` rows and returned through explicit pydantic models. One new standalone Angular page plus two entry links. No migration, no writes.

**Tech Stack:** FastAPI + SQLAlchemy + pydantic v2 (Python 3.13), pytest on sqlite; Angular 21 standalone components with signals, Vitest via `ng test`.

**Spec:** `docs/superpowers/specs/2026-11-27-futures-detail-page-design.md` (rev 1.1). Background on the stored shapes: `docs/superpowers/specs/2026-11-25-sinopac-futures-statement-design.md` §5 and the payload schema `services/stock-portfolio-service/app/schemas/sinopac_futures_statement.py`.

## Global Constraints

- Read-only: no writes, no change to transactions, holdings, FIFO, snapshots, net worth, or any existing endpoint's output.
- No migration; no new table or column.
- Monthly summary letters: `futures_realized = k`, `premium_net = g`, `fees_and_tax = h`, `deposits_net = b` (same mapping as `_DAILY_SUM` in `sinopac_futures_statement_service.py`).
- Completeness as the card (`futures_summary_service._month_block`): monthly source → `complete: true, reason: null`; `daily_sum` with any daily row in the month carrying non-empty `findings` → `complete: false, reason: "findings"`; acked findings still count.
- `missing_days` = count of the monthly row's `activity_dates` without a daily row between `month` and `month_end`; `0` when the month has no monthly row.
- Positions come from the latest daily row with `statement_date <= cutoff`, cutoff = monthly `month_end` if a monthly row exists, else the calendar month's last day; may be from an earlier month; `null` only when no such row exists. `month_end_may_differ = monthly row exists and monthly.open_positions != len(daily positions)`.
- Response models in `app/schemas/futures_detail.py`; fills / deposits / positions mapped field by field; any other stored key never reaches the response.
- Money amounts are strings with 4 decimals (`f"{Decimal(v):.4f}"`, as `futures_summary_service._amount`); prices and strikes are `str(Decimal(v))` of the stored value (as printed), strike `null` for futures.
- Month key format `YYYY-MM`; bad key → 422; month with neither a daily nor a monthly row → 404.
- Copy (exact): page title 期貨明細; summary labels 本月期貨平倉損益 / 權利金收支 / 手續費＋期交稅 / 存提淨額; 日對帳單加總; 可能不完整; 缺 N 天日對帳單，明細不完整; 摘要以月對帳單為準; 當日無交易; 持倉：MM/DD 日對帳單; 月底持倉可能不同; 期貨資料載入失敗; 尚無期貨對帳單; 這個月份沒有期貨對帳單; links 明細 → (card) and 期貨明細 → (交易明細 header), phone menu item 期貨明細.
- Colours follow the futures card (red = up, green = down) via `components/ui/format` helpers (`formatMoney`, `displayedSign`).
- Fixtures fictional only (`範例期貨`, `範例選`, order numbers like `sA1B2-01`); public repo.
- Never run `npm run config`, `set-env.js`, `npm run build` or `npm start` in the worktree. Backend tests: from `services/stock-portfolio-service`, with the DB vars eval'd from `~/workspace/home-hub/.env` (only `POSTGRES_*`), `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest <path> -q -p no:cacheprovider`. Frontend single spec: from `frontend`, `npx ng test --watch=false --include <spec>`.

## Review Focus

1. A daily row stored with `fills`, `deposits` or `positions` = `null` (nullable columns): the day still renders, as no trade / no deposits / no positions — Task 1 and Task 2 tests pin it.
2. A daily row dated after the monthly `month_end` (first days of the next month): it must not become the month's positions source — Task 2 test pins it.
3. `?month=` pointing at a month with no statements (old link, hand-typed): page shows 這個月份沒有期貨對帳單 and the picker, not a blank page or the error copy — Task 3 test pins it.
4. Option fills whose `pnl` is negative (premium paid): shown as 權利金 with its sign and colour, never as 平倉損益 — Task 3 test pins it.
5. Position from an earlier month: its date is shown, so the owner does not read an old position as this month's — Task 2 and Task 3 tests pin it.

---

### Task 1: API — response models, `/futures/months`, `/futures/detail` summary and days

**Files:**
- Create: `services/stock-portfolio-service/app/schemas/futures_detail.py`
- Create: `services/stock-portfolio-service/app/services/futures_detail_service.py`
- Modify: `services/stock-portfolio-service/app/routers/futures.py`
- Test: `services/stock-portfolio-service/tests/integration/test_futures_detail.py`

**Interfaces:**
- Produces (schemas, all `BaseModel`): `FuturesMonths(months: list[str])`; `CloseOut(date: str, qty: int, price: str)`; `FillOut(kind: Literal["future","option"], order_no: str, product: str, month: str, strike: Optional[str], right: Optional[Literal["Call","Put"]], action: Literal["新買","新賣","平買","平賣"], qty: int, price: str, tax: str, fee: str, pnl: str, closes: list[CloseOut])`; `FillsTotalOut(tax: str, fee: str, pnl: str)`; `DepositOut(direction: Literal["in","out"], amount: str)`; `DayOut(date: str, no_trade: bool, fills: list[FillOut], fills_total: Optional[FillsTotalOut], deposits: list[DepositOut])`; `DetailSummaryOut(futures_realized: str, premium_net: str, fees_and_tax: str, deposits_net: str, source: Literal["monthly","daily_sum"], complete: bool, reason: Optional[Literal["findings"]], missing_days: int)`; `PositionOut` and `PositionsOut` (Task 2 fills them in; declare here so `FuturesDetail` is complete); `FuturesDetail(month: str, summary: DetailSummaryOut, days: list[DayOut], positions: Optional[PositionsOut])`.
- Produces (service): `months(db: Session) -> dict` and `detail(db: Session, month: str) -> dict | None` (`None` = no rows → router 404).
- Produces (routes): `GET /api/portfolio/futures/months` (`response_model=FuturesMonths`), `GET /api/portfolio/futures/detail?month=YYYY-MM` (`response_model=FuturesDetail`, `month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")`).

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_futures_detail.py`. Reuse the row helpers of `tests/integration/test_futures_summary.py` (copy `_day` / `_month`, extend `_day` with `fills`, `fills_total`, `deposits`, `positions` keyword JSON and `_month` with `g`, `h`, `activity_dates`, `open_positions`). Tests and assertions:
  - `test_months_newest_first_union`: daily 2026-01-15, monthly 2026-02 (month_end 02-27) only, daily 2026-03-05 → `{"months": ["2026-03", "2026-02", "2026-01"]}`; empty DB → `{"months": []}`.
  - `test_detail_monthly_source`: monthly 2026-03 (month_end 03-31) with `k=50, g=-12, h=30, b=200`, `activity_dates=["2026-03-05","2026-03-09"]`; daily 03-05 only → `summary == {"futures_realized": "50.0000", "premium_net": "-12.0000", "fees_and_tax": "30.0000", "deposits_net": "200.0000", "source": "monthly", "complete": True, "reason": None, "missing_days": 1}`.
  - `test_detail_monthly_only_no_daily`: monthly 2026-02 only, no daily in February → `source == "monthly"`, `days == []`, `missing_days == len(activity_dates)`.
  - `test_detail_daily_sum_complete`: dailies 03-05 (`futures_realized=30, premium_net=-5, fees_and_tax=4, deposits_net=200`) and 03-09 (`20, 0, 2, 0`) → summary `"50.0000" / "-5.0000" / "6.0000" / "200.0000"`, `source == "daily_sum"`, `complete is True`, `reason is None`, `missing_days == 0`.
  - `test_detail_daily_sum_prev_finding_incomplete`: same as above but 03-09 has `findings=[{"code": "prev", "dates": ["2026-03-05"], "letters": []}]` and an ack (`ack_status="findings"`) → `complete is False`, `reason == "findings"`.
  - `test_detail_days_newest_first_and_fill_mapping`: daily 03-09 with a futures fill (`範例期貨`, `strike: null`, action `平賣`, closes one link) and an option fill (`範例選`, `strike: "21000"`, `right: "Call"`, `pnl: "-300"`), daily 03-05 with `fills=[]`, `fills_total=None`, one deposit → `days[0]["date"] == "2026-03-09"`; fills `kind` = `"future"` / `"option"`; `pnl == "-300.0000"`, `price == "6"`, `strike == "21000"`; `closes == [{"date": "2026-03-02", "qty": 1, "price": "20500"}]`; `days[1] == {"date": "2026-03-05", "no_trade": True, "fills": [], "fills_total": None, "deposits": [{"direction": "in", "amount": "200.0000"}]}`.
  - `test_detail_drops_extra_json_keys`: stored fill with extra `"note": "x"` and close with extra `"order_no"`, deposit with `"seq"` → response fill keys equal exactly the `FillOut` field set, close keys `{"date","qty","price"}`, deposit keys `{"direction","amount"}`.
  - `test_detail_null_json_columns`: daily row with `fills=None, deposits=None, fills_total=None` → `no_trade True`, `fills []`, `deposits []`.
  - `test_detail_404_and_422`: `month=2025-01` with no rows → 404; `month=2026-13` and `month=202603` → 422.
- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/integration/test_futures_detail.py -q -p no:cacheprovider` (env as Global Constraints). Expected: FAIL / 404 on the new paths.
- [ ] **Step 3: Implement** the schemas, `futures_detail_service.months` / `detail` (summary and days; set `positions` to `None` for now), and the two routes in `app/routers/futures.py` (no extra auth dependency — the token middleware already requires read scope for GET). `kind = "option" if fill["right"] else "future"`. Build each `FillOut` / `CloseOut` / `DepositOut` from named keys only.
- [ ] **Step 4: Run the file again.** Expected: all pass. Then run `tests/integration/test_futures_summary.py tests/integration/test_futures_statement_routes.py` — Expected: pass (no regression on the router).
- [ ] **Step 5: Commit** `feat(portfolio): futures detail API — months, month summary and days`.

### Task 2: API — positions block

**Files:**
- Modify: `services/stock-portfolio-service/app/schemas/futures_detail.py`, `app/services/futures_detail_service.py`
- Test: `services/stock-portfolio-service/tests/integration/test_futures_detail.py`

**Interfaces:**
- Consumes: Task 1 `detail()` and `FuturesDetail`.
- Produces: `PositionOut(product: str, month: str, strike: Optional[str], right: Optional[Literal["Call","Put"]], side: Literal["買","賣"], qty: int, entry_price: str, mark_price: str, unrealized: str, premium_value: str)`; `PositionsOut(date: str, month_end_may_differ: bool, items: list[PositionOut])`.

- [ ] **Step 1: Write the failing tests:**
  - `test_positions_from_last_daily_of_month`: dailies 03-05 (1 position) and 03-20 (2 positions), no monthly → `positions["date"] == "2026-03-20"`, `len(items) == 2`, `month_end_may_differ is False`; item keys exactly the `PositionOut` fields (stored `opened` and `initial_margin` absent); `unrealized` / `premium_value` 4-decimal strings.
  - `test_positions_carried_from_earlier_month`: daily 2026-01-28 with 1 position, monthly 2026-02 (month_end 02-27, `open_positions=1`) and no February daily → `positions["date"] == "2026-01-28"`, `month_end_may_differ is False`.
  - `test_positions_month_end_may_differ`: same but monthly `open_positions=0` → `month_end_may_differ is True`.
  - `test_positions_ignore_daily_after_month_end`: monthly 2026-02 month_end 02-26, daily 02-25 (1 position) and daily 02-27 (2 positions, dated after month_end) → `positions["date"] == "2026-02-25"`.
  - `test_positions_null_without_earlier_daily`: monthly 2026-02 only, no daily on or before 02-27 → `positions is None`.
  - `test_positions_null_json`: the source daily has `positions=None` → `positions == {"date": ..., "month_end_may_differ": False, "items": []}` when no monthly row.
- [ ] **Step 2: Run to verify failure.** Expected: positions is `None` → FAIL.
- [ ] **Step 3: Implement** the cutoff and lookup in `detail()` per Global Constraints (one query: latest `FuturesStatementDay` with `statement_date <= cutoff`).
- [ ] **Step 4: Run** `tests/integration/test_futures_detail.py` then the whole portfolio suite (`tests -q -p no:cacheprovider`). Expected: new file green; whole suite only the failures already on clean main (note their names in the report; issue #327 lists the known Cathay-fixture ones).
- [ ] **Step 5: Commit** `feat(portfolio): futures detail positions block`.

### Task 3: SPA — 期貨明細 page

**Files:**
- Modify: `frontend/src/app/models/portfolio.model.ts` (types mirroring Task 1 / Task 2 models: `FuturesMonths`, `FuturesDetail`, `FuturesDetailSummary`, `FuturesDay`, `FuturesFill`, `FuturesClose`, `FuturesDeposit`, `FuturesPositions`, `FuturesPosition`)
- Modify: `frontend/src/app/services/portfolio.service.ts` (`getFuturesMonths(): Observable<FuturesMonths>`, `getFuturesDetail(month: string): Observable<FuturesDetail>`, next to `getFuturesSummary`)
- Create: `frontend/src/app/components/portfolio/futures-detail/futures-detail.{ts,html,scss,spec.ts}` (`PortfolioFuturesDetailComponent`, selector `app-portfolio-futures-detail`, standalone, `ChangeDetectionStrategy.OnPush`, signals)
- Modify: `frontend/src/app/app.routes.ts` (`{ path: 'portfolio/futures', loadComponent: … }` next to `portfolio/realized-pnl`)

**Interfaces:**
- Consumes: the two endpoints of Tasks 1–2.
- Produces: route `/portfolio/futures` with optional query param `month=YYYY-MM`.

Behaviour: load months; selected month = `month` query param if present, else `months[0]`; picker over `months` (newest first); changing it reloads detail and updates the query param with `replaceUrl: true`. Layout follows the realized-pnl page (`app-page-header`, bento cards). Summary card with the four labels; `source === "daily_sum"` adds 日對帳單加總; `!complete && reason === "findings"` adds 可能不完整; `missing_days > 0` adds 缺 N 天日對帳單，明細不完整 with 摘要以月對帳單為準. Day blocks newest first: desktop one table per day, phone (`isPhone`, as the transaction list) one card per fill; columns 商品 / 月份 / 履約價 / 權別, 動作, 口數, 成交價, 期交稅, 手續費, and 平倉損益 (future) or 權利金 (option) with sign colour; day totals from `fills_total`; a fill with `closes` expands (button) to `平倉 → MM/DD n 口 @ price` lines; `no_trade` shows 當日無交易; deposits show 入金 / 出金 and amount. Positions card: header 持倉：MM/DD 日對帳單, 月底持倉可能不同 when flagged, rows 商品, 買／賣, 口數, 進場價, 行情價, 未沖銷損益 (future) or 權利金市值 (option); hidden when `positions` is null. States: months empty → 尚無期貨對帳單; detail 404 → 這個月份沒有期貨對帳單 (picker still shown); other errors → 期貨資料載入失敗.

- [ ] **Step 1: Write the failing spec** `futures-detail.spec.ts` with `HttpTestingController` (fictional data only). Tests:
  - `renders monthly summary with missing-days note`: summary `source "monthly"`, `missing_days 2` → text contains 本月期貨平倉損益, 缺 2 天日對帳單，明細不完整, 摘要以月對帳單為準; does not contain 日對帳單加總.
  - `marks daily_sum incomplete`: `source "daily_sum", complete false, reason "findings"` → contains 日對帳單加總 and 可能不完整.
  - `shows option pnl as 權利金 with sign`: one option fill `pnl "-300.0000"` → that row shows 權利金 and a negative-sign class matching the card's down colour; a future fill shows 平倉損益.
  - `expands closing links`: click the expand control on a fill with one close → text contains `平倉 → 03/02`.
  - `no-trade day`: `no_trade true` → 當日無交易.
  - `positions carried from earlier month`: `positions.date "2026-01-28"`, `month_end_may_differ true` → 持倉：01/28 日對帳單 and 月底持倉可能不同; `positions null` → no positions card.
  - `defaults to newest month and honours query param`: months `["2026-03","2026-02"]` → detail request `month=2026-03`; with route query `month=2026-02` → request `month=2026-02`.
  - `404 month shows no-statement copy`: detail 404 → 這個月份沒有期貨對帳單 and the picker present; 500 → 期貨資料載入失敗; months `[]` → 尚無期貨對帳單 and no detail request.
- [ ] **Step 2: Run to verify failure.** Run (from `frontend`): `npx ng test --watch=false --include src/app/components/portfolio/futures-detail/futures-detail.spec.ts`. Expected: FAIL (component missing).
- [ ] **Step 3: Implement** types, service methods, component and route.
- [ ] **Step 4: Run the spec again.** Expected: PASS. Then `npx ng test --watch=false --include 'src/app/components/portfolio/**/*.spec.ts'`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spa): 期貨明細 page`.

### Task 4: SPA — entry links

**Files:**
- Modify: `frontend/src/app/components/portfolio/dashboard/futures-card/futures-card.{html,ts,spec.ts}`
- Modify: `frontend/src/app/components/portfolio/transaction-list/transaction-list.{html,ts}` and its spec

**Interfaces:**
- Consumes: route `/portfolio/futures` (Task 3).

- [ ] **Step 1: Write the failing tests:**
  - futures card: `shows detail link when data present` — with a summary that has `value`, an anchor with text 明細 → and `href` ending `/portfolio/futures`; `hides detail link when no statements` — with `value: null`, no such anchor.
  - transaction list: desktop header has an anchor 期貨明細 → to `/portfolio/futures`; `phoneMenu` contains `{ label: '期貨明細', routerLink: '/portfolio/futures' }` (icon free choice).
- [ ] **Step 2: Run to verify failure** with `npx ng test --watch=false --include <each spec>`. Expected: FAIL.
- [ ] **Step 3: Implement** the links (`RouterLink`; desktop header link uses the same `btn btn-outline-secondary btn-sm` as 匯入 CSV).
- [ ] **Step 4: Run** both specs, then `npx ng test --watch=false`. Expected: PASS (report any pre-existing failures by name).
- [ ] **Step 5: Commit** `feat(spa): links to 期貨明細 from the futures card and 交易明細`.
