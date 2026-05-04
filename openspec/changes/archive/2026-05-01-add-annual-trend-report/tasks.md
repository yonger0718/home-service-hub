## 1. Schema 定義

- [x] 1.1 在 `services/accounting-service/app/schemas/analytics.py` 新增 `MonthlyTrendPoint`
- [x] 1.2 新增 `CategoryTrend`
- [x] 1.3 新增 `AnnualSummary`
- [x] 1.4 新增 `AnnualReport`
- [x] 1.5 在 `schemas/__init__.py` 匯出新 schema
- [x] 1.6 在 `frontend/src/app/models/accounting.model.ts` 新增對應 interfaces

## 2. Service 實作

- [x] 2.1 在 `analytics_service.py` 新增 `get_annual_report(db, year)`
- [x] 2.2 確認此 function 不呼叫 `recurring_service.generate_recurring_items()`
- [x] 2.3 一次查詢該年度 transactions，並 joinedload `category_info` / `card`（如需要）
- [x] 2.4 建立 `monthly_income[12]`、`monthly_expense[12]`
- [x] 2.5 建立 `category_monthly_map: dict[str, list[int]]`
- [x] 2.6 分類名稱優先使用 `t.category_info.name`，fallback 至 `t.category`
- [x] 2.7 組裝固定 12 筆 `monthly_trend`
- [x] 2.8 組裝依 total desc 排序的 `category_trend`
- [x] 2.9 計算年度 `summary`
- [x] 2.10 確保所有金額欄位為 int、`savings_rate` 為 float

## 3. Router/API

- [x] 3.1 在 `routers/transactions.py` 新增 `GET /report/annual/{year}`
- [x] 3.2 Response model 使用 `AnnualReport`
- [x] 3.3 在 Angular `AccountingService` 新增 `getAnnualReport(year)`

## 4. 後端測試

- [x] 4.1 查詢有資料年度：`monthly_trend` 長度 12，指定月份數值正確
- [x] 4.2 查詢無資料年度：12 個月份皆為 0，`category_trend` 空，highest/lowest 為 null
- [x] 4.3 驗證報表查詢不會產生 recurring transactions
- [x] 4.4 驗證 `category_trend` 依 total desc 排序
- [x] 4.5 驗證 `highest_expense_month` 與 `lowest_expense_month`
- [x] 4.6 驗證 JSON 金額為 integer
- [x] 4.7 驗證不對 12 個月份各發一次查詢

## 5. 前端

- [x] 5.1 新增年度趨勢 UI（dashboard tab 或獨立 route）
- [x] 5.2 年份選擇，預設當年
- [x] 5.3 收支折線圖：income / expense / surplus
- [x] 5.4 分類趨勢圖：以 `category_trend` top 5 顯示
- [x] 5.5 年度 summary 區塊
- [x] 5.6 loading / error / empty states

## 6. 驗收

- [x] 6.1 後端 `pytest` 全過
- [x] 6.2 前端測試或 build 通過
- [x] 6.3 與既有月報抽樣比對：同月份 income/expense 相同
- [x] 6.4 OpenAPI docs 顯示新增 endpoint 與 schema
