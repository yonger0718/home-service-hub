# Partial-settlement backfill guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The policy never books a bank line while a larger, unclaimed owner settlement entry near it could still absorb the line (#389).

**Architecture:** One new predicate in `app/services/stand_ins.py`, `_partial_settlement_twins`, whose ids `owner_twins` adds to its result. Every caller of `owner_twins` / `owner_may_record` picks the new twin shape up; no caller changes.

**Tech Stack:** Python 3.13, SQLAlchemy, pytest (`tests/integration`, PostgreSQL).

**Spec:** `docs/superpowers/specs/2026-11-19-backfill-partial-settlement-guard-design.md`

## Global Constraints

- A partial-settlement twin is an entry of `owner_population` (unclaimed, top-level, not a stand-in) with all of:
  - `is_settlement` true;
  - `matching._entry_class(entry_view(e))` in `matching.eligible_kinds(statement.kind, line.line_kind)`;
  - `abs(entry flow) > abs(line flow)`, strictly;
  - `drift_days(line, e) <= rules.drift_window_days`.
- `owner_twins` keeps returning a sorted list of distinct ids.
- No change to `policy_service`, `ledger_actions`, `transfer_actions`, `statement_ingest_service`, the matcher, settings or schemas.
- A plain owner action (`actor="owner"`, no policy proposal) stays allowed on a held line.
- Fixtures are synthetic.
- Test recipe: from `services/accounting-service`, eval the DB vars plus `OTEL_SERVICE_NAME_ACCOUNTING|OTEL_COLLECTOR_ENDPOINT_GRPC`, then run `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest <paths> -q`.

## Review Focus

