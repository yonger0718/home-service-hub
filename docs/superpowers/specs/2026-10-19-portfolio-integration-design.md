# Portfolio ↔ ledger integration (證券交割對帳、市值帳戶、海外券商) — design

Status: rev 3 (2026-10-20) §7 amended by the Phase 2 plan: XML instead of CSV; the token travels only in the Flex `t` query parameter (IB offers no other transport); section list; legacy-duplicate pass; `--unlatch` / `--dry-run` / `--query-id` / `--from-file`.
Rev 2 (2026-10-19) after the AGENT-175 review of rev 1 `31ab6f4`: booking owner + constituent reservations
(Must 1), global assignment before any booking (Must 2), foreign cash from `broker_cash_flows` and the trade-inclusive
balance (Must 3); the Shoulds that are spec-level are folded in (settlement-date window + calendar, valuation contract,
pair action, Flex adapter, token tests), the rest go to the plan. Rev 1 was §7-amended the same day (IB Flex only,
Firstrade/Schwab manual). Post-cutover work (cutover 2026-10-24).

## 1. Goal

HomeHub's ledger stops pretending the owner records securities by hand. The bank statement feed and the
stock-portfolio-service already hold every fact; the ledger must (a) explain the 國泰 bank account's settlement
lines from the portfolio's trades, (b) show securities accounts at market value without daily hand-written
entries, and (c) do both without the two data sources ever having to arrive in order.

Owner decisions (2026-10-19): 國泰高雄 (HomeHub account 117) is a pure position — its balance is the portfolio's
market value; the investment cash is the 國泰 bank account (67) itself. IB / Firstrade / Schwab are in scope as
market-value accounts plus their funding wires; their internal trades never touch the ledger. 永豐證券/期貨 are out
of scope. IB statements arrive by Flex Query, not by parsing the monthly PDFs.

## 2. Facts the design rests on (verified 2026-10-19)

- Bank side: account 67's statements print ONE line per settlement day (`交割股款`, withdrawal or deposit; also
  `申購股款`), posted on the settlement date; `txn_date == posted_date` on every such line (146/146).
  7–9 月: 36 of 50 lines are settlements; 15 of 16 September lines exceed the backfill cap (5000), so the policy
  only proposes on them today (12 proposals pending, deliberately left).
- Portfolio side: `cash_transaction(account_id, txn_date, type ∈ {buy_settle, sell_settle, fee, tax,
  dividend_cash, deposit, withdrawal, interest_in}, amount, currency, related_transaction_id,
  related_dividend_id, source, import_fingerprint)`; `txn_date` is the TRADE date (2484 of 2489 rows equal
  `transactions.trade_date`), not the settlement date. `broker_account(id, broker ∈ {cathay, firstrade, ib, cs},
  currency, opening_balance)`. `dividends(payment_date, amount, fee, tax, receipt_account_id, …)`.
  `portfolio_snapshot` is portfolio-wide (no per-broker split). Importers exist for cathay / firstrade / ib /
  schwab; the IB importer reads the Activity Statement CSV, not PDFs.
- Drive: `財務對帳單/投資/IBKR` holds 22 monthly PDFs (2024-12 →), `永豐證券` 20, `永豐期貨` 26, `國泰證券` 0.
  The statement worker polls only `mail` and `manual` roots.
- HomeHub: transfers are two-leg entries sharing `transfer_group_id` (`POST /transfers`); the reconciliation
  policy books entries through `_Creation` actions with `created_entry_ids`, revertable; `reconciliation_case`
  kinds and the matcher's representations are the extension points; statements are re-reconciled by the daily
  sweep and on demand (`POST /accounts/{a}/statements/{s}/reconcile`).

## 3. Principle: compare, never synchronise

Both sources are imported by hand at irregular times (broker CSV → portfolio; statement PDFs → Drive → worker).
Nothing in this design depends on order or freshness:

- A bank settlement line without portfolio data stays an open `line_unmatched` case (the policy never backfills
  settlement lines — §4.4). The next reconcile — daily sweep, or the one the portfolio import triggers — pairs it.
- Portfolio data without a bank line is simply data; nothing is written to the ledger until the line exists.
- Market value is read through at display time (§5): a stale portfolio shows "as of <date>", never a wrong entry.
- Every ledger write carries a fingerprint built from portfolio ids; re-running a match is a no-op.

