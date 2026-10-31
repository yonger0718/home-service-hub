# Portfolio integration — dividends follow-up (Tasks 12–14) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The portfolio serves confirmed 國泰 dividend receipts through the ledger feed. The accounting service then books each one as income on the matching bank deposit line of the settlement-source account, cap-exempt and revertable, under the Phase 1 booking owner.

**Architecture:** The stock-portfolio-service adds `GET /api/portfolio/ledger-feed/dividends` next to the Phase 1 feed. A new `app/services/dividend_service.py` in the accounting-service does four things: it reads the feed once per reconcile — after the statement lock and the coverage sweep (`reconciliation_service.py:1009-1016`), together with the settlement window read, and before any pair or candidate row lock and the twin retirements; it assigns a dividend-labelled deposit line to exactly one unreserved confirmed receipt; it replaces that line's plain `line_unmatched` case with a `dividend` case (or `ambiguous` + `dividend_ambiguous`), or keeps it with hint `dividend_mismatch` when exactly one near receipt differs in amount; and it runs the single-entry housekeeping and twin retirement. `book_dividend` (in `settlement_actions.py`) books one `income` entry through `_Creation._book` and one `settlement_booking` row (`kind = dividend`).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic (both services), httpx 0.28.1 (accounting → portfolio, worker → accounting), pytest (accounting: `tests/unit`, `tests/integration` on PostgreSQL, `tests/worker`; portfolio: `tests/unit`, `tests/integration` on SQLite `create_all`), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` rev 4.5 (2026-10-30, the version it states at `ac963e59`), §4.6. Owner ruling Q6 (a) is the controlling interpretation; its text is quoted in R1 below (spec §4.6 lines 246-253). The shapes it mirrors are in §4.4 (booking, idempotency, revert), §4.7 (Booking, Housekeeping, Locks), §8 (settings, case kinds, endpoints) and §9.

**Parent plan:** `docs/superpowers/plans/2026-10-19-portfolio-integration-phase1.md` rev 4.5. Tasks 1–11 must be merged first. This plan consumes the Task 3 tables, the Task 7 `CAP_EXEMPT`, booking helpers and `settlement.dividend_category_id`, and the Task 2 feed module. It was moved out of the Phase 1 plan on 2026-10-30 after Multica review AGENT-360 (a separate booking-owner path with its own recovery semantics).

**Plan rev 1 (2026-10-30):** split from Phase 1 plan rev 4.1 without changes to the task contents. The task numbers were kept so Phase 1 cross-references (`Task 12 Dividend`, `Task 13`) still resolve.

**Plan rev 2 (2026-10-31):** rewritten against main `ac963e59`, under the rulings R1–R10 below. Every symbol it relies on is cited with its file:line at `ac963e59`. Changes from rev 1:
- `in_entry_id` holds the income entry (rev 1 said None).
- The item source is `dividend` (rev 1 said `div`).
- The client returns a `DividendFeed`.
- Task 14 (owner wins: housekeeping, twin retirement, owner-delete guards) is new, so Task 13 stays one reviewable unit.

**Plan rev 2.1 (2026-10-31, Multica review AGENT-374 on `6f3f8dcf`: CHANGES REQUIRED):**
- **Must 2 (a competitor bypassing uniqueness at apply time).** `facts_now` is replaced by `read_window`: `prepare` reads `line.posted_date ± 3` and keeps the rows. Under the locks, `_plan` re-runs the same assignment (`assignment_for`, shared with `outcomes`) against the current lines and reservations, and refuses `candidate_changed` unless the receipt is still this line's unique assignment. The policy applies through the action, so it inherits the check. New test: `test_a_competing_receipt_confirmed_after_the_case_is_refused_for_owner_and_policy`.
- **Should 1 (Review Focus tests prove their promises end to end).**
  - Two deposits with one receipt, policy on: `test_one_receipt_beside_two_equal_deposits_books_nothing`.
  - A claimed owner line with its unclaimed neighbour: `test_a_receipt_beside_an_owner_claimed_line_is_never_handed_to_its_neighbour`. By the symmetric rule the neighbour is `ambiguous`, never assigned.
  - The structural-edit test now runs the policy and asserts `proposed`, `owner_deleted` and zero booked rows. Rev 2.2 ruling 2 supersedes this: the receipt stays reserved and nothing is proposed.
  - Late confirmation goes through `POST /reconciliation/settlements/run`, which exercises its flagging step: `test_settlement_run_flags_and_books_a_dividend_confirmed_later`.
- **Should 2 (lock boundary).** The Architecture line, R3 and Task 13 Step 7 now state the precise boundary. New ordering test: `test_both_feed_reads_come_before_any_row_lock_with_one_cached_client` (Task 13), plus a dividend-housekeeping variant (Task 14).
- **Should 3 (currency under the locks).** `_plan` re-validates the refreshed account, its source currency, the statement currency and the receipt currency (409 `currency_mismatch`). New test: `test_an_account_currency_change_racing_the_booking_is_refused_under_the_locks`.
- **Nit.** The spec is cited as rev 4.5 and the parent plan as rev 4.5. References to untracked `.superpowers/` files are replaced by the ruling text and inline file:line evidence.
- **Must 1 (owner booking from an ambiguous case) waits for an owner ruling.** This revision keeps rev 2's `case_kinds {"dividend", "ambiguous"}` (owner override, policy never). Ruled in rev 2.2.
- **Found during the fold.**
  - `coverage_service.RETIRABLE_ACTIONS` (:559-560) lacks `book_dividend`. Task 14 adds it, with `test_the_owner_may_book_the_proposal_after_an_owner_delete`.
  - An open question for the owner on R6 `owner_edited`. Ruled in rev 2.2.

**Plan rev 2.2 (2026-10-31, three owner rulings of 2026-10-31):**
- **Ruling 1: owner override from an ambiguous case (AGENT-374 Must 1, variant A, Phase 1 parity).**
  - `case_kinds` stays `{"dividend", "ambiguous"}`. The owner may pick one receipt from a `dividend_ambiguous` case; the policy never books from one. R1 and R4 say so.
  - Under the locks, `_plan` still re-runs the assignment. The chosen fingerprint must remain in that line's unique-or-ambiguous set, else `candidate_changed`. Any actor other than the owner on an ambiguous case → 422 `not_a_candidate`.
  - Test renamed `test_two_receipts_make_an_ambiguous_case_the_owner_override_books_one`.
- **Ruling 2: `owner_edited` follows Phase 1 (比照).**
  - A structural owner edit tombstones the row `owner_edited` and keeps its item. The receipt stays reserved, is never offered again, and no proposal is made. Only `owner_deleted` (entry gone) clears the item and frees the receipt (`trade_settlement_service.py:312-318`).
  - `dividend_service.owner_deleted` reads `owner_deleted` rows only, like `tss._owner_deleted` (:568-575).
  - Rewritten: R6, Review Focus 4, Task 14's housekeeping and its edit test. The rev 2.1 open question is dropped from Follow-ups.
- **Ruling 3: hint `dividend_mismatch` (mirrors `settlement_mismatch`).**
  - When a dividend line has no exact candidate but exactly one near receipt of another amount, its ordinary `line_unmatched` case gets `{"hint": "dividend_mismatch", "nearest": {id, symbol, receipt_date, amount, delta}}`. It never becomes a booking and gets no candidates.
  - The hint is in the dividend family for `_settlement_family`, `_dismissed` and the inbox. It is not added to `tss.SETTLEMENT_CASE_HINTS`.
  - R1, R7, the new R11, Review Focus 6, `assign` (`Assignment.near`), `outcomes`, inbox, README, `COUNT_KEYS` and the counts asserts were all updated.
  - New tests: `test_one_near_receipt_of_another_amount_is_a_mismatch_and_two_are_none` (unit) and `test_a_fee_corrected_receipt_turns_the_mismatch_into_a_booking`.
  - **Re-check (R11).** `recheck_statement_ids` now covers every open dividend-family or plain dividend-line case. It compares nothing, like Phase 1. While such a case is open, the statement is re-reconciled on each run, and the reconcile's fresh feed read sees any receipt change. The feed row gains no `revision`, so Task 12 is unchanged.

## Global Constraints

- **R1 match (owner Q6 (a), §4.6).** A line is a candidate when all of these hold:
  - it is a bank `deposit` line;
  - its `merchant_norm` contains a `Rules.dividend_labels` label (default `("股利", "股息", "現金股利")`, read from the rules document exactly like `settlement_labels`);
  - the receipt has `receipt_status == "confirmed"` on the portfolio's default 國泰 TWD broker account;
  - `|receipt_date − line.posted_date| <= 3`;
  - `amount == line.flow` exactly. `dividends.amount` is already net: never subtract `fee` / `tax`.

  Outcomes:
  - exactly one unreserved candidate → a `dividend` case with `candidates = [facts]`;
  - several → `ambiguous` + hint `dividend_ambiguous`. The policy never books from it. The owner may pick one receipt (owner override — explicit owner ruling 2026-10-31; Q6 (a) constrains automatic booking);
  - none, with exactly one near receipt → the line keeps its ordinary `line_unmatched` case, with `context = {"hint": "dividend_mismatch", "nearest": {"id", "symbol", "receipt_date", "amount", "delta"}}` (rev 2.2, owner ruling 2026-10-31, mirroring `settlement_mismatch` at `trade_settlement_service.py:733-738`):
    - a near receipt is confirmed, unreserved, within ±3 days of the line, of another amount, and no line's candidate;
    - `delta = line.flow − amount` as a signed 4-dp string (`"+10.0000"`);
    - it is never a booking and has no candidates;
    - only a plain case (no context, no candidates) gets the hint;
    - the hint also takes the line out of `backfill_eligibility`, which wants no hint (`reconciliation_actions/__init__.py:79-83`);
  - none, with no near receipt or several → the line keeps its ordinary case, with no new hint.

  The motivating case for the mismatch hint (fictional figures): a lent-out stock's dividend arrives without the 10 TWD 匯費, so the bank line is 2,010 beside a receipt of 2,000 (fee 10).
  - Fee and tax belong to the owner once the row exists (`dividend_receipt_service.py:55-56`).
  - The owner sets the fee to 0 in the portfolio. `dividend_deduction_service.update_deductions` recomputes `amount` (2,010) and bumps `revision` (`dividend_deduction_service.py:35-83`); `receipt_date` does not move.
  - The open mismatch case keeps the statement in the re-check (R11), so the next run's reconcile offers the receipt and the policy books it.

  Pending, `legacy_unknown` and `unresolved` receipts are never candidates.
- **R2 feed.** `GET /api/portfolio/ledger-feed/dividends?broker=cathay&from=YYYY-MM-DD&to=YYYY-MM-DD`, bounded by `receipt_date`.
  - Each row is `{id, symbol, receipt_date, amount ("%.4f"), currency, fingerprint "dividend:<id>"}`. The answer also carries `complete`, under the same `ROW_CAP` rule as `settlements`.
  - A non-cathay broker answers 404 through `_UNKNOWN_BROKER`.
  - Client: frozen `Dividend`, `PortfolioClient.dividends(broker, *, date_from, date_to) -> DividendFeed` and a `_CachedClient` counterpart. Parse errors raise `PortfolioUnavailable`.
- **R3 one read.**
  - The read is made in `reconcile`, next to the settlement window read and with the same cached client. It comes after the statement lock and the coverage sweep (`reconciliation_service.py:1009-1016`, which may already update coverage and cases), and before `auto_pay_service.retire_twins_for_statement`, the housekeeping and twin retirements, and `_lock_candidates` — so before every pair and candidate row lock (§4.3).
  - The statement → sweep order and the NOWAIT booking → entry lock order (`tss.lock_pair`, `trade_settlement_service.py:258-265`) are unchanged, and `reconcile` takes no account row lock.
  - It is made only when the account has `settlement_source` AND the statement prints a dividend-labelled line. The window is the statement period ± 3 days.
  - Unavailable, incomplete or other-currency answers leave the labelled lines in their ordinary case. The run reports this (`settlement_source_unavailable`, `settlements["dividend_unavailable_code"]`).
  - The only other HTTP call is `book_dividend.prepare`'s re-read.
