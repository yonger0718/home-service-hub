# Portfolio ↔ ledger integration (證券交割對帳、市值帳戶、海外券商) — design

Status: rev 4.3 (2026-10-30) owner rulings 2026-10-30 (Q1–Q7, `.superpowers/r7-refresh/owner-rulings.md`) and the
drift-audit errata (`.superpowers/r7-refresh/drift-audit.md`): settlement date from the 國泰 daily statement, T+2 bank
business days as the fallback, no trading calendar (§4.1); 國泰 rows only (TW_CATHAY + NULL broker), margin days fail
closed (§4.1); owner twins claim first (§4.3); the booking owner mirrors `statement_auto_pay`, `book_settlement` is
zero-charged and exempt from every policy cap (§4.4); re-reconcile = the worker after each 國泰 post + the daily sweep
re-check + the monthly statement arrival, no portfolio→HomeHub call (§3, §4.5); dividend formula `amount == flow`,
confirmed receipts ± 3 days (§4.6); **owner decision 2026-10-30 (Q7): daily settlement stand-ins from the 國泰 daily
statement (§4.7), superseding §3's "nothing is written until the line exists" for 國泰 settlements**; controller
ruling 2026-10-30 (owner to confirm): a dismissed settlement case or a reverted / owner-deleted bank booking is final
for the stand-in job (§4.7 gates, §9); rev 4.2 (AGENT-360): two-axis feed query with echoed bounds (§4.1, §4.3),
housekeeping for both origins + the lock protocol + replacement re-gated (§4.7); rev 4.3 (re-verify 2): the trade axis
returns every row of a trade date, the stand-in lifecycle takes no account row lock (§4.1, §4.7 Locks); §2, §8–§11 follow. §5: Phase 1 scope sentence added (read-through in the account
read models only); §6–§7 unchanged.
Rev 3 (2026-10-20) §7 amended by the Phase 2 plan: XML instead of CSV; the token travels only in the Flex `t` query parameter (IB offers no other transport); section list; legacy-duplicate pass; `--unlatch` / `--dry-run` / `--query-id` / `--from-file`.
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

