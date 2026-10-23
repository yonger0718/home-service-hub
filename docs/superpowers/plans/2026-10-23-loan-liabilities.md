# Loan Liabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a loan payable to an existing installment schedule (balance-neutral), and count open payables as 負債 on the 帳戶 page.

**Architecture:** Two small backend additions (`schedule_service.attach_loan` + `POST /schedules/definitions/{id}/loan`; `ledger_service.debts_summary` + `GET /accounts/debts`) and three SPA pieces (service/model methods; 帳戶 totals with 淨值; a 掛上貸款 form inside the existing schedule sheet). No migration, no new tables.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy (accounting-service), Angular 21 signals / Vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-loan-liabilities-design.md` (rev 2, commit b42dfa7).

## Global Constraints

- Worktree `/home/opc/workspace/home-hub-loans`, branch `feat/loan-liabilities` (from main 616f3d4). Never `git add -A`; `frontend/node_modules`, `frontend/src/environments`, `services/accounting-service/.venv` are symlinks, `.env` is local — never add them.
- Backend tests: from `services/accounting-service`, `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest <file> -q`; never two accounting files concurrently. Frontend: from `frontend`, `npx ng test --include='<spec>'` (rerun once on "could not determine executable"); `npx ng build 2>&1 | tail -3`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Attach never changes `times`, `total_amount`, `first_seq`, `end_date`, instances or `generated_until`; it adds exactly two entries (payable +X, balance_adjustment −X) and sets `loan_entry_id` on the repayment line only.
- Error codes/messages verbatim: 422 `loan` 「只有分期可以掛貸款」, 422 `loan` 「排程沒有還款那一行」, 409 `loan_already_attached`, 409 `catch_up_first`, 422 `amount` 「剩餘期數乘以每期金額已達剩餘本金」, 422 `account` 「貸款要掛在還款帳戶」, 409 `definition_ended`; SPA texts: 「排程已結束」「已經有貸款了」「先補記逾期的期數」 and the two 422 messages verbatim.
- Money/dates in the SPA as strings; amounts shown as positive magnitudes; `LoanIn.entry_date` is required (the SPA defaults it to today).
- Synthetic test data only.

## Review Focus

1. A loan in a foreign currency (JPY schedule): `debts_summary` lists it under `unconverted` when no rate exists and the 帳戶 page says so instead of silently dropping it → Task 2 (`test_debts_summary_lists_unconverted`), Task 4 (`shows the unconverted note`).
2. A schedule whose repayment line's account has a different currency from `LoanIn.account_id`'s: refused before anything is written → Task 1 (`test_attach_loan_refuses_other_account`).
3. A paused schedule: attach allowed (only `ended` is refused) and the next resume/post settles the payable → Task 1 (`test_attach_loan_on_paused_schedule_then_resume_settles`).
4. The attach-day pair must not change the account balance to the cent in a non-TWD account either → Task 1 asserts `account_balance` before == after.
5. `update_definition` after an attach keeps `loan_entry_id` and still passes `_check_total` → Task 1 (`test_update_after_attach_keeps_loan_link`).

---

## File structure

Backend (`services/accounting-service/`):
- `app/services/schedule_service.py` — `attach_loan` (Task 1).
- `app/services/schedule_read.py` — `loan_summary` sign fix (Task 1).
- `app/routers/schedules.py` — the loan route (Task 1).
- `app/services/ledger_service.py` — `debts_summary` (Task 2); `app/schemas/ledger.py` — `DebtTotalOut`, `DebtsSummaryOut` (Task 2); `app/routers/accounts.py` — `GET /accounts/debts` (Task 2).
- Tests: `tests/integration/test_schedule_attach_loan.py` (Task 1), `tests/integration/test_accounts_api.py` (Task 2, appended).

Frontend (`frontend/src/app/`):
- `models/accounting.model.ts`, `services/accounting.service.ts` (+ `services/accounting-schedules.service.spec.ts`) (Task 3).
- `components/accounting/accounts/accounts.{ts,html,spec.ts}` (Task 4).
- `components/accounting/reminders/schedule-sheet/schedule-sheet.{ts,html,scss,spec.ts}` (Task 5).

---

### Task 1: `attach_loan` service + route + `loan_summary` sign

**Files:**
- Modify: `services/accounting-service/app/services/schedule_service.py` (after `update_definition`, ~line 205)
- Modify: `services/accounting-service/app/services/schedule_read.py:100-112` (`loan_summary`)
- Modify: `services/accounting-service/app/routers/schedules.py` (after `put_definition`, ~line 82)
- Test: `services/accounting-service/tests/integration/test_schedule_attach_loan.py` (new)

**Interfaces:**
- Consumes: `create_definition`'s loan branch (:76-104: `prepare_entry(db, EntryIn(account_id, kind="payable", amount, entry_date, category_id, counterparty_id, name))` + `entry_write_service.insert_prepared(db, prepared, remember=False)`), `update_definition`'s guards (:128-150: `take_import_key_shared`, `lock_definition`, the `moze_id`/`import_locked()` → `EditLockedError`, `ConflictError("definition_ended")`), `schedule_templates.loan_line(template)`, `validate_template`, `settlement_service.open_amount`, `entry_write_service.create_balance_adjustment` (:676 — mirror its row construction for a balance_adjustment with an EXPLICIT amount; do not use its checkpoint semantics), `ScheduleInstance` (status, due_date), `ledger_service.account_balance`.
- Produces:
  - `attach_loan(db: Session, definition_id: int, loan: LoanIn) -> int` (returns the payable id). Order: import key shared → `lock_definition` → `moze_id` guard → `kind != "installment"` → 422 `loan` 「只有分期可以掛貸款」 → `status == "ended"` → `ConflictError("definition_ended")` → `loan_line(template)` None → 422 `loan` 「排程沒有還款那一行」 → line has `loan_entry_id` → `ConflictError("loan_already_attached")` → `loan.account_id != line["account_id"]` → 422 `account` 「貸款要掛在還款帳戶」 → any pending instance with `due_date <= loan.entry_date` → `ConflictError("catch_up_first")` → `remaining = times - posted_count`; `Decimal(line["amount"]) * (remaining - 1) >= loan.amount` → 422 `amount` 「剩餘期數乘以每期金額已達剩餘本金」 → insert the payable (as `create_definition`), insert a `balance_adjustment` entry on the same account/date/currency with amount `-loan.amount`, name `掛帳調整：<loan.name or definition.name>` → set `line["loan_entry_id"]`, `definition.template = template`, `definition.template_owner_edited = True` → `validate_template` → `db.flush()`.
  - Route `POST /schedules/definitions/{definition_id}/loan` (body `LoanIn`, `response_model=DefinitionOut`, 201; same `service_errors()` + `db.commit()` + `_definition_out` pattern as `put_definition`; `method_scope` as the other write routes).
  - `loan_summary`: `remaining = open_amount` (positive magnitude; drop the `-open_amount` branch for payables); `repaid` unchanged (already `abs`).

- [ ] **Step 1: Write the failing tests**

```python
"""Attach a loan to an existing installment schedule (spec 2026-10-23 §4.1). Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition
from app.services import ledger_service
from app.services.settlement_service import open_amount


@pytest.fixture()
def plain(seed, db_session, today):
    """A 36-period installment with 14 posted periods and NO loan, like the imported 信貸 definitions."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    definition = seed.definition(
        [seed.line("repayment", bank, "8333"), seed.line("interest", bank, "620")],
        kind="installment", name="信貸", anchor=date(2025, 9, 8), times=36, total_amount=Decimal("300000"),
    )
    # 14 posted periods 2025-09-08 … 2026-10-08, the rest pending from 2026-11-08 (use seed.instance; the posted ones
    # carry their entries like the generator would — copy test_schedule_posting's approach)
    for seq in range(1, 15):
        seed.instance(definition, seq, date(2025 + (seq + 7) // 12, (seq + 7) % 12 + 1, 8), status="posted")
    for seq in range(15, 37):
        seed.instance(definition, seq, date(2026 + (seq + 7) // 12, (seq + 7) % 12 + 1, 8), status="pending")
    db_session.commit()
    return SimpleNamespace(bank=bank, lender=lender, definition=definition)


def _attach(client, definition_id, bank, lender, amount="183000", **extra):
    body = {"account_id": bank.id, "counterparty_id": lender.id, "amount": amount, "entry_date": "2026-10-09", "name": "信貸"}
    body.update(extra)
    return client.post(f"/schedules/definitions/{definition_id}/loan", json=body)


def test_attach_loan_links_and_is_balance_neutral(client, db_session, plain):
    before = ledger_service.account_balance(db_session, plain.bank.id)
    res = _attach(client, plain.definition.id, plain.bank, plain.lender)
    assert res.status_code == 201, res.text
    body = res.json()
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, plain.bank.id) == before
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    adjust = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment"))
    assert payable.amount == Decimal("183000") and adjust.amount == Decimal("-183000") and adjust.account_id == plain.bank.id
    assert adjust.entry_date == payable.entry_date == date(2026, 10, 9) and adjust.name == "掛帳調整：信貸"
    assert open_amount(db_session, payable) == Decimal("183000")
    assert body["template"]["lines"][0]["loan_entry_id"] == payable.id == body["loan_entry_id"]
    assert body["template"]["lines"][1]["loan_entry_id"] is None
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    assert (d.times, d.total_amount, d.first_seq, d.template_owner_edited) == (36, Decimal("300000"), 1, True)
    detail = client.get(f"/entries/{payable.id}").json()
    assert detail["loan_schedule"]["remaining"] == "183000.0000" and detail["loan_schedule"]["repaid"] == "0.0000"


def test_attach_then_posting_settles_the_payable(client, db_session, plain, today):
    _attach(client, plain.definition.id, plain.bank, plain.lender)
    today(date(2026, 11, 8))
    # post the next pending instance the way test_schedule_posting does (schedule_posting.post / the catch-up route)
    from app.services import schedule_posting
    db_session.expire_all()
    nxt = next(i for i in plain.definition.instances if i.status == "pending")
    schedule_posting.post(db_session, nxt.id, date(2026, 11, 8))  # adapt to the real signature in schedule_posting
    db_session.commit()
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == Decimal("183000") - Decimal("8333")
    assert client.get(f"/entries/{payable.id}").json()["loan_schedule"]["repaid"] == "8333.0000"


def test_attach_refusals(client, db_session, plain, seed):
    bank, lender, d = plain.bank, plain.lender, plain.definition
    other = seed.account("別戶")
    db_session.commit()
    assert _attach(client, d.id, other, lender).status_code == 422                       # 貸款要掛在還款帳戶
    assert "剩餘期數乘以每期金額已達剩餘本金" in _attach(client, d.id, bank, lender, amount="100000").text  # 22 left × 8333 ≥ 100000
    ok = _attach(client, d.id, bank, lender)
    assert ok.status_code == 201
    again = _attach(client, d.id, bank, lender)
    assert again.status_code == 409 and "loan_already_attached" in again.text
    rec = seed.definition([seed.line("expense", bank, "100")], kind="recurring", name="月費", anchor=date(2026, 11, 1))
    db_session.commit()
    r = _attach(client, rec.id, bank, lender)
    assert r.status_code == 422 and "只有分期可以掛貸款" in r.text


def test_attach_refuses_overdue_pending_and_ended(client, db_session, plain, today):
    today(date(2026, 12, 20))  # 2026-11-08 and 2026-12-08 are pending and overdue
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, entry_date="2026-12-20")
    assert r.status_code == 409 and "catch_up_first" in r.text
    client.post(f"/schedules/definitions/{plain.definition.id}/end")
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, entry_date="2026-10-09")
    assert r.status_code == 409 and "definition_ended" in r.text


def test_attach_loan_on_paused_schedule_then_resume_settles(client, db_session, plain, today):
    client.post(f"/schedules/definitions/{plain.definition.id}/pause")
    assert _attach(client, plain.definition.id, plain.bank, plain.lender).status_code == 201
    today(date(2026, 11, 9))
    r = client.post(f"/schedules/definitions/{plain.definition.id}/resume", json={"backlog": "post"})  # match the resume route's body
    assert r.status_code == 200, r.text
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == Decimal("183000") - Decimal("8333")


def test_attach_refuses_imported_definition_while_unlocked(client, db_session, plain, monkeypatch):
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    d.moze_id = "moze-1"
    db_session.commit()
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)
    assert _attach(client, d.id, plain.bank, plain.lender).status_code == 409
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert _attach(client, d.id, plain.bank, plain.lender).status_code == 201


