# Accounting Service 改善與功能規劃

> **Superseded by `openspec/changes/add-accounting-entry`.** 本文件記錄的是已移除的舊記帳服務（transactions / subscriptions / categories 報表）的檢討與規劃，僅供歷史參考。現行記帳以 MOZE 帳本為基礎：phase 1 見 `openspec/changes/archive/2026-10-02-rebuild-accounting-moze-ledger/`，phase 2a（記帳頁、時間軸、帳戶設定、MOZE 備份匯入）見 `openspec/changes/add-accounting-entry/`，部署步驟見 `docs/deploy/accounting-phase-2a.md`。

整理 2026-04-30 review、Opus 4.7 分析與目前 owner 決策，作為後續 OpenSpec changes 與實作依據。

## 2026-05-04 已完成實作

- `tighten-accounting-internals` 已完成：退款不再灌入 income 報表、`_get_refunded_amounts` 已抽成共享 helper、transaction/recurring 驗證已集中、recurring 月底日期已改用真實月底。
- `drop-transaction-category-string` 已完成程式碼調整：Transaction / Subscription 改為只儲存 `category_id`、API 回應改提供 `category_name`、categories rename/merge 已移除 legacy string 同步、前端 payload 已改為只送 `categoryId`。
- 驗證：`services/accounting-service/.venv/bin/python -m pytest -q` 為 `40 passed`；frontend `npm run build` 與 `npm test -- --watch=false` 皆通過。
- 尚未在真實 PostgreSQL 複本上執行 Alembic smoke migration；部署前仍需做 DB-level upgrade 驗證。

## 2026-05-04 後續改善計劃

本段整理 2026-05-04 針對 `services/accounting-service` 的 follow-up 掃描結果。當下狀態：
- accounting service 工作樹無未提交 diff。
- 最近相關提交：`6db162c Improve accounting integrity and annual reporting`。
- 測試驗證：在 `services/accounting-service` 執行 `.venv/bin/pytest`，結果 `29 passed`，僅有 OpenTelemetry logging handler deprecation warning。

### P0. 降低 tracing 中的敏感資料暴露

現況：
- `app/routers/transactions.py` 會將交易 request body、response body、列表 sample 放進 span attributes。
- 記帳資料包含品項、金額、支付工具、備註，屬於敏感個人財務資料。

風險：
- tracing backend、log export、debug dump 可能保存完整交易明細。
- span attribute 體積變大，也會增加可觀測性成本與查詢雜訊。

建議調整：
- 預設只記錄低敏摘要，例如 `transaction.id`、`transaction.type`、`amount`、`response.count`、`report.period`。
- 移除或 gate 住 `http.request.body`、`http.response.body`、`http.response.body.sample`。
- 如仍需完整 payload debug，使用明確環境變數，例如 `ACCOUNTING_TRACE_BODY_ENABLED=false` 預設關閉。

驗證：
- 補測試或輕量 smoke check，確認 create/update/get/list spans 不再寫完整 body attribute。
- 手動跑 `.venv/bin/pytest`。

### P0. 分類刪除加上引用保護

現況：
- `app/routers/categories.py` 的 delete endpoint 直接 `db.delete(db_cat)`。
- 分類已被 `transactions.category_id` 或 `subscriptions.category_id` 引用時，PostgreSQL 可能丟 FK 錯誤並變成 500。
- 系統已有 category merge 流程，應引導使用者先 merge。

建議調整：
- 刪除前統計引用數。
- 若存在引用，回 400，訊息包含 affected transactions/subscriptions，提示先使用 merge。
- 若無引用才允許刪除。

驗證：
- 補 integration test：
  - 被 transaction 引用時不可刪。
  - 被 subscription 引用時不可刪。
  - 無引用時可刪。

### P1. 抽出 accounting 共用驗證 helper

> 與下方 follow-up F3 為同一項，已合併到 OpenSpec change `tighten-accounting-internals`。

