# Portfolio integration — dividends follow-up (Tasks 12–13) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirmed 國泰 dividend receipts in the portfolio are served through the ledger feed and booked as income on the matching bank deposit line of account 67, cap-exempt and revertable, under the Phase 1 booking owner.

**Architecture:** The stock-portfolio-service adds `GET /api/portfolio/ledger-feed/dividends` next to the Phase 1 feed; the accounting-service matches a dividend-labelled deposit line to exactly one unreserved confirmed receipt and books it through a `book_dividend` action on the Phase 1 `settlement_booking` tables (`kind = dividend`).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic (both services), httpx 0.28.1 (accounting → portfolio, worker → accounting), pytest (accounting: `tests/unit`, `tests/integration` on PostgreSQL, `tests/worker`; portfolio: `tests/unit`, `tests/integration` on SQLite `create_all`), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` rev 4.2, §4.6 (owner ruling Q6 (a) in `.superpowers/r7-refresh/owner-rulings.md`).

**Parent plan:** `docs/superpowers/plans/2026-10-19-portfolio-integration-phase1.md` rev 4.2 — Tasks 1–11 must be merged first; this plan consumes its Task 3 tables, Task 7 `CAP_EXEMPT` / booking helpers / `settlement.dividend_category_id`, and the Task 2 feed module. Moved out of the Phase 1 plan on 2026-10-30 after Multica review AGENT-360 (a separate booking owner path with its own recovery semantics).

**Plan rev 1 (2026-10-30):** split from Phase 1 plan rev 4.1 without changes to the task contents; task numbers kept so Phase 1 cross-references (`Task 12 Dividend`, `Task 13`) still resolve.

## Global Constraints

- Owner ruling Q6 (a): a receipt is matched only when `receipt_status == "confirmed"`, the receipt date is within ±3 days of the bank line's posted date, and the receipt's net amount equals the line's flow exactly; several candidates → an ambiguous case, never a booking.
- The dividend feed is bounded by receipt date (`from` / `to`), not by the two-axis settlement query of Phase 1 Task 2 — receipts carry one date.
- Fictional amounts only in tests and docs; no owner data.
- Same test commands, trailer and review flow as the Phase 1 plan.

---


## Task 12: Portfolio `GET /api/portfolio/ledger-feed/dividends` + client method

**Files:**
- Modify: `services/stock-portfolio-service/app/services/ledger_feed_service.py`, `app/routers/ledger_feed.py`, `app/schemas/ledger_feed.py`
- Modify: `services/accounting-service/app/services/portfolio_client.py`
- Test: `services/stock-portfolio-service/tests/integration/test_ledger_feed.py` (+2), `services/accounting-service/tests/unit/test_portfolio_client.py` (+1)

**Interfaces:**
- Produces: `ledger_feed_service.dividends(db, broker: str, date_from: date, date_to: date) -> list[dict]` — `dividends` rows with `receipt_status == "confirmed"`, `receipt_account_id` = `resolve_default_cathay_twd_account(db).id`, `receipt_date` in range: `{"id", "symbol", "receipt_date", "amount" (net, as stored), "currency", "fingerprint": "dividend:<id>"}`; client `Dividend(id: int, symbol: str, receipt_date: date, amount: Decimal, currency: str, fingerprint: str)` and `PortfolioClient.dividends(broker, date_from, date_to) -> list[Dividend]`.

- [ ] **Step 1: Failing tests** — `test_dividends_lists_confirmed_receipts_on_the_cathay_account_net` (a confirmed receipt with `amount 990`, `fee 10`: `amount == "990.0000"`, never `980`; a `pending` row and a receipt on another account are absent); `test_dividends_range_bounds_the_receipt_date`; client parse test.
- [ ] **Step 2: Run** `cd services/stock-portfolio-service && .venv/bin/python -m pytest -q tests/integration/test_ledger_feed.py -k dividends` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** both services' touched test files → pass
- [ ] **Step 5: Commit** `git commit -m "portfolio+accounting: dividend receipts in the ledger feed"`

## Task 13: Accounting — `dividend` candidates and `book_dividend`

**Files:**
- Modify: `services/accounting-service/app/services/statements/matching.py` (`Rules.dividend_labels: tuple[str, ...] = ("股利", "股息", "現金股利")`)
- Modify: `services/accounting-service/app/services/reconciliation_service.py` (after the settlement step: an unclaimed `deposit` line with a dividend label and exactly one unreserved receipt with `amount == line.flow` and `|receipt_date − posted_date| <= 3` → `dividend` case; several → `ambiguous` + `dividend_ambiguous`; none → the line's ordinary case)
- Modify: `services/accounting-service/app/services/reconciliation_actions/settlement_actions.py` (`BookDividendAction(_TwinGuarded)`, `name = "book_dividend"`), `reconciliation_actions/__init__.py` (`KIND_ACTIONS["dividend"] = ("book_dividend", "match", "dismiss")`), `stand_ins.py` (`OWNER_TWIN_GUARDED` += `book_dividend`), `policy_service.py` (`_dividend_candidate`, `POLICY_ACTIONS`, `OWNER_DELETED_GUARDED`, `collect_candidates` kinds += `dividend`), `settings_service.py` (`POLICY_DEFAULTS["actions"]["book_dividend"] = {"enabled": True, "max_amount": "0"}`)
- Test: `services/accounting-service/tests/integration/test_dividend_actions.py`

**Interfaces:**
- Consumes: Task 12 `Dividend`; Task 7 `CAP_EXEMPT`, booking helpers; `settlement.dividend_category_id` (Task 7 `SETTLEMENT_DEFAULTS`).
- Produces: `book_dividend` books one `income` entry on the bank account through `_Creation._book` (category = `dividend_category_id`, `needs_review = True` when it is None), a `settlement_booking` row (`kind dividend`, `origin bank`, `target_account_id None`, `in_entry_id None`, fingerprint `dividend:<id>`) and one item (`div`, `<id>`); revert = `_Creation.invert` + the row `retired` with its items deleted.

- [ ] **Step 1: Failing tests** — `test_book_dividend_books_income_idempotently_and_reverts` (apply → one income entry of the line's flow, row + item; reconcile again → no second case; revert → entry gone, row `retired`); `test_dividend_amount_must_equal_the_line_exactly` (a receipt 1 TWD off → no `dividend` case); `test_owner_income_entry_claims_first`; `test_policy_books_dividends_cap_exempt`.
- [ ] **Step 2: Run** `cd services/accounting-service && .venv/bin/python -m pytest -q tests/integration/test_dividend_actions.py` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** `tests/integration/test_dividend_actions.py tests/integration/test_settlement_actions.py tests/integration/test_reconciliation_policy.py` → pass
- [ ] **Step 5: Commit** `git commit -m "accounting: book_dividend — confirmed 國泰 dividend receipts booked as income on the bank line, cap-exempt"`
