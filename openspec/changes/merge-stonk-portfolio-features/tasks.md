## 1. Migrations and Model Columns

- [x] 1.1 Add `is_day_trade BOOLEAN NOT NULL DEFAULT false` and `import_fingerprint VARCHAR(64) NULL UNIQUE` to `transactions`; add `import_fingerprint VARCHAR(64) NULL UNIQUE` to `dividends`
- [x] 1.2 Create Alembic revision `f3a4b5c6d7e8_add_day_trade_and_import_fingerprint` with reversible downgrade
- [x] 1.3 Update SQLAlchemy `Transaction` and `Dividend` models with matching columns + `UniqueConstraint` on fingerprint
- [x] 1.4 Expose `is_day_trade` on `Transaction` response schema only (not `TransactionBase` or `TransactionCreate`); it is server-derived

## 2. Day-Trade Detection

- [x] 2.1 Implement `_trade_calendar_date(trade_date)` UTC helper aligned with existing `_resolve_sort_trade_date`
- [x] 2.2 Implement `_recompute_day_trade_flags(db, symbol, calendar_date)` that flips the flag for all rows in the `(symbol, date)` bucket when both BUY and SELL exist, and clears it otherwise
- [x] 2.3 Wire into `create_transaction` (flush → recompute → commit)
- [x] 2.4 Wire into `update_transaction` (recompute OLD bucket; also NEW bucket if `(symbol, date)` changed)
- [x] 2.5 Wire into `delete_transaction` (recompute remaining rows after row removed)
- [x] 2.6 Unit tests: bucket flip on second leg insert, cross-day no-flip, delete clears flag, update moves bucket scenarios (8 tests)

## 3. CSV Import

- [x] 3.1 Implement `import_service.parse_transactions_csv(content)` and `parse_dividends_csv(content)` with strict column order and per-row validation
- [x] 3.2 SHA256 fingerprint over pipe-joined canonical fields, source-prefixed; stable to whitespace; sensitive to value changes
- [x] 3.3 `commit_transactions(db, parsed, *, dry_run)` and `commit_dividends` — write only if not dry-run; dedupe within file and across prior uploads; reuse `_validate_transaction_ledger` and day-trade recomputation
- [x] 3.4 `POST /api/portfolio/imports/transactions` and `/imports/dividends` with `UploadFile` + `?dry_run=true|false`; 5 MiB cap
- [x] 3.5 Add `python-multipart` to `requirements.txt`
- [x] 3.6 Frontend: `uploadCsv(kind, file, dryRun)` service method + standalone Angular page (PrimeNG `p-fileUpload` + `p-selectButton`, preview button, commit button, parsed-row table, error table, toast)
- [x] 3.7 Add `/portfolio/import` route
- [x] 3.8 Unit tests: parse happy paths, header rejection, 8 row-level validation errors, fingerprint stability + sensitivity, dry-run no-write, cross-upload + within-CSV dedupe, day-trade triggered through import, ledger violation collected, endpoint dry-run + bad header (23 tests)

## 4. TWSE / TPEx Daily OHLC

- [x] 4.1 New `price_history` table: composite PK `(symbol, date)`, NUMERIC(12,4) OHLC, BigInteger volume, source tag, check `close > 0`
- [x] 4.2 Alembic revision `g4b5c6d7e8f9_add_price_history_table` with reversible downgrade
- [x] 4.3 Port `parse_twse_mi_index` (incl. legacy `data9` shape) and `parse_tpex_daily_quotes` from stonk — JSON-only, drop bs4 fallback
- [x] 4.4 Sync HTTP fetch (`_http_get`) honours existing `bootstrap_truststore` + `get_tls_mode`; verify-then-insecure fallback under `TWSE_TLS_MODE=fallback`
- [x] 4.5 `upsert_rows` uses composite-PK `Session.merge` for idempotency across SQLite tests and PostgreSQL prod
- [x] 4.6 `backfill_date(db, date, *, market="TWSE"|"TPEX"|"BOTH")` and `list_history(db, symbol, from_date, to_date)` with `.TW` suffix stripping
- [x] 4.7 `GET /api/portfolio/price-history?symbol=&from=&to=`; `POST /api/portfolio/price-history/backfill?date=&market=` (regex-gated `^(TWSE|TPEX|BOTH)$`)
- [x] 4.8 Register `price_history` model in `app/main.py` and `alembic/env.py`
- [x] 4.9 Unit tests: parsers (4 TWSE + 1 TPEx incl. bytes, legacy shape, missing close skip), 3 upsert idempotency, 1 list_history range, 2 backfill orchestration mocks, 1 GET range endpoint, 1 POST backfill endpoint, 3 parametrized invalid-market rejection (16 tests)

## 5. Verification

- [x] 5.1 All 128 backend unit tests pass
- [x] 5.2 Alembic upgrade head clean from fresh DB; downgrade reversible per step
- [x] 5.3 Frontend proxy `frontend/proxy.conf.js` already covers `/api/portfolio/*` — no edit needed
- [ ] 5.4 Manual end-to-end: upload 50-row CSV via Angular import page; confirm dedupe on second upload
- [ ] 5.5 Manual end-to-end: trigger backfill for a recent trading day; verify `price_history` populated
- [ ] 5.6 Manual end-to-end: same-day BUY+SELL on one symbol → both rows show `is_day_trade=true`