## 4. Settlement matching (國泰, Phase 1)

### 4.1 Portfolio read API (new, stock-portfolio-service)

`GET /settlements?broker=cathay&from=YYYY-MM-DD&to=YYYY-MM-DD` (read scope) →
```
[{"settlement_date": "2026-09-12", "trade_date": "2026-09-10", "net_amount": "-123456.0000",
  "currency": "TWD", "components": {"buy_settle": 2, "sell_settle": 0, "fee": 2, "tax": 0},
  "cash_transaction_ids": [..], "fingerprint": "cathay:2026-09-10:<sha256 of sorted ids>"}]
```
One row per trade date: `net_amount = Σ amount` over the broker's `buy_settle + sell_settle + fee + tax` rows of
that trade date (sign: negative = money leaves the bank). `settlement_date = trade_date + 2 TW trading days` on a
**TW trading calendar the portfolio service does not have yet** (its scheduler is Mon–Fri only,
`app/services/scheduler.py:59`): a new `tw_trading_calendar` table (date, is_trading_day) loaded from TWSE's
published holiday schedule (yearly, by an owner-run command, like the price history), with a `calendar_through`
date in the response. `from`/`to` filter the DERIVED settlement date, so a long closure never hides a settlement;
a trade whose settlement date lies past `calendar_through` is returned with `settlement_date: null` and the
matcher treats it as unknown (pending, never guessed). Rows with no settlement cash (pure day-trade nets of
zero) are omitted. Settlement dates are strictly order-preserving on a valid calendar, so two trade dates never
settle on one day; the "sum of two" rule of §9 exists only for a calendar fault and is gated behind an explicit
fixture.

### 4.2 Account setting

`account.settlement_source` (new nullable column, migration): `"portfolio:cathay"` on account 67. Admin-only
`PATCH /accounts/{id}` field (the SPA shows it on the account settings page later; the first value is set by SQL
like `statement_live_from`). `account.valuation_source` (§5) is a sibling column.

### 4.3 Matcher: the `settlement` candidate

In `reconciliation_service.reconcile`, when the statement's account has `settlement_source`, the orchestrator
fetches the settlements for `period_start − 5 … period_end + 5` (one HTTP call, cached per reconcile; HTTP
failure → the lines fall through to the ordinary rules and a `settlement_source_unavailable` note on the
`ReconcileResult`, never a crash). `matching.match` gets `settlements: list[Settlement]`.

A settlement line is a bank line whose `line_kind ∈ {withdrawal, deposit}` and whose `merchant_norm ∈
SETTLEMENT_LABELS = {"交割股款", "申購股款"}`. A settlement is a candidate for it when `net_amount == line.flow`
(exact, after quantisation), `settlement_date` is within 2 days of the line's date, and the settlement is not
reserved by an existing booking (§4.4). No near, drift or foreign rule applies to settlement lines.

**Assignment is global, not per line.** Before any case is written, the orchestrator builds the bipartite
candidate graph between EVERY settlement line of the account whose date falls in the fetched window — the
statement under reconcile and its neighbours' lines alike (`statement_line` rows of the account within
`window ± 2 days`, retired statements excluded) — and every unreserved settlement of the window. A pair is
booked only when it is the unique feasible assignment on both sides: the line has exactly one candidate and that
settlement has exactly one candidate line. Everything else is left open:
- a line with two candidates, or a settlement wanted by two lines (same amount within the window, e.g. two
  equal buys two days apart) → `ambiguous` case on each such line, candidates = the settlements (with their
  dates), hint `settlement_ambiguous`; the owner picks from the case (`book_settlement` with the settlement
  fingerprint as the parameter);
