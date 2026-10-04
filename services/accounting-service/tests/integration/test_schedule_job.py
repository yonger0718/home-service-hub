"""The daily job (spec "Daily schedule job", "Run-now endpoint")."""

import json
import logging
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.schemas.schedules import DefinitionIn
from app.services import schedule_job, schedule_read, schedule_service
from app.services import schedule_posting as posting
from app.services.errors import ConflictError, NotFoundError
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import SCHEDULE_JOB_LOCK_KEY


def _row(db, instance_id) -> ScheduleInstance:
    db.expire_all()
    return db.get(ScheduleInstance, instance_id)


def _entries(db) -> int:
    return db.scalar(select(func.count()).select_from(LedgerEntry))


class Held:
    """Hold an advisory lock on its own connection: Held(pg_engine, KEY) as a context manager."""

    def __init__(self, engine, key):
        self.engine, self.key = engine, key

    def __enter__(self):
        self.conn = self.engine.connect()
        self.conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": self.key})
        return self

    def __exit__(self, *exc):
        self.conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
        self.conn.commit()
        self.conn.close()


@pytest.fixture()
def card(seed):
    return seed.account("範例卡")


def test_no_silent_backlog_after_an_import(db_session, seed, card, pg_engine, today):
    # Spec "No silent backlog after an import".
    today(date(2026, 10, 3))
    imported = seed.definition([seed.line("expense", card, "390")], created_locally=False, moze_id="P-1",
                               auto_post_from=date(2026, 10, 3), anchor=date(2026, 10, 1))
    early = seed.instance(imported, 1, date(2026, 10, 1), moze_id="R-1")
    due = seed.instance(imported, 2, date(2026, 10, 3), moze_id="R-2")
    db_session.commit()

    report = schedule_job.run(pg_engine, "import", today=date(2026, 10, 3))

    assert report["status"] == "completed" and report["posted"] == [due.id]
    assert (_row(db_session, due.id).status, _row(db_session, due.id).acted_by) == ("posted", "auto")
    assert _row(db_session, early.id).status == "pending"
    assert early.id in [item["id"] for item in schedule_read.list_instances(db_session, queue=True)]


def test_back_dated_local_definition(db_session, seed, card, pg_engine, today):
    # Spec "Back-dated local definition".
    today(date(2026, 10, 3))
    definition_id = schedule_service.create_definition(
        db_session,
        DefinitionIn(kind="recurring", name="管理費", template={"lines": [{"kind": "expense", "account_id": card.id,
                     "amount": "2000", "currency": "TWD"}]}, interval_unit="month", anchor_date=date(2026, 8, 15)),
    )
    db_session.commit()

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 3))

    assert report["posted"] == [] and _entries(db_session) == 0
    queue = [(item["definition_id"], item["due_date"]) for item in schedule_read.list_instances(db_session, queue=True)]
    assert queue == [(definition_id, date(2026, 8, 15)), (definition_id, date(2026, 9, 15))]


def test_only_one_runner(db_session, seed, card, pg_engine):
    # Spec "Only one runner".
    netflix = seed.definition([seed.line("expense", card, "390")])
    seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()
    with Held(pg_engine, SCHEDULE_JOB_LOCK_KEY):
        report = schedule_job.run(pg_engine, "startup", today=date(2026, 10, 3))
    assert (report["status"], report["posted"], report["generated"]) == ("busy", [], 0)
    assert _entries(db_session) == 0


def test_retry_after_an_import(db_session, seed, card, pg_engine):
    # Spec "Retry after an import": the 00:05 run stops, the run at the end of the import posts.
    bank = seed.account("薪轉")
    loan_day = seed.definition([seed.line("expense", bank, "8333")], name="信貸", anchor=date(2026, 10, 9))
    instance = seed.instance(loan_day, 1, date(2026, 10, 9))
    db_session.commit()
    with Held(pg_engine, IMPORT_LOCK_KEY):
        stopped = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 9))
    assert (stopped["status"], stopped["posted"]) == ("import_running", [])
    after = schedule_job.run(pg_engine, "import", today=date(2026, 10, 9))
    assert (after["status"], after["posted"]) == ("completed", [instance.id])


