# 國泰證券日對帳單 auto-import — design

Status: rev 2.2 (2026-10-29), after the AGENT-335 review (findings 1–6 folded) and its re-check (buy tax pinned to 0);
rev 2.2 adds §7 notifications to the scope (owner 2026-10-29: a stuck date must reach the owner, not only a log);
rev 2.3 folds the AGENT-337 review (run deadline, notifier isolation, acknowledgement model), and its re-check
minor (pending envelope); rev 2.4 pins the text extractor (§4.1), a fact found while planning. Owner rulings 2026-10-29: the daily
statement is the primary source for the dates it covers ("對帳單為主，按日期分"); current days matter most and the
archive is used to validate the parser, not to wait for a full backfill; the first writer of a date owns it and a
mismatch is never resolved automatically in either direction (option A — the owner imports the CSV only after the
close, so an intraday partial CSV is not a case to automate). All amounts, names and order numbers below are
fictional.

## 1. Goal

Every Taiwan trade at 國泰證券 reaches the portfolio the morning after the trade, without the owner exporting the
transaction CSV. The CSV import keeps working. On a date that has a statement, the CSV can no longer create
duplicates.

Non-goals: the accounting ledger (settlement cash stays on the 國泰 bank account and its bank statement; R7 §4
settlement matching is not built yet and is not part of this change); 永豐證券 / 期貨 statements; monthly
statements.

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
- 類別 seen: 集買 613, 集賣 241, 沖買 89, 沖賣 82, 資買 1, 資賣 1. 券買 / 券賣 never appeared.
- Every 集買 / 集賣 / 沖買 / 沖賣 line (1025) has line 3 exactly `0 0 0 0 <委託書號>` (after an optional
  `(小樹點數) ` prefix) and line 4 exactly `0`, and its net is exact: buy `net = −(amount + fee)`, sell
  `net = amount − fee − tax`; every buy line (集買 613, 沖買 89) has tax 0. The 資買 / 資賣 lines carry 融資 columns whose meaning the text layer does not fix
  (the 資賣 loan repayment is not on its line at all).
- Footer: `總合計 <成交金額> <手續費> <交易稅>`, totals, `淨收付金額`, then a holdings table
  `代碼 股票名稱 當日收盤價 集保股數 集保市值 融資股數 融券股數` (one row per position held after the day; a position
  sold out that day is absent).
- **Granularity: the statement has one line per (委託, price); the CSV has one row per fill.** The 委託書號 on the
  statement is the CSV 委託書號 plus a two-digit price-group suffix (`XY123` → `XY12300`, `XY12301`). Lines and
  rows agree on totals: Σ quantity, Σ 手續費, Σ 交易稅 of the fills equal the line.
- Matching the archive against the portfolio: 989 of 1027 lines match a portfolio row by business key (symbol,
  type, quantity, price, fee, tax, date); every line of a post-2026-06 CSV import also matches its fingerprint
  (`_transaction_fingerprint` with order_id = 委託書號 minus the suffix); 5 lines are multi-fill aggregates; 25
  are trades after the last CSV import (2026-10-15); 1 is a 資賣 whose stored fee predates the interest fold.
- Three names (14 lines) are not in `name_to_symbol.json`. One resolves through the name stored on existing portfolio
  rows; two (7 lines) resolve neither way and need the holdings-table code or the held path (§5.4 case 3).

## 3. Architecture

Same split as the IB Flex import (R7 §7): the worker fetches and reads the document, the portfolio decides what
it means.

- **Worker** (accounting-service `worker/`, user `homehub-worker`): new job `python -m worker cathay`. It lists the
  Drive folder, decrypts with the password candidates of folder key `投資/國泰證券/日對帳單`, extracts text, parses the
  layout into the payload of §4, runs the self-checks of §4.2 and posts it. Layout knowledge lives here, next to the
  other PDF readers.
- **Portfolio** (stock-portfolio-service): new `POST /api/portfolio/imports/cathay-statement` (JSON body,
  `dry_run` query flag), service `broker_cathay_statement_service`. Cathay semantics live here, next to
  `broker_cathay_service`: 類別 mapping, order number, symbol resolution, date ownership, the CSV interplay.