- a line with no candidate → `line_unmatched` with `hint: "settlement_pending"` ("waiting for the broker
  import"), excluded from `backfill_entry` and `create_system_entry` candidates (§4.4);
- a settlement with no line → nothing (data waiting for a statement).
A line claimed by an earlier booking is skipped. A uniqueness failure at booking time (§4.4) never promotes the
other line: both stay open for the next reconcile.

### 4.4 Booking: `book_settlement` (new policy + owner action)

A uniquely assigned pair is not a claim of an existing entry — there is none. The matcher emits a `settlement`
case (new `reconciliation_case.kind`, candidates = the one settlement, context = its fingerprint, date, source
facts) and the policy action `book_settlement` (enabled by default, no amount cap: the bank already moved the
money) books, under the statement lock and the writer barrier:

1. **One booking owner.** A row in `settlement_booking` (new table: id, account_id, event_id, source
   `portfolio:cathay`, fingerprint UNIQUE, net_amount, settlement_date, action_id, created_at) and one
   `settlement_booking_item` row per constituent (`booking_id`, `source_id` = the portfolio
   `cash_transaction.id`, `amount` as booked; `(source, source_id)` UNIQUE). The unique constraints are the
   reservations: booking `{A, B}` and later `{A}` cannot both exist; a second booking for the same fingerprint
   is refused `already_booked`.
2. **Two legs referencing the owner.** A transfer 67 → 117 for a negative net (buy day), 117 → 67 for a positive
   one (sell day), amount = |line.flow|, date = the line's posted date, description `證券交割 <trade_date>`,
   `source = statement`; both legs carry `ledger_entry.settlement_booking_id` (nullable FK, NOT unique — the
   fingerprint uniqueness lives on the booking row). `ux_statement_coverage_active_entry`
   (`app/models/statements.py:260`) is untouched: only the 67 leg is claimed.
3. **Claim.** The 67 leg claims the line (`match_rule = book_settlement`). "Already booked" is honoured only for
   the SAME bank event (the booking's `event_id`): a booking exists for this event → the line is re-claimed by
   its leg (idempotent re-reconcile); a booking exists for another event with the same fingerprint → refuse
   `already_booked`, the line stays open (the owner sees the booked event in the case detail).
4. **Source facts, not just ids.** The booking stores the net amount and the constituent amounts it saw; a later
   reconcile recomputes the settlement from the portfolio and, if the facts differ under unchanged ids (an
   edited import), opens a `settlement` case with `hint: "source_drift"` on the booked line and never auto-rebooks
   (fail closed; the owner reverts and re-reconciles).
5. **Action shape.** A new base `_PairCreation` (beside `_Creation`, which books ONE row and inverts by one
   `created_entry_id`, `ledger_actions.py:116–128`): `created_entry_ids` = both legs, `inverse = {"booking_id",
   "leg_ids": [..]}`, snapshots of both legs; revert deletes both legs, the booking and its items in one
   transaction (reservations released atomically), releases the line; guards: both legs must still equal the
   snapshots (no later edit, child, refund or settlement on either), else `NotRevertable`.

Fees and taxes are inside the net: the ledger does not split them (117 is a position, not a P&L account — §5);
the portfolio keeps the per-trade detail.

### 4.5 Trigger from the portfolio import

`post_import_orchestrator` (portfolio) ends by calling HomeHub `POST /reconciliation/dirty` (ingest scope, new;
body `{"accounts": [67]}`) which marks the account's live statements for the next sweep. Failure to call is
harmless: the daily sweep re-reconciles anyway.

### 4.6 Dividends (國泰 stocks)

A bank `deposit` line whose `merchant_norm` is a dividend label (`股利`, `股息`, `現金股利`; table in settings,
like `bank_only_patterns`) matches a portfolio `dividends` row with `receipt_account_id = 1 (cathay)`,
`payment_date` within 3 days and `amount − fee − tax == line.flow` → `dividend` case → `book_dividend` books an
income entry on 67 (category `股利` / configurable), fingerprint = `dividend:<id>`. Same idempotency and revert
shape as §4.4.

## 5. Market-value accounts (Phase 1 for 117; Phase 1b for IB/FT/CS)

- `account.valuation_source = "portfolio:<broker>"`. Such an account's **balance is read through**: the ledger
  keeps only the transfer legs (cost flows) and the API's balance/net-worth reads replace the ledger balance
  with the portfolio's valuation, returning `{balance, valuation: {value, as_of, source}}`. No valuation entry
  is ever written; unrealised P&L is the portfolio's business and is not a ledger figure.
- Portfolio API (new): `GET /valuation?broker=<broker>&as_of=YYYY-MM-DD` → `{market_value, cash, total,
  currency, as_of, priced_through}` in the BROKER's currency (USD stays USD): positions as of `as_of` × the last
  price on or before `as_of` (`price_history`; `priced_through` = that date) plus the broker's cash as of
  `as_of` from the existing trade-inclusive balance (`cash_flow_service.get_broker_balance` /
  `list_balances`: explicit `broker_cash_flows` ± trade deltas — never recomputed here, never trades added
  twice). For `cathay` the cash term is 0 by definition (the cash is bank 67; its legacy `cash_transaction`
  ledger is not consulted for valuation).
- HomeHub contract: a valuation account's balance in every read (account list, account detail,
  `ledger_service.account_balance:109`, net worth) is `valuation.total` for the requested `as_of` (today by
  default; a historical query asks the portfolio for that date, never today's value). Period/running ledger
  figures (`ledger_service` running balances, cost flows) stay ledger figures and are labelled "cost basis" in
  the API (`balance_kind: "valuation" | "ledger"`), so a client never mistakes one for the other. HomeHub caches
  per (broker, as_of) for 10 minutes; on a miss with the portfolio unreachable the read returns
  `valuation: null, stale: true` and the UI shows "市值暫不可得" — never zero and never the ledger cost in the
  valuation slot.
- Net worth: `include_in_total` accounts sum their read-through totals; a USD total is converted ONCE, in
  HomeHub, at HomeHub's own FX rate of `as_of` (existing `fx_rate` table), shown with the rate used.

## 6. Foreign brokers (Phase 1b)

- New HomeHub accounts `IB` / `Firstrade` / `Schwab` (USD, `valuation_source = portfolio:ib|firstrade|cs`,
  `include_in_total` true). Internal trades, dividends and interest stay in the portfolio.
- Source adapter per broker (Must 3 of the rev 1 review): foreign brokers' cash lives in `broker_cash_flows`
  (`routers/cash_flows.py:68`; the IB importer writes deposits there, `broker_ib_service.py:109`), the legacy
  `cash_transaction` ledger is `cathay` only. The portfolio's `GET /funding?broker=&from&to` returns the
  broker's `deposit` / `withdrawal` flows from the right store with namespaced source ids: `bcf:<id>` for
  `broker_cash_flows`, `ct:<id>` for `cash_transaction`; fingerprints and `settlement_booking_item.source_id`
  carry the namespace, so the two stores never collide.
- Funding wires: a TWD bank `withdrawal` line whose label is a wire label (`匯出`, `外匯匯出`, …; settings table)
  matches a funding `deposit` on the broker within 5 days where `USD × HomeHub fx rate of the wire date ≈
  line.flow` within the near tolerance (bank fees differ) → `wire` case → `book_wire` books a transfer bank →
  broker account with the FX pair (`original_amount` USD, `fx_rate` implied by the bank's TWD) through the same
  `_PairCreation` / booking-owner shape as §4.4 (fingerprint `wire:bcf:<id>`). Withdrawals back to the bank are
  the mirror. Assignment is global as in §4.3. Volume: two or three per year per broker — the owner's
  `book_wire` from the case is an acceptable fallback.
- Which bank accounts send wires is a setting (`wire_accounts`, default: every TWD bank account with live
  statements); the owner names them in the settings PUT.

## 7. IB statements by Flex Query (Phase 2)

- The owner creates an IB Flex Query (XML: Trades at execution level, Cash Transactions, Transfers, Corporate Actions, Open
  Positions; last 30 calendar days; yyyy-MM-dd; currency rates off; daily) and a Flex Web Service token (read-only).
- A worker timer (`homehub-portfolio-flex.timer`, daily, as the worker user) calls the Flex Web Service
  (SendRequest → GetStatement), stores the XML under the worker's state dir and POSTs it to a NEW portfolio
  endpoint `POST /api/portfolio/imports/ib-flex` (multipart; the existing `/imports/csv` auto-detects and has no broker
  argument, `routers/imports.py:241`). Flex XML is not the Transfer History CSV the IB importer reads today:
  an adapter `broker_ib_flex_service.parse` maps the Flex sections (Trades, CashTransactions; Transfers and CorporateActions are reported, OpenPositions ignored) onto the same `ParsedRow` / cash-flow rows, using the existing fingerprint functions with the Flex tradeID / transactionID in the note, so two identical same-day fills stay two rows; the market comes from the Flex listingExchange (LSE for LSE/LSEETF/LSEIOB); cash amounts keep IB's sign. A Flex row never shares a fingerprint with a row from the manual Transfer-History CSV imports; the one-off backfill over that era runs with legacy_until=<date>, which pairs rows one-to-one by business key (symbol, type, quantity, price, date, currency, fee, tax; cash: date, type, signed amount, currency) with the rows dated up to that day, drops the pairs, and reports any key with unequal counts as "ambiguous legacy match" for the owner to resolve; a paired row adopts the Flex fingerprint, so no later import re-creates it; the daily run never pairs. Provenance: an existing row is manual-era only if its fingerprint is NULL or equals the CSV-style fingerprint recomputed from its stored fields (market ∈ {stored, US, LSE}; note = name / note); any other fingerprint is Flex-origin and never pairs. Once Flex is live the IB Transfer-History CSV is never uploaded again: a CSV row and its Flex twin carry different fingerprints, so a re-upload would duplicate.
  The plan pins a fixture from a real Flex export with the amounts replaced.
- Token: `IB_FLEX_TOKEN` in the worker's env file (0600), never in the repo, logs, API bodies, latch files or
  run summaries. Tests: the token is absent from every log line, exception text, every request other than the two Flex
  calls (the token is the t query parameter of SendRequest and GetStatement and nothing else: never a header, never the portfolio request); the CLI is the output boundary: codes and counts on stdout, class names on stderr, no traceback, and a redacting filter on the root logger while the token is in use, the latch file and the summary — on success AND on HTTP 4xx/5xx/timeout. Non-transient failures (an IB status code outside the transient table, an unparseable response, a portfolio 4xx other than 408/429) latch like the parser gate (`flex-disabled.json`); transient ones (IB codes 1003–1009, 1018, 1019, 1021; HTTP 5xx / 408 / 429; timeouts) exit 1 without latching and the unit shows failed. Recovery: `python -m worker flex --unlatch` (a plain daily run that ends with no codes — a `stale_fx` day keeps the latch; `--dry-run`, `--query-id` and `--from-file` never clear it); a one-off backfill query runs with `--query-id` and `--legacy-until`; a saved XML re-posts with `--from-file`.
  the monthly PDFs on Drive stay as the
  owner's archive, unparsed.
- Firstrade / Schwab: manual CSV as today, by owner decision (2026-10-19 research): Firstrade has no API and no
  OFX — only the Tax Center CSV export (its `Action` column lumps reinvestments, transfers and corporate actions
  as `Other`); Schwab has an official Trader API (Individual tier, OAuth, transactions endpoint) whose eligibility
  for a Schwab One International account is unverified — parked, not attempted this round. Both brokers see a
  handful of cash events a year, so the manual cadence (quarterly) costs less than a parser.

## 8. Settings and schema (summary)

- `account.settlement_source`, `account.valuation_source` (nullable text), `settlement_booking` +
  `settlement_booking_item` (§4.4; uniques on `fingerprint` and `(source, source_id)`),
  `ledger_entry.settlement_booking_id` (nullable FK, indexed, not unique) — one migration. Portfolio:
  `tw_trading_calendar` (§4.1).
- `settings.reconciliation`: `settlement_labels` (default `["交割股款", "申購股款"]`), `dividend_labels`,
  `wire_labels`, `wire_accounts`, policy actions `book_settlement` (enabled true, no cap), `book_dividend`
  (enabled true, no cap), `book_wire` (enabled false — owner confirms the first few).
- `reconciliation_case.kind` += `settlement`, `dividend`, `wire` (a positive external candidate awaiting
  creation); missing data stays `line_unmatched` with hints `settlement_pending` / `wire_pending`; competing
  assignments are `ambiguous` with hint `settlement_ambiguous`; drift is `settlement` + `source_drift`.
- Portfolio: `GET /settlements`, `GET /funding`, `GET /valuation`, `POST /imports/ib-flex`, the HomeHub
  call-back in the post-import orchestrator, the `tw_trading_calendar` table and its loader.

## 9. Failure modes (fail closed, owner sees)

- Portfolio unreachable during reconcile → settlement lines stay `settlement_pending`; summary field
  `settlement_source_unavailable: true`.
- Net amount mismatch (a partial import, a missing fee row) → no candidate; the line stays pending; the owner
  sees the nearest settlement in the case detail (`nearest: {date, delta}`) to spot the missing import.
- Two trade dates settling on one bank day cannot happen on a valid calendar (§4.1); it can when the calendar
  is wrong (a missed exchange holiday). The sum rule is therefore gated: only when the two settlements'
  `settlement_date`s are within 2 days of each other AND the line equals their sum AND neither settles anything
  else — then one booking with two constituents (both reserved). The plan carries a fixture of a real calendar
  fault (a TWSE typhoon closure) rather than an assumed one. Any overlap between a single-settlement candidate and
  a sum candidate for the same line or the same constituent → ambiguous, never auto-booked.
- Edited source under unchanged ids (a corrected broker import) → `source_drift` case on the booked line (§4.4
  item 4); nothing is rebooked automatically.
- Concurrent reconciles of two statements of the same account racing for one settlement → the second insert
  into `settlement_booking_item` fails the unique constraint; that booking rolls back and its line stays open.
- Valuation stale → shown stale, never guessed.
- Revert of a `book_settlement` after a later reconcile: the line reopens as `settlement_pending`; the next
  reconcile books again unless the owner dismisses the case (`owner_deleted` rule as for backfills).

## 10. Tests (per phase, named so the plan can pin them)

- Portfolio unit: `settlements` nets per trade date, settlement-date calendar (weekend, holiday), empty days
  omitted; `valuation` per broker (TWD cathay, USD with fx), cash running balance.
- HomeHub unit (matching): settlement representation exact / ±2 days / ambiguous twins / sum-of-two /
  no near-or-foreign on settlement lines / `settlement_pending` hint / backfill never offered.
- HomeHub unit (assignment): global uniqueness across neighbouring statements; one settlement wanted by two
  lines → both ambiguous; sum candidate overlapping a single candidate → ambiguous; reserved settlement excluded.
- HomeHub integration: `book_settlement` books two legs + booking owner + items, claims the line, idempotent on
  re-reconcile (same event), `already_booked` for another event, constituent overlap refused by the unique
  constraint, concurrent booking race (one wins, the other's line stays open), `source_drift` on an edited
  import, revert deletes both legs + booking atomically and reopens, revert-after-reconcile guard; `book_dividend`;
  `book_wire` with the FX pair from `bcf:` sources; read-through balance for today and a historical `as_of`,
  cold cache with the portfolio down (`valuation: null, stale: true`), net worth conversion once;
  `POST /reconciliation/dirty` marks statements.
- Worker: Flex fetch → `/imports/ib-flex` with a fixture XML; latch on failure; the token absent from logs,
  exception text, request URLs, latch file and summary on success, 4xx, 5xx and timeout.
- Portfolio: `tw_trading_calendar` loader + settlement date derivation (weekend, holiday, past
  `calendar_through` → null); `GET /funding` reads `broker_cash_flows` for foreign brokers and `cash_transaction`
  for cathay with namespaced ids; Flex adapter dedupes re-imports by fingerprint (Flex id in the note) and, with legacy_until, pairs the manual-era rows one-to-one by business key, reporting ambiguity.

## 11. Rollout

1. Phase 1 (國泰): migration + settings; set `settlement_source` on 67 and `valuation_source` on 117; reconcile
   67's live statements → the 12 pending settlement proposals are superseded by `settlement` cases → bookings;
   open cases on 67 drop to the non-settlement lines. Historical statements of 67 stay as they are.
2. Phase 1b: create IB / FT / CS accounts, set `valuation_source`, `wire_accounts`; reconcile the wire months.
3. Phase 2: IB Flex timer on the worker; first run against the last 30 days; compare with the owner's last manual
   import (dry-run report). Firstrade / Schwab stay manual.

## 12. Out of scope

永豐證券/期貨 (statements stay on Drive unparsed); realised / unrealised P&L in the ledger; per-trade fee and tax
entries; parsing IB / FT / CS PDFs; Firstrade and Schwab automation; multi-currency settlement (cathay settles
in TWD only).
