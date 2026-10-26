# Card Statement Auto-Pay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the business day the bank debits a card bill, HomeHub books the bank → card transfer itself (system pair `自動扣繳`, `source='statement'`), exactly once per bill, so the owner stops typing card payments and the later bank / card statement lines claim the pair; an owner twin retires the pair without a case.

**Architecture:** A provenance table `statement_auto_pay` (one row per statement, exclusion constraint one booked pair per card per 20 days) anchors a new stand-in kind. A pure-DB service `auto_pay_service.py` holds candidates, gates, the twin test, booking, housekeeping and pair retirement; `auto_pay_job.py` wraps it in the schedule-job pattern (APScheduler 06:00 Asia/Taipei + startup run, advisory lock, CLI). `stand_ins` learns the auto-pay legs as stand-ins on both sides, `reconciliation_service.reconcile` retires twinned pairs before matching, and `matching.py` gets one claiming rule (`auto_pay`, exact flow within the drift window) so holiday posting lag opens no case. Settings get an `auto_pay` block; the settings page refuses bad links.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, APScheduler 3.11 (already a dependency), Postgres 16 (`btree_gist`), pytest (unit: pure functions; integration: throw-away Postgres per session via `tests/conftest.py`, alembic-migrated), Decimal money.

**Spec:** `docs/superpowers/specs/2026-10-26-statement-auto-pay-design.md` rev 4 (`7d19b2b1`, Multica AGENT-302/303 PASS). Parents: `2026-10-15-reconciliation-r6-design.md` (R6b.0 stand-ins), `2026-10-25-reconciliation-r6c-transfers-design.md` (pair_transfer, twin guards). File:line citations below were read at `7d19b2b1` (tree of main `f2d8498`).

## Global Constraints

- Branch `feat/statement-auto-pay` in worktree `/home/opc/workspace/home-hub-autopay` (from `origin/main` f2d8498). Run everything from `/home/opc/workspace/home-hub-autopay/services/accounting-service` with `PYTHONPATH=.:../shared-python-lib /home/opc/workspace/home-hub/services/accounting-service/.venv/bin/python -m pytest -q -p no:cacheprovider …` (the main checkout's venv; a mode-600 `.env` copy sits in the worktree root — never print its values). Paths below are relative to `services/accounting-service/` unless they start with `docs/`.
- One migration, revision `c4a7e2d9f1b3` after head `b8d4f2a1c6e3` (`alembic/versions/b8d4f2a1c6e3_account_logo.py`): `CREATE EXTENSION IF NOT EXISTS btree_gist`, table `statement_auto_pay` exactly as spec §4.2, constraint `EXCLUDE USING gist (card_account_id WITH =, daterange(pay_date, pay_date + 20) WITH &&) WHERE (status = 'booked')`. No new enum type: `status` is `String(16)` with a CHECK, `reason` `String(64)`. No other schema change.
- Verbatim names: entry name `自動扣繳`; `source` = `entry_write_service.STATEMENT_SOURCE` (`"statement"`); description `f"{card.name} {period_end:%Y-%m-%d} 帳單 自動扣繳"`; statuses `booked | retired | owner_deleted | owner_edited | skipped`; terminal skip reasons `twin, cutoff, duplicate_statement, expired`; transient `header_disputed, no_link, link_ineligible, over_cap, monthly_budget`; gate order `cutoff → duplicate_statement → header_disputed → no_link → link_ineligible → twin → over_cap → monthly_budget`; duplicate window 20 days (strictly fewer than 20 days apart collides); audit actor `auto_pay`; audit events `auto_pay_booked, auto_pay_skipped, auto_pay_retired, auto_pay_owner_deleted, auto_pay_owner_edited, auto_pay_header_changed, auto_pay_statement_retired, auto_pay_calendar_stale` (added to `reconciliation_actions/audit.py:EVENTS`; the column is free text `String(48)`, case-less rows with `statement_id` set); matcher rule name `auto_pay`.
- Settings block defaults verbatim (spec §4.7): `{"enabled": false, "enabled_from": null, "max_amount": "200000", "monthly_total": "500000", "twin_window_days": 7, "retry_days": 30, "extra_holidays": [], "extra_workdays": []}`; `enabled_from` is required when `enabled` is true; changing `auto_pay` bumps `version` only (never `policy_version` / `rules_version`); unknown keys → 422 on field `auto_pay`.
- Dates: `pay_date = next_business_day(due_date)` (Mon–Fri minus `TW_HOLIDAYS ∪ extra_holidays`, plus `extra_workdays`); both legs `entry_date = pay_date`; bank leg `posted_date = pay_date`; card leg `posted_date = next_business_day(pay_date + 1 day)`; `needs_review = False` on both.
- Twin (spec §4.1): excluding the row's own legs, any top-level `transfer_in` on an account of the card family (`coverage_service.participating_accounts(db, card_account_id)`) with `amount == statement_total`, or on the pay account a `transfer_out` with `amount == -statement_total` or an `expense` with `amount == -statement_total` and `source == STATEMENT_SOURCE`, with `entry_date` in `[pay_date − twin_window_days, pay_date + twin_window_days]`, whatever the source.
- Locks: the job takes `pg_try_advisory_lock(AUTO_PAY_JOB_LOCK_KEY)` on a dedicated connection as `schedule_job.run` does (`schedule_job.py:260-279`); every write transaction (housekeeping and booking alike) first calls `schedule_locks.take_import_key_shared(db)` (raises `ImportRunningError` while a MOZE import holds the key → run status `import_running`, nothing written). A pair is retired only with both legs locked `FOR UPDATE NOWAIT`; a lock failure skips that pair (job: counted `busy`; sweep: logged, left to the job) — never waits, never raises out of `reconcile`.
- Never call `transfer_service.update_transfer` (flips `source`); never `engine.apply_action` from the job; the job never writes a `ReconciliationAction` or a case. The sweep-time retire runs inside `reconcile()`'s transaction before the first `load_candidates`.
- Booked rows are never re-evaluated as candidates; `skipped` rows with a transient reason are rewritten in place (same row, `statement_id` UNIQUE) and audited `auto_pay_skipped` only when the stored reason changes.
- The frontend is untouched (spec §4.9). No openspec delta (R6a–R6c shipped without one; the reconciliation capability spec was never archived).
- No owner financial data in tests, fixtures, docs or commit messages (fictional amounts only). Host dry-run outputs mode 600 under `~/reports/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Full suite before the final review: `… -m pytest -q -p no:cacheprovider` → 0 failed (baseline at f2d8498 to be recorded in Task 9 before comparing).

## Review Focus

1. An owner entry 4–15 days from the card `payment` line beside an auto-pay leg: the sweep must retire the pair **before** matching so the line goes to the owner's path (exact claim or `amount_delta`), never to the leg — test in Task 7 (distances 4, 5, 12).
2. A second live statement of the same bill with a misread `due_date` (3 days off, other `period_end`): gate `duplicate_statement`, and the exclusion constraint refuses a direct second insert — tests in Tasks 1 and 4; the same card's **next** monthly statement (~30 days later) must book while the previous row is still `booked` — test in Task 4.
3. The job's housekeeping and a sweep retiring the same pair at once: both lock the legs `FOR UPDATE NOWAIT`; the loser skips and nothing is deleted twice or left half-retired — test in Task 7 (`tests.helpers.race`).
4. Combined families: the link on a **child** card does not make the master's statement a candidate (`no_link`), and the owner's MOZE payment booked on a child card is a twin (the 星展 shape) — tests in Task 4.
5. Owner edits: a description-only change on a leg keeps the row `booked`; a `PUT /transfers` (source → `manual`) makes it `owner_edited` and the legs stay; an `adjust_transfer`-style re-date with `source` still `statement` updates the row's figures — tests in Task 5.

---

### Task 1: Migration and model `StatementAutoPay`

**Files:**
- Create: `alembic/versions/c4a7e2d9f1b3_statement_auto_pay.py`
- Modify: `app/models/statements.py` (after `ReconciliationAudit`, :376–388), `app/models/__init__.py:26-60` (export)
- Test: `tests/integration/test_auto_pay_table.py`

**Interfaces:**
- Produces: `app.models.StatementAutoPay` (`__tablename__ = "statement_auto_pay"`) with columns `id`, `statement_id` (FK `account_statement.id` RESTRICT, UNIQUE, NOT NULL), `card_account_id`, `pay_account_id` (FK `account.id` RESTRICT, NOT NULL), `amount` (`Numeric(20, 4)` NOT NULL), `due_date`, `pay_date` (Date NOT NULL), `transfer_group_id` (Uuid, nullable), `out_entry_id`, `in_entry_id` (FK `ledger_entry.id` ON DELETE SET NULL, nullable), `status` (`String(16)` NOT NULL, CHECK in the five statuses, name `ck_statement_auto_pay_status`), `reason` (`String(64)`, nullable), `settings_version` (Integer NOT NULL), `created_at`, `updated_at` (timestamptz, `server_default=NOW`, `onupdate=NOW`); indexes `ix_statement_auto_pay_card_account_id`, `ix_statement_auto_pay_status`; exclusion constraint `ex_statement_auto_pay_card_window` (raw SQL in the migration; in the model as a comment — SQLAlchemy's `ExcludeConstraint` with the `daterange(...)` expression is not needed for the ORM). `app.models.AUTO_PAY_STATUSES = ("booked", "retired", "owner_deleted", "owner_edited", "skipped")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_auto_pay_table.py
from datetime import date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy.exc import IntegrityError
from app.models import StatementAutoPay
from tests.integration.statement_fixtures import OCT, SEP, _card, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)

