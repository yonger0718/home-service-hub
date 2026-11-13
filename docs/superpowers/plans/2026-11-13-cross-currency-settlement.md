# Cross-Currency Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle a receivable or payable from an account in another currency (#382), with every open-amount figure netting it in the debt's currency.

**Architecture:** `POST /entries/{id}/settle` takes an optional `original_amount` (the amount settled, in the debt's currency) and books the settlement in the chosen account's currency with the transfer-leg FX fields. One SQL "settled value" expression replaces the same-currency-only sums in the four places that net settlements. The SPA settle panel offers accounts in any currency and asks for both amounts.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Pydantic 2 (Python 3.13), pytest on a per-test PostgreSQL database; Angular 21 standalone + signals, Vitest.

**Spec:** `openspec/changes/add-cross-currency-settlement/` (proposal, design D1–D8, spec deltas for `accounting-ledger`, `accounting-schedules`, `frontend-accounting-ledger`).

## Global Constraints

- Settled value of a settlement `s` against its target `t` (design D3), used everywhere: `s.amount` when `s.currency = t.currency`; `s.original_amount` when `s.currency ≠ t.currency` and `s.original_currency = t.currency`; otherwise `0`.
- Cross-currency settlement row: `currency = account.currency`, `amount = sign × amount`, `original_amount = sign × original_amount`, `original_currency = target.currency`, `fx_rate = (|amount| / |original_amount|).quantize(Decimal("1E-10"), ROUND_HALF_UP)`, `fx_source = "manual"`. Sign: `+1` receivable, `−1` payable (`SETTLE_SIGNS`).
- 422 field names: `original_amount` (missing across currencies, or present in the same currency); `amount` (settled value above the open amount) — message unchanged `exceeds the open amount {remaining}`.
- Refunds stay same-currency (`_same_currency_account` keeps serving `refund()`); schedule postings stay same-currency (they never pass `original_amount`).
- No migration; no change to `resolve_fx` / the entry form.
- Backend test command, from `services/accounting-service` (the worktree has no `.env`; the venv is a symlink to the main checkout's):
  `set -a; eval "$(grep -E '^(POSTGRES_(HOST|PORT|USER|PASSWORD|DB)|DB_HOST|ACCOUNTING_DB|OTEL_SERVICE_NAME_ACCOUNTING|OTEL_COLLECTOR_ENDPOINT_GRPC)=' ~/workspace/home-hub/.env)"; set +a; PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest <paths> -q`
  Never print the variables, never copy the `.env`, never run alembic or the app against them (pytest creates its own databases).
- SPA from `frontend/`: `npx ng test --watch=false --include='**/<file>.spec.ts'`, `npx tsc -p tsconfig.app.json --noEmit`, `npx ng build --configuration development`. Never `npm run config` / `set-env.js` / `npm run build` / `npm start`.
- Fixture data fictional. Commit trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.superpowers/` or `.venv`.

## Review Focus

1. A legacy MOZE link across currencies with no `original_amount` must still net to nothing in every reader (open filter, list/detail `open_amount`, counterparty, loan `repaid`) — `test_a_settlement_in_another_currency_leaves_the_list_row_open` and `test_a_settlement_in_another_currency_never_reduces_the_open_amount` keep passing unchanged.
2. Mixed settlements on one debt (one same-currency, one cross-currency) net together; `is_settled` flips only at exactly 0.
3. The refund panel keeps listing same-currency accounts only and never sends `original_amount`.
4. Switching the panel's account between a same-currency and a foreign account resets the second field and the error; the 上限 hint and the cap apply to the settled value, not to the account-currency amount.
5. A payable repaid across currencies: negative signs on both `amount` and `original_amount`, open amount decreases (scenario "Repayment in another currency").

---

### Task 1: Settle endpoint across currencies

**Files:**
- Create: `services/accounting-service/app/services/settled_value.py`
- Modify: `services/accounting-service/app/schemas/writes.py:167` (`SettleIn`), `services/accounting-service/app/services/settlement_service.py`
- Test: `services/accounting-service/tests/integration/test_settlements.py`

**Interfaces:**
- Produces: `settled_value(settlement, target_currency)` in `settled_value.py` — a SQLAlchemy column expression over an (aliased) `LedgerEntry` `settlement` and a currency column or string `target_currency`, implementing the Global Constraints rule with `case(...)` and `coalesce(original_amount, 0)`; no imports from other service modules (it is imported by `ledger_service`, `settings_service`, `schedule_read`, `settlement_service`).
- Produces: `SettleIn.original_amount: Money | None = None`.
- Produces: `settlement_service.open_amount(db, entry)` → `|entry.amount + Σ settled_value(s, entry.currency)|` over `s.settles_entry_id == entry.id`.

- [ ] **Step 1: Write the failing tests** in `test_settlements.py` (fixture helpers: `seed.account(name, currency=...)`, `seed.counterparty`, `seed.entry(account, "-20", kind="receivable", counterparty_id=...)`; `_balance`, `_error_fields` exist):
  - `test_collection_in_another_currency` — USD card `-20` receivable; POST settle `{account_id: TWD wallet, amount: "640", original_amount: "20"}` → 201; wallet balance `+640`; the new row (`response.json()`) has `amount "640.0000"`, `original_amount "20.0000"`, `original_currency "USD"`, `fx_rate "32.0000000000"`, `fx_source "manual"`, `currency "TWD"`; `GET /entries/{receivable}` → `open_amount "0.0000"`, `is_settled true`.
  - `test_repayment_in_another_currency` — USD payable `+50`; settle `amount 1600, original_amount 30` into TWD → row `amount "-1600.0000"`, `original_amount "-30.0000"`; payable `open_amount "20.0000"`.
  - `test_partial_cross_currency_then_same_currency_settles_fully` — USD receivable `-20`; cross `640/12` then USD account `8` → `is_settled true`; a further `1` USD → 422 `{"amount"}`.
  - `test_cross_currency_over_settlement_refused` — `amount 800, original_amount 25` against `20` open → 422 `{"amount"}`.
  - `test_original_amount_refused_in_the_same_currency` — TWD receivable, TWD account, `original_amount "10"` → 422 `{"original_amount"}`.
  - Change `test_settlement_in_another_currency_refused` (keep its name and spec comment): the expected field becomes `{"original_amount"}`.
  - `test_refund_rules_currency_and_kind` stays as is (refund in another currency still refused on `currency`).
- [ ] **Step 2: Run** `… -m pytest tests/integration/test_settlements.py -q` — expected: the new tests and the changed one FAIL (422 `currency` / missing fields).
- [ ] **Step 3: Implement** `settled_value`, `SettleIn.original_amount`, and in `settle()`: load the account (404-style `ValidationError("account_id", …)` as today); same currency → refuse a non-null `original_amount`; other currency → require it; compute `settled = original_amount or amount`; compare `settled` with `open_amount`; build the row with the FX fields per Global Constraints. Rewrite `open_amount` on `settled_value`. Keep `_same_currency_account` for `refund()`. Update the module docstring (cross-currency settlement is no longer "a later phase"; refunds still are).
- [ ] **Step 4: Run** `test_settlements.py`, `tests/integration/test_entries_api.py`, `tests/integration/test_edit_lock.py`, `tests/integration/test_schedule_attach_loan.py` — all PASS.
- [ ] **Step 5: Commit** `feat(accounting): settle a debt from an account in another currency (#382)`.

### Task 2: Every reader nets the settled value

**Files:**
- Modify: `services/accounting-service/app/services/ledger_service.py:316` (`_settled_sum`), `services/accounting-service/app/services/settings_service.py:1121` (`_open_amounts`), `services/accounting-service/app/services/schedule_read.py:157` (`loan_summary` repaid)
- Test: `services/accounting-service/tests/integration/test_entries_api.py`, `tests/integration/test_settings_crud_api.py` (next to `test_counterparty_open_amounts_per_currency`, line 425), `tests/integration/test_schedule_read.py`

**Interfaces:**
- Consumes: `settled_value` (Task 1).
- `_settled_sum()` keeps its signature (correlated scalar subquery over the outer `LedgerEntry`); `_open_amounts(db)` keeps its return shape `{counterparty_id: [{"currency", "amount"}]}`; `loan_summary` keeps `(remaining, repaid)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_entries_api.py::test_a_cross_currency_settlement_with_an_original_amount_closes_the_list_row` — rows built with `make_entry` (USD card receivable `-20`; TWD collection `+640` with `original_amount=Decimal("20")`, `original_currency="USD"`, `settles_entry_id`, `is_settlement=True`) → `GET /entries?open=true` excludes it; list row and detail `open_amount "0.0000"`; counterparty `open_count` 0.
  - same file, mixed: `-20` USD with a USD `+5` and a TWD `+480` (`original_amount 15`) → not open; with `original_amount 14` → `open_amount "1.0000"`.
  - counterparties: scenario "Collection in another currency nets in the debt's currency" (`open_amounts == []` for Bea) and "Imported link across currencies without an original amount" (`[{"currency": "TWD", "amount": "100.0000"}]` — match the existing assertions' string format in that file).
  - `test_schedule_read.py`: scenario "Hand repayment in another currency" — reuse the file's existing loan-definition fixture style; assert `repaid == 300`, `remaining == -700` (signs as the file's existing loan tests express them).
- [ ] **Step 2: Run** them — expected FAIL (row still open; counterparty shows a TWD item; repaid sums 3200).
- [ ] **Step 3: Implement** with `settled_value`: `_settled_sum` sums `settled_value(settlement, LedgerEntry.currency)`; `_open_amounts` buckets a linked cross-currency settlement under its target's currency with its settled value and keeps excluding one without (join the target; group by the effective currency); `loan_summary` sums `abs(settled_value(s, loan.currency))`. Update the three docstrings that say "another currency never nets".
- [ ] **Step 4: Run** the full backend suite (`… -m pytest -q`) — expected all PASS (report the counts; skips unchanged).
- [ ] **Step 5: Commit** `feat(accounting): open amounts, counterparty totals and loan repaid net cross-currency settlements (#382)`.

### Task 3: SPA settle panel across currencies

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts:581` (`SettleInput`), `frontend/src/app/components/accounting/entry-detail/entry-detail.ts`, `entry-detail.html:120-152`, `entry-detail.scss` (only if the second field needs a rule)
- Test: `frontend/src/app/components/accounting/entry-detail/entry-detail.spec.ts`

**Interfaces:**
- `SettleInput.original_amount?: string` (omitted, not null, for same currency).
- New signals/computeds on `EntryDetailComponent`: `settleAccounts` (non-archived; entry currency first in current order, then the rest in current order), `formAccount` (the chosen `LedgerAccount | null`), `crossCurrency` (`panel() === 'settle'` and `formAccount().currency !== detail.currency`), `formOriginal: WritableSignal<string>` (the 抵銷 field), `conversionLine` (`fxConversionLine({amount, currency: account currency, original_amount, original_currency: entry currency, fx_rate: String(|amount| / |original|)})` from `accounting-ui.ts` — its parameter type `FxSource` is a `Pick` of `LedgerEntry`, so pass strings, or null until both parse > 0).
- `sameCurrencyAccounts` stays and keeps feeding the refund panel.

- [ ] **Step 1: Write the failing tests** (fixtures fictional; extend the existing settle tests' harness at `entry-detail.spec.ts:330`):
  - settle panel lists a USD account after the TWD ones, labelled `名稱 (USD)`; the refund panel still lists TWD only.
  - choosing the USD-debt's TWD account (debt `-20` USD, open `20`) shows a second field 抵銷 prefilled `20` and an empty 金額; typing `640` shows `US$20.00 × 32 = $640`; saving posts `{account_id, amount: "640", original_amount: "20", …}`.
  - 抵銷 above the open amount → `金額不可超過 US$20.00`, no request; empty 金額 → `請輸入金額`, no request.
  - switching back to a same-currency account hides 抵銷, clears it, and the body has no `original_amount` key.
  - Enter in the 抵銷 field submits (same rule as the other inline-form inputs).
- [ ] **Step 2: Run** `npx ng test --watch=false --include='**/entry-detail.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** Template: the 帳戶 select iterates `settleAccounts()` on the settle panel and `sameCurrencyAccounts()` on the refund panel; option text `name` or `name (CUR)` for another currency. With `crossCurrency()`: label the first amount `收到金額` (receivable) / `付出金額` (payable) with the account's currency code, add `<label><span>抵銷 {entry currency}</span><input class="form-original" …><small>上限 …</small></label>`, and a `<p class="form-conversion">` with `conversionLine()`. `openPanel('settle')` and `setFormAccount` reset `formOriginal` to the open amount string (cross) or `''`; `setFormAccount` also clears `formError`. `submitForm`: same currency → current path; cross → parse 金額 in the account currency (`parseAmountText(value, account.currency)`), parse 抵銷 in the entry currency, cap 抵銷 by `formMax()`, send `original_amount: amountString(original, detail.currency)`.
- [ ] **Step 4: Run** the spec file, then `npx tsc -p tsconfig.app.json --noEmit` — PASS.
- [ ] **Step 5: Commit** `feat(spa): collect or repay a debt from an account in another currency (#382)`.

### Task 4: 已收 / 已還 in the debt's currency, ticks

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (`breakdown`), `openspec/changes/add-cross-currency-settlement/tasks.md`
- Test: `entry-detail.spec.ts`

**Interfaces:**
- A pure helper `settledValue(settlement: Pick<LedgerEntry,'amount'|'currency'|'original_amount'|'original_currency'>, currency: string): number` (in `entry-detail.ts` or `accounting-ui.ts`, exported for the test) implementing the Global Constraints rule.

- [ ] **Step 1: Write the failing tests:** `-20` USD receivable settled by `+640 TWD (original 20 USD)` → breakdown `原始 US$20.00 · 已收 US$20.00 · 剩餘 US$0.00` and the 已結清 badge; a legacy row (`+100 JPY`, no original) on a `-100` TWD receivable closed by MOZE → `已收 $0` is not shown as `$100` (breakdown `原始 $100 · 已收 $0 · 已結清`); the existing breakdown tests unchanged.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `breakdown` on `settledValue`; tick 1.1–1.3 and 2.1–2.3 in the change's `tasks.md`.
- [ ] **Step 4: Run** the full SPA suite `npx ng test --watch=false`, `tsc`, `npx ng build --configuration development`, and `npx -y @fission-ai/openspec@latest validate add-cross-currency-settlement --strict` — PASS.
- [ ] **Step 5: Commit** `feat(spa): 已收 / 已還 sums settled values in the debt's currency (#382)`.

## Rollout (after merge; not a task)

API deploy (no migration) → Hermes `homehub-api` skill: `POST /entries/{id}/settle {account_id, amount, original_amount?, entry_date}`, with the rule "account in another currency → `amount` in the account's currency, `original_amount` in the debt's" → SPA release → book the waiting USD receivable by hand with the owner → tick 3.1, archive the change.
