# 永豐期貨對帳單 auto-import — design

Status: rev 1.2 (2026-11-25). Rev 1.1 folds the builder check (a day-to-day `prev` finding, the scope of the frozen
note, no 永豐 money already in the portfolio); rev 1.2 folds the AGENT-589 review (text columns read in order, the
per-broker valuation dispatch, the cash-refresh interplay and no commit inside the futures refresh, the monthly
`carry` finding, the lock tag, the missing-snapshot-row limit); rev 1.3 folds the AGENT-591 re-check (one futures
lock for every futures write and every snapshot writer, futures source dates in the replay, the remaining grammar
guards, a valid zero total in the chart, the findings / ack wire contract, the successor recheck). No further Multica
round on the spec (owner rule: one review plus one re-check); the plan review covers what follows. Rev 1.4 (plan
review AGENT-592): the §5.1 example is made internally consistent (it is the shared test fixture), and
`--validate-history` replays the whole archive in one rolled-back portfolio transaction (§7). Owner rulings 2026-11-25: on a day without a statement the account's equity is the
latest statement's value, and the monthly statement's month-end value corrects it (no re-pricing from exchange
settlement prices); storage is one row per statement day with the fills and positions kept as JSON, not normalised
tables; net worth uses 權益總值 (it includes the market value of open options). All amounts, product names and order
numbers below are fictional.

## 1. Goal

The 永豐期貨 account reaches HomeHub every morning without the owner exporting anything: its equity counts in the
portfolio net worth and in accounting account 147 (永豐期貨), and its balance, margins, realised and unrealised
profit and loss, fees and futures tax are on record per day and per month.

Non-goals:

- Re-pricing open positions on days without a statement (owner ruling; a later change can add exchange settlement
  prices on top of this one).
- Futures or options as portfolio transactions or holdings. A futures fill is not a stock trade, and nothing in this
  change writes `transactions`, cash legs or `price_history`.
- Reports per product or per contract month (the JSON keeps what they would need).
- The accounting ledger: deposits and withdrawals stay as the owner's transfers into account 147; matching them to
  the statement's 存提 lines is not part of this change.
- 永豐證券 stock statements; foreign futures (none exist in the archive, and a statement showing any is rejected).

## 2. Facts (88 daily statements 2024-08-27 → 2026-11-23, 27 monthly statements 2024-08 → 2026-10)

- Mail: sender `service@bhu.sinotrade.com.tw`, subjects `期貨日對帳單` / `期貨月對帳單` without a date, one PDF plus
  `smime.p7s`, the daily one around 17:55 on the statement day. The Gmail→Drive Apps Script (v6.2, routes
  `sinopac-futures-daily` and `sinopac-futures-monthly`, owner-maintained) saves them under
  `財務對帳單/投資/永豐期貨/日對帳單/` as `YYYY-MM-DD_永豐期貨_日對帳單_<msgid10>_01_YYYY年M月D日期貨日對帳單.pdf` and
  `…/月對帳單/` as `YYYY-MM_永豐期貨_月對帳單_<msgid10>_01_YYYY年MM月期貨月對帳單.pdf`. The trigger runs at 06:10
  Asia/Taipei, so day D is in Drive by D+1 06:30.
- PDF: RC4 128-bit, user password = the ID number (`$ID`, the rule of `投資/國泰證券/日對帳單`); every file unlocks and
  has a text layer (no OCR). Daily: two pages, page 2 is notices only. Monthly: two to four pages.
- A daily statement is mailed on days with account activity: 80 have fills, 8 print `無交易`. Daily coverage is not
  complete: five activity dates in the monthly statements have no daily statement in Drive (four trade dates, one of
  them also a deposit date, and one deposit-only date). Positions are often held across days without a statement
  (the longest stretch: 58 weekdays).
