# Loan Liabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a loan payable to an existing installment schedule (balance-neutral), and count open payables as 負債 on the 帳戶 page.

**Architecture:** Backend: (1) a loan-kind schedule line without an applicable loan posts as a plain expense (validation + posting + a `loan_from_seq` boundary key — also the fix for the imported 信貸 definitions that would fail to post from 2026-10-25); (2) `schedule_service.attach_loan` + `POST /schedules/definitions/{id}/loan` with the remaining-principal series rule and additive detail fields; (3) `ledger_service.debts_summary` + `GET /accounts/debts`. SPA: service/model methods; 帳戶 totals with 淨值; a 掛上貸款 form in the schedule sheet. No migration, no new tables.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy (accounting-service), Angular 21 signals / Vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-loan-liabilities-design.md` (rev 4). Plan rev 3 folds in the AGENT-227 review (Musts 1–4, Shoulds 1–4, nit) and the AGENT-228 re-check (M1 boundary lifecycle, M2 plain-entry write path).

## Global Constraints

- Worktree `/home/opc/workspace/home-hub-loans`, branch `feat/loan-liabilities` (from main 616f3d4). Never `git add -A`; `frontend/node_modules`, `frontend/src/environments`, `services/accounting-service/.venv` are symlinks, `.env` is local — never add them.
- Backend tests: from `services/accounting-service`, `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest <file> -q`; never two accounting files concurrently. Frontend: from `frontend`, `npx ng test --include='<spec>'` (rerun once on "could not determine executable"); `npx ng build 2>&1 | tail -3`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Attach never changes `times`, `total_amount`, `first_seq`, `end_date`, instances (every `amount_override` stays) or `generated_until`; it adds exactly two entries (payable +X, balance_adjustment −X, same `entry_date` and `posted_date`) and sets `loan_entry_id` + `loan_from_seq` on the `repayment` line only.
- A loan-kind line with no applicable loan (`loan_entry_id` None, or `instance.seq < loan_from_seq`) posts a plain `expense` (`income` for collection) — in post, repost and reopen→post alike; never a settlement. The plain entry is built like the `interest` branch (direct `LedgerEntry`, `category_id=None`, counterparty kept, `source=SOURCE`), never through `prepare_entry`.
- `loan_from_seq` survives edits: server side (`update_definition` copies the stored boundary when the same loan comes back without it) and client side (`schedule-save.ts` `LINE_INPUT_KEYS` + `ScheduleLineInput`).
- Error codes/messages verbatim: 422 `loan` 「只有分期可以掛貸款」, 422 `loan` 「排程沒有還款那一行」, 409 `loan_already_attached`, 409 `catch_up_first`, 409 `nothing_pending`, 422 `amount` 「剩餘期數乘以每期金額已達剩餘本金」, 422 `amount` 「超過之後各期本金合計」, 422 `account` 「貸款要掛在還款帳戶」, 409 `definition_ended`; SPA texts: 「排程已結束」「已經有貸款了」「先補記逾期的期數」「沒有待入帳的期數」 and the 422 messages verbatim.
- The loan route and `GET /schedules/definitions/{id}` return `DefinitionDetailOut` (with `pending_principal`, `loan_remaining`); `loan_summary.remaining`/`repaid` are positive magnitudes and the SPA consumers switch to magnitude wording.
- Money/dates in the SPA as strings; `LoanIn.entry_date` is required by the API (the SPA defaults it to today). `count` in `/accounts/debts` counts every open original, converted or not.
- Synthetic test data only.

## Review Focus

0. Definition 40's next auto-post is due 2026-10-25 (46 on 10-26, 43 on 11-08) with a loan-less repayment line: today `validate_template` refuses it at posting time; Task 1 makes it post as a plain expense → `test_unlinked_repayment_posts_as_expense` (and the daily poster must not need a restart beyond the API deploy).
1. A loan in a foreign currency (JPY schedule): `debts_summary` lists it under `unconverted` when no rate exists and the 帳戶 page says so instead of silently dropping it → Task 2 (`test_debts_summary_lists_unconverted`), Task 4 (`shows the unconverted note`).
2. A schedule whose repayment line's account has a different currency from `LoanIn.account_id`'s: refused before anything is written → Task 1 (`test_attach_loan_refuses_other_account`).
3. A paused schedule: attach allowed (only `ended` is refused) and the next resume/post settles the payable → Task 1 (`test_attach_loan_on_paused_schedule_then_resume_settles`).
4. The attach-day pair must not change the account balance to the cent in a non-TWD account either → Task 1 asserts `account_balance` before == after.
5. `update_definition` after an attach keeps `loan_entry_id` and still passes `_check_total` → Task 1 (`test_update_after_attach_keeps_loan_link`).

---

## File structure

Backend (`services/accounting-service/`):
- `app/services/schedule_templates.py` (`LINE_KEYS`, `_check_loan`, `validate_template`, `normalize_template`), `app/services/schedule_posting.py` (loan-kind branch), `app/schemas/schedules.py` (`TemplateLineIn/Out.loan_from_seq`, `DefinitionDetailOut.pending_principal/loan_remaining`) — Task 1.
- `app/services/schedule_service.py` — `attach_loan` (Task 2); `app/services/schedule_read.py` — `pending_principal`, `loan_summary` magnitudes (Task 2); `app/routers/schedules.py` — the loan route (Task 2).
- `app/services/ledger_service.py` — `debts_summary` (Task 3); `app/schemas/ledger.py` — `DebtTotalOut`, `DebtsSummaryOut` (Task 3); `app/routers/accounts.py` — `GET /accounts/debts` (Task 3).
- Tests: `tests/integration/test_schedule_unlinked_loan_line.py` (Task 1), `tests/integration/test_schedule_attach_loan.py` (Task 2), `tests/integration/test_accounts_api.py` (Task 3, appended).

Frontend (`frontend/src/app/`):
- `models/accounting.model.ts`, `services/accounting.service.ts` (+ `services/accounting-schedules.service.spec.ts`), `entry-detail/entry-detail.ts` + `entry-detail-schedule.spec.ts`, `reminders/schedule-queue.ts` (+ spec) — magnitude wording (Task 4).
- `components/accounting/accounts/accounts.{ts,html,spec.ts}` (Task 5).
- `components/accounting/reminders/schedule-sheet/schedule-sheet.{ts,html,scss,spec.ts}` (Task 6).

---

### Task 1: Loan-kind lines without a loan post as plain expenses; `loan_from_seq`

**Files:**
- Modify: `services/accounting-service/app/services/schedule_templates.py` (`LINE_KEYS` line 15-18, `_check_loan` ~63-75, `validate_template`, `normalize_template`)
- Modify: `services/accounting-service/app/services/schedule_posting.py` (the `kind in LOAN_LINE_KINDS` branch ~152-163 and the loan lookup in `post_locked` ~205-236)
- Modify: `services/accounting-service/app/schemas/schedules.py` (`TemplateLineIn`, `TemplateLineOut`: `loan_from_seq: Int32 | None = None`)
- Test: `services/accounting-service/tests/integration/test_schedule_unlinked_loan_line.py` (new)

**Interfaces:**
- Produces:
  - Template line key `loan_from_seq` (int ≥ 1 or None), carried by `LINE_KEYS`/`normalize_template`, accepted/emitted by the schemas; `validate_template` refuses it on a non-loan-kind line (`ValidationError(field, "只有還款／收款那一行可以有起算期")`) and refuses `< 1`.
  - `_check_loan` is called only when `line["loan_entry_id"] is not None`; a loan-kind line with `None` is valid.
  - `schedule_posting`: `loan_applies(line: dict, seq: int) -> bool` = `line["loan_entry_id"] is not None and seq >= (line.get("loan_from_seq") or 1)`. In `post_locked`, the loan is looked up / locked only when it applies (`locked_loan` handling in `repost_instance` likewise, `schedule_service.py:489-516`); when it does NOT apply the loan-kind branch writes the plain entry exactly like the `interest` branch (`schedule_posting.py:165-176`): `LedgerEntry(account_id, currency=account.currency, kind="expense" (repayment) | "income" (collection), amount=-amount | +amount, entry_date=due, entry_time=None, posted_date=due, category_id=None, project_id=line["project_id"], name=name, merchant=line["merchant"], counterparty_id=line["counterparty_id"], description=description, tags=tags, source=SOURCE)` + `db.add` + `db.flush()` — NOT `prepare_entry` (it refuses a counterparty on expense/income and `system_category_id` has no "expense"); the repayment line's own category (a payable-kind category) is deliberately not copied. `reopen_instance` (`:474`) then `post` goes through the same branch.

- [ ] **Step 1: Write the failing tests**

```python
"""A repayment line without an applicable loan posts a plain expense (spec §4.0). Synthetic data only."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import LedgerEntry, ScheduleInstance
from app.services import ledger_service
from app.services.settlement_service import open_amount


def _post_next(client, definition_id):
    """Post the earliest pending instance through the public route the sheet uses (read routers/schedules.py: the
    instance `post` route) and return the response."""
    body = client.get(f"/schedules/definitions/{definition_id}").json()
    nxt = next(i for i in body["instances"] if i["status"] == "pending")
    return client.post(f"/schedules/instances/{nxt['id']}/post")  # adapt the path to the real route


def test_unlinked_repayment_posts_as_expense(client, db_session, seed, today):
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    d = seed.definition([seed.line("repayment", bank, "8333", counterparty_id=lender.id), seed.line("interest", bank, "620")],
                        kind="installment", name="信貸", anchor=date(2026, 10, 8), times=36, total_amount=Decimal("300000"))
    db_session.commit()
    before = ledger_service.account_balance(db_session, bank.id)
    r = _post_next(client, d.id)
    assert r.status_code in (200, 201), r.text
    db_session.expire_all()
    rows = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == bank.id).order_by(LedgerEntry.id)).all()
    kinds = sorted(e.kind for e in rows)
    assert kinds == ["expense", "interest"]
    exp = next(e for e in rows if e.kind == "expense")
    assert (exp.amount, exp.counterparty_id, exp.is_settlement, exp.settles_entry_id, exp.category_id, exp.source) == (Decimal("-8333"), lender.id, False, None, None, "schedule")
    assert exp.group_id is not None and exp.group_id == next(e for e in rows if e.kind == "interest").group_id
    assert ledger_service.account_balance(db_session, bank.id) == before - Decimal("8333") - Decimal("620")


def test_loan_from_seq_boundary_repost_and_reopen(client, db_session, seed, today):
    """Periods before the boundary never touch the loan, even on repost / reopen→post; periods at/after settle it."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="500000")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "183000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 9))
    d = seed.definition([seed.line("repayment", bank, "8333", loan_entry_id=payable.id, loan_from_seq=15), seed.line("interest", bank, "620")],
                        kind="installment", name="信貸", anchor=date(2025, 9, 8), times=36, total_amount=Decimal("300000"))
    seed.instance(d, 14, date(2026, 10, 8), status="pending")   # a period BEFORE the boundary, still unposted
    seed.instance(d, 15, date(2026, 11, 8), status="pending")
    db_session.commit()
    # seq 14 → plain expense, loan untouched
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    # repost 14 with a changed amount → still no settlement
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/repost", json={"amounts": ["8000", "620"]})  # adapt body to the real route
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    # reopen 14 then post again → still a plain expense, loan untouched
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/reopen")   # adapt to the real route
    assert r.status_code in (200, 201), r.text
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    # seq 15 → settles
    today(date(2026, 11, 8))
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 15)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000") - Decimal("8333")


