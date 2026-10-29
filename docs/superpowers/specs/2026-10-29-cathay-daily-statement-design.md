# 國泰證券日對帳單 auto-import — design

Status: rev 1 (2026-10-29). Owner rulings 2026-10-29: the daily statement is the primary source for the dates it
covers ("對帳單為主，按日期分"); current days matter most and the archive is used to validate the parser, not to
wait for a full backfill. All amounts, names and order numbers below are fictional.

## 1. Goal

Every Taiwan trade at 國泰證券 reaches the portfolio the morning after the trade, without the owner exporting the
transaction CSV. The CSV import keeps working. On a date that has a statement, the CSV can no longer create
duplicates.

Non-goals: the accounting ledger (settlement cash stays on the 國泰 bank account and its bank statement; R7 §4
settlement matching is not built yet and is not part of this change); 永豐證券 / 期貨 statements; monthly
statements; push notifications (§7.3 names the follow-up).

## 2. Facts (from 319 archived statements, 2024-01 → 2026-10)

- Mail: sender `e-notification@ebill1.cathaysec.com.tw`, subject `國泰綜合證券日對帳單` (no date), one attachment
  `國泰證券日對帳單.pdf` plus `smime.p7s`, sent the trade day between about 19:50 and 20:30 Asia/Taipei, only on
  days with a fill. The Gmail→Drive Apps Script v6.1 (`cathay-securities-daily` route) saves it under
  `財務對帳單/投資/國泰證券/日對帳單/` as `YYYY-MM-DD_國泰證券_日對帳單_<msgid10>_01_國泰證券日對帳單.pdf` (mail date in
  Asia/Taipei). Its daily trigger runs at 06:10 Asia/Taipei, so day D is in Drive by D+1 06:30.
- PDF: 1–2 pages, text layer (pdfium extracts it; no OCR), encrypted with the statement ID-number password.
- Header: `證券日對帳單 115年 3月 9日`, then one line `成交日期 交割日期 預收款 留置款 淨收付`, for example
  `2026/03/09 2026/03/11 0 0 -12,345`.
- One block per line item, four text lines:
  1. `<商品名稱> <類別> <成交股數> <單價> <成交金額> <手續費> <交易稅>`, for example `範例電子 集買 1,000 12.3 12,300 5 0`
  2. `<客戶應收付額>` signed, for example `-12,305`
  3. margin columns then the 7-character `委託書號` last, for example `0 0 0 0 XY12300`; a fee paid with points
     prefixes the line with `(小樹點數)`; a 資賣 line carries the 融資利息 here (`0 23 XY45600` = interest 23)
  4. `0` (債息利息)
- 類別 seen: 集買 608, 集賣 241, 沖買 89, 沖賣 82, 資買 1, 資賣 1. 券買 / 券賣 never appeared.
- Footer: `總合計 <成交金額> <手續費> <交易稅>`, totals, `淨收付金額`, then a holdings table
  `代碼 股票名稱 當日收盤價 集保股數 集保市值 融資股數 融券股數` (one row per position held after the day; a position
  sold out that day is absent).
- **Granularity: the statement has one line per (委託, price); the CSV has one row per fill.** The 委託書號 on the
  statement is the CSV 委託書號 plus a two-digit price-group suffix (`XY123` → `XY12300`, `XY12301`). Lines and
  rows agree on totals: Σ quantity, Σ 手續費, Σ 交易稅 of the fills equal the line.
- Matching the archive against the portfolio: 989 of 1022 lines match a portfolio row by business key (symbol,
  type, quantity, price, fee, tax, date); every line of a post-2026-06 CSV import also matches its fingerprint
  (`_transaction_fingerprint` with order_id = 委託書號 minus the suffix); 5 lines are multi-fill aggregates; 25
  are trades after the last CSV import (2026-10-15); 1 is a 資賣 whose stored fee predates the interest fold.
