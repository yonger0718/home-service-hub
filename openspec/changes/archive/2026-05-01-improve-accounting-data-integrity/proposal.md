## Why

Accounting service 目前有幾個資料一致性與部署可靠性問題：更新交易卡片時付款方式同步邏輯和建立交易不同、報表 GET 會觸發 recurring 生成造成寫入副作用、router 文件殘留「軟刪除」但實作為硬刪除、Alembic baseline 無法從空 DB 建 schema、refund 可超額或對收入退款、卡片淨額會把淨退款壓成 0、報表與列表存在 category/card lazy load 風險，以及分類改名/合併缺少一致流程。

這些問題不需要引入預算、交易狀態或匯出功能，但需要在年度趨勢報表與未來對帳功能前先收斂成穩定基線。

## What Changes

- 修正 `update_transaction(card_id=...)` 時的 `payment_method` 同步，改用 `card.default_payment_method or "Apple Pay"`。
- 報表查詢改為純讀取：月報、月比月、年度報表都不呼叫 `generate_recurring_items()`；recurring 生成只透過明確操作觸發。
- 移除「軟刪除 / soft delete」文件語意，保留現有硬刪除實作。
- 補齊可從空 DB 建立完整 accounting schema 的 Alembic baseline / bootstrap migration，並提供既有 DB 的安全套用策略。
- Refund endpoint 加入金額、來源類型、累計退款上限檢查，並在 transaction response 補 `refundable_amount`。
- 卡片週期淨額允許負數，讓淨退款情境不被歸零。
- 使用 joinedload 消除交易列表、報表、訂閱/分期列表的 N+1。
- 守住金額型別一律為 int、百分比為 float 的 contract。
- 分類改名時同步 legacy `transactions.category`；規劃 category merge 流程。

## Capabilities

### New Capabilities

- `accounting-data-integrity`: 規範 accounting service 的資料一致性、報表純讀取、硬刪除語意、Alembic baseline、退款規則、分類同步與查詢效能不變式。

### Modified Capabilities

目前 `openspec/specs/` 下尚無現存 capability，故無修改項。

## Impact

- **Code**:
  - `services/accounting-service/app/services/transaction_service.py`
  - `services/accounting-service/app/services/billing_service.py`
  - `services/accounting-service/app/services/analytics_service.py`
  - `services/accounting-service/app/services/recurring_service.py`
  - `services/accounting-service/app/routers/{transactions,cards,categories,recurring,payment_methods}.py`
  - `services/accounting-service/app/schemas/transaction.py`
  - `services/accounting-service/alembic/`
  - `frontend/src/app/models/accounting.model.ts`
  - `frontend/src/app/services/accounting.service.ts`
- **API contract**:
  - Existing report endpoints remain read-only.
  - `POST /api/accounting/transactions/{id}/refund` returns HTTP 400 for invalid/over-limit refunds.
  - Transaction responses include `refundable_amount`.
  - Delete endpoints are documented as hard delete.
- **Data migration**:
  - Alembic baseline/bootstrap must support empty DB.
  - Existing DBs need a safe stamp/reconcile path.
- **Risk**:
  - Baseline repair must not drop or recreate production tables.
  - Removing report-side recurring generation changes when recurring items appear in reports; callers must use explicit generation first.