def _row(stmt, card, bank, pay_date, status="booked", **extra):
    return StatementAutoPay(statement_id=stmt.id, card_account_id=card.id, pay_account_id=bank.id, amount=Decimal("580"),
                            due_date=pay_date, pay_date=pay_date, status=status, settings_version=1, **extra)

def test_one_booked_pair_per_card_per_20_days(seed, db_session, run):
    bank = seed.account("薪轉"); card = _card(seed, db_session, auto_pay_account_id=bank.id)
    a, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 24))
    b, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 11, 24))
    db_session.add(_row(a, card, bank, date(2026, 10, 27))); db_session.commit()
    db_session.add(_row(b, card, bank, date(2026, 10, 27) + timedelta(days=19)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add(_row(b, card, bank, date(2026, 10, 27) + timedelta(days=20))); db_session.commit()  # 20 days: no overlap
    assert db_session.query(StatementAutoPay).count() == 2

def test_retired_rows_do_not_block_and_statement_is_unique(seed, db_session, run):
    bank = seed.account("薪轉"); card = _card(seed, db_session, auto_pay_account_id=bank.id)
    a, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 24))
    b, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 10, 27))
    db_session.add(_row(a, card, bank, date(2026, 10, 27), status="retired", reason="twin")); db_session.commit()
    db_session.add(_row(b, card, bank, date(2026, 10, 28))); db_session.commit()  # a retired row never collides
    db_session.add(_row(b, card, bank, date(2026, 12, 1), status="skipped", reason="cutoff"))
    with pytest.raises(IntegrityError):  # statement_id UNIQUE
        db_session.flush()
    db_session.rollback()
```

- [ ] **Step 2: Run them** — `… -m pytest tests/integration/test_auto_pay_table.py -q -p no:cacheprovider`. Expected: FAIL (`ImportError: cannot import name 'StatementAutoPay'`).

- [ ] **Step 3: Write the migration** (`revision = "c4a7e2d9f1b3"`, `down_revision = "b8d4f2a1c6e3"`): `op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")`, `op.create_table(...)` with the columns above, the two indexes, then `op.execute("ALTER TABLE statement_auto_pay ADD CONSTRAINT ex_statement_auto_pay_card_window EXCLUDE USING gist (card_account_id WITH =, daterange(pay_date, pay_date + 20) WITH &&) WHERE (status = 'booked')")`. `downgrade`: `op.drop_table("statement_auto_pay")` (the extension stays). Docstring: one paragraph on what the table anchors (spec §4.2).

- [ ] **Step 4: Write the model and the export**; the test database is migrated by `tests/conftest.py` (alembic `upgrade head`), so the tests prove the migration.

- [ ] **Step 5: Run the tests** — Expected: PASS. Also `… -m pytest tests/integration/test_statement_retire.py -q -p no:cacheprovider` still passes (migration chain intact).

- [ ] **Step 6: Commit** — `git commit -m "feat(auto-pay): statement_auto_pay table, model and 20-day exclusion constraint"`.

---

### Task 2: Business-day calendar

**Files:**
- Create: `app/services/business_days.py`
- Test: `tests/unit/test_business_days.py`

**Interfaces:**
- Produces: `TW_HOLIDAYS: frozenset[date]` — Taiwan public holidays 2026 and 2027 as published by the 行政院人事行政總處 辦公日曆表 (make-up holidays included, make-up workdays excluded; the builder fills the full list from the official calendar and cites it in the module docstring); `LAST_KNOWN_DAY = date(2027, 12, 31)`; `is_business_day(day: date, *, extra_holidays: frozenset[date] = frozenset(), extra_workdays: frozenset[date] = frozenset()) -> bool` (`day in extra_workdays` wins over everything; else weekday < 5 and not in `TW_HOLIDAYS | extra_holidays`); `next_business_day(day: date, *, extra_holidays=..., extra_workdays=...) -> date` (first business day ≥ `day`); `calendar_covers(day: date) -> bool` (`day <= LAST_KNOWN_DAY`).