def test_update_after_attach_keeps_loan_link(client, db_session, plain):
    _attach(client, plain.definition.id, plain.bank, plain.lender)
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    update = {k: body[k] for k in ("name", "template", "interval_unit", "interval_n", "anchor_date", "day_of_month", "times", "end_date", "total_amount")}
    update["template"] = {"lines": [{k: v for k, v in line.items() if k in ("kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount", "currency", "loan_entry_id", "name", "merchant")} for line in body["template"]["lines"]], "description": body["template"].get("description"), "tags": body["template"].get("tags", [])}
    update["name"] = "信貸（改名）"
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=update)
    assert r.status_code == 200, r.text
    assert r.json()["loan_entry_id"] == body["loan_entry_id"]
```

Fixture notes: `seed.definition` / `seed.line` / `seed.instance` exist in `tests/conftest.py` (`Seed`); the posted instances in `plain` need no real entries for the attach tests, but `test_attach_then_posting_settles_the_payable` must post through the real posting path — read `schedule_posting.py`'s public entry point and `test_schedule_posting.py:48` for the call shape and adapt (the assertion stays). `today` is the conftest fixture.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_schedule_attach_loan.py -q`
Expected: FAIL (404 on the route).

- [ ] **Step 3: Implement `attach_loan`, the route, and the `loan_summary` sign fix**