現況：
- `transaction_service.py` 與 `recurring_service.py` 重複實作：
  - `category_id` 是否存在並同步 category name。
  - `payment_method` 是否存在。
  - `card_id` 是否存在並同步 `card.default_payment_method or "Apple Pay"`。

風險：
- create/update/subscription/installment 規則未來容易漂移。
- 修一條路徑時可能漏掉另一條路徑。

建議調整：
- 新增 service-level helper，例如 `app/services/accounting_validation.py`。
- 提供明確小函式：
  - `resolve_category_name(db, category_id)`
  - `ensure_payment_method_exists(db, payment_method)`
  - `resolve_card_default_payment_method(db, card_id, requested_payment_method)`
- 保持 HTTPException status/detail 與現有 API 相容，避免前端需要跟著改。

驗證：
- 既有 transaction / recurring tests 應全過。
- 新增一個覆蓋「卡片預設付款工具同步規則在 transaction/subscription/installment 一致」的測試。

### P1. 查詢參數與報表月份邊界驗證

現況：
- `list_transactions` 的 `skip` / `limit` 沒有限制。
- 報表路由的 `year` / `month` 沒有限制。
- service 雖會查不到資料時回空報表，但非法月份或過大的 limit 不應流到 service 層。

建議調整：
- FastAPI route 參數使用 `Query` / `Path`：
  - `skip >= 0`
  - `1 <= limit <= 500` 或依實際 UI 需求設定。
  - `1 <= month <= 12`
  - `year` 設合理範圍，例如 `2000 <= year <= 2100`。
- 若前端已有固定分頁大小，limit 上限可以更保守。

驗證：
- 補 API integration tests 驗證非法參數回 422。
- 既有報表測試維持通過。

### P2. 報表查詢效能與索引友善度

現況：
- `analytics_service.get_annual_report` / monthly report 使用 `extract('year', Transaction.date)` 與 `extract('month', Transaction.date)`。
- 年報一次抓整年交易到 Python 聚合，對目前家用資料量可接受。

風險：
- 資料量增加後，`extract()` 可能降低日期索引利用率。
- 年報回應時間會隨全年交易量線性增加。

建議調整：
- 先把查詢條件改成 date range：
  - 年報：`date >= Jan 1` 且 `date < next Jan 1`。
  - 月報：`date >= month_start` 且 `date < next_month_start`。
- 若資料量繼續增加，再把年度趨勢改成 SQL aggregation。

驗證：
- 既有 annual/monthly report 測試全過。
- 加一個邊界測試，確認年初、年末、下年度 1/1 不會被錯算。

### P2. recurring 生成防併發重複

現況：
- `generate_recurring_items` 逐筆查 exists 後新增。
- 若排程與手動觸發同時執行，有 race condition 風險。

建議調整：
- 長期加 DB unique constraint，限制同一 subscription/installment 同年月只能生成一筆 transaction。
- 短期可在 service 中加 transaction-level locking 或重跑時更嚴格檢查。
- 也可將 exists 查詢批次化，降低 N+1。

驗證：
- 補測試覆蓋重複呼叫不增加 transaction。
- 若新增 unique constraint，補 Alembic migration 與衝突處理測試。

### 2026-05-04 第二輪 follow-up（Opus 4.7 掃描）

第二輪掃讀 `services/accounting-service/app/{services,routers}/*.py` 後，補列以下項目。owner 已挑選 F1a / F2 / F3 / F4 / F5 進入實作，整理進兩個 OpenSpec change（見「建議落地順序」末段）。

> 取捨：tracing body 暴露（上方 P0「降低 tracing 中的敏感資料暴露」）這輪不動，等之後再評估。

#### F1. 退款語意：refund 汙染 income 報表（owner 選 F1a，已完成）

