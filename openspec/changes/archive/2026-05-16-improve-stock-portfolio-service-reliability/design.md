## Context

`stock-portfolio-service` 是 Python/FastAPI service，負責台股交易、股利紀錄、portfolio summary、TWSE 即時報價與除權息提醒。現況已有 Alembic、Pydantic schema、SQLAlchemy model、OpenTelemetry tracing，以及 `shared-python-lib` 提供 app factory / health routes。

這次 change 的主要約束：

- Owner 過去嘗試將 TWSE `requests.get(..., verify=True)` 直接打開時失敗率很高，因此不能只把 `verify=False` 改成硬性 `verify=True`。
- 服務偏個人/內部使用，availability 不能因 TLS 理想化方案大幅下降。
- 不引入 Redis；market-data cache 先採 in-process TTL。
- 不把 TWSE-specific TLS 行為放進 `shared_lib.create_app()`，避免影響 accounting-service 等其他 Python services。
- 優先保留既有 API shape；需要 response schema additive change 時必須明確標示。

## Goals / Non-Goals

**Goals:**

- 將 TWSE request 從「永遠 `verify=False`」提升為「先驗證 TLS，只有 TLS 驗證錯誤才 fallback」。
- 透過 `truststore` 降低 Python/certifi trust store 造成的 TLS false negative。
- 將 TWSE quote / ex-dividend request 的 timeout、retry、TLS fallback、cache、logging、tracing 收斂到共用 helper。
- 在 API 與 DB 邊界阻擋明顯非法 portfolio 資料。
- 阻擋 SELL 超過可用持股，修正 update optional field 靜默清空。
- 抽出共用 holdings aggregation，減少 summary 與 ex-dividend 的邏輯漂移。
- 消除 Pydantic v2 deprecation warning 與未使用 local health router。

**Non-Goals:**

- 不支援 short selling。
- 不新增 Redis / distributed cache。
- 不全面重寫 summary 成 DB-side aggregation 或 holdings snapshot table。
- 不在本輪解決 multi-user / tenancy。
- 不把 `truststore.inject_into_ssl()` 放進 shared app factory。
- 不強制把 DELETE endpoint 改成 204；這是低優先級 follow-up，需先確認前端不依賴 response body。
- 不在未決策前改變 `total_dividends` 既有語意；先文件化或以 additive field 補足。

## Decisions

### D1. `TWSE_TLS_MODE=fallback` 是預設

**選擇**：新增 `TWSE_TLS_MODE`，允許三種值：

- `fallback`：預設。先 `verify=True`；只有 `requests.exceptions.SSLError` 時 retry 一次 `verify=False`。
- `verify`：永遠 `verify=True`，TLS error 直接失敗。
- `insecure`：永遠 `verify=False`，emergency only，必須 log warning。

**為什麼**：

- Owner 已確認直接 `verify=True` 實務失敗率高。
- `fallback` 比現況永遠 `verify=False` 安全，且不犧牲可用性。
- mode enum 比原本 `TWSE_SSL_VERIFY=true/false` 更能表達 operational intent。

**替代方案**：

- `verify=True` default：安全但已知會 regress availability。
- 沿用 `TWSE_SSL_VERIFY`：相容但語意不足，無法清楚表示「先驗證再 fallback」。

### D2. `truststore` 只在 stock/TWSE client bootstrap 注入

**選擇**：在 stock service 的 TWSE client/helper module 內做一次性 `truststore.inject_into_ssl()`，並確保 idempotent；不放進 `shared_lib.create_app()`。

**為什麼**：

- TLS 問題目前只在 TWSE request 上明確存在。
- shared app factory 被其他 services 共用，全域改變 TLS 行為會擴大 blast radius。

**替代方案**：

- 放 shared lib：可讓所有 service 受益，但會改變 unrelated outbound TLS 行為。
- 不用 `truststore`：fallback 會更常發生，安全改善幅度較小。

### D3. TWSE request helper 負責 request policy，不負責 business parsing

**選擇**：新增或抽出 TWSE request helper，例如 `app/services/twse_client.py`，只處理：

- URL request
- timeout
- limited retry/backoff
- TLS mode/fallback
- TTL cache primitive
- logging/tracing metadata

`twse_service.py` 保留 quote JSON parsing，`exdividend_service.py` 保留除權息 table parsing。

**為什麼**：

- request policy 是重複橫切關注點；business parsing 不應混在 generic client 內。
- 測試可以分層：client 測 TLS/cache，service 測 parsing。

### D4. Update paths 保留 PUT endpoint，但採 partial-update 行為