- Monthly against daily (27 months): B (存提淨額), G, H and K equal the sums of the month's daily statements, and M equals
  the last daily statement's 本日餘額, in every month whose activity dates all have a daily statement; every
  difference falls in one of the four months with a missing daily statement (or, for the continuity check of A, in the
  month after one). Per trade date, the monthly 交易稅 / 手續費 equal the daily `總計` (73 of 73 dates present in both).
  In the 12 months whose last daily statement shows no open position, the monthly 權益總值 equals that daily value.
  Month to month: A equals the previous monthly statement's M in 26 of 26.
- Day to day: a daily 前日餘額 equals the previous daily statement's 本日餘額 in 82 of 87 pairs; the 5 exceptions are
  exactly the first daily statements after the 5 activity dates without one.
- The portfolio has no 永豐 broker account, cash transaction or transaction (brokers on record: 國泰, IB, Firstrade,
  CS), so nothing already counts this money.
- Every daily statement has four sections, each opened by a marker line `交易幣別：新台幣(NTT) ＊國內市場<name>＊`:
  `存提`, `交易紀錄`, `留倉彙總`, `權益帳`. Header lines: the title `國內期貨日對帳單`, a greeting with the name the
  broker already masks, and `期貨帳號：… 證券帳號：…` (masked by the broker).
- Numbers are right-aligned: a value's right edge equals its column label's right edge within 3 pt (every numeric
  word on every fill and position line: 1502 and 748 of them), and an empty value is simply absent from the value row.
  Numeric columns must therefore be read by x position, not by token order. Text values are not aligned to their
  labels: the exchange `Taiwan F` extracts as two words 1.7 pt apart whose right edge matches no label, and product
  names on position lines miss their label in 42 cases; the gaps between other adjacent text words are 4.4 pt or more.
- Products in the archive: 微型臺指期貨, 台指選擇權, weekly options (`W1週台選` … `W5週台選`, `F1週台選` … `F3週台選`),
  stock futures (two names). Exchange column always `Taiwan F`; currency always 新台幣. An option line carries a 履約價格
  and a 權別 of `Call` / `Put`; a futures line has neither.
- The statement date: every dated line of `存提` and `交易紀錄` carries the same date (`無存提` / `無交易` lines too), and it
  equals the file-name date in 88 of 88.
- Identities that hold on every archived statement (the self-checks of §4.2):
  - daily, 88 of 88: 本日餘額 = 前日餘額 + 存提淨額 − 期交稅手續費 + 本日期貨平倉損益淨額 + 權利金收支與轉現價款;
    本日權益數 = 本日餘額 + 未沖銷期貨浮動損益; 權益總值 = 本日權益數 + Σ 留倉 權利金市值 (a sold option counts negative),
    which also equals the `未沖銷選擇權市場價值淨額` line when that line is printed; 本日期貨平倉損益淨額 = Σ of the
    futures fills' `權利金/平倉損益`; 權利金收支與轉現價款 = Σ of the option fills' `權利金/平倉損益`; 未沖銷期貨浮動損益 = Σ of
    the futures positions' `未沖銷浮動損益`; 存提淨額 = Σ 入金 − Σ 出金;
  - daily with fills, 80 of 80: the `總計` columns 期交稅, 手續費 and 權利金/平倉損益 equal the sums over the fill lines,
    and 期交稅手續費 = 總計.期交稅 + 總計.手續費;
  - monthly, 27 of 27: M = A + B + G − H + K; Q = M + N; 權益總值 = Q + 未沖銷買方權利金市值 − 未沖銷賣方權利金市值 (no
    month-end option position exists in the archive, so the sign of the last term is taken from the daily rule).

## 3. Approaches

Valuation on days without a statement (owner ruling 2026-11-25):

- **Carry forward plus month-end correction (chosen).** The latest statement's 權益總值 holds until the next daily
  or monthly statement. No new data source; the number is exact on every statement day and every month end, and
  frozen in between while positions are open (the UI says so).