- Three names are not in `name_to_symbol.json`; each is stored on existing portfolio rows with one symbol.

## 3. Architecture

Same split as the IB Flex import (R7 §7): the worker fetches and reads the document, the portfolio decides what
it means.

- **Worker** (accounting-service `worker/`, user `homehub-worker`): new job `python -m worker cathay`. It lists the
  Drive folder, skips files already done (state file keyed by Drive file id + sha256), decrypts with the password
  candidates of folder key `投資/國泰證券/日對帳單`, extracts text, parses the layout into the neutral JSON of §4,
  checks it (§4.2) and posts it. Layout knowledge lives here, next to the other PDF readers.
- **Portfolio** (stock-portfolio-service): new `POST /api/portfolio/imports/cathay-statement` (JSON body,
  `dry_run` query flag), service `broker_cathay_statement_service`. Cathay semantics live here, next to
  `broker_cathay_service`: 類別 mapping, order number, symbol resolution, date ownership, the CSV interplay.
- Timer `homehub-portfolio-cathay.timer` daily 07:30 Asia/Taipei (after the Apps Script run) plus an owner-run
  command for one file or a date range.

## 4. Statement payload (worker → portfolio)

```json
{"source": {"drive_file_id": "…", "sha256": "…", "file_name": "…"},
 "trade_date": "2026-03-09", "settlement_date": "2026-03-11", "net_amount": "-12345",
 "lines": [{"name": "範例電子", "category": "集買", "quantity": 1000, "price": "12.3", "amount": "12300",
            "fee": "5", "tax": "0", "interest": "0", "borrow_fee": "0", "net": "-12305", "order_no": "XY12300",
            "points": false}],
 "totals": {"amount": "12300", "fee": "5", "tax": "0", "net": "-12345"},
 "holdings": [{"code": "9999", "name": "範例電子", "shares": 1000}]}
```

### 4.1 Parser rules