## 2. Facts the design rests on (verified 2026-10-19; the counts in the first bullet are historical — Task 11's dry run recomputes them)

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
- Verified 2026-10-30 (drift audit, main `7b3969f4`; supersedes the lines above where they differ): the
  `cash_transaction` withdrawal type is `withdraw`. The legs of EVERY TWD trade (TW_CATHAY, TW_MANUAL, TW_SINOPAC,
  NULL broker) land on the one 國泰 TWD cash account; TW legs book `settle_gross` = ⌊qty × price⌋, a buy paid with
  小樹點數 has no legs. Since 2026-10-16 the worker posts every 國泰證券日對帳單 to the portfolio; its payload carries
  `trade_date`, `settlement_date` (交割日期) and `net_amount` (應收付金額, Σ line net), and the cash the bank moves is
  淨收付金額 = `net_amount` − Σ net of the `(小樹點數)` lines; none of the three is persisted today. A statement with a
  資買 / 資賣 / 券買 / 券賣 line is recorded `held` (`unsupported_category`) and its day stays with the CSV, whose rows do
  not record the margin side. `dividends.amount` is already net of 匯費 / 補充保費; receipts are explicit
  (`receipt_status = confirmed`, `receipt_date`, `receipt_account_id`). The portfolio has no auth (issue #117) and
  every route is under `/api/portfolio/`. HomeHub ships an audited Taiwan bank business-day table
  (`business_days`, with the `auto_pay.extra_holidays` / `extra_workdays` overrides) and the auto-pay stand-in
  machinery (`statement_auto_pay`, `stand_ins.auto_pay_legs`, `retire_twins_for_statement`, auto-pay orphans). The
  daily sweep reconciles only dirty / `needs_recheck` statements.

## 3. Principle: compare, never synchronise

Both sources are imported by hand at irregular times (broker CSV → portfolio; statement PDFs → Drive → worker).
Nothing in this design depends on order or freshness:

- A bank settlement line without portfolio data stays an open `line_unmatched` case (the policy never backfills
  settlement lines — §4.4). The next reconcile — the daily sweep's re-check of settlement-source statements with
  open settlement cases, or the one a 國泰 post triggers (§4.5) — pairs it.
- Portfolio data without a bank line is simply data; nothing is written to the ledger until the line exists —
  except, by owner decision 2026-10-30, a day the 國泰 daily statement attests: it is booked as a stand-in on its
  settlement date (§4.7), which the bank line later claims or contradicts. Days without a daily statement still wait.
- Market value is read through at display time (§5): a stale portfolio shows "as of <date>", never a wrong entry.
- Every ledger write carries a fingerprint built from portfolio ids; re-running a match is a no-op.

## 4. Settlement matching (國泰, Phase 1)

### 4.1 Portfolio read API (new, stock-portfolio-service)

`GET /api/portfolio/ledger-feed/settlements?broker=cathay&settle_from=…&settle_to=…&from=…&to=…` (no auth until #117;
HomeHub sends its read token once #117 adds one) →
```
{"rows": [{"trade_date": "2026-09-10", "settlement_date": "2026-09-12", "date_source": "statement",
           "net_amount": "-12345.0000", "legs_net": "-12345.0000", "currency": "TWD", "bookable": true,
           "reason": null, "items": [{"source_id": "ct:5", "type": "buy_settle", "amount": "-12300.0000"}, ..],
           "fingerprint": "cathay:2026-09-10:<sha256 of sorted ids, 16 hex>"}],
 "bounds": {"settle_from": "…", "settle_to": "…", "from": "…", "to": "…"}, "complete": true}
```
(amounts fictional). Two axes (rev 4.2, AGENT-360; rev 4.3): `from` / `to` bound the TRADE date of every row, with
or without statement facts; `settle_from` / `settle_to` additionally pull in statement days whose persisted
`settlement_date` lies in the window, whatever their trade date. A row is returned when its trade date lies in the
trade range, OR its persisted `settlement_date` lies in the settlement window — the settlement window is the only
filter that reads the settlement date, so a caller asking for one trade date gets that day's row regardless of
when it settles. The caller computes the trade range from its own business-day calendar (the earliest trade date whose
T+2 can reach `settle_from`, up to `settle_to`), so completeness never rests on a fixed calendar-day margin; the
response echoes the bounds it applied and `complete` (false when a row cap truncated it), and a caller that cannot
assert completeness (error, narrower bounds, `complete: false`) assigns nothing automatically — its lines stay
`settlement_pending`. One row per trade date with 國泰 cash: the legs
(`buy_settle`, `sell_settle`, `fee`, `tax`) of the trades in `TW_CATHAY_FILTER` (market TW, broker TW_CATHAY or
NULL; owner ruling Q2 — TW_MANUAL / TW_SINOPAC legs share the cash account and are left out), `legs_net = Σ amount`
(negative = money leaves the bank). The settlement date and the net come from the persisted 國泰 daily statement of
that trade date when one exists (owner ruling Q1 (c): `cathay_statement_day.settlement_date`, `net_amount` and the
cash net 淨收付金額, persisted on every post, §8): `date_source = "statement"`, `net_amount` = the cash net. Without
a statement (a CSV-only day) `settlement_date` is null, `date_source = "legs"` and `net_amount = legs_net`; HomeHub
derives the date as the 2nd bank business day after the trade date (`business_days` + `auto_pay.extra_holidays` /
`extra_workdays`; owner ruling Q1 (b) — TWSE settlement-only days are bank business days) and treats a date past
the table's `LAST_KNOWN_DAY` as unknown (pending, never guessed). A row is `bookable: false` — shown, contested,
never booked — with `reason = "margin_day"` when the statement day was held `unsupported_category` (資買 / 資賣 / 券買 /
券賣; owner ruling Q2: the owner settles margin days) and `reason = "legs_disagree"` when a statement day's cash net
differs from `legs_net`. A CSV-only margin day cannot be recognised (the CSV rows do not keep the side), but its
full-gross legs never equal the owner's part the bank moves, so it stays pending by amount. Rows whose net is zero
(`net_amount == 0`, whatever the source — a cash net of 0 when every buy was paid with points, a legs net of 0 on a
沖買/沖賣 CSV day) are omitted, items or not; the stand-in job (§4.7) never books a zero amount. Settlement dates are strictly order-preserving on a valid calendar, so two trade dates never settle
on one day; the "sum of two" rule of §9 exists only for a calendar fault and is gated behind an explicit fixture.

### 4.2 Account setting

`account.settlement_source` (new nullable column, migration): `"portfolio:cathay"` on account 67. Admin-only
`PATCH /accounts/{id}` field (the SPA shows it on the account settings page later; the first value is set by SQL
like `statement_live_from`). `account.valuation_source` (§5) is a sibling column.

### 4.3 Matcher: the `settlement` candidate

In `reconciliation_service.reconcile`, when the statement's account has `settlement_source` and the statement
prints a settlement line, the orchestrator fetches the settlements whose (statement or derived) settlement date can
fall in `period_start − 5 … period_end + 5` (a settlement-window query of the feed, §4.1: statement days by their
printed date, CSV-only days by the trade range the bank calendar implies — a 春節 run is found by its printed date;
an incomplete answer leaves every settlement line `settlement_pending`, never a silent booking; one HTTP call per reconcile, made before the candidate row locks with
a short timeout; HTTP failure → every settlement line `settlement_pending` and a `settlement_source_unavailable`
note on the `ReconcileResult`, the rest of the statement reconciled as usual, never a crash).

A settlement line is a bank line whose `line_kind ∈ {withdrawal, deposit}` and whose `merchant_norm` contains a
label of `settlement_labels` (default `交割股款`, `申購股款`; the same substring test the inbox's settlement question
uses, from the one rules setting). It meets, in this order (owner ruling Q5 (b)):
1. the ordinary exact rule over owner entries — an owner twin claims it as any line, two equal owner entries are an
   ordinary `ambiguous` case;
2. a settlement stand-in (§4.7) whose bank leg has `flow == line.flow` and `entry_date == line.posted_date` claims it
   (rule `settlement_stand_in`); a stand-in is never claimed by the ordinary rules or at another date;
3. the global assignment below for every line nothing claimed.
No near, drift, foreign or transfer rule, no bank-only hint and no backfill apply to settlement lines. A settlement
is a candidate for a line when `net_amount == line.flow` (exact, after quantisation), its settlement date is within
2 days of the line's posted date, and it is not reserved by a booking (§4.4).

**Assignment is global, not per line.** Before any case is written, the orchestrator builds the bipartite
candidate graph between EVERY settlement line of the account whose posted date falls in the fetched window ± 2 days
— the statement under reconcile and its neighbours' lines alike (current lines of the account's LIVE statements,
retired statements excluded; historical statements stay as they are, §11) — and every unreserved, dated settlement of
the window. A line something already explains (owner or policy coverage, a stand-in claim, this run's exact claim)
stays in the graph as a contestant that is never booked, so its settlement is never handed to a look-alike line.
A pair is booked only when it is the unique feasible assignment on both sides: the line has exactly one candidate,
that settlement has exactly one candidate line, and the settlement is `bookable`. Everything else is left open:
- a line with two candidates, or a settlement wanted by two lines (same amount within the window, e.g. two
  equal buys two days apart) → `ambiguous` case on each such line, candidates = the settlements (with their
  dates), hint `settlement_ambiguous`; the owner picks from the case (`book_settlement` with the settlement
  fingerprint as the parameter);
- a line with no candidate → `line_unmatched` with `hint: "settlement_pending"` ("waiting for the broker
  import"), excluded from `backfill_entry` and `create_system_entry` candidates (§4.4); a line whose only candidate
  is not `bookable` → the same, with the row's `reason` in the context;
- a line with no candidate while an unclaimed stand-in of the account lies within 2 days of it (another amount, or
  the same amount on another date) → `line_unmatched` with `hint: "settlement_mismatch"` naming the stand-in
  (booking id, date, amount); the stand-in itself surfaces as an orphan (§4.7) — the owner decides, nothing is fixed
  silently;
- a settlement with no line → nothing (data waiting for a statement).
A line claimed by an earlier booking is skipped. A uniqueness failure at booking time (§4.4) never promotes the
other line: both stay open for the next reconcile.

### 4.4 Booking: `book_settlement` (new policy + owner action)

A uniquely assigned pair is not a claim of an existing entry — there is none. The matcher emits a `settlement`
case (new `reconciliation_case.kind`, candidates = the one settlement, context = its fingerprint, date, source
facts) and the policy action `book_settlement` (enabled by default; owner ruling Q4 (b): zero-charged and exempt
from `max_amount`, the monthly budget, `per_statement_total` and `per_statement_count` — the bank already moved the
money — and still revertable) books, under the statement lock, the account row and the writer barrier:

1. **One booking owner.** A row in `settlement_booking` (new table, shaped like `statement_auto_pay`: id,
   account_id (the bank account), target_account_id (117), origin `bank` | `stand_in` (§4.7), event_id (the bank
   event; NULL for a stand-in until a line claims it), source `portfolio:cathay`, fingerprint, trade_date,
   settlement_date, net_amount, facts (the row as seen), transfer_group_id, out_entry_id / in_entry_id (ON DELETE
   SET NULL), status `booked` | `retired` | `replaced` | `owner_deleted` | `owner_edited` | `skipped`, reason,
   action_id, timestamps) and one `settlement_booking_item` row per constituent (`booking_id`, `source` `ct`,
   `source_id` = the portfolio `cash_transaction.id`, `amount` as booked; `(source, source_id)` UNIQUE). The unique
   constraints are the reservations: booking `{A, B}` and later `{A}` cannot both exist; a second booking for the
   same fingerprint is refused `already_booked`; fingerprint and (account, settlement_date) are unique among `booked`
   rows (partial unique indexes), so a replaced or retired row keeps its history. A row leaving `booked` releases its
   items in the same transaction.
2. **Two legs, provenance on the owner row.** A transfer 67 → 117 for a negative net (buy day), 117 → 67 for a
   positive one (sell day), amount = |line.flow|, date = the line's posted date, description `證券交割 <trade_date>`,
   `source = statement`, booked through `transfer_service.create_transfer` as the auto-pay and `pair_transfer`
   bookings are. The legs carry no new column: the booking row names them (rev 4: `ledger_entry` is not widened,
   as `statement_auto_pay` does it). `ux_statement_coverage_active_entry` is untouched: only the 67 leg is claimed.
3. **Claim.** The 67 leg claims the line (`match_rule = book_settlement`). "Already booked" is honoured only for
   the SAME bank event (the booking's `event_id`): a booking exists for this event → nothing new; a booking exists
   for another event with the same fingerprint → refuse `already_booked`, the line stays open (the owner sees the
   booked event in the case detail).
4. **Source facts, not just ids.** The booking stores the net amount and the constituent amounts it saw; a later
   reconcile recomputes the settlement from the portfolio and, if the facts differ under unchanged ids (an
   edited import, a leg resync), opens a `settlement` case with `hint: "source_drift"` on the claimed line (any
   origin) and never auto-rebooks (fail closed; the owner reverts and re-reconciles).
5. **Guards and shape.** The action re-checks under the barrier that no owner entry records the line
   (`stand_ins.owner_may_record`, as the `_TwinGuarded` creations do; owner ruling Q5 (b)) and that the line's
   event has no applied creation; it books and reverts the pair the way `pair_transfer` does (`create_transfer`,
   claim, revert = release → `delete_entries_cascade` of both legs → the booking row `retired` with its items
   deleted, in one transaction). The engine's snapshot gate refuses a revert after either leg changed.

Fees and taxes are inside the net: the ledger does not split them (117 is a position, not a P&L account — §5);
the portfolio keeps the per-trade detail.

### 4.5 Re-reconcile triggers (owner ruling Q3)

Owner, verbatim: 「1為主 不過每個月銀行對帳單來以後也要重新去對照」. There is no portfolio → HomeHub call and no new
token (the rev 3 call-back from `post_import_orchestrator` is dropped). Three triggers:

1. **After each 國泰 post.** The accounting worker's `cathay` job, which posts the daily statements to the
   portfolio, calls HomeHub `POST /reconciliation/settlements/run` (ingest scope, the worker's existing token)
   after every plain daily commit run that is not latched (so a statement posted that run, and a settlement date
   reached that morning, are both seen), with `{"source": "portfolio:cathay"}`: HomeHub runs the stand-in job (§4.7)
   and flags `needs_recheck` the live, non-retired statements of that `settlement_source` account whose period holds a
   settlement date the job booked, replaced or retired, plus those holding an open `settlement_pending` /
   `settlement_ambiguous` / `settlement_mismatch` case. Best effort: a failed call is logged by class name and never
   changes the job's result.
2. **The daily sweep.** `POST /reconciliation/sweep` first runs the stand-in job, then reconciles, besides the
   pending statements, every live statement of a `settlement_source` account holding an open `settlement_pending`,
   `settlement_ambiguous` or `settlement_mismatch` case — a missed trigger costs at most a day.
3. **The monthly bank statement.** Its ingest reconcile (the revision hook) fetches the feed afresh for its own
   lines, and flags the account's other live statements within the settlement window `needs_recheck`, so no case
   derived before the new lines existed stays actionable (a flagged statement is closed to the policy until its next
   reconcile).

A portfolio CSV upload (the SPA) triggers nothing; the next 國泰 post, the daily re-check or an owner reconcile covers
it.

### 4.6 Dividends (國泰 stocks)

A bank `deposit` line whose `merchant_norm` contains a dividend label (`股利`, `股息`, `現金股利`; `dividend_labels` in
the rules, like `bank_only_patterns`) matches a portfolio `dividends` row with `receipt_status = confirmed`,
`receipt_account_id` = the 國泰 TWD account, `receipt_date` within 3 days of the line and `amount == line.flow`
(owner ruling Q6 (a); rev 4 erratum: `dividends.amount` is already net of 匯費 and 補充保費, so the rev 3 formula
`amount − fee − tax` deducted them twice) → `dividend` case → `book_dividend` books an income entry on 67
(category from the settings, `needs_review` when unset), fingerprint = `dividend:<id>`. Same idempotency, caps
exemption and revert shape as §4.4. Pending entitlements are never candidates.

### 4.7 Daily settlement stand-ins (owner decision 2026-10-30, Q7)

Owner: 「我會想選2欸，然後後續月對帳單進來以後再重新對帳?」 — settlements are visible in the ledger the day they settle,
and the monthly bank statement re-reconciles them. The machinery is the auto-pay stand-in's
(`auto_pay_service` / `stand_ins`), applied to the 國泰 cash leg:

- **Candidate.** A `bookable` feed row with `date_source = "statement"` (a persisted 國泰 daily statement; TW_CATHAY +
  NULL broker rows only) whose settlement date lies on or after the go-live date (`settlement.enabled_from`) and on
  or before today (Asia/Taipei), on a `settlement_source` account with a target account configured. Margin days
  (`margin_day`), `legs_disagree` rows and CSV-only days get no stand-in: they wait for the bank line and the §4.3
  path (the CSV-only day with the T+2 business-day date). Days before go-live get none.
- **Gates**, in order: the stand-in setting enabled; a non-zero net; no final booking row for (account, settlement date)
  — `booked`, `owner_deleted`, `owner_edited`, a `retired` row of a reverted `book_settlement` or a twin — and no
  dismissed settlement case of the account for that fingerprint or that settlement date (controller ruling
  2026-10-30, owner to confirm: an owner's dismissal or revert of the bank booking is final for the stand-in job as
  well, the auto-pay `owner_deleted` principle; the bank path of §4.4 is unchanged); no reserved constituent; no owner twin — an owner entry on the bank account with the net's amount, or an owner
  transfer leg on the target account with its mirror, within `settlement.twin_window_days` (default 3) of the
  settlement date (owner ruling Q5: owner twins first) → `skipped` `twin`.
- **Booking.** The same transfer as §4.4 item 2, dated the settlement date, name `證券交割`, description
  `證券交割 <trade_date>`, a `settlement_booking` row `origin = stand_in`, `status = booked`, event_id NULL, with its
  items; zero-charged and outside every policy cap (it is not a policy action). Idempotent per (account,
  settlement_date): the job re-run books nothing new.
- **Housekeeping** (rev 4.2, AGENT-360; both origins, `bank` and `stand_in`, in the job AND at the start of every
  reconcile of the account, before the reservations are read): both legs gone → `owner_deleted` (the row leaves
  `booked` and releases its items in the same transaction; the tombstone stays: never re-booked, and the §4.3 path
  treats the event as owner-deleted — proposed, never auto-booked); a leg gone or edited → `owner_edited` (items
  kept: the edited pair still records the event); an owner twin appeared → the pair is retired (`retired`, reason
  `twin`, the auto-pay semantics: coverage released, the line re-meets the owner twin). For an UNCLAIMED stand-in
  only, under the row and leg locks with the claim state re-read: the feed row changed (another net, date or item
  set — a re-posted statement, a CSV edit) → the pair is deleted and the row `replaced`, then the new figures go
  through the candidate rules and gates again (statement source, bookable, non-zero, go-live ≤ new date ≤ today,
  final / dismissed / twin / reservation on the new date) and are booked only when they pass — otherwise the day
  waits; a feed row gone or no longer `bookable` → `retired` with the reason. A claimed stand-in is never retired
  or replaced by the job: a change after the claim is reconcile's `source_drift` (§4.4 item 4; the comparison
  covers net, settlement date, items, `bookable` and `date_source`). Where this departs from auto-pay: auto-pay
  pairs are never replaced (a card bill has one figure), so `retire_pair`'s "release the claim" step is reused for
  twins only; replacement and retirement for source reasons refuse a claimed pair instead of releasing it.
- **Locks** (rev 4.3). One protocol for everything that retires, replaces or tombstones a settlement booking, the
  auto-pay order: the booking row FOR UPDATE NOWAIT → its two legs FOR UPDATE NOWAIT → coverage re-read, inside one
  savepoint; a held row or leg is 409 / a skipped row (`settlement_busy`), never a wait (a blocking row lock would
  close a cycle: a job holding a twin-retired pair blocks on a statement's `needs_recheck` update while that
  statement's reconcile, holding the statement row, waits for the booking row). The stand-in lifecycle takes **no account row lock**: the
  engine's invariant (engine_locks.py:1-9) is that the account row is a blocking lock whose takers hold no entry
  or statement lock, and that is what keeps it cycle-free. Taking it in the job or in `reconcile` would close two
  cycles: (a) the job holds the account row, retires a twin and blocks on another statement's `needs_recheck`
  update while that statement's reconcile, holding its statement row, waits for the account row; (b) a reconcile
  that has flushed an `owner_deleted` tombstone waits for the account row while the job, holding it, waits for
  that booking row. Serialisation instead: the job's advisory lock (`SETTLEMENT_JOB_LOCK_KEY`) serialises jobs;
  the partial unique on (account, settlement_date) over `booked` rows serialises bookings for one day, whoever
  books (`book_stand_in` vs `book_settlement`); `reconcile` claims a stand-in leg only while it holds that leg
  (`_lock_candidates`, NOWAIT) and runs `housekeep` under its statement row with the same row → legs NOWAIT order;
  `book_settlement` and its revert keep the engine's own account lock + writer barrier (`_TwinGuarded`, a ledger
  writer like `create_system_entry`) and lock the booking row → legs NOWAIT after the case checks. So a job
  holding the legs makes the reconcile `reconcile_busy` and a reconcile holding them makes the job skip the row.
- **Bank line.** The monthly statement's settlement line claims the stand-in's bank leg on exact amount and exact
  date (§4.3 step 2): the stand-in becomes the bank-attested booking (its row stays `booked`, `event_id` set to the
  line's event), no second booking, no duplicate effect. An owner twin found at reconcile retires it first, as
  `auto_pay_service.retire_twins_for_statement` does. A line of another amount or date → `settlement_mismatch`
  (§4.3); a stand-in whose settlement date lies in a live statement's period with no claim anywhere → an
  `entry_unmatched` case with `hint: "orphaned_stand_in"` and `settlement_booking_id` (the auto-pay orphan surfacing):
  the owner matches it, deletes the pair, or leaves it for the next statement. Nothing is fixed silently.

## 5. Market-value accounts (Phase 1 for 117; Phase 1b for IB/FT/CS)

- `account.valuation_source = "portfolio:<broker>"`. Such an account's **balance is read through**: the ledger
  keeps only the transfer legs (cost flows) and the API's balance/net-worth reads replace the ledger balance
  with the portfolio's valuation, returning `{balance, balance_kind, stale, valuation: {value, currency, as_of,
  priced_through, complete, missing}}` (`balance` null while no complete valuation is available). No valuation entry
  is ever written; unrealised P&L is the portfolio's business and is not a ledger figure.
- Portfolio API (new): `GET /valuation?broker=<broker>&as_of=YYYY-MM-DD` → `{market_value, cash, total,
  currency, as_of, priced_through}` in the BROKER's currency (USD stays USD): positions as of `as_of` × the last
  price on or before `as_of` (`price_history`; `priced_through` = that date) plus the broker's cash as of
  `as_of` from the existing trade-inclusive balance (`cash_flow_service.get_broker_balance` /
  `list_balances`: explicit `broker_cash_flows` ± trade deltas — never recomputed here, never trades added
  twice). For `cathay` the cash term is 0 by definition (the cash is bank 67; its legacy `cash_transaction`
  ledger is not consulted for valuation).
- HomeHub contract (Phase 1 scope, rev 4: the account list and account detail — `list_accounts` / `get_account` —
  only; `ledger_service.account_balance:109`, `held_balance`, period `end_balance` and the net-worth totals stay
  ledger figures until Phase 1b): a valuation account's balance in those reads is `valuation.total` for the requested `as_of` (today by
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

- HomeHub, one migration: `account.settlement_source`, `account.valuation_source` (nullable text),
  `settlement_booking` + `settlement_booking_item` (§4.4; partial uniques on `fingerprint` and `(account_id, settlement_date)` where
  `status = booked`, unique `(source, source_id)` on the items).
  No `ledger_entry` column (rev 4: the booking row owns its two entry ids, as `statement_auto_pay` does).
  Portfolio, one migration: `cathay_statement_day.settlement_date`, `net_amount`, `cash_net` (nullable; §4.1). No
  `tw_trading_calendar` (rev 4: Q1 (c) + the accounting `business_days` fallback replace it).
- `settings.reconciliation.rules`: `settlement_labels` (default `["交割股款", "申購股款"]`), `settlement_window_days`
  (default 2), `settlement_sum_matching` (default false in Phase 1: the plan enables it only with a real closure fixture), `dividend_labels`; Phase 1b: `wire_labels`, `wire_accounts`.
- `settings.reconciliation.policy` actions: `book_settlement` (enabled true, cap-exempt, Q4), `book_dividend`
  (enabled true, cap-exempt); Phase 1b: `book_wire` (enabled false — owner confirms the first few).
- `settings.reconciliation.settlement` (new block, like `auto_pay`): `stand_in_enabled` (default false),
  `enabled_from` (the go-live date; required when enabled), `target_accounts` (bank account id → market-value
  account id), `twin_window_days` (default 3), `dividend_category_id` (nullable).
- `reconciliation_case.kind` += `settlement`, `dividend` (Phase 1b: `wire`) — a positive external candidate
  awaiting creation; missing data stays `line_unmatched` with hint `settlement_pending` (Phase 1b `wire_pending`);
  competing assignments are `ambiguous` with hint `settlement_ambiguous`; a line beside an unclaimed stand-in of
  another figure is `line_unmatched` + `settlement_mismatch`; drift is `settlement` + `source_drift`; an unclaimed
  stand-in in a live period is `entry_unmatched` + `orphaned_stand_in`.
- HomeHub endpoint: `POST /reconciliation/settlements/run` (ingest scope; §4.5). Portfolio:
  `GET /api/portfolio/ledger-feed/settlements` (two axes + bounds echo), `/valuation`, `/dividends` (follow-up plan); Phase 1b `/funding`, `POST
  /imports/ib-flex`.

## 9. Failure modes (fail closed, owner sees)

- Portfolio unreachable during reconcile → settlement lines stay `settlement_pending`; summary field
  `settlement_source_unavailable: true`. Unreachable during the stand-in job → nothing booked, nothing retired;
  the job reports `source_unavailable` and the next trigger retries.
- Net amount mismatch (a partial import, a missing fee row) → no candidate; the line stays pending; the owner
  sees the nearest settlement in the case detail (`nearest: {date, delta}`) to spot the missing import.
- Two trade dates settling on one bank day cannot happen when both dates come from the 國泰 statement or a valid
  calendar; it can when the fallback calendar is wrong (a missed exchange holiday). The sum rule is therefore
  gated: only when the two settlements' `settlement_date`s are within 2 days of each other AND the line equals
  their sum AND neither settles anything else — then one booking with two constituents (both reserved). The plan
  carries a fixture of a calendar fault (a typhoon closure) rather than an assumed one. Any overlap between a
  single-settlement candidate and a sum candidate for the same line or the same constituent → ambiguous, never
  auto-booked.
- Edited source under unchanged ids (a corrected broker import, a re-posted 國泰 statement) → before a line claims
  it: the stand-in is replaced (§4.7); after: `source_drift` case on the claimed line (§4.4 item 4); nothing
  claimed is rebooked automatically.
- Bank line on another date or amount than the stand-in → `settlement_mismatch` on the line and the stand-in
  orphaned, both shown to the owner; neither is moved or edited.
- Stand-in reverted by the owner, bank line arrives later → the event is owner-deleted: the line gets a
  `settlement` case that is proposed only (never auto-booked), and the job never re-books the stand-in.
- Concurrent reconciles of two statements of the same account racing for one settlement → the second insert
  into `settlement_booking_item` fails the unique constraint; that booking rolls back and its line stays open.
  The stand-in job and a reconcile serialise on the stand-in's leg rows (§4.7 Locks); a job that lost the race
  re-reads the claim under the locks and leaves the pair alone.
- Valuation stale → shown stale, never guessed.
- Revert of a `book_settlement` after a later reconcile: the line reopens as `settlement_pending`; the next
  reconcile books again unless the owner dismisses the case (`owner_deleted` rule as for backfills). The stand-in
  job never re-books that settlement day (§4.7 gates).

## 10. Tests (per phase, named so the plan can pin them)

- Portfolio unit: settlement facts persisted from a posted statement (imported, verified, duplicate fills NULLs,
  held margin day); `settlements` rows per trade date with `date_source`, `bookable` / `reason`, TW_CATHAY scope,
  zero nets omitted; `valuation` (TWD cathay), cash running balance.
- HomeHub unit (business days): T+2 over a weekend, a holiday, an `extra_workdays` Saturday, past `LAST_KNOWN_DAY`
  → None.
- HomeHub unit (matching): settlement representation exact / ±2 days / ambiguous twins / sum-of-two /
  no near-or-foreign on settlement lines / `settlement_pending` hint / backfill never offered / stand-in claim on
  exact date + amount only / owner exact twin claims before the stand-in.
- HomeHub unit (assignment): global uniqueness across neighbouring statements; one settlement wanted by two
  lines → both ambiguous; sum candidate overlapping a single candidate → ambiguous; reserved settlement excluded.
- HomeHub integration: `book_settlement` books two legs + booking owner + items, claims the line, idempotent on
  re-reconcile (same event), `already_booked` for another event, constituent overlap refused by the unique
  constraint, concurrent booking race (one wins, the other's line stays open), `source_drift` on an edited
  import, revert deletes both legs + booking atomically and reopens, revert-after-reconcile guard, cap exemption
  (a booking over `max_amount` with the budget exhausted still applies and charges nothing); stand-in job: books
  once per (account, settlement_date), skips margin / CSV-only / pre-go-live / future days and owner twins,
  replaces on a re-posted statement while unclaimed, `owner_deleted` after a revert never re-booked, bank line
  claims it, mismatch and orphan cases; `book_dividend`; read-through balance for today and a historical `as_of`,
  cold cache with the portfolio down (`valuation: null, stale: true`), net worth conversion once;
  `POST /reconciliation/settlements/run` marks statements.
- Worker: after a plain, unlatched 國泰 commit run the job calls `/reconciliation/settlements/run`; a dry run, a
  latched run or a `--file` / `--since` run does not; a failed call leaves the job result unchanged. Phase 2: Flex fetch → `/imports/ib-flex` with a fixture XML; latch on failure;
  the token absent from logs, exception text, request URLs, latch file and summary on success, 4xx, 5xx and
  timeout.
- Portfolio, Phase 1b / 2: `GET /funding` reads `broker_cash_flows` for foreign brokers and `cash_transaction`
  for cathay with namespaced ids; Flex adapter dedupes re-imports by fingerprint (Flex id in the note) and, with
  legacy_until, pairs the manual-era rows one-to-one by business key, reporting ambiguity.

## 11. Rollout

1. Phase 1 (國泰): both migrations + settings, the portfolio cash-leg setting on; set `settlement_source` on 67 and
   `valuation_source` on 117; then a dry run (no writes) lists, per live statement of 67, each settlement line's
   outcome — stand-in to claim, owner match, `book_settlement` candidate, pending, ambiguous, mismatch — and the
   cases it would dismiss or supersede (the pending settlement proposals of rev 3 are now `settlement` cases); the
   owner decides case by case, one case per question (owner ruling Q5). Then enable `book_settlement` and the
   stand-in job with `enabled_from` = the go-live date: no stand-ins for trade days that settled before it, and
   historical statements of 67 stay as they are apart from the owner's case-by-case decisions.
2. Phase 1b: create IB / FT / CS accounts, set `valuation_source`, `wire_accounts`; reconcile the wire months.
3. Phase 2: IB Flex timer on the worker; first run against the last 30 days; compare with the owner's last manual
   import (dry-run report). Firstrade / Schwab stay manual.

## 12. Out of scope

永豐證券/期貨 (statements stay on Drive unparsed); realised / unrealised P&L in the ledger; per-trade fee and tax
entries; parsing IB / FT / CS PDFs; Firstrade and Schwab automation; multi-currency settlement (cathay settles
in TWD only).