- **R4 booking.** `BookDividendAction(_TwinGuarded)` in `settlement_actions.py`: `name = "book_dividend"`, `case_kinds {"dividend", "ambiguous"}`, actors OWNER + POLICY, `budget_exempt = True`.
  - On an `ambiguous` case it is the owner override (explicit owner ruling 2026-10-31; Q6 (a) constrains automatic booking). The policy never collects one, and any other actor is refused 422 `not_a_candidate`.
  - `prepare` re-reads the line's window once (`line.posted_date ± 3`; `PortfolioUnavailable` → 409 `source_unavailable`).
  - Under the locks:
    - `candidate_changed` when the re-read receipt differs, when `amount != line.flow`, or when the receipt no longer fits the line against the current lines and reservations (rev 2.1, Must 2). For a `dividend` case the receipt must still be the line's unique assignment; for an `ambiguous` one it must still be in the line's unique-or-ambiguous set;
    - `currency_mismatch` unless the refreshed account, its source's currency, the statement and the receipt agree (rev 2.1, Should 3);
    - `already_booked` when a `booked` row of the fingerprint holds another event;
    - a replay (`Replay`) when the booked row covers this event.
  - `apply` books ONE `income` entry through `_Creation._book`: `category_id = settlement.dividend_category_id`, with `needs_review = True` when it is unset.
  - The booking row: `kind "dividend"`, `origin "bank"`, `target_account_id None`, `out_entry_id None`, `in_entry_id` = the entry, `trade_date = settlement_date = receipt_date`, `net_amount = amount`, `facts`, `transfer_group_id None`, `status "booked"`, `action_id`. It has one item `(source "dividend", source_id "dividend:<id>", amount)`.
  - Unique violations map through `tss.UNIQUE_CODES`.
  - Revert has the `_Creation.invert` semantics. Then the row is `retired` (`reason "revert"`, items cleared), the case goes back to `line_unmatched` with no candidates, and audit `dividend_retired` is written.