現況：
- `transaction_service.refund_transaction` 把退款建成 `INCOME` 並複製原始 EXPENSE 的 `category` 與 `category_id`。
- `analytics_service.get_monthly_report` / `get_annual_report` 直接把所有 `INCOME` 加進 `total_income`，所以退款被當成「收入」灌入 income breakdown 與 savings_rate 計算。
- 結果：分類面板會出現「餐飲」這種收入分類；savings_rate 被推高。

風險：
- 報表數字失真，使用者看到的儲蓄率比實際高。

建議調整（F1a，採用）：
- 在 analytics 聚合時，視 `related_transaction_id IS NOT NULL` 的 `INCOME` 交易為「退款抵減」而非「收入」：從 `total_income` 排除，並從原 EXPENSE 月份的 `total_expense` 與該 category 月度金額中扣回（即 net expense）。
- 年報、月報、月對月、top expenses 全部一致。
- list/detail 顯示行為不變（仍是兩筆紀錄、有 `refundable_amount`）。

不採用：
- F1b：把退款另存成獨立 `RefundLedger` 表。改動面太大且和現行 schema 不相容。

驗證：
- 整合測試：建立 EXPENSE → 部分退款 → 月報 income / expense / savings_rate / category breakdown 都正確扣回。
- `top_expenses` 顯示的 `transaction_amount` 不變（仍是原始金額），`refundable_amount` 反映已退。

#### F2. `_get_refunded_amounts` 完全重複（owner 選，已完成）

現況：
- `app/services/transaction_service.py:21-42` 與 `app/services/analytics_service.py:16-37` 是同一個函式。

調整：
- 抽到 `app/services/refund_utils.py`（或合併到 billing_service），兩處改 import。
- 不改變外部行為，純內部 dedupe。

#### F3. 校驗邏輯重複 6 次（owner 選；併原 P1，已完成）

合併原 P1「抽出 accounting 共用驗證 helper」。新增 `app/services/accounting_validation.py`，提供：
- `resolve_category_name(db, category_id) -> str`
- `ensure_payment_method_exists(db, payment_method) -> None`
- `resolve_card_payment_defaults(db, card_id, requested_payment_method) -> str | None`

以下 6 處改為呼叫 helper：
- `transaction_service.create_transaction` / `update_transaction`
- `recurring_service.create_subscription` / `update_subscription`
- `recurring_service.create_installment` / `update_installment`

維持現有 HTTPException status/detail 字串以保留 API 相容。

#### F4. recurring 生成的 `min(day, 28)` 太保守（owner 選，已完成）

現況：
- `recurring_service.generate_recurring_items`（行 29 與行 58）對訂閱與分期一律用 `min(day_of_month, 28)`。
- 訂閱日設 30 號的，永遠在 28 號出帳；2/29、3/31 等真實月底場景被無聲截斷。

調整：
- 改用 `billing_service.safe_date_replace(year, month, day)`，自動取該月最後一天。
- 對訂閱：`safe_date_replace(today.year, today.month, sub.day_of_month)`。
- 對分期：`safe_date_replace(today.year, today.month, inst.start_date.day)`。

驗證：
- 單元測試覆蓋 day=31 的訂閱在 2 月、4 月、3 月各自落在最後一天 / 31 號。

#### F5. 移除 `Transaction.category` / `Subscription.category` 雙寫（owner 選；要 SQL，程式碼已完成，待 DB smoke migration）

現況：
- `Transaction` 和 `Subscription` 同時存 `category` 字串與 `category_id` FK。
- 靠 `_sync_category_name_references`、merge endpoint 等多處同步維持一致；任一漏寫就會漂移。
- 上方 B 段「合併規劃」與「暫不做」原本明文「不移除 `Transaction.category` 字串欄位」；本次決議翻轉，本輪要做。

owner 決議：
- 「我要免除雙寫風險，這是不該存在的」。
- 「資料面可以使用 SQL 就用 SQL 處理」→ migration 用 SQL backfill，不在 Python 層 loop。