def test_moved_earlier_seq_is_attempted_before_the_next_one(db_session, seed, card, pg_engine, monkeypatch):
    # Multica R-F6 (D31 wins over due-date order): seq 1 moved to Nov-10, seq 2 on Nov-9; run on Nov-10.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    moved = seed.instance(netflix, 1, date(2026, 11, 10), rule_date=date(2026, 10, 9), edited_by_owner=True)
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()
    order: list[int] = []
    real_post = posting.post_instance
    monkeypatch.setattr(posting, "post_instance", lambda db, instance_id, **kwargs: order.append(instance_id) or real_post(db, instance_id, **kwargs))

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))

    assert order == [moved.id, second.id] and report["posted"] == [moved.id, second.id]


def test_failed_moved_earlier_seq_holds_the_next_one_until_the_owner_posts_it(db_session, seed, card, pg_engine):
    # Multica R-F6: seq 1 (moved later) fails → seq 2 is not posted, in this run and the next; the owner posts seq 1
    # → seq 2 posts on the next run.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    moved = seed.instance(
        netflix, 1, date(2026, 11, 10), rule_date=date(2026, 10, 9), edited_by_owner=True, amount_override=["0"]
    )
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()

    first_run = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))
    assert (first_run["failed"], first_run["posted"], first_run["stopped_definitions"]) == ([moved.id], [], [netflix.id])
    assert _row(db_session, second.id).status == "pending"
    again = schedule_job.run(pg_engine, "retry", today=date(2026, 11, 10))
    assert (again["posted"], _row(db_session, second.id).status) == ([], "pending")

    row = _row(db_session, moved.id)
    row.amount_override = ["390"]
    db_session.commit()
    posting.post_instance(db_session, moved.id, actor="owner")
    db_session.commit()
    after = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 11))
    assert after["posted"] == [second.id] and _row(db_session, second.id).acted_by == "auto"


def test_a_failed_earlier_period_the_job_does_not_own_still_holds_the_series(db_session, seed, card, pg_engine):
    # Multica R-F6 "(or failed)": a reopened seq 1 whose owner post failed (last_error) keeps seq 2 waiting; a reopened
    # seq 1 without an error does not (the owner deleted it on purpose, D33).
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    reopened_at = datetime(2026, 10, 10, tzinfo=timezone.utc)
    first = seed.instance(netflix, 1, date(2026, 10, 9), reopened_at=reopened_at, last_error="lines[0].account_id: 帳戶已封存")
    second = seed.instance(netflix, 2, date(2026, 11, 9))
    db_session.commit()
    held = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 9))
    assert (held["posted"], held["failed"], held["stopped_definitions"]) == ([], [], [netflix.id])

    row = _row(db_session, first.id)
    row.last_error = None
    db_session.commit()
    assert schedule_job.run(pg_engine, "cron", today=date(2026, 11, 9))["posted"] == [second.id]


def test_due_but_unposted_are_counted_when_posting_is_off(db_session, seed, card, pg_engine):
    # Multica R-F4: run(post=False) generates and counts what the job would post, posting nothing.
    seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22), auto_post_from=date(2026, 10, 1))
    db_session.commit()
    report = schedule_job.run(pg_engine, "import", today=date(2026, 10, 22), post=False)
    assert (report["status"], report["posted"], report["due_unposted"]) == ("completed", [], 1)
    assert report["generated"] == 14 and _entries(db_session) == 0


def test_failure_stops_the_loan_not_the_others(db_session, seed, card, pg_engine):
    # Spec "Failure stops the loan, not the others".
    bank = seed.account("薪轉")
    loan = seed.definition([seed.line("expense", bank, "8333")], name="L", anchor=date(2026, 10, 9), auto_post_from=date(2026, 10, 1))
    other = seed.definition([seed.line("expense", card, "390")], name="N", anchor=date(2026, 11, 1), auto_post_from=date(2026, 10, 1))
    failing = seed.instance(loan, 1, date(2026, 10, 9), amount_override=["0"])
    waiting = seed.instance(loan, 2, date(2026, 11, 9))
    fine = seed.instance(other, 1, date(2026, 11, 1))
    db_session.commit()

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 11, 10))

    assert (report["failed"], report["posted"], report["stopped_definitions"]) == ([failing.id], [fine.id], [loan.id])
    assert _row(db_session, failing.id).last_error.startswith("amounts")
    assert (_row(db_session, waiting.id).status, _row(db_session, waiting.id).last_error) == ("pending", None)