def test_unlinked_collection_posts_as_income_and_payable_category_is_not_copied(client, db_session, seed, today):
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    friend = seed.counterparty("朋友")
    cat = seed.category("借出", kind="receivable")
    d = seed.definition([seed.line("collection", bank, "500", counterparty_id=friend.id, category_id=cat.id)],
                        kind="installment", name="收回", anchor=date(2026, 10, 8), times=3)
    db_session.commit()
    r = _post_next(client, d.id)
    assert r.status_code in (200, 201), r.text
    inc = db_session.scalar(select(LedgerEntry).where(LedgerEntry.account_id == bank.id))
    assert (inc.kind, inc.amount, inc.counterparty_id, inc.category_id) == ("income", Decimal("500"), friend.id, None)


def _instance_id(db, definition_id, seq):
    db.expire_all()
    return db.scalar(select(ScheduleInstance.id).where(ScheduleInstance.definition_id == definition_id, ScheduleInstance.seq == seq))


def test_template_rules_for_loan_from_seq(client, db_session, seed):
    bank = seed.account("薪轉")
    db_session.commit()
    base = {"kind": "installment", "name": "x", "interval_unit": "month", "anchor_date": "2026-11-08", "times": 12}
    bad = client.post("/schedules/definitions", json={**base, "template": {"lines": [{"kind": "expense", "account_id": bank.id, "amount": "10", "currency": bank.currency, "loan_from_seq": 3}]}})
    assert bad.status_code == 422 and "起算期" in bad.text
    ok = client.post("/schedules/definitions", json={**base, "template": {"lines": [{"kind": "repayment", "account_id": bank.id, "amount": "10", "currency": bank.currency}]}})
    assert ok.status_code == 201, ok.text   # a repayment line without a loan is valid now
    assert ok.json()["template"]["lines"][0]["loan_entry_id"] is None