- Re-price open positions daily from exchange settlement prices. Exact every day, but needs a new price feed,
  contract multipliers and option pricing for every product; deferred.
- Daily statements only. Simplest, but five activity days have no daily statement, so the balance would stay wrong
  until the next one; rejected.

Storage (owner ruling 2026-11-25):

- **One row per statement day (chosen),** every 權益帳 field as a column, fills / positions / deposits as JSON as
  printed; one row per monthly statement. Everything the goal needs is a column; per-contract reports can read the
  JSON later.
- Normalised `futures_fill` / `futures_position` tables. More schema and migration surface for queries nobody runs
  yet; rejected for now.

## 4. Architecture

Same split as the 國泰 import: the worker reads the document, the portfolio decides what it means.

- **Worker** (accounting-service `worker/`, user `homehub-worker`): new job `python -m worker sinopac-futures`. It
  lists both Drive folders, decrypts with the password candidates of folder keys `投資/永豐期貨/日對帳單` and
  `投資/永豐期貨/月對帳單`, extracts words with positions, parses the layout into the payloads of §5, runs the
  self-checks of §5.3 and posts them, daily statements in date order before the monthly ones.
- **Portfolio** (stock-portfolio-service): `POST /api/portfolio/imports/sinopac-futures-statement` (JSON body,
  `dry_run` query flag), service `sinopac_futures_statement_service`; tables of §6.1; the equity series of §6.3, which
  feeds `portfolio_snapshot` and the ledger-feed valuation.
- **Accounting:** account 147 gets `valuation_source = portfolio:sinopac_futures`, the read-through the 國泰 account
  117 already uses; its balance shows the futures equity, its ledger stays the owner's transfers.
- Timer `homehub-portfolio-sinopac-futures.timer` daily 06:45 Asia/Taipei (after the 06:10 Apps Script trigger, up to
  15 minutes late, and the 06:40 國泰 job; before the 07:00 notifier), plus the owner-run flags of §7.

## 5. Statement payloads (worker → portfolio)

Amounts are decimal strings, signed as printed; an empty column is `"0"`. No name, account number, bank or branch is
posted.

### 5.1 Daily

```json
{"kind": "daily",
 "source": {"drive_file_id": "…", "sha256": "…", "file_name": "…"},
 "statement_date": "2026-03-09",
 "equity": {"prev_balance": "100000", "deposits_net": "50000", "fees_and_tax": "34",
            "futures_realized": "1500", "premium_net": "-300", "balance": "151166",
            "futures_unrealized": "-200", "equity": "150966", "total_equity": "151266",
            "initial_margin": "20000", "maintenance_margin": "15000", "securities_collateral": "0",
            "excess_margin": "131266", "margin_call": "0", "additional_margin": "0", "withdrawable": "130966"},
 "deposits": [{"seq": "1", "direction": "in", "amount": "50000"}],
 "fills": [{"order_no": "sA1B2-01", "product": "範例期貨", "month": "2026/03", "strike": null, "right": null,
            "action": "平賣", "qty": 1, "price": "21000", "tax": "4", "fee": "20", "pnl": "1500",
            "closes": [{"date": "2026-03-02", "order_no": "sA0Z9-01", "qty": 1, "price": "20850"}]},
           {"order_no": "sA1B3-01", "product": "範例選", "month": "2026/03", "strike": "21000", "right": "Call",
            "action": "新買", "qty": 1, "price": "6", "tax": "0", "fee": "10", "pnl": "-300", "closes": []}],
 "fills_total": {"tax": "4", "fee": "30", "pnl": "1200"},
 "positions": [{"opened": "2026-03-05", "product": "範例期貨", "month": "2026/04", "strike": null,
                "right": null, "side": "買", "entry_price": "21020", "mark_price": "21000", "qty": 1,
                "initial_margin": "20000", "premium_value": "0", "unrealized": "-200"},
               {"opened": "2026-03-09", "product": "範例選", "month": "2026/03", "strike": "21000",
                "right": "Call", "side": "買", "entry_price": "6", "mark_price": "6", "qty": 1,
                "initial_margin": "0", "premium_value": "300", "unrealized": "0"}],
 "position_lines": {"option_value_net": "300"},
 "warnings": []}
```