def test_paused_definition(db_session, seed, card, pg_engine):
    # Spec "Paused definition".
    paused = seed.definition([seed.line("expense", card, "390")], status="paused")
    instance = seed.instance(paused, 1, date(2026, 10, 22))
    db_session.commit()
    schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))
    assert _row(db_session, instance.id).status == "pending"


def test_dry_run(db_session, seed, card, pg_engine, today, capsys):
    # Spec "Dry run".
    today(date(2026, 10, 22))
    netflix = seed.definition([seed.line("expense", card, "390")])
    spotify = seed.definition([seed.line("expense", card, "149")], name="Spotify")
    first = seed.instance(netflix, 1, date(2026, 10, 22))
    second = seed.instance(spotify, 1, date(2026, 10, 22))
    db_session.commit()

    assert schedule_job.main(["--dry-run"], engine=pg_engine) == 0

    printed = json.loads(capsys.readouterr().out)
    assert (printed["status"], sorted(printed["posted"])) == ("dry_run", sorted([first.id, second.id]))
    assert _entries(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(ScheduleInstance)) == 2
    assert {_row(db_session, first.id).status, _row(db_session, second.id).status} == {"pending"}
    assert db_session.get(ScheduleDefinition, netflix.id).generated_until is None


def test_job_run_twice_in_one_day_posts_each_instance_once(db_session, seed, card, pg_engine):
    # Review Focus 2: the 00:05 run, then a restart's startup run on the same day.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 10, 22))
    db_session.commit()

    first = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))
    entries = _entries(db_session)
    second = schedule_job.run(pg_engine, "startup", today=date(2026, 10, 22))

    assert (first["generated"], len(first["posted"])) == (14, 1)  # 2026-10-22 … 2027-11-22 (horizon 2027-11-22)
    assert (second["generated"], second["posted"], second["failed"]) == (0, [], [])
    assert _entries(db_session) == entries == 1
    posted = db_session.scalars(select(ScheduleInstance).where(ScheduleInstance.status == "posted")).all()
    assert [(row.definition_id, row.due_date) for row in posted] == [(netflix.id, date(2026, 10, 22))]


def test_mode_switch_then_the_job_posts_only_today(client, db_session, seed, card, pg_engine, today):
    # Spec "Switching to automatic posting", the job half.
    today(date(2026, 10, 3))
    rent = seed.definition([seed.line("expense", card, "18000")], name="房租", posting_mode="confirm",
                           auto_post_from=date(2026, 9, 1))
    backlog = seed.instance(rent, 1, date(2026, 9, 22))
    due = seed.instance(rent, 2, date(2026, 10, 3))
    db_session.commit()
    assert client.put(f"/schedules/definitions/{rent.id}/mode", json={"posting_mode": "auto"}).status_code == 200

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 3))

    assert report["posted"] == [due.id]
    assert _row(db_session, backlog.id).status == "pending"


def test_reopened_instance_is_never_posted_by_the_job(db_session, seed, card, pg_engine):
    netflix = seed.definition([seed.line("expense", card, "390")])
    reopened = seed.instance(netflix, 1, date(2026, 10, 22), reopened_at=datetime(2026, 10, 22, tzinfo=timezone.utc))
    db_session.commit()
    assert schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))["posted"] == []
    assert _row(db_session, reopened.id).status == "pending"


def test_run_now_refused_while_the_job_runs(client, db_session, seed, card, pg_engine):
    # Spec "Manual run while the job runs".
    netflix = seed.definition([seed.line("expense", card, "390")])
    seed.instance(netflix, 1, date(2026, 10, 3))
    db_session.commit()
    with Held(pg_engine, SCHEDULE_JOB_LOCK_KEY):
        response = client.post("/schedules/run-now")
    assert response.status_code == 409
    assert _entries(db_session) == 0