Header date and the `成交日期` line must agree (ROC year + 1911). Line items are recognised by the first-line
pattern and read as the four-line block above; any other text inside the item section fails the statement. The
order number is the last token of line 3, `[A-Za-z0-9]{7}`. 資賣 interest is the middle number of a three-token
line 3. A two-page statement is read as one text (the archive's 167 two-page statements parse this way).

### 4.2 Self-checks (all must hold, else the statement is rejected with a code, never posted)

- Σ line 手續費 = 總合計 手續費, Σ 交易稅 = 總合計 交易稅, Σ 成交金額 = 總合計 成交金額.
- Σ line net = header 淨收付 = footer 淨收付金額.
- Each line: |quantity × price − amount| < 1.
- The file-name date equals 成交日期 (the Apps Script names by mail date; a mismatch is reported, the statement date
  wins).

## 5. Portfolio import rules

### 5.1 Mapping

- 類別 → CSV 買賣別: 集買→現買, 集賣→現賣, 沖買→沖買, 沖賣→沖賣, 資買→資買, 資賣→資賣, 券買→券買, 券賣→券賣;
  then `CATHAY_SIDE_MAP` gives type and position side, and `broker_day_trade_marker` / `broker_subtype` follow
  `parse_cathay_rows`. Any other 類別 rejects the statement.
- fee = 手續費 + interest + borrow_fee (the CSV fold); tax = 交易稅; trade_date = 成交日期 00:00 UTC (CSV shape).
- Symbol: `resolve_symbol(name)`; else the single symbol stored with that name on existing TW rows; else the
  holdings table code for that name. Still unresolved → the whole date is held (§5.4).
- Fingerprint: `_broker_transaction_fingerprint(broker="TW_CATHAY_STATEMENT", market="TW", currency="TWD",
  note=<7-char order_no>, …)` — a namespace no CSV row can produce, so a statement line and a fill never share a
  fingerprint.

### 5.2 Date ownership

New table `cathay_statement_day(trade_date PK, sha256, drive_file_id, status, line_count, net_amount, imported_at,
detail jsonb)`, status ∈ `imported | verified | mismatch | held`. Exactly one source owns a date's rows.

On a statement for date D:

A "TW Cathay row" is a transaction with market `TW` and broker NULL or `TW_CATHAY` (a `TW_MANUAL` row is
never compared and never blocks). Inserted rows get the same columns as `broker_cathay_service._insert_transaction`
(broker NULL), so every reader that treats CSV rows as Cathay treats them the same.

1. Portfolio has no TW Cathay row on D → insert one transaction per line (cash legs via
   `sync_transaction_cash_legs`, day-trade flags, recalc chain — same calls as the CSV commit), status `imported`.
2. Portfolio already has rows on D (the CSV came first) → compare without writing: group the rows by
   (symbol, type, position side, price) summing quantity, fee and tax; group the lines the same way. Equal →
   `verified`. Unequal → `mismatch` with both groupings in `detail`; nothing written.
3. The same sha256 again → no-op. A different file for an `imported` / `verified` date → `mismatch` (a corrected
   statement is an owner decision, never an overwrite).

### 5.3 CSV import on a statement-owned date

`parse_cathay_transactions_csv` (dry run and commit) first splits rows by date. Rows on a date with status
`imported` or `verified` never insert or rehash: grouped by (symbol, type, position side, price, 委託書號) they must
equal the statement lines grouped by (symbol, type, position side, price, order_no minus suffix); equal → row status
`covered` (new, counted apart from `duplicate`); unequal → the row is an error naming the date. Other dates import
exactly as today. The import response and the 匯入 page show `covered` like `duplicate`.

### 5.4 Held dates

An unresolved symbol or a portfolio-side rejection stores status `held` with the reason; the date stays CSV-owned,
so the owner can import that day's CSV with the existing override flow. A later statement post for a held date is
retried.

### 5.5 Holdings check

After an `imported` / `verified` date, compare each holdings row's 集保股數 with the portfolio's LONG quantity of
that symbol after D; the differences go into `detail.holdings_diff` and the response. Margin (融資股數) is out of
scope for the check.

## 6. Rollout and history

- Cutover = the first date after the last CSV import (2026-10-16). Dates before it run `dry_run` only: the archive
  produces a validation report (verified / mismatch / held counts, mismatch list) for the owner; no write, no
  ownership row. Dates from the cutover commit normally, so 2026-10-16 → today import on the first run.
- The owner adds the password line for folder `投資/國泰證券/日對帳單` (same rule as the other ID-number
  statements) — a secret write, done by the owner.
- Deploy order: portfolio (migration + endpoint + CSV change) first, then the worker job and timer.

## 7. Failure handling

1. Password, decrypt, layout or self-check failure: non-transient → latch like the Flex job
   (`cathay-disabled.json`), unit failed; `--unlatch` after a fix.
2. Portfolio 5xx / timeout: transient, retried next run; the state file marks the file done only on a 2xx.
3. Notifications: the worker summary and journald only; a daily-notifier line for `mismatch` / `held` / latch is a
   follow-up issue.

## 8. Tests (fictional fixtures only; never a real statement)

Worker: a fixture text per shape (single line, two pages, 沖買/沖賣 pair, 資買, 資賣 with interest, points prefix,
two price groups of one order); each self-check failure; filename-date mismatch; state file skip and retry.
Portfolio: mapping table; fingerprint namespace; §5.2 cases 1–3; §5.3 covered / error / untouched other dates;
held then retried; holdings diff; dry run writes nothing.

## 9. Review focus

1. A statement line and a CSV fill must never both become transactions for one date.
2. A layout change must stop the job, not import a wrong number.
3. A corrected or repeated statement must never overwrite owned rows.
4. Unresolved names must not import a partial day.
5. Cash legs and realized P&L of an imported date must equal what the CSV import would have produced.