```

Read `routers/schedules.py` for the exact instance `post` / `repost` routes and bodies and `tests/integration/test_schedule_posting.py` for how posts are driven; adapt the calls, keep the assertions. `seed.line(..., loan_from_seq=15)` must pass through `normalize_template` — extend `Seed.line` if it whitelists keys.

- [ ] **Step 2: Run** `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_schedule_unlinked_loan_line.py -q` — FAIL (`貸款記錄不存在或類型不符` / unknown key).
- [ ] **Step 3: Implement** (templates, posting, schemas).
- [ ] **Step 4: Run** the new file + `test_schedule_templates.py`, `test_schedule_posting.py`, `test_schedule_definitions_api.py` — pass (an existing test asserting the old refusal of a loan-less repayment line is updated and named in the report).
- [ ] **Step 5: Commit**

```bash
git add app/services/schedule_templates.py app/services/schedule_posting.py app/schemas/schedules.py tests/integration/test_schedule_unlinked_loan_line.py tests/integration/test_schedule_templates.py
git commit -m "fix(schedules): a repayment line without an applicable loan posts a plain expense; loan_from_seq boundary"
```

---

### Task 2: `attach_loan` service + route + detail fields + magnitudes

**Files:**
- Modify: `services/accounting-service/app/services/schedule_service.py` (after `update_definition`, ~line 205)
- Modify: `services/accounting-service/app/services/schedule_read.py` (`loan_summary` ~100-112; new `pending_principal`; detail builder)
- Modify: `services/accounting-service/app/schemas/schedules.py` (`DefinitionDetailOut.pending_principal: Decimal | None`, `loan_remaining: Decimal | None`)
- Modify: `services/accounting-service/app/routers/schedules.py` (after `put_definition`, ~line 82)
- Test: `services/accounting-service/tests/integration/test_schedule_attach_loan.py` (new); `tests/integration/test_schedule_links.py` (magnitude assertion)

**Interfaces:**
- Consumes: Task 1 (`loan_from_seq`, `loan_applies`), `create_definition`'s loan branch (:76-104), `update_definition`'s guards (:128-150), `schedule_templates.loan_line`, `validate_template`, `settlement_service.open_amount`, `entry_write_service.create_balance_adjustment` (:676 — mirror its row construction with an EXPLICIT amount, same `entry_date` and `posted_date` as the payable), `ScheduleInstance` (seq, status, due_date, amount_override), `rules`/`schedule_generation` only to know `times`.
- Produces:
  - `remaining_principal_series(db, definition) -> list[Decimal]` in `schedule_read.py`: for `seq` from the lowest pending seq (or `max_seq + 1` when none pending) to `times`: the repayment line's amount — the instance's `amount_override[line_index]` when that instance exists with an override, else `Decimal(template line amount)`; `[]` when `times` is None or no repayment line. `pending_principal(db, definition) -> Decimal | None` = its sum (None for `[]`).
  - `attach_loan(db: Session, definition_id: int, loan: LoanIn) -> int` (payable id), in this order: import key shared → `lock_definition` → `moze_id` guard → `kind != "installment"` → 422 `loan` 「只有分期可以掛貸款」 → `status == "ended"` → 409 `definition_ended` → no `repayment` line → 422 `loan` 「排程沒有還款那一行」 → line has `loan_entry_id` → 409 `loan_already_attached` → `loan.account_id != line["account_id"]` → 422 `account` 「貸款要掛在還款帳戶」 → no pending instance → 409 `nothing_pending` → any pending `due_date <= loan.entry_date` → 409 `catch_up_first` → `series = remaining_principal_series(...)`; `loan.amount > sum(series)` → 422 `amount` 「超過之後各期本金合計」; `loan.amount <= sum(series[:-1])` → 422 `amount` 「剩餘期數乘以每期金額已達剩餘本金」 → insert the payable (as `create_definition`), insert the `balance_adjustment` (−amount, same account/date/posted_date, name `掛帳調整：<loan.name or definition.name>`) → `line["loan_entry_id"] = payable_id`, `line["loan_from_seq"] = lowest pending seq`, `definition.template = template`, `template_owner_edited = True` → `validate_template` → `db.flush()`.
  - Route `POST /schedules/definitions/{definition_id}/loan` (body `LoanIn`, `response_model=DefinitionDetailOut`, 201, `service_errors()` + `db.commit()` + the same detail serializer `GET /definitions/{id}` uses; write scope like the other write routes).
  - `update_definition` (`schedule_service.py:128-205`, where the new template replaces the stored one ~:135/:171): for the loan line, if `new["loan_entry_id"] == old["loan_entry_id"] is not None` and `new.get("loan_from_seq") is None`, set `new["loan_from_seq"] = old.get("loan_from_seq")` before `validate_template` (M1 server-side guard).
  - `loan_summary`: `remaining = open_amount` (positive), `repaid` unchanged; `DefinitionDetailOut.loan_remaining` = that open amount when linked.

- [ ] **Step 1: Write the failing tests**

```python
"""Attach a loan to an existing installment schedule (spec §4.1). Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import ledger_service
from app.services.settlement_service import open_amount

# 36 monthly periods on the 8th from 2025-09-08: seq 1 = 2025-09-08 … seq 14 = 2026-10-08, seq 15 = 2026-11-08 …
DUE = [date(2025 + (8 + i) // 12, (8 + i) % 12 + 1, 8) for i in range(36)]
assert DUE[0] == date(2025, 9, 8) and DUE[13] == date(2026, 10, 8) and DUE[14] == date(2026, 11, 8) and DUE[35] == date(2028, 8, 8)


@pytest.fixture()
def plain(seed, db_session, today):
    """A 36-period installment with 14 posted periods and NO loan, like the imported 信貸 definitions; per-period
    overrides like MOZE's amortisation (principal 8000+seq, interest 1000-seq)."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    definition = seed.definition(
        [seed.line("repayment", bank, "8333"), seed.line("interest", bank, "620")],
        kind="installment", name="信貸", anchor=date(2025, 9, 8), times=36, total_amount=Decimal("300000"),
    )
    for seq in range(1, 37):
        seed.instance(definition, seq, DUE[seq - 1], status="posted" if seq <= 14 else "pending",
                      amount_override=[str(8000 + seq), str(1000 - seq)])
    db_session.commit()
    pending_principal = sum(Decimal(8000 + seq) for seq in range(15, 37))  # 22 periods
    return SimpleNamespace(bank=bank, lender=lender, definition=definition, due_total=pending_principal)