def test_run_now_refused_while_an_import_runs(client, db_session, pg_engine):
    with Held(pg_engine, IMPORT_LOCK_KEY):
        response = client.post("/schedules/run-now")
    assert (response.status_code, response.json()["message"]) == (409, "import_running")


def test_a_period_deleted_mid_run_is_passed_over(db_session, seed, card, pg_engine, monkeypatch):
    # end / a definition PUT may delete a period between due_instances and its post: not a failure.
    netflix = seed.definition([seed.line("expense", card, "390")])
    gone = seed.instance(netflix, 1, date(2026, 10, 22))
    db_session.commit()

    def deleted_meanwhile(db, instance_id, **kwargs):
        raise NotFoundError(f"schedule instance {instance_id} not found")

    monkeypatch.setattr(posting, "post_instance", deleted_meanwhile)
    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert (report["failed"], report["stopped_definitions"]) == ([], [])
    assert _row(db_session, gone.id).last_error is None


def test_definition_changed_is_retried_once(db_session, seed, card, pg_engine, monkeypatch):
    # Task 11's post_sequence semantics: the template gained a loan line between the peek and the FOR SHARE lock;
    # the job retries the period once (then FOR UPDATE) instead of leaving it, and the series, to the next run.
    netflix = seed.definition([seed.line("expense", card, "390")])
    first = seed.instance(netflix, 1, date(2026, 10, 21))
    second = seed.instance(netflix, 2, date(2026, 10, 22))
    db_session.commit()
    real_lock = posting.lock_definition_for_post
    calls: list[int] = []

    def changed_once(db, definition_id):
        calls.append(definition_id)
        if len(calls) == 1:
            raise ConflictError("definition_changed")
        return real_lock(db, definition_id)

    monkeypatch.setattr(posting, "lock_definition_for_post", changed_once)
    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert (report["posted"], report["failed"], report["stopped_definitions"]) == ([first.id, second.id], [], [])


def test_run_now_needs_the_bearer_token_when_api_tokens_are_set(client, db_session, monkeypatch):
    # #42 / design D42: run-now sits behind ApiTokenMiddleware like every other route.
    monkeypatch.setenv("ACCOUNTING_API_TOKENS", "test:synthetic-token-1")
    assert client.post("/schedules/run-now").status_code == 401
    allowed = client.post("/schedules/run-now", headers={"Authorization": "Bearer synthetic-token-1"})
    assert allowed.status_code == 200


def test_import_starts_mid_run(client, db_session, seed, card, pg_engine, today, monkeypatch):
    # Spec "Import starts mid-run".
    today(date(2026, 10, 22))
    ids = []
    for name in ("A", "B", "C"):
        definition = seed.definition([seed.line("expense", card, "100")], name=name)
        ids.append(seed.instance(definition, 1, date(2026, 10, 22)).id)
    db_session.commit()
    holder = pg_engine.connect()
    real_post = schedule_job._post_job_one
    calls: list[int] = []

    def post_then_import(db, instance_id, definition_id, today):
        # Wraps _post_job_one, before it shares the import key (wrapping post_instance would hold the key already).
        if len(calls) == 2:
            holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        calls.append(instance_id)
        return real_post(db, instance_id, definition_id, today)

    monkeypatch.setattr(schedule_job, "_post_job_one", post_then_import)
    try:
        response = client.post("/schedules/run-now")
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        holder.close()

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["posted"], body["trigger"]) == ("import_running", ids[:2], "manual")


def test_pre_auto_post_from_period_with_error_blocks_the_series(db_session, seed, card, pg_engine):
    # R-F6 reading: a period due before auto_post_from is the owner's, but once it carries last_error (an owner post
    # failed) it holds the later periods; the job records nothing on either.
    netflix = seed.definition([seed.line("expense", card, "390")], anchor=date(2026, 9, 22), auto_post_from=date(2026, 10, 1))
    seed.instance(netflix, 1, date(2026, 9, 22), last_error="lines[0].account_id: 帳戶已封存")
    later = seed.instance(netflix, 2, date(2026, 10, 22))
    db_session.commit()

    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert (report["posted"], report["failed"], report["stopped_definitions"]) == ([], [], [netflix.id])
    assert (_row(db_session, later.id).status, _row(db_session, later.id).last_error) == ("pending", None)


