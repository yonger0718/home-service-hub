# Debt reopen / close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two explicit actions that set and clear `is_closed` on a receivable / payable original (#391).

**Architecture:** One service function flips the flag under the entry lock; two POST routes call it; the SPA entry detail gets a button and a confirm panel per state.

**Tech Stack:** FastAPI + SQLAlchemy (accounting-service), Angular 21 + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-19-debt-reopen-close-design.md`

## Global Constraints

- The flag only: no amount, settlement or link changes.
- `write` scope through the entry router's `method_scope()`; no new scope code.
- Refusals are `ValidationError(field, message)` → `422`; fields `kind` and `loan_schedule`.
- SPA copy exactly as in the spec.
- No migration.

## Review Focus

- A settlement row or a row with `settles_entry_id` passed as the target → `422 kind` (test in Task 1).
- Close under a live schedule's loan line → refused; under an ended one → allowed (Task 1).
- Reopen of a debt whose settlements net → still `is_settled`, no remainder (Task 1).
- The entry shown changes while the request is in flight → the result is not applied to the other entry (`runWrite` already guards; Task 2 uses it).
- A locked (pre-cutover) entry → no buttons (Task 2).

---

### Task 1: API

**Files:**
- Modify: `services/accounting-service/app/services/settlement_service.py`
- Modify: `services/accounting-service/app/routers/entries.py`
- Modify: `services/accounting-service/README.md`
- Test: `services/accounting-service/tests/integration/test_debt_reopen_close.py`

**Interfaces:**
- Produces: `settlement_service.set_closed(db: Session, entry_id: int, closed: bool) -> None`; `POST /entries/{entry_id}/reopen`, `POST /entries/{entry_id}/close` → `200 EntryDetailOut`.

- [ ] Step 1: write the tests of the spec's API list (fictional fixtures; `seed.entry`, `client`), run, expect 404 on the routes.
- [ ] Step 2: implement `set_closed` (`locked_entry`, `assert_entry_editable`, target check, live-schedule check through `schedule_read.loan_schedule_for(db, id)["status"] != "ended"`), the two routes, the README line.
- [ ] Step 3: run the new file and `tests/integration/test_settlements.py`, expect pass.
- [ ] Step 4: commit.

### Task 2: SPA

**Files:**
- Modify: `frontend/src/app/services/accounting.service.ts` (+ spec)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.{ts,html,scss}` (+ spec)

**Interfaces:**
- Consumes: the two routes of Task 1.
- Produces: `AccountingService.reopenDebt(id: number): Observable<EntryDetail>`, `closeDebt(id: number): Observable<EntryDetail>`; `DetailPanel` gains `'reopen' | 'close'`.

- [ ] Step 1: write the service and component tests of the spec's SPA list, run, expect failures.
- [ ] Step 2: implement the service methods, the state-line buttons (`.debt-reopen`, `.debt-close`), the confirm panel (`.debt-confirm`, confirm button `.debt-confirm-go`) and its texts.
- [ ] Step 3: run the two spec files and `tsc --noEmit` (app and spec), expect pass.
- [ ] Step 4: commit.
