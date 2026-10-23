# Editable TW dividend 匯費 / 補充保費 — Implementation Plan (rev 2.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rev 2 (AGENT-219, dev-grok, CHANGES REQUIRED at 7c5e70f):** Musts folded — the leg edit is an amount-only update (never via `sync_dividend_cash_leg`'s desired-dict); the non-auto-leg 409 gate runs before any write for both nets; the dividend + leg change commits in one transaction before the snapshot refresh (`today = _today_tw()`); the route maps 404 / 422 / 409 explicitly; confirmed rows get a 修改扣款-only menu and focus returns to that row's `button.row-action`. Shoulds — no user-facing "account mismatch" 409 (a leg on another account keeps its account), fingerprints compared via `compute_backfill_fingerprint`, no leg on a confirmed row → 409 (never insert), two legs → 409 before any write, 2-dp money (`decimal_places=2`, `step="0.01"`, quantize before the sign check), no `CASH_LEG_ENABLED` read, the receipt stock transaction stays, pending USD rows keep today's menu, ordering: **merge before redesign v2 PR 5**. Nits folded.

**Goal:** Let the owner change a TW dividend's 匯費 (`fee`) and 補充保費 (`tax`) — including after 確認收款 — with the net amount recomputed and the posted cash leg kept in step, from the 股利 page.

**Architecture:** One new write path `PATCH /api/portfolio/dividends/{id}/deductions` (service `dividend_deduction_service.update_deductions`): row lock + compare-and-swap on `revision`, gross recomputed from per-share × quantity (else stored net + old deductions), and an amount-only update of the single auto-derived cash leg (or its deletion when the net is 0), committed together, then the balance snapshots refreshed. The dividends page gains a ⋯ → 修改扣款 action that opens an inline form under the row (same look and one-open rule as the receipt form). No schema change, no migration, defaults untouched.

**Tech Stack:** FastAPI + SQLAlchemy service (`cd services/stock-portfolio-service && pytest tests/unit -q`), Angular 21 standalone / signals (`cd frontend && npx ng test --watch=false --include <spec>`).

**Spec:** Owner requirement 2026-10-23 (a resolved dividend was not charged the 10 TWD handling fee and could not be edited after confirmation) + Multica AGENT-218 §2 and AGENT-219 invariants. Code: `DEFAULT_HANDLING_FEE = 10` (`app/services/dividend_auto_record_service.py:37`), `persist_entitlement` (`app/services/dividend_receipt_service.py:59-65`; `MIN_DIVIDEND_AMOUNT` floor applies there only), `update_dividend` guard (`app/services/portfolio_service.py:1691-1707`, not extended), `confirm_receipt` CAS + commit (`dividend_receipt_service.py:153-215`, commit at `:211`), `sync_dividend_cash_leg` (`app/services/cash_account_service.py:350-399`: lookup by `related_dividend_id` AND fingerprint `:369-374`, insert on miss `:375-387`, desired dict writes `amount`/`txn_date`/`currency`/`source` `:390-398`), `compute_backfill_fingerprint("dividends", id, "dividend_cash")` (`:105-106`, sha256 stored), `delete_auto_derived_dividend_cash_leg` (`:409-416`, AUTO_DERIVE only), `_refresh_snapshot_cash_range` (`:549-570`, swallows errors after a rollback) called after commit by manual create (`:540-545`) with `today = portfolio_snapshot_service._today_tw()`; `get_db` does not commit (`services/shared-python-lib/shared_lib/database.py:58-64`); `ck_dividends_amount_positive` = `amount > 0 OR (amount = 0 AND stock_dividend_shares > 0)` (`app/models/portfolio.py:105`), fee/tax `>= 0` (`:106-107`), `Numeric(12, 2)` (`:133-134`); `CashTxnSource` = MANUAL / CSV_IMPORT / AUTO_DERIVE (`app/models/cash_transaction.py:39-42`), `related_dividend_id` not unique (`:76-79`), `import_fingerprint` unique (`:47-48`); route error mapping to copy for 409 (`app/routers/portfolio.py:156-166`), `ReceiptConfirmation` (`app/schemas/portfolio.py:160-163`), `DividendCreate` money fields `ge=0, decimal_places=2` (`:151-152`); dividends page: menu guard (`frontend/src/app/components/portfolio/dividend-list/dividend-list.ts:485-492`), `receiptRow` keeps pending only (`:152-156`), focus helper targets `.c-actions app-btn button` (`:217-218`), `openReceipt` no-ops while busy (`:163`), the receipt form closes on filter/sort/status/page/clear (`:362, 416, 427, 442`), `.receipt-fields` compact stack (`dividend-list.scss:395-408`), ⋯ button `button.row-action` with `data-row-id`.

