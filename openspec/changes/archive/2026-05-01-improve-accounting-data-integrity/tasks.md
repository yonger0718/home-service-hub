## 1. Baseline 與現況確認

- [x] 1.1 檢查 dev/prod DB 的 `alembic_version` 與現有 tables
- [x] 1.2 決定 baseline 修補策略：修正既有 revision 或新增 reconcile/bootstrap revision
- [x] 1.3 補齊可從空 DB 建 schema 的 Alembic migration
- [x] 1.4 在空 PostgreSQL DB 跑 `alembic upgrade head`，驗證 tables/FK/index/unique constraints
- [x] 1.5 在既有 DB 驗證 upgrade/stamp 流程不 drop、不 recreate production tables

## 2. 報表純讀取

- [x] 2.1 從 `analytics_service.get_monthly_report` 移除 `recurring_service.generate_recurring_items(db)` 呼叫
- [x] 2.2 確認 `get_monthly_compare_report` 無任何寫入副作用
- [x] 2.3 補測試：查詢當月報表不會新增 subscription/installment transactions
- [x] 2.4 確認前端仍保留明確 `triggerRecurringGeneration()` 操作

## 3. 卡片付款方式同步

- [x] 3.1 修改 `transaction_service.update_transaction`：只更新 `card_id` 且未提供 `payment_method` 時，同步 `card.default_payment_method or "Apple Pay"`
- [x] 3.2 補測試：create 與 update 綁定同張卡時 payment method 行為一致
- [x] 3.3 補測試：payload 明確提供 `payment_method` 時不被 card default 覆蓋

## 4. 硬刪除文件語意

- [x] 4.1 全文檢查 `services/accounting-service/` 的「軟刪除 / soft delete / soft-delete」
- [x] 4.2 更新 transactions/cards/categories/payment-methods/recurring DELETE endpoint summary
- [x] 4.3 補測試或 OpenAPI 檢查：schema 不再包含「軟刪除」字樣

## 5. Refund 防呆與 refundable_amount

- [x] 5.1 在 `refund_transaction` 檢查 `refund_amount > 0`
- [x] 5.2 禁止對 `INCOME` transaction 退款
- [x] 5.3 計算已退金額：`SUM(transaction_amount)` where `related_transaction_id = original.id` and `transaction_type = "INCOME"`
- [x] 5.4 禁止累計退款超過原交易金額
- [x] 5.5 已全額退款時回 400
- [x] 5.6 在 transaction response schema 新增 `refundable_amount: int`
- [x] 5.7 在 list/detail 回傳時計算 `refundable_amount`
- [x] 5.8 前端 refund 輸入以 `refundable_amount` 作為上限
- [x] 5.9 補測試：合法部分退款、超額退款、對 INCOME 退款、全額後再退、`refundable_amount`

## 6. 卡片週期淨額允許負數

- [x] 6.1 移除 `billing_service.get_card_cycle_usage` 末端的 `max(0.0, ...)`
- [x] 6.2 更新 `CardUsageSummary` 計算，負淨額時 `is_near_limit = false`、`is_over_limit = false`
- [x] 6.3 更新 `get_card_status` 文案以支援負淨額
- [x] 6.4 補測試：退款大於消費、一般消費、超過門檻

## 7. N+1 與金額型別保護

- [x] 7.1 `get_transactions` 加 `joinedload(Transaction.card)` 與 `joinedload(Transaction.category_info)`
- [x] 7.2 `get_subscriptions` / `get_installments` 加 `joinedload(...card)`
- [x] 7.3 `get_monthly_report` / `get_monthly_compare_report` 預載 card/category_info
- [x] 7.4 報表 accumulator 使用 int 初始值
- [x] 7.5 補測試：月報與卡片 usage JSON 金額為 integer
- [x] 7.6 補測試：大量 transaction list 查詢數量為常數

## 8. Category 改名同步

- [x] 8.1 月報與月比月分類名稱優先使用 `category_info.name`，fallback 至 legacy `category`
- [x] 8.2 更新 category name 時，同步 `transactions.category` where `category_id = id`
- [x] 8.3 補測試：分類改名後，歷史交易與報表顯示新名稱

## 9. Category 合併規劃與實作

- [x] 9.1 設計 merge preview response：source/target category、受影響 transactions/subscriptions 數量
- [x] 9.2 新增 category merge preview endpoint
- [x] 9.3 新增 category merge apply endpoint
- [x] 9.4 apply 時更新 transactions/subscriptions 的 `category_id` 與 legacy `category`
- [x] 9.5 source category 引用清空後刪除
- [x] 9.6 補測試：preview、apply、source not found、target not found、source == target

## 10. 驗證

- [x] 10.1 後端測試全跑：`services/accounting-service/.venv/bin/python -m pytest -q`
- [x] 10.2 前端 accounting service/model 測試更新並通過
- [x] 10.3 手動驗證：產生 recurring 後再查月報，報表包含既有交易但查詢本身不新增資料
- [x] 10.4 手動驗證：空 DB 可跑 Alembic 到 head