def _attach(client, definition_id, bank, lender, amount, entry_date="2026-10-09", **extra):
    body = {"account_id": bank.id, "counterparty_id": lender.id, "amount": str(amount), "entry_date": entry_date, "name": "信貸"}
    body.update(extra)
    return client.post(f"/schedules/definitions/{definition_id}/loan", json=body)


def test_detail_exposes_pending_principal(client, plain):
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    assert Decimal(body["pending_principal"]) == plain.due_total and body["loan_remaining"] is None


def test_attach_loan_links_and_is_balance_neutral(client, db_session, plain):
    before = ledger_service.account_balance(db_session, plain.bank.id)
    res = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    assert res.status_code == 201, res.text
    body = res.json()
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, plain.bank.id) == before
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    adjust = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment"))
    assert payable.amount == plain.due_total and adjust.amount == -plain.due_total and adjust.account_id == plain.bank.id
    assert adjust.entry_date == adjust.posted_date == payable.entry_date == payable.posted_date == date(2026, 10, 9)
    assert adjust.name == "掛帳調整：信貸"
    line0, line1 = body["template"]["lines"]
    assert line0["loan_entry_id"] == payable.id == body["loan_entry_id"] and line0["loan_from_seq"] == 15
    assert line1["loan_entry_id"] is None and line1.get("loan_from_seq") is None
    assert Decimal(body["loan_remaining"]) == plain.due_total
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    assert (d.times, d.total_amount, d.first_seq, d.template_owner_edited) == (36, Decimal("300000"), 1, True)
    overrides = db_session.scalars(select(ScheduleInstance.amount_override).where(ScheduleInstance.definition_id == d.id)).all()
    assert all(o is not None for o in overrides) and len(overrides) == 36   # the per-period split survived
    detail = client.get(f"/entries/{payable.id}").json()
    assert Decimal(detail["loan_schedule"]["remaining"]) == plain.due_total and Decimal(detail["loan_schedule"]["repaid"]) == 0