def test_one_broken_definition_does_not_stop_generation(db_session, seed, card, pg_engine, monkeypatch, caplog):
    # One definition's generation error is logged (class + frames, no message), rolled back and listed under
    # generation_failed; the other definitions still generate and the job still posts.
    broken = seed.definition([seed.line("expense", card, "390")], name="A", anchor=date(2026, 10, 22))
    waiting = seed.instance(broken, 1, date(2026, 10, 22))
    fine = seed.definition([seed.line("expense", card, "149")], name="B", anchor=date(2026, 10, 22))
    db_session.commit()
    real_generate = schedule_job.generation.generate_locked
    secret = "SECRET" + "-GEN-TOKEN"

    def broken_first(db, definition_id, today):
        if definition_id == broken.id:
            raise RuntimeError(secret)
        return real_generate(db, definition_id, today)

    monkeypatch.setattr(schedule_job.generation, "generate_locked", broken_first)
    # alembic's fileConfig (the session's in-process upgrade) disables loggers that already exist (as in
    # test_backup_replace_and_report); the service runs migrations in a separate process.
    monkeypatch.setattr(logging.getLogger("app.services.schedule_job"), "disabled", False)
    caplog.set_level("INFO", logger="app.services.schedule_job")
    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert (report["status"], report["generation_failed"], report["generated"]) == ("completed", [broken.id], 14)
    fine_first = db_session.scalar(
        select(ScheduleInstance.id).where(ScheduleInstance.definition_id == fine.id, ScheduleInstance.seq == 1)
    )
    assert report["posted"] == [waiting.id, fine_first]
    assert db_session.get(ScheduleDefinition, broken.id).generated_until is None
    [record] = [r for r in caplog.records if r.getMessage() == "schedule_job.generation_error"]
    assert (record.levelname, record.definition_id, record.error_class) == ("ERROR", broken.id, "RuntimeError")
    assert "broken_first" in record.frames
    assert secret not in caplog.text and all(secret not in repr(r.__dict__) for r in caplog.records)


def test_post_error_is_logged_with_frames_and_no_message(db_session, seed, card, pg_engine, monkeypatch, caplog):
    netflix = seed.definition([seed.line("expense", card, "390")])
    instance = seed.instance(netflix, 1, date(2026, 10, 22))
    db_session.commit()
    secret = "SECRET" + "-POST-TOKEN"

    def explode(db, instance_id, **kwargs):
        try:
            raise KeyError(secret)
        except KeyError as inner:
            raise RuntimeError(secret) from inner

    monkeypatch.setattr(posting, "post_instance", explode)
    # alembic's fileConfig (the session's in-process upgrade) disables loggers that already exist (as in
    # test_backup_replace_and_report); the service runs migrations in a separate process.
    monkeypatch.setattr(logging.getLogger("app.services.schedule_job"), "disabled", False)
    caplog.set_level("INFO", logger="app.services.schedule_job")
    report = schedule_job.run(pg_engine, "cron", today=date(2026, 10, 22))

    assert report["failed"] == [instance.id]
    [record] = [r for r in caplog.records if r.getMessage() == "schedule_job.post_error"]
    assert (record.levelname, record.instance_id, record.error_class) == ("ERROR", instance.id, "RuntimeError")
    assert "explode" in record.frames and record.error_chain == ["KeyError"]
    assert secret not in caplog.text and all(secret not in repr(r.__dict__) for r in caplog.records)


def test_run_now_error_after_the_pre_checks_is_a_500(client, monkeypatch):
    # service_errors covers only the pre-checks: a service error escaping the run (after partial commits) is not
    # mapped to 404 / 409 / 422. `client` installs the test-database overrides this raw client shares.
    def gone(engine, trigger, **kwargs):
        raise NotFoundError("schedule instance 1 not found")

    monkeypatch.setattr(schedule_job, "run", gone)
    with TestClient(client.app, raise_server_exceptions=False) as raw:
        assert raw.post("/schedules/run-now").status_code == 500
