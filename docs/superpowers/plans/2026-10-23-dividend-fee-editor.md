# Editable TW dividend 匯費 / 補充保費 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner change a TW dividend's 匯費 (`fee`) and 補充保費 (`tax`) — including after 確認收款 — with the net amount recomputed and the posted cash leg kept in step, from the 股利 page.

**Architecture:** One new write path `PATCH /api/portfolio/dividends/{id}/deductions` (service `dividend_deduction_service.update_deductions`) with compare-and-swap on `revision`, gross recomputed from per-share × quantity (or from the stored net + old deductions), and an idempotent update of the single auto-derived cash leg via the existing `sync_dividend_cash_leg`. The dividends page gains a ⋯ → 修改扣款 action that opens an inline form under the row (same look as the receipt form). No schema change, no migration, defaults untouched.

**Tech Stack:** FastAPI + SQLAlchemy service (`cd services/stock-portfolio-service && pytest tests/unit -q`), Angular 21 standalone / signals (`cd frontend && npx ng test --watch=false --include <spec>`).

**Spec:** Owner requirement 2026-10-23 (a resolved dividend was not charged the 10 TWD handling fee and could not be edited after confirmation) + the invariants from Multica AGENT-218 §2 (recorded in `docs/superpowers/plans/2026-10-23-redesign-v2-realized-cash.md`, Appendix). Background: `DEFAULT_HANDLING_FEE = 10` (`app/services/dividend_auto_record_service.py:37`) applied by `persist_entitlement` (`app/services/dividend_receipt_service.py:59-65`, `amount = max(0.01, gross − fee − tax)`); `update_dividend` rejects non-pending rows (`app/services/portfolio_service.py:1691-1692`) and writes a client `amount`; `confirm_receipt` (`dividend_receipt_service.py:153-215`) is the CAS pattern; `sync_dividend_cash_leg` (`app/services/cash_account_service.py:350-399`) upserts by fingerprint `backfill|dividends|{id}|dividend_cash` and raises when a confirmed row's account differs; `_refresh_snapshot_cash_range(session, from, to)` (`:549`) is what manual cash writes call (`:545`); the dividends page refuses the ⋯ menu for non-pending rows (`frontend/src/app/components/portfolio/dividend-list/dividend-list.ts:485-488`).

## Decisions