`action` ∈ {新買, 新賣, 平買, 平賣}; `right` ∈ {Call, Put} or null; `direction` ∈ {in, out}; `fills_total` is null on a
`無交易` day; `position_lines` keeps every labelled line of the 留倉 block (`未沖銷選擇權浮動損益`, `未沖銷選擇權市場價值淨額`,
the combination-margin lines) as label → amount, for the record.

### 5.2 Monthly

```json
{"kind": "monthly",
 "source": {"drive_file_id": "…", "sha256": "…", "file_name": "…"},
 "month": "2026-03", "month_end": "2026-03-31",
 "lines": {"A": "90000", "B": "10000", "G": "-300", "H": "120", "K": "1500", "M": "101080",
           "N": "-200", "Q": "100880", "P": "0", "U": "20000", "V": "15000", "W": "80880",
           "securities_collateral": "0", "additional_margin": "0", "withdrawable": "80580",
           "long_premium_value": "300", "short_premium_value": "0", "total_equity": "101180"},
 "activity_dates": ["2026-03-02", "2026-03-09"],
 "open_positions": 1,
 "warnings": []}
```

`activity_dates` are the dates of the 存提 and 交易明細 lines; `open_positions` counts the 留倉明細 lines.

### 5.3 Parser grammar and self-checks (anything else fails the statement)

- Text source: the worker's `pdf.extract_lines` (pdfplumber 0.11.10, pinned in `requirements-worker.txt`), which
  keeps each word's x position; `pdf.unlock` picks the password candidate. Not the pypdfium2 text range the 國泰
  parser uses: the columns here can only be told apart by position.
- Numeric words (amounts, prices, quantities, strikes) belong to the column whose label's right edge is within 3 pt of
  their own; a numeric word with no such label, or two in one column, fails the statement.
- Text words are read in order, not by position. On a fill line the non-numeric words must be exactly: the date
  (`NNN/NN/NN`, ROC), the order number (`[A-Za-z0-9]{5}-[0-9]{2}`), `Taiwan`, `F` (the literal pair, nothing else),
  one product word, the contract month (`YYYY/MM`), for an option `Call` or `Put`, then the action (新買 / 新賣 / 平買
  / 平賣). On a position line: the date, one product word, the contract month, for an option `Call` or `Put`, then the
  side (買 / 賣). An option line has both a strike (numeric) and a right; a futures line has neither. Any other
  sequence fails. The plan's first task confirms these sequences against the 88 archived files before the parser is
  written.
- Every numeric word matches exactly one label (labels in one header row are more than 6 pt apart, so two matches
  cannot happen silently: two are a failure). Every header row and every label of the 權益帳 rows must be present
  exactly once with its label set exactly as in §2; the 權益帳 has exactly one value row under each label row (an
  empty column is a missing word in that row, never a missing row).
- Daily: exactly two pages; title `國內期貨日對帳單`; the four section markers in the order of §2, each exactly once, each with
  `新台幣(NTT)`; any other currency or a `國外` / `海外` marker fails. Every line of page 1 between the markers is one of
  the line kinds of §2 (header row, dated data line, `無存提` / `無交易` / `無留倉`, `平倉->` link, `商品小計`, `<商品> 小計`,
  `總計`, the labelled 留倉 lines, the 權益帳 label and value rows, `匯率：`, numbered notes). Exchange `Taiwan F` only.
  Page 2 is notices only: a section marker, a header row, a dated line or a `總計` / `小計` line on page 2 fails the
  statement; its other text is not read.