調整：
1. **Backfill SQL（Alembic upgrade）：**
   - 對 `transactions.category_id IS NULL AND transactions.category IS NOT NULL`：依 `category` 字串反查 `categories.name` 找到 id 寫回；找不到的字串 `INSERT INTO categories(name)` 後再回填。
   - 同步 `subscriptions`。
2. **API contract 變更：**
   - `TransactionCreate.category_id`、`SubscriptionCreate.category_id`、`InstallmentCreate.category_id`（若有）改為必填。
   - 移除 `category: str` 欄位 from `TransactionCreate / TransactionUpdate / Subscription*`。
   - 響應仍可在 schema 用 `category_name`（computed from relationship）給前端顯示用。
3. **Service 層：**
   - 拿掉所有 `transaction.category = cat.name` 同步寫入。
   - `analytics_service._resolve_category_name` 直接用 `category_info.name`，沒 fallback。
4. **Schema migration：**
   - Alembic：先 backfill → 加 `NOT NULL` 到 `category_id` → drop `category` 欄位。
   - 同步刪 `routers/categories.py::_sync_category_name_references` 與 `merge_categories` 中對 legacy 字串的 update（merge 只剩 `category_id` 重指向）。
5. **前端 audit：**
   - 確認所有 `transaction.category`（string）使用點改成讀 `category_name` 或 `category_info.name`。
   - 送出 payload 一律帶 `category_id`，移除送 `category` 字串的路徑。

風險與緩解：
- 既有資料若有 `category` 字串無法對到任何 Category record：migration 自動補建。
- 前端如有路徑送字串：在 backend 先 deploy schema 之前要把前端 PR 一起 ready，否則 422。
- merge 流程簡化：原本要同步 legacy 字串的 SQL update 全部刪掉，回歸單一 source of truth。

驗證：
- 空 DB / 既有 DB 各跑一次 `alembic upgrade head`，DB 比對 `category` 欄位已不存在、`category_id NOT NULL`。
- 整合測試覆蓋分類改名後報表立即看到新名稱（已不靠同步）。
- Frontend e2e 或單測：建立交易必須帶 `category_id`，缺了回 422。

### 建議落地順序

1. `harden-accounting-observability`
   - 移除完整 body tracing。
   - 低風險、影響面小，但隱私收益高。
2. `harden-accounting-category-delete`
   - 分類刪除引用保護。
   - 和既有 merge 功能自然銜接。
3. `normalize-accounting-validation`
   - 抽共用驗證 helper。
   - 先不改 API 行為，只收斂重複邏輯。
4. `harden-accounting-query-parameters`
   - route 參數邊界驗證。
5. `optimize-accounting-report-queries`
   - date range 查詢與後續 SQL aggregation。
6. `harden-recurring-generation`
   - 防併發重複與唯一約束。

第二輪 follow-up（owner 已挑選、已開 OpenSpec change）：

7. `tighten-accounting-internals`
   - F1a 退款不汙染 income 報表 + F2 dedupe `_get_refunded_amounts` + F3 抽共用驗證 helper（吸收原 P1 / 第 3 項）+ F4 修 recurring `min(day, 28)`。
   - 純內部 / 報表計算改動，無 API breaking。
8. `drop-transaction-category-string`
   - F5 移除 Transaction/Subscription 的 `category` 字串欄位、改 `category_id` 必填。
   - 含 SQL backfill migration、前端 audit、API breaking change。建議排在 7 之後執行。

## 決策摘要

服務：`services/accounting-service`

技術棧：Python 3.13 / FastAPI / SQLAlchemy / Alembic / PostgreSQL

幣別假設：目前只處理 TWD，金額以整數儲存與回傳；百分比才使用 float。

本輪明確納入：
- 修正信用卡預設付款方式同步問題。
- 報表查詢改為純讀取，不在 GET 報表時自動產生 recurring transactions。
- 移除「軟刪除」文件語意，維持既有硬刪除。
- 補齊 Alembic baseline，讓空 DB 可以可靠建 schema。
- 規劃年度趨勢報表。
- 規劃分類改名與合併流程。

