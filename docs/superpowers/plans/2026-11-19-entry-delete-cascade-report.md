# Entry delete report and foreign-child hold (#390) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `DELETE /entries/{id}` answers with every row it removed and refuses to remove a child that sits on another account than its parent unless `?cascade=true`.

**Architecture:** `entry_write_service.delete_entry` reads the doomed rows once under its existing locks, runs the foreign-child check, and returns report rows; the router turns them into a response model. The SPA names a foreign child's account in the delete dialog and sends `cascade=true` only then.

**Tech Stack:** FastAPI, SQLAlchemy, pytest (accounting service); Angular, Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-19-entry-delete-cascade-report-design.md`

## Global Constraints

- `delete_entries_cascade` and every other caller of it stay unchanged.
- No refusal because a child is covered by a statement line.
- The existing refusals of `delete_entry` keep their answers and come first.
- Status `200` with the body `{"deleted": [...]}`; the refusal is `409` with the code `cascade_unconfirmed` (error body key `message`, as for every coded conflict).
- Fixtures are fictional.

## Review Focus

- A transfer whose **other** leg has a child on a third account: refused, nothing deleted. (Task 1 test.)
- A foreign child under an entry that another refusal already stops (a reward row without an accrual): the old `409` text, not `cascade_unconfirmed`. (Task 1 test.)
- A refused delete leaves no partial write (reward accrual, group, schedule hooks): the check runs before `forfeit_for_delete` and the cascade. (Task 1: placement; the "still exist" assertions.)
- A child added to the dialog's entry after the SPA loaded it: the server refuses, the SPA shows the text. (Covered by the server test; the SPA test pins that `cascade` is absent when no foreign child was listed.)
- An old SPA against the new API: the body is ignored. (No test; `http.delete<void>` ignores a body.)

---

### Task 1: API report and refusal

**Files:**
- Modify: `services/accounting-service/app/services/entry_write_service.py` (`delete_entry`)
- Modify: `services/accounting-service/app/schemas/ledger.py` (two response models)
- Modify: `services/accounting-service/app/routers/entries.py` (`remove_entry`)
- Test: `services/accounting-service/tests/integration/test_entry_writes.py`
- Modify (mechanical): every test asserting `204` on `client.delete("/entries/…")`

**Interfaces:**
- Produces:
  - `entry_write_service.CASCADE_UNCONFIRMED = "cascade_unconfirmed"`
  - `entry_write_service.DeletedRow` (frozen dataclass): `id: int, role: str, parent_entry_id: int | None, kind: str, name: str | None, amount: Decimal, currency: str, account_id: int, account_name: str, entry_date: date`
  - `delete_entry(db, entry_id, *, cascade: bool = False) -> list[DeletedRow]`
  - schemas `DeletedEntryOut` (the same fields; `role: Literal["entry", "transfer_leg", "child"]`) and `EntryDeleteOut(deleted: list[DeletedEntryOut])`
  - `DELETE /entries/{entry_id}?cascade=<bool>` → `200 EntryDeleteOut`

- [ ] **Step 1: Write the failing tests** in `test_entry_writes.py`, after `test_delete_removes_children_and_the_other_transfer_leg`:
  - `test_delete_reports_the_entry_alone`: one expense → `200`, `deleted` has one row with `role == "entry"`, `parent_entry_id is None`, the entry's id, account id and account name.
  - `test_delete_reports_children_on_the_same_account`: expense −100 with fee −15 and discount 5 on its account, no `cascade` → `200`; `[(r["id"], r["role"], r["parent_entry_id"]) ...] == [(entry, "entry", None), (fee, "child", entry), (discount, "child", entry)]` (children by id); no ledger row left.
  - `test_delete_reports_the_other_leg_and_its_child`: transfer A→B, fee child under the A leg on A, delete the B leg → roles `entry` (B leg), `transfer_leg` (A leg), `child` with `parent_entry_id` = the A leg.
  - `test_delete_refuses_a_child_on_another_account_until_confirmed`: expense on A with a fee row on B (`parent_entry_id` = the expense) → `409`, `message` starts with `cascade_unconfirmed` and contains the child id; both rows still exist; `?cascade=true` → `200`, both reported, none left.
  - `test_delete_refuses_a_foreign_child_of_the_other_leg`: transfer A→B, fee under the A leg on account C, delete the B leg → `409 cascade_unconfirmed`; all three rows still exist.
  - `test_delete_takes_a_covered_child_without_cascade`: expense with a fee on its account, the fee holding an active `StatementCoverage` row → `200` without `cascade` (build the statement rows as the neighbouring coverage tests of the suite do; if no fixture exists in this file, put the test in `tests/integration/test_statement_coverage*.py` beside one).
  - `test_delete_of_a_reward_row_keeps_its_refusal_beside_a_foreign_child`: a `reward` row without an accrual with a fee child on another account → `409` whose message contains `reward` and not `cascade_unconfirmed`.

- [ ] **Step 2: Run them, expect FAIL** (`204 != 200`, missing `deleted`, no `409`).

  Run: `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/integration/test_entry_writes.py -q -p no:cacheprovider -W ignore::DeprecationWarning -k "delete"`

- [ ] **Step 3: Implement.**
  - `delete_entry`: after the `assert_entry_editable` loop and before `forfeit_for_delete`, read the children of `before.target_ids` (id order) with their account names, build the report (`entry`, then the other targets by id as `transfer_leg`, then children by id), and raise `CodedConflictError(CASCADE_UNCONFIRMED, …)` when `not cascade` and any child's `account_id` differs from its parent's. The detail lists `entry <id> (<kind>, account <account_id>)` per such child and ends with `repeat with ?cascade=true to delete them too`. Return the report at the end.
  - Router: `cascade: bool = False` query parameter, `response_model=EntryDeleteOut`, status `200`.

- [ ] **Step 4: Run the tests of Step 1, expect PASS.**

- [ ] **Step 5: Change the `204` assertions on this route to `200`** (grep `delete\(f?"/entries/` under `tests/`; 24 lines assert `204` inline, a few more through a `response` variable), then run the files that call the route:

  Run: `… -m pytest tests/integration/test_entry_writes.py tests/integration/test_reward_engine_hooks.py tests/integration/test_schedule_entry_hooks.py tests/integration/test_edit_lock.py tests/integration/test_split_dissolve.py tests/integration/test_splits.py tests/integration/test_transfers.py -q …`
  Expected: all pass.

- [ ] **Step 6: Commit** `fix(entries): report what a delete removed and hold a child on another account (#390)`.

### Task 2: SPA dialog and request

**Files:**
- Modify: `frontend/src/app/services/accounting.service.ts` (`deleteEntry`)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts` (`deleteNotes`, `confirmDelete`)
- Test: `frontend/src/app/services/accounting.service.spec.ts`, `frontend/src/app/components/accounting/entry-detail/entry-detail.spec.ts`

**Interfaces:**
- Consumes: `DELETE /entries/{id}?cascade=true` from Task 1.
- Produces: `deleteEntry(id: number, cascade = false): Observable<void>`; `foreignChildren` computed on the entry detail component (children whose `account_id` differs from the entry's).

- [ ] **Step 1: Write the failing tests.**
  - Service: `deleteEntry(9, true)` requests `DELETE ${API}/entries/9?cascade=true`; `deleteEntry(9)` keeps the bare URL (existing table row).
  - Entry detail: with a fee child on another account (`account_id` 2, `account_name` `玉山卡`) the dialog lists `手續費 $30（在 玉山卡）` and the delete request URL ends with `?cascade=true`; with a fee child on the entry's own account the note has no `（在` and the request has no query.

- [ ] **Step 2: Run, expect FAIL.**

  Run: `npx ng test --watch=false --include 'src/app/components/accounting/entry-detail/entry-detail.spec.ts' --include 'src/app/services/accounting.service.spec.ts'`

- [ ] **Step 3: Implement** (`HttpParams` or a query suffix for `cascade`; the note suffix `（在 {account_name}）`; `confirmDelete('entry')` passes `foreignChildren().length > 0`).

- [ ] **Step 4: Run the two spec files, expect PASS; `npx tsc --noEmit -p tsconfig.app.json` clean.**

- [ ] **Step 5: Commit** `fix(entry-detail): name a child's other account in the delete dialog and confirm its cascade (#390)`.
