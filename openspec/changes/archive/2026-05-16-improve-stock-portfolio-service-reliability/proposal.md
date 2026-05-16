## Why

`stock-portfolio-service` 已能支援台股投資組合 dashboard，但目前仍有幾個會影響真實使用的風險：TWSE TLS 驗證若直接開啟失敗率高、交易資料缺少 API/DB 邊界驗證、SELL 可賣超、PUT 更新可能靜默清空 optional 欄位，且 quote 失敗時前端缺少明確訊號。

這次 change 的目標是把服務推向更穩定、可觀測、資料更可信的狀態，並把會影響 API shape 的改動明確標示，方便分派給多個 agent 實作與 review。

## What Changes

- **TWSE TLS 與外部資料韌性**
  - 導入 `truststore`，讓 stock service 的 TWSE HTTP client 優先使用 OS trust store。
  - 新增 `TWSE_TLS_MODE`：`fallback` 預設，先 `verify=True`，只有 `SSLError` 才 retry `verify=False`；`verify` 強制驗證；`insecure` emergency only。
  - 將 quote 與除權息 API 共用同一個 TWSE request helper，支援 timeout、有限 retry/backoff、TLS fallback tracing/logging。
  - 新增 in-process TTL cache：quotes 15-60 秒，ex-dividend table 15-60 分鐘。
  - quote failure 時保留 dashboard safe fallback，但提供 summary-level 狀態讓前端可顯示資料不可用或過期。

- **資料完整性與交易正確性**
  - Pydantic schema 加強驗證：symbol trim/non-empty、quantity/price/amount 正值、fee/tax 非負。
  - SQLAlchemy model 加 matching `CheckConstraint`，並新增 Alembic migration。
  - 交易 create/update 時禁止 SELL 超過可用持股；目前不支援 short selling。
  - 修正 `update_transaction` / `update_dividend` 對 optional 欄位的靜默覆蓋問題。
  - 將 Pydantic v2 class-based `Config` 改成 `ConfigDict`，消除 deprecation warning。

- **API 與內部可維護性**
  - 抽出共用 holdings aggregation，讓 summary 與 upcoming ex-dividend 使用同一套 active holding 邏輯。
  - 將 list transactions/dividends 移到 service layer，新增 `limit` / `offset` 與 optional `symbol` filter。
  - 降低 per-symbol quote log volume：per-symbol 改 DEBUG，保留 aggregate INFO。
  - 刪除未使用的 local health router，保留 shared-lib health route 測試。
  - 文件化 `total_dividends` 的 lifetime vs active holdings 語意；需要產品決策後才新增欄位。

- **API shape changes**
  - `PortfolioSummary` 新增 quote 狀態欄位，例如 `quotes_status` 或 `quotes_stale`。這是 response schema 的 additive change，需要前端同步使用但應保持 backward-compatible。
  - List endpoints 新增 optional pagination/filter query params，預設仍回傳最近資料，避免破壞既有呼叫。
  - DELETE 改 204 No Content 屬行為變更，先列為低優先級 optional follow-up，只有確認前端不依賴 body 後才執行。

## Capabilities

### New Capabilities

- `stock-portfolio-data-integrity`: portfolio transaction/dividend validation, SELL availability checks, update semantics, and database constraints.
- `stock-portfolio-market-data-resilience`: TWSE TLS fallback, truststore usage, request retry/cache behavior, quote availability reporting, and market-data observability.
- `stock-portfolio-api-maintainability`: service-layer list behavior, shared holdings aggregation, logging cleanup, health router cleanup, and dividend semantics documentation.

### Modified Capabilities

- None. This repository currently has no active `openspec/specs/` directory, so these are introduced as new capability specs.

## Impact

- **Code**
  - `services/stock-portfolio-service/requirements.txt`
  - `services/stock-portfolio-service/app/main.py` or TWSE client bootstrap location
  - `services/stock-portfolio-service/app/services/twse_service.py`
  - `services/stock-portfolio-service/app/services/exdividend_service.py`
  - `services/stock-portfolio-service/app/services/portfolio_service.py`
  - `services/stock-portfolio-service/app/routers/portfolio.py`
  - `services/stock-portfolio-service/app/routers/exdividend.py`
  - `services/stock-portfolio-service/app/routers/health.py` (delete if confirmed unused)
  - `services/stock-portfolio-service/app/schemas/portfolio.py`
  - `services/stock-portfolio-service/app/models/portfolio.py`
  - `services/stock-portfolio-service/alembic/versions/`
  - `services/stock-portfolio-service/tests/`
  - `services/stock-portfolio-service/SPEC.md`

- **Dependencies**
  - Add `truststore`.
  - No Redis dependency in this change; cache is in-process unless explicitly revised later.

- **API**
  - Additive summary quote-status field.
  - Additive list pagination/filter query params.
  - Potential DELETE 204 change is deferred/optional and requires frontend check.

- **Operational**
  - New env var: `TWSE_TLS_MODE=fallback|verify|insecure`, default `fallback`.
  - Observe TLS fallback rate after deploy. If near zero for one week, consider switching default to `verify`; if high, capture cert-chain details before deciding whether to pin/bundle CA material.

- **Risks**
  - DB constraints can fail migration if existing invalid data exists; run read-only preflight queries before migration.
  - SELL validation must define deterministic same-day ordering; use `(trade_date, id)` where possible.
  - `truststore.inject_into_ssl()` should be scoped to stock service / TWSE client, not shared app factory, unless intentionally changing all Python services.