- **R5 reservations.** A booked receipt (fingerprint or item id) is never offered again. A second reconcile finds the line claimed and opens nothing.
- **R6 owner wins (§4.7, single leg).**
  - Housekeeping follows Phase 1 (owner ruling 2026-10-31, `trade_settlement_service.py:312-318`):
    - The income entry deleted → `owner_deleted`. The item is cleared and the receipt is free again: the next reconcile offers it, and the policy proposes it (`owner_deleted: True`) but never books it.
    - Edited structurally (no longer `source='statement'`) → `owner_edited`. The item is kept, so the receipt stays reserved (`tss.reservations` reads every row's items, :223-234). It is never offered again and nothing is proposed for it.
  - An owner `income` twin within `twin_window_days` retires the booking.
  - `OWNER_TWIN_GUARDED += "book_dividend"`; the twin check under the barrier is `_twin_check` plus `tss.find_twins(target_account_id=None)`, which supports None (`trade_settlement_service.py:253`).
- **R7 policy.**
  - `POLICY_DEFAULTS["actions"]["book_dividend"] = {"enabled": True, "max_amount": "0"}` (`CAP_EXEMPT` already lists it).
  - `POLICY_ACTIONS["book_dividend"] = _dividend_candidate`.
  - `OWNER_DELETED_GUARDED` and `PROPOSED_WHEN_OWNER_DELETED` += `book_dividend`.
  - `coverage_service.RETIRABLE_ACTIONS` += `book_dividend` (rev 2.1): the sweep then retires an owner-deleted booking's action, as it does for `book_settlement`.
  - `KIND_ACTIONS["dividend"] = ("book_dividend", "match", "dismiss")`.
  - `collect_candidates` kinds += `dividend`.
  - `_settlement_family` covers kind `dividend` and the hints `dividend_ambiguous` and `dividend_mismatch` (`dividend_service.FAMILY_HINTS`). Neither hint enters `tss.SETTLEMENT_CASE_HINTS`.
- **R8 out of scope:** stand-ins for dividends, foreign brokers, pending entitlements, and SPA work (follow-up issues; see Follow-ups).
- **R10 commands:**
  - accounting: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q <files>`
  - portfolio: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q <files>`
  - commit trailer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **R11 re-check (rev 2.2, owner ruling 2026-10-31).** `dividend_service.recheck_statement_ids` is unioned into `tss.recheck_targets`, so both the settlements run and the daily sweep flag and reconcile these statements.
  - Which statements: every live, non-retired statement of a settlement-source account that holds an `open` case of one of these shapes:
    - kind `dividend`;
    - a hint in `FAMILY_HINTS`;
    - a plain `line_unmatched` case on a dividend-labelled deposit line.
  - It compares nothing, which is Phase 1's mechanism (`trade_settlement_service.py:770-782`, every open settlement-hint case on every run). While such a case is open, the statement is re-reconciled on each run, and the reconcile's one fresh feed read sees any change to a receipt: a new confirmation, a `fee` correction moving `amount`, a moved `receipt_date`, or a status change.
  - No `revision` or timestamp is exposed by the feed or stored in accounting, so Task 12's row shape stays as it is.
  - A dismissed case is not `open`, so the dismissal holds. A booked line has no open case; a change to its receipt is the deferred `source_drift` (Follow-ups).
- **No migration.**
  - `reconciliation_case.kind` already allows `dividend` (`models/statements.py:28-29`, migration `d5e1f7a3b9c2`).
  - `ux_settlement_booking_day_booked` is partial on `kind = 'settlement'` (`models/statements.py:439-440`), so dividend rows never meet it.
  - `ux_settlement_booking_fingerprint_booked` and `ux_settlement_booking_item_source` hold dividends unchanged.
  - Audit `event` is a free `String(48)`.
- **Import cycle (#247).** `settlement_actions` and `dividend_service` read each other's and `trade_settlement_service`'s attributes at call time only, never at module level. `dividend_service` imports `reconciliation_service._as_line` locally, as `tss.applies` does.
- **Logs carry ids, dates and codes, never amounts** (as `tss` logs).
- **Copy:** the booked entry's `name` is `f"股利 {symbol}"`. `merchant` and `description` keep `_book`'s defaults (`line.merchant_raw[:128]`, `對帳建立 <period_end> <merchant_raw>`).
- **Revert, then the next reconcile:** the receipt is free again, so the next reconcile derives the `dividend` case again and the policy may re-book it unless the owner dismisses the line. This mirrors owner ruling A for settlements.
- **Test data:** fictional amounts only. Account ids 67 / 117 appear only as settings examples. No owner data.

## Review Focus

1. **Dismissal holds.** The owner dismissed a dividend line's plain `line_unmatched` case before the receipt was confirmed. The next reconcile must not open a `dividend` case, and the policy must not book it. Test: `test_a_dismissed_plain_case_hides_the_dividend_once_the_receipt_is_confirmed` (Task 13).
2. **Late confirmation is picked up.** The receipt is confirmed after the statement was reconciled. `POST /reconciliation/settlements/run` must flag the statement (through `dividend_service.recheck_statement_ids` → `tss.recheck_targets` → `flag_statements`), reconcile it, and let the policy book the dividend in the same call. Test: `test_settlement_run_flags_and_books_a_dividend_confirmed_later` (Task 13, `test_reconciliation_api.py`).
3. **One receipt beside two equal deposits, or beside a line the owner already claimed.**
   - Two equal deposits: both lines must be `ambiguous` and the policy books nothing; it never picks the first line in id order.
   - A claimed neighbour: the unclaimed line must not take the receipt the claimed line already explains. It is `ambiguous` and is never auto-booked.
   - A second equal receipt confirmed after the case was opened turns the booking into `candidate_changed`.

   Tests: `test_one_receipt_near_two_lines_is_ambiguous_on_both` (unit), `test_one_receipt_beside_two_equal_deposits_books_nothing`, `test_a_receipt_beside_an_owner_claimed_line_is_never_handed_to_its_neighbour` and `test_a_competing_receipt_confirmed_after_the_case_is_refused_for_owner_and_policy` (all Task 13).
4. **Cosmetic vs structural owner edit, and owner delete (owner ruling 2026-10-31, Phase 1 parity).**
   - A recategorised booked income entry keeps its booking.
   - An amount edit tombstones the row `owner_edited` and keeps its item. The receipt stays reserved, the next reconcile and policy runs open nothing for it, and zero rows are booked.
   - An owner delete tombstones it `owner_deleted` and clears the item. The policy then proposes the `dividend` case (`owner_deleted: True`) and never auto-books it.

   Tests: `test_an_owner_edit_tombstones_the_booking_only_when_structural` and `test_an_owner_delete_tombstones_the_booking_and_the_policy_proposes` (Task 14).
5. **Bad category setting.** `dividend_category_id` names an expense category or a deleted id. The booking still applies, uncategorised and with `needs_review`: never a wrong-kind category and never a 500. Test: `test_the_dividend_category_must_be_an_existing_income_category` (Task 13).
6. **Mismatch, then a fee correction (owner ruling 2026-10-31).**
   - The only near receipt of a dividend line differs in amount → `line_unmatched` + `dividend_mismatch`, with `nearest` and a signed `delta`. It is never a booking. Two near receipts give no hint.
   - The open case keeps the statement in the re-check (R11). After the owner corrects the receipt's fee in the portfolio, the next run flags and reconciles the statement and opens the `dividend` case, and the policy books it.

   Tests: `test_one_near_receipt_of_another_amount_is_a_mismatch_and_two_are_none` (unit) and `test_a_fee_corrected_receipt_turns_the_mismatch_into_a_booking` (Task 13).

---

## Task 12: Portfolio `GET /api/portfolio/ledger-feed/dividends` + client method

**Files:**
- Modify: `services/stock-portfolio-service/app/services/ledger_feed_service.py` (add `dividends` after `settlements` :102-134)
- Modify: `services/stock-portfolio-service/app/routers/ledger_feed.py` (route after `get_settlements` :17-31)
- Modify: `services/stock-portfolio-service/app/schemas/ledger_feed.py` (models after `SettlementsOut` :34-37)
- Modify: `services/stock-portfolio-service/README.md:108-109` (ledger-feed table row)
- Modify: `services/accounting-service/app/services/portfolio_client.py` (:18-19 path, dataclasses after `Valuation` :71-80, parser after `_valuation` :135, method after `valuation` :205, `_CachedClient` after :228)
- Test: `services/stock-portfolio-service/tests/integration/test_ledger_feed.py` (+1 helper, +4 tests)
- Test: `services/accounting-service/tests/unit/test_portfolio_client.py` (+2 tests)

**Interfaces:**
- Produces (portfolio):
  - `ledger_feed_service.dividends(db: Session, broker: str, *, date_from: date, date_to: date) -> dict` returns `{"rows": [...], "complete": bool}`.
  - Schemas `DividendRow(id: int, symbol: str, receipt_date: str, amount: str, currency: str, fingerprint: str)` and `DividendsOut(rows: list[DividendRow], complete: bool)`.
- Produces (accounting):
  - `portfolio_client.Dividend(id: int, symbol: str, receipt_date: date, amount: Decimal, currency: str, fingerprint: str)` (frozen) and `portfolio_client.DividendFeed(rows: tuple[Dividend, ...], complete: bool)` (frozen; always `True` once returned).
  - `PortfolioClient.dividends(broker: str, *, date_from: date, date_to: date) -> DividendFeed` and `_CachedClient.dividends(broker: str, **window: date) -> DividendFeed`. Both raise `PortfolioUnavailable` (`connect`, `timeout`, `http_<status>`, `bad_body`, `incomplete`).

- [ ] **Step 1: Write the failing portfolio tests**

Add a helper to `_Cathay` (:28-71):

`dividend(self, receipt_date: str, amount: str, *, fee="0", tax="0", status="confirmed", account_id=None, symbol="9999", shares=0) -> models.Dividend`

It writes one `models.Dividend` and commits:
- `market "TW"`, `currency "TWD"`, `ex_dividend_date` = the first of the receipt month (UTC), `stock_dividend_shares=shares`, `import_fingerprint=f"ledger-feed-div-{seq}"`;
- when `status == "confirmed"`: `receipt_date`, `receipt_account_id = account_id or self.account.id` and `receipt_confirmed_at` set;
- otherwise all three are None (`ck_dividends_confirmed_receipt`).

```python
DIVIDENDS = "/api/portfolio/ledger-feed/dividends"


def D(date_from, date_to, broker="cathay"):
    return {"broker": broker, "from": date_from, "to": date_to}


def test_dividends_list_confirmed_receipts_on_the_cathay_account_net(client, cathay):
    paid = cathay.dividend("2026-09-11", "990", fee="10", tax="0")       # amount is stored net: never 980
    cathay.dividend("2026-09-11", "500", status="pending")
    cathay.dividend("2026-09-12", "0", shares=50)                         # stock-only: no cash to match
    body = client.get(DIVIDENDS, params=D("2026-09-01", "2026-09-30")).json()
    assert body == {"rows": [{"id": paid.id, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "990.0000",
                              "currency": "TWD", "fingerprint": f"dividend:{paid.id}"}], "complete": True}


def test_dividends_range_bounds_the_receipt_date_and_other_accounts_are_left_out(client, cathay, db_session):
    other = BrokerAccount(broker=BrokerEnum.SINOPAC, nickname="Sinopac TWD", currency="TWD",
                          opening_balance=Decimal("0"), opening_date=date(2026, 1, 1), is_active=True)
    db_session.add(other); db_session.commit()
    inside = [cathay.dividend(day, "100") for day in ("2026-09-10", "2026-09-20")]
    cathay.dividend("2026-09-09", "100"); cathay.dividend("2026-09-21", "100")
    cathay.dividend("2026-09-15", "100", account_id=other.id)
    rows = client.get(DIVIDENDS, params=D("2026-09-10", "2026-09-20")).json()["rows"]
    assert [r["id"] for r in rows] == [d.id for d in inside]                # (receipt_date, id) order


def test_dividends_reuse_the_unknown_broker_404_and_the_row_cap(client, cathay, monkeypatch):
    response = client.get(DIVIDENDS, params=D("2026-09-01", "2026-09-30", broker="ib"))
    assert response.status_code == 404 and response.json() == {"detail": "unknown_broker"}
    monkeypatch.setattr(ledger_feed_service, "ROW_CAP", 1)
    cathay.dividend("2026-09-10", "100"); cathay.dividend("2026-09-11", "100")
    body = client.get(DIVIDENDS, params=D("2026-09-01", "2026-09-30")).json()
    assert body["complete"] is False and len(body["rows"]) == 1


def test_dividends_without_a_cathay_account_are_empty_and_complete(client):
    assert client.get(DIVIDENDS, params=D("2026-09-01", "2026-09-30")).json() == {"rows": [], "complete": True}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py -k dividends`
Expected: 4 failed (404 Not Found on the route)

- [ ] **Step 3: Implement the portfolio side**
  - `dividends(...)`:
    - `_check_broker` first;
    - the default account through `cash_account_service.resolve_default_cathay_twd_account(db)`: `CashAccountNotFound` → `{"rows": [], "complete": True}`; `CashAccountAmbiguous` propagates (a 500 the client reads as `http_500`: fail closed);
    - select `models.Dividend` with `receipt_status == "confirmed"`, `receipt_account_id == account.id`, `receipt_date` between the bounds and `amount > 0`, ordered `(receipt_date, id)`;
    - `complete = len(found) <= ROW_CAP`; rows = `found[:ROW_CAP]`; `amount` through `_amount`; `fingerprint = f"dividend:{row.id}"`.
  - Route: `@router.get("/dividends", response_model=DividendsOut)` with `broker: str = Query()`, `date_from: date_type = Query(alias="from")`, `date_to: date_type = Query(alias="to")`; `UnknownBroker` → `_UNKNOWN_BROKER`.
  - README row: `` | `GET` | `/api/portfolio/ledger-feed/dividends?broker=&from=&to=` | confirmed receipts on the 國泰 TWD account with `receipt_date` in range: `id`, `symbol`, `receipt_date`, `amount` (net of 匯費 / 補充保費), `currency`, `fingerprint` (`dividend:<id>`); plus `complete` | ``

- [ ] **Step 4: Run the portfolio tests to verify they pass**

Run: `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py`
Expected: all passed

- [ ] **Step 5: Write the failing client tests** in `tests/unit/test_portfolio_client.py`

```python
DIV = {"id": 7, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "990.0000", "currency": "TWD",
       "fingerprint": "dividend:7"}
DIV_WINDOW = dict(date_from=date(2026, 8, 29), date_to=date(2026, 10, 3))


def test_dividends_parse_send_the_receipt_window_and_memoise():
    seen = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"rows": [DIV], "complete": True})

    with pc.cached(pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler))) as c:
        feed = c.dividends("cathay", **DIV_WINDOW)
        c.dividends("cathay", **DIV_WINDOW)
    assert seen == [("/api/portfolio/ledger-feed/dividends", {"broker": "cathay", "from": "2026-08-29", "to": "2026-10-03"})]
    assert feed == pc.DividendFeed(rows=(pc.Dividend(7, "9999", date(2026, 9, 11), Decimal("990.0000"), "TWD", "dividend:7"),),
                                   complete=True)


@pytest.mark.parametrize("body,code", [
    ({"rows": [{**DIV, "amount": 990}], "complete": True}, "bad_body"),
    ({"rows": [{**DIV, "id": "7"}], "complete": True}, "bad_body"),
    ({"rows": [{**DIV, "id": True, "fingerprint": "dividend:True"}], "complete": True}, "bad_body"),
    ({"rows": [{**DIV, "fingerprint": "dividend:8"}], "complete": True}, "bad_body"),
    ({"complete": True}, "bad_body"),
    ({"rows": [DIV], "complete": False}, "incomplete"),
])
def test_malformed_or_incomplete_dividend_answers_are_unavailable(body, code):
    client = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(pc.PortfolioUnavailable) as info:
        client.dividends("cathay", **DIV_WINDOW)
    assert info.value.code == code and str(info.value) == code
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/unit/test_portfolio_client.py -k dividend`
Expected: FAIL with `AttributeError: ... has no attribute 'dividends'`

- [ ] **Step 7: Implement the client**
  - `_DIVIDENDS_PATH = "/api/portfolio/ledger-feed/dividends"`.
  - `_dividend(raw: dict) -> Dividend`:
    - `id` must be an `int` and not a `bool`;
    - `fingerprint == f"dividend:{id}"`;
    - `symbol` and `currency` must be `str`;
    - amount through `_dec`, date through `_date`;
    - any violation raises `ValueError`.
  - `dividends()` calls `_get` with `{"broker", "from", "to"}`. A `KeyError` / `TypeError` / `ValueError` / `InvalidOperation` → `bad_body`; `complete` through `_bool`; `False` → `incomplete`.
  - The `_CachedClient` key is `("dividends", broker, tuple(sorted(window.items())))`, like `settlements`.

- [ ] **Step 8: Run both services' touched files to verify they pass**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/unit/test_portfolio_client.py`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add services/stock-portfolio-service/app/services/ledger_feed_service.py services/stock-portfolio-service/app/routers/ledger_feed.py \
  services/stock-portfolio-service/app/schemas/ledger_feed.py services/stock-portfolio-service/README.md \
  services/stock-portfolio-service/tests/integration/test_ledger_feed.py \
  services/accounting-service/app/services/portfolio_client.py services/accounting-service/tests/unit/test_portfolio_client.py
git commit -m "portfolio+accounting: dividend receipts in the ledger feed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 13: Accounting — `dividend` cases and `book_dividend`

**Files:**
- Create: `services/accounting-service/app/services/dividend_service.py`
- Modify: `app/services/statements/matching.py`:
  - `DIVIDEND_LABELS` next to `SETTLEMENT_LABELS` :130;
  - `Rules.dividend_labels` after `settlement_sum_matching` :154;
  - `is_dividend_line` after `is_settlement_line` :348-350.
- Modify: `app/services/trade_settlement_service.py`:
  - `reservations` :223-234 gains a `kind` parameter;
  - `recheck_targets` :791 adds `dividend_service.recheck_statement_ids` (call-time import).
- Modify: `app/services/reconciliation_service.py`:
  - import :71-72;
  - `_settlement_family` :786-792 and `_dismissed` :795-818;
  - `reconcile` :1024-1044;
  - `_match_and_record` :1047-1111.
- Modify: `app/services/reconciliation_actions/settlement_actions.py`:
  - `_add_booking` helper (taken from `BookSettlementAction.apply` :226-236);
  - `BookDividendAction` with its params;
  - `register` after :274.
- Modify: `app/services/reconciliation_actions/__init__.py`:
  - `KIND_ACTIONS` :29 and :38;
  - `CONDITIONS` :141-143.
- Modify: `app/services/reconciliation_actions/coverage_actions.py:73` and `case_actions.py:47-49` (`case_kinds` += `"dividend"`).
- Modify: `app/services/reconciliation_actions/audit.py:9-21` (`"dividend_retired"`).
- Modify: `app/services/policy_service.py`:
  - `_dividend_candidate` after :364;
  - `POLICY_ACTIONS` :373;
  - `collect_candidates` kinds :421;
  - `_unchanged` after the `book_settlement` branch :553.
- Modify: `app/services/settings_service.py:135` (`POLICY_DEFAULTS["actions"]["book_dividend"]`).
- Modify: `app/services/inbox_service.py:52-65` (classify + docstring).
- Modify: `services/accounting-service/README.md` (a "Dividends" paragraph after the R7 Phase 1 section, ~:725).
- Modify: `tests/integration/settlement_fixtures.py` (`portfolio_stub` :145-176; helpers `dividend_row`, `dividend_line`).
- Modify: `tests/unit/test_import_order.py:24-30` (`ENTRY_POINTS` += `"app.services.dividend_service"`).
- Modify: `tests/unit/test_inbox_service.py:18-33` (+2 rows).
- Test (new): `tests/unit/test_dividend_assign.py`, `tests/integration/test_dividend_actions.py`.
- Test: `tests/integration/test_reconciliation_api.py` (+1, in the "settlement re-reconcile triggers" section after :335).

**Interfaces:**
- Consumes: Task 12 `Dividend`, `DividendFeed`, `PortfolioClient.dividends`. `Context.rules` (`reconciliation_actions/base.py:41`). Phase 1 `tss.broker_of`, `tss.currency_mismatch` (it reads `.currency` only), `tss.CURRENCY_MISMATCH`, `app.schemas.writes.SOURCE_CURRENCIES` (`writes.py:189`), `reconciliation_service._current_lines` (:170-172), `tss.lock_pair`, `tss.find_twins`, `tss.twin_window_days`, `tss.UNIQUE_CODES`, `tss.SETTLEMENT_BUSY`, `BookSettlementAction._replay`, `_candidate`, `_TwinGuarded`, `_Creation._book` / `invert`, `Context._settlement_plan`.
- Produces:
  - `matching.DIVIDEND_LABELS = ("股利", "股息", "現金股利")`; `Rules.dividend_labels: tuple[str, ...] = DIVIDEND_LABELS`. `RULE_DEFAULTS` and `_check_rules` pick it up from the dataclass (`settings_service.py:84, 238`).
  - `matching.is_dividend_line(line: Line, rules: Rules) -> bool`: `line_kind == "deposit"`, a dividend label in `merchant_norm`, and `not is_settlement_line(line, rules)` (settlement labels win).
  - `tss.reservations(db: Session, kind: str = KIND) -> tuple[set[str], set[str]]`. Every Phase 1 caller keeps the default.
  - `dividend_service` constants: `KIND = "dividend"`, `AMBIGUOUS_HINT = "dividend_ambiguous"`, `MISMATCH_HINT = "dividend_mismatch"` (rev 2.2), `FAMILY_HINTS = (AMBIGUOUS_HINT, MISMATCH_HINT)`, `WINDOW_DAYS = 3`, `ITEM_SOURCE = "dividend"`, `UNAVAILABLE_KEY = "dividend_unavailable_code"`, `COUNT_KEYS = ("dividend", "dividend_ambiguous", "dividend_mismatch")`.
  - `applies(account, statement, lines: Iterable[StatementLine], rules) -> bool`: `bool(account.settlement_source)` and `statement.kind == "bank"` and any `is_dividend_line`.
  - `window(statement) -> tuple[date, date]`: period ± `WINDOW_DAYS`.
  - `fetch_for(db, account, statement, *, client) -> tuple[DividendFeed | None, str | None]`: one `client.dividends(tss.broker_of(account), ...)`. `PortfolioUnavailable` → `(None, exc.code)`, logged `dividend.source_unavailable` with `statement_id` and `code`.
  - `facts_of(d: Dividend) -> dict` gives `{"fingerprint", "id", "symbol", "receipt_date" (ISO), "amount" (4-dp str), "currency"}`. `same_facts(booked: dict, now: dict | None) -> bool` compares fingerprint, `Decimal(amount)`, receipt_date and currency. `nearest_of(line: matching.Line, d: Dividend) -> dict` (rev 2.2) gives `{"id", "symbol", "receipt_date" (ISO), "amount" (4-dp str), "delta"}`, where `delta = f"{line.flow - d.amount:+.4f}"`.
  - `read_window(account, day: date, *, client: PortfolioClient | None = None) -> tuple[Dividend, ...]` (rev 2.1; replaces rev 2's `facts_now`):
    - one `client.dividends(tss.broker_of(account), date_from=day − WINDOW_DAYS, date_to=day + WINDOW_DAYS)` through `client or portfolio_client.client_for()`;
    - returns every row of the window;
    - raises the feed's `PortfolioUnavailable`, or `PortfolioUnavailable(tss.CURRENCY_MISMATCH)` when `tss.currency_mismatch(account, rows)`.

    Every candidate of a line, and every line the line's candidates could also fit, lies within `day ± WINDOW_DAYS` of a receipt in it, so the window decides the line's assignment.
  - `@dataclass(frozen=True) Assignment(unique: dict[int, Dividend], ambiguous: dict[int, tuple[Dividend, ...]], near: dict[int, Dividend] = field(default_factory=dict))` keyed by line id. `near` (rev 2.2) holds a line without candidates and with exactly one near receipt (R1). `assign(lines: Sequence[matching.Line], rows: Sequence[Dividend], reserved: set[str], rules: matching.Rules) -> Assignment` is pure.
  - `assignment_for(db, account_id: int, all_lines: list[StatementLine], rows: Sequence[Dividend], rules) -> Assignment` (rev 2.1) is the one rule both callers apply: `outcomes` at reconcile and `book_dividend._plan` under the action locks.
    - The lines that take part: every `is_dividend_line` line of `all_lines` except those whose event a `booked` `kind="dividend"` row of the account holds.
    - `reserved` = the union of both sets from `tss.reservations(db, kind=KIND)`.
    - Returns `assign(...)` over them.
  - `owner_deleted(db, fingerprint: str) -> bool`: a `kind="dividend"` row of the fingerprint in `owner_deleted` (rev 2.2: an `owner_edited` row keeps its item, so its receipt is never offered; `tss._owner_deleted` :568-575 shape).
  - `@dataclass Divided(cases: list[matching.Case], counts: dict, unavailable: bool)`.
  - `outcomes(db, statement, account, cases: list[matching.Case], all_lines: list[StatementLine], rules, feed: DividendFeed | None, unavailable_code: str | None) -> Divided`.
  - `category_for(db) -> tuple[int | None, bool]`: `(id, False)` only when `settlement.dividend_category_id` names an existing `Category` of kind `income`, else `(None, True)`.
  - `recheck_statement_ids(db) -> list[int]` (R11).
  - `settlement_actions.BookDividendParams(ActionParams)` with `fingerprint: str`.
  - `settlement_actions._add_booking(db: Session, booking: SettlementBooking, label: str) -> None`.
  - `ReconcileResult.settlements` gains `dividend`, `dividend_ambiguous`, `dividend_mismatch` and `dividend_unavailable_code` when the statement takes the dividend step.

- [ ] **Step 1: Write the failing fixtures and unit tests**

`settlement_fixtures.py`:
- `portfolio_stub` gains `dividends=[]`, `dividends_complete=True` and `dividend_calls=[]`.
- Its handler branches first on `request.url.path.endswith("/dividends")`:
  - append the params to `dividend_calls` (never to `calls`);
  - `stub.down` → 503;
  - else 200 `{"rows": [r for r in stub.dividends if from <= r["receipt_date"] <= to], "complete": stub.dividends_complete}`.
- Helpers:
  - `dividend_row(id: int, receipt_date: str, amount, *, symbol="9999", currency="TWD") -> dict` (the feed's JSON, `amount` through `_money`, `fingerprint f"dividend:{id}"`);
  - `dividend_line(seq, day, flow, merchant="現金股利")` = `bank_line(seq, day, merchant, flow)`.

`tests/unit/test_dividend_assign.py` (imports: `dataclasses.replace`, `date`, `Decimal`, `dividend_service as ds`, `portfolio_client.Dividend`, `statements.matching`):

```python
RULES = matching.Rules(period_end=date(2026, 9, 30))


def _line(id, day, flow, merchant="現金股利", kind="deposit"):
    return matching.Line(id=id, event_id=100 + id, posted_date=day, txn_date=None, flow=Decimal(flow), foreign_amount=None,
                         foreign_currency=None, line_kind=kind, merchant_norm=merchant, installment_seq=None,
                         installment_total=None)


def _div(id, day, amount):
    return Dividend(id, "9999", day, Decimal(amount), "TWD", f"dividend:{id}")


def test_a_dividend_line_is_a_deposit_naming_a_label_and_no_settlement_label():
    assert matching.is_dividend_line(_line(1, date(2026, 9, 12), "990"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "-990", kind="withdrawal"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="交割股款 股利"), RULES)
    assert not matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="薪資"), RULES)
    assert matching.is_dividend_line(_line(1, date(2026, 9, 12), "990", merchant="配息"),
                                     replace(RULES, dividend_labels=("配息",)))


def test_assign_takes_the_exact_net_amount_within_three_days():
    lines = [_line(1, date(2026, 9, 12), "990")]
    rows = [_div(7, date(2026, 9, 15), "990"), _div(8, date(2026, 9, 16), "990"), _div(9, date(2026, 9, 12), "989")]
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={1: rows[0]}, ambiguous={})


def test_reserved_receipts_are_skipped_and_several_are_ambiguous():
    lines = [_line(1, date(2026, 9, 12), "990")]
    rows = [_div(7, date(2026, 9, 11), "990"), _div(8, date(2026, 9, 13), "990")]
    assert ds.assign(lines, rows, {"dividend:7"}, RULES) == ds.Assignment(unique={1: rows[1]}, ambiguous={})
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={}, ambiguous={1: (rows[0], rows[1])})


def test_one_receipt_near_two_lines_is_ambiguous_on_both():
    lines = [_line(1, date(2026, 9, 12), "990"), _line(2, date(2026, 9, 13), "990")]
    rows = [_div(7, date(2026, 9, 12), "990")]
    assert ds.assign(lines, rows, set(), RULES) == ds.Assignment(unique={}, ambiguous={1: (rows[0],), 2: (rows[0],)})


def test_one_near_receipt_of_another_amount_is_a_mismatch_and_two_are_none():
    """Rev 2.2, owner ruling 3: a hint only. Reserved receipts, receipts another line takes, and receipts more than
    three days away are not near."""
    lines = [_line(1, date(2026, 9, 12), "2010")]
    one = [_div(7, date(2026, 9, 11), "2000"), _div(8, date(2026, 9, 16), "2000")]          # 8 is four days away
    assert ds.assign(lines, one, set(), RULES) == ds.Assignment(unique={}, ambiguous={}, near={1: one[0]})
    assert ds.nearest_of(lines[0], one[0]) == {"id": 7, "symbol": "9999", "receipt_date": "2026-09-11",
                                               "amount": "2000.0000", "delta": "+10.0000"}
    assert ds.nearest_of(_line(1, date(2026, 9, 12), "1990"), one[0])["delta"] == "-10.0000"
    two = [_div(7, date(2026, 9, 11), "2000"), _div(9, date(2026, 9, 14), "1500")]
    assert ds.assign(lines, two, set(), RULES) == ds.Assignment(unique={}, ambiguous={})
    assert ds.assign(lines, one, {"dividend:7"}, RULES) == ds.Assignment(unique={}, ambiguous={})
    taken = [_line(1, date(2026, 9, 12), "2010"), _line(2, date(2026, 9, 12), "2000")]
    assert ds.assign(taken, one[:1], set(), RULES) == ds.Assignment(unique={2: one[0]}, ambiguous={})
```

`tests/unit/test_inbox_service.py` parametrize (+2 rows):
`("ambiguous", "open", "bank", "dividend_ambiguous", "現金股利", ["match", "book_dividend", "dismiss"], "other"),`
`("line_unmatched", "open", "bank", "dividend_mismatch", "現金股利", ["match", "create_entry", "dismiss"], "other"),` (rev 2.2: `other`, never `bank_only`, although `create_entry` is allowed)

`tests/unit/test_import_order.py`: `ENTRY_POINTS` += `"app.services.dividend_service"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/unit/test_dividend_assign.py tests/unit/test_inbox_service.py tests/unit/test_import_order.py`
Expected: FAIL. `test_dividend_assign.py` fails collection with `ModuleNotFoundError: No module named 'app.services.dividend_service'`; the new inbox rows return `ambiguous` and `bank_only`.

- [ ] **Step 3: Implement the matching rule, `assign` and inbox**
  - `matching.py`:
    - `DIVIDEND_LABELS`;
    - `Rules.dividend_labels`;
    - `is_dividend_line`.
  - `dividend_service.py` with the Interfaces block's constants, `facts_of`, `same_facts` and `assign`. Only `is_dividend_line` lines take part. A row is a candidate of a line when it is unreserved (fingerprint not in `reserved`), `amount == line.flow` (both quantised to 4 dp) and the dates are within `WINDOW_DAYS`. The uniqueness rule:

```python
# both sides unique within the statement: a receipt that two lines could take decides neither
takers = Counter(row.fingerprint for found in candidates.values() for row in found)
unique = {lid: found[0] for lid, found in candidates.items() if len(found) == 1 and takers[found[0].fingerprint] == 1}
ambiguous = {lid: found for lid, found in candidates.items() if found and lid not in unique}  # (receipt_date, id) order
```

  - `near` (rev 2.2) covers each line without candidates:
    - its near rows are unreserved, within `WINDOW_DAYS`, of another amount, and absent from `takers`;
    - the line gets `near[lid]` only when exactly one such row exists.

  - `inbox_service.classify`: `kind == "dividend"`, or a `hint` in `dividend_service.FAMILY_HINTS` (`dividend_ambiguous` on `ambiguous`, `dividend_mismatch` on `line_unmatched`) → `"other"`, in the branch of today's `kind == "dividend"` test (`inbox_service.py:64-65`), before the `proposed` and `bank_only` branches. `inbox_service` reads `dividend_service.FAMILY_HINTS` at call time only (#247).
    - The ambiguous bucket's answers are entry matches, and these candidates are receipts.
    - A mismatch line is answered in the portfolio (the fee) or by dismissal, not as a bank-only line.

- [ ] **Step 4: Run them to verify they pass**

Run: the Step 2 command
Expected: all passed

- [ ] **Step 5: Write the failing integration tests** in `tests/integration/test_dividend_actions.py`

Imports follow `test_settlement_actions.py:5-28`, plus:
- `ReconciliationAudit` from `app.models`;
- `bank_line`, `dividend_line`, `dividend_row` and `put_settings` from `settlement_fixtures`;
- `_booked` / `_all_cases` from `test_settlement_actions`;
- `dividend_service as ds` and `trade_settlement_service as tss`.

```python
FACTS = {"fingerprint": "dividend:7", "id": 7, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "990.0000",
         "currency": "TWD"}
CONTEXT = {"fingerprint": "dividend:7", "source": "portfolio:cathay", "owner_deleted": False}


def _dividend_case(seed, db, run, stub, *, rows=None, flow="990", day=date(2026, 9, 12)):
    """A live settlement-source bank statement (SEP) printing one 現金股利 deposit; returns (bank, sid, open cases)."""
    bank, _ = settlement_bank(seed, db, target=False)
    stub.dividends = [dividend_row(7, "2026-09-11", "990")] if rows is None else rows
    sid = submit_bank(db, run, bank, [dividend_line(1, day, flow)])
    return bank, sid, _cases(db, sid, "open")


def _plain(case) -> bool:
    return case.kind == "line_unmatched" and not case.candidates and "hint" not in (case.context or {})


def test_book_dividend_books_one_income_entry_idempotently_and_reverts(seed, db_session, run, portfolio_stub):
    bank, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    assert (case.kind, case.candidates, case.context) == ("dividend", [FACTS], CONTEXT)
    assert reconciliation_actions.allowed(case) == ["book_dividend", "match", "dismiss"]
    row = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    entry_id = booking.in_entry_id
    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.account_id, entry.kind, entry.amount, entry.entry_date, entry.source, entry.name, entry.category_id,
            entry.needs_review) == (bank.id, "income", Decimal("990"), date(2026, 9, 12), "statement", "股利 9999", None, True)
    assert (booking.kind, booking.origin, booking.status, booking.target_account_id, booking.out_entry_id,
            booking.transfer_group_id, booking.event_id, booking.action_id) == (
        "dividend", "bank", "booked", None, None, None, case.event_id, row.id)
    assert (booking.fingerprint, booking.trade_date, booking.settlement_date, booking.net_amount, booking.facts) == (
        "dividend:7", date(2026, 9, 11), date(2026, 9, 11), Decimal("990"), FACTS)
    assert [(i.source, i.source_id, i.amount) for i in booking.items] == [("dividend", "dividend:7", Decimal("990"))]
    assert [(c.entry_id, c.match_rule) for c in _active(db_session, sid)] == [(entry_id, "book_dividend")]
    _reconcile(db_session, sid)
    assert _cases(db_session, sid, "open") == [] and _booked(db_session) == 1     # claimed line, reserved receipt
    _revert(db_session, row.id)
    booking = _fresh(db_session, SettlementBooking, booking.id)
    assert (booking.status, booking.reason, booking.items) == ("retired", "revert", [])
    assert db_session.get(LedgerEntry, entry_id) is None
    (reopened,) = _cases(db_session, sid, "open")
    assert (reopened.id, reopened.kind, reopened.candidates, reopened.context) == (case.id, "line_unmatched", [], {})
    assert db_session.scalars(select(ReconciliationAudit.detail).where(
        ReconciliationAudit.event == "dividend_retired")).one()["reason"] == "revert"


@pytest.mark.parametrize("receipt_date,amount,kind,delta", [
    ("2026-09-15", "990", "dividend", None), ("2026-09-09", "990", "dividend", None),   # ±3 days is inside
    ("2026-09-16", "990", "line_unmatched", None),                                      # four days: not even near
    ("2026-09-11", "989", "line_unmatched", "+1.0000"),                                 # rev 2.2: a near receipt
    ("2026-09-11", "1000", "line_unmatched", "-10.0000"),                               # gross before 匯費: never deducted
])
def test_only_the_exact_net_amount_within_three_days_is_a_dividend(seed, db_session, run, portfolio_stub, receipt_date,
                                                                  amount, kind, delta):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, receipt_date, amount)])
    assert case.kind == kind
    if kind == "line_unmatched":
        assert _plain(case) if delta is None else (
            case.candidates == [] and (case.context["hint"], case.context["nearest"]["delta"]) == ("dividend_mismatch", delta))


def test_two_receipts_make_an_ambiguous_case_the_owner_override_books_one(seed, db_session, run, portfolio_stub,
                                                                         policy_on):
    """Owner override, an explicit owner ruling of 2026-10-31: Q6 (a) constrains automatic booking. The policy books
    nothing from an ambiguity; the owner picks one receipt."""
    policy_on()
    rows = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-13", "990", symbol="9998")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    assert (case.kind, case.context, [c["fingerprint"] for c in case.candidates]) == (
        "ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7", "dividend:8"])
    assert "book_dividend" in reconciliation_actions.allowed(case) and "book_settlement" not in reconciliation_actions.allowed(case)
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert (result.applied, result.proposed) == ([], []) and _booked(db_session) == 0   # the policy never answers one
    exc = _refuse(db_session, lambda: engine.apply_action(db_session, case_id=case.id, action="book_dividend",
                                                          params={"fingerprint": "dividend:8"}, actor="policy",
                                                          label="policy"))
    assert _code(exc) == "not_a_candidate"                                              # the override is the owner's
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:8"})
    assert db_session.scalar(select(SettlementBooking.fingerprint)) == "dividend:8"


def test_one_receipt_beside_two_equal_deposits_books_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    bank, _ = settlement_bank(seed, db_session, target=False)
    portfolio_stub.dividends = [dividend_row(7, "2026-09-12", "990")]
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 12), "990"),
                                              dividend_line(2, date(2026, 9, 13), "990")])
    assert [(c.kind, c.context, [x["fingerprint"] for x in c.candidates]) for c in _cases(db_session, sid, "open")] == \
        [("ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7"])] * 2
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert (result.applied, result.proposed) == ([], []) and _booked(db_session) == 0


def test_a_receipt_beside_an_owner_claimed_line_is_never_handed_to_its_neighbour(seed, db_session, run, portfolio_stub,
                                                                                policy_on):
    """The symmetric rule counts the owner-claimed line as a taker: the receipt fits both lines, so the unclaimed
    neighbour is `ambiguous` (listing it) and is never assigned or auto-booked."""
    policy_on()
    bank, _ = settlement_bank(seed, db_session, target=False)
    seed.entry(bank, "990", kind="income", day=date(2026, 9, 9)); db_session.commit()      # claims line 1 (same day)
    portfolio_stub.dividends = [dividend_row(7, "2026-09-12", "990")]                       # 3 days from both lines
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 9), "990"),
                                              dividend_line(2, date(2026, 9, 15), "990")])  # 6 days from the owner entry
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context, [c["fingerprint"] for c in case.candidates]) == (
        "ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7"])
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0


def test_a_competing_receipt_confirmed_after_the_case_is_refused_for_owner_and_policy(seed, db_session, run,
                                                                                     portfolio_stub, policy_on):
    """Rev 2.1 Must 2: the case was unique when listed; a second equal receipt confirmed since, on another day inside
    the line's window, makes it an ambiguity — `_plan` re-runs the assignment and refuses. The policy applies through
    the same action, so it is refused the same way and books nothing."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)          # unique: dividend:7 (2026-09-11)
    assert case.kind == "dividend"
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-14", "990")]
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "candidate_changed"
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert result.applied == [] and result.skipped.get("candidate_changed") == 1 and _booked(db_session) == 0


def test_an_account_currency_change_racing_the_booking_is_refused_under_the_locks(pg_engine, seed, db_session, run,
                                                                                  portfolio_stub, monkeypatch):
    """Rev 2.1 Should 3: prepare's read saw a TWD account; a concurrent writer changes it to USD before the account
    lock. `_plan` re-checks the refreshed account against its source, the statement and the receipt."""
    bank, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    original = engine_locks.lock_account_and_barrier

    def racing(db, targets, statement):
        with Session(pg_engine) as writer:
            writer.execute(update(Account).where(Account.id == bank.id).values(currency="USD"))
            writer.commit()
        original(db, targets, statement)

    monkeypatch.setattr(engine_locks, "lock_account_and_barrier", racing)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "currency_mismatch" and _booked(db_session) == 0
    assert db_session.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.source == "statement")) == 0


def test_both_feed_reads_come_before_any_row_lock_with_one_cached_client(seed, db_session, run, portfolio_stub,
                                                                         monkeypatch):
    """Rev 2.1 Should 2, the Phase 1 `test_the_window_read_comes_before_any_row_lock` shape: a statement printing a
    settlement line AND a dividend line; tss.housekeep locks an owner-deleted stand-in (row → legs NOWAIT)."""
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    ews.delete_entry(db_session, booking.out_entry_id); db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    seen, clients = [], []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            seen.append((name, len(portfolio_stub.calls), len(portfolio_stub.dividend_calls)))
            return real(*args, **kwargs)
        return wrapped

    for module, name in ((pair_locks, "lock_row"), (pair_locks, "lock_legs"), (rec, "_lock_candidates")):
        monkeypatch.setattr(module, name, spy(name, getattr(module, name)))
    real_cached = portfolio_client.cached
    monkeypatch.setattr(portfolio_client, "cached", lambda client: clients.append(client) or real_cached(client))
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                        dividend_line(2, date(2026, 9, 12), "990")])
    assert {"lock_row", "_lock_candidates"} <= {name for name, *_ in seen}
    assert {(s, d) for _, s, d in seen} == {(1, 1)} and len(clients) == 1   # every lock after both reads, one client


def test_an_owner_income_entry_claims_the_line_first(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session, target=False)
    seed.entry(bank, "990", kind="income", day=date(2026, 9, 12)); db_session.commit()
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 12), "990")])
    assert _cases(db_session, sid, "open") == [] and _booked(db_session) == 0


def test_policy_books_dividends_uncapped_and_charges_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on(max_amount_everywhere="1", per_statement_count=0, per_statement_total="0", monthly_total="0")
    _, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, "2026-09-11", "999999")],
                               flow="999999")
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"
    assert db_session.scalar(select(func.coalesce(func.sum(PolicyBudget.used_count), 0))) == 0
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


@pytest.mark.parametrize("failure,code", [("down", "http_503"), ("incomplete", "incomplete"), ("usd", "currency_mismatch")])
def test_an_unreadable_dividend_feed_leaves_the_ordinary_case(seed, db_session, run, portfolio_stub, policy_on, failure, code):
    policy_on()
    portfolio_stub.down, portfolio_stub.dividends_complete = failure == "down", failure != "incomplete"
    rows = [dividend_row(7, "2026-09-11", "990", currency="USD" if failure == "usd" else "TWD")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    result = _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _plain(case) and result.settlement_source_unavailable is True
    assert result.settlements == {"dividend": 0, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": code}
    assert _booked(db_session) == 0


def test_one_dividend_read_per_reconcile_and_none_without_a_label_or_source(seed, db_session, run, portfolio_stub):
    bank, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub)
    before = len(portfolio_stub.dividend_calls)
    _reconcile(db_session, sid)
    assert len(portfolio_stub.dividend_calls) == before + 1 and portfolio_stub.calls == []
    assert portfolio_stub.dividend_calls[-1] == {"broker": "cathay", "from": "2026-08-29", "to": "2026-10-03"}
    plain = seed.account("活存", statement_live_from=date(2026, 1, 1)); db_session.commit()
    submit_bank(db_session, run, plain, [dividend_line(1, date(2026, 9, 12), "990")])               # no settlement_source
    submit_bank(db_session, run, bank, [bank_line(1, date(2026, 10, 5), "薪資", "990")], period=OCT)  # no dividend label
    assert len(portfolio_stub.dividend_calls) == before + 1


def test_a_receipt_confirmed_later_is_offered_on_the_next_reconcile(seed, db_session, run, portfolio_stub):
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[])     # still pending: not in the feed
    assert _plain(case) and sid in tss.recheck_targets(db_session, {})
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    result = _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert case.kind == "dividend"
    assert result.settlements == {"dividend": 1, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": None}
    assert sid in tss.recheck_targets(db_session, {})                                  # R11: an open `dividend` case
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    assert sid not in tss.recheck_targets(db_session, {})


def test_a_fee_corrected_receipt_turns_the_mismatch_into_a_booking(seed, db_session, run, portfolio_stub, policy_on):
    """Owner ruling 2026-10-31 (fictional figures): a lent-out stock's dividend arrives without the 10 TWD 匯費, so the
    bank line is 2,010 beside a receipt of 2,000 (fee 10). The owner sets the fee to 0 in the portfolio
    (`update_deductions`: amount 2,010, revision + 1, receipt_date unchanged). R11 keeps the statement in the re-check
    while the mismatch case is open, so the next re-check reconciles it, opens the `dividend` case, and the policy
    books it."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, "2026-09-11", "2000")],
                                     flow="2010")
    assert (case.kind, case.candidates, case.context) == ("line_unmatched", [], {
        "hint": "dividend_mismatch",
        "nearest": {"id": 7, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "2000.0000", "delta": "+10.0000"}})
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and sid in tss.recheck_targets(db_session, {})          # a hint, never a booking
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "2010")]                       # the fee corrected to 0
    assert tss.flag_statements(db_session, tss.recheck_targets(db_session, {})) == 1
    result = _reconcile(db_session, sid)
    assert result.settlements == {"dividend": 1, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": None}
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, [c["amount"] for c in case.candidates]) == ("dividend", ["2010.0000"])
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert db_session.scalar(select(SettlementBooking.net_amount)) == Decimal("2010")
    assert tss.recheck_targets(db_session, {}) == set()                                      # the line is claimed


def test_a_dismissed_plain_case_hides_the_dividend_once_the_receipt_is_confirmed(seed, db_session, run, portfolio_stub,
                                                                                policy_on):
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[])
    _apply(db_session, case.id, "dismiss", {"note": "owner: not a dividend"})
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and _all_cases(db_session, sid) == [("line_unmatched", None, "dismissed")]


def test_a_dismissed_ambiguity_holds_when_it_narrows_to_one(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    rows = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-13", "990")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    _apply(db_session, case.id, "dismiss", {"note": "owner: later"})
    portfolio_stub.dividends = rows[:1]
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and _all_cases(db_session, sid) == [("ambiguous", "dividend_ambiguous", "dismissed")]


def test_the_dividend_family_and_its_dismissal_rule():
    from app.services.reconciliation_service import _dismissed, _settlement_family
    plain = SimpleNamespace(kind="line_unmatched", event_id=7, entry_id=None, context={})
    book = {"kind": "dividend", "event_id": 7, "entry_id": None, "context": {"fingerprint": "dividend:7"}}
    many = {"kind": "ambiguous", "event_id": 7, "entry_id": None, "context": {"hint": "dividend_ambiguous"}}
    settle = {"kind": "settlement", "event_id": 7, "entry_id": None, "context": {"fingerprint": "f"}}
    near = {"kind": "line_unmatched", "event_id": 7, "entry_id": None, "context": {"hint": "dividend_mismatch"}}
    assert _settlement_family("dividend", {}) and _settlement_family("ambiguous", {"hint": "dividend_ambiguous"})
    assert _settlement_family("line_unmatched", {"hint": "dividend_mismatch"})              # rev 2.2
    assert _dismissed([plain], book) and _dismissed([plain], many) and not _dismissed([plain], dict(book, event_id=8))
    assert _dismissed([plain], near) and _dismissed([SimpleNamespace(**near)], book)       # holds across a fee fix
    assert not _dismissed([plain], settle)                   # a plain dismissal stands in for the dividend family only
    assert not set(ds.FAMILY_HINTS) & set(tss.SETTLEMENT_CASE_HINTS)                       # never a stand-in hint


CHANGES = {"amount": lambda s: setattr(s, "dividends", [dividend_row(7, "2026-09-11", "980")]),
           "gone": lambda s: setattr(s, "dividends", []),
           "moved": lambda s: setattr(s, "dividends", [dividend_row(7, "2026-09-13", "990")]),
           "down": lambda s: setattr(s, "down", True)}


@pytest.mark.parametrize("change,code", [("amount", "candidate_changed"), ("gone", "candidate_changed"),
                                         ("moved", "candidate_changed"), ("down", "source_unavailable")])
def test_a_changed_or_unreadable_receipt_is_refused(seed, db_session, run, portfolio_stub, change, code):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    CHANGES[change](portfolio_stub)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == code and _booked(db_session) == 0


def test_a_fingerprint_outside_the_candidates_is_refused(seed, db_session, run, portfolio_stub):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:8"}))
    assert _code(exc) == "not_a_candidate"


def test_another_event_holding_the_receipt_is_already_booked(seed, db_session, run, portfolio_stub):
    bank, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    other = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 10, 1), "990")], period=OCT)
    (plain,) = _cases(db_session, other, "open")
    forced = ReconciliationCase(statement_id=other, revision_id=plain.revision_id, event_id=plain.event_id,  # a stale listing
                                line_id=plain.line_id, kind="dividend", status="open", candidates=case.candidates,
                                context=case.context)
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "already_booked" and _booked(db_session) == 1


def test_a_second_listing_of_the_booked_event_replays(seed, db_session, run, portfolio_stub):
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    again = ReconciliationCase(statement_id=sid, revision_id=case.revision_id, event_id=case.event_id, line_id=case.line_id,
                               kind="dividend", status="open", candidates=case.candidates, context=case.context)
    db_session.add(again); db_session.commit()
    row, replayed = engine.apply_action(db_session, case_id=again.id, action="book_dividend",
                                        params={"fingerprint": "dividend:7"}, actor="owner", label="owner-test")
    db_session.commit()
    assert (row.id, replayed) == (first.id, True) and db_session.scalar(select(func.count(LedgerEntry.id))) == 1


@pytest.mark.parametrize("kind", ["income", "expense", "missing"])
def test_the_dividend_category_must_be_an_existing_income_category(seed, db_session, run, portfolio_stub, kind):
    category_id = 999999 if kind == "missing" else seed.category("股利收入", kind).id
    db_session.commit()
    block = settings_service.read_settlement_settings(db_session)
    put_settings(db_session, settlement={**{k: v for k, v in block.items() if k in settings_service.SETTLEMENT_DEFAULTS},
                                         "dividend_category_id": category_id})
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    entry = db_session.get(LedgerEntry, db_session.scalar(select(SettlementBooking.in_entry_id)))
    assert (entry.category_id, entry.needs_review) == ((category_id, False) if kind == "income" else (None, True))


def test_book_dividend_takes_the_account_lock_and_barrier():
    assert reconciliation_actions.ACTIONS["book_dividend"].lock_targets(None, None) == LockSet(account_lock=True, barrier=True)


def test_policy_refuses_when_an_owner_income_entry_appears_under_the_barrier(pg_engine, seed, db_session, run, portfolio_stub,
                                                                            policy_on, monkeypatch):
    policy_on()
    bank, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub)
    (cand,) = policy_service.collect_candidates(db_session, db_session.get(AccountStatement, sid),
                                                settings_service.reconciliation_policy(db_session))
    original = policy_service._policy_precheck

    def racing(candidate, ctx):                    # after the precheck, before the account lock and the barrier
        original(candidate, ctx)
        with Session(pg_engine) as writer:
            Seed(writer).entry(writer.get(Account, bank.id), "990", kind="income", day=date(2026, 9, 13))
            writer.commit()

    monkeypatch.setattr(policy_service, "_policy_precheck", racing)
    exc = _refuse(db_session, lambda: policy_service.apply_candidate(db_session, db_session.get(AccountStatement, sid), cand,
                                                                     month=date(2026, 9, 1)))
    assert _code(exc) == "candidate_changed" and _booked(db_session) == 0
```

`settlement_bank` maps no target here (`target=False`): `book_dividend` needs no target account. `submit_bank` defaults to SEP, so the dividend window is 2026-08-29 … 2026-10-03.

Extra imports for the rev 2.1 tests: `pair_locks` and `portfolio_client` from `app.services`, `reconciliation_service as rec`, and `feed`, `settle_line`, `seed_stand_in` from `settlement_fixtures`.

`tests/integration/test_reconciliation_api.py`, in the "settlement re-reconcile triggers" section. It drives the run's flagging step itself (`recheck_targets` → `flag_statements` → `reconcile_each` → policy, `routers/statements.py:313-327`). Import `dividend_line`, `dividend_row` and `policy_on` from `settlement_fixtures`, and `SettlementBooking`.

```python
def test_settlement_run_flags_and_books_a_dividend_confirmed_later(client, seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    bank, _ = settlement_bank(seed, db_session, target=False)
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 12), "990")])     # receipt still pending
    assert tss.recheck_targets(db_session, {}) == {sid}                                        # dividend_service's half
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]                          # confirmed since
    res = client.post("/reconciliation/settlements/run", headers=W, json={"source": "portfolio:cathay"})
    assert res.status_code == 200 and (res.json()["flagged"], res.json()["reconciled"], res.json()["busy"]) == (1, 1, 0)
    assert "990" not in res.text                                                               # counts, ids and codes only
    db_session.expire_all()
    assert db_session.get(AccountStatement, sid).needs_recheck is False
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"                     # the run's policy pass
    assert tss.recheck_targets(db_session, {}) == set()                                        # the line is claimed
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py`
Expected: FAIL. The cases stay `line_unmatched` and `book_dividend` is not registered (`unknown_action`).

- [ ] **Step 7: Implement `dividend_service` reads and outcomes**

  - **Signatures.** `applies`, `window`, `fetch_for`, `read_window`, `assignment_for`, `owner_deleted`, `category_for` and `recheck_statement_ids` follow the Interfaces block.
  - **`recheck_statement_ids` (R11, rev 2.2).** Returns the live, non-retired statements, in ascending order, of accounts whose `settlement_source` is in `tss.SETTLEMENT_SOURCES` and that hold an `open` case of one of these shapes:
    - kind `dividend`;
    - `context["hint"]` in `FAMILY_HINTS` (`dividend_ambiguous`, `dividend_mismatch`);
    - kind `line_unmatched` without a `hint`, on a `deposit` line whose `merchant_norm` contains one of the rules' `dividend_labels`.

    The query has the Phase 1 shape (`trade_settlement_service.py:770-782`) and compares nothing. The statement stays a target while such a case is open, so each run's reconcile re-reads the feed and sees a receipt confirmed, fee-corrected (`amount`), re-dated or re-statused since.
    - `tss.recheck_targets` unions it in through a call-time `from app.services import dividend_service`, so the settlements run and the daily sweep re-check those statements.
  - **`outcomes` inputs.**
    - The plain cases: `kind == "line_unmatched"`, no context, no candidates.
    - The assignment is `assignment_for(db, account.id, all_lines, feed.rows, rules)`, the same function `book_dividend._plan` re-runs under the action locks. A line the owner claimed still takes part, so a receipt near it is never handed to its neighbour.
  - **`outcomes` results.**
    - Feed None: the cases are unchanged; `unavailable` is set and the code is the given one.
    - `tss.currency_mismatch(account, feed.rows)`: treated as unavailable, with code `currency_mismatch`.
    - A unique assignment on a plain case → `Case(line.id, None, "dividend", (facts_of(row),), {"fingerprint", "source": account.settlement_source, "owner_deleted": owner_deleted(db, fp)})`.
    - An ambiguous one → `Case(line.id, None, "ambiguous", tuple(facts_of(r) for r in rows), {"hint": AMBIGUOUS_HINT})`.
    - A `near` entry on a plain case (rev 2.2) → `Case(line.id, None, "line_unmatched", (), {"hint": MISMATCH_HINT, "nearest": nearest_of(line, row)})`. It is never a candidate and never a booking.
    - Every other case is kept unchanged. `counts = {"dividend": n, "dividend_ambiguous": m, "dividend_mismatch": k, UNAVAILABLE_KEY: code}`.
  - **`tss.reservations`.** Filters both selects on the `kind` parameter.
  - **`reconciliation_service`.**
    - `from app.services import dividend_service`.
    - In `reconcile`: `divides = dividend_service.applies(...)` beside `settles`. The cached client opens when `settles or divides`. `dividends, dividend_code = dividend_service.fetch_for(...) if divides else (None, None)` runs right after the settlement read (:1032-1033).
      - The boundary: after `_lock_statement` and `coverage_service.sweep` (:1009-1016), before `auto_pay_service.retire_twins_for_statement` (:1036), the housekeeping and twin retirements (:1037-1042) and `_lock_candidates` (in `_match_and_record`).
      - The statement → sweep order and the NOWAIT booking → entry order (`tss.lock_pair`) stay as they are. No account row lock enters `reconcile`.
      - Extend the comment at :1029-1031 to name both reads.

      Both reads are passed to `_match_and_record(..., divides=divides, dividends=dividends, dividend_code=dividend_code)`.
    - In `_match_and_record`, after the `settles` block: `divided = dividend_service.outcomes(db, statement, account, result.cases, all_lines, rules, dividends, dividend_code)` and `result = replace(result, cases=divided.cases)`. The result's `settlements` merges both counts dicts, and `settlement_source_unavailable` ORs both `unavailable`.
    - `_settlement_family`: True also for `kind == "dividend"` or a hint in `dividend_service.FAMILY_HINTS` (`dividend_ambiguous`, `dividend_mismatch`).
    - `_dismissed`: a want of the dividend family is also hidden by a dismissed `line_unmatched` case without a hint on the same event.
      - A dismissed mismatch is itself family, so it hides the `dividend` case that a fee correction later yields.
      - Do NOT add either hint to `tss.SETTLEMENT_CASE_HINTS`: the stand-in `_dismissed` (`trade_settlement_service.py:925-930`) and the settlement re-check read that tuple.

- [ ] **Step 8: Implement `book_dividend`, the registry, policy and settings**

  - **`_add_booking`.** The savepoint add + flush, with `IntegrityError` mapped through `tss.UNIQUE_CODES` to `CodedConflictError(code, label)`. `BookSettlementAction.apply` now calls it (behaviour unchanged).
  - **`BookDividendAction` contract.** `name`, `Params = BookDividendParams`, `case_kinds`, `actors` and `budget_exempt` as in R4.
  - **`prepare`.**
    1. `super().prepare`.
    2. `_candidate` (422 `not_a_candidate`). On an `ambiguous` case, `ctx.actor != OWNER` is also 422 `not_a_candidate` (rev 2.2: the owner override only, R4).
    3. Without `settlement_source` or without `ctx.line`: `data={"rows": (), "now": None, "read": False}`. `check_before_locks` / `_open_line` refuse later.
    4. Else `rows = dividend_service.read_window(ctx.account, ctx.line.posted_date)`, the action's one HTTP call. `PortfolioUnavailable` → 409 `source_unavailable`.
    5. `data = {"rows": rows, "now": facts_of(the row of the fingerprint) or None, "read": True}`.
  - **`check_before_locks`.**
    - 409 `needs_recheck` while the statement awaits its re-check.
    - 409 `no_source` without `settlement_source`.
    - Then `super().check_before_locks`.
  - **`validate_before`** stores `ctx._settlement_plan = self._plan(...)` → `(line, now)`, in this order:
    1. `_candidate` on the locked case (422).
    2. The `booked` `kind="dividend"` rows of the fingerprint or the line's event, each `tss.lock_pair` (409 `settlement_busy`).
    3. A row of this event whose income entry still actively covers a line of the event → `BookSettlementAction._replay(ctx, row)`.
    4. `_open_line(ctx)`.
    5. A holder of the fingerprint → 409 `already_booked`.
    6. Not `read`, `not same_facts(candidate, now)`, or `Decimal(now["amount"]) != Decimal(line.flow_amount)` → 409 `candidate_changed`.
    7. No source → 409 `no_source`.
    8. **Currency (rev 2.1, Should 3).** `ctx.account` was refreshed by the engine's account lock. Unless `writes.SOURCE_CURRENCIES.get(ctx.account.settlement_source) == ctx.account.currency == ctx.statement.currency == now["currency"]` → 409 `tss.CURRENCY_MISMATCH`, as `BookSettlementAction._plan` does at `settlement_actions.py:148-153`.
    9. **Assignment (rev 2.1, Must 2).** `found = dividend_service.assignment_for(db, ctx.statement.account_id, reconciliation_service._current_lines(db, ctx.statement), prepared.data["rows"], ctx.rules)` (a local import of the reconciler, #247). It is read after step 2's locks, against the current lines and reservations.
       - For a `dividend` case, `found.unique.get(line.id)` must carry `params.fingerprint`.
       - For an `ambiguous` case (the owner override, owner ruling 2026-10-31: the policy never collects one, and `prepare` refused any other actor), the fingerprint must still be among `found.unique.get(line.id)` / `found.ambiguous.get(line.id, ())`.
       - Else 409 `candidate_changed`: a second equal receipt appeared in the window, or the receipt now also fits another line.
       - The policy applies through `engine.apply_action` and so through this same `_plan` (`policy_service.apply_candidate` → the engine). It inherits the check, and a refusal is a `candidate_changed` skip (`policy_service.py:789-796`).
    10. `self._twin_check(ctx, line)`; then, for a guarded actor, `tss.find_twins(account_id=statement account, target_account_id=None, amount=Decimal(line.flow_amount), settlement_date=line.posted_date, window_days=tss.twin_window_days(db))` → 409 with `_guard_code`.
  - **`apply`.**
    - Raise `RuntimeError` without a plan.
    - `category_id, needs_review = dividend_service.category_for(db)`.
    - `entry = self._book(ctx, line, effect, kind="income", category_id=category_id, needs_review=needs_review, name=f"股利 {facts['symbol']}")`. Pass no `description`: `_book` sets it (`ledger_actions.py:111`).
    - Then the R4 row through `_add_booking(db, booking, f"dividend {fingerprint}")`.
    - `effect.inverse = {"created_entry_id": entry.id, "booking_id": booking.id}`.
  - **`revert_lock_targets`.** As `book_settlement` (account lock + barrier).
  - **`invert`.**
    1. `pair_locks.lock_row(..., busy_code=tss.SETTLEMENT_BUSY)`.
    2. A row that is not `booked` → `NotRevertable`.
    3. Copy the detail (`booking_id`, `origin`, `account_id`, `fingerprint`, `settlement_date`, `reason "revert"`, `in_entry_id`).
    4. `effect = super().invert(ctx, action)`.
    5. Re-get the row: `status, reason = "retired", "revert"`; `items.clear()`.
    6. The case → `kind "line_unmatched"`, `candidates []`, `context {}`.
    7. `audit.record(event="dividend_retired", actor=ctx.label, statement_id, case_id, action_id, detail)`.
    8. Return `effect`.
  - **`audit.EVENTS`** += `"dividend_retired"`.
  - **Registry.**
    - `KIND_ACTIONS["dividend"] = ("book_dividend", "match", "dismiss")`.
    - `"ambiguous"` gets `"book_dividend"` after `"book_settlement"`.
    - `CONDITIONS[("ambiguous", "book_dividend")]` = hint `dividend_ambiguous`.
    - `CONDITIONS[("dividend", "book_dividend")]` = no hint.
    - `MatchAction.case_kinds` and `DismissAction.case_kinds` += `"dividend"`.
  - **Policy.**
    - `_dividend_candidate(db, case, statement) -> Candidate | None`: an open `dividend` case without a hint and with a `fingerprint` → `Candidate(case.id, case.kind, case.status, "book_dividend", {"fingerprint": fp}, SETTLEMENT_AMOUNT)`.
    - `POLICY_ACTIONS["book_dividend"]`.
    - `collect_candidates` kinds += `"dividend"`.
    - `_unchanged`: `book_dividend` is still unhinted, with the collected fingerprint (the `book_settlement` branch's shape).
  - **Settings.** `POLICY_DEFAULTS["actions"]["book_dividend"] = {"enabled": True, "max_amount": "0"}`, with the comment "spec §4.6 / §8: on by default, zero-charged and cap-exempt (`CAP_EXEMPT`)".
  - **README** (accounting, after the R7 Phase 1 section). A "Dividends" paragraph:
    - the matching rule (labels in `rules.dividend_labels`; confirmed receipts; ±3 days; exact net amount);
    - the outcomes:
      - `dividend`;
      - `ambiguous` + `dividend_ambiguous`: the owner may pick one receipt (owner override), and the policy never does;
      - `line_unmatched` + `dividend_mismatch`, with the nearest receipt and its signed delta: fix the receipt's fee in the portfolio, or dismiss;
      - the ordinary case;
    - the one feed read and how unavailability is reported;
    - `book_dividend` (income entry `股利 <symbol>`, category from `settlement.dividend_category_id`, else `needs_review`; cap-exempt; revert → `line_unmatched`);
    - the re-check through `POST /reconciliation/settlements/run` and the daily sweep. Every statement holding an open dividend-family or plain dividend-line case is re-reconciled on each run, so a receipt confirmed or corrected in the portfolio is seen by the next run.

- [ ] **Step 9: Run the task's tests and the Phase 1 neighbours to verify they pass**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py tests/unit/test_dividend_assign.py tests/unit/test_inbox_service.py tests/unit/test_import_order.py tests/unit/test_portfolio_client.py tests/integration/test_settlement_actions.py tests/integration/test_settlement_reconcile.py tests/integration/test_reconciliation_policy.py tests/integration/test_statement_ingest_api.py tests/integration/test_reconciliation_api.py`
Expected: all passed. `test_statement_ingest_api.py:503` compares the policy to `POLICY_DEFAULTS` itself.

- [ ] **Step 10: Commit**

```bash
git add services/accounting-service/app services/accounting-service/tests services/accounting-service/README.md
git commit -m "accounting: book_dividend — confirmed 國泰 dividend receipts booked as income on the bank line, cap-exempt

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 14: Accounting — owner wins over a booked dividend

**Files:**
- Modify: `services/accounting-service/app/services/dividend_service.py` (`housekeep`, `retire_twin`, `retire_twins_for_statement`)
- Modify: `app/services/reconciliation_service.py:1036-1042` (an `if divides:` block after the `settles` one)
- Modify: `app/services/stand_ins.py:23` (`OWNER_TWIN_GUARDED` += `"book_dividend"`)
- Modify: `app/services/policy_service.py:367-371` (`OWNER_DELETED_GUARDED`, `PROPOSED_WHEN_OWNER_DELETED` += `"book_dividend"`)
- Modify: `app/services/reconciliation_actions/audit.py:9-21` (`"dividend_owner_deleted"`, `"dividend_owner_edited"`)
- Modify: `app/services/coverage_service.py:559-560` (`RETIRABLE_ACTIONS` += `"book_dividend"`). Rev 2.1 found this gap:
  - Without it, the sweep's `_retire_creations` (:570-600) never retires a `book_dividend` action whose entry the owner deleted.
  - The event then never counts as owner-deleted for `stand_ins.owner_deleted_events`.
  - The `create` slot also stays taken, so the owner's answer to the policy's proposal is refused 422 `duplicate_effect` (`engine.py:319-328`).
- Modify: `services/accounting-service/README.md` (the "Dividends" paragraph: owner wins) and `services/accounting-service/deploy/statements/README.md` (a "Dividends rollout" section after the R7 Phase 1 rollout, ~:540)
- Test: `tests/integration/test_dividend_actions.py` (+5)

**Interfaces:**
- Consumes: Task 13 `KIND`, `owner_deleted`, `dividend_service` wiring in `reconcile`; `tss.lock_pair`, `tss.find_twins`, `tss.twin_window_days`, `tss.ACTOR`, `tss.SETTLEMENT_BUSY`, `coverage_service.release_entries`, `entry_write_service.delete_entries_cascade`, `matching.population_window_days`.
- Produces:
  - `dividend_service.housekeep(db, account_id: int) -> dict[str, int]` with keys `owner_deleted`, `owner_edited` and `settlement_busy`.
  - `dividend_service.retire_twin(db, row: SettlementBooking) -> set[int] | None` (the released statement ids, or None when the locked re-check fails).
  - `dividend_service.retire_twins_for_statement(db, statement, accounts: list[int]) -> list[int]` (the retired row ids).
  - All three flush and never commit.

- [ ] **Step 1: Write the failing tests** (append to `tests/integration/test_dividend_actions.py`; `client` is the conftest fixture, `_put_body` from `tests.integration.test_stand_ins`)

```python
def test_an_owner_delete_tombstones_the_booking_and_the_policy_proposes(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    ews.delete_entry(db_session, booking.in_entry_id); db_session.commit()
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    row = _fresh(db_session, SettlementBooking, booking.id)
    assert (row.status, row.items) == ("owner_deleted", [])
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.context["owner_deleted"], case.status) == ("dividend", True, "proposed")
    assert _booked(db_session) == 0
    assert db_session.scalar(select(func.count(ReconciliationAudit.id)).where(
        ReconciliationAudit.event == "dividend_owner_deleted")) == 1


def test_the_owner_may_book_the_proposal_after_an_owner_delete(seed, db_session, run, portfolio_stub, policy_on):
    """The sweep retires the deleted entry's `book_dividend` action (`RETIRABLE_ACTIONS`), so the create slot is free
    and the owner's answer to the policy's proposal books the receipt again (the Phase 1 settlement test's shape)."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.in_entry_id))); db_session.commit()
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)
    assert _fresh(db_session, ReconciliationAction, first.id).status == "reverted"
    (proposal,) = db_session.scalars(select(ReconciliationProposal).where(ReconciliationProposal.status == "pending"))
    assert (proposal.author, proposal.action) == ("policy", "book_dividend")
    proposal_service.apply_proposal(db_session, proposal_id=proposal.id, label="owner-test", request_id=None)
    db_session.commit()
    assert sorted(db_session.scalars(select(SettlementBooking.status))) == ["booked", "owner_deleted"]


@pytest.mark.parametrize("edit,status", [("category", "booked"), ("amount", "owner_edited")])
def test_an_owner_edit_tombstones_the_booking_only_when_structural(seed, db_session, run, portfolio_stub, client, policy_on,
                                                                  edit, status):
    """Owner ruling 2026-10-31, Phase 1 parity (`trade_settlement_service.py:312-318`): a structural edit tombstones the
    row `owner_edited` and KEEPS its item, so the receipt stays reserved. Neither the next reconcile nor the policy
    opens or proposes anything for it, and nothing is booked."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    entry = db_session.get(LedgerEntry, booking.in_entry_id)
    # 500, far from 990: the line is unmatched again, yet the kept item still reserves the receipt
    fields = {"category_id": seed.category("股利收入", "income").id} if edit == "category" else {"amount": "500"}
    db_session.commit()
    assert client.put(f"/entries/{entry.id}", json=_put_body(entry, **fields)).status_code == 200
    for _ in range(2):                                                               # the next runs, too
        _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
        row = _fresh(db_session, SettlementBooking, booking.id)
        assert row.status == status and [(i.source, i.source_id) for i in row.items] == [("dividend", "dividend:7")]
        assert tss.reservations(db_session, kind="dividend") == (
            ({"dividend:7"}, {"dividend:7"}) if status == "booked" else (set(), {"dividend:7"}))
        assert _booked(db_session) == (1 if status == "booked" else 0)
        assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1
        assert [c.id for c in _cases(db_session, sid, "open", "proposed")
                if c.kind == "dividend" or (c.context or {}).get("hint") in ds.FAMILY_HINTS] == []
        assert db_session.scalar(select(func.count(ReconciliationProposal.id))) == 0
    assert db_session.scalar(select(func.count(ReconciliationAudit.id)).where(
        ReconciliationAudit.event == "dividend_owner_edited")) == (1 if status == "owner_edited" else 0)


def test_dividend_housekeeping_locks_come_after_the_dividend_read(seed, db_session, run, portfolio_stub, monkeypatch):
    """Rev 2.1 Should 2: `dividend_service.housekeep` locks the owner-deleted booking (row → entry NOWAIT) only after
    the reconcile's one dividend read."""
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.in_entry_id))); db_session.commit()
    before, seen = len(portfolio_stub.dividend_calls), []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            seen.append((name, len(portfolio_stub.dividend_calls)))
            return real(*args, **kwargs)
        return wrapped

    for module, name in ((pair_locks, "lock_row"), (pair_locks, "lock_legs"), (rec, "_lock_candidates")):
        monkeypatch.setattr(module, name, spy(name, getattr(module, name)))
    _reconcile(db_session, sid)
    assert {"lock_row", "_lock_candidates"} <= {name for name, _ in seen}
    assert {calls for _, calls in seen} == {before + 1}


def test_an_owner_income_entry_arriving_later_retires_the_booking(seed, db_session, run, portfolio_stub):
    bank, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    system_id = booking.in_entry_id
    owner = seed.entry(bank, "990", kind="income", day=date(2026, 9, 13)); db_session.commit()
    _reconcile(db_session, sid)
    row = _fresh(db_session, SettlementBooking, booking.id)
    assert (row.status, row.reason, row.items) == ("retired", "twin", []) and db_session.get(LedgerEntry, system_id) is None
    assert [c.entry_id for c in _active(db_session, sid)] == [owner.id] and _cases(db_session, sid, "open") == []
    assert db_session.scalars(select(ReconciliationAudit.detail).where(
        ReconciliationAudit.event == "dividend_retired")).one()["reason"] == "twin"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py -k "owner_delete or owner_edit or arriving_later or housekeeping_locks"` (the `-k` pattern also selects `test_the_owner_may_book_the_proposal_after_an_owner_delete`)
Expected: FAIL. The rows stay `booked`, the twin keeps both income entries, and no `lock_row` is seen.

- [ ] **Step 3: Implement**

  - **`housekeep`.** Walks the account's `booked` `kind="dividend"` rows (refreshed, id order) and skips each whose income entry is present with `source == "statement"`.
    - Every other row is locked through `tss.lock_pair` and re-checked under the lock. A busy row (`settlement_busy`) is logged as `dividend.housekeep_busy` with the id, counted, and keeps reserving.
    - Entry gone → `owner_deleted`, and `row.items.clear()` (the reservation goes with it).
    - Present but no longer `statement` (a structural owner edit flips it to `manual`, `entry_write_service.py:352-371`) → `owner_edited`, items kept: the edited entry still records the receipt. This is Phase 1 parity, `trade_settlement_service.py:312-318`, under owner ruling 2026-10-31.
    - Audit `dividend_owner_deleted` / `dividend_owner_edited`, `actor=tss.ACTOR`, with `{booking_id, account_id, fingerprint, settlement_date, in_entry_id}`.
  - **`retire_twins_for_statement`.**
    - Takes the `booked` dividend rows on `accounts` whose `settlement_date` lies in the period ± `matching.population_window_days(rules_for(...))` (local import of `rules_for`, as `tss` does), with the income entry still the system's.
    - A row is retired when `tss.find_twins(account_id=row.account_id, target_account_id=None, amount=entry.amount, settlement_date=entry.entry_date, window_days=tss.twin_window_days(db), exclude=[entry.id])` finds an owner entry → `retire_twin(db, row)`.
    - A `settlement_busy` row is logged `dividend.sweep_retire_busy` and left for the next run.
  - **`retire_twin`.** Mirrors `tss.retire_pair(reason="twin")` (`trade_settlement_service.py:326-371`) for one leg:
    1. In one savepoint: `lock_pair`, then re-check under the locks (still `booked`, entry present and `statement`, the twin still found).
    2. `coverage_service.release_entries(db, [entry.id], reason="dividend:twin")`.
    3. `delete_entries_cascade`.
    4. The row `retired` / `twin`, items cleared.
    5. `needs_recheck` on the released statements.
    6. Audit `dividend_retired` with `reason "twin"` and `released_statement_ids`.
  - **`reconcile`.** After the `settles` block: `if divides:` → `dividend_service.housekeep(db, statement.account_id)` then `dividend_service.retire_twins_for_statement(db, statement, accounts)`. Both run before the population is read; the comment cites spec §4.7.
  - **Guards.** `stand_ins.OWNER_TWIN_GUARDED`, `policy_service.OWNER_DELETED_GUARDED`, `PROPOSED_WHEN_OWNER_DELETED` and `coverage_service.RETIRABLE_ACTIONS` += `"book_dividend"`. Audit `EVENTS` += the two owner events.
  - **README** (accounting, "Dividends" paragraph):
    - owner delete → `owner_deleted`: the receipt is freed, and the policy then proposes it but never books it;
    - structural edit → `owner_edited`: the receipt stays reserved and is never offered again;
    - a cosmetic edit keeps the booking;
    - an owner income twin retires it.
  - **`deploy/statements/README.md` "Dividends rollout":** the Rollout section below, verbatim.

- [ ] **Step 4: Run the whole dividend and settlement suite to verify it passes**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py tests/integration/test_settlement_actions.py tests/integration/test_settlement_reconcile.py tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py tests/unit/test_import_order.py`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add services/accounting-service/app services/accounting-service/tests services/accounting-service/README.md \
  services/accounting-service/deploy/statements/README.md
git commit -m "accounting: owner wins over a booked dividend — housekeeping, twin retirement, owner-delete proposals

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Rollout (proposed; the owner confirms each step)

1. **Before the accounting deploy:**
   - Put the dividend category in place: GET the settings document, set `settlement.dividend_category_id` to an income category, and PUT it back whole. The current release already accepts that key.
   - Set `policy.enabled = false`. The current release refuses a `book_dividend` key (`settings_service._check_policy`), so the new default `enabled: true` cannot be pre-disabled per action.
2. **Deploy** the portfolio release (no migration), then the accounting API (no migration).
3. **Settings PUT** (whole document): `policy.enabled = true`, `policy.actions.book_dividend.enabled = false`. GET it back and check.
4. **Re-reconcile the settlement-source account's live statements** (67 in the settings example). The dividend cases appear without bookings, and their counts are in each answer's `settlements`. A `dividend_mismatch` line names its nearest receipt and the signed delta: the owner corrects that receipt's fee in the portfolio, and the next settlements run picks it up (R11), or the owner dismisses the line. The owner reviews them one case per question. `r6_case_dump.py` does not list dividend outcomes; the cases themselves are the review list.
5. **Enable** `book_dividend.enabled = true` and run the policy on those statements.

**Rollback:**
- The `dividend` case kind already exists, so an accounting release below this one can still read the cases.
- To disable, set `book_dividend.enabled = false`. Booked rows stay, and each can be reverted from its action.

## Follow-ups (issues to file at merge)

- **SPA:**
  - `CASE_KIND_LABELS` (`frontend/src/app/components/accounting/statements/statement-view.ts:82-92`) lacks `dividend` (proposed 「股利入帳」) and `settlement` (「交割款」); both fall back to the raw kind.
  - An inbox card for `dividend` / `dividend_ambiguous` / `dividend_mismatch`. Until it exists they sit in the folded `other` bucket and the owner answers through the API.
- **Dividend `source_drift`:** spec §4.6 says "same idempotency … as §4.4", and §4.4 item 4 (drift of a booked row's facts) is not in R1–R11. A booked dividend whose receipt later changes is left alone. The R11 re-check covers open cases only.
- **Settlement-family ambiguous cases offer `adjust_amount`:** that action has no `CONDITIONS` entry (`reconciliation_actions/__init__.py:29, 128-150`), so settlement- and dividend-ambiguous cases offer it although their candidates are not entries.
- **`dividend_category_id` validation:** `_check_settlement` accepts any integer (`settings_service.py:401-402`); a PUT-time income-category check would surface a wrong id earlier than `category_for` does.
