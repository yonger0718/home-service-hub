"""The auto-pay daily job: lock, housekeeping, candidates, dry run, calendar staleness, CLI (auto-pay spec §4.4).
Fictional amounts only."""
import json
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import LedgerEntry, ReconciliationAudit, StatementAutoPay
from app.services import auto_pay_job
from app.services import auto_pay_service as aps
from app.services import settings_service
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import AUTO_PAY_JOB_LOCK_KEY
from tests.helpers import race
from tests.integration.statement_fixtures import OCT, SEP, _card, _line, _submit, _transfer, run  # noqa: F401
from tests.integration.test_schedule_job import Held

DAY = date(2026, 9, 3)
DUE = date(2026, 10, 24)  # Sat; Mon 10-26 is the 光復節 make-up holiday
PAY = date(2026, 10, 27)
TODAY = PAY


def _enable(db, enabled=True, enabled_from="2026-09-01"):
    settings_service.update_reconciliation_settings(
        db, {"auto_pay": {"enabled": enabled, "enabled_from": enabled_from}})
    db.commit()


@pytest.fixture
def world(seed, db_session, run):
    bank = seed.account("薪轉")
    card = _card(seed, db_session, "卡", auto_pay_account_id=bank.id)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=DUE)
    return bank, card, stmt


def _entries(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(LedgerEntry))


def _rows(db) -> list[StatementAutoPay]:
    db.expire_all()
    return db.query(StatementAutoPay).order_by(StatementAutoPay.id).all()


def _audits(db, event=None) -> list[ReconciliationAudit]:
    db.expire_all()
    query = db.query(ReconciliationAudit).filter(ReconciliationAudit.event.like("auto_pay_%"))
    if event is not None:
        query = query.filter(ReconciliationAudit.event == event)
    return query.order_by(ReconciliationAudit.id).all()


def _book_with_owner_twin(pg_engine, db, seed, bank, card) -> StatementAutoPay:
    """Book the statement with a real job run, then add the owner's own payment transfer (a twin)."""
    assert auto_pay_job.run(pg_engine, "cron", today=TODAY)["status"] == "completed"
    (row,) = _rows(db)
    assert row.status == "booked"
    _transfer(seed, bank, card, 580, PAY)
    db.commit()
    return row


def test_run_books_once_and_is_a_no_op_afterwards(pg_engine, db_session, world, today):
    db, (bank, card, stmt) = db_session, world
    _enable(db)
    today(TODAY)
    first = auto_pay_job.run(pg_engine, "cron")
    assert (first["trigger"], first["today"], first["status"], first["enabled"]) == (
        "cron", "2026-10-27", "completed", True)
    assert [(d["statement_id"], d["decision"]) for d in first["decisions"]] == [(stmt.id, "booked")]
    assert first["calendar_stale"] is False and first["no_due_date"] == []
    assert first["housekeeping"] == {"checked": 0, "owner_deleted": 0, "owner_edited": 0, "retired": 0,
                                     "updated": 0, "header_changed": 0, "busy": 0, "failed": 0}
    (row,) = _rows(db)
    entries = _entries(db)
    assert row.status == "booked" and entries >= 2

    second = auto_pay_job.run(pg_engine, "cron")
    assert (second["status"], second["decisions"]) == ("completed", [])
    assert second["housekeeping"]["checked"] == 1
    assert [r.id for r in _rows(db)] == [row.id] and _rows(db)[0].status == "booked"
    assert _entries(db) == entries
    assert len(_audits(db, "auto_pay_booked")) == 1


def test_busy_when_the_lock_is_held(pg_engine, db_session, world):
    db = db_session
    _enable(db)
    entries = _entries(db)
    with Held(pg_engine, AUTO_PAY_JOB_LOCK_KEY):
        report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert (report["status"], report["decisions"]) == ("busy", [])
    assert _rows(db) == [] and _entries(db) == entries and _audits(db) == []


def test_import_running_blocks_writes(pg_engine, db_session, world):
    db = db_session
    _enable(db)
    entries = _entries(db)
    with Held(pg_engine, IMPORT_LOCK_KEY):
        report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert report["status"] == "import_running"
    assert _rows(db) == [] and _entries(db) == entries and _audits(db) == []


def test_import_running_blocks_housekeeping_too(pg_engine, db_session, seed, world):
    db, (bank, card, stmt) = db_session, world
    _enable(db)
    row = _book_with_owner_twin(pg_engine, db, seed, bank, card)
    out_id, in_id = row.out_entry_id, row.in_entry_id
    with Held(pg_engine, IMPORT_LOCK_KEY):
        report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert report["status"] == "import_running"
    assert report["housekeeping"]["retired"] == 0
    (after,) = _rows(db)
    assert after.status == "booked"
    assert db.get(LedgerEntry, out_id) is not None and db.get(LedgerEntry, in_id) is not None


def test_dry_run_while_disabled_still_lists(pg_engine, db_session, world):
    db, (bank, card, stmt) = db_session, world
    report = auto_pay_job.run(pg_engine, "manual", today=TODAY, dry_run=True)
    assert (report["status"], report["enabled"]) == ("dry_run", False)
    assert [(d["statement_id"], d["decision"]) for d in report["decisions"]] == [(stmt.id, "would_book")]
    assert _rows(db) == [] and _audits(db) == []