1. **Default unchanged.** New auto entitlements keep fee 10 (`DEFAULT_HANDLING_FEE`); "optional" means any row's fee may later be set to 0 or another non-negative value.
2. **Endpoint:** `PATCH /dividends/{dividend_id}/deductions`, body `DeductionsUpdate { fee: Decimal ≥ 0, tax: Decimal ≥ 0, revision: int ≥ 1 }` (schema beside `ReceiptConfirmation`, `app/schemas/portfolio.py:160-163`), response `schemas.Dividend`. Errors: 404 missing id; 409 (`ReceiptConflict` text) for stale revision, status not in {pending, confirmed}, non-TW/TWD, receipt-account mismatch on sync, non-auto cash leg; 422 for negative fee/tax or a non-positive net without stock shares. Router mirrors the confirm-receipt block (`app/routers/portfolio.py:156-166`): rollback + 409 on `ReceiptConflict` / `IntegrityError`, 409 on `OperationalError`.
3. **Amount rule:** `gross = quantize(quantity_at_record_date × cash_dividend_per_share, 0.01, ROUND_HALF_UP)` when both are present and per-share > 0, else `gross = amount + fee + tax` (stored values); `new_amount = gross − new_fee − new_tax`; no 0.01 floor; `new_amount < 0` → 422; `new_amount == 0 and stock_dividend_shares == 0` → 422 (`ck_dividends_amount_positive`); `== 0` with stock shares is legal. The client never sends `amount`.
4. **Concurrency:** lock the row `with_for_update()`, then `UPDATE … WHERE id = :id AND revision = :client SET fee, tax, amount, revision = revision + 1`; rowcount ≠ 1 → rollback, 409 `entitlement changed; reload before editing`. Identity columns (symbol, market, ex-date, quantity, per-share, receipt date/account, status) never change.
5. **Cash leg:** pending → no sync (a leg is created at confirm time from the then-current amount). Confirmed and `new_amount > 0` → find the one `CashTransaction` with `related_dividend_id == id`: if its `source` is not `AUTO_DERIVE` → 409 `cash leg was imported; edit the cash transaction instead`; else set only its `amount` (not `txn_date`, not `source`) — when its fingerprint equals the backfill fingerprint this is what `sync_dividend_cash_leg(db, row, row.receipt_account_id, AUTO_DERIVE)` does; when the fingerprint differs, update that row directly and never insert a second leg; after the write assert exactly one leg for the dividend. Confirmed and `new_amount == 0` → `delete_auto_derived_dividend_cash_leg` (the dividend row stays). After any leg change call `_refresh_snapshot_cash_range(db, min(leg.txn_date, today), today)`.
6. **Totals** follow `Dividend.amount` automatically (dividend totals, pending projection, XIRR cashflows: `portfolio_service.py:651-663, 879, 957-967`); realized PnL untouched.
7. **UI:** on the 股利 page, ⋯ for pending **and** confirmed TW/TWD rows offers 修改扣款（匯費 / 補充保費）(legacy / unresolved / foreign rows keep today's info toast); it opens an inline form under the row sharing the receipt form's styles and the one-open-at-a-time rule (`openReceiptId` and the new `openDeductionsId` never both set): title `修改扣款 · {name} {amount}`, fields 匯費 and 補充保費 (`<input type="number" min="0" step="1">`, prefilled from the row), note `淨額會重新計算` (+ `，現金帳的股利入帳也會一併更新` on confirmed rows), buttons 取消 / 儲存 (disabled while busy or when either field is empty/negative); submit `updateDividendDeductions(id, { fee, tax, revision: row.revision ?? 1 })`; success → close + `reload()` (both sets); error → close + reload + toast `扣款更新未完成` with the server `detail` when it is a string, else `請重新載入後核對`. Filter/sort/status/page changes close the form (same hooks as the receipt form). Editing the pending row's 編輯 dialog is unchanged.
8. **Copy:** 修改扣款, 匯費, 補充保費, 取消, 儲存, 淨額會重新計算, `，現金帳的股利入帳也會一併更新`, 扣款更新未完成, 請重新載入後核對.

## Global Constraints

- No schema/migration; `DEFAULT_HANDLING_FEE` unchanged; `update_dividend` and `confirm_receipt` behaviour unchanged.
- Every write path tested against the SQLite unit fixtures used by `tests/unit/test_dividend_cash_sync.py` (`db_session`, `monkeypatch` to enable the cash-leg flag — see `cash_leg_enabled()`).
- Frontend: signals + OnPush; specs restore globals; existing dividend-list tests stay green; copy verbatim; `−` U+2212 where amounts are negative (none expected here).

## Review Focus

1. **Double leg** — a confirmed row whose leg has a non-backfill fingerprint must be updated, not duplicated. → T1 test "legacy fingerprint leg is updated, count stays 1".
2. **Race with confirm** — a fee edit with a stale revision after a confirm must change nothing. → T1 CAS test.
3. **Zero net** — fee + tax = gross on a cash-only row → 422, nothing written; with stock shares → amount 0 and the leg deleted. → T1.
4. **Snapshot drift** — the account's balance snapshots after an amount change must be recomputed from the leg date. → T1 asserts `_refresh_snapshot_cash_range` called with (leg date, today).
5. **UI state** — opening 修改扣款 closes an open 確認收款 form and vice versa; a filter change closes it. → T3.

---

### Task 1: Service `update_deductions` (backend)

**Files:**
- Create: `services/stock-portfolio-service/app/services/dividend_deduction_service.py`, `tests/unit/test_dividend_deduction_service.py`

**Interfaces:**
```py
def update_deductions(db: Session, dividend_id: int, fee: Decimal, tax: Decimal, revision: int) -> Dividend  # Decisions 3–5; raises ReceiptConflict / ValueError(422 cases) / LookupError(404)
```
(Reuse `ReceiptConflict` from `dividend_receipt_service`; define `DeductionError(ValueError)` for the 422 cases and `DividendNotFound(LookupError)` for 404.)

- [ ] **Step 1: Failing tests** — pending auto row (per-share 0.48 × 20,000, fee 10, tax 0, amount 9,590, revision 1): fee → 0 gives amount 9,600, fee 0, revision 2, no cash leg; confirmed row with an AUTO_DERIVE leg of 9,590 (backfill fingerprint): fee → 0 gives leg amount 9,600, `txn_date` and `source` unchanged, exactly one leg, revision bumped, snapshot refresh called with (leg date, today); a second identical call with the new revision → no-op success (amount same), still one leg; confirmed leg with a **different** fingerprint → updated in place, count 1; stale revision → `ReceiptConflict`, nothing changed; `legacy_unknown` / `unresolved` → 409 text; USD row → 409; `fee = -1` → 422; fee + tax > gross → 422, nothing written; fee + tax == gross with no stock shares → 422; with `stock_dividend_shares 100` → amount 0 and the auto leg deleted; a row without per-share data (gross = amount + fee + tax) → recomputed from stored values; confirmed leg with `source csv_import` → 409 and the leg untouched; missing id → `DividendNotFound`.
- [ ] **Step 2–4:** run → FAIL (`pytest tests/unit/test_dividend_deduction_service.py -q`); implement; `pytest tests/unit -q` → PASS.
- [ ] **Step 5: Commit** `feat(api): update_deductions for TW dividend fee/tax with cash-leg re-sync`

---

### Task 2: Route + schema

**Files:** `app/schemas/portfolio.py` (`DeductionsUpdate`), `app/routers/portfolio.py` (route), `tests/unit/test_dividend_deductions_router.py` (TestClient like `tests/unit/test_dividends_backfill_router.py`).

- [ ] **Step 1: Failing tests** — `PATCH /api/portfolio/dividends/{id}/deductions` with `{fee: 0, tax: 0, revision: 1}` → 200 and the returned `amount` / `fee` / `revision`; stale revision → 409 with the service text in `detail`; negative fee → 422 (pydantic); fee + tax > gross → 422 with `detail` string; unknown id → 404; legacy row → 409.
- [ ] **Step 2–4:** run → FAIL; implement; run → PASS.
- [ ] **Step 5: Commit** `feat(api): PATCH /dividends/{id}/deductions`

---

### Task 3: Dividends page — 修改扣款 inline form

**Files:** `frontend/src/app/services/portfolio.service.ts` (`updateDividendDeductions(id, body: { fee: number; tax: number; revision: number }): Observable<Dividend>`), `components/portfolio/dividend-list/dividend-list.{ts,html,scss,spec.ts}`.

- [ ] **Step 1: Failing tests** — ⋯ on a pending TW row and on a confirmed TW row lists 修改扣款; on legacy / unresolved / USD rows it does not (today's info toast stays); choosing it renders one `.deductions-form` under that row with the title, both inputs prefilled (`10`, `0`), the note (confirmed variant includes the cash sentence), 取消 / 儲存; 儲存 disabled when a field is empty or negative or while busy; submit sends `{ fee: 0, tax: 0, revision: 2 }` (row revision 2) and on success closes + reloads both sets (list + filter-wide); 409 → toast `扣款更新未完成` + detail; 422 array detail → fallback text; opening 修改扣款 closes an open 確認收款 form and vice versa; a filter change closes it; focus moves into the 匯費 input and back to the ⋯ button on close (same helper as the receipt form); phone → stacked fields.
- [ ] **Step 2–4:** run → FAIL; implement (reuse `receiptSub`-style cancellation and the `focusAfterRender` helper); run the dividend-list spec → PASS; full suite once; `npm run build` budget check.
- [ ] **Step 5: Commit** `feat(dividends): 修改扣款 inline form for pending and confirmed TW dividends`

---

### Task 4: Verification + deploy notes

- [ ] **Step 1:** `pytest tests/unit -q` (service), frontend suite twice, `npm run build`.
- [ ] **Step 2 (controller):** Playwright screenshots (stubbed API) of the form open on a confirmed row, desktop + phone.
- [ ] **Step 3:** docs: a short "Deductions" section in `services/stock-portfolio-service/README.md` or `SPEC.md` (where the receipt endpoint is documented) + the CHANGELOG line if the repo keeps one; commit `docs(api): dividend deductions endpoint`.
- [ ] **Deploy:** API first from the release checkout (memory `portfolio-api-release-checkout`; no migration), then the SPA release; the owner then fixes the affected dividend via the new form.