- **Window edge.** A receipt 15 days from the line holds it; 16 days does not. Covered by `test_partial_settlement_twin_boundaries`.
- **Not a settlement.** A larger plain `income` entry is no twin (the spec's non-goal). Covered by the same test.
- **Wrong direction.** A larger `settlement_out` entry is no twin of a deposit line. Covered by the same test.
- **Claimed receipt.** A receipt another line already claims holds nothing. Covered by `test_a_claimed_settlement_receipt_holds_no_other_line`.
- **Pending policy proposal.** A proposal made before the receipt existed must be superseded, not applied (`_supersede_reason`). No new test: the path is the shared `owner_may_record` one that the near-twin tests already exercise. The reviewer checks that nothing bypasses it.

---

### Task 1: partial-settlement twins in the owner-twin guard

**Files:**
- Modify: `services/accounting-service/app/services/stand_ins.py` (`_split_twins` area ~:270, `owner_twins` ~:319)
- Test: `services/accounting-service/tests/integration/test_reconciliation_policy.py` (after `test_near_twin_window_boundary_is_the_matchers_near_window`, ~:1897)
- Test: `services/accounting-service/tests/integration/test_reconciliation_api.py` (after `test_case_detail_names_the_owner_twins_the_policy_left_the_line_for`, ~:82)

**Interfaces:**
- Produces: `stand_ins._partial_settlement_twins(statement: AccountStatement, line: StatementLine, population: list[LedgerEntry], rules) -> list[int]`
- `stand_ins.owner_twins(...)` keeps its signature.

- [ ] **Step 1: Add the test helpers** to `test_reconciliation_policy.py`:

```python
def _live_bank(seed, db, name="銀行"):
    bank = seed.account(name, opening="1000", statement_live_from=date(2026, 9, 1))
    db.commit()
    return bank


def _settlement(seed, bank, amount, *, day=DAY):
    """An owner settlement entry on the bank: a receipt of a receivable (positive) or a repayment of a payable."""
    kind = "receivable" if Decimal(amount) > 0 else "payable"
    debt = seed.entry(bank, str(-Decimal(amount)), kind=kind, day=day - timedelta(days=20))
    seed.db.flush()
    return seed.entry(bank, amount, kind=kind, day=day, is_settlement=True, settles_entry_id=debt.id)
```

The debt original is dated 20 days earlier so that it is outside every window the tests use. If `seed` exposes its session under another name, use that.

- [ ] **Step 2: Write the failing tests**

```python
def test_policy_never_backfills_deposits_that_are_parts_of_an_owner_settlement_receipt(seed, db_session, run, dirty_on):
    """#389: a friend repaid 600 in two transfers; the owner recorded one receipt of 600."""
    bank = _live_bank(seed, db_session)
    receipt = _settlement(seed, bank, "600")
    db_session.commit()
    stmt, (a, b), _ = _submit(db_session, run, bank, [_line(1, DAY, "轉入", 400, "deposit"), _line(2, DAY, "轉入", 200, "deposit")],
                              kind="bank", opening=Decimal("1000"))
    for line in (a, b):
        case = _open_line_case(db_session, stmt.id, line.id)
        assert not case.candidates                      # the matcher offers nothing: only the guard keeps the policy off
        _never_books(db_session, stmt, line, case, "backfill_entry")
    assert db_session.get(LedgerEntry, receipt.id) is not None


def test_the_hold_ends_once_the_receipt_is_gone(seed, db_session, run, dirty_on):
    # as above, then db_session.delete(receipt) + its debt, commit, rec.reconcile(stmt.id), commit
    # → _set_policy(_creation_policy("backfill_entry")); len(run_policy(stmt.id).applied) == 2


@pytest.mark.parametrize("amount, kind, days, blocked", [
    ("600", "settlement", 15, True),      # the drift window's edge
    ("600", "settlement", 16, False),
    ("150", "settlement", 0, False),      # smaller than the 200 line
    ("600", "income", 0, False),          # larger, but not a settlement (non-goal)
    ("-600", "settlement", 0, False),     # a repayment going out is no twin of a deposit
])
def test_partial_settlement_twin_boundaries(seed, db_session, run, dirty_on, amount, kind, days, blocked):
    # one 200 deposit line at DAY; the entry at DAY - days: _settlement(...) or seed.entry(bank, amount, kind="income", day=...)
    # blocked → _never_books(..., "backfill_entry"); else → _books_one(db_session, stmt, "backfill_entry")


def test_a_claimed_settlement_receipt_holds_no_other_line(seed, db_session, run, dirty_on):
    # receipt 600; lines: 600 deposit (claims the receipt exactly) and 200 deposit
    # → [c.entry_id for c in _active(stmt.id)] == [receipt.id]; _books_one(..., "backfill_entry") books the 200 line


def test_policy_never_backfills_withdrawals_that_are_parts_of_an_owner_repayment(seed, db_session, run, dirty_on):
    # _settlement(seed, bank, "-600"); lines _line(1, DAY, "轉出", -400, "withdrawal"), _line(2, DAY, "轉出", -200, "withdrawal")
    # → _never_books for both


def test_the_owner_may_still_backfill_a_line_the_guard_holds(seed, db_session, run, dirty_on):
    # the first test's setup; "backfill_entry" in registry.allowed(case);
    # engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "income"}, actor="owner", label="t")
    # → len(_booked(db_session)) == 1
```

In `test_reconciliation_api.py`:

```python
def test_case_detail_names_a_larger_settlement_receipt_as_the_owner_twin(client, seed, db_session, run):
    # bank + receipt 600 + one 200 deposit line → GET /reconciliation/cases/{id} (headers=H) ["owner_twins"] == [receipt.id]
```

- [ ] **Step 3: Confirm the new tests fail.** Run the two files with `-k "settlement_receipt or hold_ends or partial_settlement or owner_repayment or guard_holds or larger_settlement"`. Expected: the `blocked=True` cases, the two "never backfills" tests and the API test FAIL (the policy books the lines; `owner_twins == []`). The `blocked=False` cases, the claimed-receipt test, the hold-ends test and the owner test may already pass: they pin behaviour that must not change.

- [ ] **Step 4: Implement in `app/services/stand_ins.py`:**
  - Add `_partial_settlement_twins(statement, line, population, rules) -> list[int]` after `_split_twins`, with the four conditions of Global Constraints. Read flows the way `_conserves` does (`reconciliation_service._as_line(line)`, `reconciliation_service.entry_view(e)`, local import).
  - In `owner_twins`, union its ids into the returned set (the function already holds `population`).
  - Extend the `owner_twins` docstring with the new shape and `#389`.

- [ ] **Step 5: Run the gates.**
  - `tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_api.py`: all pass.
  - Then the whole accounting suite (`pytest -q`): all pass.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/services/stand_ins.py services/accounting-service/tests/integration/test_reconciliation_policy.py services/accounting-service/tests/integration/test_reconciliation_api.py
git commit -m "fix(reconciliation): hold the policy backfill beside a larger unclaimed settlement receipt (#389)"
```