def test_disabled_runs_housekeeping_only(pg_engine, db_session, seed, run, world):
    db, (bank, card, stmt) = db_session, world
    _enable(db)
    row = _book_with_owner_twin(pg_engine, db, seed, bank, card)
    other_card = _card(seed, db, "另卡", auto_pay_account_id=bank.id)
    other, _, _ = _submit(db, run, other_card, [_line(1, DAY, "全聯", 320)], period=SEP, due_date=DUE)
    _enable(db, enabled=False)
    report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert (report["status"], report["enabled"], report["decisions"]) == ("disabled", False, [])
    assert report["housekeeping"]["retired"] == 1 and report["housekeeping"]["checked"] == 1
    assert [(r.statement_id, r.status, r.reason) for r in _rows(db)] == [(stmt.id, "retired", "twin")]
    assert db.query(StatementAutoPay).filter_by(statement_id=other.id).count() == 0


def test_dry_run_lists_without_writing(pg_engine, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    _enable(db)
    no_due, _, _ = _submit(db, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT)
    entries = _entries(db)
    report = auto_pay_job.run(pg_engine, "manual", today=TODAY, dry_run=True)
    assert (report["status"], report["enabled"]) == ("dry_run", True)
    assert [(d["statement_id"], d["decision"], d["reason"]) for d in report["decisions"]] == [
        (stmt.id, "would_book", None)]
    assert report["no_due_date"] == [no_due.id]
    assert _rows(db) == [] and _entries(db) == entries and _audits(db) == []


def test_two_runners_book_once(pg_engine, db_session, world):
    db, (bank, card, stmt) = db_session, world
    _enable(db)

    def book(session):
        settings = settings_service.read_auto_pay_settings(session)
        (cand,) = aps.candidates(session, TODAY, settings)
        aps.book(session, cand, settings)

    outcome = race(pg_engine, book, book)
    assert isinstance(outcome, IntegrityError)
    assert [(r.statement_id, r.status) for r in _rows(db)] == [(stmt.id, "booked")]
    assert db.query(LedgerEntry).filter_by(name=aps.AUTO_PAY_NAME).count() == 2
    assert len(_audits(db, "auto_pay_booked")) == 1


def test_calendar_stale_audited_once_per_day(pg_engine, db_session, seed, run):
    db = db_session
    _enable(db)
    unlinked = _card(seed, db, "無連結")  # no_link: a transient skip, still a candidate on the second run
    late, _, _ = _submit(db, run, unlinked, [_line(1, date(2027, 12, 3), "全聯", 580)],
                         period=(date(2027, 12, 1), date(2027, 12, 31)), due_date=date(2028, 1, 5))
    day = date(2028, 1, 7)
    for _ in range(2):
        report = auto_pay_job.run(pg_engine, "cron", today=day)
        assert (report["status"], report["calendar_stale"]) == ("completed", True)
        assert [(d["statement_id"], d["decision"], d["reason"]) for d in report["decisions"]] == [
            (late.id, "skipped", "no_link")]
    (stale,) = _audits(db, "auto_pay_calendar_stale")
    assert stale.detail["today"] == "2028-01-07" and stale.detail["statement_ids"] == [late.id]
    assert auto_pay_job.run(pg_engine, "cron", today=date(2028, 1, 8))["calendar_stale"] is True
    assert len(_audits(db, "auto_pay_calendar_stale")) == 2  # a new day: audited again


def test_main_cli_prints_json(pg_engine, db_session, world, capsys):
    assert auto_pay_job.main(["--dry-run", "--as-of", "2026-10-27"], engine=pg_engine) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["status"], report["today"], report["trigger"]) == ("dry_run", "2026-10-27", "manual")
    assert _rows(db_session) == []


def test_a_failing_row_or_candidate_does_not_stop_the_run(pg_engine, db_session, seed, run, world, monkeypatch):
    db, (bank, card, stmt) = db_session, world
    _enable(db)
    row = _book_with_owner_twin(pg_engine, db, seed, bank, card)
    other_card = _card(seed, db, "另卡", auto_pay_account_id=bank.id)
    other, _, _ = _submit(db, run, other_card, [_line(1, DAY, "全聯", 320)], period=SEP, due_date=DUE)
    third_card = _card(seed, db, "三卡", auto_pay_account_id=bank.id)
    third, _, _ = _submit(db, run, third_card, [_line(1, DAY, "全聯", 210)], period=SEP, due_date=DUE)
    real_decide = aps.decide

    def flaky_decide(session, cand, *args, **kwargs):
        if cand.statement_id == other.id:
            raise RuntimeError("boom")
        return real_decide(session, cand, *args, **kwargs)

    def flaky_check(session, booked, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(aps, "decide", flaky_decide)
    monkeypatch.setattr(aps, "check_row", flaky_check)
    report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert report["status"] == "completed"
    assert report["housekeeping"]["failed"] == 1 and report["housekeeping"]["retired"] == 0
    decisions = {d["statement_id"]: d for d in report["decisions"]}
    assert (decisions[other.id]["decision"], decisions[other.id]["error_class"]) == ("failed", "RuntimeError")
    assert decisions[third.id]["decision"] == "booked"
    assert {r.statement_id: r.status for r in _rows(db)} == {stmt.id: "booked", third.id: "booked"}

    def busy_check(session, booked, *args, **kwargs):
        raise aps.CodedConflictError(aps.PAIR_BUSY, "locked")

    monkeypatch.setattr(aps, "check_row", busy_check)
    report = auto_pay_job.run(pg_engine, "cron", today=TODAY)
    assert (report["housekeeping"]["busy"], report["housekeeping"]["failed"]) == (2, 0)