- [ ] **Step 1: Write the failing tests** (dates pinned from the owner's observed bank behaviour and the 2026 calendar; the 2027 pin is 春節 — verify against the official 116年 calendar and correct the expected date if it disagrees):

```python
from datetime import date
from app.services import business_days as bd

def test_weekend_and_make_up_holiday_shift():
    assert bd.next_business_day(date(2026, 10, 24)) == date(2026, 10, 27)   # Sat; Mon 10-26 = 光復節 make-up
    assert bd.next_business_day(date(2026, 10, 9)) == date(2026, 10, 12)    # 國慶 10-10 Sat → Fri 10-09 holiday
    assert bd.next_business_day(date(2026, 9, 25)) == date(2026, 9, 29)     # 中秋 Fri 09-25, 教師節 Mon 09-28
    assert bd.next_business_day(date(2026, 10, 28)) == date(2026, 10, 28)   # plain Wednesday

def test_spring_festival_2027():
    assert bd.next_business_day(date(2027, 2, 5)) == date(2027, 2, 11)      # 除夕 Fri 02-05 … make-up Wed 02-10

def test_settings_overrides():
    assert bd.next_business_day(date(2026, 10, 24), extra_workdays=frozenset({date(2026, 10, 26)})) == date(2026, 10, 26)
    assert bd.next_business_day(date(2026, 10, 28), extra_holidays=frozenset({date(2026, 10, 28)})) == date(2026, 10, 29)
    assert bd.is_business_day(date(2026, 10, 31), extra_workdays=frozenset({date(2026, 10, 31)}))  # a Saturday make-up workday

def test_calendar_coverage():
    assert bd.calendar_covers(date(2027, 12, 31)) and not bd.calendar_covers(date(2028, 1, 1))
    assert all(d.year in (2026, 2027) for d in bd.TW_HOLIDAYS)
```

- [ ] **Step 2: Run** — `… -m pytest tests/unit/test_business_days.py -q -p no:cacheprovider`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement the module** (pure functions, no settings access; the holiday list as ISO literals grouped per year with the holiday name in a trailing comment).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): Taiwan business-day calendar"`.

---

### Task 3: Settings `auto_pay` block and link validation

**Files:**
- Modify: `app/services/settings_service.py` (`_reconciliation_dict` :146–157, `_check_policy` :266–312 as the model for `_check_auto_pay`, `update_reconciliation_settings` :394–418, `_validate_account` :487–490, `update_account` :564–580), `app/services/reconciliation_actions/audit.py:8-19` (EVENTS)
- Test: `tests/integration/test_auto_pay_settings.py`

**Interfaces:**
- Produces: `settings_service.AUTO_PAY_DEFAULTS` (the verbatim dict); `_check_auto_pay(auto_pay) -> dict` (validated as given, defaults overlaid on read; refusals are `ValidationError("auto_pay", …)`: unknown key; `enabled` not bool; `enabled_from` not `None`/ISO date string; `enabled` true with `enabled_from` None → `"'enabled_from' is required when enabled"`; amounts via `_check_amount("auto_pay", key, value)`; `twin_window_days` int 0..30; `retry_days` int 0..365; `extra_holidays` / `extra_workdays` lists of ISO date strings, no date in both); `read_auto_pay_settings(db) -> dict` = effective `auto_pay` block plus `"version": row.version` (SELECT-only, built on `read_reconciliation_settings`); `_reconciliation_dict` returns `"auto_pay": {**AUTO_PAY_DEFAULTS, **(data.get("auto_pay") or {})}`; `update_reconciliation_settings` validates `data.get("auto_pay", {})` and stores it under `"auto_pay"` (version bump only). `_validate_account`: the link target must satisfy `not target.is_credit and not target.is_archived and target.currency == payload.currency` else `ValidationError("auto_pay_account_id", "must be a non-credit, non-archived account in the same currency")`. `update_account`: when `payload.is_archived and not account.is_archived`, `update(Account).where(Account.auto_pay_account_id == account_id).values(auto_pay_account_id=None, settings_locally_edited=True)` (the `delete_account` statement at :602–605, reused). `audit.EVENTS` gains the eight `auto_pay_*` names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_auto_pay_settings.py
import pytest
from app.models import Account
from app.services import settings_service
from app.services.reconciliation_actions import audit

def _body(client, **auto_pay):
    current = client.get("/settings/reconciliation").json()
    return {"account_map": current["account_map"], "dirty_enabled": current["dirty_enabled"], "auto_pay": auto_pay}

def test_defaults_and_version_bump(client, db_session):
    got = client.get("/settings/reconciliation").json()
    assert got["auto_pay"] == settings_service.AUTO_PAY_DEFAULTS == {
        "enabled": False, "enabled_from": None, "max_amount": "200000", "monthly_total": "500000",
        "twin_window_days": 7, "retry_days": 30, "extra_holidays": [], "extra_workdays": []}
    put = client.put("/settings/reconciliation", json=_body(client, enabled=True, enabled_from="2026-10-27", extra_holidays=["2026-12-31"]))
    assert put.status_code == 200
    after = put.json()
    assert after["auto_pay"]["enabled"] is True and after["auto_pay"]["max_amount"] == "200000"  # defaults overlaid
    assert after["version"] == got["version"] + 1
    assert (after["policy_version"], after["rules_version"]) == (got["policy_version"], got["rules_version"])
    assert settings_service.read_auto_pay_settings(db_session)["enabled_from"] == "2026-10-27"

@pytest.mark.parametrize("auto_pay", [
    {"enabled": True}, {"enabled_from": "27/10/2026"}, {"twin_window_days": 31}, {"retry_days": -1},
    {"max_amount": "-1"}, {"bogus": 1}, {"extra_holidays": ["2026-12-31"], "extra_workdays": ["2026-12-31"]}])
def test_refusals_name_the_field(client, auto_pay):
    res = client.put("/settings/reconciliation", json=_body(client, **auto_pay))
    assert res.status_code == 422 and "auto_pay" in str(res.json()["detail"])

def test_audit_events_registered():
    assert {"auto_pay_booked", "auto_pay_skipped", "auto_pay_retired", "auto_pay_owner_deleted", "auto_pay_owner_edited",
            "auto_pay_header_changed", "auto_pay_statement_retired", "auto_pay_calendar_stale"} <= audit.EVENTS
```

Link tests (same file; import `_account_body` and `_error_fields` from `tests.integration.test_settings_crud_api`, :12–18):

```python
def test_link_must_be_non_credit_non_archived_same_currency(client, db_session, seed):
    other_card = seed.account("別卡", is_credit=True); archived = seed.account("舊戶", is_archived=True)
    usd = seed.account("美金戶", currency="USD"); bank = seed.account("薪轉"); db_session.commit()
    for target in (other_card, archived, usd):
        res = client.post("/accounts", json=_account_body(name=f"卡{target.id}", is_credit=True, auto_pay_account_id=target.id))
        assert res.status_code == 422 and _error_fields(res) == {"auto_pay_account_id"}, target.name
    assert client.post("/accounts", json=_account_body(name="好卡", is_credit=True, auto_pay_account_id=bank.id)).status_code == 201