本輪明確不做：
- 預算模組。
- 交易狀態模型，例如 `PENDING/POSTED`。
- CSV / PDF 匯出。
- 立即匯入對帳單；後續可做「對帳單比對」功能。
- 多幣別。

## A. Data Integrity 改善

### A1. 修正信用卡預設付款方式同步

現況：
- 建立交易時，如果帶 `card_id` 且 `payment_method` 為空或 `"信用卡"`，會同步成 `card.default_payment_method`。
- 更新交易時，如果只更新 `card_id`，目前會把 `payment_method` 設成 `card.name`，和建立邏輯不一致。

調整：
- `transaction_service.update_transaction` 改成和 create 一致：預設同步到 `card.default_payment_method or "Apple Pay"`。
- 補測試覆蓋 create / update 兩條路徑。

### A2. 報表查詢移除寫入副作用

現況：
- `analytics_service.get_monthly_report` 會在查詢當月報表前呼叫 `recurring_service.generate_recurring_items(db)`。

問題：
- GET 報表有資料庫寫入副作用，重跑報表、除錯、測試都比較難預期。

調整：
- 月報、月比月、年度趨勢報表都只讀既有 transactions。
- recurring 生成只透過明確操作觸發，例如現有 `POST /api/accounting/recurring/generate`。
- 前端需要預期 recurring 資料時，由使用者操作或既有排程先產生，再查看報表。

### A3. 移除軟刪除語意，對齊硬刪除

現況：
- 部分 router summary 寫「軟刪除」，但 service 實際上是 `db.delete()`。
- model 沒有 `deleted_at` 或 `is_deleted`。

調整：
- 保持目前直接刪除行為。
- 移除 `services/accounting-service/` 中所有「軟刪除 / soft delete」字樣。
- DELETE endpoint 文件統一描述為「刪除」或「永久刪除」。

### A4. Alembic baseline 補強

現況：
- `alembic/versions/aaaa59cad2a5_baseline_from_existing_schema.py` 名稱是 baseline，但內容只 drop 舊欄位，不能從空 DB 建出完整 schema。

風險：
- 新環境、測試資料庫、災難復原時，`alembic upgrade head` 可能無法建立 tables。

調整：
- 補一份真正可從空 DB 建立 accounting schema 的 Alembic baseline 或修正現有 baseline。
- migration 需包含目前 model 所需 tables：`transactions`、`credit_cards`、`categories`、`payment_methods`、`subscriptions`、`installments`。
- 若既有環境已經套過舊 revision，需提供安全升級策略，例如先 stamp 現況或新增 reconcile migration，而不是破壞 production DB。
- 補驗證：在空 PostgreSQL database 上跑 `alembic upgrade head`，確認 tables、FK、unique/index 都存在。

### A5. Refund 防呆

現況：
- `refund_transaction` 可接受任意金額，也沒有阻止對 INCOME 退款。

調整規則：
- `refund_amount` 必須大於 0。
- 來源交易必須是 `EXPENSE`。
- 已退金額 = 所有 `related_transaction_id == original.id` 且 `transaction_type == "INCOME"` 的 `transaction_amount` 加總。
- `已退金額 + refund_amount` 不可大於原始 `transaction_amount`。
- 回傳 transaction 時可補 `refundable_amount`，讓前端可以限制退款輸入。

### A6. 信用卡週期淨額允許負數

現況：
- `billing_service.get_card_cycle_usage` 會用 `max(0.0, current_usage)` 把淨退款情境壓成 0。

調整：
- 回傳真實淨額，允許負數。
- `is_near_limit` / `is_over_limit` 只在正向淨消費下成立。
- UI 可以把負數顯示成「本期淨退款」。

### A7. N+1 查詢消除