- Timer `homehub-portfolio-cathay.timer` daily 06:40 Asia/Taipei — after the Apps Script trigger (06:10, which Apps
  Script may start up to 15 minutes late) and before the 07:00 notifier — plus an owner-run command for one file or a
  date range. A statement saved after a run is picked up by the next run.

## 4. Statement payload (worker → portfolio)

```json
{"source": {"drive_file_id": "…", "sha256": "…", "file_name": "…"},
 "trade_date": "2026-03-09", "settlement_date": "2026-03-11", "net_amount": "-12345",
 "lines": [{"name": "範例電子", "category": "集買", "quantity": 1000, "price": "12.3", "amount": "12300",
            "fee": "5", "tax": "0", "net": "-12305", "order_no": "XY12300", "points": false}],
 "totals": {"amount": "12300", "fee": "5", "tax": "0", "net": "-12345"},
 "holdings": [{"code": "9999", "name": "範例電子", "shares": 1000}],
 "warnings": []}
```

### 4.1 Parser grammar (anything else fails the statement)

- Text source: pypdfium2 (pinned 5.14.0 in `requirements-worker.txt`), `PdfDocument(data, password=…)` and each
  page's `get_textpage().get_text_range()`, pages joined by a newline, lines stripped. The grammar below and every
  count in §2 come from this extractor. The worker's general `pdf.extract` (pdfplumber) lays the same PDF out
  differently (net before the item, margin columns and order number on the item line, spaced title) and is not
  used for this job; `pdf.unlock` still picks the password candidate.