def test_archiving_the_bank_clears_the_links(client, db_session, seed):
    bank = seed.account("薪轉"); card = seed.account("卡", is_credit=True, auto_pay_account_id=bank.id); db_session.commit()
    res = client.put(f"/accounts/{bank.id}", json=_account_body(name="薪轉", is_archived=True))
    assert res.status_code == 200
    db_session.expire_all()
    assert db_session.get(Account, card.id).auto_pay_account_id is None and db_session.get(Account, card.id).settings_locally_edited
```

- [ ] **Step 2: Run** — Expected: FAIL (`auto_pay` missing from GET; 201 where 422 expected).
- [ ] **Step 3: Implement** the four settings changes and the EVENTS additions. Keep `test_settings_crud_api.py::test_deleting_an_account_flags_the_accounts_whose_links_it_clears` green: it seeds a credit→credit link directly through the model, which the API rule does not touch.
- [ ] **Step 4: Run** `tests/integration/test_auto_pay_settings.py tests/integration/test_settings_crud_api.py tests/integration/test_settings_api.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): settings block, link rule, archive clears links, audit events"`.

---

### Task 4: `auto_pay_service` — candidates, gates, twins, booking

**Files:**
- Create: `app/services/auto_pay_service.py`
- Test: `tests/integration/test_auto_pay_service.py`

**Interfaces:**
- Consumes: Task 1 model; Task 2 `next_business_day`, `calendar_covers`; Task 3 `read_auto_pay_settings` (the `settings` dict argument everywhere below is that return value); `transfer_service.create_transfer(db, TransferIn, *, source, remember) -> UUID` and `transfer_service.transfer_legs(db, group_id) -> (out_leg, in_leg)`; `coverage_service.participating_accounts(db, account_id) -> list[int]`; `schedule_locks.take_import_key_shared(db)`; `reconciliation_actions.audit.record(db, event=, actor=, statement_id=, detail=)`.
- Produces:
  - Constants `AUTO_PAY_NAME = "自動扣繳"`, `ACTOR = "auto_pay"`, `DUPLICATE_DAYS = 20`, `TERMINAL_REASONS`, `TRANSIENT_REASONS`, `FINAL_STATUSES = frozenset({"booked", "retired", "owner_deleted", "owner_edited"})`.
  - `@dataclass(frozen=True) Candidate(statement_id: int, card_account_id: int, card_name: str, currency: str, pay_account_id: int | None, amount: Decimal, due_date: date, pay_date: date, period_end: date, existing_reason: str | None)`.
  - `calendar_sets(settings) -> tuple[frozenset[date], frozenset[date]]` (parsed `extra_holidays`, `extra_workdays`); `pay_date_for(due_date: date, settings) -> date`.
  - `candidates(db, today: date, settings) -> list[Candidate]`: live card statements (`kind='card'`, `mode='live'`) with `due_date IS NOT NULL`, `statement_total > 0`, whose account has `auto_pay_account_id` **or not** (the `no_link` gate decides; `pay_account_id` None then), with no `statement_auto_pay` row or a `skipped` row whose reason is transient, with `pay_date <= today`, ordered `(pay_date, statement_id)`.
  - `no_due_date(db) -> list[int]`: live card statements with `due_date IS NULL` and a linked master (dry-run listing only).
  - `find_twins(db, *, card_account_id: int, pay_account_id: int, amount: Decimal, pay_date: date, window_days: int, exclude: Iterable[int] = ()) -> list[int]` (Global Constraints twin definition; sorted ids).
  - `gate(db, cand: Candidate, today: date, settings) -> str | None`: the reason in the fixed order, or `None`; a transient reason becomes `"expired"` when `today > cand.pay_date + retry_days`; `monthly_budget` sums `amount` of `booked` rows (any card) with `pay_date` in `cand.pay_date`'s calendar month; `header_disputed` = an open/proposed case of kind `parse_review` or `statement_conflict` on the statement.
  - `book(db, cand: Candidate, settings) -> StatementAutoPay`: `take_import_key_shared(db)`; `create_transfer(db, TransferIn(from_account_id=pay, to_account_id=card, out_amount=amount, entry_date=pay_date, posted_date=pay_date, name=AUTO_PAY_NAME, description=…), source=STATEMENT_SOURCE, remember=False)`; `in_leg.posted_date = next_business_day(pay_date + 1 day, …)`; `needs_review = False` on both legs; insert the `booked` row (`settings_version = settings["version"]`, entry ids, group id); audit `auto_pay_booked` with detail `{card_account_id, pay_account_id, amount: str, pay_date: iso, out_entry_id, in_entry_id}`; flushes, never commits.
  - `record_skip(db, cand: Candidate, reason: str, settings) -> StatementAutoPay`: insert or update the statement's `skipped` row (`reason`, `pay_date`, `amount`, `pay_account_id` or the card itself when None — the column is NOT NULL, so a `no_link` skip stores `card_account_id` there, documented in the docstring — `settings_version`); audit `auto_pay_skipped` `{reason, …}` only when `reason != cand.existing_reason`.
  - `decide(db, cand: Candidate, today: date, settings, *, dry_run: bool = False) -> dict` = `{"statement_id", "card_account_id", "pay_account_id", "amount": str, "pay_date": iso, "decision": "booked" | "skipped" | "would_book" | "would_skip", "reason": str | None}`; with `dry_run` nothing is written.

- [ ] **Step 1: Write the failing tests** (fixtures: `bank = seed.account("薪轉")`, `card = _card(seed, db, "卡", auto_pay_account_id=bank.id)`, a statement via `_submit(db, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 24))` → `statement_total` 580, pay date 2026-10-27; `_settings(**over)` = `{**AUTO_PAY_DEFAULTS, "enabled": True, "enabled_from": "2026-09-01", "version": 1, **over}`; every test asserts through `decide(...)` with `today = date(2026, 10, 27)`):