- Daily date: every dated line of 存提 and 交易紀錄 carries one date, the `statement_date`. A file-name date that
  differs adds `filename_date_differs` to `warnings` only.
- Daily self-checks: every identity of §2 (balance, equity, total equity, realised, premium, unrealised, deposits;
  with fills also the `總計` sums and 期交稅手續費 = 總計.期交稅 + 總計.手續費). The portfolio repeats the balance, equity
  and total-equity identities on the payload and answers HTTP 422 when one fails.
- Monthly: title `國內期貨月對帳單`; the five sections of §2; the 月底帳戶餘額明細 lines by left / right column; self-checks
  M = A + B + G − H + K, Q = M + N, 權益總值 = Q + 未沖銷買方權利金市值 − 未沖銷賣方權利金市值. The other monthly sections are
  read only for dates and the open-position count; the plan's first task pins their line grammar from a census of the
  27 archived files, the way §2 pins the daily one, under the same rules: an unknown line in any monthly section, a
missing or repeated section, or a numeric word without exactly one label fails the statement.

## 6. Portfolio rules

### 6.1 Tables

- `futures_statement_day`, primary key `statement_date`:
  - acceptance, written once: `accepted_sha256`, `accepted_at`, `drive_file_id`; one Numeric(18,4) column per
    `equity` field of §5.1; `deposits`, `fills`, `fills_total`, `positions`, `position_lines` (JSONB, as posted);
    `open_positions` (count);
  - latest attempt: `last_sha256`, `last_status` ∈ {`imported`, `duplicate`, `conflict`}, `last_detail`, `last_at`,
    `since`;
  - acknowledgement: `ack_sha256`, `ack_status`, `ack_at`.
  - `findings` (JSONB, §6.4), `findings_sha`, `findings_since`, `ack_findings_sha`.
- `futures_statement_month`, primary key `month` (first day of the month): the same acceptance / attempt / ack columns,
  `month_end`, one Numeric column per `lines` key, `activity_dates`, `open_positions`, and `findings` (JSONB, §6.4).
- `futures_statement_run`: the 國泰 run table's shape (`kind` latest / ok, `run_at`, `result`, `code`, `files_seen`,
  `posted`).

### 6.2 A statement

Serialisation: one futures lock, `pg_advisory_xact_lock(0x46555455 << 32)` ("FUTU", not the 國泰 tag `0x43415448`;
one key, not one per date). Every futures write takes it exclusively — a statement import, the findings it recomputes,
an ack — and every snapshot writer takes it in shared mode (`pg_advisory_xact_lock_shared`) before it reads the series:
`write_today_snapshot`, `refresh_snapshot_cash_range`, the backfill replay. Under the lock each path re-reads what it
uses (the statement rows, findings, the snapshot rows); nothing read before the lock is reused. The statements arrive
a few a day, so one key costs nothing and covers cross-date effects (snapshot ranges, successor findings).

Under the lock:

1. Not accepted → write the row, `imported`; recompute the snapshot rows of §6.5 in the same transaction.
2. Accepted, same sha → `duplicate`, nothing written.
3. Accepted, different sha → `conflict`; the accepted columns stay as they were. A corrected statement is an owner
   decision (there is none in the archive).

A daily statement for a date inside a month that already has a monthly row re-runs that month's findings (§6.4).
Dry runs take no lock and write nothing.

### 6.3 The equity series

`futures_equity(D)` = the `total_equity` of the latest accepted source dated on or before D, where a daily row is dated
by its `statement_date` and a monthly row by its `month_end`; on the same date the monthly row wins. Before the first
statement there is no value: 0 in totals, and the ledger-feed valuation answers `total`, `cash` and
`market_value` `"0"`, `priced_through` null, `complete` false. A source's value applies from its own date up to,
not including, the next source's date. With it come `source_date` and `positions_open` (the source's
`open_positions` > 0): a value with open positions and `source_date` < D is frozen, and every view that shows it says
「持倉中，數字停在 <source_date>」.