def test_attach_then_posting_settles_with_the_period_principal(client, db_session, plain, today):
    _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    today(date(2026, 11, 8))
    nxt = db_session.scalar(select(ScheduleInstance.id).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.seq == 15))
    r = client.post(f"/schedules/instances/{nxt}/post")  # adapt to the real instance post route
    assert r.status_code in (200, 201), r.text
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == plain.due_total - Decimal(8015)
    assert Decimal(client.get(f"/entries/{payable.id}").json()["loan_schedule"]["repaid"]) == Decimal(8015)


def test_attach_amount_rules(client, db_session, plain):
    d, bank, lender = plain.definition, plain.bank, plain.lender
    last = Decimal(8036)
    too_much = _attach(client, d.id, bank, lender, plain.due_total + 1)
    assert too_much.status_code == 422 and "超過之後各期本金合計" in too_much.text
    too_little = _attach(client, d.id, bank, lender, plain.due_total - last)
    assert too_little.status_code == 422 and "剩餘期數乘以每期金額已達剩餘本金" in too_little.text
    early = _attach(client, d.id, bank, lender, plain.due_total - last + 1)   # ends in the last period with a smaller final payment
    assert early.status_code == 201, early.text


def test_series_counts_periods_beyond_the_horizon(client, db_session, seed, today):
    """Only 3 of 24 periods are generated; the rule still counts all 24."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    d = seed.definition([seed.line("repayment", bank, "1000")], kind="installment", name="短", anchor=date(2026, 11, 8), times=24, total_amount=Decimal("24000"))
    for seq in range(1, 4):
        seed.instance(d, seq, date(2026, 10 + seq, 8) if seq <= 2 else date(2027, 1, 8), status="pending")
    db_session.commit()
    assert Decimal(client.get(f"/schedules/definitions/{d.id}").json()["pending_principal"]) == Decimal(24000)
    assert _attach(client, d.id, bank, lender, 24000).status_code == 201


def test_attach_refusals(client, db_session, plain, seed):
    bank, lender, d = plain.bank, plain.lender, plain.definition
    other = seed.account("別戶")
    coll = seed.definition([seed.line("collection", bank, "100")], kind="installment", name="收", anchor=date(2026, 11, 8), times=3)
    rec = seed.definition([seed.line("expense", bank, "100")], kind="recurring", name="月費", anchor=date(2026, 11, 1))
    db_session.commit()
    assert _attach(client, d.id, other, lender, plain.due_total).status_code == 422                 # 貸款要掛在還款帳戶
    r = _attach(client, coll.id, bank, lender, 300); assert r.status_code == 422 and "排程沒有還款那一行" in r.text
    r = _attach(client, rec.id, bank, lender, 100); assert r.status_code == 422 and "只有分期可以掛貸款" in r.text
    assert _attach(client, d.id, bank, lender, plain.due_total).status_code == 201
    again = _attach(client, d.id, bank, lender, plain.due_total)
    assert again.status_code == 409 and "loan_already_attached" in again.text


def test_catch_up_then_attach_end_to_end(client, db_session, plain, today):
    today(date(2026, 12, 20))                                   # seq 15 (11-08) and 16 (12-08) are overdue
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total, entry_date="2026-12-20")
    assert r.status_code == 409 and "catch_up_first" in r.text
    for seq in (15, 16):                                        # 補記: plain expenses (Task 1)
        iid = db_session.scalar(select(ScheduleInstance.id).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.seq == seq))
        assert client.post(f"/schedules/instances/{iid}/post").status_code in (200, 201)
    remaining = plain.due_total - Decimal(8015) - Decimal(8016)
    assert Decimal(client.get(f"/schedules/definitions/{plain.definition.id}").json()["pending_principal"]) == remaining
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, remaining, entry_date="2026-12-20")
    assert r.status_code == 201, r.text
    assert r.json()["template"]["lines"][0]["loan_from_seq"] == 17
    assert db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable")) is not None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.is_settlement.is_(True))).all() == []


def test_attach_refuses_ended_and_nothing_pending(client, db_session, plain, seed, today):
    client.post(f"/schedules/definitions/{plain.definition.id}/end")
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    assert r.status_code == 409 and "definition_ended" in r.text
    bank, lender = plain.bank, plain.lender
    d = seed.definition([seed.line("repayment", bank, "100")], kind="installment", name="無待入帳", anchor=date(2026, 1, 8), times=2)
    seed.instance(d, 1, date(2026, 1, 8), status="posted"); seed.instance(d, 2, date(2026, 2, 8), status="posted")
    db_session.commit()
    r = _attach(client, d.id, bank, lender, 100)
    assert r.status_code == 409 and "nothing_pending" in r.text


def test_attach_loan_on_paused_schedule_then_resume_settles(client, db_session, plain, today):
    client.post(f"/schedules/definitions/{plain.definition.id}/pause")
    assert _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total).status_code == 201
    today(date(2026, 11, 9))
    r = client.post(f"/schedules/definitions/{plain.definition.id}/resume", json={"backlog": "post"})  # match the resume route's body
    assert r.status_code == 200, r.text
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == plain.due_total - Decimal(8015)


def test_attach_refuses_imported_definition_while_unlocked(client, db_session, plain, monkeypatch):
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    d.moze_id = "moze-1"
    db_session.commit()
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)
    assert _attach(client, d.id, plain.bank, plain.lender, plain.due_total).status_code == 409
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert _attach(client, d.id, plain.bank, plain.lender, plain.due_total).status_code == 201


def test_update_after_attach_keeps_loan_link(client, db_session, plain):
    _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    keep = ("kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount", "currency", "loan_entry_id", "loan_from_seq", "name", "merchant")
    update = {k: body[k] for k in ("name", "template", "interval_unit", "interval_n", "anchor_date", "day_of_month", "times", "end_date", "total_amount")}
    update["template"] = {"lines": [{k: v for k, v in line.items() if k in keep} for line in body["template"]["lines"]], "description": body["template"].get("description"), "tags": body["template"].get("tags", [])}
    update["name"] = "信貸（改名）"
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=update)
    assert r.status_code == 200, r.text
    assert r.json()["loan_entry_id"] == body["loan_entry_id"] and r.json()["template"]["lines"][0]["loan_from_seq"] == 15
    # a client that does not know the key (sends the same loan without loan_from_seq) must not erase the boundary
    naked = {**update, "name": "信貸（再改）"}
    naked["template"] = {**update["template"], "lines": [{k: v for k, v in line.items() if k != "loan_from_seq"} for line in update["template"]["lines"]]}
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=naked)
    assert r.status_code == 200, r.text
    assert r.json()["template"]["lines"][0]["loan_from_seq"] == 15
    # existing behaviour, recorded not fixed here: the rename regenerates future pending instances and flattens their
    # per-period overrides (see spec §5) — assert what happens so the follow-up has a baseline:
    overrides = db_session.scalars(select(ScheduleInstance.amount_override).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.status == "pending")).all()
    print("pending overrides after rename:", sum(o is not None for o in overrides), "of", len(overrides))
```

- [ ] **Step 2: Run** `PYTHONPATH=../shared-python-lib .venv/bin/python -m pytest tests/integration/test_schedule_attach_loan.py -q` — FAIL (404).
- [ ] **Step 3: Implement** `remaining_principal_series`, `pending_principal`, `attach_loan`, the route, the detail fields, the `loan_summary` magnitudes (update `test_schedule_links.py`'s signed expectation and name it in the report).
- [ ] **Step 4: Run** the new file + `test_schedule_definitions_api.py`, `test_schedule_posting.py`, `test_schedule_links.py`, `test_schedule_unlinked_loan_line.py` — pass.
- [ ] **Step 5: Commit**

```bash
git add app/services/schedule_service.py app/services/schedule_read.py app/schemas/schedules.py app/routers/schedules.py tests/integration/test_schedule_attach_loan.py tests/integration/test_schedule_links.py
git commit -m "feat(schedules): attach a loan payable to an existing installment from a period boundary (balance-neutral)"
```

---

### Task 3: `GET /accounts/debts`

**Files:**
- Modify: `services/accounting-service/app/services/ledger_service.py` (near `open_debt_filter`, ~line 296; uses `latest_rates`, `main_currency`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (`DebtTotalOut`, `DebtsSummaryOut`)
- Modify: `services/accounting-service/app/routers/accounts.py` (route declared BEFORE `GET /{account_id}` at line 28)
- Test: `services/accounting-service/tests/integration/test_accounts_api.py` (append)

**Interfaces:**
- Produces: `debts_summary(db) -> dict` = `{"main_currency": str, "payables": {"open_main": Decimal, "count": int, "unconverted": [{"currency", "amount"}]}, "receivables": {...}}` — open originals from `open_debt_filter()`, per-entry `open_amount` (positive), converted with `latest_rates(db, currencies, main)`; a currency without a rate is summed per currency into `unconverted` and excluded from `open_main`; `count` counts EVERY open original (converted or not). Schemas `DebtTotalOut {open_main: Decimal, count: int, unconverted: list[CurrencyAmountOut]}` (reuse `CurrencyAmountOut`), `DebtsSummaryOut {main_currency: str, payables: DebtTotalOut, receivables: DebtTotalOut}`. Route `GET /accounts/debts` (READ like `GET /accounts`).

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
    settled = seed.entry(bank, "3000", kind="payable", counterparty_id=friend.id, name="全還")      # fully settled → excluded
    seed.entry(bank, "-3000", kind="payable", settles_entry_id=settled.id, is_settlement=True, name="還款")
    partial = seed.entry(bank, "10000", kind="payable", counterparty_id=friend.id, name="還一半")    # open part 6000
    seed.entry(bank, "-4000", kind="payable", settles_entry_id=partial.id, is_settlement=True, name="還款")
    db_session.commit()
    body = client.get("/accounts/debts").json()
    assert body["main_currency"] == "TWD"
    assert body["payables"] == {"open_main": "194000.0000", "count": 4, "unconverted": [{"currency": "JPY", "amount": "100000.0000"}]}
    assert body["receivables"] == {"open_main": "1200.0000", "count": 1, "unconverted": []}


def test_debts_summary_lists_unconverted(client, db_session, seed):
    jp = seed.account("日幣", currency="JPY")
    seed.entry(jp, "100000", kind="payable", counterparty_id=seed.counterparty("x").id, name="日圓貸")
    db_session.commit()
    body = client.get("/accounts/debts").json()
    assert body["payables"]["open_main"] == "0.0000" and body["payables"]["count"] == 1 and body["payables"]["unconverted"][0]["currency"] == "JPY"


def test_debts_summary_converts_with_a_cached_rate(client, db_session, seed):
    """Copy the rate fixture from test_accounts_api.py::test_balance_main_uses_the_latest_cached_rate (line ~76)."""
    jp = seed.account("日幣", currency="JPY")
    seed.entry(jp, "100000", kind="payable", counterparty_id=seed.counterparty("x").id, name="日圓貸")
    # cache JPY→TWD = 0.2 the way the balance_main test does
    db_session.commit()
    body = client.get("/accounts/debts").json()
    assert body["payables"]["open_main"] == "20000.0000" and body["payables"]["unconverted"] == [] and body["payables"]["count"] == 1
```

(Signs: a payable is seeded positive as in `test_schedule_posting`'s `loan` fixture and its settlements negative; the receivable the other way round — read `settlement_service.settle` / `seed.entry` and keep the asserted magnitudes. If `seed.entry` cannot write a settlement row directly, use `settlement_service.settle` as the posting path does.)

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

### Task 4: SPA model + service methods + magnitude wording

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (near `ScheduleLoanInput` ~line 792, `LoanSchedule` ~874)
- Modify: `frontend/src/app/services/accounting.service.ts` (near `updateScheduleDefinition` ~line 332)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (~line 129) + `entry-detail-schedule.spec.ts` (~line 96), `frontend/src/app/components/accounting/reminders/schedule-queue.ts` (~line 117) + its spec — `loan_schedule.remaining`/`repaid` are now positive magnitudes: render `欠 <formatMoney(remaining)>` for a payable and `待收 <…>` for a receivable (kind from the entry), no leading minus.
- Modify: `frontend/src/app/components/accounting/entry-form/schedule-save.ts:131-134` — add `'loan_from_seq'` to `LINE_INPUT_KEYS` (and `ScheduleLineInput.loan_from_seq?: number | null` in the model) AND exclude it from `formOwnedKeys()` (:136-144, the way `counterparty_id` is excluded on the loan path) — otherwise the built line (which has no `loan_from_seq`) overwrites the loaded value with undefined; test in `schedule-save.spec.ts`: a loaded definition whose repayment line has `loan_entry_id: 9, loan_from_seq: 15` → after a rename the built update payload still carries `loan_from_seq: 15` (M1 client-side).
- Test: `frontend/src/app/services/accounting-schedules.service.spec.ts` (append)

**Interfaces:**
- Produces: `DebtTotal {open_main: string; count: number; unconverted: {currency: string; amount: string}[]}`, `DebtsSummary {main_currency: string; payables: DebtTotal; receivables: DebtTotal}`; `ScheduleLine.loan_from_seq?: number | null`; `ScheduleDefinitionDetail.pending_principal: string | null`, `loan_remaining: string | null`; `AccountingService.getDebts(): Observable<DebtsSummary>` (`GET /api/accounting/accounts/debts`), `attachScheduleLoan(id: number, input: ScheduleLoanInput): Observable<ScheduleDefinitionDetail>` (`POST /api/accounting/schedules/definitions/{id}/loan`, through `bump()`).

- [ ] **Step 1: Write the failing specs** — `getDebts hits /accounts/debts`; `attachScheduleLoan posts the loan body and bumps entriesChanged` (HttpTestingController, same style as the file's existing cases); update `entry-detail-schedule.spec.ts:96` and the schedule-queue spec to the magnitude wording (`欠 $…` / `待收 $…`); `schedule-save.spec.ts`: `keeps loan_from_seq through a rename round trip`.
- [ ] **Step 2: Run** `npx ng test --include='src/app/services/accounting-schedules.service.spec.ts'` and the two detail/queue specs — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(spa): debts summary, attach-loan service method, loan magnitudes`.

---

### Task 5: 帳戶 page totals with debts

**Files:**
- Modify: `frontend/src/app/components/accounting/accounts/accounts.ts:148-160`, `accounts.html:60-66`
- Test: `frontend/src/app/components/accounting/accounts/accounts.spec.ts` (near line 265)

**Interfaces:**
- Consumes: Task 4 `getDebts`.
- Produces: `debts = signal<DebtsSummary | null>(null)` loaded in `load()` beside the accounts (`catchError(() => of(null))`); `liabilities = Σ max(0, −balance_main) + Number(debts()?.payables.open_main ?? 0)`; `netWorth = assets − liabilities`; `loanCount = debts()?.payables.count ?? 0`; template: `.totals-line` = `總資產 X · 總負債 Y` + (when `loanCount > 0`) `（含借貸 N 筆）`; a new `.net-worth` line `淨值 Z` rendered only when `debts()` is non-null; `.unconverted-hint` `另有 N 種幣別未換算` when `payables.unconverted.length > 0`; when `debts()` is null the markup is exactly today's.

- [ ] **Step 1: Write the failing specs** — `liabilities include open payables and the 淨值 line` (flush `GET /api/accounting/accounts/debts` with payables `open_main "20000.0000", count 2` → `.totals-line` = `總資產 $14,226 · 總負債 $64,362（含借貸 2 筆）`, `.net-worth` = `淨值 −$50,136`), `shows the unconverted note`, `falls back to the old line when the debts read fails` (existing test at :265 keeps passing by flushing the debts request with an error). Update existing tests minimally with a shared flush helper.
- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/accounts/accounts.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** `feat(spa): 帳戶 totals count open payables; 淨值 line`.

---

### Task 6: 掛上貸款 in the schedule sheet + frontend gate

**Files:**
- Modify: `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.{ts,html,scss}`
- Test: `frontend/src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.spec.ts` (append)

**Interfaces:**
- Consumes: Task 4 `attachScheduleLoan`, `getCounterparties()`; the sheet's `definition`, `busy`, `error`, `today` signals and `scheduleActionError` pattern (`schedule-sheet.ts:40-60, 137-155`).
- Produces: `repaymentLine = computed(() => d.template.lines.findIndex(l => l.kind === 'repayment'))`; `canAttachLoan = computed(() => d.kind === 'installment' && d.loan_entry_id === null && d.status !== 'ended' && repaymentLine() >= 0)`; a button `.sheet-attach-loan` 「掛上貸款」 in the actions block; `attachOpen = signal(false)`; inline form (`.loan-form`): hint line 「輸入到上一期為止還欠多少；若有逾期未補的期數先補記」, 對象 `<select>` from `getCounterparties()` (required), 剩餘本金 `<input inputmode="decimal">` (required, > 0, prefilled from `d.pending_principal` when present), 起算日 `<input type="date">` default `today()`, 名稱 default `d.name`; 取消 / 確定 (確定 disabled until 對象 + amount valid); `attach()` → `attachScheduleLoan(d.id, {account_id: d.template.lines[loanLineIndex].account_id, counterparty_id, category_id: null, amount, entry_date, name})` → on success reload the definition (existing load) and close the form; errors → `error` signal via a local map: `definition_ended` 「排程已結束」, `loan_already_attached` 「已經有貸款了」, `catch_up_first` 「先補記逾期的期數」, `nothing_pending` 「沒有待入帳的期數」, 422 messages verbatim (`detail[0].msg`), else `scheduleActionError`.
- A summary line when `d.loan_entry_id !== null`: `已掛上貸款：剩餘 <formatMoney(d.loan_remaining, currency)>`.

- [ ] **Step 1: Write the failing specs** — `shows 掛上貸款 only for an installment with a repayment line, no loan, not ended` (four definitions incl. a collection-only one), `prefills 剩餘本金 from pending_principal`, `shows 已掛上貸款：剩餘 after success`, `posts the loan with the repayment line's account and hides the button after success`, `maps catch_up_first to 先補記逾期的期數`, `確定 disabled without a counterparty or amount`.
- [ ] **Step 2: Run** `npx ng test --include='src/app/components/accounting/reminders/schedule-sheet/schedule-sheet.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.** - [ ] **Step 4: Run** — pass; then the frontend gate `npx ng test 2>&1 | tail -5` (0 failed) and `npx ng build 2>&1 | tail -3`.
- [ ] **Step 5: Commit** `feat(spa): 掛上貸款 in the schedule sheet`.

---

## Rollout notes (not tasks)

API deploy (homehub-api checkout → restart; no migration) → SPA release → the owner opens 🔔 借還款追蹤 › 排程 for each 信貸 (40 / 43 / 46), 補記 any overdue period first, then 掛上貸款 with the bank and the remaining principal as of the last posted period.