## Decisions

1. **Default unchanged.** New auto entitlements keep fee 10; "optional" means a row's fee may later be set to 0 or any non-negative 2-dp value.
2. **Endpoint:** `PATCH /dividends/{dividend_id}/deductions`, body `DeductionsUpdate { fee: Decimal = Field(ge=0, decimal_places=2), tax: Decimal = Field(ge=0, decimal_places=2), revision: int = Field(ge=1) }` beside `ReceiptConfirmation`; response `schemas.Dividend`. Route mapping: `DividendNotFound` → 404 `dividend not found`; `DeductionError` → 422 with `detail` = the message; `ReceiptConflict` → rollback + 409 `str(exc)`; `OperationalError` → rollback + 409 `Concurrent edit or database contention; reload before retry`; `IntegrityError` → rollback + 409 `deductions conflict with the ledger; reload` (its own text, and the 422 checks fire first so it is a backstop).
3. **Amount rule:** `gross = quantize(quantity_at_record_date × cash_dividend_per_share, 0.01, ROUND_HALF_UP)` when both present and per-share > 0, else `gross = amount + fee + tax` from the stored row; `new_amount = quantize(gross − new_fee − new_tax, 0.01, ROUND_HALF_UP)`; no `MIN_DIVIDEND_AMOUNT` floor; `new_amount < 0` → `DeductionError('deductions exceed the gross dividend')`; `new_amount == 0 and stock_dividend_shares == 0` → `DeductionError('deductions leave no payable amount')`; `== 0` with stock shares is legal (the check constraint allows it). The client never sends `amount`. A repeat call with the same values is a no-op on `amount`/leg (revision may still bump).
4. **Order of operations (one transaction):** (a) `SELECT … FOR UPDATE` the dividend; 404 if missing; 409 `deductions editable for TW/TWD only` unless `market == 'TW' and currency == 'TWD'`; 409 `legacy or unresolved dividend requires explicit resolution` unless status in {pending, confirmed}; (b) compute `new_amount` (422 cases above); (c) **leg gate before any write, for both nets, confirmed rows only:** load every `CashTransaction` with `related_dividend_id == id` — 0 rows → 409 `no posted cash leg for this receipt; reload`; 2+ rows → 409 `multiple cash legs for this dividend; manual review required`; the one row with `source != AUTO_DERIVE` → 409 `cash leg was imported or entered by hand; edit the cash transaction instead`; (d) CAS `UPDATE dividends SET fee, tax, amount, revision = revision + 1 WHERE id = :id AND revision = :client`, rowcount ≠ 1 → rollback, 409 `entitlement changed; reload before editing`; (e) confirmed: `new_amount > 0` → set **only** `leg.amount = new_amount` (never `txn_date`, `source`, `account_id`, `import_fingerprint`; whatever the fingerprint or the account — a leg on another account stays there; `sync_dividend_cash_leg` is **not** called because its desired dict would move `txn_date`/`source` and its miss path would insert); `new_amount == 0` → capture `leg.txn_date`, delete that AUTO_DERIVE leg (the receipt stock transaction, if any, stays); (f) `db.commit()`; (g) confirmed only: `_refresh_snapshot_cash_range(db, min(leg_date, today), today)` with `today = _today_tw()` — after the commit, so a refresh failure (which rolls back and is swallowed inside the helper) never loses the money write; (h) `db.refresh(row)` and return it. Pending rows: steps a, b, d, f, h only (no leg, no refresh). Identity columns (symbol, market, ex-date, quantity, per-share, receipt date/account/status, stock shares) never change. No `CASH_LEG_ENABLED` read.
5. **Totals** follow `Dividend.amount` automatically (dividend totals, pending projection, XIRR cashflows: `portfolio_service.py:651-663, 879, 957-967`); realized PnL untouched; the cash page's running balance follows the leg on the next fetch.
6. **UI (dividends page):** `showMenu`: pending TW/TWD row → 編輯, 修改扣款, separator, 刪除; confirmed TW/TWD row with `amount > 0` → 修改扣款 only (no toast); a confirmed row with net 0 has no cash leg (the endpoint would always 409), so it gets today's info toast — zero net is one-way for a confirmed row (its leg is deleted and not recreated); pending foreign row → today's 編輯 / 刪除 (no toast); legacy / unresolved rows → today's info toast. 修改扣款 opens an inline form under the row (`openDeductionsId` signal; `openReceiptId` and `openDeductionsId` are never both set — opening either closes the other; both close on filter/sort/status/page/clear; both opens no-op while `receiptBusy()` or `deductionsBusy()`): title `修改扣款 · {name} {amount}`, fields 匯費 and 補充保費 (`<input type="number" min="0" step="0.01">`, prefilled from the row), note `淨額會重新計算` (+ `，現金帳的股利入帳也會一併更新` on confirmed rows), buttons 取消 / 儲存 (`app-btn`, 儲存 disabled while busy or when a field is empty or negative); the row lookup for the form keeps confirmed rows (not `receiptRow`); submit `updateDividendDeductions(id, { fee, tax, revision })` with the revision captured when the form opened (`row.revision ?? 1`), so a reload under the open form gets the server's 409 instead of applying old values to a newer row; 編輯 / 刪除 from the menu close both forms; success → close + `reload()` (both sets); error → close + reload + toast `扣款更新未完成` with the server `message` when it is a string, else `detail` when it is a string, else `請重新載入後核對` (error bodies are `{code, message, trace_id}`; the 收款確認 toast is fixed the same way). Focus: open → the 匯費 input; close (取消 / settled submit) → that row's `button.row-action[data-row-id]` (new helper beside the receipt one; the receipt helper's target stays). Markup reuses `.receipt-form` / `.receipt-fields` classes (compact stack on phone for free) with a `.deductions-form` marker class.
7. **Copy:** 修改扣款, 匯費, 補充保費, 取消, 儲存, 淨額會重新計算, `，現金帳的股利入帳也會一併更新`, 扣款更新未完成, 請重新載入後核對.
8. **Scheduler / reconciliation must not undo an edit (rev 2.1, Task 1 review).** `persist_entitlement` (`dividend_receipt_service.py:59-110`) recomputes `fee = DEFAULT_HANDLING_FEE` and `tax = compute_nhi_surtax(gross)` on every run and is called by the daily auto-record job (`scheduler.py:178`) and the reconciliation job (`dividend_reconciliation_service.py:146`): a pending `auto:` row with an edited fee would be reverted to 10 and re-amounted (`:106-109`), and an edited confirmed row would get `source_correction` + `review_reason` on every run (`:87-92`). Ruling: **once a dividend row exists, its `fee` and `tax` belong to the owner.** `persist_entitlement` keeps the defaults for a brand-new row only; for an existing row it (i) builds `values` with `fee = row.fee`, `tax = row.tax`, `amount = max(0, gross − row.fee − row.tax)` (quantized, no `MIN_DIVIDEND_AMOUNT` floor when the row has stock shares; otherwise the floor as today) and (ii) compares only `cash_dividend_per_share`, `quantity_at_record_date`, `stock_dividend_shares`, `payment_date` / `payment_date_source` in `_changed`. Consequence: an upstream gross change still re-amounts a pending row (with the owner's deductions) and still flags a confirmed row; a deduction edit alone never does. Task 1b implements this with tests on both callers' paths.

## Global Constraints

- No schema/migration; `DEFAULT_HANDLING_FEE`, `update_dividend`, `confirm_receipt`, `sync_dividend_cash_leg` unchanged; `persist_entitlement` changes only per Decision 8.
- **Merge and deploy before redesign v2 PR 5** (that PR rebases its `cash_account_service.py` work); the endpoint is never added to the PR 5 plan.
- Backend tests on the SQLite unit fixtures of `tests/unit/test_dividend_cash_sync.py` (`db_session`); money as `Decimal` with 2 dp.
- Frontend: signals + OnPush; specs restore globals; existing dividend-list tests stay green; copy verbatim.

## Review Focus

1. **Double leg** — a confirmed row whose single leg has a non-backfill fingerprint or sits on another account is updated in place; the count stays 1 and the account stays. → T1.
2. **Race with confirm / stale revision** — nothing written, 409. → T1.
3. **Zero net** — cash-only row → 422 and nothing written (dividend and leg); with stock shares → amount 0, the auto leg deleted, the stock transaction kept; a non-auto leg → 409 on the zero-net path too. → T1.
4. **Commit ordering** — the write survives a failing snapshot refresh; the refresh range is `(min(leg date, today), today)`. → T1.
5. **UI state** — confirmed rows get 修改扣款 only; opening one form closes the other; focus returns to ⋯. → T3.

---

### Task 1: Service `update_deductions` (backend)

**Files:**
- Create: `services/stock-portfolio-service/app/services/dividend_deduction_service.py`, `tests/unit/test_dividend_deduction_service.py`

**Interfaces:**
```py
class DividendNotFound(LookupError): ...
class DeductionError(ValueError): ...     # 422 cases
def update_deductions(db: Session, dividend_id: int, fee: Decimal, tax: Decimal, revision: int) -> Dividend  # Decision 4; ReceiptConflict from dividend_receipt_service for 409 cases
```

- [ ] **Step 1: Failing tests** (fixtures: a TWD account; dividends built like `test_dividend_cash_sync.py`; legs created directly with `CashTransaction(...)`; `_refresh_snapshot_cash_range` and `_today_tw` monkeypatched to record calls):
  - pending auto row (0.48 × 20,000, fee 10, tax 0, amount 9,590, revision 1): fee → 0 gives amount `9600.00`, fee 0, revision 2, no leg, no refresh call, and the row is committed (re-open a session and read it back);
  - confirmed row with one AUTO_DERIVE leg (backfill fingerprint, amount 9,590, `txn_date` = a date earlier than `receipt_date` on purpose): fee → 0 gives leg amount `9600.00`, `txn_date`, `source`, `account_id`, `import_fingerprint` unchanged, exactly one leg, revision bumped, refresh called once with `(leg txn_date, today)` where today is the patched `_today_tw()`;
  - repeat call with the new revision and the same values → amount and leg unchanged, still one leg;
  - confirmed leg with a **different** fingerprint → updated in place, count 1;
  - confirmed leg on **another account** (non-backfill fingerprint) → updated in place, `account_id` unchanged, count 1;
  - confirmed row with no leg → 409 text, nothing written;
  - confirmed row with two legs → 409, nothing written;
  - stale revision → `ReceiptConflict`, dividend and leg unchanged;
  - `legacy_unknown` / `unresolved` → 409; USD row → 409;
  - fee + tax > gross → `DeductionError`, nothing written; fee + tax == gross without stock shares → `DeductionError`, nothing written; with `stock_dividend_shares 100` → amount `0.00`, the auto leg deleted, the receipt stock `Transaction` still present, refresh called with the deleted leg's date;
  - row without per-share data → gross from `amount + fee + tax`;
  - confirmed leg with `source csv_import`: positive-net edit → 409 and nothing written; zero-net edit → 409 and nothing written (leg kept);
  - a patched `_refresh_snapshot_cash_range` that raises → the new amount and leg amount are still committed;
  - `fee 1.005` → quantized `1.01` on the row (or rejected by the schema in Task 2 — the service quantizes anyway);
  - missing id → `DividendNotFound`.
- [ ] **Step 2–4:** run → FAIL (`pytest tests/unit/test_dividend_deduction_service.py -q`); implement; `pytest tests/unit -q` → PASS.
- [ ] **Step 5: Commit** `feat(api): update_deductions for TW dividend fee/tax with amount-only cash-leg update`

---

### Task 1b: `persist_entitlement` keeps owner-set deductions (backend)

**Files:** `services/stock-portfolio-service/app/services/dividend_receipt_service.py:59-110` (`persist_entitlement`); tests in `tests/unit/test_dividend_event_service.py` or a new `tests/unit/test_persist_entitlement_deductions.py` (use the fixtures of `test_dividend_auto_record_service.py`).

**Interfaces:** unchanged signature `persist_entitlement(db, event, qty, payment_date=None, *, default_fee=None)`; new behaviour per Decision 8.

- [ ] **Step 1: Failing tests** — existing pending auto row (0.48 × 20,000, fee 10 → amount 9,590) edited to fee 0 (amount 9,600, revision 2): a re-persist with the same event → no change, revision stays 2, `review_reason` None; a re-persist with per-share 0.50 → amount `10000.00` (gross 10,000 − fee 0 − tax 0), fee still 0, revision 3; existing confirmed row edited to fee 0 → re-persist with the same event → no `source_correction`, no `review_reason`; re-persist with a changed per-share → `source_correction` + `review_reason` as today; a brand-new event → fee 10 / computed tax as today (existing tests keep passing); a row with stock shares and deductions equal to gross → amount 0 (no floor); a cash-only row → the floor as today.
- [ ] **Step 2–4:** run → FAIL; implement; `pytest tests/unit -q` → PASS (including `test_dividend_auto_record_service.py`, `test_dividend_event_service.py`, the reconciliation tests).
- [ ] **Step 5: Commit** `fix(api): persist_entitlement keeps owner-set dividend deductions`

---

### Task 2: Route + schema

**Files:** `app/schemas/portfolio.py` (`DeductionsUpdate`), `app/routers/portfolio.py` (route after `confirm-receipt`), `tests/unit/test_dividend_deductions_router.py` (TestClient pattern of `tests/unit/test_dividends_backfill_router.py`).

- [ ] **Step 1: Failing tests** — `PATCH /api/portfolio/dividends/{id}/deductions` `{fee: 0, tax: 0, revision: 1}` → 200 with `amount`, `fee`, `revision`; stale revision → 409 with the service text; `fee: -1` → 422 (pydantic); `fee: 1.005` → 422 (`decimal_places=2`); fee + tax > gross → 422 with a string `detail`; unknown id → 404 `dividend not found`; legacy row → 409; `OperationalError` (monkeypatched service) → 409 `Concurrent edit or database contention; reload before retry`.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(api): PATCH /dividends/{id}/deductions`

---

### Task 3: Dividends page — 修改扣款 inline form

**Files:** `frontend/src/app/services/portfolio.service.ts` (`updateDividendDeductions(id: number, body: { fee: number; tax: number; revision: number }): Observable<Dividend>`), `components/portfolio/dividend-list/dividend-list.{ts,html,scss,spec.ts}`.

- [ ] **Step 1: Failing tests** — menus: pending TW row → 編輯 / 修改扣款 / 刪除; confirmed TW row → 修改扣款 only, no toast; pending USD row → 編輯 / 刪除, no toast; legacy and unresolved rows → the info toast; choosing 修改扣款 renders one `.deductions-form` under that row with the title, both inputs prefilled (`10`, `0`), `step="0.01"`, the note (confirmed variant includes the cash sentence), 取消 / 儲存; 儲存 disabled when a field is empty or negative or while busy; submit sends `{ fee: 0, tax: 0, revision: 2 }` (row revision 2; `revision ?? 1` when absent) and on success closes + reloads both sets; 409 → toast `扣款更新未完成` + detail; 422 array detail → fallback text; opening 修改扣款 closes an open 確認收款 form and vice versa; opens are ignored while either submit is busy; a filter change closes it; focus moves to the 匯費 input on open and to `button.row-action[data-row-id="…"]` on 取消 and after a settled submit; phone → stacked fields via `.receipt-fields`; the request is cancelled on destroy (same pattern as `receiptSub`).
- [ ] **Step 2–4:** run → FAIL; implement; dividend-list spec → PASS; full suite once; `npm run build` budget check.
- [ ] **Step 5: Commit** `feat(dividends): 修改扣款 inline form for pending and confirmed TW dividends`

---

### Task 4: Verification + docs

- [ ] **Step 1:** `pytest tests/unit -q`, frontend suite twice, `npm run build`.
- [ ] **Step 2 (controller):** Playwright screenshots (stubbed API) of the form open on a confirmed row, desktop + phone.
- [ ] **Step 3:** docs: a "Deductions" paragraph where the receipt endpoint is documented (`services/stock-portfolio-service/README.md` or `SPEC.md`); commit `docs(api): dividend deductions endpoint`.
- [ ] **Deploy (owner go):** API first from the release checkout (memory `portfolio-api-release-checkout`; no migration; smoke `PATCH` on a nonexistent id → 404), then the SPA release; the owner then fixes the affected dividend via 修改扣款.
