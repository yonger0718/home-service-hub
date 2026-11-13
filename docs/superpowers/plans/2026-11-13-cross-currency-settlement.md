# Cross-Currency Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle a receivable or payable from an account in another currency (#382), with every open-amount figure netting it in the debt's currency.

**Architecture:** `POST /entries/{id}/settle` takes an optional `original_amount` (the amount settled, in the debt's currency) and books the settlement in the chosen account's currency with the transfer-leg FX fields. One SQL "settled value" expression replaces the same-currency-only sums in the four places that net settlements. The SPA settle panel offers accounts in any currency and asks for both amounts.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Pydantic 2 (Python 3.13), pytest on a per-test PostgreSQL database; Angular 21 standalone + signals, Vitest.

**Spec:** `openspec/changes/add-cross-currency-settlement/` (proposal, design D1–D8, spec deltas for `accounting-ledger`, `accounting-schedules`, `frontend-accounting-ledger`).

## Global Constraints

- Settled value of a settlement `s` against its target `t` (design D3), used everywhere: `s.amount` when `s.currency = t.currency`; `s.original_amount` when `s.currency ≠ t.currency` and `s.original_currency = t.currency`; otherwise `0`. Branches in that order: a same-currency row carrying `original_amount` (MOZE FX rows) counts its `amount`.
- Cross-currency settlement row: `currency = account.currency`, `amount = sign × amount`, `original_amount = sign × original_amount`, `original_currency = target.currency`, `fx_rate = (|amount| / |original_amount|).quantize(Decimal("1E-10"), ROUND_HALF_UP)`, `fx_source = "manual"`. Rate bound: compute and quantize under `localcontext(prec=60)` (the default 28 digits raise `InvalidOperation` for `9999999999999999 / 0.0001`); when the quantized rate is `0` or `>= Decimal("1E10")` (NUMERIC(20,10)), 422 on `original_amount` before any write. Sign: `+1` receivable, `−1` payable (`SETTLE_SIGNS`).
- `original_amount: null` in the JSON = absent (accepted in the same currency).
- 422 field names: `original_amount` (missing across currencies, present in the same currency, or rate out of range); `amount` (settled value above the open amount) — message unchanged `exceeds the open amount {remaining}`.
- Refunds stay same-currency (`_same_currency_account` keeps serving `refund()`); schedule postings stay same-currency (they never pass `original_amount`). The MOZE importer's FIFO stays as is.
- Loan `remaining` stays the positive magnitude `loan_open_amount` returns today; only `repaid` changes.
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
  - `test_null_original_amount_is_absent` (regression guard: already passes on today's code, which ignores the key) — TWD receivable `-200`, TWD account, body with `"original_amount": null` → 201, `is_settled true`.
  - `test_rate_out_of_range_refused` — USD receivable `-20`: `amount "1000000000000", original_amount "20"` → 422 `{"original_amount"}`; USD receivable `-5000000`: `amount "0.0001", original_amount "5000000"` (rate quantizes to 0) → 422 `{"original_amount"}`; USD receivable `-1`: `amount "9999999999999999", original_amount "0.0001"` → 422 `{"original_amount"}` (not 500); both write nothing (receivable `open_amount` unchanged, account balance unchanged); just inside the bound, `amount "99999999.9999", original_amount "0.01"` against the `-20` receivable (rate `9999999999.99`) → 201.
  - `test_cross_currency_four_decimals_full_settle` — USD receivable `-20.0001`; TWD settle `amount "640.0032", original_amount "20.0001"` → `fx_rate "32.0000000000"`, `open_amount "0.0000"`, `is_settled true`.
  - `test_non_divisible_rate_keeps_the_settled_value` — USD receivable `-3`; TWD settle `amount "100", original_amount "3"` → `fx_rate "33.3333333333"`, `open_amount "0.0000"` (the open amount uses `original_amount`, never the rate).
  - `test_settled_value_precedence` — rows built with `make_entry` against a USD receivable `-50`: a USD settlement `+10` carrying `original_amount=Decimal("320")`, `original_currency="TWD"` (counts `10`); a TWD settlement `+320` with `original_amount=Decimal("10")`, `original_currency="JPY"` (counts `0`); a TWD settlement `+320` with `original_amount=Decimal("10")`, `original_currency="USD"` (counts `10`) → `open_amount(db, receivable) == Decimal("30")`.
- [ ] **Step 2: Run** `… -m pytest tests/integration/test_settlements.py -q` — expected: the new tests and the changed one FAIL (422 `currency` / missing fields), except the null regression guard.
- [ ] **Step 3: Implement** `settled_value`, `SettleIn.original_amount`, and in `settle()`: load the account (404-style `ValidationError("account_id", …)` as today); same currency → refuse a non-null `original_amount`; other currency → require it, compute the rate under `decimal.localcontext()` with `prec = 60`, quantize it, and refuse `0` / `>= 1E10` on `original_amount`; compute `settled = original_amount or amount`; compare `settled` with `open_amount`; build the row with the FX fields per Global Constraints. Rewrite `open_amount` on `settled_value`. Keep `_same_currency_account` for `refund()`. Update the module docstring (cross-currency settlement is no longer "a later phase"; refunds still are).
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
  - counterparties: scenario "Collection in another currency nets in the debt's currency" (`open_amounts == []` for Bea) — must FAIL today; "Imported link across currencies without an original amount" (`[{"currency": "TWD", "amount": "100.0000"}]` — match the existing assertions' string format in that file) — a regression guard that already passes, name it `..._regression_guard` or say so in its docstring.
  - counterparties, closed target: a USD receivable `-20` with `is_closed=True` and a TWD collection `+640` (`original_amount 20`, `original_currency "USD"`) → nothing for that counterparty (closed debts and their settlements stay out).
  - counterparties, unlinked settlement: a TWD `receivable` `+300` with `is_settlement=True` and no `settles_entry_id` next to a USD receivable `-20` → buckets `TWD -300` and `USD 20` (unlinked rows keep their own currency; proves the target join is outer).
  - `test_schedule_read.py`: scenario "Hand repayment in another currency" — reuse the file's existing loan-definition fixture style; a USD payable loan `+1000`, 2 USD repayments `-100`, one TWD hand repayment `-3200` with `original_amount=Decimal("-100")`, `original_currency="USD"` → `repaid == 300`, `remaining == 700` (positive magnitude, like the file's existing `275001`).
  - same file, scenario "Legacy link across currencies does not count as repaid": 1 USD repayment `-100` and one linked TWD `-3200` without `original_amount` → `repaid == 100`, `remaining == 900` (FAILs today: repaid sums 3300).
- [ ] **Step 2: Run** them — expected FAIL (row still open; counterparty shows a TWD item; repaid sums 3200).
- [ ] **Step 3: Implement** with `settled_value`: `_settled_sum` sums `settled_value(settlement, LedgerEntry.currency)`; `_open_amounts` LEFT OUTER JOINs the target (aliased) on `settles_entry_id`, keeps the existing closed exclusions, and only for a linked row whose currency differs from the target's uses the target's currency as bucket and `settled_value` as amount (excluded when it has none); every other row keeps its own currency and amount; group by the effective currency; `loan_summary` sums `abs(settled_value(s, loan.currency))`; `remaining` untouched. Update the three docstrings that say "another currency never nets".
- [ ] **Step 4: Run** the full backend suite (`… -m pytest -q`) — expected all PASS (report the counts; skips unchanged).
- [ ] **Step 5: Commit** `feat(accounting): open amounts, counterparty totals and loan repaid net cross-currency settlements (#382)`.

### Task 3: SPA settle panel across currencies

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts:581` (`SettleInput`), `frontend/src/app/components/accounting/entry-detail/entry-detail.ts`, `frontend/src/app/components/accounting/amount-text.ts` (+ its spec), `entry-detail.html:120-152`, `entry-detail.scss` (only if the second field needs a rule)
- Test: `frontend/src/app/components/accounting/entry-detail/entry-detail.spec.ts`

**Interfaces:**
- `SettleInput.original_amount?: string` (omitted, not null, for same currency).
- New signals/computeds on `EntryDetailComponent`: `settleAccounts` (non-archived; entry currency first in current order, then the rest in current order), `formAccount` (the chosen `LedgerAccount | null`), `crossCurrency` (`panel() === 'settle'` and `formAccount().currency !== detail.currency`), `formOriginal: WritableSignal<string>` (the 抵銷 field), `conversionLine` (`fxConversionLine({amount, currency: account currency, original_amount, original_currency: entry currency, fx_rate: String(|amount| / |original|)})` from `accounting-ui.ts` — its parameter type `FxSource` is a `Pick` of `LedgerEntry`, so pass strings, or null until both parse > 0).
- `sameCurrencyAccounts` stays and keeps feeding the refund panel.

- [ ] **Step 1: Write the failing tests** (fixtures fictional; extend the existing settle tests' harness at `entry-detail.spec.ts:330`):
  - settle panel lists a USD account after the TWD ones, labelled `名稱 (USD)`; the refund panel still lists TWD only.
  - choosing the USD-debt's TWD account (debt `-20` USD, open `20`) shows a second field 抵銷 prefilled `20` and an empty 金額; typing `640` shows `US$20 × 32 = $640`; saving posts `{account_id, amount: "640", original_amount: "20", …}`.
  - 抵銷 above the open amount → `金額不可超過 US$20`, no request; empty 金額 → `請輸入金額`, no request.
  - switching from the TWD default (金額 prefilled with the open amount) to a USD-debt's foreign account clears 金額; switching back to a same-currency account hides 抵銷, clears it, restores 金額 to the open amount, and the body has no `original_amount` key.
  - a USD account chosen for a TWD debt (`-640` TWD, open `640`): typing `20.5` in 金額 posts `amount: "20.5"` (serialized in the account's currency, not rounded to TWD's 0 decimals), 抵銷 `640` → `original_amount: "640"`.
  - precision: debt `-20.0001` USD (`open_amount "20.0001"`), TWD account → 抵銷 prefilled `20.0001`; 金額 `640`, saved → body `original_amount: "20.0001"`; 抵銷 typed `20.0002` → `金額不可超過 …`, no request; typed `10` → `"10"`. Remainder below one display unit: open `"0.0001"` → 抵銷 prefilled `0.0001`, 金額 `1` → `original_amount: "0.0001"`; 抵銷 cleared → `請輸入金額`, no request; 抵銷 `abc` → no request. TWD debt open `"0.1"` with a USD account → 抵銷 prefilled `0.1` → `original_amount: "0.1"`.
  - Enter in the 抵銷 field submits (same rule as the other inline-form inputs); Escape in it closes the inline form only (the outer sheet stays open — same assertion style as the existing Escape test).
  - refund panel submit body has no `original_amount` key.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='**/entry-detail.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** Template: the 帳戶 select iterates `settleAccounts()` on the settle panel and `sameCurrencyAccounts()` on the refund panel; option text `name` or `name (CUR)` for another currency. With `crossCurrency()`: label the first amount `收到金額` (receivable) / `付出金額` (payable) with the account's currency code, add `<label><span>抵銷 {entry currency}</span><input class="form-original" …><small>上限 …</small></label>`, and a `<p class="form-conversion">` with `conversionLine()`. `openPanel('settle')` and `setFormAccount` reset `formOriginal` to `moneyString(Number(detail.open_amount))` (cross) or `''`; `setFormAccount` also clears `formError`, clears `formAmount` when the new account is in another currency, and restores `formAmount = amountString(formMax(), detail.currency)` when it switches back to the entry's currency. `submitForm`: same currency → current path; cross → parse 金額 in the account currency (`parseAmountText(value, account.currency)`) and serialize it with `amountString(amount, account.currency)` (today line ~797 uses `detail.currency` — must not for the cross path); parse 抵銷 with `parseMoneyText(text)` (four decimals: `evaluateAmount(text, 4)`, null when empty, invalid or not positive → `請輸入金額`); `v > Number(detail.open_amount)` (compare after `moneyString`) → `金額不可超過 {formatted open}`; else `original_amount: moneyString(v)`. Add `parseMoneyText(text: string): number | null` and `moneyString(value: number): string` (`toFixed(4)` without trailing zeros) to `amount-text.ts`, with unit tests in its spec file (`0.0001`, `20.0001`, `''`, `abc`, `0`).
- [ ] **Step 4: Run** the spec file, then `npx tsc -p tsconfig.app.json --noEmit` — PASS.
- [ ] **Step 5: Commit** `feat(spa): collect or repay a debt from an account in another currency (#382)`.

### Task 4: 已收 / 已還 in the debt's currency, ticks

**Files:**
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (`breakdown`), `openspec/changes/add-cross-currency-settlement/tasks.md`
- Test: `entry-detail.spec.ts`

**Interfaces:**
- A pure helper `settledValue(settlement: Pick<LedgerEntry,'amount'|'currency'|'original_amount'|'original_currency'>, currency: string): number` (in `entry-detail.ts` or `accounting-ui.ts`, exported for the test) implementing the Global Constraints rule.

- [ ] **Step 1: Write the failing tests:** `-20` USD receivable settled by `+640 TWD (original 20 USD)` → breakdown `原始 US$20 · 已收 US$20 · 剩餘 US$0` and the 已結清 badge; a legacy row (`+100 JPY`, no original) on a `-100` TWD receivable closed by MOZE → `已收 $0` is not shown as `$100` (breakdown `原始 $100 · 已收 $0 · 已結清`); the existing breakdown tests unchanged.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `breakdown` on `settledValue`; tick 1.1–1.3 and 2.1–2.3 in the change's `tasks.md`.
- [ ] **Step 4: Run** the full SPA suite `npx ng test --watch=false`, `tsc`, `npx ng build --configuration development`, and `npx -y @fission-ai/openspec@latest validate add-cross-currency-settlement --strict` — PASS.
- [ ] **Step 5: Commit** `feat(spa): 已收 / 已還 sums settled values in the debt's currency (#382)`.

## Rollout (after merge; not a task)

API deploy (no migration) → Hermes `homehub-api` skill: `POST /entries/{id}/settle {account_id, amount, original_amount?, entry_date}`, with the rule "account in another currency → `amount` in the account's currency, `original_amount` in the debt's" → SPA release → book the waiting USD receivable by hand with the owner → tick 3.1, archive the change.
