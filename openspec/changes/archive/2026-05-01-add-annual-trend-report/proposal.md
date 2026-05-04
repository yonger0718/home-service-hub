## Why

Accounting service 目前只有單月報表與月對月比較，使用者無法從年度視角看到收入、支出、結餘與分類支出的趨勢。owner 已確認想做年度/趨勢報表，並且不需要預算、交易狀態或匯出功能。

## What Changes

- 新增年度趨勢報表 endpoint：`GET /api/accounting/transactions/report/annual/{year}`。
- 回傳固定 12 個月份的 income / expense / surplus 趨勢。
- 回傳各支出分類的 12 個月金額序列、年度 total、月平均。
- 回傳年度 summary：年收入、年支出、結餘、儲蓄率、最高/最低支出月份。
- 報表只讀既有 transactions，不自動呼叫 `recurring_service.generate_recurring_items()`。
- 前端新增年度趨勢視圖，以現有 chart 能力呈現收支折線與分類趨勢。

## Capabilities

### New Capabilities

- `accounting-annual-report`: 規範年度趨勢報表的 API、資料結構與聚合語意。

### Modified Capabilities

不修改既有 capability。

## Impact

- **Code**:
  - `services/accounting-service/app/schemas/analytics.py`
  - `services/accounting-service/app/services/analytics_service.py`
  - `services/accounting-service/app/routers/transactions.py`
  - `frontend/src/app/models/accounting.model.ts`
  - `frontend/src/app/services/accounting.service.ts`
  - accounting dashboard components
- **API contract**:
  - 新增 `GET /api/accounting/transactions/report/annual/{year}`。
  - 金額欄位為 int，百分比為 float。
- **Dependency**:
  - 建議先完成 `improve-accounting-data-integrity` 的報表純讀、分類 join 與金額型別保護。