- [ ] **Step 4: Run the new file + `tests/integration/test_schedule_definitions_api.py`, `test_schedule_posting.py`, `test_schedule_links.py`** — all pass (the sign fix may change an existing assertion on `remaining` for a payable: update that assertion to the positive magnitude and say so).

- [ ] **Step 5: Commit**

```bash
git add app/services/schedule_service.py app/services/schedule_read.py app/routers/schedules.py tests/integration/test_schedule_attach_loan.py tests/integration/test_schedule_links.py
git commit -m "feat(schedules): attach a loan payable to an existing installment (balance-neutral)"
```

---

### Task 2: `GET /accounts/debts`

**Files:**
- Modify: `services/accounting-service/app/services/ledger_service.py` (near `open_debt_filter`, ~line 296; uses `latest_rates`, `main_currency`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (`DebtTotalOut`, `DebtsSummaryOut`)
- Modify: `services/accounting-service/app/routers/accounts.py` (route declared BEFORE `GET /{account_id}` at line 28)
- Test: `services/accounting-service/tests/integration/test_accounts_api.py` (append)

**Interfaces:**
- Produces: `debts_summary(db) -> dict` = `{"main_currency": str, "payables": {"open_main": Decimal, "count": int, "unconverted": [{"currency", "amount"}]}, "receivables": {...}}` — open originals from `open_debt_filter()`, per-entry `open_amount` (positive), converted with `latest_rates(db, currencies, main)`; a currency without a rate is summed per currency into `unconverted` and excluded from `open_main`. Schemas `DebtTotalOut {open_main: Decimal, count: int, unconverted: list[CurrencyAmountOut]}` (reuse `CurrencyAmountOut`), `DebtsSummaryOut {main_currency: str, payables: DebtTotalOut, receivables: DebtTotalOut}`. Route `GET /accounts/debts` (READ like `GET /accounts`).

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_accounts_debts_summary(client, db_session, seed):
    bank = seed.account("薪轉")
    jp = seed.account("日幣", currency="JPY")
    lender = seed.counterparty("範例銀行")
    friend = seed.counterparty("朋友")
    seed.entry(bank, "183000", kind="payable", counterparty_id=lender.id, name="信貸")
    seed.entry(bank, "5000", kind="payable", counterparty_id=friend.id, name="借")
    seed.entry(jp, "100000", kind="payable", counterparty_id=lender.id, name="日圓貸")       # no JPY rate cached
    seed.entry(bank, "-1200", kind="receivable", counterparty_id=friend.id, name="代墊")
    closed = seed.entry(bank, "9000", kind="payable", counterparty_id=friend.id, name="已還", is_closed=True)
    db_session.commit()
    body = client.get("/accounts/debts").json()
    assert body["main_currency"] == "TWD"
    assert body["payables"] == {"open_main": "188000.0000", "count": 2, "unconverted": [{"currency": "JPY", "amount": "100000.0000"}]}
    assert body["receivables"] == {"open_main": "1200.0000", "count": 1, "unconverted": []}


def test_debts_summary_lists_unconverted(client, db_session, seed):
    jp = seed.account("日幣", currency="JPY")
    seed.entry(jp, "100000", kind="payable", counterparty_id=seed.counterparty("x").id, name="日圓貸")
    db_session.commit()
    body = client.get("/accounts/debts").json()
    assert body["payables"]["open_main"] == "0.0000" and body["payables"]["unconverted"][0]["currency"] == "JPY"
```

(Adapt the `seed.entry` sign convention to what `test_schedule_posting`'s `loan` fixture uses — a payable is seeded as a positive amount there; the receivable sign follows the same helper. Keep the asserted magnitudes.)

- [ ] **Step 2: Run** `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_accounts_api.py -q -k debts` — FAIL (404/422: `/accounts/debts` resolves to `/{account_id}` today).

- [ ] **Step 3: Implement** (route placed before `/{account_id}`).

- [ ] **Step 4: Run** the whole `test_accounts_api.py` — pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/ledger_service.py app/schemas/ledger.py app/routers/accounts.py tests/integration/test_accounts_api.py
git commit -m "feat(accounts): GET /accounts/debts — open payables/receivables in the main currency"
```

- [ ] **Step 6: Backend gate** — `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/unit tests/integration -q -p no:cacheprovider 2>&1 | tail -3` → 0 failed (record counts).

---

### Task 3: SPA model + service methods

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (near `ScheduleLoanInput` ~line 792, `LoanSchedule` ~874)
- Modify: `frontend/src/app/services/accounting.service.ts` (near `updateScheduleDefinition` ~line 332)
- Test: `frontend/src/app/services/accounting-schedules.service.spec.ts` (append)

**Interfaces:**
- Produces: `DebtTotal {open_main: string; count: number; unconverted: {currency: string; amount: string}[]}`, `DebtsSummary {main_currency: string; payables: DebtTotal; receivables: DebtTotal}`; `AccountingService.getDebts(): Observable<DebtsSummary>` (`GET /api/accounting/accounts/debts`), `attachScheduleLoan(id: number, input: ScheduleLoanInput): Observable<ScheduleDefinition>` (`POST /api/accounting/schedules/definitions/{id}/loan`, through `bump()`).

- [ ] **Step 1: Write the failing specs** — `getDebts hits /accounts/debts`; `attachScheduleLoan posts the loan body and bumps entriesChanged` (HttpTestingController, same style as the file's existing cases).
- [ ] **Step 2: Run** `npx ng test --include='src/app/services/accounting-schedules.service.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(spa): debts summary and attach-loan service methods`.

---

### Task 4: 帳戶 page totals with debts

**Files:**
- Modify: `frontend/src/app/components/accounting/accounts/accounts.ts:148-160`, `accounts.html:60-66`
- Test: `frontend/src/app/components/accounting/accounts/accounts.spec.ts` (near line 265)

**Interfaces:**
- Consumes: Task 3 `getDebts`.
- Produces: `debts = signal<DebtsSummary | null>(null)` loaded in `load()` beside the accounts (`catchError(() => of(null))`); `liabilities = Σ max(0, −balance_main) + Number(debts()?.payables.open_main ?? 0)`; `netWorth = assets − liabilities`; `loanCount = debts()?.payables.count ?? 0`; template: `.totals-line` = `總資產 X · 總負債 Y` + (when `loanCount > 0`) `（含借貸 N 筆）`; a new `.net-worth` line `淨值 Z` rendered only when `debts()` is non-null; `.unconverted-hint` `另有 N 種幣別未換算` when `payables.unconverted.length > 0`; when `debts()` is null the markup is exactly today's.

- [ ] **Step 1: Write the failing specs** — `liabilities include open payables and the 淨值 line` (flush `GET /api/accounting/accounts/debts` with payables `open_main "20000.0000", count 2` → `.totals-line` = `總資產 $14,226 · 總負債 $64,362（含借貸 2 筆）`, `.net-worth` = `淨值 −$50,136`), `shows the unconverted note`, `falls back to the old line when the debts read fails` (existing test at :265 keeps passing by flushing the debts request with an error). Update existing tests minimally with a shared flush helper.
- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/accounts/accounts.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(spa): 帳戶 totals count open payables; 淨值 line`.

---

### Task 5: 掛上貸款 in the schedule sheet + frontend gate

**Files:**
- Modify: `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.{ts,html,scss}`
- Test: `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.spec.ts` (append)

**Interfaces:**
- Consumes: Task 3 `attachScheduleLoan`, `getCounterparties()`; the sheet's `definition`, `busy`, `error`, `today` signals and `scheduleActionError` pattern (`schedule-sheet.ts:40-60, 137-155`).
- Produces: `canAttachLoan = computed(() => d.kind === 'installment' && d.loan_entry_id === null && d.status !== 'ended')`; a button `.sheet-attach-loan` 「掛上貸款」 in the actions block; `attachOpen = signal(false)`; inline form (`.loan-form`): hint line 「輸入到上一期為止還欠多少；若有逾期未補的期數先補記」, 對象 `<select>` from `getCounterparties()` (required), 剩餘本金 `<input inputmode="decimal">` (required, > 0), 起算日 `<input type="date">` default `today()`, 名稱 default `d.name`; 取消 / 確定 (確定 disabled until 對象 + amount valid); `attach()` → `attachScheduleLoan(d.id, {account_id: d.template.lines[loanLineIndex].account_id, counterparty_id, category_id: null, amount, entry_date, name})` → on success reload the definition (existing load) and close the form; errors → `error` signal via a local map: `definition_ended` 「排程已結束」, `loan_already_attached` 「已經有貸款了」, `catch_up_first` 「先補記逾期的期數」, 422 messages verbatim (`detail[0].msg`), else `scheduleActionError`. The repayment line index = first line with `kind === 'repayment'`.
- A summary line when `d.loan_entry_id !== null`: `已掛上貸款` (the remaining amount is on the payable's detail; keep the sheet simple).

- [ ] **Step 1: Write the failing specs** — `shows 掛上貸款 only for an installment without a loan that is not ended` (three definitions), `posts the loan with the repayment line's account and hides the button after success`, `maps catch_up_first to 先補記逾期的期數`, `確定 disabled without a counterparty or amount`.
- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass; then the frontend gate `npx ng test 2>&1 | tail -5` (0 failed) and `npx ng build 2>&1 | tail -3`.
- [ ] **Step 5: Commit** `feat(spa): 掛上貸款 in the schedule sheet`.

---

## Rollout notes (not tasks)

API deploy (homehub-api checkout → restart; no migration) → SPA release → the owner opens 🔔 借還款追蹤 › 排程 for each 信貸 (40 / 43 / 46), 補記 any overdue period first, then 掛上貸款 with the bank and the remaining principal as of the last posted period.
