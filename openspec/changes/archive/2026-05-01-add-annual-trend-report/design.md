## Context

Accounting service 的現有前端使用：
- `GET /api/accounting/transactions/report/{year}/{month}` 取得月報。
- `GET /api/accounting/transactions/report/compare/{year}/{month}` 取得月對月比較。

年度趨勢報表應延續這個路徑風格，而不是引入新的 `/reports/monthly` 命名。資料量是個人/家庭使用情境，年度交易筆數可一次撈出後在 Python 端聚合。

## Goals / Non-Goals

**Goals:**
- 提供年度趨勢 API。
- 固定回傳 12 個月份，方便前端 chart 直接繪製。
- 分類趨勢回完整列表，由前端決定 top N 顯示。
- 報表查詢保持純讀取。

**Non-Goals:**
- 不做 rolling 12 months。
- 不做年同比 YoY。
- 不做預算線或 budget overlay。
- 不做 CSV/PDF 匯出。
- 不在報表查詢時自動產生 recurring transactions。

## Decisions

### D1. Endpoint path follows existing transaction report routes

**選擇**：`GET /api/accounting/transactions/report/annual/{year}`。

**為什麼**：現有月報與月比月都掛在 `/transactions/report/...` 下，這樣前端 service 與 router 結構最小改動。

### D2. Report query is read-only

**選擇**：年度報表不呼叫 `recurring_service.generate_recurring_items()`。

**為什麼**：和 `improve-accounting-data-integrity` 的報表純讀決策一致。若使用者需要 recurring 項目進入報表，需先透過明確生成操作或排程產生 transactions。

### D3. monthly_trend follows year scope

**選擇**：已完成年度回傳 12 個月份；當年度先回傳從 1 月到目前月份的 year-to-date 範圍，避免顯示尚未發生的月份。

**為什麼**：系統目前資料尚未滿一年時，year-to-date 呈現比補滿未來月份的 0 更符合使用情境；已完成年度仍維持完整 12 個月份。

### D4. category_trend returns all categories

**選擇**：後端回完整分類列表，依 `total` desc 排序。

**為什麼**：top N 是 UI 決策，資料量小，全量回傳成本可接受。

### D5. Aggregation uses one annual transaction query

**選擇**：一次查出該年度 transactions，Python 端 group by month/category。

**為什麼**：避免 12 次查詢，實作簡單且足夠快。若未來資料量大幅增加再改 DB-side aggregation。

### D6. Category names follow data-integrity rules

**選擇**：分類名稱以 `transaction.category_info.name` 優先，fallback 至 legacy `transaction.category`。

**為什麼**：與分類改名同步規劃一致，兼容舊資料。

### D7. highest/lowest expense month behavior

**選擇**：
- `highest_expense_month`：如果全年支出皆 0，回 `null`；否則回支出最高月份。
- `lowest_expense_month`：忽略支出為 0 的月份；若全年無支出，回 `null`。

**為什麼**：避免把沒有交易的月份誤導成「最低支出月」。

## Risks / Trade-offs

- **[Risk] 使用者期待報表自動補 recurring**
  - Mitigation：文件與 UI 明確保留「產生定期項目」操作；報表本身不寫資料。

- **[Risk] 一次撈全年資料未來變慢**
  - Mitigation：目前資料量可接受；保留後續改 server-side aggregation 的空間。

- **[Trade-off] Endpoint 掛在 transactions router**
  - 接受。它和既有月報一致；若未來 report endpoints 變多，可再拆 `reports.py`。

## Open Questions

- 前端年度趨勢放在既有 accounting dashboard 內的 tab，或獨立 route。實作時依現有 UI 結構決定。