```python
def test_books_the_pair_with_two_posting_dates_and_the_row(...):
    out = aps.decide(db, cand, TODAY, _settings()); db.commit()
    assert out["decision"] == "booked"
    row = db.query(StatementAutoPay).one(); out_leg, in_leg = transfer_service.transfer_legs(db, row.transfer_group_id)
    assert (out_leg.account_id, in_leg.account_id, out_leg.amount, in_leg.amount) == (bank.id, card.id, Decimal("-580"), Decimal("580"))
    assert (out_leg.entry_date, out_leg.posted_date, in_leg.entry_date, in_leg.posted_date) == (PAY, PAY, PAY, date(2026, 10, 28))
    assert out_leg.source == in_leg.source == "statement" and out_leg.name == "自動扣繳" and not in_leg.needs_review
    assert (row.status, row.amount, row.due_date, row.pay_date, row.settings_version) == ("booked", Decimal("580"), date(2026, 10, 24), PAY, 1)
    assert [a.event for a in db.query(ReconciliationAudit).filter_by(statement_id=stmt.id)][-1] == "auto_pay_booked"
    assert aps.candidates(db, TODAY, _settings()) == []  # booked: never a candidate again

def test_not_a_candidate_before_pay_date_and_without_due_date(...)      # today 10-26 → []; a statement with due_date None → not in candidates, in no_due_date()
def test_gate_order_cutoff_duplicate_header_link(...):
    # cutoff: enabled_from "2026-11-01" → "cutoff"; duplicate_statement: a booked row for the card at pay_date 10-20 → "duplicate_statement"
    # (statement B, period OCT, due_date 10-21, misread); the same card's next statement due 11-24 → books while the first row is booked;
    # header_disputed: an open parse_review on the statement → "header_disputed"; dismissed → books; statement_conflict open → "header_disputed";
    # no_link: card without auto_pay_account_id (a CHILD card carrying the link does not count) → "no_link";
    # link_ineligible: link to a credit card / an archived account / a USD account → "link_ineligible"
def test_twin_shapes(...):
    # owner transfer bank→card on 10-27 (via _transfer) → "twin"; owner transfer bank→CHILD card (combined_account_id=card.id) → "twin";
    # a transfer_out -580 on the bank to some other account on 10-30 → "twin"; a statement-sourced expense -580 on the bank → "twin";
    # a manual expense -580 on the bank → NOT a twin (books); an owner transfer of 580 on 11-05 (9 days) → NOT a twin; find_twins(..., exclude=own legs) == []
def test_caps_and_expiry(...):
    # max_amount "500" → "over_cap"; monthly_total "1000" with a booked row of 580 in October for another card → "monthly_budget";
    # a transient skip at today = pay_date + 31 → "expired" (terminal: no longer a candidate); a second decide with the same reason adds no auto_pay_skipped audit
def test_dry_run_writes_nothing(...)    # decision "would_book", no row, no entries, no audit
```

- [ ] **Step 2: Run** — Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement `app/services/auto_pay_service.py`** per the Interfaces block (module docstring: spec §4.1–§4.4 summary and the lock note).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): candidates, gates, twin test and booking"`.

---

### Task 5: Housekeeping and pair retirement

**Files:**
- Modify: `app/services/auto_pay_service.py`, `app/services/coverage_service.py` (next to `release_rows` :125–128)
- Test: `tests/integration/test_auto_pay_service.py` (append)

**Interfaces:**
- Consumes: `entry_write_service.delete_entries_cascade(db, ids)`; `coverage_service.active_rows`, `claimed_entry_ids`.
- Produces:
  - `coverage_service.release_entries(db, entry_ids: Iterable[int], *, reason: str) -> set[int]`: releases every active coverage row of those entries (via `_release`) and returns the affected `statement_id`s.
  - `auto_pay_service.PAIR_BUSY = "auto_pay_busy"`; `lock_pair(db, row: StatementAutoPay) -> tuple[LedgerEntry | None, LedgerEntry | None]`: both legs `SELECT … FOR UPDATE NOWAIT` (`populate_existing`); a lock failure raises `CodedConflictError(PAIR_BUSY, …)`; missing legs come back `None`.
  - `retire_pair(db, row: StatementAutoPay, *, reason: str, run_id: int | None = None) -> set[int]`: `lock_pair` → `release_entries(db, legs, reason=f"auto_pay:{reason}")` → `delete_entries_cascade(db, legs)` → `row.status = "retired"`, `row.reason = reason` → `needs_recheck = True` on the returned statements → audit `auto_pay_retired` `{reason, out_entry_id, in_entry_id, released_statement_ids}`; returns the statement ids.
  - `check_row(db, row: StatementAutoPay, today: date, settings) -> str | None` — one booked row, in this order, returning the transition name: both legs gone → `"owner_deleted"` (status + audit `auto_pay_owner_deleted`); any leg `source != STATEMENT_SOURCE` → `"owner_edited"` (status + audit once; figures untouched); a twin per `find_twins(..., exclude=legs)` while `today <= pay_date + twin_window_days + 7` → `retire_pair(reason="twin")` → `"retired"`; legs' `amount` / `entry_date` / `posted_date` (out leg) / accounts differ from the row → copy the legs' figures onto the row (`amount`, `pay_date = out_leg.entry_date`, `pay_account_id`, `card_account_id`), stays `booked`, no audit → `"updated"`; the statement's current `statement_total` or `due_date` differs from the row and `row.reason != "header_changed"` → `needs_review = True` on both legs, `row.reason = "header_changed"` (status stays `booked`; the reason column marks "audited once"), audit `auto_pay_header_changed` → `"header_changed"`; else `None`. A description / name / category change alone is none of these.
  - `booked_rows(db) -> list[StatementAutoPay]` (status `booked`, id order) for the job.