- Header `證券日對帳單 <ROC>年 <M>月 <D>日` and the `成交日期 交割日期 …` data line must agree (ROC year + 1911). A
  two-page statement is read as one text (the archive's 167 two-page statements parse this way).
- An item starts at a line matching `<name> <類別> <qty> <price> <amount> <fee> <tax>` where 類別 ∈ {集買, 集賣, 沖買,
  沖賣}; qty a positive integer with optional thousands commas, price a positive decimal, amount / fee / tax
  non-negative integers. Line 2 is one signed integer. Line 3: the optional `(小樹點數) ` prefix is removed first
  (it sets `points`), then the rest must be exactly `0 0 0 0 <order_no>` with order_no `[A-Za-z0-9]{7}`. Line 4 must be
  exactly `0`.
- A line whose 類別 is 資買 / 資賣 / 券買 / 券賣, or any other two-character 類別 in the item position, makes the
  statement **unsupported**: the worker posts no lines for it and the portfolio records the date `held` with reason
  `unsupported_category` (§5.4), so the date stays with the CSV. Margin and short lines are imported by CSV only
  until a later change defines their columns.
- Every other line between the column header and `總合計` fails the statement.

### 4.2 Self-checks (all must hold, else the statement is rejected with a code and not posted)

- Per line: |quantity × price − amount| < 1, and the exact net identity of §2 for its 類別 (this is what pins the fee
  and tax actually booked; no line can carry a fee component the identity does not see).
- Per buy line (集買 / 沖買): tax = 0. The buy identity does not contain tax and the cash legs book any positive tax, so
  a non-zero buy tax rejects the statement. The portfolio applies the same check to the payload (HTTP 422).
- Σ line amount / fee / tax = the `總合計` amount / fee / tax; Σ line net = the header net = `淨收付金額`.
- Holdings rows: code `[0-9A-Z]{4,6}`, shares a non-negative integer.

The file-name date is not a check: the Apps Script names by mail date. A different file-name date only adds
`filename_date_differs` to `warnings`; the statement 成交日期 is the date for ownership, cutover, retry and
everything else.

## 5. Portfolio import rules

### 5.1 Mapping

- 類別 → CSV 買賣別: 集買→現買, 集賣→現賣, 沖買→沖買, 沖賣→沖賣; then `CATHAY_SIDE_MAP` gives type and position side, and
  `broker_day_trade_marker` / `broker_subtype` follow `parse_cathay_rows` (marker 沖買 / 沖賣, else NULL).
- fee = 手續費; tax = 交易稅; trade_date = 成交日期 00:00 UTC (CSV shape).
- Symbol: `resolve_symbol(name)`; else the single symbol stored with that name on existing TW rows; else the
  holdings-table code for that name. Still unresolved → the date is `held` (§5.4).
- Fingerprint: `_broker_transaction_fingerprint(broker="TW_CATHAY_STATEMENT", market="TW", currency="TWD",
  note=<7-char order_no>, …)` — a namespace no CSV or IB Flex row can produce.
- An inserted row gets the same columns as `broker_cathay_service._insert_transaction` (broker NULL), so every reader
  that treats CSV rows as Cathay treats it the same. A "TW Cathay row" below is a transaction with market `TW` and
  broker NULL or `TW_CATHAY`; a `TW_MANUAL` row is never compared and never blocks.

### 5.2 Ownership record

Table `cathay_statement_day`, one row per trade date, two parts:

- **Acceptance (written once, never changed by a later post):** `accepted_kind` ∈ {`imported`, `verified`},
  `accepted_sha256`, `accepted_at`, and `canonical_lines` (jsonb: per line the resolved symbol, type, position side,
  day-trade marker, quantity, price, fee, tax, full 7-char order_no). Both kinds keep the lines; the CSV guard of §5.3
  reads only these.
- **Latest attempt (overwritten by each post):** `last_sha256`, `last_status` ∈ {`imported`, `verified`,
  `duplicate`, `mismatch`, `held`, `conflict`}, `last_reason`, `last_detail` (jsonb), `last_at`.

A date is **owned** once `accepted_kind` is set. `held`, `mismatch` and `conflict` never set or clear acceptance.

- **Acknowledgement (owner, §7.1):** `ack_sha256`, `ack_status`, `ack_at` — the (sha, status) of the latest attempt
  at the moment of the ack. It never changes acceptance or any other column, and never blocks a later import.

### 5.3 Atomicity

The statement commit and the CSV commit (`_commit_rehash`) take the same lock before reading anything for a date:
`pg_advisory_xact_lock` on a key derived from (`cathay_day`, trade_date), one per date, taken in ascending date
order, inside the transaction that writes. Under the lock each path re-reads the ownership row and the date's TW
Cathay rows; nothing decided outside the lock (a dry run, the router's parse) is reused. The day's transactions,
cash legs, day-trade flags and the ownership row commit or roll back together. Dry runs take no lock and write
nothing.

### 5.4 A statement for date D

Under the lock:

1. D owned and `accepted_sha256` equals the payload sha → `duplicate`, nothing written.
2. D owned, different sha → `conflict` in the latest attempt (both line sets in `last_detail`); the accepted part, the
   rows and the CSV guard stay exactly as they were. A corrected statement is an owner decision.
3. D not owned, any line unsupported or any symbol unresolved → `held` with the reason; nothing else written.
4. D not owned, no TW Cathay row on D → insert one transaction per line (cash legs via `sync_transaction_cash_legs`,
   `_recompute_day_trade_flags`, recalc chain — the CSV commit's calls), acceptance `imported`.
5. D not owned, TW Cathay rows exist (the CSV came first) → compare without writing transactions. Group the rows by
   (symbol, type, position side, day-trade marker, price) summing quantity, fee and tax; group the lines the same way.
   Equal → acceptance `verified` with the canonical lines. Unequal → `mismatch` (both groupings in `last_detail`), not
   owned, rows untouched; the next post of the same sha re-compares, so an owner fix of the rows lets it verify.

### 5.5 CSV import on an owned date

`parse_cathay_transactions_csv` splits the parsed rows by trade date. Dry run: rows on dates owned at read time are
previewed as below. Commit: under the §5.3 lock, rows on an owned date never reach the legacy-fingerprint, duplicate
or business-key branches. They are grouped by (symbol, type, position side, day-trade marker, price, 委託書號) and
compared with the canonical lines grouped by (symbol, type, position side, day-trade marker, price, order_no minus
its two-character suffix): equal → each row status `covered` (new, counted apart from `duplicate`); unequal → each
row of that date is an error naming the date, nothing of that date is written. Rows on dates not owned import
exactly as today. The rehash branches additionally never select a row whose fingerprint is in the
`TW_CATHAY_STATEMENT` namespace. The import response and the 匯入 page show `covered` like `duplicate`.

### 5.6 Holdings check

After acceptance, compare each holdings row's 集保股數 with the portfolio's LONG quantity of that symbol after D;
differences go into `last_detail.holdings_diff` and the response. A held date skips the check.

## 6. Worker contract

- Response: HTTP 200 with `{"outcome": <last_status>, "trade_date", "reason", "counts", "holdings_diff",
  "errors": []}`; the worker validates the body (outcome in the set, trade_date equal to the payload's, errors
  empty for `imported` / `verified`) as the Flex job does, and treats a body it cannot validate as transient.
  HTTP 422 = the payload failed the portfolio's own checks (non-transient). 5xx / 408 / 429 / timeout = transient.
- State file (commit runs only; a dry run reads it and never writes it), keyed by Drive file id + sha256:
  `imported`, `verified`, `duplicate`, `conflict` and `mismatch` are terminal for that sha (a new file or sha is
  processed again; a `mismatch` sha is re-posted only by the owner command `--retry <date>` after fixing rows).
  `held` is never terminal: the file is posted again on every run until it is accepted, so a fixed symbol mapping is
  picked up without owner action.
- Latch (`cathay-disabled.json`, like the Flex job) on: decrypt failure with every candidate, any §4.1 / §4.2
  failure, HTTP 422. Unit failed; `--unlatch` after a fix; dry runs never clear it.

## 7. Notifications (in scope)

A date that needs the owner, and a job that stopped, must reach the owner through the existing notifier
(accounting-service `notify/`, Discord); the worker log alone is not enough. The worker runs as `homehub-worker` and
the notifier as the owner's user, so both meet at the portfolio API.

### 7.1 Portfolio read and report endpoints

- `GET /api/portfolio/imports/cathay-statement/pending` → `{"items": [{"trade_date", "status", "reason", "since",
  "last_at"}], "total": <int>, "truncated": <bool>}`:
  every date whose latest attempt is `held` or `mismatch` and that is not owned, plus every owned date whose latest
  attempt is `conflict` — except a date whose (`last_sha256`, `last_status`) equals its (`ack_sha256`,
  `ack_status`). `since` is the first attempt that put the date in this state. Sorted by date, at most 50 items; `total`
  counts every pending date and `truncated` is `total > len(items)`. `reason` is a code plus at most one name, cut to 40 characters.
- `POST /api/portfolio/imports/cathay-statement/runs` (worker, once per commit run, also when the run ends latched or
  failed): `{"run_at", "result": "ok" | "latched" | "failed", "code", "files_seen", "posted"}`. Portfolio keeps the
  latest row overall and the latest `ok` row (`cathay_statement_run`, small table).
- `GET /api/portfolio/imports/cathay-statement/runs/latest` → `{"latest": row | null, "latest_ok": row | null}`.
- Acknowledgement: `python -m worker cathay --ack <date>` posts `POST …/days/<date>/ack`, which stores the date's
  current (`last_sha256`, `last_status`) as the ack. An identical retry (same sha, same status — e.g. a `held` file
  re-posted every run after the owner imported that day by CSV) stays acknowledged; a new sha or a new status is a new
  incident and shows again. Acceptance is never touched, so an acknowledged held date still imports if a later post is
  accepted.
- No auth today, like every portfolio route (issue #117 adds bearer auth for all of them later); the endpoints carry
  dates, statuses and reason codes only, no amounts.

### 7.2 Notifier

- Config: optional `portfolio_base` (e.g. `http://127.0.0.1:8001`); absent → the section is off and nothing else
  changes. `cathay_run_time` (default `06:40`, Asia/Taipei) and `cathay_run_grace_minutes` (default 15).
- Run deadline: the latest scheduled run is the most recent `cathay_run_time` at least `cathay_run_grace_minutes`
  before now. Stale = `latest` is null, or `latest.run_at` is before that scheduled run. The daily notifier runs at
  07:00, so a run missed at 06:40 is reported at 07:00 the same day; a worker that never reported is reported on the
  first run with `portfolio_base` set.
- Daily (`python -m notify daily`): a section 「國泰對帳單」 with one item per pending date
  (`10/27 保留：名稱對不到代號 範例電子`, `10/28 對不上 CSV`, `10/29 收到不同版本的對帳單`), at most 10 items plus
  「另有 N 天」 with N = `total` − the items shown, and one item when the latest run is `latched` / `failed` or stale. Items count like the other daily
  items, so a day with only this section still sends, and the same-day dedupe applies as for every other item.
- Weekly: a new block 「國泰對帳單待處理」 after 「自動扣繳本週跳過」 (`notify/render.py`), listing the same pending
  dates (same cap) and the run state; absent when there is nothing to list.
- Isolation: the portfolio part runs in its own client (timeout 10 s, response body at most 64 KiB) and its own
  step — fetch, strict validation (status 200, the exact shapes above, ISO dates and timestamps, status values in the
  sets, `reason` ≤ 40 characters), normalisation and rendering of the section's lines, each line ≤ 120 characters —
  before the debit items are rendered. Any exception in that step (HTTP error, timeout, oversized body, invalid JSON,
  wrong shape, bad date, unknown status) replaces the whole section with the single item 「國泰對帳單狀態讀不到」;
  the debit and weekly items render and send exactly as without the section.

## 8. Rollout and history

- Cutover = the first date after the last CSV import (2026-10-16). Dates before it run `dry_run` only: the archive
  produces a validation report (would-verify / mismatch / held counts and the mismatch list) for the owner; no write,
  no ownership row. Dates from the cutover commit normally, so 2026-10-16 → today import on the first run.
- The owner adds the password line `投資/國泰證券/日對帳單=$ID` to the worker's password file.
- Deploy order: portfolio (migration + endpoint + CSV change) first, then the worker job and timer.
- Notifier: release the notifier with `portfolio_base` set after the portfolio and the worker are live.

## 9. Tests (fictional fixtures only; never a real statement)

Worker: a fixture text per shape (single line, two pages, 沖買/沖賣 pair, points prefix, two price groups of one
order); an unsupported 資買 line → no lines posted with reason; each §4.1 grammar failure; each §4.2 failure,
including a line whose fee is changed while every footer total is changed to match (the net identity must reject
it) and a buy line whose tax and the footer tax are made non-zero with every net unchanged (rejected before any post,
and the portfolio returns 422 for the same payload with no row written); file-name date differs → warning only; response validation; state file: held re-posted, mismatch not re-posted
without `--retry`, dry run then commit, 5xx retried, 422 latches.
Portfolio: mapping table; fingerprint namespace; §5.4 cases 1–5; §5.5 covered / error / other dates untouched /
rehash never selects a statement row; imported → conflict (corrected sha) → CSV multi-fill upload: transactions and
fingerprints unchanged; CSV-first verified then the same statement again → duplicate; same totals but different
day-trade marker → mismatch; two sessions (CSV commit and statement commit for one date, interleaved at the read):
exactly one writes, the other sees ownership or rows under the lock; a failure after inserting rows rolls back the
ownership row; held then mapping fixed → imported; dry run writes nothing; pending list contents per status, cap and envelope (51 pending dates → 50 items, total 51, truncated; the
notifier shows 10 and 「另有 41 天」);
held → CSV import elsewhere → ack → identical held repost stays acknowledged; a new sha after an ack shows again; a
conflict until ack; ack never touches acceptance and an acknowledged held date still imports; run report latest rows.
Notifier: section off without `portfolio_base`; items for held / mismatch / conflict / latched / failed; stale on
the timeline Monday 06:40 ok → Tuesday 06:40 missed → reported Tuesday 07:00; empty run history → reported; the item
cap; a day with only this section sends; timeout, invalid JSON, a missing field, a bad timestamp, an unknown status and
an oversized body or reason each → one item, and the debit messages are still delivered within the Discord limit.

## 10. Review focus

1. A statement line and a CSV fill must never both become transactions for one date (§5.3 lock, §5.5 guard).
2. A layout change must stop the job, not import a wrong number (§4.1 grammar, §4.2 net identity).
3. A corrected or repeated statement must never overwrite or un-own accepted rows (§5.2, §5.4 cases 1–2).
4. Unresolved names or unsupported lines must not import a partial day (§5.4 case 3).
5. Cash legs and realized P&L of an imported date must equal what the CSV import would have produced: same columns,
   same calls, same day-trade marker.
6. A stuck date or a stopped job must reach the owner's Discord within a day, and a portfolio outage must not
   silence the debit reminders (§7).
