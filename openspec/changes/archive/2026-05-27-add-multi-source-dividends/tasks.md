## 1. Source Package

- [x] 1.1 New `app/services/dividend_sources/__init__.py` with a shared `DividendEventRow` dataclass: `symbol`, `ex_dividend_date`, `cash_dividend` (Decimal | None), `stock_dividend` (Decimal | None), `source` (str)
- [x] 1.2 `dividend_sources/twse_twt48u.py` — `parse_twt48u(raw)` and `fetch_twt48u(year)` returning rows from the existing TWSE TWT48U OpenAPI feed (logic extracted verbatim from current `parse_twse_exdividend_records`)
- [x] 1.3 `dividend_sources/twse_twt49u.py` — `parse_twt49u(raw)` + `fetch_twt49u(year)`. Upstream: `https://openapi.twse.com.tw/v1/exchangeReport/TWT49U`. JSON-only. Maps `公司代號`, `除權息日期` (ROC), `現金股利`, `股票股利`
- [x] 1.4 `dividend_sources/tpex_otc.py` — `parse_tpex_otc(raw)` + `fetch_tpex_otc(year)`. Upstream: `https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ`. JSON-only. Maps cells[0]=ex_date (ROC), cells[1]=symbol, cells[13]=cash, cells[14]=stock-per-thousand → stock-per-share (divide by 1000)
- [x] 1.5 Each module exports the same two functions for orchestrator use

## 2. Orchestrator

- [x] 2.1 New `app/services/dividend_event_service.py`:
  - `fetch_for_holdings(held_symbols: Set[str], *, year: Optional[int] = None) -> list[DividendEventRow]`
  - Defaults `year` to current TW year if missing
  - Runs the three source fetchers; wraps each in `try/except` and logs with structlog `event=dividend_source.failed` plus the source name when one raises
  - Filters to rows whose `symbol` is in `held_symbols`
  - Dedupes by `(symbol, ex_dividend_date)`; first row with a non-null `cash_dividend` (or `stock_dividend`) wins; ties keep first source order: TWT48U > TWT49U > TPEx
  - Sorts ascending by `ex_dividend_date`

## 3. Endpoint

- [x] 3.1 Add `GET /api/portfolio/dividend-events?year=YYYY` to `app/routers/exdividend.py`
  - `year` optional integer query param, defaults to current TW year inside the handler
  - Reads active holdings via existing `portfolio_service.get_active_holdings`
  - Calls `dividend_event_service.fetch_for_holdings`
  - Returns list of dicts mirroring the row shape (Decimals serialised as strings)

## 4. Tests

- [x] 4.1 `tests/unit/test_dividend_sources.py`:
  - TWT48U parser: held-symbol filter, ROC date conversion, decimal extraction
  - TWT49U parser: synthetic JSON fixture, missing-field skip
  - TPEx OTC parser: synthetic JSON fixture, stock-per-thousand conversion, ROC date
  - Each source returns empty when payload missing required fields
- [x] 4.2 `tests/unit/test_dividend_event_service.py`:
  - merges across three sources
  - dedupe by `(symbol, ex_date)`: TWT48U wins over TWT49U over TPEx
  - source raising does not abort other sources
  - held-symbols filter excludes non-held symbols
  - default year resolves to TW current year
- [x] 4.3 Endpoint test: GET returns merged rows for held holdings

## 5. Verification

- [x] 5.1 Full `pytest` — 188 prior + new tests pass
- [x] 5.2 Existing `/api/portfolio/ex-dividends/upcoming` endpoint output byte-identical for the TWT48U path
- [x] 5.3 Manual: hit `/api/portfolio/dividend-events?year=2025` against a real TWSE backend (optional, low priority)