### 6.4 Findings

Findings are recomputed, under the lock of §6.2, for: an accepted daily row D — D itself, the next accepted daily
row after D (its `prev`), and D's month; an accepted monthly row M — M itself and the next month (its `carry`). So an
out-of-order import (February before January) still gets February's `carry` when January arrives.

A daily row is checked when it is accepted: `prev:<date>` when its `prev_balance` differs from the `balance` of the
latest accepted daily row before it (`<date>` = that row's date) — activity on a day without a statement, reported
with the next statement. The first statement has no predecessor. A daily statement accepted later for a date in
between re-checks the row after it.

A monthly row is checked after it is accepted, and again whenever a daily row of that month is accepted:

- `gap`: every `activity_dates` entry with no accepted daily row;
- `carry`: A differs from the previous accepted monthly row's M (that row is the month before; none → no check);
- `diff`: only when there is no gap — the lines B, G, H, K differ from the sums over the month's daily rows
  (`deposits_net`, `premium_net`, `fees_and_tax`, `futures_realized`), or M differs from the last daily row's
  `balance`; each differing letter is listed.

Findings (`findings` JSONB on both tables) never change a row. Shape: a list sorted by code, each
`{"code": "prev" | "gap" | "carry" | "diff", "dates": [...], "letters": [...]}` (`prev`: the predecessor's date;
`gap`: every activity date without a daily row; `diff`: the differing letters). Its identity is the sha256 of its
canonical JSON (`findings_sha`), so a gap with the same count but different dates is a new incident. `findings_since`
is set when `findings_sha` changes. A day or month with findings is pending (§8) until the owner acknowledges that
`findings_sha`.

### 6.5 Net worth

- `portfolio_snapshot` gets `futures_equity_twd` Numeric(20,4) NOT NULL default 0, and `total_assets_twd` becomes
  market value + cash + futures equity everywhere it is computed: `history._serialize_snapshot`,
  `PortfolioSummary.total_assets_twd`, the SPA dashboard composition (a fourth segment 期貨權益), its
  `carryHoldings` / `todayChange` helpers and `dashboard.ts` `totalAssetsValue`. There an absent or non-numeric
  `total_assets_twd` falls back to market value + cash + futures equity, and a present `0` is a valid total (today
  any 0 falls back, so market 100, cash 0, futures −100 would chart 100).
- Writers: `write_today_snapshot` and the backfill replay (`replay_snapshots_range`, every `PortfolioSnapshot(...)`
  site) set it from §6.3. In `refresh_snapshot_cash_range` and the replay's cash-only gating, a row with zero cash is
  deleted only when its `futures_equity_twd` is also 0, and a row it inserts takes `futures_equity_twd` from §6.3 (not
  the default 0).
- An import (§6.2 case 1) calls a new `refresh_snapshot_futures_range(db, start, end)`, which updates
  `futures_equity_twd` on the existing snapshot rows from the statement date (a monthly one: its month end) up to the
  next source date (or today). It never commits and never deletes or inserts rows: the import's transaction commits
  once, after the statement row and the refresh, so the advisory lock of §6.2 is held until then.
  (`refresh_snapshot_cash_range` commits by itself and must not be called from the import.)
- Replay: futures source dates (every accepted daily `statement_date` and monthly `month_end`) join the eligibility
  gates of `replay_snapshots_range` alongside `cash_activity_dates`, so a rebuild writes a row on them even with no
  stock or cash activity; the phantom-delete filter keeps any row whose `futures_equity_twd` is non-zero. The rollout
  runs one networth backfill from 2024-08-27 after the first commit run (§10), which creates the rows the archive
  needs; later imports update existing rows only, and the daily snapshot job writes a row every day.
- Ledger feed: `ledger_feed_service` today accepts only `BROKER = "cathay"` and echoes it. Valuation dispatches by
  broker (`cathay` → today's code, `sinopac_futures` → the series) and echoes the requested broker; `settlements` and
  `dividends` keep answering 404 `unknown_broker` for anything but `cathay`.
- `GET /api/portfolio/ledger-feed/valuation?broker=sinopac_futures&as_of=D`: `total` = `futures_equity(D)`,
  `cash` = the source's `balance`, `market_value` = `total` − `cash`, `priced_through` = `source_date`,
  `complete` = a value exists, `missing` = `[]`. Account 147 then shows it as 市值 like 117. The response shape does
  not change, so the accounting side needs no code; the frozen note of §6.3 is not shown on account 147, on the
  dashboard total or on the 淨值走勢 chart — only on the futures card and the import card (§9). Known limit, kept
  to stay inside the existing valuation contract.

## 7. Worker contract

- Config: `sinopac_futures_daily_folder` (default `投資/永豐期貨/日對帳單`), `sinopac_futures_monthly_folder` (default
  `投資/永豐期貨/月對帳單`), `sinopac_futures_timeout_s` (60); env names `STATEMENT_SINOPAC_FUTURES_*`.
- Flags as the 國泰 job: `--dry-run --file --since --until --retry --ack --unlatch --validate-history`; `--ack` takes a
  date or a month. Response, state file (keyed by Drive file id + sha256; every outcome is terminal for its sha),
  latch file `sinopac-futures-disabled.json` (decrypt failure with every candidate, any §5.3 failure, HTTP 422), lock,
  run report and exit codes follow the 國泰 job (`worker/cathay.py`); only a plain commit run reports a run.
- No cutover: nothing else writes futures data, so every archived statement imports. `--validate-history` parses
  both folders, then posts every payload in one request to `POST …/sinopac-futures-statement/validate`: the portfolio
  imports them in order (dailies by date, then monthlies) inside one transaction under the futures lock and rolls it
  back, answering per-file outcomes (repeats → `duplicate`, a second sha → `conflict`) and the findings every row would
  carry at the end. Nothing persists; the worker writes the answer as its report.

## 8. Notifications

- `GET …/sinopac-futures-statement/pending` → `{"items": [{"key", "kind", "status", "reason", "since", "last_at"}],
  "total", "truncated"}` (cap 50, sorted by key then status): `key` a date (`kind` `day`) or a month `YYYY-MM`
  (`kind` `month`); `status` ∈ {`conflict`, `findings`}. One item per incident: a row whose latest attempt is an
  unacknowledged `conflict` gives a `conflict` item (`since` = the first conflicting attempt); a row whose
  `findings_sha` is set and not acknowledged gives a `findings` item (`since` = `findings_since`), so a row can give
  both. `reason` for findings: the codes joined by `,` — `prev:<MM/DD>`, `gap:<n>`, `carry`, `diff:<letters>` — cut to
  40 characters. An accepted-sha re-post stays `duplicate` and writes nothing.
- `POST …/days/<key>/ack?status=conflict|findings` acknowledges that one incident: `conflict` stores
  (`last_sha256`, `conflict`) as the 國泰 ack does; `findings` stores the current `findings_sha`. 404 when that
  incident is not pending. `POST …/runs` and `GET …/runs/latest` as for 國泰.
- Notifier: a new module (not `notify/cathay.py`, whose validator accepts only held / mismatch / conflict) with a
  section 「永豐期貨對帳單」 built the same way (own client, the statuses and kinds above, strict validation, one 「讀不到」 item on
  any failure, run deadline `sinopac_futures_run_time` 06:45 + grace 15): `11/03 收到不同版本的日對帳單`,
  `11/05 前日餘額接不上 10/28（中間有一天沒有日對帳單）`, `2026-04 月初餘額接不上 3 月月底`,
  `2026-03 月對帳單有 1 天沒有日對帳單`, `2026-03 月對帳單對不上：B K`; weekly block 「永豐期貨對帳單待處理」.
- SPA notification center: source `sinopac_futures` (「永豐期貨對帳單」) registered the way `cathay` is, one task per
  item (id `sinopac_futures:<key>:<status>`), primary command 「知道了」 (ack with that status), 查看 → `/portfolio/import#sinopac-futures`.

## 9. Frontend

- `/portfolio/import`: a status card `#sinopac-futures` next to the 國泰 card — latest run, pending items, ack.
- `/portfolio` dashboard: the 期貨權益 composition segment (§6.5) and one card 「永豐期貨」: 權益總值, 可動用(出金)保證金,
  原始 / 維持保證金, 超額 / 追繳保證金, 本月損益 (權益總值 change since the last month end minus the month's 存提淨額) and
  本月期貨平倉損益, the source date, and the frozen note of §6.3. Read from a new
  `GET /api/portfolio/futures/summary`.
- No 風險指標: the statement does not print one and the broker's formula is not on it; the card shows the printed
  margins instead.

## 10. Rollout and history

1. The owner adds `投資/永豐期貨/日對帳單=$ID` and `投資/永豐期貨/月對帳單=$ID` to the worker's password file.
2. Portfolio (migration, endpoints, series, snapshot columns), then `--validate-history` (report to the owner), then
   the first commit run imports the archive, then one networth backfill from 2024-08-27 (`run_backfill`) writes the
   futures equity into the history, including rows on futures-only dates; the 淨值走勢 history changes from that date
   by the futures equity.
3. Timer, notifier release, SPA.
4. Set `valuation_source = portfolio:sinopac_futures` on account 147 (admin PATCH, with the owner's go).

The archive comes in with 9 pending findings (5 `prev` days, 4 months with a gap; no `carry`); the owner acknowledges
each once.

## 11. Tests (fictional fixtures only; never a real statement)

Worker: a fixture per daily shape built from positioned words (fills with a close link, `無交易`, deposit and
withdrawal, futures and option positions, the combination-margin lines, a value row with blank columns), and a monthly
fixture; each grammar failure (unknown line, unknown section, foreign currency, a value with no label, two dates);
each identity broken alone with the others intact (rejected, nothing posted, latched); response validation; state file;
`--validate-history`. Portfolio: migration; the three cases of §6.2 for daily and monthly; 422 on a broken identity;
the series (carry, monthly wins on a tie, frozen flag, before the first statement); monthly findings (gap, diff, a late
daily statement clearing a gap, `prev` on a gap and its re-check when the missing day arrives, ack keyed to
findings); snapshot refresh on import and in the replay (a zero-cash day with futures equity keeps its row; a row the cash
refresh inserts carries the futures equity; the futures refresh commits nothing — a failure after it rolls back the
statement row too); `carry` (February imported before January gets its `carry` when January arrives); findings identity (same gap count,
different dates → shown again); a row with both a conflict and findings → two items, each acked alone; concurrency
(two imports of different dates, a daily and a monthly, an import against `write_today_snapshot` and against the
replay: the series each writer reads is the committed one); the chart's valid zero total; ledger feed: valuation for `sinopac_futures`, settlements / dividends still 404 for it; ledger-feed
valuation for `sinopac_futures`; pending envelope and cap. Notifier and SPA: the 國泰 test set for the new source.

## 12. Review focus

1. A layout change must stop the job, not import a wrong number (§5.3 position mapping and identities).
2. A repeated or corrected statement never changes an accepted row (§6.2).
3. Net worth counts the futures equity exactly once, in every total of §6.5, and the replay agrees with the live
   writer.
4. On the futures card and the import card, a frozen value shows as frozen, never as current (§6.3).
5. Activity without a daily statement reaches the owner within a day of the next statement (daily or monthly), and a
   monthly difference within a day of the monthly statement (§6.4, §8).
