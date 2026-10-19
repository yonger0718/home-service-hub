# Portfolio ↔ ledger integration (證券交割對帳、市值帳戶、海外券商) — design

Status: rev 1 (2026-10-19), for the owner's review; Multica review after the owner's go. Post-cutover work (cutover 2026-10-24).

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
that trade date (sign: negative = money leaves the bank). `settlement_date = trade_date + 2 TW business days`
(the service's trading calendar; holidays included — the matcher still tolerates ±2 days). Rows with no
settlement cash (pure day-trade nets of zero) are omitted.

### 4.2 Account setting

`account.settlement_source` (new nullable column, migration): `"portfolio:cathay"` on account 67. Admin-only
`PATCH /accounts/{id}` field (the SPA shows it on the account settings page later; the first value is set by SQL
like `statement_live_from`). `account.valuation_source` (§5) is a sibling column.

### 4.3 Matcher: the `settlement` candidate

In `reconciliation_service.reconcile`, when the statement's account has `settlement_source`, the orchestrator
fetches the settlements for `period_start − 5 … period_end + 5` (one HTTP call, cached per reconcile; HTTP
failure → the lines fall through to the ordinary rules and a `settlement_source_unavailable` note on the
`ReconcileResult`, never a crash). `matching.match` gets `settlements: list[Settlement]`.

For a bank line whose `line_kind ∈ {withdrawal, deposit}` and whose `merchant_norm ∈ SETTLEMENT_LABELS =
{"交割股款", "申購股款"}`: a settlement whose `net_amount == line.flow` (exact, after quantisation) and whose
`settlement_date` is within 2 days of the line's date is a `settlement` representation, scored like an exact
match (0.80 + date term). Two settlements with the same amount in the window → `ambiguous` as usual. No near,
drift or foreign rule applies to settlement lines (they are never approximations).

A settlement line with no settlement candidate → `line_unmatched` with `hint: "settlement_pending"` (the SPA and
the triage list show it as "waiting for the broker import"); it is excluded from `backfill_entry` and
`create_system_entry` candidates (§4.4).

### 4.4 Booking: `book_settlement` (new policy + owner action)

A matched settlement representation is not a claim of an existing entry — there is none. The matcher emits a
`settlement` case (new `reconciliation_case.kind`, candidates = the settlement) and the policy action
`book_settlement` (enabled by default, no amount cap: the bank already moved the money) books, under the usual
locks and writer barrier:

- a transfer 67 → 117 for a negative net (buy day), 117 → 67 for a positive one (sell day), amount = |line.flow|,
  date = the line's posted date, description `證券交割 <trade_date>`, `source = statement`, both legs tagged
  `settlement_fingerprint` (new nullable column on `ledger_entry`, unique) = the portfolio fingerprint;
- the claim of the 67 leg on the line (`match_rule = book_settlement`);
- `created_entry_ids` = both legs; revert deletes both and releases the line.

Fees and taxes are inside the net: the ledger does not split them (117 is a position, not a P&L account — §5);
the portfolio keeps the per-trade detail. A second match for the same fingerprint is refused (`already_booked`)
and the line is claimed by the existing leg instead (idempotent re-reconcile).

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
- Portfolio API (new): `GET /valuation?broker=<broker>` → `{market_value, cash, total, currency, as_of}` from the
  broker's positions × latest `price_history` (× `fx_rates` for USD brokers, the existing Phase 2 hybrid FX rule)
  plus the broker's cash (`cash_transaction` running balance from `opening_balance`). For `cathay` `cash` is 0 by
  definition (the cash is bank 67). HomeHub caches the answer for 10 minutes; an unreachable portfolio shows the
  last cached value with its `as_of` and a `stale` flag, never an error page.
- Net worth: `include_in_total` accounts sum their read-through balance; a USD valuation is converted at
  HomeHub's own FX rate of the day (existing `fx_rate` table), shown with the rate used.

## 6. Foreign brokers (Phase 1b)

- New HomeHub accounts `IB` / `Firstrade` / `Schwab` (USD, `valuation_source = portfolio:ib|firstrade|cs`,
  `include_in_total` true). Internal trades, dividends and interest stay in the portfolio.
- Funding wires: a TWD bank `withdrawal` line whose label is a wire label (`匯出`, `外匯匯出`, …; settings table)
  matches a portfolio `cash_transaction` of type `deposit` on the broker account within 5 days where
  `USD × HomeHub fx rate of the wire date ≈ line.flow` within the near tolerance (bank fees differ) →
  `wire` case → `book_wire` books a transfer bank → broker account with the FX pair (`original_amount` USD,
  `fx_rate` implied by the bank's TWD), fingerprint `cash:<id>`. Withdrawals back to the bank are the mirror.
  Volume: two or three per year per broker — the owner's `match` from the case is an acceptable fallback.
- Which bank accounts send wires is a setting (`wire_accounts`, default: every TWD bank account with live
  statements); the owner names them in the settings PUT.

## 7. IB statements by Flex Query (Phase 2)

- The owner creates an IB Flex Query (Activity: trades, cash transactions, dividends, interest, transfers; CSV;
  last 30 days, daily) and a Flex Web Service token (read-only).
- A worker timer (`homehub-portfolio-flex.timer`, daily, as the worker user) calls the Flex Web Service
  (SendRequest → GetStatement), stores the CSV under the worker's state dir, and POSTs it to the portfolio's
  `/imports/csv` with the IB broker (idempotent by `import_fingerprint`). The token lives in the worker's env file
  (0600), never in the repo, logs or API bodies. Failures latch like the parser gate (`flex-disabled.json`) and
  surface in the run summary; the monthly PDFs on Drive stay as the owner's archive, unparsed.
- Firstrade / Schwab: no automation this round (manual CSV as today).

## 8. Settings and schema (summary)

- `account.settlement_source`, `account.valuation_source` (nullable text; one migration), `ledger_entry.
  settlement_fingerprint` (nullable text, unique partial index).
- `settings.reconciliation`: `settlement_labels` (default `["交割股款", "申購股款"]`), `dividend_labels`,
  `wire_labels`, `wire_accounts`, policy actions `book_settlement` (enabled true, no cap), `book_dividend`
  (enabled true, no cap), `book_wire` (enabled false — owner confirms the first few).
- `reconciliation_case.kind` += `settlement`, `dividend`, `wire`; case hints `settlement_pending`.
- Portfolio: `GET /settlements`, `GET /valuation`, HomeHub call-back in the post-import orchestrator; a trading
  calendar for TW settlement dates (the service already has market calendars for prices; reuse).

## 9. Failure modes (fail closed, owner sees)

- Portfolio unreachable during reconcile → settlement lines stay `settlement_pending`; summary field
  `settlement_source_unavailable: true`.
- Net amount mismatch (a partial import, a missing fee row) → no candidate; the line stays pending; the owner
  sees the nearest settlement in the case detail (`nearest: {date, delta}`) to spot the missing import.
- Two trade dates settling on one bank day (holiday compression) → the bank line equals the SUM of two nets: the
  matcher also tries the sum of settlements whose `settlement_date` equals the line date (at most 3) → one
  booking with both fingerprints. Beyond that → pending.
- Valuation stale → shown stale, never guessed.
- Revert of a `book_settlement` after a later reconcile: the line reopens as `settlement_pending`; the next
  reconcile books again unless the owner dismisses the case (`owner_deleted` rule as for backfills).

## 10. Tests (per phase, named so the plan can pin them)

- Portfolio unit: `settlements` nets per trade date, settlement-date calendar (weekend, holiday), empty days
  omitted; `valuation` per broker (TWD cathay, USD with fx), cash running balance.
- HomeHub unit (matching): settlement representation exact / ±2 days / ambiguous twins / sum-of-two /
  no near-or-foreign on settlement lines / `settlement_pending` hint / backfill never offered.
- HomeHub integration: `book_settlement` books two legs with fingerprint, claims the line, idempotent on
  re-reconcile, revert deletes both legs and reopens; `book_dividend`; `book_wire` with the FX pair;
  read-through balance and net worth with a stale valuation; `POST /reconciliation/dirty` marks statements.
- Worker: Flex fetch → import call with a fixture CSV; latch on failure; token never logged.

## 11. Rollout

1. Phase 1 (國泰): migration + settings; set `settlement_source` on 67 and `valuation_source` on 117; reconcile
   67's live statements → the 12 pending settlement proposals are superseded by `settlement` cases → bookings;
   open cases on 67 drop to the non-settlement lines. Historical statements of 67 stay as they are.
2. Phase 1b: create IB / FT / CS accounts, set `valuation_source`, `wire_accounts`; reconcile the wire months.
3. Phase 2: Flex timer on the worker; first run against the last 30 days; compare with the owner's last manual
   import (dry-run report).

## 12. Out of scope

永豐證券/期貨 (statements stay on Drive unparsed); realised / unrealised P&L in the ledger; per-trade fee and tax
entries; parsing IB / FT / CS PDFs; Firstrade and Schwab automation; multi-currency settlement (cathay settles
in TWD only).