- [ ] **Step 1: Write the failing tests** (each books with `decide` first, then mutates, then asserts `check_row`'s return, `row.status`, the audit tail):

```python
def test_owner_deleted(...)             # ews.delete_entry on one leg (both go) → "owner_deleted"; a second check_row → None, no second audit
def test_owner_edited_via_put_transfers(...)   # transfer_service.update_transfer(db, group, TransferIn(... description="我付的")) → source manual → "owner_edited"; legs still exist
def test_description_only_edit_keeps_booked(...)   # out_leg.description = "x"; db.commit() → check_row is None, status booked
def test_action_style_redate_updates_figures(...)  # out_leg.entry_date = in_leg.entry_date = PAY + 1 day (source stays statement) → "updated"; row.pay_date == PAY + 1
def test_twin_after_booking_retires_the_pair_and_releases_claims(...):
    # bank statement (kind="bank", period OCT) with a withdrawal line −580 on PAY submitted → the bank leg holds the line (match_rule exact);
    # owner _transfer(bank, card, 580, PAY) → check_row → "retired"; both legs gone; the bank statement's coverage row for the line is stale
    # (reason "auto_pay:twin"), statement.needs_recheck True; rec.reconcile(db, bank_stmt.id) → the line is claimed by the owner's transfer_out
def test_twin_window_closed_after_14_days(...)   # owner twin dated PAY, today = PAY + 15 → check_row returns None (the job stopped looking); the sweep path (Task 7) still retires
def test_header_change_marks_needs_review_once(...)   # UPDATE account_statement SET statement_total = 600 → "header_changed", both legs needs_review, one audit; again → None
def test_retire_pair_busy_when_a_leg_is_locked(...)   # another connection holds FOR UPDATE on the in leg → CodedConflictError code "auto_pay_busy"; row untouched
```

- [ ] **Step 2: Run** — Expected: FAIL (`AttributeError: check_row`).
- [ ] **Step 3: Implement** `release_entries`, `lock_pair`, `retire_pair`, `check_row`, `booked_rows`.
- [ ] **Step 4: Run** `tests/integration/test_auto_pay_service.py tests/integration/test_coverage_service.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): housekeeping transitions and pair retirement"`.

---

### Task 6: The job, scheduler wiring and CLI

**Files:**
- Create: `app/services/auto_pay_job.py`
- Modify: `app/services/schedule_locks.py:18` (add the key beside `SCHEDULE_JOB_LOCK_KEY`), `app/main.py:38-45` (startup/shutdown hooks)
- Test: `tests/unit/test_auto_pay_job_scheduler.py`, `tests/integration/test_auto_pay_job.py`

**Interfaces:**
- Consumes: Task 4 `candidates`, `decide`, `no_due_date`; Task 5 `booked_rows`, `check_row`, `PAIR_BUSY`; Task 2 `calendar_covers`; `schedule_job.is_enabled()` (the one env switch `ACCOUNTING_SCHEDULER_ENABLED` governs both in-process jobs — tests already set it false in `tests/conftest.py:97-100`); `schedule_job.TZ_NAME`.
- Produces: `schedule_locks.AUTO_PAY_JOB_LOCK_KEY = 0x41505041` (`"APPA"`); `auto_pay_job.DAILY_JOB_ID = "auto_pay_daily"`, `STARTUP_JOB_ID = "auto_pay_startup"`, `STARTUP_DELAY_SEC = 20`, `TRIGGERS = ("cron", "startup", "manual")`; `run(engine: Engine, trigger: str, *, today: date | None = None, dry_run: bool = False) -> dict` with report keys `trigger`, `today` (iso), `status` ∈ `completed | dry_run | busy | disabled | import_running`, `housekeeping` (`{"checked", "owner_deleted", "owner_edited", "retired", "updated", "header_changed", "busy", "failed"}`), `decisions` (list of `decide` dicts; `failed` decisions carry `error_class`), `no_due_date` (statement ids), `calendar_stale` (bool). Order: advisory lock (busy → return) → housekeeping, one transaction per booked row (`CodedConflictError(PAIR_BUSY)` → `busy`; any other exception → logged `auto_pay_job.housekeeping_failed` with ids and error class only, counted `failed`) → `settings = read_auto_pay_settings` → `enabled` false → `status="disabled"` (housekeeping already done; with `dry_run`, candidates are still listed as `would_*`) → candidates, one transaction per statement (`ImportRunningError` anywhere → `status="import_running"`, stop) → `calendar_stale`: any candidate with `due_date > LAST_KNOWN_DAY` → audit `auto_pay_calendar_stale` once per `today` (skip when an audit row with that event exists for today). `_job(engine, trigger)` (never raises), `build_scheduler(engine) -> BackgroundScheduler` (`CronTrigger(hour=6, minute=0, timezone=TZ_NAME)`, `coalesce=True, max_instances=1, misfire_grace_time=3600`; startup `DateTrigger` now + 20 s), `start(engine)`, `stop()`, `main(argv=None, *, engine=None) -> int` (`--dry-run`, `--as-of YYYY-MM-DD`; prints the report as JSON; exit 0 for `completed | dry_run | disabled`, else 1). `app/main.py` registers `auto_pay_job.start(engine)` / `stop()` beside the schedule job.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_auto_pay_job_scheduler.py  (no database; model on tests/unit/test_schedule_job_scheduler.py)
def test_build_scheduler_registers_daily_and_startup_runs():
    jobs = {j.id: j for j in auto_pay_job.build_scheduler(engine=object()).get_jobs()}
    assert set(jobs) == {"auto_pay_daily", "auto_pay_startup"}
    daily = jobs["auto_pay_daily"]
    assert str(daily.trigger) == "cron[hour='6', minute='0']" and str(daily.trigger.timezone) == "Asia/Taipei"
    assert (daily.coalesce, daily.max_instances, daily.misfire_grace_time) == (True, 1, 3600)
def test_main_parses_as_of_and_dry_run(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(auto_pay_job, "run", lambda engine, trigger, **kw: seen.update(kw, trigger=trigger) or {"status": "dry_run", "decisions": []})
    assert auto_pay_job.main(["--dry-run", "--as-of", "2026-10-27"], engine=object()) == 0
    assert seen == {"trigger": "manual", "today": date(2026, 10, 27), "dry_run": True}
```

```python
# tests/integration/test_auto_pay_job.py  (Held from tests/integration/test_schedule_job.py:29-42; settings via settings_service.update_reconciliation_settings)
def test_run_books_once_and_is_a_no_op_afterwards(...):
    # enabled, enabled_from 2026-09-01; today(date(2026, 10, 27)); run(pg_engine, "cron") → status completed, one decision booked;
    # run again → completed, decisions == [], the same row id, ledger entry count unchanged, exactly one auto_pay_booked audit
def test_busy_when_the_lock_is_held(...)          # Held(pg_engine, AUTO_PAY_JOB_LOCK_KEY) → status busy, nothing written
def test_import_running_blocks_writes(...)        # Held(pg_engine, IMPORT_LOCK_KEY) → status import_running, no row, no entries
def test_disabled_runs_housekeeping_only(...)     # a booked row + owner twin, settings enabled False → status disabled, housekeeping.retired == 1, no new booking
def test_dry_run_lists_without_writing(...)       # dry_run=True → status dry_run, decisions would_book, no_due_date lists the due-less statement, no rows/entries/audit
def test_two_runners_book_once(...)               # tests.helpers.race: two sessions call auto_pay_service.book for the same candidate; one commits, the other fails IntegrityError; one pair in the ledger
def test_calendar_stale_audited_once_per_day(...) # a statement due 2028-01-05 (today 2028-01-07) → calendar_stale True, one auto_pay_calendar_stale audit after two runs
def test_main_cli_prints_json(...)                # main(["--dry-run", "--as-of", "2026-10-27"], engine=pg_engine) == 0 and json.loads(capsys.readouterr().out)["status"] == "dry_run"
```

- [ ] **Step 2: Run both files** — Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `auto_pay_job.py` (module docstring on the lock, the run order and the CLI), the lock key and the `main.py` hooks. Logs carry ids, counts and error classes only.
- [ ] **Step 4: Run** `tests/unit/test_auto_pay_job_scheduler.py tests/integration/test_auto_pay_job.py tests/integration/test_schedule_job.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): daily job, scheduler wiring and CLI"`.

---

### Task 7: Stand-in provenance, sweep-time twin retire, orphans

**Files:**
- Modify: `app/services/stand_ins.py` (`stand_in_entry_ids` :27–39, `held_by_stand_ins` :125–143, `shadows` :296–313, `orphans` :316–345), `app/services/reconciliation_service.py` (`reconcile` :944–1018 — the hook after `_skip_reason`, before the first `load_candidates`; `_desired_cases` :628–680 — `auto_pay_id` in the shadow / orphan contexts), `app/services/auto_pay_service.py`
- Test: `tests/integration/test_auto_pay_stand_ins.py`

**Interfaces:**
- Consumes: Task 5 `retire_pair`, `PAIR_BUSY`, `find_twins`; Task 3 `read_auto_pay_settings`.
- Produces: `stand_ins.auto_pay_legs(db, account_ids: list[int]) -> dict[int, int]` (entry id → `statement_auto_pay.id` for `booked` rows whose leg's own `account_id` is in `account_ids`; both legs qualify, each on its own side); `stand_in_entry_ids` returns today's set ∪ those ids; `held_by_stand_ins` keeps its 3-tuple and yields `(line, entry_id, None)` for an auto-pay leg (no creating action); `shadows` dicts carry `"backfill_action_id": None, "auto_pay_id": <row id>` for such legs; `orphans` additionally reports, for every auto-pay leg of `account_ids` with no active coverage anywhere, whose `entry_date` lies in `[statement.period_start, statement.period_end]` and whose row has no twin (`find_twins(..., exclude=legs)` with the stored settings window), the dict `{"entry_id", "event_id": None, "line_id": None, "backfill_action_id": None, "auto_pay_id", "claimant_entry_id": None}`; `_desired_cases` copies `auto_pay_id` into the `shadowed_by_stand_in` / `orphaned_stand_in` contexts when present. `auto_pay_service.retire_twins_for_statement(db, statement: AccountStatement, accounts: list[int]) -> list[int]`: for every booked row with a leg on `accounts` whose `pay_date` lies within `period ± matching.population_window_days(rules)` and that has a twin → `retire_pair(reason="twin")`; `CodedConflictError(PAIR_BUSY)` → logged `auto_pay.sweep_retire_busy` and skipped; returns the retired row ids. `reconcile()` calls it right after the `_skip_reason` check (the deleted legs are then absent from `load_candidates`).

- [ ] **Step 1: Write the failing tests** (shared fixture `_booked(seed, db, run, today)` → bank, card, statement, row via `decide` with `today(date(2026, 10, 27))`; bank statements via `_submit(db, run, bank, lines, kind="bank", period=OCT, opening=…)`):

```python
def test_both_legs_are_stand_ins_on_their_own_side(...)   # stand_in_entry_ids(db, [card.id]) == {in_leg}; (db, [bank.id]) == {out_leg}; auto_pay_legs maps to row.id
@pytest.mark.parametrize("days", [4, 5, 12])
def test_owner_entry_beside_the_leg_wins_in_the_sweep(days, ...):
    # next card statement (OCT) with a payment line 580 on PAY + days (txn_date None) and an owner _transfer(bank, card, 580, PAY + days);
    # rec.reconcile(card_stmt_oct) → row retired (reason twin), legs gone; the line is claimed by the owner's transfer_in when days <= 3,
    # else an amount_delta/transfer_delta case names the owner's leg; the auto-pay leg appears nowhere
def test_owner_first_then_job_skips_twin(...)          # owner transfer on PAY before decide → decision skipped/twin, no pair
def test_owner_after_the_bank_leg_holds_a_line(...)    # bank statement withdrawal −580 claims the out leg; owner transfer arrives; rec.reconcile(bank_stmt) → retired, line re-claimed by the owner's transfer_out, no open case
def test_orphan_only_when_covered_period_exists_without_line_or_twin(...):
    # before the next card statement: no orphan case anywhere; next card statement (OCT, period containing PAY) with NO payment line → entry_unmatched
    # hint orphaned_stand_in, context auto_pay_id == row.id, backfill_action_id None; add an owner twin → next reconcile: pair retired, orphan case gone
def test_policy_never_backfills_a_line_an_auto_pay_leg_holds(...)   # bank withdrawal line held by the out leg; policy run (BACKFILL_ON from test_reconciliation_policy) creates no action for that line
def test_sweep_and_job_racing_on_one_pair(...)        # tests.helpers.race: first = rec.reconcile(bank_stmt) holding the legs, second = check_row → one retires, the other gets auto_pay_busy / finds the legs gone; exactly one auto_pay_retired audit
```

- [ ] **Step 2: Run** — Expected: FAIL (`AttributeError: auto_pay_legs`).
- [ ] **Step 3: Implement** the `stand_ins` changes, `retire_twins_for_statement`, the `reconcile` hook and the `_desired_cases` contexts.
- [ ] **Step 4: Run** `tests/integration/test_auto_pay_stand_ins.py tests/integration/test_stand_ins.py tests/integration/test_reconciliation_policy.py tests/integration/test_reconciliation_service.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): legs as stand-ins, sweep-time twin retire, orphan hint"`.

---

### Task 8: Matcher rule `auto_pay` and the dry-run attribution

**Files:**
- Modify: `app/services/statements/matching.py` (`Entry` :66–96 — new field; `match` :1028–1110 — the insertion before the `_foreign_case` chain), `app/services/reconciliation_service.py` (`load_candidates` :293–301 — set the flag), `scripts/r6_dryrun.py` (`attribute` :41)
- Test: `tests/unit/test_statement_matching.py` (append), `tests/integration/test_auto_pay_stand_ins.py` (append)

**Interfaces:**
- Consumes: Task 7 `stand_ins.auto_pay_legs`.
- Produces: `matching.Entry.auto_pay: bool = False` (set with `stand_in=True` by `load_candidates` for ids in `auto_pay_legs`); `matching.AUTO_PAY_RULE = "auto_pay"`; `matching.AUTO_PAY_LINE_KINDS = {"card": frozenset({"payment"}), "bank": frozenset({"withdrawal", "payment", "transfer_out"})}`; `_auto_pay_claim(kind: str, line: Line, entries: list[Entry], claimed: set[int], rules: Rules) -> Representation | None`: admissible legs = `e.auto_pay and e.id not in claimed and _entry_class(e) in eligible_kinds(kind, line.line_kind) and _peer_ok(kind, line, e) and _same(e.flow, line.flow) and _date_distance(line, e) <= rules.drift_window_days`; exactly one, and no entry with `not e.stand_in` satisfying the same class/flow/distance test → `Representation("standalone", (Row(leg.id, "principal", leg.flow),), rules.accept, AUTO_PAY_RULE, (f"date_distance={d}", "auto_pay"))`; otherwise `None` (two legs fall through to `_transfer_case` → `ambiguous` as today). Insertion in `match()`: after the `if not reps:` / `_claimed_by_earlier_line` block and before `case = _foreign_case(...)`: a non-None rep is appended as a `Claim`, its rows added to `claimed`, `continue`. `_write_coverage` needs no change (`match_rule=rep.rule`). `r6_dryrun.attribute`: first branch `if after and after[0] == "claim" and after[1] == "auto_pay": return "auto-pay claim (2026-10-26)"`.

- [ ] **Step 1: Write the failing unit tests** (build `matching.Entry(...)` directly as the existing tests in `tests/unit/test_statement_matching.py` do; a card `payment` line of flow +580 on day D; the leg: `kind="transfer_in"`, flow 580, `transfer_group_id` set, `transfer_peer_is_card=False`, `stand_in=True, auto_pay=True`):

```python
def test_auto_pay_leg_claims_a_payment_line_12_days_later():          # posted D, entry_date D-12 → one claim, rule "auto_pay", match score == accept
def test_auto_pay_leg_within_three_days_claims_exact():                # distance 2 → rule "payment" (the exact path wins first)
def test_owner_entry_in_the_drift_window_blocks_the_auto_pay_claim():  # owner transfer_in (stand_in False) at distance 10 + leg at 5 → no claim; a case (amount_delta/transfer_delta or ambiguous) names the owner entry
def test_two_auto_pay_legs_are_ambiguous():                            # two legs within 15 days → no auto_pay claim; case kind ambiguous
def test_bank_lines_claim_the_out_leg():                               # bank withdrawal / payment / transfer_out line flow −580, out leg −580 at distance 6 → claim rule "auto_pay" each
def test_auto_pay_leg_beyond_drift_window_does_not_claim():            # distance 16 → no claim
def test_r6_dryrun_attributes_auto_pay_claims():                       # attribute({"before": None, "after": ("claim", "auto_pay", (1,))}) == "auto-pay claim (2026-10-26)"
```

Integration (append to `tests/integration/test_auto_pay_stand_ins.py`): `test_next_statements_claim_the_legs` — next card statement with a `payment` line on PAY + 1 / + 4 / + 12 (three statements or parametrize) → `StatementCoverage.match_rule` `payment`, `auto_pay`, `auto_pay`, no open case; a bank statement with a `withdrawal` line on PAY → rule `exact`-family claim of the out leg; a bank `payment` and a bank `transfer_out` line each claim it too.

- [ ] **Step 2: Run** — Expected: FAIL (`TypeError: unexpected keyword 'auto_pay'`).
- [ ] **Step 3: Implement** the field, the rule, the loader flag and the attribution.
- [ ] **Step 4: Run** `tests/unit/test_statement_matching.py tests/integration/test_auto_pay_stand_ins.py tests/integration/test_reconciliation_service.py tests/integration/test_coverage_sweep.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(auto-pay): matcher rule auto_pay, loader provenance, dry-run attribution"`.

---

### Task 9: Statement-retire audit, README, spec errata, full suite

**Files:**
- Modify: `app/services/statement_retire_service.py` (`retire_statement` :68–…, after the `statement_retired` audit), `README.md` (a paragraph after the R6c policy keys line :258 and two CLI lines after :562), `docs/superpowers/specs/2026-10-26-statement-auto-pay-design.md` (rev 4.1 errata: Task 1 revision id, the `no_link` `pay_account_id` storage, the shared `ACCOUNTING_SCHEDULER_ENABLED` switch, `header_changed` marker in `reason`, lock key)
- Test: `tests/integration/test_statement_retire.py` (append one test)

**Interfaces:**
- Produces: retiring a statement that has a `booked` `statement_auto_pay` row leaves the row and the legs untouched and audits `auto_pay_statement_retired` `{auto_pay_id, out_entry_id, in_entry_id}` (statement_id set).

- [ ] **Step 1: Write the failing test** — `test_retire_leaves_a_booked_auto_pay_row_and_audits_it`: book via `auto_pay_service.decide`, `_retire(db, stmt)`, assert row still `booked`, both legs exist, audit tail contains `auto_pay_statement_retired` with `auto_pay_id == row.id`.
- [ ] **Step 2: Run** — Expected: FAIL (no such audit row).
- [ ] **Step 3: Implement** the hook; write the README paragraph (what is booked, when, the settings block with defaults, the gates and skip reasons, the twin rule and retirement, the matcher rule, the audit events, the CLI `python -m app.services.auto_pay_job --dry-run [--as-of YYYY-MM-DD]`, the rollout order flag off → migration → dry-run → `enabled` + `enabled_from`, the rollback, and that `ACCOUNTING_SCHEDULER_ENABLED=false` switches this job off together with the schedule job); apply the spec errata as a short "rev 4.1 errata (plan)" list at the top of §8.
- [ ] **Step 4: Run** `tests/integration/test_statement_retire.py` — Expected: PASS.
- [ ] **Step 5: Full suite** — `… -m pytest -q -p no:cacheprovider` → `0 failed`; record the total beside the baseline (checkout f2d8498 count, taken once before Task 1's merge-base compare, in the SDD ledger).
- [ ] **Step 6: Commit** — `git commit -m "feat(auto-pay): retire audit, README, spec errata"`.

---

## Host steps after merge (owner go required for each; not tasks)

1. Deploy: `alembic upgrade head` (creates `btree_gist` — the DB user is a superuser on this host) → restart the API (`deploy-177-api.sh <sha>` pattern). The scheduler starts with `auto_pay.enabled = false`: housekeeping runs, nothing books.
2. One-off check, read-only, from the release checkout: the currency and `is_credit` / `is_archived` of every `auto_pay_account_id` target against its card (spec §4.8 "22 linked cards"), output mode 600 under `~/reports/home-hub-auto-pay-release-<ts>/`.
3. `python -m app.services.auto_pay_job --dry-run --as-of <today>` → every live card statement with its decision (`cutoff` for everything already due once `enabled_from` is set; the 星展 shape as `twin`).
4. Owner: `PUT /settings/reconciliation` with the current body plus `auto_pay: {enabled: true, enabled_from: <today>}`.
5. Watch the first 06:00 run: `GET /reconciliation/audit` for `auto_pay_booked`, the legs in the ledger, the 已逾期 pill, `gaps.py` unchanged for the card's current statement; `r6_dryrun.py` before/after shows only `auto-pay claim` attributions.

## Self-review

- Spec coverage: §4.2 → Task 1; §4.3 → Task 2; §4.7 + §4.8 → Task 3; §4.4 steps 1–4 → Task 4, step 0 → Task 5, scheduler/CLI/lock → Task 6; §4.5 (stand-ins both sides, sweep-time retire, orphans, `shadows` untouched, statement retire) → Tasks 7 and 9; §4.6 → Task 8; §5 rollout → Host steps; §7 tests → each test list above (unit: calendar Task 2, gates/twins/housekeeping Tasks 4–5, matcher Task 8; integration: booking/no-op/concurrency Tasks 4 and 6, duplicate and next-month Task 4, stand-ins/claims/owner-wins/orphans Tasks 7–8, owner delete/header Task 5, settings Task 3, `owner_may_record` Task 7, import key Task 6); §4.9 no frontend task on purpose.
- Type consistency: `settings` is always the `read_auto_pay_settings` dict (with `version`); `Candidate.existing_reason` feeds `record_skip`'s audit-once rule; `retire_pair` returns statement ids that `check_row` / `retire_twins_for_statement` use for `needs_recheck`; `held_by_stand_ins` keeps the 3-tuple so `shadows` is unchanged.
- Review Focus 1 → Task 7 parametrised test; 2 → Tasks 1 and 4; 3 → Task 7 race test; 4 → Task 4 gate/twin tests; 5 → Task 5 edit tests.