調整：
- `transaction_service.get_transactions` 預載 `card` 與 `category_info`。
- `analytics_service` 讀報表需要 card/category 時使用 joinedload。
- `recurring_service.get_subscriptions` / `get_installments` 預載 card。

### A8. 金額型別一致性

目前 schemas 多數已是 int，但實作仍要守住以下規則：
- 金額欄位一律 `int`。
- 百分比欄位一律 `float`。
- service accumulator 用 `0`，避免 JSON 出現 `123.0`。
- 補測試保護月報、卡片 usage、年度趨勢報表的 JSON 金額型別。

## B. 分類改名與合併規劃

現況：
- `Transaction.category` 字串與 `category_id` FK 並存。
- 報表目前主要依賴字串欄位，分類改名後歷史資料可能漂移。

短期調整：
- 報表分類名稱以 `category_info.name` 優先，fallback 到 `Transaction.category`。
- 更新分類名稱時，同步更新 `transactions.category` 中相同 `category_id` 的歷史交易。

合併規劃：
- 新增 category merge 行為，例如 `POST /api/accounting/categories/{source_id}/merge`。
- 合併時將 source category 相關 transactions / subscriptions 的 `category_id` 改到 target category。
- 同步更新 legacy `category` 字串。
- source category 只有在沒有未遷移引用後才刪除。
- 補 dry-run 或 preview 回傳，讓使用者知道會影響幾筆交易與訂閱。

暫不做（**已於 2026-05-04 翻轉，見上方 follow-up F5 與 OpenSpec change `drop-transaction-category-string`**）：
- ~~這一輪不強制 `TransactionCreate.category_id` 必填~~ → 第二輪改為必填。
- ~~這一輪不移除 `Transaction.category` 字串欄位~~ → 第二輪移除。

## C. 年度趨勢報表

目的：
- 補上目前只有「單月」與「月對月」報表的缺口。
- 提供全年收入、支出、結餘、分類支出趨勢。

API 建議：

```text
GET /api/accounting/transactions/report/annual/{year}
```

Response 概念：

```json
{
  "year": 2026,
  "monthly_trend": [
    {"month": "2026-01", "total_income": 80000, "total_expense": 55000, "surplus": 25000}
  ],
  "category_trend": [
    {"category": "餐飲", "monthly_amounts": [3200, 4100, 0], "total": 45000, "average": 3750}
  ],
  "summary": {
    "total_income": 960000,
    "total_expense": 660000,
    "surplus": 300000,
    "savings_rate": 31.25,
    "highest_expense_month": "2026-08",
    "lowest_expense_month": "2026-02"
  }
}
```

設計重點：
- 已完成年度回傳 12 個月份；當年度則先回傳 year-to-date 月份範圍，尚未發生的月份不先補進回應。
- 年度報表只讀 transactions，不自動產生 recurring transactions。
- 一次撈出該年度資料後在 Python 端 group，避免對 12 個月份各查一次。
- 分類趨勢回完整分類列表，由前端決定 top 5 或其他顯示方式。

## D. 對帳單比對（後續）

目前不做 CSV 匯出或匯入功能，但可以後續規劃「對帳單比對」：
- 匯入銀行 / 信用卡帳單 CSV。
- 與系統 transactions 比對：日期 ± N 天、金額、商家名稱模糊比對。
- 找出漏記、重複登錄、金額不符。
- 不自動寫入，需人工確認後才建立或修正交易。

建議後續獨立開 change：`add-statement-reconciliation`。

## E. 建議 OpenSpec 拆分

1. `improve-accounting-data-integrity`
   - A1–A8。
   - 包含 Alembic baseline 補強。
   - 包含分類改名同步與分類合併規劃。

2. `add-annual-trend-report`
   - 年度趨勢 API。
   - 前端年度趨勢圖。
   - 依賴 data integrity change 的金額型別與分類 join 規則。

3. `add-statement-reconciliation`
   - 後續再開。
   - 對帳單比對，不在本輪實作。