**選擇**：短期保留既有 `PUT /transactions/{id}` 與 `PUT /dividends/{id}`，但 update service 使用 `model_dump(exclude_unset=True)` 或 dedicated update schemas，避免 omitted optional fields 被寫成 `None`。

**為什麼**：

- 現有 API 已是 PUT；改 PATCH 或要求 full replacement 可能破壞前端。
- 使用者觀感上現有 PUT 更像 partial update。先修 data loss，再視需要補 PATCH。

**替代方案**：

- 新增 PATCH：語意更正確，但前端需要改用新 endpoint。
- 保持 full PUT：會保留 silent data loss 風險。

### D5. SELL validation 以 deterministic ledger ordering 計算

**選擇**：SELL create/update 驗證使用 `symbol` 正規化後的 ledger。排序採 `(trade_date, id)`；未入庫的新交易在同日排序時視為最後一筆。update 時排除被更新交易，再把新版本放入 ledger 驗證。

**為什麼**：

- 同一天多筆交易只用 `trade_date` 不 deterministic。
- `(trade_date, id)` 與現有 integer PK 配合，容易理解與測試。

**替代方案**：

- 僅看總持股：無法阻擋時間序上先賣後買的非法 ledger。
- 允許負持股：等同 short selling，與現有產品語意不符。

### D6. Quote failure 用 additive summary status 表達

**選擇**：`PortfolioSummary` 新增 additive field，例如：

```python
quotes_status: Literal["ok", "partial", "unavailable"] = "ok"
```

當 quote API 回傳空資料但仍有 active holdings 時為 `unavailable`；部分 active symbols 缺 quote 時為 `partial`。前端可據此顯示 banner。現有 numeric fallback 保留，避免 dashboard crash。

**為什麼**：

- 現況 quote failure 會讓價格全為 0，使用者無法分辨 API 掛了還是真的沒資料。
- Additive field 對既有 consumer 風險最低。

### D7. Pagination/filter 是 additive，DELETE 204 延後

**選擇**：list endpoints 新增 optional `limit` / `offset` / `symbol`，但不移除既有 route；DELETE 204 不列入必要 acceptance criteria。

**為什麼**：

- Pagination/filter 是低風險改善。
- DELETE 200 body 改 204 雖較 RESTful，但可能破壞前端對 response body 的假設，價值較低。

## Risks / Trade-offs

- **[Risk] TLS fallback 可能掩蓋真正的憑證問題** → Mitigation: fallback 只對 `SSLError` 生效；log warning；trace `tls.fallback=true`；deploy 後觀察 fallback rate 一週。
- **[Risk] `truststore` 行為依 OS / container CA 狀態而異** → Mitigation: 保留 fallback mode；測試 verify path 與 fallback path；部署文件列出 Linux `ca-certificates` 前提。
- **[Risk] In-process cache 在多 instance 下不共享** → Mitigation: 本輪不引入 Redis；若未來多 instance 或 rate-limit 壓力增加，再做 distributed cache。
- **[Risk] DB constraints migration 可能因既有壞資料失敗** → Mitigation: migration 前跑 read-only preflight SQL；若有壞資料，由 owner 決定 fail migration 或先清理。
- **[Risk] SELL validation 與既有壞 ledger 衝突** → Mitigation: 新增 validation 前先測現有交易是否存在負持股序列；測試覆蓋 same-day ordering。
- **[Risk] Response additive field 需要前端理解才有 UX 效益** → Mitigation: schema default 保持 backward-compatible；frontend follow-up 使用 `quotes_status` 顯示 banner。

## Migration Plan

1. 跑 stock portfolio baseline tests。
2. 在 production DB 執行 read-only invalid data preflight SQL。
3. 若資料乾淨，套用 Alembic constraints/index migration；若不乾淨，先產出清單給 owner 決定修正方式。
4. 部署 code changes。
5. 驗證 `/health`、`/health/ready`、`/api/portfolio/summary`、`/api/portfolio/ex-dividends/upcoming`。
6. 觀察 TWSE TLS fallback log/trace 一週。

Rollback:

- Code rollback 可恢復舊 request/validation 行為。
- DB constraints rollback 需 Alembic downgrade；若期間已有新合法資料，downgrade 應不丟資料。
- `TWSE_TLS_MODE=insecure` 可作短期 emergency mitigation，但必須留下 warning log。

## Open Questions

- `quotes_status` 欄位名稱採 `quotes_status` 還是 `market_data_status`？
- `total_dividends` 要保持 lifetime 語意並文件化，還是新增 `active_total_dividends`？
- `limit` default/max 要用 `200/1000` 還是依前端需求調整？
- DELETE 204 是否要納入本輪，或保持在後續 optional cleanup？
