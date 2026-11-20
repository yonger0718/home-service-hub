# Debt state events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every close / reopen of a debt writes an event that no entry edit or delete removes; the entry detail and the reminders page read it (#401).

**Architecture:** One table without a foreign key to the entry, written by `settlement_service.set_closed` under the entry's row lock; one small module; a new router (the list and the owner's review); the SPA shows the events on the entry detail and the closed remainders on the reminders page.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (accounting-service), Angular 21 + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-20-debt-state-events-design.md`

## Global Constraints

- `debt_state_event.entry_id` has no foreign key. Nothing in the delete path changes.
- No route edits or deletes an event; `POST /debt-events/{event_id}/review` only sets `reviewed_at`, and needs `admin`.
- `set_closed` no longer writes to `description`.
- An event only when the flag really changes; a refusal or a repeated call writes nothing.
- SPA copy exactly as in the spec.
- The migration creates the table and nothing else; `down_revision = "c4e8a1d7b3f9"`.
- The new write route `POST /debt-events/{event_id}/review` is registered in `tests/integration/test_edit_lock.py::EXPECTED_WRITES`.

## Review Focus

- close → reopen → delete of the entry: not in the to-review list (the latest event of that id is a reopen) (Task 1).
- A `write`-only caller posting the review of its own close: `403`, still listed (Task 1).
- A debt deleted while closed with a remainder: listed with `deleted: true` and its snapshot (Task 1).
- A caller without a label (auth off, bare token): event with `actor` null, shown without the actor part (Tasks 1, 2).
- The new SPA against the API of #400: a detail without `debt_events`, a 404 on the list (Task 2).
- A close with nothing left: an event with `open_amount` 0, in the detail, not in the list (Tasks 1, 2).
- A close by the owner's own token (`legacy` / `admin` / scopes off): never listed (Task 1).

---

### Task 1: API

**Files:**
- Create: `services/accounting-service/alembic/versions/<rev>_debt_state_event.py`
- Create: `services/accounting-service/app/services/debt_events.py`
- Create: `services/accounting-service/app/routers/debt_events.py`
- Modify: `services/accounting-service/app/models/ledger.py`, `app/models/__init__.py`, `app/schemas/ledger.py`, `app/services/settlement_service.py`, `app/services/ledger_service.py`, `app/routers/entries.py`, `app/main.py`, `README.md`
- Test: `tests/integration/test_debt_state_events.py`; update `tests/integration/test_debt_reopen_close.py` (the description-line tests go), `tests/integration/test_migration.py` (new head constant, round trip), any table list a schema test pins.

**Interfaces:**
- Produces:
  - model `DebtStateEvent` (columns as in the spec's table);
  - `auth.holds_admin(request: Request) -> bool`;
  - `debt_events.record(db: Session, entry: LedgerEntry, action: str, actor: str | None, open_amount: Decimal, *, by_owner: bool) -> None`;
  - `debt_events.review(db: Session, event_id: int) -> None` (`NotFoundError` / `ValidationError("action", …)`);
  - `debt_events.events_of(db: Session, entry_id: int) -> list[dict]` (keys `id, action, actor, open_amount, currency, event_date, needs_review`), oldest first;
  - `debt_events.to_review(db: Session, limit: int) -> dict` (`{"items": [...], "total": int}`, item keys as in the spec);
  - `settlement_service.set_closed(db, entry_id, closed, *, actor: str | None = None, by_owner: bool = True) -> None`;
  - `EntryDetailOut.debt_events: list[DebtEventOut] = []`;
  - `GET /debt-events/to-review?limit=` → `DebtReviewPage`; `POST /debt-events/{event_id}/review` → `204`.

- [ ] Step 1: write `test_debt_state_events.py` from the spec's API test list (token labels through `auth.TOKENS_ENV` / `auth.SCOPES_ENV` as in `test_debt_reopen_close.py`; `ledger_service._today` is not involved: `event_date` comes from `created_at`), change the description tests of `test_debt_reopen_close.py` to "description untouched", add the migration round trip. Run: expect failures (no table, no field, 404).
- [ ] Step 2: migration + model + `debt_events` module + `set_closed` change + detail field + router + README. `event_date` = `created_at` converted to `ledger_service.LEDGER_TZ`.
- [ ] Step 3: run the new file, `test_debt_reopen_close.py`, `test_edit_lock.py`, `test_migration.py`, `test_models_schema.py`, `test_ledger_model.py`, `test_settlements.py`: expect pass.
- [ ] Step 4: commit.

### Task 2: SPA

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts`, `frontend/src/app/services/accounting.service.ts` (+ spec)
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.{ts,html,scss}` (+ spec)
- Modify: `frontend/src/app/components/accounting/reminders/reminders.{ts,html,scss}` (+ spec)

**Interfaces:**
- Consumes: `debt_events` on the detail; `GET /debt-events/to-review`; `POST /debt-events/{event_id}/review`.
- Produces: `DebtEvent`, `DebtReviewItem`, `DebtReviewPage` types; `AccountingService.getDebtReviews(limit = 50): Observable<DebtReviewPage>`, `reviewDebtEvent(eventId: number): Observable<void>`; a shared `debtActorLabel(actor: string | null): string` (`spa` → `網頁`, `hermes` → `Hermes`, else the label, null → `''`).

- [ ] Step 1: write the tests of the spec's SPA list; run, expect failures.
- [ ] Step 2: implement: detail block `.debt-events` (one `.debt-event` per line); the close confirm text; reminders section `.debt-reviews` (rows `.review-row` with `.review-open` and `.review-done`, a deleted one `.review-row.deleted` without `.review-open`, the `僅顯示最近 50 筆`, read-failure and `確認失敗，請稍後再試。` lines), read by a request of its own that `load()` starts (a failed read shows its line and never gates the page).
- [ ] Step 3: run the three spec files and `tsc --noEmit` (app and spec): expect pass.
- [ ] Step 4: commit.
