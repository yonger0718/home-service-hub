## Context

Accounting service 已有交易、卡片、分類、付款方式、訂閱/分期與報表功能。這次 change 只整理正確性與可維運性，不擴大到預算、交易狀態、匯出或對帳單匯入。

目前重要現況：
- DELETE endpoints 實作為硬刪除。
- recurring transactions 由信用卡帳單/手動產生是預期行為，不引入 `PENDING/POSTED` 狀態模型。
- `Transaction.category` 字串與 `category_id` FK 並存，短期仍需相容。
- Alembic baseline 檔名是 baseline，但內容不是完整建表 migration。

## Goals / Non-Goals

**Goals:**
- 修正交易卡片更新時 payment method 同步不一致。
- 讓報表 GET 成為純讀取操作。
- 統一硬刪除文件語意。
- 補強 Alembic baseline / empty DB bootstrap 能力。
- 補 refund 防呆與可退款金額。
- 允許信用卡週期淨額為負。
- 消除常見 N+1。
- 規劃分類改名同步與分類合併。

**Non-Goals:**
- 不做預算模組。
- 不做交易狀態模型。
- 不做 CSV / PDF 匯出。
- 不做對帳單匯入或比對；後續另開 `add-statement-reconciliation`。
- 不強制 `TransactionCreate.category_id` 必填。
- 不移除 `Transaction.category` 字串欄位。
- 不改變 recurring 產生「預期會支付項目」的業務語意。

## Decisions

### D1. Report endpoints are read-only

**選擇**：`get_monthly_report`、`get_monthly_compare_report`、未來 `get_annual_report` 都不呼叫 `recurring_service.generate_recurring_items()`。

**為什麼**：GET 應可重跑、可 cache、可測試；寫入行為應由 explicit command 或排程觸發。現有前端已有 `triggerRecurringGeneration()` 可明確呼叫。

**影響**：使用者若希望報表包含本月 recurring，需先呼叫 `POST /api/accounting/recurring/generate` 或透過排程先產生。

### D2. Hard delete is the source of truth

**選擇**：保留 `db.delete()`，只改文件與 OpenAPI summary。

**為什麼**：owner 確認目前都是直接刪除，不需要軟刪除。

### D3. Card update sync uses default payment method

**選擇**：更新交易卡片時，若 payload 未明確提供 `payment_method`，使用 `card.default_payment_method or "Apple Pay"`。

**為什麼**：create/update 行為一致，避免把 `payment_method` 寫成卡片名稱造成語意漂移。

### D4. Alembic baseline repair must be safe for existing DBs

**選擇**：提供可從空 DB 建 schema 的 baseline/bootstrap，同時定義既有 DB 的 stamp/reconcile 策略。

**為什麼**：目前 migration 不能重建環境；但直接改歷史 baseline 也可能影響已套用的 DB。實作時需先確認 production alembic version 狀態。

可接受方案：
- 若舊 revision 尚未在任何長期環境使用：修正現有 baseline 為完整建表 migration。
- 若舊 revision 已被使用：新增 bootstrap/reconcile migration 或文件化 `alembic stamp` 流程，避免破壞既有 DB。

### D5. Refund guard uses related refund sum

**選擇**：以 `related_transaction_id == original.id AND transaction_type == "INCOME"` 的退款交易加總作為已退金額。

**為什麼**：現有 schema 已有關聯欄位，不需新增 refund ledger。

規則：
- `refund_amount > 0`
- 原交易必須是 `EXPENSE`
- 累計退款不可超過原交易 `transaction_amount`
- 已全額退款後拒絕再次退款

### D6. Card cycle usage can be negative

**選擇**：`EXPENSE - INCOME` 的淨額原樣回傳，可為負。

**為什麼**：淨退款是有效財務狀態，壓成 0 會誤導卡片回饋/門檻顯示。

### D7. Category migration remains incremental

**選擇**：短期保留 `Transaction.category` 字串，但報表讀取優先使用 `category_info.name`，分類改名時同步 legacy 字串。

**為什麼**：不破壞舊資料與前端 payload；同時降低分類改名造成報表漂移的風險。

分類合併採規劃先行：
- merge preview 回傳受影響 transactions/subscriptions 數量。
- merge apply 將 source category 的引用改到 target category。
- 同步 legacy category 字串。
- source category 在引用遷移後刪除。

### D8. Amount types stay integer

**選擇**：所有金額欄位維持 int；百分比維持 float。

**為什麼**：TWD 無小數需求，避免 JSON `123.0` 造成前端與比對邏輯額外 normalize。

## Risks / Trade-offs

- **[Risk] Report behavior changes**：以前打開當月報表可能順手產生 recurring，改成純讀取後資料出現時機改變。
  - Mitigation：保留明確 `POST /recurring/generate`，前端若需要可在使用者操作後呼叫。

- **[Risk] Alembic baseline repair accidentally affects existing DB**
  - Mitigation：實作前查 production `alembic_version`；empty DB 驗證與 existing DB 驗證分開跑。

- **[Risk] Category merge is destructive**
  - Mitigation：先做 preview；apply endpoint 必須明確指定 target category，不做隱式合併。

- **[Trade-off] 不強制 category_id 必填**
  - 接受短期雙軌輸入，避免破壞既有前端；報表與改名同步先降低漂移。

## Migration Plan

1. 確認目前 production/dev DB 的 `alembic_version`。
2. 建立 empty DB，驗證修正後 baseline/bootstrap 可 `alembic upgrade head`。
3. 在 existing DB 跑非破壞性 upgrade/stamp 流程。
4. 補 migration smoke test 或文件化操作步驟。

## Open Questions

- Alembic baseline 是直接修正既有 revision，還是新增 reconcile/bootstrap revision，取決於目前環境是否已套用舊 revision。
- Category merge endpoint 是本 change 實作，或只完成設計後拆成後續 change；目前先保留在規劃與 tasks 中。
